from __future__ import annotations
import json, math, time
from collections import deque
from pathlib import Path
import numpy as np

def _norm(v):
    v=np.asarray(v,dtype=float); n=float(np.linalg.norm(v))
    return v/n if n>1e-12 else v

def _wrap_deg(a):
    return (float(a)+180.0)%360.0-180.0

def q_wxyz_to_R(q):
    w,x,y,z=map(float,q); n=math.sqrt(w*w+x*x+y*y+z*z)
    if n<1e-12: raise ValueError("zero quaternion")
    w,x,y,z=w/n,x/n,y/n,z/n
    return np.array([
        [1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
        [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
        [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]],float)

def matrix_to_quat_wxyz(R):
    m=np.asarray(R,float); tr=float(np.trace(m))
    if tr>0:
        s=math.sqrt(tr+1.0)*2; w=.25*s
        x=(m[2,1]-m[1,2])/s; y=(m[0,2]-m[2,0])/s; z=(m[1,0]-m[0,1])/s
    else:
        i=int(np.argmax(np.diag(m)))
        if i==0:
            s=math.sqrt(max(1e-15,1+m[0,0]-m[1,1]-m[2,2]))*2
            w=(m[2,1]-m[1,2])/s;x=.25*s;y=(m[0,1]+m[1,0])/s;z=(m[0,2]+m[2,0])/s
        elif i==1:
            s=math.sqrt(max(1e-15,1+m[1,1]-m[0,0]-m[2,2]))*2
            w=(m[0,2]-m[2,0])/s;x=(m[0,1]+m[1,0])/s;y=.25*s;z=(m[1,2]+m[2,1])/s
        else:
            s=math.sqrt(max(1e-15,1+m[2,2]-m[0,0]-m[1,1]))*2
            w=(m[1,0]-m[0,1])/s;x=(m[0,2]+m[2,0])/s;y=(m[1,2]+m[2,1])/s;z=.25*s
    q=_norm([w,x,y,z])
    return -q if q[0]<0 else q

def quat_average_markley(qs):
    """Sign-invariant quaternion average; unlike arithmetic mean it handles q/-q exactly."""
    if not qs: raise ValueError("no quaternions")
    M=np.zeros((4,4),float)
    for q in qs:
        q=_norm(q); M+=np.outer(q,q)
    _,V=np.linalg.eigh(M); q=_norm(V[:,-1])
    return -q if q[0]<0 else q

def Rz(rad):
    c,s=math.cos(rad),math.sin(rad)
    return np.array([[c,-s,0],[s,c,0],[0,0,1]],float)

def _shortest_z_frame(z):
    """Rotation taking board +Z to shaft z with minimum twist."""
    z=_norm(z); ez=np.array([0.,0.,1.]); c=float(np.dot(ez,z))
    if c>1-1e-10: return np.eye(3)
    if c<-1+1e-8: return np.diag([1.,-1.,-1.])  # singular case; deterministic
    v=np.cross(ez,z); s=float(np.linalg.norm(v)); k=v/s
    K=np.array([[0,-k[2],k[1]],[k[2],0,-k[0]],[-k[1],k[0],0]],float)
    return np.eye(3)+K*s+(K@K)*(1-c)

class PoseMapper:
    """
    Canonical transform:
        R_fusion_from_brush = R_fusion_from_imu @ R_imu_mount_correction @ R_imu_from_brush
        R_board_from_brush  = R_board_from_fusion @ R_fusion_from_brush

    6-axis limitation is explicit: startup reference defines yaw; it is never
    reported as geomagnetic/absolute heading.
    """
    def __init__(self, calibration, heading_mode="startup_reference",
                 reference_seconds=1.5, reference_heading_deg=0.0,
                 ref_gyro_max_rad_s=0.08, ref_accel_error_max=0.35,
                 ref_vertical_max_deg=12.0, stale_ms=250.0,
                 imu_mount_correction=None):
        if isinstance(calibration,(str,Path)):
            calibration=json.loads(Path(calibration).read_text(encoding="utf-8"))
        self.cal=calibration
        self.C=np.asarray(calibration["accelerometer"]["C"],float)
        self.abias=np.asarray(calibration["accelerometer"]["bias_m_s2"],float)
        self.g=float(calibration["accelerometer"]["gravity_m_s2"])
        self.A=np.asarray(calibration["rigid_mount"]["R_imu_from_brush"],float)
        
        # IMU 安装补偿矩阵 (IMU 坐标系下的旋转)，用于修正物理安装与标定不符
        if imu_mount_correction is not None:
            self.imu_mount_correction = np.asarray(imu_mount_correction, float)
        else:
            # 从标定文件读取（向后兼容），默认单位阵
            self.imu_mount_correction = np.asarray(
                calibration.get("rigid_mount", {}).get("R_imu_mount_correction", np.eye(3)), float)
        
        # 有效安装矩阵 = 补偿 @ 原标定
        self.A_eff = self.imu_mount_correction @ self.A
        
        self.gbias=np.asarray(calibration["gyroscope"]["static_bias_rad_s"],float)
        self.heading_mode=heading_mode; self.reference_seconds=float(reference_seconds)
        self.reference_heading=math.radians(float(reference_heading_deg))
        self.ref_gyro_max=float(ref_gyro_max_rad_s); self.ref_accel_max=float(ref_accel_error_max)
        self.ref_vertical_max=float(ref_vertical_max_deg); self.stale_ms=float(stale_ms)
        self.reset_reference()
        if heading_mode=="calibration_session":
            self.R_board_from_fusion=np.asarray(calibration["calibration_session_board_alignment"]["R_board_from_fusion_calibration_session"],float)
            self.reference_locked=True; self.reference_state="LOCKED_CALIBRATION_SESSION"
        elif heading_mode not in ("startup_reference","relative"):
            raise ValueError("heading_mode must be startup_reference, relative, or calibration_session")

    def reset_reference(self):
        self.reference_locked=False; self.reference_state="WAITING_FOR_STILL_UP_R0"
        self.R_board_from_fusion=None; self._ref_q=[]; self._ref_valid_t0=None
        self._last_host_ns=None; self._last_frame_id=None

    def _reference_update(self,q,Rf_b,aerr,gyro_norm,now):
        if self.reference_locked:return
        shaft=Rf_b[:,2]
        vertical=math.degrees(math.acos(np.clip(float(shaft[2]),-1,1)))
        good=(aerr<=self.ref_accel_max and gyro_norm<=self.ref_gyro_max)
        if self.heading_mode=="startup_reference": good=good and vertical<=self.ref_vertical_max
        if not good:
            self._ref_q.clear();self._ref_valid_t0=None
            self.reference_state="WAITING_FOR_STILL_UP_R0" if self.heading_mode=="startup_reference" else "WAITING_FOR_STILL"
            return
        if self._ref_valid_t0 is None:self._ref_valid_t0=now
        self._ref_q.append(np.asarray(q,float));elapsed=now-self._ref_valid_t0
        self.reference_state=f"COLLECTING {min(100,int(100*elapsed/max(self.reference_seconds,1e-6)))}%"
        if elapsed<self.reference_seconds:return
        q0=quat_average_markley(self._ref_q); R0=q_wxyz_to_R(q0)@self.A_eff
        x=R0[:,0];h=math.hypot(float(x[0]),float(x[1]))
        if h<0.25:
            self.reference_state="REJECTED_X_AXIS_NEAR_VERTICAL";self._ref_q.clear();self._ref_valid_t0=None;return
        current=math.atan2(float(x[1]),float(x[0]))
        self.R_board_from_fusion=Rz(self.reference_heading-current)
        self.reference_locked=True;self.reference_state="LOCKED"

    def map_frame(self,f,host_ns=None):
        required=("q_w","q_x","q_y","q_z","ax_m_s2","ay_m_s2","az_m_s2")
        if not all(k in f for k in required):return None
        host_ns=int(host_ns or time.perf_counter_ns()); now=host_ns*1e-9
        q=np.array([f["q_w"],f["q_x"],f["q_y"],f["q_z"]],float);qn=float(np.linalg.norm(q))
        if not np.isfinite(qn) or qn<0.5:return None
        a_raw=np.array([f["ax_m_s2"],f["ay_m_s2"],f["az_m_s2"]],float)
        a=self.C@(a_raw-self.abias); an=float(np.linalg.norm(a));aerr=abs(an-self.g)
        gyro=np.array([f.get("gx_rad_s",0),f.get("gy_rad_s",0),f.get("gz_rad_s",0)],float)
        # bias is diagnostic calibration for RAW gyro only; quaternion is never bias-corrected here.
        gyro_corr=gyro-self.gbias;gn=float(np.linalg.norm(gyro_corr))
        Rf_i=q_wxyz_to_R(q);Rf_b=Rf_i@self.A_eff
        self._reference_update(q,Rf_b,aerr,gn,now)

        shaft_f=Rf_b[:,2]
        tilt=math.degrees(math.acos(np.clip(float(shaft_f[2]),-1,1)))
        fid=int(f.get("frame_id",-1))
        dt_ms=None if self._last_host_ns is None else (host_ns-self._last_host_ns)/1e6
        frame_gap=None if self._last_frame_id is None or fid<0 else max(0,fid-self._last_frame_id-1)
        self._last_host_ns=host_ns;self._last_frame_id=fid if fid>=0 else self._last_frame_id
        stale=bool(dt_ms is not None and dt_ms>self.stale_ms)

        # Dynamic confidence is deliberately a confidence/quality signal, not an orientation correction.
        qa=max(0.,1.-aerr/2.0); qg=max(0.,1.-max(0.,gn-1.5)/8.0)
        quality=float(np.clip(qa*qg*(0.5 if stale else 1.0),0,1))
        out={"frame_id":fid,"mcu_sample_time_us":int(f.get("sample_time_us",f.get("mcu_sample_time_us",0))),
             "reference_locked":bool(self.reference_locked),"reference_state":self.reference_state,
             "heading_absolute":False,"heading_reference_valid":bool(self.reference_locked),
             "q_input_norm":qn,"accel_cal_x":float(a[0]),"accel_cal_y":float(a[1]),"accel_cal_z":float(a[2]),
             "accel_norm_m_s2":an,"accel_gravity_error_m_s2":aerr,"gyro_norm_rad_s":gn,
             "tilt_from_vertical_deg":tilt,"pose_quality":quality,"frame_gap":frame_gap,"host_dt_ms":dt_ms,"stale":stale}
        if not self.reference_locked:return out

        R=self.R_board_from_fusion@Rf_b;shaft=R[:,2];bx=R[:,0];by=R[:,1];qb=matrix_to_quat_wxyz(R)
        horiz=math.hypot(float(shaft[0]),float(shaft[1]))
        shaft_az=math.degrees(math.atan2(float(shaft[1]),float(shaft[0]))) if horiz>1e-4 else None
        tilt_x=math.degrees(math.atan2(float(shaft[0]),float(shaft[2])))
        tilt_y=math.degrees(math.atan2(float(shaft[1]),float(shaft[2])))
        heading=math.degrees(math.atan2(float(bx[1]),float(bx[0]))) if math.hypot(float(bx[0]),float(bx[1]))>1e-4 else None

        # Twist/roll around brush shaft relative to the minimum-twist frame.
        T=_shortest_z_frame(shaft);x0=T[:,0];y0=T[:,1]
        twist=_wrap_deg(math.degrees(math.atan2(float(np.dot(bx,y0)),float(np.dot(bx,x0)))))

        Rb_i=self.R_board_from_fusion@Rf_i
        omega_board=Rb_i@gyro_corr
        omega_brush=self.A_eff.T@gyro_corr
        out.update({
            "brush_axis_x":float(shaft[0]),"brush_axis_y":float(shaft[1]),"brush_axis_z":float(shaft[2]),
            "brush_x_axis_x":float(bx[0]),"brush_x_axis_y":float(bx[1]),"brush_x_axis_z":float(bx[2]),
            "brush_y_axis_x":float(by[0]),"brush_y_axis_y":float(by[1]),"brush_y_axis_z":float(by[2]),
            "tilt_x_deg":tilt_x,"tilt_y_deg":tilt_y,"shaft_azimuth_deg":shaft_az,
            "heading_rel_deg":heading,"roll_about_brush_rel_deg":twist,
            "brush_q_w":float(qb[0]),"brush_q_x":float(qb[1]),"brush_q_y":float(qb[2]),"brush_q_z":float(qb[3]),
            "omega_board_x_rad_s":float(omega_board[0]),"omega_board_y_rad_s":float(omega_board[1]),"omega_board_z_rad_s":float(omega_board[2]),
            "omega_brush_x_rad_s":float(omega_brush[0]),"omega_brush_y_rad_s":float(omega_brush[1]),"omega_brush_z_rad_s":float(omega_brush[2]),
        })
        return out

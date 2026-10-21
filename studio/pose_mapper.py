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

def _yaw_deg(R):
    """Board-frame yaw of a yaw-only (Rz) rotation, in degrees."""
    return math.degrees(math.atan2(float(R[1,0]),float(R[0,0])))

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
                 ref_vertical_max_deg=8.0, stale_ms=250.0,
                 imu_mount_correction=None, reference_enabled=True,
                 reference_min_samples=20, ref_spread_max_deg=3.0,
                 restore_reference=None, auto_restore_tolerance_deg=30.0,
                 persist_reference_path=None):
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
        self.reference_enabled=bool(reference_enabled)
        self.reference_min_samples=int(reference_min_samples)
        self.ref_spread_max=float(ref_spread_max_deg)
        # Auto-restore: adopt the saved R0 only if the live upright pose agrees.
        self._restore_R=(None if restore_reference is None
                         else np.asarray(restore_reference,float).reshape(3,3))
        self._restore_tol=float(auto_restore_tolerance_deg)
        self._restore_pending=bool(self._restore_R is not None and self.reference_enabled)
        self._restore_failed=""
        self._persist_path=(Path(persist_reference_path)
                            if persist_reference_path else None)
        self._last_token=None
        self._last_sample_us=None
        self._last_host_ns=None
        self._last_frame_id=None
        self._last_pose=None
        self.reset_reference()
        if heading_mode=="calibration_session":
            self.R_board_from_fusion=np.asarray(calibration["calibration_session_board_alignment"]["R_board_from_fusion_calibration_session"],float)
            self.reference_locked=True; self.reference_state="LOCKED_CALIBRATION_SESSION"
        elif heading_mode not in ("startup_reference","relative"):
            raise ValueError("heading_mode must be startup_reference, relative, or calibration_session")

    def reset_reference(self, disarm=False):
        """Invalidate heading; preserve input freshness tracking across re-arms."""
        if disarm:
            self.reference_enabled=False
        self.reference_locked=False
        self.reference_state=("WAITING_FOR_STILL_UP_R0" if self.reference_enabled
                              else "WAITING_FOR_UP0_CONFIRMATION")
        self._restore_pending=bool(self._restore_R is not None and self.reference_enabled)
        self._restore_failed=""
        self.R_board_from_fusion=None
        self._ref_q=[]
        self._ref_valid_t0=None
        self._ref_last_valid_t=None

    def disarm_reference(self):
        self.reset_reference(disarm=True)

    def arm_reference(self):
        """Call ONLY after user places R0 mark toward board +X and confirms it."""
        if self.heading_mode != "startup_reference":
            raise RuntimeError("UP0 arming requires startup_reference mode")
        self.reference_enabled=True
        self.reset_reference()
        self._restore_pending=False  # a manual confirmation outranks the saved R0

    def _persist_locked_reference(self):
        """Store the live R0 so the next launch can restore it without a click."""
        if self._persist_path is None or self.R_board_from_fusion is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(json.dumps({
                "schema":"ebrush.up0_reference.v1",
                "saved_unix":time.time(),
                "reference_state":self.reference_state,
                "R_board_from_fusion":self.R_board_from_fusion.tolist(),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _reference_update(self,q,Rf_b,aerr,gyro_norm,now,imu_data_valid=True):
        if self.reference_locked:
            return
        if not self.reference_enabled:
            self.reference_state="WAITING_FOR_UP0_CONFIRMATION"
            return
        if self._restore_failed:
            # Latched until a manual UP0 confirmation re-arms the reference.
            self.reference_state=self._restore_failed
            return
        if not imu_data_valid:
            self._ref_q.clear()
            self._ref_valid_t0=None
            self.reference_state="REJECTED_INVALID_IMU_DATA"
            return
        shaft=Rf_b[:,2]
        vertical=math.degrees(math.acos(np.clip(float(shaft[2]),-1,1)))
        moving=(aerr>self.ref_accel_max or gyro_norm>self.ref_gyro_max)
        not_vertical=(self.heading_mode=="startup_reference" and
                      vertical>self.ref_vertical_max)
        if moving or not_vertical:
            self._ref_q.clear()
            self._ref_valid_t0=None
            self.reference_state=("REJECTED_NOT_UPRIGHT" if not_vertical
                                  else "REJECTED_MOVING")
            return
        if self._ref_last_valid_t is not None and now-self._ref_last_valid_t>0.25:
            # A stream outage breaks the continuous stillness window.
            self._ref_q.clear()
            self._ref_valid_t0=None
        self._ref_last_valid_t=now
        if self._ref_valid_t0 is None:
            self._ref_valid_t0=now
        self._ref_q.append(np.asarray(q,float))
        elapsed=now-self._ref_valid_t0
        self.reference_state=f"COLLECTING {min(100,int(100*elapsed/max(self.reference_seconds,1e-6)))}%"
        if elapsed<self.reference_seconds or len(self._ref_q)<self.reference_min_samples:
            return
        q0=quat_average_markley(self._ref_q)
        # Check actual quaternion consistency, not just gyro magnitude.
        spread=max(math.degrees(2*math.acos(
            float(np.clip(abs(np.dot(_norm(q0),_norm(qi))),0.,1.))))
            for qi in self._ref_q)
        if spread>self.ref_spread_max:
            self.reference_state="REJECTED_ORIENTATION_DRIFT"
            self._ref_q.clear()
            self._ref_valid_t0=None
            return
        R0=q_wxyz_to_R(q0)@self.A_eff
        x=R0[:,0]
        h=math.hypot(float(x[0]),float(x[1]))
        if h<0.25:
            self.reference_state="REJECTED_X_AXIS_NEAR_VERTICAL"
            self._ref_q.clear()
            self._ref_valid_t0=None
            return
        current=math.atan2(float(x[1]),float(x[0]))
        R_live=Rz(self.reference_heading-current)
        if self._restore_pending and self._restore_R is not None:
            dyaw=_wrap_deg(_yaw_deg(R_live)-_yaw_deg(self._restore_R))
            if abs(dyaw)>self._restore_tol:
                self._ref_q.clear()
                self._ref_valid_t0=None
                self._restore_pending=False
                self._restore_failed=f"REJECTED_R0_MISMATCH({dyaw:+.0f}deg)"
                self.reference_state=self._restore_failed
                return
            self.R_board_from_fusion=self._restore_R
            self.reference_locked=True
            self._restore_pending=False
            self.reference_state="LOCKED_RESTORED"
            return
        self.R_board_from_fusion=R_live
        self.reference_locked=True
        self.reference_state="LOCKED"
        self._persist_locked_reference()

    @staticmethod
    def _sample_key(f):
        frame=f.get("frame_id")
        sample=f.get("sample_time_us",f.get("mcu_sample_time_us"))
        try:
            frame=int(frame) if frame is not None else -1
            sample=int(sample) if sample is not None else 0
        except (ValueError,TypeError):
            return None
        if frame>=0 and sample>0:
            return ("frame_time",frame,sample)
        if sample>0:
            return ("sample_time",sample)
        if frame>=0:
            return ("frame_id",frame)
        return None

    def map_frame(self,f,host_ns=None):
        required=("q_w","q_x","q_y","q_z","ax_m_s2","ay_m_s2","az_m_s2")
        if not all(k in f for k in required):
            return None
        now_ns=int(host_ns if host_ns is not None else time.perf_counter_ns())
        now=now_ns*1e-9
        token=self._sample_key(f)
        if token is not None and token==self._last_token:
            if self._last_pose is None:
                return None
            out=dict(self._last_pose)
            out.update({"frame_fresh":False,
                        "reference_state":self.reference_state,
                        "reference_locked":self.reference_locked,
                        "heading_reference_valid":self.reference_locked,
                        "reference_enabled":self.reference_enabled})
            if not self.reference_locked:
                for k in list(out):
                    if k.startswith(("brush_axis_", "brush_x_axis_", "brush_y_axis_",
                                     "brush_q_", "tilt_x_", "tilt_y_", "omega_")) or k in (
                            "roll_about_brush_rel_deg","shaft_azimuth_deg",
                            "heading_rel_deg"):
                        out.pop(k,None)
            return out

        q=np.array([f["q_w"],f["q_x"],f["q_y"],f["q_z"]],float)
        qn=float(np.linalg.norm(q))
        if not np.isfinite(qn) or not 0.5<=qn<=1.5:
            return None
        a_raw=np.array([f["ax_m_s2"],f["ay_m_s2"],f["az_m_s2"]],float)
        if not np.isfinite(a_raw).all():
            return None
        a=self.C@(a_raw-self.abias)
        an=float(np.linalg.norm(a))
        aerr=abs(an-self.g)
        # Never synthesize zero angular velocity when gyro channels are missing.
        gyro_present=all(k in f for k in ("gx_rad_s","gy_rad_s","gz_rad_s"))
        gyro=np.array([f.get("gx_rad_s",math.nan),
                       f.get("gy_rad_s",math.nan),
                       f.get("gz_rad_s",math.nan)],float)
        gyro_valid=gyro_present and bool(np.isfinite(gyro).all())
        gyro_corr=gyro-self.gbias if gyro_valid else np.full(3,math.nan)
        gn=float(np.linalg.norm(gyro_corr)) if gyro_valid else math.inf
        # A valid quaternion and accelerometer are enough to display diagnostics;
        # a complete gyro is mandatory to acquire the UP0 reference.
        imu_valid=gyro_valid and bool(np.isfinite(an)) and 0.5*self.g<an<1.5*self.g
        # BRUSH bits 5/6/7: IMU_VALID, IMU_FRESH, POSE_VALID.
        # Non-fresh packets are normal when telemetry runs faster than IMU.
        # They must NOT count as samples or reset a valid acquisition window.
        status=int(f.get("device_status",0xE0))
        imu_integrity_ok=(status & 0xA0)==0xA0
        imu_new=(status & 0x40)!=0
        imu_valid=bool(imu_valid and imu_integrity_ok and imu_new)
        Rf_i=q_wxyz_to_R(q)
        Rf_b=Rf_i@self.A_eff
        shaft_f=Rf_b[:,2]
        tilt=math.degrees(math.acos(np.clip(float(shaft_f[2]),-1,1)))

        fid=int(f.get("frame_id",-1))
        sample_us=int(f.get("sample_time_us",f.get("mcu_sample_time_us",0)))
        reboot=(self._last_sample_us is not None and sample_us>0 and
                sample_us<self._last_sample_us)
        if reboot:
            if self._restore_R is not None:
                self.reset_reference()
                self.reference_state="DEVICE_REBOOT_WAITING_UP0"
            else:
                self.disarm_reference()
                self.reference_state="DEVICE_REBOOT_CONFIRM_UP0"
        dt_ms=None if self._last_host_ns is None else (now_ns-self._last_host_ns)/1e6
        frame_gap=(None if self._last_frame_id is None or fid<0 else
                   (fid-self._last_frame_id-1)&0xFFFFFFFF)
        stale=bool(dt_ms is not None and dt_ms>self.stale_ms)
        if stale and not self.reference_locked:
            self._ref_q.clear()
            self._ref_valid_t0=None
        self._last_token=token
        self._last_host_ns=now_ns
        self._last_frame_id=fid if fid>=0 else self._last_frame_id
        if sample_us>0:
            self._last_sample_us=sample_us

        if not reboot:
            if not imu_integrity_ok or not gyro_valid:
                self._reference_update(q,Rf_b,aerr,gn,now,False)
            elif imu_new:
                self._reference_update(q,Rf_b,aerr,gn,now,imu_valid)
            elif not self.reference_locked and self.reference_enabled:
                if (self._ref_last_valid_t is not None and
                        now-self._ref_last_valid_t>0.25):
                    self._ref_q.clear()
                    self._ref_valid_t0=None
                    self._ref_last_valid_t=None
                    self.reference_state="WAITING_FOR_FRESH_IMU"
        qa=max(0.,1.-aerr/2.0)
        qg=max(0.,1.-max(0.,gn-1.5)/8.0) if gyro_valid else 0.
        quality=float(np.clip(qa*qg*(0.5 if stale else 1.0),0,1))
        out={
            "frame_id":fid,"mcu_sample_time_us":sample_us,
            "frame_fresh":True,"imu_data_valid":imu_valid,
            "reference_locked":bool(self.reference_locked),
            "reference_enabled":bool(self.reference_enabled),
            "reference_state":self.reference_state,
            "heading_absolute":False,
            "heading_reference_valid":bool(self.reference_locked),
            "q_input_norm":qn,
            "accel_cal_x":float(a[0]),"accel_cal_y":float(a[1]),"accel_cal_z":float(a[2]),
            "accel_norm_m_s2":an,"accel_gravity_error_m_s2":aerr,
            "gyro_norm_rad_s":gn,"up0_tilt_deg":tilt,
            "up0_vertical_valid":bool(tilt<=self.ref_vertical_max),
            "tilt_from_vertical_deg":tilt,
            "pose_quality":quality,"frame_gap":frame_gap,"host_dt_ms":dt_ms,
            "stale":stale,"device_reboot_detected":reboot,
            "r_board_from_fusion":(self.R_board_from_fusion.tolist()
                                   if self.R_board_from_fusion is not None else None),
        }
        if not self.reference_locked:
            self._last_pose=dict(out)
            return out

        R=self.R_board_from_fusion@Rf_b
        shaft=R[:,2]
        bx=R[:,0]
        by=R[:,1]
        qb=matrix_to_quat_wxyz(R)
        horiz=math.hypot(float(shaft[0]),float(shaft[1]))
        shaft_az=(math.degrees(math.atan2(float(shaft[1]),float(shaft[0])))
                  if horiz>1e-4 else None)
        tilt_x=math.degrees(math.atan2(float(shaft[0]),float(shaft[2])))
        tilt_y=math.degrees(math.atan2(float(shaft[1]),float(shaft[2])))
        heading=(math.degrees(math.atan2(float(bx[1]),float(bx[0])))
                 if math.hypot(float(bx[0]),float(bx[1]))>1e-4 else None)
        T=_shortest_z_frame(shaft)
        x0,y0=T[:,0],T[:,1]
        twist=_wrap_deg(math.degrees(math.atan2(
            float(np.dot(bx,y0)),float(np.dot(bx,x0)))))
        Rb_i=self.R_board_from_fusion@Rf_i
        omega_board=Rb_i@gyro_corr
        omega_brush=self.A_eff.T@gyro_corr
        out.update({
            "brush_axis_x":float(shaft[0]),"brush_axis_y":float(shaft[1]),"brush_axis_z":float(shaft[2]),
            "brush_x_axis_x":float(bx[0]),"brush_x_axis_y":float(bx[1]),"brush_x_axis_z":float(bx[2]),
            "brush_y_axis_x":float(by[0]),"brush_y_axis_y":float(by[1]),"brush_y_axis_z":float(by[2]),
            "tilt_x_deg":tilt_x,"tilt_y_deg":tilt_y,"shaft_azimuth_deg":shaft_az,
            "heading_rel_deg":heading,"roll_about_brush_rel_deg":twist,
            "brush_q_w":float(qb[0]),"brush_q_x":float(qb[1]),
            "brush_q_y":float(qb[2]),"brush_q_z":float(qb[3]),
            "omega_board_x_rad_s":float(omega_board[0]),
            "omega_board_y_rad_s":float(omega_board[1]),
            "omega_board_z_rad_s":float(omega_board[2]),
            "omega_brush_x_rad_s":float(omega_brush[0]),
            "omega_brush_y_rad_s":float(omega_brush[1]),
            "omega_brush_z_rad_s":float(omega_brush[2]),
        })
        self._last_pose=dict(out)
        return out

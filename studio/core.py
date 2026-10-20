import json,math,time,importlib.util
from pathlib import Path
import numpy as np
def norm(v):
    v=np.asarray(v,float);n=np.linalg.norm(v);return v/n if n else v
def qR(q):
    w,x,y,z=norm(q);return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
def Rz(a):
    c,s=math.cos(a),math.sin(a);return np.array([[c,-s,0],[s,c,0],[0,0,1.]])
class PoseMapper:
    def __init__(self,p):
        c=json.loads(Path(p).read_text());self.C=np.array(c["accelerometer"]["C"]);self.b=np.array(c["accelerometer"]["bias_m_s2"]);self.A=np.array(c["rigid_mount"]["R_imu_from_brush"]);self.g=c["accelerometer"]["gravity_m_s2"];self.reset_reference()
    def reset_reference(self):self.t0=None;self.qs=[];self.B=None
    @property
    def locked(self):return self.B is not None
    def map(self,f):
        keys=("q_w","q_x","q_y","q_z","ax_m_s2","ay_m_s2","az_m_s2")
        if not all(k in f for k in keys):return None
        q=np.array([f[k] for k in keys[:4]],float);R=qR(q)@self.A
        if not self.locked:
            now=time.perf_counter()
            if self.t0 is None:self.t0=now
            self.qs.append(q)
            if now-self.t0>1.5:
                q0=norm(np.mean([(-x if np.dot(x,self.qs[0])<0 else x) for x in self.qs],axis=0));R0=qR(q0)@self.A;x=R0[:,0]
                if np.hypot(x[0],x[1])>.25:self.B=Rz(-math.atan2(x[1],x[0]))
        a=self.C@(np.array([f[k] for k in keys[4:]])-self.b)
        o={"reference_locked":self.locked,"accel_norm_m_s2":float(np.linalg.norm(a))}
        if self.locked:
            M=self.B@R;z=M[:,2];x=M[:,0]
            o.update(brush_axis_x=float(z[0]),brush_axis_y=float(z[1]),brush_axis_z=float(z[2]),tilt_from_vertical_deg=math.degrees(math.acos(np.clip(z[2],-1,1))),heading_rel_deg=math.degrees(math.atan2(x[1],x[0])),heading_absolute=False)
        return o
class HotPatch:
    def __init__(self,p):self.p=Path(p);self.m=0;self.mod=None;self.error=""
    def run(self,state,params):
        try:
            mt=self.p.stat().st_mtime
            if mt>self.m:
                sp=importlib.util.spec_from_file_location("_patch",self.p);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);self.mod=m;self.m=mt
            return self.mod.process(state,params) if self.mod else {}
        except Exception as e:self.error=str(e);return {}

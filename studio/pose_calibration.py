from __future__ import annotations
import json, math, time
from pathlib import Path
import numpy as np

STAGES=("IDLE","ON_STAND","PICK_UP","UP_R0","X_TEST","Y_TEST","ROLL_TEST","REVIEW","COMMITTED")
def _clamp(x,a,b): return max(a,min(b,x))
def _unit(v):
 v=np.asarray(v,float);n=float(np.linalg.norm(v));return v/n if n>1e-12 else v

class PoseCalibrationSession:
 """Workflow/lifecycle manager above PoseMapper. It never rewrites sensor calibration.
 The PoseMapper remains the runtime transform engine; this class drives reference
 acquisition, validates directional response, and commits versioned mapping profiles.
 """
 def __init__(self,root,pose_mapper,mapper_debug):
  self.root=Path(root);self.pose=pose_mapper;self.mapper=mapper_debug
  self.dir=self.root/"config"/"pose_mapping_profiles";self.dir.mkdir(parents=True,exist_ok=True)
  self.active_path=self.root/"config"/"pose_mapping_profile_active.json"
  self.reset()
 def reset(self):
  self.stage="IDLE";self.started_unix=None;self.samples={};self.results={};self.message="Ready"
  self._still_t0=None;self._motion_seen=False;self._stage_t0=None;self._roll0=None
 def start(self):
  self.reset();self.stage="ON_STAND";self.started_unix=time.time();self._stage_t0=time.monotonic()
  self.message="Keep brush still on stand";return self.state()
 def cancel(self): self.reset();return self.state()
 def advance(self):
  nxt={"ON_STAND":"PICK_UP","PICK_UP":"UP_R0","UP_R0":"X_TEST","X_TEST":"Y_TEST","Y_TEST":"ROLL_TEST","ROLL_TEST":"REVIEW"}.get(self.stage)
  if nxt:self._enter(nxt)
  return self.state()
 def retry(self):
  if self.stage in ("REVIEW","COMMITTED"):self._enter("UP_R0")
  else:self._enter(self.stage)
  return self.state()
 def _enter(self,s):
  self.stage=s;self._stage_t0=time.monotonic();self._still_t0=None
  if s=="UP_R0":self.pose.reset_reference()
  if s=="ROLL_TEST":self._roll0=None
  self.message={"PICK_UP":"Remove brush from stand","UP_R0":"Hold UP_R0 still until reference locks",
   "X_TEST":"Tilt brush tail toward board +X / right","Y_TEST":"Tilt brush tail toward board +Y / away",
   "ROLL_TEST":"Keep shaft near vertical and rotate brush about its shaft","REVIEW":"Review and commit mapping"}.get(s,s)
 def _raw_metrics(self,b):
  if not b:return {}
  a=np.array([b.get("ax_m_s2",0),b.get("ay_m_s2",0),b.get("az_m_s2",0)],float)
  g=np.array([b.get("gx_rad_s",0),b.get("gy_rad_s",0),b.get("gz_rad_s",0)],float)
  return {"accel_norm":float(np.linalg.norm(a)),"gyro_norm":float(np.linalg.norm(g))}
 def update(self,brush,pose):
  if self.stage=="IDLE" or not brush:return self.state()
  now=time.monotonic();m=self._raw_metrics(brush);gyro=m.get("gyro_norm",99);an=m.get("accel_norm",0)
  still=gyro<0.08 and abs(an-9.80665)<0.5
  if self.stage=="ON_STAND":
   if still:
    if self._still_t0 is None:self._still_t0=now
    progress=_clamp((now-self._still_t0)/1.5,0,1)
    self.results["stand_stability"]=progress
    if progress>=1:
     self.samples["stand"]={"accel_norm":an,"gyro_norm":gyro,"unix":time.time()}
     self._enter("PICK_UP")
   else:self._still_t0=None;self.results["stand_stability"]=0
  elif self.stage=="PICK_UP":
   if gyro>0.18 or abs(an-9.80665)>0.9:
    self._motion_seen=True;self.results["pickup_detected"]=True;self._enter("UP_R0")
  elif self.stage=="UP_R0":
   self.results["reference_state"]=getattr(self.pose,"reference_state","")
   if getattr(self.pose,"reference_locked",False):
    self.results["up_r0_locked"]=True
    if pose:self.results["up_r0_tilt_deg"]=float(pose.get("tilt_from_vertical_deg",0))
    self._enter("X_TEST")
  elif self.stage=="X_TEST" and pose and pose.get("reference_locked"):
   tx=float(pose.get("tilt_x_deg",0));ty=float(pose.get("tilt_y_deg",0))
   self.results["x_live"]={"tilt_x_deg":tx,"tilt_y_deg":ty}
   # Require a meaningful +X response and dominance over cross-axis response.
   if tx>=15 and abs(tx)>=1.5*abs(ty):
    self.results["x_test"]={"pass":True,"tilt_x_deg":tx,"cross_y_deg":ty};self._enter("Y_TEST")
  elif self.stage=="Y_TEST" and pose and pose.get("reference_locked"):
   tx=float(pose.get("tilt_x_deg",0));ty=float(pose.get("tilt_y_deg",0))
   self.results["y_live"]={"tilt_x_deg":tx,"tilt_y_deg":ty}
   if ty>=15 and abs(ty)>=1.5*abs(tx):
    self.results["y_test"]={"pass":True,"tilt_y_deg":ty,"cross_x_deg":tx};self._enter("ROLL_TEST")
  elif self.stage=="ROLL_TEST" and pose and pose.get("reference_locked"):
   tilt=float(pose.get("tilt_from_vertical_deg",99));r=pose.get("roll_about_brush_rel_deg")
   if r is not None and tilt<15:
    if self._roll0 is None:self._roll0=float(r)
    d=(float(r)-self._roll0+180)%360-180
    self.results["roll_live_delta_deg"]=d
    if abs(d)>=45:
     self.results["roll_test"]={"relative_pass":True,"delta_deg":d,"absolute_verified":False};self._enter("REVIEW")
  return self.state()
 def quality(self):
  score=0
  score+=20 if self.samples.get("stand") else 0
  score+=25 if self.results.get("up_r0_locked") else 0
  score+=20 if self.results.get("x_test",{}).get("pass") else 0
  score+=20 if self.results.get("y_test",{}).get("pass") else 0
  score+=15 if self.results.get("roll_test",{}).get("relative_pass") else 0
  return score
 def state(self):
  return {"schema":"ebrush.pose_calibration_session.v1","stage":self.stage,"message":self.message,
   "started_unix":self.started_unix,"quality_score":self.quality(),"results":self.results,
   "mapper":self.mapper.state(),"can_commit":self.stage=="REVIEW" and self.quality()>=80,
   "limitations":{"heading_absolute":False,"roll_absolute_verified":False}}
 def commit(self,name=None):
  if self.stage!="REVIEW":raise RuntimeError("calibration not in REVIEW")
  if not name:name=time.strftime("posemap_%Y%m%d_%H%M%S")
  name="".join(c for c in str(name) if c.isalnum() or c in "-_") or "posemap"
  # Save mapper override as a separately rollback-able version too.
  mv=self.mapper.save(name)
  prof={"schema":"ebrush.pose_mapping.v2","name":name,"created_unix":time.time(),
   "parent":self._active_name(),"sensor_calibration":self.pose.cal.get("created_from"),
   "rigid_mount":{"R_imu_from_brush":np.asarray(self.pose.A).tolist()},
   "startup_reference":{"method":"UP_R0","heading_absolute":False,"reference_state":self.results.get("reference_state")},
   "validation":self.results,"quality_score":self.quality(),"mapper_override":mv,
   "limitations":{"absolute_heading":False,"absolute_roll":"UNVERIFIED"}}
  p=self.dir/(name+".json");p.write_text(json.dumps(prof,ensure_ascii=False,indent=2),encoding="utf-8")
  self.active_path.write_text(json.dumps({"active":name,"path":str(p)},ensure_ascii=False,indent=2),encoding="utf-8")
  self.stage="COMMITTED";self.message="Mapping profile committed";return prof
 def _active_name(self):
  try:return json.loads(self.active_path.read_text(encoding="utf-8")).get("active")
  except:return None
 def list_profiles(self):
  out=[]
  for p in sorted(self.dir.glob("*.json"),reverse=True):
   try:
    d=json.loads(p.read_text(encoding="utf-8"));out.append({"name":d.get("name",p.stem),"quality_score":d.get("quality_score"),"created_unix":d.get("created_unix"),"active":d.get("name")==self._active_name()})
   except:pass
  return out
 def load_profile(self,name):
  p=self.dir/(str(name)+".json")
  d=json.loads(p.read_text(encoding="utf-8"));mo=d.get("mapper_override",{})
  # Restore its non-destructive mapper override; base sensor calibration remains untouched.
  self.mapper.board_deg=np.asarray(mo.get("board_xyz_deg",[0,0,0]),float)
  self.mapper.brush_deg=np.asarray(mo.get("brush_xyz_deg",[0,0,0]),float)
  self.mapper.active_version=str(name);self.mapper.persist()
  self.active_path.write_text(json.dumps({"active":str(name),"path":str(p)},indent=2),encoding="utf-8")
  return d

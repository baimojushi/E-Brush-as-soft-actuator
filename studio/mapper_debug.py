from __future__ import annotations
import json,math,time
from pathlib import Path
import numpy as np
from pose_mapper import matrix_to_quat_wxyz

def Rx(a):
 c,s=math.cos(a),math.sin(a);return np.array([[1,0,0],[0,c,-s],[0,s,c]],float)
def Ry(a):
 c,s=math.cos(a),math.sin(a);return np.array([[c,0,s],[0,1,0],[-s,0,c]],float)
def Rz(a):
 c,s=math.cos(a),math.sin(a);return np.array([[c,-s,0],[s,c,0],[0,0,1]],float)
def euler_xyz_deg(v):
 x,y,z=np.radians(np.asarray(v,float));return Rz(z)@Ry(y)@Rx(x)

class MapperDebug:
 """Non-destructive SO(3) mapper override + persistent version history.
 R_final = R_board_debug @ R_mapper @ R_brush_debug
 Base calibration is never rewritten by quick debugging.
 """
 def __init__(self,root):
  self.root=Path(root);self.dir=self.root/"config"/"mapper_versions";self.dir.mkdir(parents=True,exist_ok=True)
  self.active_path=self.root/"config"/"mapper_debug_active.json"
  self.board_deg=np.zeros(3);self.brush_deg=np.zeros(3);self.active_version=None
  self.load_active()
 def state(self):
  return {"schema":"ebrush.mapper_debug.v1","board_xyz_deg":self.board_deg.tolist(),"brush_xyz_deg":self.brush_deg.tolist(),
          "active_version":self.active_version,"formula":"R_board_debug @ R_mapper @ R_brush_debug"}
 def load_active(self):
  if self.active_path.exists():
   try:
    d=json.loads(self.active_path.read_text(encoding="utf-8"));self.board_deg=np.asarray(d.get("board_xyz_deg",[0,0,0]),float);self.brush_deg=np.asarray(d.get("brush_xyz_deg",[0,0,0]),float);self.active_version=d.get("active_version")
   except:pass
 def persist(self):
  self.active_path.write_text(json.dumps(self.state(),ensure_ascii=False,indent=2),encoding="utf-8")
 def set_board(self,i,v):self.board_deg[i]=float(v);self.active_version=None;self.persist();return self.state()
 def set_brush(self,i,v):self.brush_deg[i]=float(v);self.active_version=None;self.persist();return self.state()
 def reset(self):self.board_deg[:]=0;self.brush_deg[:]=0;self.active_version=None;self.persist();return self.state()
 def apply(self,p):
  if not p or not p.get("reference_locked") or "brush_axis_x" not in p:return p
  q=dict(p)
  x=np.array([p["brush_x_axis_x"],p["brush_x_axis_y"],p["brush_x_axis_z"]],float)
  y=np.array([p["brush_y_axis_x"],p["brush_y_axis_y"],p["brush_y_axis_z"]],float)
  z=np.array([p["brush_axis_x"],p["brush_axis_y"],p["brush_axis_z"]],float)
  R=np.column_stack([x,y,z]);R=euler_xyz_deg(self.board_deg)@R@euler_xyz_deg(self.brush_deg)
  x,y,z=R[:,0],R[:,1],R[:,2];qq=matrix_to_quat_wxyz(R)
  q.update({"brush_axis_x":float(z[0]),"brush_axis_y":float(z[1]),"brush_axis_z":float(z[2]),
   "brush_x_axis_x":float(x[0]),"brush_x_axis_y":float(x[1]),"brush_x_axis_z":float(x[2]),
   "brush_y_axis_x":float(y[0]),"brush_y_axis_y":float(y[1]),"brush_y_axis_z":float(y[2]),
   "brush_q_w":float(qq[0]),"brush_q_x":float(qq[1]),"brush_q_y":float(qq[2]),"brush_q_z":float(qq[3]),
   "mapper_debug_applied":True,"mapper_debug_board_xyz_deg":self.board_deg.tolist(),"mapper_debug_brush_xyz_deg":self.brush_deg.tolist()})
  return q
 def save(self,name=None):
  if not name:name=time.strftime("map_%Y%m%d_%H%M%S")
  name="".join(c for c in str(name) if c.isalnum() or c in "-_") or "map"
  d=self.state();d["name"]=name;d["saved_unix"]=time.time()
  (self.dir/(name+".json")).write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8");self.active_version=name;self.persist();return d
 def list_versions(self):
  out=[]
  for p in sorted(self.dir.glob("*.json"),reverse=True):
   try:
    d=json.loads(p.read_text(encoding="utf-8"));out.append({"name":d.get("name",p.stem),"saved_unix":d.get("saved_unix"),"board_xyz_deg":d.get("board_xyz_deg"),"brush_xyz_deg":d.get("brush_xyz_deg")})
   except:pass
  return out
 def load(self,name):
  p=self.dir/(str(name)+".json")
  if not p.exists():raise FileNotFoundError(name)
  d=json.loads(p.read_text(encoding="utf-8"));self.board_deg=np.asarray(d["board_xyz_deg"],float);self.brush_deg=np.asarray(d["brush_xyz_deg"],float);self.active_version=str(name);self.persist();return self.state()

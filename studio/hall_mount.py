from __future__ import annotations
import json
import threading
from collections import deque
from pathlib import Path
import numpy as np

def _norm(v):
    v=np.asarray(v,dtype=float)
    n=float(np.linalg.norm(v))
    return v/n if n>1e-12 else v

class HallMountModel:
    """Hall visualization / field-frame model with world-fixed flux semantics."""
    def __init__(self, root, history_len=240):
        self.root=Path(root)
        self.lock=threading.RLock()
        self.path=self.root/"config"/"hall_sensor_mount.json"
        self.history_world=deque(maxlen=int(history_len))
        self.history_local=deque(maxlen=int(history_len))
        if self.path.exists():
            data=json.loads(self.path.read_text(encoding="utf-8"))
        else:
            data=self._default_payload()
            self.path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        self.data=data
        self.R_brush_from_hall=np.asarray(data["R_brush_from_hall"],dtype=float)

    def _default_payload(self):
        return {
            "schema":"ebrush.hall_mount.v2",
            "method":"single_static_pose_underconstrained_identity_seed",
            "created_from":"user_static_mean_2s_400frames_200Hz",
            "hall_mean_mT":{"bx":-0.2042,"by":-0.1026,"bz":-0.1427,"B_total":0.2702},
            "hall_std_mT":{"bx":0.0184,"by":0.0170,"bz":0.0105},
            "imu_quaternion_mean_wxyz":[0.713603,-0.034366,0.699229,0.025871],
            "brush_axis_world_mean":[0.0208,-0.0111,-0.9997],
            "R_brush_from_hall":np.eye(3).tolist(),
            "limitations":{
                "fully_observable":False,
                "note":"Single static Hall vector does not determine full Hall→Brush extrinsics. v1.1 keeps an explicit seed mount and uses the physically correct world-field model B_world = R_world_from_brush @ R_brush_from_hall @ B_hall."
            }
        }

    def _basis_from_pose(self, pose):
        x=np.array([pose["brush_x_axis_x"],pose["brush_x_axis_y"],pose["brush_x_axis_z"]],float)
        y=np.array([pose["brush_y_axis_x"],pose["brush_y_axis_y"],pose["brush_y_axis_z"]],float)
        z=np.array([pose["brush_axis_x"],pose["brush_axis_y"],pose["brush_axis_z"]],float)
        return np.column_stack([x,y,z])

    def process(self, brush, pose=None):
        with self.lock:
            bx=brush.get("bx_mT"); by=brush.get("by_mT"); bz=brush.get("bz_mT")
            if bx is None or by is None or bz is None:
                return {"present":False,"reason":"no_hall_fields_in_brush_stream","schema":"ebrush.hall_state.v2"}

            Bh=np.array([float(bx),float(by),float(bz)],float)
            B=float(np.linalg.norm(Bh))
            Bb=self.R_brush_from_hall @ Bh
            self.history_local.append(Bb.copy())
            out={
                "schema":"ebrush.hall_state.v2",
                "present":True,
                "method":self.data.get("method"),
                "mount_seed":self.data.get("method"),
                "mount_observability":("multi_pose_static_validated"
                     if self.data.get("calibration_quality",{}).get("validation_passed")
                     else "underconstrained_single_static_pose"),
                "active_profile":self.data.get("active_profile"),
                "uniform_field_assumption":True,
                "bx_mT":float(Bh[0]),"by_mT":float(Bh[1]),"bz_mT":float(Bh[2]),
                "B_total_mT":B,
                "sensor_components_mT":{"x":float(Bh[0]),"y":float(Bh[1]),"z":float(Bh[2])},
                "brush_components_mT":{"x":float(Bb[0]),"y":float(Bb[1]),"z":float(Bb[2])},
                "sensor_unit":_norm(Bh).tolist() if B>1e-12 else [0.0,0.0,0.0],
                "brush_unit":_norm(Bb).tolist() if B>1e-12 else [0.0,0.0,0.0],
                "R_brush_from_hall":self.R_brush_from_hall.tolist(),
                "sensor_origin_ratio_on_brush":0.65,
                "global_field_concept":"world_fixed",
            }

            if pose and pose.get("reference_locked"):
                Rwb=self._basis_from_pose(pose)
                Bw=Rwb @ Bb
                self.history_world.append(Bw.copy())
                W=np.array(list(self.history_world),float)
                mean=W.mean(axis=0)
                std=W.std(axis=0)
                mag=float(np.linalg.norm(mean))
                out.update({
                    "pose_locked":True,
                    "world_components_mT":{"x":float(Bw[0]),"y":float(Bw[1]),"z":float(Bw[2])},
                    "world_unit":_norm(Bw).tolist() if B>1e-12 else [0.0,0.0,0.0],
                    "world_mean_mT":{"x":float(mean[0]),"y":float(mean[1]),"z":float(mean[2])},
                    "world_mean_unit":_norm(mean).tolist() if mag>1e-12 else [0.0,0.0,0.0],
                    "world_std_mT":{"x":float(std[0]),"y":float(std[1]),"z":float(std[2])},
                    "world_stability_mT":float(np.linalg.norm(std)),
                    "world_history_xyz":[[float(x),float(y),float(z)] for x,y,z in W[-120:]],
                })
            else:
                out.update({"pose_locked":False,"world_history_xyz":[]})
            return out

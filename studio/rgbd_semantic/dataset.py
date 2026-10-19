"""Episode-compatible RGB-D evidence store: lossless, traceable, never silent overwrite.

Existing BRUSH 'signal.csv' and 'episode.json' files remain unchanged.
Additional vision fields live in episode/vision/.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import threading
import uuid

import cv2
import numpy as np

from .geometry import analyze_depth
from .types import CameraCalibration, RGBDFrame, SegmentationMask


def _atomic_bytes(path: Path, blob: bytes):
    if path.exists():
        raise FileExistsError(f"Immutable evidence already exists: {path}")
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(tmp,"xb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            raise FileExistsError(f"Evidence already exists: {path}")
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def _json_safe(data):
    if isinstance(data,dict):
        return {str(k):_json_safe(v) for k,v in data.items()}
    if isinstance(data,(list,tuple)):
        return [_json_safe(x) for x in data]
    if isinstance(data,np.ndarray):
        return _json_safe(data.tolist())
    if isinstance(data,(np.bool_,bool)):
        return bool(data)
    if isinstance(data,(np.integer,int)) and not isinstance(data,bool):
        return int(data)
    if isinstance(data,(np.floating,float)):
        return float(data) if np.isfinite(data) else None
    if isinstance(data,(str,type(None))):
        return data
    raise TypeError(f"Cannot serialize type {type(data).__name__}")


def _json_bytes(data):
    return json.dumps(_json_safe(data),ensure_ascii=False,sort_keys=True,
                      indent=2,allow_nan=False).encode("utf-8")


class EpisodeVisionWriter:
    def __init__(self, episode_dir: str | Path, calibration: CameraCalibration,
                 model_version: str = "sam3_v4_meta"):
        self.episode=Path(episode_dir)
        self.root=self.episode/"vision"
        self.calibration=calibration
        self.model_version=model_version
        self.root.mkdir(parents=True,exist_ok=True)
        self._lock=threading.Lock()
        self._written=set()
        self._load_existing_or_init()

    def _load_existing_or_init(self):
        path=self.root/"calibration.json"
        if path.exists():
            obj=json.loads(path.read_text(encoding="utf-8"))
            if obj.get("calibration_sha256")!=self.calibration.sha256:
                raise ValueError("Refusing to mix camera calibrations within one episode")
        else:
            _atomic_bytes(path,_json_bytes(
                {"schema_version":"rgbd_semantic_v1",
                 "calibration":self.calibration.to_dict(),
                 "calibration_sha256":self.calibration.sha256,
                 "model_version":self.model_version,
                 "label_policy":"CANDIDATE_UNTIL_INDEPENDENT_REVIEW"}))
        # Frame metadata is the commit marker for the pair (RGB PNG, depth NPY).
        # Crash or disk-full interruptions leave detectable orphan files.
        committed={p.stem for p in (self.root/"frames").glob("*.json")
                   if p.stem.isdigit()}
        rgb_files={p.stem for p in (self.root/"rgb").glob("*.png")
                   if p.stem.isdigit()}
        depth_files={p.stem for p in (self.root/"depth").glob("*.npy")
                     if p.stem.isdigit()}
        if rgb_files != committed or depth_files != committed:
            raise RuntimeError(
                "Incomplete RGB-D archive: orphan/missing PNG, NPY, or frame metadata; "
                "quarantine episode and recover from raw camera recording"
            )
        self._written={int(k) for k in committed}

    def store_raw_frame(self, frame: RGBDFrame, matched_brush=None, mapped_pose=None):
        frame.validate(self.calibration)
        fid=frame.camera_frame_id
        key=f"{fid:08d}"
        with self._lock:
            if fid in self._written:
                raise FileExistsError(f"Camera frame {fid} already archived")
            rgb_path=self.root/"rgb"/(key+".png")
            depth_path=self.root/"depth"/(key+".npy")
            meta_path=self.root/"frames"/(key+".json")
            # RGB lossless; OpenCV encoder explicitly expects BGR.
            good, rgb_bytes=cv2.imencode(
                ".png",cv2.cvtColor(frame.color_rgb,cv2.COLOR_RGB2BGR))
            if not good:
                raise RuntimeError("Cannot encode RGB PNG")
            from io import BytesIO
            buf=BytesIO()
            np.save(buf,frame.depth_raw,allow_pickle=False)
            rb=rgb_bytes.tobytes()
            db=buf.getvalue()
            _atomic_bytes(rgb_path,rb)
            _atomic_bytes(depth_path,db)
            camera_json={
                "schema_version":"rgbd_semantic_v1",
                "camera_frame_id":fid,
                "capture_host_ns":frame.capture_host_ns,
                "camera_device_ts_us":frame.camera_device_ts_us,
                "camera_clock_synced":frame.camera_clock_synced,
                "calibration_id":frame.calibration_id,
                "calibration_sha256":self.calibration.sha256,
                "color_file":f"rgb/{key}.png",
                "color_sha256":hashlib.sha256(rb).hexdigest(),
                "depth_file":f"depth/{key}.npy",
                "depth_sha256":hashlib.sha256(db).hexdigest(),
                "depth_native_dtype":str(frame.depth_raw.dtype),
                "depth_scale_m":self.calibration.depth_scale_m,
                "depth_aligned_to_color":self.calibration.depth_aligned_to_color,
                "matched_brush":(
                    _json_safe(vars(matched_brush)) if matched_brush is not None else None),
                "mapped_brush_pose":_json_safe(mapped_pose),
                "label_status":"UNLABELED",
            }
            _atomic_bytes(meta_path,_json_bytes(camera_json))
            self._written.add(fid)
            return meta_path

    def read_frame(self, fid:int)->RGBDFrame:
        key=f"{fid:08d}"
        path=self.root/"frames"/(key+".json")
        meta=json.loads(path.read_text(encoding="utf-8"))
        rb=(self.root/meta["color_file"]).read_bytes()
        db=(self.root/meta["depth_file"]).read_bytes()
        if hashlib.sha256(rb).hexdigest()!=meta["color_sha256"]:
            raise ValueError("RGB checksum mismatch")
        if hashlib.sha256(db).hexdigest()!=meta["depth_sha256"]:
            raise ValueError("Depth checksum mismatch")
        bgr=cv2.imdecode(np.frombuffer(rb,np.uint8),cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError("RGB decode error")
        from io import BytesIO
        depth=np.load(BytesIO(db),allow_pickle=False)
        frame=RGBDFrame(fid,int(meta["capture_host_ns"]),
            cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB),depth,
            meta["calibration_id"],meta.get("camera_device_ts_us"),
            bool(meta.get("camera_clock_synced",False)))
        frame.validate(self.calibration)
        return frame

    def store_mask(self, frame: RGBDFrame, mask:SegmentationMask,
                   model_revision: str = ""):
        frame.validate(self.calibration)
        mask.validate(self.calibration)
        if frame.camera_frame_id != mask.camera_frame_id:
            raise ValueError("Cannot write a mask against a different RGB-D frame")
        fid=frame.camera_frame_id
        key=f"{fid:08d}"
        raw_ref=self.root/"frames"/(key+".json")
        if not raw_ref.exists():
            raise FileNotFoundError("Raw RGB-D frame must be persisted before adding annotations")
        geometry=analyze_depth(frame,mask,self.calibration)
        # Candidate masks are always unverified; independent manual/GT audit promotes them.
        subkey=f"{key}_obj_{mask.object_id:04d}_clip_{mask.source_clip_id}"
        if not re.fullmatch(r"[a-zA-Z0-9_-]+",mask.source_clip_id):
            raise ValueError("Unsafe clip ID")
        ok,bytes_img=cv2.imencode(".png",mask.mask.astype(np.uint8)*255)
        if not ok:
            raise RuntimeError("Mask PNG encoding failed")
        binary=bytes_img.tobytes()
        mask_file=self.root/"masks"/(subkey+".png")
        _atomic_bytes(mask_file,binary)
        meta_file=self.root/"annotations"/(subkey+".json")
        quality=geometry.to_dict()
        from .axis import estimate_brush_axis
        shaft_axis=estimate_brush_axis(frame,mask,self.calibration)
        raw_meta=json.loads(raw_ref.read_text(encoding="utf-8"))
        brush_pose=raw_meta.get("mapped_brush_pose")
        if (self.calibration.world_frame_name == "board"
            and self.calibration.world_from_camera is not None
            and isinstance(brush_pose,dict)
            and brush_pose.get("reference_locked")
            and shaft_axis.get("status") == "CANDIDATE"):
            from host.vision_pose import compare_axes
            imu_axis=[brush_pose.get(k) for k in
                      ("brush_axis_x","brush_axis_y","brush_axis_z")]
            if all(x is not None for x in imu_axis):
                result=compare_axes(
                    imu_axis,shaft_axis["axis_camera_undirected"],
                    np.asarray(self.calibration.world_from_camera)[:3,:3])
                if result is not None:
                    shaft_axis["imu_visual_shaft_error_deg"]=result["shaft_error_deg"]
                    shaft_axis["imu_comparison_frame"]="board"
        payload={
            "schema_version":"rgbd_semantic_v1",
            "camera_frame_id":fid,
            "calibration_sha256":self.calibration.sha256,
            "source_clip_id":mask.source_clip_id,
            "clip_frame_index":mask.clip_frame_index,
            "sam3_object_id":mask.object_id,
            "model_version":self.model_version,
            "model_revision":model_revision or None,
            "prompt":mask.prompt,
            "mask_score":mask.score,
            "mask_file":f"masks/{subkey}.png",
            "mask_sha256":hashlib.sha256(binary).hexdigest(),
            "geometry":quality,
            "shaft_axis":shaft_axis,
            "label_status": "CANDIDATE" if quality["status"]=="CANDIDATE" else "REVIEW",
            "requires_independent_review":True,
        }
        _atomic_bytes(meta_file,_json_bytes(payload))
        return meta_file

    def review_annotation(self, annotation_file: Path, decision: str,
                          reviewer: str, evidence: str):
        if decision not in ("ACCEPTED","REJECTED","REVIEW"):
            raise ValueError("Decision must be ACCEPTED/REJECTED/REVIEW")
        if not reviewer.strip() or not evidence.strip():
            raise ValueError("Review requires reviewer and supporting evidence")
        if not annotation_file.resolve().is_relative_to(
                (self.root/"annotations").resolve()):
            raise ValueError("Review file outside annotation archive")
        if not annotation_file.is_file():
            raise FileNotFoundError(annotation_file)
        # Immutable audit decision, never silently mutate original predictions.
        audit={
            "annotation":annotation_file.name, "decision":decision,
            "reviewer":reviewer, "evidence":evidence,
            "review_id":uuid.uuid4().hex,
        }
        file=self.root/"reviews"/(annotation_file.stem+"_"+audit["review_id"]+".json")
        _atomic_bytes(file,_json_bytes(audit))
        return file

"""Exact-frame SAM3 v4 Meta adapter, with defensive ZIP and manifest parsing."""
from __future__ import annotations

from dataclasses import dataclass
import io
import json
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
import uuid
from urllib.parse import urlsplit
import zipfile

import cv2
import numpy as np

from .types import CameraCalibration, RGBDFrame, SegmentationMask, FrameReference

MASK_NAME = re.compile(
    r"^frame_(\d+)_obj_(\d+)(?:_score([0-9]+(?:\.[0-9]+)?))?\.png$"
)


class Sam3ResponseError(ValueError):
    pass


@dataclass(frozen=True)
class Sam3TransportConfig:
    base_url: str = "http://127.0.0.1:6014"
    api_key: str = ""
    prompt: str = "brush"
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 180.0
    max_zip_bytes: int = 100 * 1024 * 1024
    max_uncompressed_bytes: int = 200 * 1024 * 1024
    max_clip_frames: int = 10
    output_prob_thresh: float = .1

    def __post_init__(self):
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in ("http","https") or not parsed.hostname:
            raise ValueError("SAM3 URL must be http(s)")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("SAM3 URL must not include credentials or fragments")
        local = parsed.hostname in ("127.0.0.1","::1","localhost")
        if parsed.scheme == "http" and not local:
            raise ValueError("Remote SAM3 calls require HTTPS")
        if not local and not self.api_key:
            raise ValueError("Remote SAM3 must be authenticated")
        if not (1<=self.max_clip_frames<=400):
            raise ValueError("max_clip_frames must be 1..400")
        if not 0<=self.output_prob_thresh<=1:
            raise ValueError("Invalid SAM3 output probability threshold")


@dataclass
class Sam3ClipResult:
    clip_id: str
    camera_frame_ids: list[int]
    masks: dict[int,list[SegmentationMask]]
    latency_ms: float
    backend: str
    meta: dict


def parse_sam3_zip(content: bytes, frames: list[RGBDFrame],
                   calibration: CameraCalibration, clip_id: str,
                   max_zip_bytes: int = 100*1024*1024,
                   max_uncompressed_bytes: int = 200*1024*1024) -> Sam3ClipResult:
    """Do not infer a frame ID from ZIP order; require exact verified clip index."""
    if len(content)>max_zip_bytes:
        raise Sam3ResponseError("Compressed prediction ZIP exceeds safety cap")
    if not frames:
        raise Sam3ResponseError("No manifest frames")
    seen_camera_ids=set()
    for f in frames:
        f.validate(calibration)
        if f.camera_frame_id in seen_camera_ids:
            raise Sam3ResponseError("Duplicate original frame ID")
        seen_camera_ids.add(f.camera_frame_id)
    output: dict[int,list[SegmentationMask]] = {f.camera_frame_id:[] for f in frames}
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names=z.namelist()
        if len(names)>len(frames)*32+4:
            raise Sam3ResponseError("ZIP has excessive member count")
        if len(set(names))!=len(names):
            raise Sam3ResponseError("Duplicate member names in prediction ZIP")
        total=sum(x.file_size for x in z.infolist())
        if total>max_uncompressed_bytes:
            raise Sam3ResponseError("Prediction ZIP decompression bound exceeded")
        if "meta.json" not in names:
            raise Sam3ResponseError("Prediction ZIP missing meta.json")
        info=z.getinfo("meta.json")
        if info.file_size>3*1024*1024:
            raise Sam3ResponseError("meta.json exceeds safety cap")
        meta=json.loads(z.read("meta.json"))
        if type(meta) is not dict or meta.get("success",True) is not True:
            raise Sam3ResponseError("SAM3 reported unsuccessful inference")
        count=meta.get("num_frames")
        if type(count) is not int or count!=len(frames):
            raise Sam3ResponseError(f"num_frames {count} differs from clip length {len(frames)}")
        for key in ("frame_indices", "processed_frame_indices"):
            if key in meta and list(meta[key])!=list(range(len(frames))):
                raise Sam3ResponseError(f"Invalid or unexpected {key}: {meta[key]}")
        # Prefer exact unrounded score from meta over display-rounded ZIP filename.
        exact_scores={}
        if isinstance(meta.get("frame_results"),list):
            if len(meta["frame_results"])!=len(frames):
                raise Sam3ResponseError("frame_results count mismatch")
            for i,row in enumerate(meta["frame_results"]):
                if row.get("frame_index") != i:
                    raise Sam3ResponseError("Frame metadata index mismatch")
                for obj in row.get("objects",[]):
                    exact_scores[i,int(obj["object_id"])]=float(obj["score"])
        elif isinstance(meta.get("object_scores"),list):
            # The old service may expose scores per frame only, without per-object IDs.
            pass

        matched=set()
        for n in names:
            if n=="meta.json":
                continue
            if "/" in n or "\\" in n or n.startswith("."):
                raise Sam3ResponseError("Unexpected prediction ZIP member path")
            match=MASK_NAME.fullmatch(n)
            if not match:
                raise Sam3ResponseError(f"Unrecognized ZIP member: {n}")
            idx, obj=int(match.group(1)),int(match.group(2))
            if idx<0 or idx>=len(frames):
                raise Sam3ResponseError("ZIP mask index outside clip")
            if (idx,obj) in matched:
                raise Sam3ResponseError("Repeated object ID in one clip frame")
            matched.add((idx,obj))
            mask_bytes=z.read(n)
            raw=cv2.imdecode(np.frombuffer(mask_bytes,np.uint8),cv2.IMREAD_GRAYSCALE)
            if raw is None or raw.shape!=(calibration.height,calibration.width):
                raise Sam3ResponseError("Mask decode/shape mismatch")
            score=exact_scores.get((idx,obj))
            if score is None:
                if match.group(3) is not None:
                    score=float(match.group(3))
                else:
                    # Explicitly require per-object score instead of inventing 0.5 or 1.0.
                    raise Sam3ResponseError(f"No confidence score for mask {n}")
            mask=SegmentationMask(frames[idx].camera_frame_id,obj,raw>127,
                                  float(score),clip_id,idx,
                                  str(meta.get("text_prompt","brush")))
            mask.validate(calibration)
            output[frames[idx].camera_frame_id].append(mask)

        # Cross-check metadata mask object IDs where available.
        if isinstance(meta.get("frame_results"),list):
            for i,row in enumerate(meta["frame_results"]):
                if isinstance(row.get("objects"),list):
                    declared={int(o["object_id"]) for o in row["objects"]
                              if int(o.get("mask_pixels",1))>0}
                    actual={m.object_id for m in output[frames[i].camera_frame_id]}
                    if declared!=actual:
                        raise Sam3ResponseError(
                            f"Object IDs conflict with ZIP at clip frame {i}"
                        )
    return Sam3ClipResult(clip_id,[f.camera_frame_id for f in frames],
                         output,0.,str(meta.get("backend","meta-batch")),meta)


class Sam3V4BatchClient:
    def __init__(self, cfg: Sam3TransportConfig, calibration: CameraCalibration):
        self.cfg=cfg
        self.calibration=calibration

    def health(self) -> dict:
        import requests
        headers={"Authorization":f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
        with requests.Session() as client:
            client.trust_env=False
            r=client.get(self.cfg.base_url.rstrip("/")+"/health/ready",
                         headers=headers,timeout=(self.cfg.connect_timeout_s,5))
            r.raise_for_status()
            data=r.json()
            if data.get("status")!="ready" or data.get("backend")!="meta-batch":
                raise Sam3ResponseError("Target must be ready Meta batch backend")
            return data

    def segment_clip(self, frames: list[RGBDFrame]) -> Sam3ClipResult:
        import requests
        if not frames or len(frames)>self.cfg.max_clip_frames:
            raise ValueError("SAM3 batch is empty or exceeds configured clip size")
        ids=[f.camera_frame_id for f in frames]
        if ids!=sorted(set(ids)):
            raise ValueError("SAM3 clip camera frame IDs must be strictly increasing")
        for f in frames:
            f.validate(self.calibration)
        clip_id=uuid.uuid4().hex
        started=time.perf_counter()
        fd, filename=tempfile.mkstemp(prefix="ebrush_sam3_",suffix=".mp4")
        os.close(fd)
        try:
            out=cv2.VideoWriter(filename,cv2.VideoWriter_fourcc(*"mp4v"),
                                20.0,(self.calibration.width,self.calibration.height))
            if not out.isOpened():
                raise RuntimeError("MP4 writer unavailable")
            try:
                for f in frames:
                    # Explicit RGB -> BGR conversion; legacy client silently swapped channels.
                    out.write(cv2.cvtColor(f.color_rgb,cv2.COLOR_RGB2BGR))
            finally:
                out.release()
            return self.segment_video_file(
                filename,
                [FrameReference(f.camera_frame_id,f.calibration_id) for f in frames],
                clip_id=clip_id,
            )
        finally:
            try:
                os.unlink(filename)
            except FileNotFoundError:
                pass

    def segment_video_file(self, filename: str, frames: list[FrameReference],
                           clip_id: str | None = None) -> Sam3ClipResult:
        """Send pre-encoded MP4; keep only reference IDs in memory for offline video."""
        import requests
        if not frames or len(frames)>self.cfg.max_clip_frames:
            raise ValueError("SAM3 MP4 exceeds configured clip size")
        ids=[f.camera_frame_id for f in frames]
        if ids!=sorted(set(ids)):
            raise ValueError("Duplicate/unordered original camera frame IDs")
        for reference in frames:
            reference.validate(self.calibration)
        started=time.perf_counter()
        headers={"Authorization":f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}
        with requests.Session() as http:
            http.trust_env=False
            with open(filename,"rb") as fh:
                resp=http.post(
                    self.cfg.base_url.rstrip("/")+"/video/segment",
                    headers=headers,
                    files={"file":("episode_clip.mp4",fh,"video/mp4")},
                    data={
                        "text_prompt":self.cfg.prompt,
                        "frame_stride":"1",
                        "max_frames":str(len(frames)),
                        "return_masks_png":"true",
                        "output_prob_thresh":str(self.cfg.output_prob_thresh),
                    },
                    timeout=(self.cfg.connect_timeout_s,self.cfg.read_timeout_s),
                )
            resp.raise_for_status()
        if len(resp.content)>self.cfg.max_zip_bytes:
            raise Sam3ResponseError("SAM3 response exceeds ZIP size cap")
        result=parse_sam3_zip(resp.content,frames,self.calibration,
                              clip_id or uuid.uuid4().hex,
                              self.cfg.max_zip_bytes,self.cfg.max_uncompressed_bytes)
        if result.meta.get("text_prompt") is not None and result.meta["text_prompt"]!=self.cfg.prompt:
            raise Sam3ResponseError("SAM3 result prompt does not match submitted request")
        if result.meta.get("source_sha256"):
            sha=hashlib.sha256()
            with open(filename,"rb") as source:
                while True:
                    chunk=source.read(1024*1024)
                    if not chunk: break
                    sha.update(chunk)
            if result.meta["source_sha256"]!=sha.hexdigest():
                raise Sam3ResponseError("SAM3 response video SHA256 mismatch")
        result.latency_ms=(time.perf_counter()-started)*1000.
        return result


def best_unambiguous_brush(masks: list[SegmentationMask],
                           min_score: float = .1, margin: float = .08):
    """Semantic score is a ranking signal, not a certified confidence of correctness."""
    candidates=[m for m in masks if m.score>=min_score and int(m.mask.sum())>=30]
    if not candidates:
        return None, "NO_VALID_MASK"
    candidates.sort(key=lambda m:m.score,reverse=True)
    if len(candidates)>1 and candidates[0].score-candidates[1].score<margin:
        return None, "AMBIGUOUS_OBJECT_IDENTITY"
    return candidates[0],"UNVERIFIED_CANDIDATE"

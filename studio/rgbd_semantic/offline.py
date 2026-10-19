"""Offline full-episode semantic labeling via the validated Meta v4 batch service.

Keeps only one RGB frame in memory while encoding. The original RGB-D data
remain immutable and the prediction masks retain per-clip frame provenance.
"""
from __future__ import annotations
import os
from pathlib import Path
import tempfile

import cv2

from .dataset import EpisodeVisionWriter
from .sam3_client import Sam3V4BatchClient
from .types import FrameReference


def annotate_episode_offline(writer: EpisodeVisionWriter,
                             client: Sam3V4BatchClient,
                             max_frames_per_clip: int = 400,
                             fps: float = 20.0):
    if writer.calibration.sha256 != client.calibration.sha256:
        raise ValueError("Offline client and writer calibrations differ")
    if not (1<=max_frames_per_clip<=client.cfg.max_clip_frames):
        raise ValueError("max_frames_per_clip exceeds SAM3 client cap")
    if not (0<fps<=120):
        raise ValueError("Invalid video frame rate")
    frames=sorted(int(f.stem) for f in (writer.root/"frames").glob("*.json")
                  if f.stem.isdigit())
    if not frames:
        raise ValueError("RGB-D episode contains no recorded frames")
    emitted=0
    processed_clips=0
    for offset in range(0,len(frames),max_frames_per_clip):
        source_ids=frames[offset:offset+max_frames_per_clip]
        fd,path=tempfile.mkstemp(prefix="ebrush_offline_",suffix=".mp4")
        os.close(fd)
        try:
            vid=cv2.VideoWriter(path,cv2.VideoWriter_fourcc(*"mp4v"),
                                fps,(writer.calibration.width,writer.calibration.height))
            if not vid.isOpened():
                raise RuntimeError("Cannot create offline video clip")
            try:
                for fid in source_ids:
                    source=writer.read_frame(fid)
                    vid.write(cv2.cvtColor(source.color_rgb,cv2.COLOR_RGB2BGR))
            finally:
                vid.release()
            refs=[FrameReference(fid,writer.calibration.calibration_id) for fid in source_ids]
            prediction=client.segment_video_file(path,refs)
            if prediction.camera_frame_ids!=source_ids:
                raise RuntimeError("SAM3 prediction does not match input frame IDs")
            for fid in source_ids:
                raw=writer.read_frame(fid)
                for annotation in prediction.masks.get(fid,[]):
                    writer.store_mask(raw,annotation,prediction.backend)
                    emitted+=1
            processed_clips+=1
        finally:
            try:os.unlink(path)
            except FileNotFoundError:pass
    return {
        "source_frames":len(frames),
        "inference_clips":processed_clips,
        "mask_evidence_written":emitted,
        "source_model":client.cfg.base_url,
        "status":"CANDIDATE_REVIEW_REQUIRED",
    }

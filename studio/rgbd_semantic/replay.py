#!/usr/bin/env python3
"""Replay existing RGB-D frames to the production Meta SAM3 service.

No native HP60C SDK is bundled: hardware capture uses CalibratedCameraAdapter.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
import sys

from .dataset import EpisodeVisionWriter
from .pipeline import RGBDSemanticPipeline
from .sam3_client import Sam3TransportConfig, Sam3V4BatchClient
from .types import CameraCalibration


def main():
    p=argparse.ArgumentParser(description="Replay a recorded RGB-D episode through SAM3 v4")
    p.add_argument("--source-episode",type=Path,required=True,
                   help="Existing episode with vision/rgb, vision/depth, vision/frames")
    p.add_argument("--output-episode",type=Path,required=True,
                   help="New empty output episode, never overwrites existing evidence")
    p.add_argument("--url",default="http://127.0.0.1:6014")
    p.add_argument("--api-key-env",default="SAM3_API_KEY")
    p.add_argument("--clip-frames",type=int,default=5)
    p.add_argument("--request-interval-s",type=float,default=5.)
    p.add_argument("--limit",type=int,default=None)
    p.add_argument("--timeout-s",type=float,default=180.)
    args=p.parse_args()
    if args.source_episode.resolve()==args.output_episode.resolve():
        p.error("Source and destination must differ")
    if args.output_episode.exists() and any(args.output_episode.iterdir()):
        p.error("Output episode must be empty")
    config=json.loads((args.source_episode/"vision"/"calibration.json").read_text(encoding="utf-8"))
    cal=CameraCalibration(**config["calibration"])
    if not cal.geometric_ready:
        p.error("Calibration must be verified and aligned before 3D processing")
    source=EpisodeVisionWriter(args.source_episode,cal)
    dest=EpisodeVisionWriter(args.output_episode,cal)
    import os
    cfg=Sam3TransportConfig(base_url=args.url,
                            api_key=os.environ.get(args.api_key_env,""),
                            read_timeout_s=args.timeout_s,
                            max_clip_frames=args.clip_frames)
    client=Sam3V4BatchClient(cfg,cal)
    print("SAM3 ready:",client.health())
    engine=RGBDSemanticPipeline(cal,dest,client,clip_frames=args.clip_frames,
                                request_interval_s=args.request_interval_s)
    engine.start()
    try:
        nums=sorted(int(path.stem) for path in (source.root/"frames").glob("*.json"))
        if args.limit is not None:
            nums=nums[:args.limit]
        for fid in nums:
            frame=source.read_frame(fid)
            while True:
                try:
                    engine.on_rgbd_frame(frame)
                    break
                except Exception as exc:
                    from .pipeline import RecorderBackpressure
                    if not isinstance(exc,RecorderBackpressure):
                        raise
                    time.sleep(.05)
        if not engine.flush(timeout_s=max(30.,args.timeout_s+30.)):
            raise RuntimeError("Timed out waiting for SAM3; output may be partially annotated")
    finally:
        engine.close(timeout_s=max(30.,args.timeout_s+30.))
    print(json.dumps(engine.status(),ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())

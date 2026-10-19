"""Legacy Studio SAM3 client API backed by one repaired, bounded implementation.

The previous host client selected masks across ALL video frames and could attach
a historical mask to the most recent RGB-D frame. Public constructor and
SamResult attributes remain compatible with Studio.
"""
from __future__ import annotations

from tools.sam3_video_client import SAM3Client as _SafeClient
from tools.sam3_video_client import SAM3Config, SamResult


class SAM3Client(_SafeClient):
    def __init__(self, base_url: str, api_key: str = "",
                 prompt: str = "calligraphy brush", jpeg_quality: int = 88,
                 timeout_s: float = 30.0, clip_frames: int = 30,
                 clip_fps: int = 30, min_interval_s: float = 0.0):
        super().__init__(SAM3Config(
            base_url=base_url, api_key=api_key, prompt=prompt,
            jpeg_quality=jpeg_quality, timeout_s=timeout_s,
            clip_frames=clip_frames, clip_fps=clip_fps,
            min_interval_s=min_interval_s
        ))
"""Strict contracts: color/depth frame identity, calibration and label provenance."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import hashlib
import json
import math

import numpy as np


@dataclass(frozen=True)
class CameraCalibration:
    calibration_id: str
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    depth_scale_m: float = 0.001
    depth_aligned_to_color: bool = False
    verified: bool = False
    world_from_camera: Optional[list[list[float]]] = None
    source: str = "unverified"
    # Explicit flag for undistorted/aligned color image coordinates.
    color_rectified: bool = False
    world_frame_name: str = "unknown"  # board only with independent external calibration

    def __post_init__(self):
        if not self.calibration_id.strip():
            raise ValueError("Calibration must have a unique ID")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Invalid image dimensions")
        vals = [self.fx, self.fy, self.cx, self.cy, self.depth_scale_m]
        if any(not math.isfinite(float(x)) for x in vals):
            raise ValueError("Non-finite calibration parameter")
        if self.fx <= 0 or self.fy <= 0 or self.depth_scale_m <= 0:
            raise ValueError("Invalid focal length or depth scale")
        if self.world_from_camera is not None:
            t = np.asarray(self.world_from_camera, dtype=np.float64)
            if t.shape != (4, 4) or not np.isfinite(t).all() or not np.allclose(t[3], [0,0,0,1], atol=1e-6):
                raise ValueError("world_from_camera must be finite 4x4 homogeneous matrix")
            if not np.allclose(t[:3,:3].T @ t[:3,:3], np.eye(3), atol=1e-3):
                raise ValueError("world_from_camera rotation is not orthonormal")
            if not np.isclose(np.linalg.det(t[:3,:3]),1.0,atol=1e-3):
                raise ValueError("world_from_camera rotation must be right-handed")

    @property
    def geometric_ready(self) -> bool:
        return bool(self.verified and self.depth_aligned_to_color and self.color_rectified)

    def to_dict(self) -> dict:
        return {
            "calibration_id": self.calibration_id,
            "width": self.width, "height": self.height,
            "fx": self.fx, "fy": self.fy, "cx": self.cx, "cy": self.cy,
            "depth_scale_m": self.depth_scale_m,
            "depth_aligned_to_color": self.depth_aligned_to_color,
            "color_rectified": self.color_rectified, "verified": self.verified,
            "source": self.source,
            "world_from_camera": self.world_from_camera,
            "world_frame_name": self.world_frame_name,
        }

    @property
    def sha256(self) -> str:
        b = json.dumps(self.to_dict(), sort_keys=True, allow_nan=False).encode()
        return hashlib.sha256(b).hexdigest()


@dataclass
class RGBDFrame:
    camera_frame_id: int
    capture_host_ns: int
    color_rgb: np.ndarray
    depth_raw: np.ndarray
    calibration_id: str
    camera_device_ts_us: Optional[float] = None
    camera_clock_synced: bool = False

    def validate(self, calib: CameraCalibration) -> None:
        if self.camera_frame_id < 0 or self.capture_host_ns <= 0:
            raise ValueError("Camera frame ID and monotonic timestamp required")
        if self.calibration_id != calib.calibration_id:
            raise ValueError("Camera calibration ID mismatch")
        if self.color_rgb.dtype != np.uint8 or self.color_rgb.shape != (calib.height, calib.width, 3):
            raise ValueError("Expected 8-bit RGB image with calibrated dimensions")
        if self.depth_raw.shape != (calib.height, calib.width):
            raise ValueError("Depth must be aligned to color and have identical pixel dimensions")
        if self.depth_raw.dtype not in (np.uint16, np.float32, np.float64):
            raise ValueError("Depth must preserve uint16 native values or floating-point raw units")
        if self.camera_device_ts_us is not None and not math.isfinite(self.camera_device_ts_us):
            raise ValueError("Non-finite camera device timestamp")


@dataclass(frozen=True)
class FrameReference:
    """Small audited association key: avoids loading all RGB-D arrays at once."""
    camera_frame_id: int
    calibration_id: str

    def validate(self, calib: CameraCalibration) -> None:
        if self.camera_frame_id < 0 or self.calibration_id != calib.calibration_id:
            raise ValueError("Invalid camera frame reference or calibration ID")


@dataclass
class SegmentationMask:
    camera_frame_id: int
    object_id: int
    mask: np.ndarray
    score: float
    source_clip_id: str
    clip_frame_index: int
    prompt: str = "brush"

    def validate(self, calib: CameraCalibration) -> None:
        if self.camera_frame_id < 0 or self.clip_frame_index < 0:
            raise ValueError("Invalid frame reference")
        if not math.isfinite(self.score) or not 0 <= self.score <= 1:
            raise ValueError("Invalid model probability")
        if self.mask.dtype != bool or self.mask.shape != (calib.height, calib.width):
            raise ValueError("Mask must be aligned binary image")


@dataclass
class GeometryResult:
    status: str
    reasons: list[str] = field(default_factory=list)
    mask_pixels: int = 0
    valid_depth_pixels: int = 0
    valid_depth_ratio: float = 0.0
    inlier_pixels: int = 0
    camera_center_m: Optional[list[float]] = None
    world_center_m: Optional[list[float]] = None
    median_depth_m: Optional[float] = None
    depth_mad_m: Optional[float] = None
    position_kind: str = "visible_surface_robust_center"
    # These quality checks are insufficient for certified training ground truth.
    label_status: str = "REVIEW"

    def to_dict(self) -> dict:
        return dict(vars(self))


@dataclass(frozen=True)
class TrackOutput:
    camera_frame_id: int
    capture_host_ns: int
    track_id: str | None
    state: str  # OBSERVED, PREDICTED, LOST, UNKNOWN
    confidence: float
    camera_center_m: list[float] | None
    source_mask_camera_frame_id: int | None
    age_frames: int
    notes: str = ""

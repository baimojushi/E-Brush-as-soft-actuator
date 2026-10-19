"""Depth alignment gate and robust visible-surface 3D measurements."""
from __future__ import annotations
import numpy as np

from .types import CameraCalibration, RGBDFrame, SegmentationMask, GeometryResult


def mask_to_points(frame: RGBDFrame, binary_mask: np.ndarray,
                   calib: CameraCalibration, min_depth_m: float = .12,
                   max_depth_m: float = 4.0,
                   max_points: int = 20000) -> np.ndarray:
    """Camera X right, Y down, Z forward (metres). No implicit distortion correction."""
    frame.validate(calib)
    if not calib.geometric_ready:
        raise ValueError("Cannot produce calibrated 3D points: alignment/rectification unverified")
    if binary_mask.shape != frame.depth_raw.shape or binary_mask.dtype != bool:
        raise ValueError("Mask and aligned raw depth must be same size")
    depth = frame.depth_raw.astype(np.float64) * calib.depth_scale_m
    valid = binary_mask & np.isfinite(depth) & (depth >= min_depth_m) & (depth <= max_depth_m)
    vs, us = np.nonzero(valid)
    if len(us) > max_points:
        ids = np.linspace(0, len(us)-1, max_points, dtype=int)
        us, vs = us[ids], vs[ids]
    z = depth[vs, us]
    return np.column_stack(((us - calib.cx)*z/calib.fx,
                            (vs - calib.cy)*z/calib.fy, z))


def analyze_depth(frame: RGBDFrame, annotation: SegmentationMask,
                  calib: CameraCalibration, min_depth_m: float = .12,
                  max_depth_m: float = 4.0, min_mask_pixels: int = 30,
                  min_valid_fraction: float = .65, min_inliers: int = 20,
                  min_inlier_fraction: float = .70) -> GeometryResult:
    frame.validate(calib)
    annotation.validate(calib)
    if frame.camera_frame_id != annotation.camera_frame_id:
        raise ValueError("Mask frame ID does not equal depth frame ID")
    if min_depth_m <= 0 or max_depth_m <= min_depth_m:
        raise ValueError("Depth range is invalid")

    mask = annotation.mask
    pixels = int(np.count_nonzero(mask))
    result = GeometryResult(status="REVIEW", mask_pixels=pixels)
    if not calib.geometric_ready:
        result.reasons.append("UNCALIBRATED_OR_NOT_ALIGNED")
        return result
    if not frame.camera_clock_synced:
        result.reasons.append("CAMERA_CLOCK_UNVERIFIED")
    if pixels < min_mask_pixels:
        result.reasons.append("INSUFFICIENT_MASK_PIXELS")
        return result

    depth = frame.depth_raw.astype(np.float64) * calib.depth_scale_m
    valid = mask & np.isfinite(depth) & (depth >= min_depth_m) & (depth <= max_depth_m)
    total = int(np.count_nonzero(valid))
    result.valid_depth_pixels = total
    result.valid_depth_ratio = float(total/pixels)
    if result.valid_depth_ratio < min_valid_fraction:
        result.reasons.append("LOW_VALID_DEPTH_COVERAGE")
    if total < min_inliers:
        result.reasons.append("INSUFFICIENT_DEPTH_POINTS")
        return result

    d = depth[valid]
    median = float(np.median(d))
    mad = float(np.median(np.abs(d - median)))
    result.median_depth_m = median
    result.depth_mad_m = mad
    # Tight minimum tolerance will need camera-specific noise calibration.
    threshold = max(0.015, 3.5 * 1.4826 * mad)
    inliers = valid & (np.abs(depth - median) <= threshold)
    count = int(np.count_nonzero(inliers))
    result.inlier_pixels = count
    if count / max(total,1) < min_inlier_fraction:
        result.reasons.append("MULTI_SURFACE_OR_BACKGROUND_DEPTH")
    if count < min_inliers:
        result.reasons.append("INSUFFICIENT_DEPTH_INLIERS")
        return result

    vs, us = np.nonzero(inliers)
    z = depth[vs, us]
    points = np.column_stack(((us-calib.cx)*z/calib.fx,
                              (vs-calib.cy)*z/calib.fy, z))
    # Median of visible surface points. Not object center, centroid of full geometry, or brush tip.
    xyz = np.median(points,axis=0)
    result.camera_center_m = [float(v) for v in xyz]
    if calib.world_from_camera is not None:
        transform = np.asarray(calib.world_from_camera, dtype=np.float64)
        loc = transform[:3,:3] @ xyz + transform[:3,3]
        result.world_center_m = [float(v) for v in loc]

    if not result.reasons:
        result.status = "CANDIDATE"
        result.label_status = "CANDIDATE"  # manual/independent QA needed before ACCEPTED
    return result

"""3D brush shaft-axis candidate using actual host.vision_pose VisionPoseEstimator.

Derived from calibrated, rectified and color-aligned depth. Axis is unsigned:
the RANSAC result alone cannot determine which endpoint is the brush tip.
"""
from __future__ import annotations
import numpy as np
from .types import CameraCalibration,RGBDFrame,SegmentationMask


def depth_to_organized_points(frame:RGBDFrame,cal:CameraCalibration):
    frame.validate(cal)
    if not cal.geometric_ready:
        raise ValueError("Cannot unproject point cloud from unverified camera calibration")
    z=frame.depth_raw.astype(np.float32)*np.float32(cal.depth_scale_m)
    yy,xx=np.indices(z.shape,dtype=np.float32)
    valid=np.isfinite(z)&(z>.05)&(z<10)
    points=np.zeros((cal.height,cal.width,3),dtype=np.float32)
    points[:,:,0]=np.where(valid,(xx-cal.cx)*z/cal.fx,0)
    points[:,:,1]=np.where(valid,(yy-cal.cy)*z/cal.fy,0)
    points[:,:,2]=np.where(valid,z,0)
    return points


def estimate_brush_axis(frame:RGBDFrame,annotation:SegmentationMask,
                        cal:CameraCalibration,
                        erode_px:int=0,ransac_mm:float=7.,
                        min_points:int=80)->dict:
    """Return reviewed-quality candidate data; no tip position or directed pose."""
    from host.vision_pose import VisionPoseEstimator
    if frame.camera_frame_id != annotation.camera_frame_id:
        raise ValueError("Axis mask is not from same camera/depth frame")
    points=depth_to_organized_points(frame,cal)
    estimator=VisionPoseEstimator(
        erode_px=erode_px,ransac_mm=ransac_mm,min_points=min_points)
    out=estimator.estimate(annotation.mask,{"pointcloud":points})
    if out is None or not out.get("ok"):
        return {"status":"REVIEW",
                "reasons":[(out or {}).get("reason","NO_VISIBLE_SHAFT")],
                "axis_is_directed":False,"tip_position_m":None}
    axis=np.asarray(out["axis_camera"],dtype=np.float64)
    axis/=np.linalg.norm(axis)
    center_m=np.asarray(out["origin_camera_mm"],dtype=np.float64)/1000.
    inlier_ratio=float(out["inlier_ratio"])
    length=float(out["visible_length_mm"])
    rms=float(out["line_rms_mm"])
    q=float(out["vision_quality"])
    reject=[]
    if inlier_ratio<.55:reject.append("LOW_LINE_INLIER_RATIO")
    if length<.02*1000:reject.append("SHORT_VISIBLE_SHAFT")
    if rms>10:reject.append("HIGH_FITTING_RESIDUAL")
    if q<.10:reject.append("LOW_VISION_QUALITY")
    data={
        "status":"REVIEW" if reject else "CANDIDATE",
        "reasons":reject,
        "coordinate_frame":"camera",
        "axis_camera_undirected":[float(x) for x in axis],
        "visible_line_origin_camera_m":[float(x) for x in center_m],
        "point_count":int(out["point_count"]),
        "depth_valid_ratio":float(out["depth_valid_ratio"]),
        "line_rms_mm":rms,
        "line_inlier_ratio":inlier_ratio,
        "visible_shaft_length_mm":length,
        "axis_eigen_ratio":float(out["eigen_ratio"]),
        "vision_quality":q,
        "axis_is_directed":False,
        "tip_position_m":None,
        "is_six_dof_pose":False,
        "is_ground_truth":False,
    }
    if cal.world_from_camera is not None:
        t=np.asarray(cal.world_from_camera,dtype=np.float64)
        axis_world=t[:3,:3] @ axis
        center_world=t[:3,:3] @ center_m+t[:3,3]
        data["axis_world_undirected"]=[float(x) for x in axis_world]
        data["visible_line_origin_world_m"]=[float(x) for x in center_world]
    return data

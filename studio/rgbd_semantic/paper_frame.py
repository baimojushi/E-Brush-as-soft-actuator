"""纸面世界系构建：SAM3 纸掩码 → RANSAC 平面 → 世界坐标系。

替代 CameraCalibration.world_from_camera 的静态标定矩阵。
纸面中心 = 原点，法向朝相机 = +Z，纸长边方向 = +X，Y = Z×X（右手系）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import cv2
import numpy as np


@dataclass
class PaperWorldFrame:
    """SAM3 纸掩码派生的世界坐标系。

    所有轴向量均为相机坐标系（X 右、Y 下、Z 前）。
    origin_camera_m 为纸面中心（相机系, 米）。
    """
    origin_camera_m: list[float]
    z_axis_camera: list[float]       # 法向（指向相机）
    x_axis_camera: list[float]       # 纸长边方向
    y_axis_camera: list[float]       # Z × X
    inlier_ratio: float = 0.0
    rms_m: float = 0.0
    plane_points: int = 0
    paper_mask_frame_id: int = -1
    paper_clip_id: str = ""
    frame_timestamp_ns: int = 0
    is_stale: bool = False
    stale_reason: str = ""

    def world_from_camera(self) -> np.ndarray:
        """构建 T_world_from_camera（4×4 齐次矩阵）。

        世界系定义：
          原点 = 纸面中心（相机系坐标）
          +Z   = 平面法向（指向相机）
          +X   = 纸长边方向（minAreaRect/PCA）
          +Y   = Z × X（右手系）
        """
        x = np.asarray(self.x_axis_camera, dtype=np.float64)
        y = np.asarray(self.y_axis_camera, dtype=np.float64)
        z = np.asarray(self.z_axis_camera, dtype=np.float64)
        R = np.column_stack([x, y, z])
        o = np.asarray(self.origin_camera_m, dtype=np.float64)
        # T_world_from_camera: point_world = R * point_camera - R * origin
        T = np.eye(4)
        T[:3, :3] = R
        T[:3, 3] = -R @ o
        return T

    def camera_from_world(self) -> np.ndarray:
        """T_camera_from_world = inverse(T_world_from_camera)。"""
        T_wc = self.world_from_camera()
        return np.linalg.inv(T_wc)

    def to_camera_calibration_world(self) -> list:
        """转换为 CameraCalibration.world_from_camera 格式（4×4 list）。"""
        return self.world_from_camera().tolist()

    def transform_camera_to_world(self, point_camera_m: np.ndarray) -> np.ndarray:
        """将相机系点变换到纸面世界系。"""
        p = np.asarray(point_camera_m, dtype=np.float64).ravel()
        T = self.world_from_camera()
        return T @ np.append(p, 1.0)

    def to_dict(self) -> dict:
        return {
            "origin_camera_m": self.origin_camera_m,
            "z_axis_camera": self.z_axis_camera,
            "x_axis_camera": self.x_axis_camera,
            "y_axis_camera": self.y_axis_camera,
            "inlier_ratio": self.inlier_ratio,
            "rms_m": self.rms_m,
            "plane_points": self.plane_points,
            "paper_mask_frame_id": self.paper_mask_frame_id,
            "paper_clip_id": self.paper_clip_id,
            "frame_timestamp_ns": self.frame_timestamp_ns,
            "is_stale": self.is_stale,
            "stale_reason": self.stale_reason,
            "world_frame_name": "paper_sam3",
        }


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def _ransac_plane(
    points: np.ndarray,
    iterations: int = 200,
    threshold: float = 0.05,
    seed: int = 42,
) -> tuple[np.ndarray | None, int, float, float, np.ndarray | None]:
    """3D RANSAC 平面拟合。

    返回 (n, p0, inlier_count, inlier_ratio, rms) 或 (None, ...)。
    n: 单位法向（未定向），p0: 平面中心（inliers 的 median）。
    """
    if len(points) < 3:
        return None, 0.0, 0, 0.0, None
    rng = np.random.default_rng(seed)
    best_n = None
    best_count = 0
    best_inliers = None
    for _ in range(iterations):
        idx = rng.choice(len(points), size=3, replace=False)
        p0, p1, p2 = points[idx[0]], points[idx[1]], points[idx[2]]
        v1 = p1 - p0
        v2 = p2 - p0
        n = np.cross(v1, v2)
        norm_n = float(np.linalg.norm(n))
        if norm_n < 1e-9:
            continue
        n = n / norm_n
        d = float(np.dot(n, p0))
        dists = np.abs(points @ n - d)
        inliers = dists < threshold
        count = int(np.count_nonzero(inliers))
        if count > best_count:
            best_count = count
            best_n = n
            best_inliers = inliers
    if best_n is None or best_count < 3:
        return None, 0.0, 0, 0.0, None
    inlier_pts = points[best_inliers]
    p0 = np.median(inlier_pts, axis=0)
    rms = float(np.sqrt(np.mean((inlier_pts @ best_n - p0 @ best_n) ** 2)))
    inlier_ratio = float(best_count / len(points))
    return best_n, p0, best_count, inlier_ratio, rms


def fit_paper_plane(
    frame,
    paper_mask: np.ndarray,
    calib,
    brush_mask: np.ndarray | None = None,
    min_points: int = 200,
    inlier_threshold_m: float = 0.05,
) -> dict:
    """用 SAM3 纸掩码点云拟合 RANSAC 平面。

    返回 dict:
      status: "CANDIDATE" | "REVIEW"
      n: [3] 法向单位向量（指向相机）
      p0: [3] 平面中心（相机系, m）
      inlier_ratio: float
      rms_m: float
      plane_points: int
      reasons: [str]
    """
    from .geometry import mask_to_points
    from .types import CameraCalibration

    if not isinstance(calib, CameraCalibration):
        raise TypeError("fit_paper_plane expects a CameraCalibration")
    if not calib.geometric_ready:
        return {"status": "REVIEW", "reasons": ["UNCALIBRATED_OR_NOT_ALIGNED"]}

    points = mask_to_points(frame, paper_mask.astype(bool), calib, max_points=20000)
    if len(points) < min_points:
        return {"status": "REVIEW", "reasons": ["INSUFFICIENT_PAPER_POINTS"]}

    # 剔除与毛笔掩码重叠的点
    if brush_mask is not None and np.any(brush_mask):
        overlap = paper_mask.astype(bool) & brush_mask.astype(bool)
        if np.count_nonzero(overlap) > 0:
            # 从纸点云中排除重叠区域
            valid_mask = paper_mask.astype(bool) & ~brush_mask.astype(bool)
            if np.count_nonzero(valid_mask) < min_points:
                return {"status": "REVIEW", "reasons": ["PAPER_MASK_OVERLAPS_BRUSH"]}
            points = mask_to_points(frame, valid_mask, calib, max_points=20000)

    n, p0, inlier_count, inlier_ratio, rms = _ransac_plane(
        points, iterations=200, threshold=inlier_threshold_m
    )
    if n is None:
        return {"status": "REVIEW", "reasons": ["PLANE_FIT_FAILED"]}

    # 定向：法向指向相机（+Z 轴分量 > 0）
    z_axis_cam = np.array([0.0, 0.0, 1.0])
    if np.dot(n, z_axis_cam) < 0:
        n = -n

    reasons = []
    if inlier_ratio < 0.5:
        reasons.append("LOW_PAPER_INLIER_RATIO")
    if rms > 0.005:
        reasons.append("HIGH_PLANE_RMS")

    return {
        "status": "CANDIDATE" if not reasons else "REVIEW",
        "reasons": reasons,
        "n": [float(x) for x in n],
        "p0": [float(x) for x in p0],
        "inlier_ratio": inlier_ratio,
        "rms_m": rms,
        "plane_points": inlier_count,
    }


def paper_plane_to_world_frame(
    plane: dict,
    paper_mask: np.ndarray,
    calib,
) -> PaperWorldFrame | None:
    """从纸平面拟合结果构建 PaperWorldFrame。

    plane: fit_paper_plane() 返回的 dict（status 必须为 CANDIDATE）
    paper_mask: 二值纸掩码（用于 minAreaRect 取长边方向）
    """
    if plane.get("status") != "CANDIDATE":
        return None

    n = np.asarray(plane["n"], dtype=np.float64)
    p0 = np.asarray(plane["p0"], dtype=np.float64)

    # 法向 = +Z 世界轴
    z_axis = n / float(np.linalg.norm(n))

    # 用 minAreaRect 取纸长边方向（图像像素域）
    # 纸掩码通常是大面积矩形/方形区域
    mask_u8 = paper_mask.astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    x_axis = None
    if contours:
        c = max(contours, key=cv2.contourArea)
        rect = cv2.minAreaRect(c)
        (cx, cy), (w, h), angle = rect
        # 长边方向（像素域）
        if w > h:
            theta = np.deg2rad(angle)
        else:
            theta = np.deg2rad(angle + 90)
        # 像素域 x 轴方向（指向右）
        px_dir = np.array([np.cos(theta), -np.sin(theta)])  # 图像 y 轴向下，所以 -sin
        # 反投影到 3D（在纸平面深度 z ≈ p0_z 处）
        # 取纸掩码中心像素作为参考点
        if p0[2] > 0.01:
            # 从纸掩码内取中心像素
            ys, xs = np.nonzero(paper_mask)
            if len(xs) > 0:
                cx_px = int(np.median(xs))
                cy_px = int(np.median(ys))
                z_ref = p0[2]  # 近似用平面中心的 z
                # 像素域 x 方向 → 3D 相机系方向
                fx = calib.fx
                fy = calib.fy
                cx_cal = calib.cx
                cy_cal = calib.cy
                # 3D 方向 = 反投影 px_dir 到 z_ref 平面
                u = cx_px + fx * px_dir[0] * 0.1  # 取小偏移避免中心点
                v = cy_px + fy * px_dir[1] * 0.1
                p3d = np.array([(u - cx_cal) * z_ref / fx,
                                (v - cy_cal) * z_ref / fy,
                                z_ref])
                center3d = np.array([(cx_px - cx_cal) * z_ref / fx,
                                     (cy_px - cy_cal) * z_ref / fy,
                                     z_ref])
                x_axis_3d = p3d - center3d
                x_axis_3d = _unit(x_axis_3d)
                # 投影到纸平面（去掉法向分量）
                x_axis = x_axis_3d - np.dot(x_axis_3d, z_axis) * z_axis
                x_axis = _unit(x_axis)

    if x_axis is None:
        # fallback：用相机 x 轴投影到纸平面
        cam_x = np.array([1.0, 0.0, 0.0])
        x_axis = cam_x - np.dot(cam_x, z_axis) * z_axis
        x_axis = _unit(x_axis)

    # Y = Z × X（右手系）
    y_axis = np.cross(z_axis, x_axis)
    y_axis = _unit(y_axis)

    # 右手系校验
    R = np.column_stack([x_axis, y_axis, z_axis])
    det = float(np.linalg.det(R))
    if abs(det - 1.0) > 0.01:
        # 修正：若 det ≈ -1，翻转 Y
        y_axis = -y_axis
        R = np.column_stack([x_axis, y_axis, z_axis])
        det = float(np.linalg.det(R))
        if abs(det - 1.0) > 0.01:
            # 无法修复，返回 None
            return None

    return PaperWorldFrame(
        origin_camera_m=[float(v) for v in p0],
        z_axis_camera=[float(v) for v in z_axis],
        x_axis_camera=[float(v) for v in x_axis],
        y_axis_camera=[float(v) for v in y_axis],
        inlier_ratio=plane.get("inlier_ratio", 0.0),
        rms_m=plane.get("rms_m", 0.0),
        plane_points=plane.get("plane_points", 0),
        paper_mask_frame_id=plane.get("paper_mask_frame_id", -1),
        paper_clip_id=plane.get("paper_clip_id", ""),
        frame_timestamp_ns=plane.get("frame_timestamp_ns", 0),
    )


def classify_brush_paper(
    masks: list,
) -> tuple[int | None, int | None, list[str]]:
    """给定同一帧的 SAM3 多对象掩码，返回 (brush_idx, paper_idx, reasons)。

    masks: list[SegmentationMask]，同一 camera_frame_id 的所有对象。

    分类规则（确定性）：
    - 纸：面积最大，通常 ≫ 毛笔
    - 毛笔：面积远小于纸，细长（长宽比 > 3）
    - 若只有 1 个对象：面积 > 30% 画面 → paper，否则 → brush
    - 若 ≥ 2 个对象：面积最大 → paper（若面积比 > 5×），否则按长宽比
    """
    if not masks:
        return None, None, ["NO_MASKS"]

    reasons: list[str] = []
    total_area = int(masks[0].mask.size) if masks else 1

    # 计算每个掩码的面积和长宽比
    stats = []
    for i, m in enumerate(masks):
        mask = m.mask.astype(np.uint8)
        area = int(np.count_nonzero(mask))
        ys, xs = np.nonzero(mask)
        if len(xs) < 20:
            stats.append((i, 0.0, 0.0, 0.0))
            continue
        w = float(xs.max() - xs.min() + 1)
        h = float(ys.max() - ys.min() + 1)
        aspect = max(w, h) / max(1.0, min(w, h))
        fraction = area / max(1, total_area)
        stats.append((i, area, aspect, fraction))

    # 按面积降序排序
    stats_sorted = sorted(stats, key=lambda s: s[1], reverse=True)

    if len(stats) == 1:
        idx, area, aspect, fraction = stats_sorted[0]
        if fraction > 0.30:
            # 大面积 → 纸
            return None, idx, []
        else:
            # 细长 → 毛笔
            return idx, None, []

    # 两个或以上对象
    largest_idx, largest_area, largest_aspect, largest_frac = stats_sorted[0]
    second_idx, second_area, second_aspect, second_frac = stats_sorted[1]

    # 面积比
    area_ratio = largest_area / max(1, second_area)

    if area_ratio > 5.0:
        # 明显一大一小 → 大的是纸，小的是毛笔
        # 但要确认大的确实更像纸（长宽比 < 5）
        if largest_aspect < 5.0:
            return second_idx, largest_idx, []
        elif second_aspect > 3.0:
            return second_idx, largest_idx, []
        else:
            # 不确定，按面积
            return second_idx, largest_idx, ["AREA_BASED_CLASSIFICATION"]

    elif largest_aspect > 3.0 and second_aspect < 3.0:
        # 一个细长（毛笔），一个方形（纸）
        return largest_idx, second_idx, []
    elif second_aspect > 3.0 and largest_aspect < 3.0:
        return second_idx, largest_idx, []

    # 模糊情况
    reasons.append("AMBIGUOUS_OBJECT_IDENTITY")
    return None, None, reasons

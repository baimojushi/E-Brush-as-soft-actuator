from __future__ import annotations
import json
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np


def fit_rigid_transform(source_xyz: Sequence[Sequence[float]], target_xyz: Sequence[Sequence[float]]) -> np.ndarray:
    """
    Kabsch 刚体标定。source/target 至少 3 个非共线点。
    返回 4x4 齐次矩阵 T_target_source，使 target ~= T * source。
    """
    src = np.asarray(source_xyz, dtype=np.float64)
    dst = np.asarray(target_xyz, dtype=np.float64)
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 3 or src.shape[0] < 3:
        raise ValueError("source_xyz/target_xyz 必须为 N×3 且 N>=3")

    c_src = src.mean(axis=0)
    c_dst = dst.mean(axis=0)
    X = src - c_src
    Y = dst - c_dst
    H = X.T @ Y
    U, _, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    if np.linalg.det(R) < 0:
        Vt[-1, :] *= -1
        R = Vt.T @ U.T
    t = c_dst - R @ c_src

    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = R
    T[:3, 3] = t
    return T


def apply_transform(T: np.ndarray, xyz: Sequence[Sequence[float]]) -> np.ndarray:
    pts = np.asarray(xyz, dtype=np.float64)
    single = pts.ndim == 1
    pts = np.atleast_2d(pts)
    ones = np.ones((pts.shape[0], 1))
    out = (np.asarray(T, dtype=np.float64) @ np.hstack([pts, ones]).T).T[:, :3]
    return out[0] if single else out


def rms_error(T: np.ndarray, source_xyz, target_xyz) -> float:
    pred = apply_transform(T, source_xyz)
    dst = np.asarray(target_xyz, dtype=np.float64)
    return float(np.sqrt(np.mean(np.sum((pred - dst) ** 2, axis=1))))


def save_calibration(path: str, calibration_id: int, T_paper_camera: np.ndarray, metadata: dict | None = None) -> None:
    payload = {
        "schema": "BRUSH_CALIBRATION_V1",
        "calibration_id": int(calibration_id),
        "T_paper_camera": np.asarray(T_paper_camera, dtype=float).tolist(),
        "metadata": metadata or {},
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_calibration(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))

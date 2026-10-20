#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HP60C RGB-Depth 软件级对齐模块 - 调试版
"""

import json
import numpy as np
import cv2
from pathlib import Path
from typing import Tuple, Dict, Any, Optional


CALIB_PATH = Path(r"D:\项目\交互艺术\展厅孪生\ai\hp60c_audit\audit_output\calibration_recomputed.json")


def load_calibration(path: Path = CALIB_PATH) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_camera_matrix(intr: Dict[str, float]) -> np.ndarray:
    K = np.eye(3, dtype=np.float64)
    K[0, 0] = intr['fx']
    K[1, 1] = intr['fy']
    K[0, 2] = intr['cx']
    K[1, 2] = intr['cy']
    return K


def build_distortion_coeffs(dist: Dict[str, float]) -> np.ndarray:
    return np.array([
        dist.get('k1', 0.0), dist.get('k2', 0.0),
        dist.get('p1', 0.0), dist.get('p2', 0.0),
        dist.get('k3', 0.0), dist.get('k4', 0.0),
        dist.get('k5', 0.0), dist.get('k6', 0.0),
    ], dtype=np.float64)


def build_extrinsics(extr: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
    R = np.array(extr['rotation'], dtype=np.float64).reshape(3, 3)
    T = np.array(extr['translation_mm'], dtype=np.float64).reshape(3, 1)
    return R, T


class HP60CAligner:
    def __init__(self, calib_path: Path = CALIB_PATH):
        calib = load_calibration(calib_path)
        
        self.width = calib['intrinsics']['rgb']['width']
        self.height = calib['intrinsics']['rgb']['height']
        
        self.K_rgb = build_camera_matrix(calib['intrinsics']['rgb'])
        self.dist_rgb = build_distortion_coeffs(calib['distortion']['rgb'])
        
        self.K_depth = build_camera_matrix(calib['intrinsics']['depth'])
        self.dist_depth = build_distortion_coeffs(calib['distortion']['depth'])
        
        self.R_d2r, self.T_d2r = build_extrinsics(calib['extrinsics_depth_to_rgb'])
        
        self._init_maps()
        
        print(f"[Aligner] Init: {self.width}x{self.height}")
        print(f"[Aligner] K_rgb:\n{self.K_rgb}")
        print(f"[Aligner] K_depth:\n{self.K_depth}")
        print(f"[Aligner] R_d2r:\n{self.R_d2r}")
        print(f"[Aligner] T_d2r (mm):\n{self.T_d2r.ravel()}")
    
    def _init_maps(self):
        # RGB 畸变校正映射
        self.map_rgb_x, self.map_rgb_y = cv2.initUndistortRectifyMap(
            self.K_rgb, self.dist_rgb, None, self.K_rgb,
            (self.width, self.height), cv2.CV_32FC1
        )
        
        # Depth 畸变校正映射
        self.map_depth_x, self.map_depth_y = cv2.initUndistortRectifyMap(
            self.K_depth, self.dist_depth, None, self.K_depth,
            (self.width, self.height), cv2.CV_32FC1
        )
        
        # 预计算：Depth 像素网格
        u, v = np.meshgrid(
            np.arange(self.width, dtype=np.float32),
            np.arange(self.height, dtype=np.float32)
        )
        
        # Depth 归一化平面坐标
        Kd_inv = np.linalg.inv(self.K_depth.astype(np.float32))
        ones = np.ones_like(u)
        uv1 = np.stack([u, v, ones], axis=-1)  # (H, W, 3)
        X_depth_norm = uv1 @ Kd_inv.T  # (H, W, 3)
        
        self.Xn_x = X_depth_norm[..., 0]  # (H, W)
        self.Xn_y = X_depth_norm[..., 1]  # (H, W)
        
        # 预计算投影矩阵
        self.KR = (self.K_rgb @ self.R_d2r).astype(np.float32)  # (3, 3)
        self.KT = (self.K_rgb @ self.T_d2r).astype(np.float32).ravel()  # (3,)
    
    def undistort_rgb(self, rgb: np.ndarray) -> np.ndarray:
        return cv2.remap(rgb, self.map_rgb_x, self.map_rgb_y, cv2.INTER_LINEAR)
    
    def undistort_depth(self, depth: np.ndarray) -> np.ndarray:
        return cv2.remap(depth, self.map_depth_x, self.map_depth_y, cv2.INTER_NEAREST)
    
    def align_depth_to_rgb(self, depth: np.ndarray, rgb_shape: Tuple[int, int] = None) -> np.ndarray:
        """将深度图对齐到 RGB 像素坐标系"""
        if rgb_shape is None:
            rgb_shape = (self.height, self.width)
        
        H_d, W_d = depth.shape[:2]
        H_r, W_r = rgb_shape
        
        if (H_d, W_d) != (self.height, self.width):
            raise ValueError(f"Depth size {W_d}x{H_d} != calibrated {self.width}x{self.height}")
        
        # 1. Depth 畸变校正
        depth_undist = cv2.remap(depth, self.map_depth_x, self.map_depth_y, cv2.INTER_NEAREST)
        
        # 2. 有效掩码
        valid = depth_undist > 0
        if not np.any(valid):
            return np.zeros(rgb_shape, dtype=depth.dtype)
        
        # 3. 获取有效像素的归一化坐标
        x_norm = self.Xn_x[valid]
        y_norm = self.Xn_y[valid]
        z_mm = depth_undist[valid].astype(np.float32)
        
        # 3.1 转换到米
        z_m = z_mm / 1000.0
        
        # 3.2 Depth 归一化平面 -> 3D 点 (Depth 相机坐标系)
        # X_depth_cam = [x_norm, y_norm, 1] * z_m
        X_depth = np.stack([
            x_norm * z_m,
            y_norm * z_m,
            z_m
        ], axis=1)  # (N, 3)
        
        # 3.3 变换到 RGB 相机坐标系: X_rgb = R * X_depth + T
        # T 单位是 mm，转米
        T_m = self.T_d2r.ravel().astype(np.float32) / 1000.0
        R_f = self.R_d2r.astype(np.float32)
        X_rgb = (R_f @ X_depth.T).T + T_m  # (N, 3)
        
        # 3.4 投影到 RGB 像素平面
        # u_rgb = K_rgb * X_rgb
        u_rgb = (self.KR @ X_rgb.T).T + self.KT  # (N, 3)
        
        # 透视除法
        u = u_rgb[:, 0] / u_rgb[:, 2]
        v = u_rgb[:, 1] / u_rgb[:, 2]
        
        # 调试：打印投影坐标范围
        print(f"[Debug] Projected u: min={u.min():.1f}, max={u.max():.1f}")
        print(f"[Debug] Projected v: min={v.min():.1f}, max={v.max():.1f}")
        print(f"[Debug] X_rgb z: min={X_rgb[:,2].min():.3f}, max={X_rgb[:,2].max():.3f}")
        
        # 3.5 创建输出深度图
        aligned = np.zeros(rgb_shape, dtype=depth.dtype)
        
        u_int = np.round(u).astype(np.int32)
        v_int = np.round(v).astype(np.int32)
        
        # 边界检查
        mask = (u_int >= 0) & (u_int < W_r) & (v_int >= 0) & (v_int < H_r)
        u_valid = u_int[mask]
        v_valid = v_int[mask]
        z_valid = z_mm[mask]
        
        print(f"[Debug] Valid projections: {len(z_valid)} / {len(z_mm)}")
        
        if len(z_valid) > 0:
            # 处理多个点投影到同一像素：取最小深度
            for ui, vi, zi in zip(u_valid, v_valid, z_valid):
                if zi < aligned[vi, ui] or aligned[vi, ui] == 0:
                    aligned[vi, ui] = zi
        
        return aligned


def create_aligner(calib_path: Path = CALIB_PATH) -> HP60CAligner:
    return HP60CAligner(calib_path)


if __name__ == "__main__":
    aligner = create_aligner()
    print("\n[Test] Aligner created successfully!")
    
    # 测试投影几何
    print("\n[Test] Projection test at center (320, 240):")
    depth_test = np.zeros((480, 640), dtype=np.uint16)
    depth_test[240, 320] = 1000  # 1米
    
    # 先做畸变校正
    depth_undist = aligner.undistort_depth(depth_test)
    print(f"  Depth undist at (240, 320): {depth_undist[240, 320]}")
    
    # 再做对齐
    aligned = aligner.align_depth_to_rgb(depth_undist)
    print(f"  Aligned depth shape: {aligned.shape}")
    print(f"  Non-zero pixels: {np.count_nonzero(aligned)}")
    if np.any(aligned > 0):
        idx = np.argmax(aligned)
        print(f"  Max at: {np.unravel_index(idx, aligned.shape)}, value={aligned.flat[idx]}mm")
    
    # 测试多个点
    print("\n[Test] Multiple points:")
    depth_test2 = np.zeros((480, 640), dtype=np.uint16)
    for y, x in [(240, 320), (100, 100), (380, 500), (100, 500), (380, 100)]:
        depth_test2[y, x] = 1500
    
    depth_undist2 = aligner.undistort_depth(depth_test2)
    aligned2 = aligner.align_depth_to_rgb(depth_undist2)
    print(f"  Non-zero pixels: {np.count_nonzero(aligned2)}")
    if np.any(aligned2 > 0):
        coords = np.argwhere(aligned2 > 0)
        for c in coords[:10]:
            print(f"    ({c[0]}, {c[1]}) = {aligned2[c[0], c[1]]}mm")
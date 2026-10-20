#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HP60C RGB-Depth 软件级对齐模块
基于 audit_output/calibration_recomputed.json 的标定参数
将深度图从 depth 坐标系变换到 RGB 坐标系（像素级对齐）
"""

import json
import numpy as np
import cv2
from pathlib import Path
from typing import Tuple, Dict, Any, Optional


CALIB_PATH = Path(r"D:\项目\交互艺术\展厅孪生\ai\hp60c_audit\audit_output\calibration_recomputed.json")


def load_calibration(path: Path = CALIB_PATH) -> Dict[str, Any]:
    """加载标定参数"""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_camera_matrix(intr: Dict[str, float]) -> np.ndarray:
    """构建相机内参矩阵 K"""
    K = np.eye(3, dtype=np.float64)
    K[0, 0] = intr['fx']
    K[1, 1] = intr['fy']
    K[0, 2] = intr['cx']
    K[1, 2] = intr['cy']
    return K


def build_distortion_coeffs(dist: Dict[str, float]) -> np.ndarray:
    """构建畸变系数 [k1, k2, p1, p2, k3, k4, k5, k6]"""
    # OpenCV 标准顺序：k1, k2, p1, p2, k3, k4, k5, k6
    return np.array([
        dist.get('k1', 0.0),
        dist.get('k2', 0.0),
        dist.get('p1', 0.0),
        dist.get('p2', 0.0),
        dist.get('k3', 0.0),
        dist.get('k4', 0.0),
        dist.get('k5', 0.0),
        dist.get('k6', 0.0),
    ], dtype=np.float64)


def build_extrinsics(extr: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
    """构建外参 R, T (Depth -> RGB)"""
    R = np.array(extr['rotation'], dtype=np.float64).reshape(3, 3)
    T = np.array(extr['translation_mm'], dtype=np.float64).reshape(3, 1)  # mm
    return R, T


class HP60CAligner:
    """HP60C RGB-Depth 对齐器"""
    
    def __init__(self, calib_path: Path = CALIB_PATH):
        calib = load_calibration(calib_path)
        
        # 图像尺寸
        self.width = calib['intrinsics']['rgb']['width']
        self.height = calib['intrinsics']['rgb']['height']
        
        # RGB 相机
        self.K_rgb = build_camera_matrix(calib['intrinsics']['rgb'])
        self.dist_rgb = build_distortion_coeffs(calib['distortion']['rgb'])
        
        # Depth 相机
        self.K_depth = build_camera_matrix(calib['intrinsics']['depth'])
        self.dist_depth = build_distortion_coeffs(calib['distortion']['depth'])
        
        # Depth -> RGB 外参
        self.R_d2r, self.T_d2r = build_extrinsics(calib['extrinsics_depth_to_rgb'])
        
        # 预计算 undistort/rectify 映射表
        self._init_maps()
        
        print(f"[Aligner] Init: {self.width}x{self.height}")
        print(f"[Aligner] K_rgb:\n{self.K_rgb}")
        print(f"[Aligner] K_depth:\n{self.K_depth}")
        print(f"[Aligner] R_d2r:\n{self.R_d2r}")
        print(f"[Aligner] T_d2r (mm):\n{self.T_d2r.ravel()}")
    
    def _init_maps(self):
        """预计算畸变校正 + 立体校正映射表"""
        # 1. RGB 畸变校正映射 (不做立体校正，保持原图坐标系)
        self.map_rgb_x, self.map_rgb_y = cv2.initUndistortRectifyMap(
            self.K_rgb, self.dist_rgb, None, self.K_rgb,
            (self.width, self.height), cv2.CV_32FC1
        )
        
        # 2. Depth 畸变校正映射 (Depth 无畸变，但仍需归一化)
        self.map_depth_x, self.map_depth_y = cv2.initUndistortRectifyMap(
            self.K_depth, self.dist_depth, None, self.K_depth,
            (self.width, self.height), cv2.CV_32FC1
        )
        
        # 3. 计算 Depth->RGB 的投影矩阵
        # 将 Depth 图像投影到 RGB 相机平面
        # 步骤：Depth像素 -> Depth归一化平面 -> 3D点 -> RGB相机坐标 -> RGB像素
        
        # 构建 Depth 到 RGB 的完整变换矩阵 (3x4)
        # X_rgb = R * X_depth + T
        # 像素坐标: u_depth = K_depth * X_depth_norm
        # X_depth_norm = K_depth^-1 * [u, v, 1]^T * z
        # 这里我们用稠密映射表实现
        
        # 预计算：Depth 像素 -> RGB 像素的映射表
        self._compute_depth_to_rgb_map()
    
    def _compute_depth_to_rgb_map(self):
        """计算 Depth 像素到 RGB 像素的稠密映射表"""
        # 生成 Depth 图像的所有像素坐标网格
        u, v = np.meshgrid(
            np.arange(self.width, dtype=np.float32),
            np.arange(self.height, dtype=np.float32)
        )
        
        # Depth 归一化平面坐标 (x, y, 1)
        # X_norm = K_depth^-1 * [u, v, 1]^T
        Kd_inv = np.linalg.inv(self.K_depth.astype(np.float32))
        ones = np.ones_like(u)
        uv1 = np.stack([u, v, ones], axis=-1)  # (H, W, 3)
        X_depth_norm = uv1 @ Kd_inv.T  # (H, W, 3)
        
        # 应用外参变换：X_rgb = R * X_depth + T
        # 注意：X_depth_norm 是归一化坐标，深度值 z=1
        # 实际 3D 点 = X_depth_norm * depth_mm
        # 这里我们只计算归一化平面的变换，深度由运行时提供
        
        # 存储归一化平面坐标 (x, y)，用于运行时乘以 depth 再投影
        self.X_depth_norm_x = X_depth_norm[..., 0]  # (H, W)
        self.X_depth_norm_y = X_depth_norm[..., 1]  # (H, W)
        
        # 预计算 R * [x, y, 1]^T 的旋转部分
        # X_rgb_norm = R * X_depth_norm + T / depth
        # 运行时：X_rgb_norm = R @ [x, y, 1] + T / depth
        # 然后投影到 RGB 像素：u_rgb = K_rgb * X_rgb_norm
        
        # 预计算 K_rgb * R (3x3)
        self.KR = (self.K_rgb @ self.R_d2r).astype(np.float32)  # (3, 3)
        self.KT = (self.K_rgb @ self.T_d2r).astype(np.float32).ravel()  # (3,)
    
    def undistort_rgb(self, rgb: np.ndarray) -> np.ndarray:
        """RGB 畸变校正"""
        return cv2.remap(rgb, self.map_rgb_x, self.map_rgb_y, cv2.INTER_LINEAR)
    
    def undistort_depth(self, depth: np.ndarray) -> np.ndarray:
        """Depth 畸变校正 (Depth 无畸变，仅归一化)"""
        return cv2.remap(depth, self.map_depth_x, self.map_depth_y, cv2.INTER_NEAREST)
    
    def align_depth_to_rgb(self, depth: np.ndarray, rgb_shape: Tuple[int, int] = None) -> np.ndarray:
        """
        将深度图对齐到 RGB 像素坐标系
        
        Args:
            depth: (H, W) uint16 mm 或 float32 m
            rgb_shape: 目标 RGB 尺寸 (h, w)，默认用标定尺寸
            
        Returns:
            aligned_depth: (H_rgb, W_rgb) 同 dtype，对齐后的深度图
        """
        if rgb_shape is None:
            rgb_shape = (self.height, self.width)
        
        H_d, W_d = depth.shape[:2]
        H_r, W_r = rgb_shape
        
        # 确保输入尺寸匹配标定
        if (H_d, W_d) != (self.height, self.width):
            raise ValueError(f"Depth size {W_d}x{H_d} != calibrated {self.width}x{self.height}")
        
        # 1. Depth 畸变校正 (最近邻插值保持离散深度值)
        depth_undist = cv2.remap(depth, self.map_depth_x, self.map_depth_y, cv2.INTER_NEAREST)
        
        # 2. 生成有效深度掩码
        valid = depth_undist > 0
        if not np.any(valid):
            return np.zeros(rgb_shape, dtype=depth.dtype)
        
        # 3. 将有效深度像素投影到 RGB 坐标系
        # 3.1 获取有效像素的归一化坐标
        x_norm = self.X_depth_norm_x[valid]  # (N,)
        y_norm = self.X_depth_norm_y[valid]  # (N,)
        z_depth = depth_undist[valid].astype(np.float32)  # mm
        
        # 3.2 转换到米 (外参 T 单位是 mm)
        z_m = z_depth / 1000.0
        
        # 3.3 Depth 归一化平面 -> 3D 点 (Depth 相机坐标系)
        # X_depth_cam = [x_norm, y_norm, 1] * z_m
        X_depth = np.stack([
            x_norm * z_m,
            y_norm * z_m,
            z_m
        ], axis=1)  # (N, 3)
        
        # 3.4 变换到 RGB 相机坐标系: X_rgb = R * X_depth + T
        # T 单位是 mm，转米
        T_m = self.T_d2r.ravel().astype(np.float32) / 1000.0
        X_rgb = (self.R_d2r.astype(np.float32) @ X_depth.T).T + T_m  # (N, 3)
        
        # 3.5 投影到 RGB 像素平面
        # u_rgb = K_rgb * X_rgb
        u_rgb = (self.KR @ X_rgb.T).T + self.KT  # (N, 3)
        u = u_rgb[:, 0] / u_rgb[:, 2]
        v = u_rgb[:, 1] / u_rgb[:, 2]
        
        # 3.6 创建输出深度图，用最近邻填充
        aligned = np.zeros(rgb_shape, dtype=depth.dtype)
        
        # 四舍五入到最近像素
        u_int = np.round(u).astype(np.int32)
        v_int = np.round(v).astype(np.int32)
        
        # 边界检查
        mask = (u_int >= 0) & (u_int < W_r) & (v_int >= 0) & (v_int < H_r)
        u_valid = u_int[mask]
        v_valid = v_int[mask]
        z_valid = z_depth[mask]
        
        if len(z_valid) > 0:
            # 处理多个点投影到同一像素：取最小深度 (最近表面)
            # 使用简单的索引累积
            for ui, vi, zi in zip(u_valid, v_valid, z_valid):
                if zi < aligned[vi, ui] or aligned[vi, ui] == 0:
                    aligned[vi, ui] = zi
        
        return aligned
    
    def align_depth_to_rgb_fast(self, depth: np.ndarray, rgb_shape: Tuple[int, int] = None) -> np.ndarray:
        """
        快速版本：使用 OpenCV remap + 预计算映射表
        适合实时应用
        """
        if rgb_shape is None:
            rgb_shape = (self.height, self.width)
        
        H_d, W_d = depth.shape[:2]
        H_r, W_r = rgb_shape
        
        if (H_d, W_d) != (self.height, self.width):
            raise ValueError(f"Depth size {W_d}x{H_d} != calibrated {self.width}x{self.height}")
        
        # 1. Depth 畸变校正
        depth_undist = cv2.remap(depth, self.map_depth_x, self.map_depth_y, cv2.INTER_NEAREST)
        
        # 2. 使用预计算的映射表 (需要在 _compute_maps 中生成 map_d2r_x, map_d2r_y)
        # 暂时用逐像素版本，后续优化
        return self.align_depth_to_rgb(depth_undist, rgb_shape)


# 便捷函数
def create_aligner(calib_path: Path = CALIB_PATH) -> HP60CAligner:
    return HP60CAligner(calib_path)


if __name__ == "__main__":
    # 测试初始化
    aligner = create_aligner()
    print("\n[Test] Aligner created successfully!")
    
    # 验证投影几何
    print("\n[Test] Projection test:")
    # Depth 中心像素 (320, 240) 对应的 RGB 像素
    depth_test = np.zeros((480, 640), dtype=np.uint16)
    depth_test[240, 320] = 1000  # 1米
    aligned = aligner.align_depth_to_rgb(depth_test)
    print(f"  Aligned depth shape: {aligned.shape}")
    print(f"  Non-zero pixels: {np.count_nonzero(aligned)}")
    if np.any(aligned > 0):
        idx = np.argmax(aligned)
        print(f"  Max at: {np.unravel_index(idx, aligned.shape)}, value={aligned.flat[idx]}mm")
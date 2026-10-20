#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深度调试投影矩阵计算
"""

import json
import numpy as np
import cv2
from pathlib import Path


CALIB_PATH = Path(r"D:\项目\交互艺术\展厅孪生\ai\hp60c_audit\audit_output\calibration_recomputed.json")


def load_calibration(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_camera_matrix(intr):
    K = np.eye(3, dtype=np.float64)
    K[0, 0] = intr['fx']
    K[1, 1] = intr['fy']
    K[0, 2] = intr['cx']
    K[1, 2] = intr['cy']
    return K


calib = load_calibration(CALIB_PATH)

# 相机内参
K_rgb = build_camera_matrix(calib['intrinsics']['rgb'])
K_depth = build_camera_matrix(calib['intrinsics']['depth'])

# 外参
R = np.array(calib['extrinsics_depth_to_rgb']['rotation'], dtype=np.float64).reshape(3, 3)
T_mm = np.array(calib['extrinsics_depth_to_rgb']['translation_mm'], dtype=np.float64).reshape(3, 1)

print("=== 标定参数 ===")
print(f"K_rgb:\n{K_rgb}")
print(f"K_depth:\n{K_depth}")
print(f"R:\n{R}")
print(f"T_mm:\n{T_mm.ravel()}")

# 测试点：Depth 图像中心 (320, 240), 深度 1000mm
u_d, v_d = 320, 240
z_mm = 1000.0

print(f"\n=== 测试点: Depth像素({u_d},{v_d}), 深度={z_mm}mm ===")

# 1. Depth 像素 -> 归一化平面坐标
Kd_inv = np.linalg.inv(K_depth)
uv1 = np.array([u_d, v_d, 1.0], dtype=np.float64)
Xn = Kd_inv @ uv1  # [x_norm, y_norm, 1]
print(f"X_norm (K_depth^-1 * [u,v,1]): {Xn}")

# 2. 归一化坐标 -> 3D 点 (Depth 相机坐标系，单位：米)
z_m = z_mm / 1000.0
X_depth_cam = Xn * z_m
print(f"X_depth_cam (meters): {X_depth_cam}")

# 3. Depth -> RGB 坐标变换
T_m = T_mm / 1000.0  # 转米
X_rgb_cam = (R @ X_depth_cam.reshape(3,1) + T_m).ravel()
print(f"X_rgb_cam (meters): {X_rgb_cam}")

# 4. 投影到 RGB 像素
u_rgb = K_rgb @ X_rgb_cam
u = u_rgb[0] / u_rgb[2]
v = u_rgb[1] / u_rgb[2]
print(f"RGB像素坐标: u={u:.1f}, v={v:.1f}")
print(f"预期RGB中心附近: cx={K_rgb[0,2]:.1f}, cy={K_rgb[1,2]:.1f}")

# === 现在检查我代码中的 KR, KT 计算 ===
print("\n=== 代码中的矩阵计算 ===")
KR = (K_rgb @ R).astype(np.float32)
KT = (K_rgb @ T_mm).astype(np.float32).ravel()  # 这里有问题！T_mm 是 mm 单位
print(f"KR (K_rgb @ R):\n{KR}")
print(f"KT (K_rgb @ T_mm): {KT}  <- 单位是 pixel*mm，不对！")

# 正确的 KT 应该是：
KT_correct = (K_rgb @ T_m).astype(np.float32).ravel()
print(f"KT_correct (K_rgb @ T_m): {KT_correct}")

# === 验证代码中的计算方式 ===
print("\n=== 代码中的计算方式 ===")
# 代码中：u_rgb = (KR @ X_rgb.T).T + KT
# 其中 X_rgb = (R @ X_depth.T).T + T_m
# 所以 u_rgb = KR @ (R @ X_depth + T_m) + KT
#       = K_rgb @ R @ R @ X_depth + K_rgb @ R @ T_m + K_rgb @ T_mm
# 这不对！重复应用了 R 和 T！

# 正确应该是：
# u_rgb = K_rgb @ (R @ X_depth + T_m)
#       = K_rgb @ R @ X_depth + K_rgb @ T_m
# 所以 KT 应该是 K_rgb @ T_m，且 KR = K_rgb @ R

print(f"\n=== 正确公式 ===")
X_depth_homo = np.array([Xn[0]*z_m, Xn[1]*z_m, z_m], dtype=np.float64)
u_rgb_correct = K_rgb @ (R @ X_depth_homo + T_m.ravel())
u_c = u_rgb_correct[0] / u_rgb_correct[2]
v_c = u_rgb_correct[1] / u_rgb_correct[2]
print(f"正确投影: u={u_c:.1f}, v={v_c:.1f}")

# === 检查代码中的 KR, KT 定义 ===
print("\n=== 代码中的 KR, KT 定义 ===")
print("代码: self.KR = (self.K_rgb @ self.R_d2r).astype(np.float32)")
print("代码: self.KT = (self.K_rgb @ self.T_d2r).astype(np.float32).ravel()")
print("问题: T_d2r 是 mm 单位，KT 变成了 pixel*mm")
print("修正: self.KT = (self.K_rgb @ (self.T_d2r / 1000.0)).astype(np.float32).ravel()")

# === 验证代码中的投影计算 ===
print("\n=== 代码中的投影计算 ===")
# 代码中：
# X_depth = [x_norm*z, y_norm*z, z]  (meters)
# X_rgb = R @ X_depth.T + T_m  (meters)  -- 正确
# u_rgb = (KR @ X_rgb.T).T + KT
# 其中 KR = K_rgb @ R, KT = K_rgb @ T_mm (WRONG!)
# 所以 u_rgb = K_rgb @ R @ X_rgb + K_rgb @ T_mm
#           = K_rgb @ R @ (R @ X_depth + T_m) + K_rgb @ T_mm
#           = K_rgb @ R @ R @ X_depth + K_rgb @ R @ T_m + K_rgb @ T_mm
# 这里有双重 R，且 T 单位不对！

# 正确应该是：
# X_rgb 已经是 RGB 相机坐标系下的 3D 点 (meters)
# 直接 u_rgb = K_rgb @ X_rgb
# 或者用 KR, KT：
# u_rgb = (K_rgb @ R) @ X_depth + K_rgb @ T_m
# 其中 X_depth 是 depth 相机坐标系下的 3D 点

print("\n=== 总结问题 ===")
print("1. KT 单位错误：K_rgb @ T_mm (mm) -> 应该是 K_rgb @ T_m (m)")
print("2. 投影公式错误：代码对已经在 RGB 坐标系的 X_rgb 又乘了 KR")
print("   代码: u_rgb = (KR @ X_rgb.T).T + KT")
print("   应该: u_rgb = K_rgb @ X_rgb")
print("   或者用 KR: u_rgb = KR @ X_depth + KT_correct")
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HP60C → TouchDesigner 直接桥接 v7（检查 pointCloud 字段获取深度 + RGB-Depth 对齐）

数据路径：
  AngstrongCameraSdk.dll → sdk.py (ctypes) → 回调线程 → 线程安全队列
                        → TD Script TOP (深度/RGB/IR/点云) / Script CHOP (点云)

用法：
  1. td_init()          - 初始化相机，配置流参数，启动流
  2. td_get_depth()     - 获取最新深度帧
  3. td_get_rgb()       - 获取最新 RGB 帧
  4. td_get_ir()        - 获取最新 IR 帧
  5. td_get_pointcloud() - 获取最新点云
  6. td_get_depth_aligned() - 获取对齐到 RGB 的深度帧
  7. td_get_stats()     - 获取统计信息
  8. td_shutdown()      - 关闭相机
"""

# ============================================================
# 全局状态（模块级）
# ============================================================
import sys
import os
import time
import threading
import queue
import ctypes
import numpy as np
from typing import Optional, Dict, Any

# 添加审计项目路径
AUDIT_DIR = r"D:\项目\交互艺术\展厅孪生\ai\hp60c_audit"
if AUDIT_DIR not in sys.path:
    sys.path.insert(0, AUDIT_DIR)

import sdk  # 审计项目中的 CameraSdk 类

# 全局变量
_cam_sdk: Optional[sdk.CameraSdk] = None
_camera_handle = None
_running = False
_frame_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=2)
_latest_frame: Optional[Dict[str, Any]] = None
_stats = {
    "frame_count": 0,
    "depth_frames": 0,
    "rgb_frames": 0,
    "ir_frames": 0,
    "pc_frames": 0,
    "merge_frames": 0,
    "aligned_frames": 0,
    "last_frame_time": 0.0,
    "errors": 0,
}
_lock = threading.Lock()

# 回调引用（防止 GC）
_stream_cb_ref = None
_merge_cb_ref = None

# ============================================================
# RGB-Depth 对齐器（内嵌，避免额外依赖）
# ============================================================
class _DepthAligner:
    """HP60C RGB-Depth 软件级对齐器"""
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        # 标定参数（来自 audit_output/calibration_recomputed.json）
        self.width = 640
        self.height = 480
        
        # RGB 相机内参
        self.K_rgb = np.array([
            [583.93653661, 0.0, 337.52662648],
            [0.0, 584.71100721, 235.27076084],
            [0.0, 0.0, 1.0]
        ], dtype=np.float32)
        
        self.dist_rgb = np.array([
            -0.018551162092571415, 0.4767321470864813,
            0.0001360337423618726, 0.0016659518780197354,
            -1.252309932027979, 0.0, 0.0, 0.0
        ], dtype=np.float32)
        
        # Depth 相机内参
        self.K_depth = np.array([
            [421.893219, 0.0, 320.463593],
            [0.0, 421.451019, 237.050751],
            [0.0, 0.0, 1.0]
        ], dtype=np.float32)
        
        self.dist_depth = np.array([0.0]*8, dtype=np.float32)
        
        # Depth -> RGB 外参
        self.R_d2r = np.array([
            [0.999994, 0.000443, -0.003517],
            [-0.000419, 0.999976, 0.006975],
            [0.00352, -0.006974, 0.999969]
        ], dtype=np.float32)
        
        self.T_d2r_m = np.array([
            [-12.098233], [0.163637], [0.178466]
        ], dtype=np.float32) / 1000.0  # mm -> m
        
        # 预计算畸变校正映射表
        self._init_undistort_maps()
        
        # 预计算投影矩阵
        self._init_projection_matrices()
        
        print("[Aligner] Initialized with calibration from audit_output")
    
    def _init_undistort_maps(self):
        import cv2
        self.map_rgb_x, self.map_rgb_y = cv2.initUndistortRectifyMap(
            self.K_rgb, self.dist_rgb, None, self.K_rgb,
            (self.width, self.height), cv2.CV_32FC1
        )
        self.map_depth_x, self.map_depth_y = cv2.initUndistortRectifyMap(
            self.K_depth, self.dist_depth, None, self.K_depth,
            (self.width, self.height), cv2.CV_32FC1
        )
    
    def _init_projection_matrices(self):
        u, v = np.meshgrid(
            np.arange(self.width, dtype=np.float32),
            np.arange(self.height, dtype=np.float32)
        )
        Kd_inv = np.linalg.inv(self.K_depth)
        ones = np.ones_like(u)
        uv1 = np.stack([u, v, ones], axis=-1)
        X_depth_norm = uv1 @ Kd_inv.T
        self.Xn_x = X_depth_norm[..., 0]
        self.Xn_y = X_depth_norm[..., 1]
        
        self.KR = (self.K_rgb @ self.R_d2r).astype(np.float32)
        self.KT = (self.K_rgb @ (self.T_d2r_m / 1000.0)).astype(np.float32).ravel()
    
    def undistort_depth(self, depth):
        import cv2
        return cv2.remap(depth, self.map_depth_x, self.map_depth_y, cv2.INTER_NEAREST)
    
    def align_depth_to_rgb(self, depth):
        """将深度图对齐到 RGB 像素坐标系"""
        import cv2
        depth_undist = self.undistort_depth(depth)
        
        valid = depth_undist > 0
        if not np.any(valid):
            return np.zeros((self.height, self.width), dtype=depth.dtype)
        
        x_norm = self.Xn_x[valid]
        y_norm = self.Xn_y[valid]
        z_mm = depth_undist[valid].astype(np.float32)
        z_m = z_mm / 1000.0
        
        X_depth = np.stack([
            x_norm * z_m,
            y_norm * z_m,
            z_m
        ], axis=1)
        
        u_rgb = (self.KR @ X_depth.T).T + self.KT
        u = u_rgb[:, 0] / u_rgb[:, 2]
        v = u_rgb[:, 1] / u_rgb[:, 2]
        
        aligned = np.zeros((self.height, self.width), dtype=depth.dtype)
        u_int = np.round(u).astype(np.int32)
        v_int = np.round(v).astype(np.int32)
        
        mask = (u_int >= 0) & (u_int < self.width) & (v_int >= 0) & (v_int < self.height)
        u_valid = u_int[mask]
        v_valid = v_int[mask]
        z_valid = z_mm[mask]
        
        if len(z_valid) > 0:
            for ui, vi, zi in zip(u_valid, v_valid, z_valid):
                if zi < aligned[vi, ui] or aligned[vi, ui] == 0:
                    aligned[vi, ui] = zi
        
        return aligned


# 全局对齐器实例（延迟初始化）
_aligner = None


def _get_aligner():
    global _aligner
    if _aligner is None:
        _aligner = _DepthAligner()
    return _aligner

# ============================================================
# 回调函数（在 SDK 内部线程中调用） - 检查 pointCloud 字段获取深度
# ============================================================
def _stream_callback(pCamera, pData, privateData):
    global _stats
    if not _running:
        return
    data = pData.contents
    frame = {}
    ts = time.time()

    # 1. 先检查 pointCloud 字段（可能包含深度信息）
    if data.pointCloud.data and data.pointCloud.size > 0:
        w, h = data.pointCloud.width, data.pointCloud.height
        # pointCloud 可能是 float32 (xyz) 或 uint16 (深度图)
        # 先尝试作为 uint16 读取（深度图）
        buf_uint16 = np.frombuffer(
            (ctypes.c_ubyte * data.pointCloud.size).from_address(data.pointCloud.data),
            dtype=np.uint16
        ).copy()
        if buf_uint16.size == w * h:
            # 是深度图
            frame["depth"] = buf_uint16.reshape(h, w)
            with _lock:
                _stats["depth_frames"] = _stats.get("depth_frames", 0) + 1
        else:
            # 尝试作为 float32 点云
            buf_float = buf_uint16.view(np.float32)
            if buf_float.size % 3 == 0:
                frame["pointcloud"] = buf_float.reshape(-1, 3)
                with _lock:
                    _stats["pc_frames"] = _stats.get("pc_frames", 0) + 1

    # 2. 检查 depthImg 字段
    if data.depthImg.data and data.depthImg.size > 0:
        w, h = data.depthImg.width, data.depthImg.height
        buf = np.frombuffer(
            (ctypes.c_ubyte * data.depthImg.size).from_address(data.depthImg.data),
            dtype=np.uint16
        ).copy().reshape(h, w)
        frame["depth"] = buf
        with _lock:
            _stats["depth_frames"] = _stats.get("depth_frames", 0) + 1

    # 3. RGB
    if data.rgbImg.data and data.rgbImg.size > 0:
        w, h = data.rgbImg.width, data.rgbImg.height
        channels = data.rgbImg.size // (w * h)
        buf = np.frombuffer(
            (ctypes.c_ubyte * data.rgbImg.size).from_address(data.rgbImg.data),
            dtype=np.uint8
        ).copy().reshape(h, w, channels)
        # SDK 返回 BGR，转为 RGB
        try:
            import cv2
            if channels == 3:
                buf = cv2.cvtColor(buf, cv2.COLOR_BGR2RGB)
        except: pass
        frame["rgb"] = buf
        with _lock:
            _stats["rgb_frames"] = _stats.get("rgb_frames", 0) + 1

    # 4. MJPEG (HP60C may output RGB as MJPEG)
    if "rgb" not in frame and data.mjpegImg.data and data.mjpegImg.size > 0:
        try:
            import cv2
            buf = np.frombuffer(
                (ctypes.c_ubyte * data.mjpegImg.size).from_address(data.mjpegImg.data),
                dtype=np.uint8
            ).copy()
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is not None:
                # imdecode 返回 BGR，转为 RGB
                img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                frame["rgb"] = img
                with _lock:
                    _stats["rgb_frames"] = _stats.get("rgb_frames", 0) + 1
        except Exception as e:
            print(f"[Warning] MJPEG decode failed: {e}")

    # 5. YUYV (packed YUV 4:2:2)
    if "rgb" not in frame and data.yuyvImg.data and data.yuyvImg.size > 0:
        try:
            import cv2
            w, h = data.yuyvImg.width, data.yuyvImg.height
            buf = np.frombuffer(
                (ctypes.c_ubyte * data.yuyvImg.size).from_address(data.yuyvImg.data),
                dtype=np.uint8
            ).copy().reshape(h, w, 2)  # YUYV is 2 bytes per pixel
            img = cv2.cvtColor(buf, cv2.COLOR_YUV2BGR_YUYV)
            frame["rgb"] = img
            with _lock:
                _stats["rgb_frames"] = _stats.get("rgb_frames", 0) + 1
        except Exception as e:
            print(f"[Warning] YUYV decode failed: {e}")

    # 6. IR
    if data.irImg.data and data.irImg.size > 0:
        w, h = data.irImg.width, data.irImg.height
        buf = np.frombuffer(
            (ctypes.c_ubyte * data.irImg.size).from_address(data.irImg.data),
            dtype=np.uint16
        ).copy().reshape(h, w)
        frame["ir"] = buf
        with _lock:
            _stats["ir_frames"] = _stats.get("ir_frames", 0) + 1

    # 7. 点云（如果 pointCloud 没被当作深度用）
    if "pointcloud" not in frame and data.pointCloud.data and data.pointCloud.size > 0:
        w, h = data.pointCloud.width, data.pointCloud.height
        buf = np.frombuffer(
            (ctypes.c_ubyte * data.pointCloud.size).from_address(data.pointCloud.data),
            dtype=np.float32
        ).copy().reshape(-1, 3)
        frame["pointcloud"] = buf
        with _lock:
            _stats["pc_frames"] = _stats.get("pc_frames", 0) + 1

    frame["timestamp"] = ts
    frame["frame_id"] = data.depthImg.frameId if data.depthImg.data else 0
    frame["source"] = "stream"

    # 生成对齐深度（如果同时有 depth 和 rgb）
    if "depth" in frame and "rgb" in frame:
        try:
            aligner = _get_aligner()
            depth_aligned = aligner.align_depth_to_rgb(frame["depth"])
            if np.any(depth_aligned > 0):
                frame["depth_aligned"] = depth_aligned
                with _lock:
                    _stats["aligned_frames"] = _stats.get("aligned_frames", 0) + 1
        except Exception as e:
            print(f"[Warning] Depth alignment failed: {e}")

    try:
        _frame_queue.put_nowait(frame)
    except queue.Full:
        try:
            _frame_queue.get_nowait()
        except queue.Empty:
            pass
        _frame_queue.put_nowait(frame)

    with _lock:
        global _latest_frame
        _latest_frame = frame
        _stats["frame_count"] += 1
        _stats["last_frame_time"] = ts


def _merge_callback(pCamera, pData, privateData):
    """合并帧回调：提供对齐后的深度+点云"""
    global _stats
    if not _running:
        return
    data = pData.contents
    frame = {}
    ts = time.time()

    # 合并帧包含对齐后的深度和点云
    if data.depthImg.data and data.depthImg.size > 0:
        w, h = data.depthImg.width, data.depthImg.height
        buf = np.frombuffer(
            (ctypes.c_ubyte * data.depthImg.size).from_address(data.depthImg.data),
            dtype=np.uint16
        ).copy().reshape(h, w)
        frame["depth"] = buf
        with _lock:
            _stats["depth_frames"] = _stats.get("depth_frames", 0) + 1

    if data.pointCloud.data and data.pointCloud.size > 0:
        w, h = data.pointCloud.width, data.pointCloud.height
        buf = np.frombuffer(
            (ctypes.c_ubyte * data.pointCloud.size).from_address(data.pointCloud.data),
            dtype=np.float32
        ).copy().reshape(-1, 3)
        frame["pointcloud"] = buf
        with _lock:
            _stats["pc_frames"] = _stats.get("pc_frames", 0) + 1

    frame["timestamp"] = ts
    frame["frame_id"] = data.depthImg.frameId if data.depthImg.data else 0
    frame["source"] = "merge"

    try:
        _frame_queue.put_nowait(frame)
    except queue.Full:
        try:
            _frame_queue.get_nowait()
        except queue.Empty:
            pass
        _frame_queue.put_nowait(frame)

    with _lock:
        global _latest_frame
        _latest_frame = frame
        _stats["merge_frames"] = _stats.get("merge_frames", 0) + 1
        _stats["last_frame_time"] = ts

# 保持回调引用
_stream_cb_ref = sdk.StreamCb(_stream_callback)
_merge_cb_ref = sdk.MergeCb(_merge_callback)

# ============================================================
# 帧新鲜度检查（相机断开检测）
# ============================================================
MAX_FRAME_AGE_S = 1.0  # 超过 1 秒无新帧视为断开

def _is_frame_fresh(frame: Dict[str, Any]) -> bool:
    """检查帧是否在新鲜度阈值内"""
    if frame is None:
        return False
    ts = frame.get("timestamp")
    if ts is None:
        return False
    return (time.time() - ts) < MAX_FRAME_AGE_S


def _warmup_enabled() -> bool:
    """HP60C_V7_INIT=audit restores the legacy warmup/timestamp sequence."""
    return os.environ.get("HP60C_V7_INIT", "simplified").strip().lower() == "audit"


def _wait_camera_ready_after_reboot(timeout_s: float = 10.0) -> bool:
    """CAMERA_Reboot resets the USB device; poll until it enumerates again."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            ret, devs = _cam_sdk.enumerate(include_net=False)
        except Exception as exc:
            print(f"[HP60C] enumerate after reboot failed: {exc}")
            ret, devs = -1, []
        if ret == 0 and any(
                int(d.get("vid", 0)) == 13442 and int(d.get("pid", 0)) == 26403
                for d in (devs or [])):
            time.sleep(0.5)  # let the device node settle before open
            return True
        time.sleep(0.2)
    return False


# ============================================================
# TD 集成入口函数
# ============================================================
def td_init(config_path: str = None) -> Dict[str, Any]:
    """初始化相机，配置流参数，启动流"""
    global _cam_sdk, _camera_handle, _running

    if _running:
        return {"ok": True, "msg": "Already running"}

    if config_path is None:
        config_path = sdk.CONFIG_HP60C

    if not os.path.exists(config_path):
        return {"ok": False, "msg": f"Config not found: {config_path}"}

    try:
        _cam_sdk = sdk.CameraSdk()
    except Exception as e:
        return {"ok": False, "msg": f"Failed to create CameraSdk: {e}"}

    # 初始化 SDK
    ret = _cam_sdk.init()
    if ret != 0:
        return {"ok": False, "msg": f"SDK init failed: {ret}"}

    # 枚举相机（通过 listener）
    r1, attached = _cam_sdk.enumerate(include_net=False)
    if not attached:
        return {"ok": False, "msg": "No camera found via listener"}

    # 取第一个相机
    cam_info = attached[0]
    print(f"[HP60C] Found camera: {cam_info}")

    # 创建 handle
    _camera_handle = ctypes.c_void_p()
    attr = sdk.AS_CAM_ATTR_S()
    attr.type = cam_info["type"]
    if attr.type == 0:  # LNX_USB
        attr.attr.usbAttrs.vid = cam_info.get("vid", 0)
        attr.attr.usbAttrs.pid = cam_info.get("pid", 0)
        attr.attr.usbAttrs.serial = cam_info.get("serial", "").encode()[:63]
        attr.attr.usbAttrs.bnum = cam_info.get("bnum", 0)
        attr.attr.usbAttrs.dnum = cam_info.get("dnum", 0)
        attr.attr.usbAttrs.port_number = cam_info.get("port_number", 0)
        attr.attr.usbAttrs.port_numbers = cam_info.get("port_numbers", "").encode()[:63]
    elif attr.type == 1:  # WIN_USB
        attr.attr.winAttrs.vid = cam_info.get("vid", 0)
        attr.attr.winAttrs.pid = cam_info.get("pid", 0)
        attr.attr.winAttrs.deviceID = cam_info.get("deviceID", 0)
        attr.attr.winAttrs.symbol_link = cam_info.get("symbol_link", "").encode()[:2047]
        attr.attr.winAttrs.location_path = cam_info.get("location_path", "").encode()[:2047]
        attr.attr.winAttrs.serialPort = cam_info.get("serialPort", "").encode()[:2047]
    elif attr.type == 2:  # NET
        attr.attr.netAttrs.ip_addr = cam_info.get("ip_addr", "").encode()[:63]
        attr.attr.netAttrs.port = cam_info.get("port", 0)
        attr.attr.netAttrs.model = cam_info.get("model", 0)

    ret = _cam_sdk.create_cam_handle(ctypes.byref(_camera_handle), ctypes.byref(attr))
    if ret != 0:
        return {"ok": False, "msg": f"create_cam_handle failed: {ret}"}

    # 打开相机
    ret = _cam_sdk.open_camera(_camera_handle, config_path.encode('utf-8'))
    if ret != 0:
        return {"ok": False, "msg": f"open_camera failed: {ret}"}

    # 获取序列号
    sn_buf = ctypes.create_string_buffer(128)
    ret = _cam_sdk.get_serial_number(_camera_handle, sn_buf, 128)
    sn = sn_buf.value.decode() if ret == 0 else "unknown"

    # ===== 完全复刻 audit.py 序列：sec_calibration (warmup) → sec_stream (stream + reboot + new handle) =====
    # audit.py 验证的序列：
    # sec_calibration:
    #   1. register dummy callback
    #   2. warmup start_stream(RGB|DEPTH) 2s
    #   3. stop_stream
    #   4. poll GetCamParameter (触发参数缓存就绪，官方驱动会轮询)
    # sec_stream:
    #   5. set_timestamp_type(STEADY_CLOCK)
    #   6. register real stream callback
    #   7. start_stream(RGB|DEPTH|POINTCLOUD, 0x0B) -> 预期 ret=-86 (MF 后端需重置)
    #   8. CAMERA_Reboot (关键！官方驱动用此重置 MF 管线)
    #   9. close_camera + destroy_cam_handle + create_cam_handle (NEW handle!)
    #  10. open_camera + register_callback + start_stream -> 成功
    
    # audit.py-style warmup is opt-in: on this unit it makes the first
    # start_stream return -86 and sends the worker into a reboot loop. The
    # simplified sequence matches the direct SDK, which streams reliably.
    if _warmup_enabled():
        # 预热流 (RGB|DEPTH) - 让相机管线就绪 (sec_calibration 步骤)
        flags_warmup = sdk.IMG_RGB | sdk.IMG_DEPTH  # 0x05

        @sdk.StreamCb
        def _dummy(cam, pdata, priv):
            pass

        cb_dummy = sdk.AS_CAM_Stream_Cb_s()
        cb_dummy.callback = _dummy
        ret = _cam_sdk.register_stream_callback(_camera_handle, ctypes.byref(cb_dummy))
        if ret != 0:
            return {"ok": False, "msg": f"register_dummy_callback failed: {ret}"}
        print("[HP60C] Warmup dummy callback registered")

        ret = _cam_sdk.start_stream(_camera_handle, flags_warmup)
        if ret != 0:
            return {"ok": False, "msg": f"warmup start_stream failed: {ret}"}
        print("[HP60C] Warmup stream started (RGB|DEPTH)")
        time.sleep(2.0)

        ret = _cam_sdk.stop_stream(_camera_handle, 0)
        if ret != 0:
            print(f"[Warning] warmup stop_stream failed: {ret}")
        print("[HP60C] Warmup stream stopped")

        # 关键步骤：轮询 GetCamParameter (audit.py sec_calibration 做法)
        # 官方驱动验证：GetCamParameter 仅在 start_stream 后就绪，需轮询直到返回 0
        # 这步会让相机内部参数缓存完全就绪，为后续 POINTCLOUD 流做准备
        print("[HP60C] Polling GetCamParameter...")
        for i in range(10):
            param = sdk.AS_CAM_Parameter_s()
            ret = _cam_sdk.get_cam_parameter(_camera_handle, ctypes.byref(param))
            if ret == 0:
                print(f"[HP60C] GetCamParameter ready after {i+1} tries")
                break
            time.sleep(0.5)
        else:
            print("[HP60C] Warning: GetCamParameter not ready, continuing anyway")

        # 设置时间戳类型 (sec_stream 步骤: 必须 STEADY_CLOCK)
        ret = _cam_sdk.set_timestamp_type(_camera_handle, sdk.TS_STEADY_CLOCK)
        if ret != 0:
            print(f"[Warning] set_timestamp_type STEADY_CLOCK failed: {ret}")
        else:
            print("[HP60C] Timestamp type set: STEADY_CLOCK")
    else:
        print("[HP60C] Simplified init: skipping warmup/GetCamParameter/timestamp_type")

    # 注册正式流回调
    stream_cb = sdk.AS_CAM_Stream_Cb_s(_stream_cb_ref, None)
    ret = _cam_sdk.register_stream_callback(_camera_handle, ctypes.byref(stream_cb))
    if ret != 0:
        return {"ok": False, "msg": f"register_stream_callback failed: {ret}"}
    print("[HP60C] Stream callback registered")

    # 尝试注册 merge callback (硬件级 RGB-Depth 对齐)
    merge_cb = sdk.AS_CAM_Merge_Cb_s(_merge_cb_ref, None)
    ret = _cam_sdk.register_merge_callback(_camera_handle, ctypes.byref(merge_cb))
    if ret == 0:
        print("[HP60C] Merge callback registered (hardware alignment enabled)")
    else:
        print(f"[HP60C] Merge callback not supported (ret={ret}), using stream callback only")

    # 第 1 次启动流 (RGB|DEPTH|POINTCLOUD = 0x0B) - 预期 ret=-86 (MF 后端需重置)
    flags = sdk.IMG_RGB | sdk.IMG_DEPTH | sdk.IMG_POINTCLOUD  # 0x0B
    _running = True
    ret = _cam_sdk.start_stream(_camera_handle, flags)
    if ret == 0:
        print("[HP60C] Stream started successfully on first try")
        return {
            "ok": True,
            "msg": "Camera started",
            "serial": sn,
            "width": 640,
            "height": 480,
            "fps": 30,
        }
    
    print(f"[HP60C] Expected first start_stream failed (ret={ret}), performing CAMERA_Reboot...")

    # 关键修复：CAMERA_Reboot 重置 MF 管线 (audit.py 验证此步骤可解决 -86)
    ret = _cam_sdk.camera_reboot(_camera_handle)
    if ret != 0:
        _running = False
        return {"ok": False, "msg": f"camera_reboot failed: {ret}"}
    print("[HP60C] Camera reboot completed, waiting for USB re-enumeration...")
    time.sleep(3.0)  # 等待 USB 重新枚举
    if not _wait_camera_ready_after_reboot(10.0):
        _running = False
        return {"ok": False,
                "msg": "USB re-enumeration timeout after CAMERA_Reboot"}

    # The reset invalidated the old handle; close/destroy on it can fault inside
    # the vendor SDK (access violation), so abandon it and open a fresh handle.
    print("[HP60C] Abandoning pre-reboot handle, creating a new one...")
    _camera_handle = ctypes.c_void_p()
    
    # 重新创建 handle (使用相同的设备属性)
    ret = _cam_sdk.create_cam_handle(ctypes.byref(_camera_handle), ctypes.byref(attr))
    if ret != 0:
        _running = False
        return {"ok": False, "msg": f"create_cam_handle after reboot failed: {ret}"}
    print("[HP60C] New camera handle created after reboot")

    # 重新打开相机
    ret = _cam_sdk.open_camera(_camera_handle, config_path.encode('utf-8'))
    if ret != 0:
        _running = False
        return {"ok": False, "msg": f"re-open_camera after reboot failed: {ret}"}
    print("[HP60C] Camera re-opened after reboot")

    if _warmup_enabled():
        # 重新设置时间戳类型
        ret = _cam_sdk.set_timestamp_type(_camera_handle, sdk.TS_STEADY_CLOCK)
        if ret != 0:
            print(f"[Warning] set_timestamp_type STEADY_CLOCK failed after reboot: {ret}")
        else:
            print("[HP60C] Timestamp type set: STEADY_CLOCK (after reboot)")

    # 重新注册流回调
    stream_cb = sdk.AS_CAM_Stream_Cb_s(_stream_cb_ref, None)
    ret = _cam_sdk.register_stream_callback(_camera_handle, ctypes.byref(stream_cb))
    if ret != 0:
        _running = False
        return {"ok": False, "msg": f"register_stream_callback after reboot failed: {ret}"}
    print("[HP60C] Stream callback registered (after reboot)")

    # 尝试注册 merge callback
    merge_cb = sdk.AS_CAM_Merge_Cb_s(_merge_cb_ref, None)
    ret = _cam_sdk.register_merge_callback(_camera_handle, ctypes.byref(merge_cb))
    if ret == 0:
        print("[HP60C] Merge callback registered (after reboot)")
    else:
        print(f"[HP60C] Merge callback not supported (after reboot, ret={ret})")

    # 第 2 次启动流 - 应该成功
    _running = True
    ret = _cam_sdk.start_stream(_camera_handle, flags)
    if ret != 0:
        _running = False
        return {"ok": False, "msg": f"start_stream after reboot failed: {ret}"}
    
    print("[HP60C] Stream started successfully after CAMERA_Reboot + new handle!")

    return {
        "ok": True,
        "msg": "Camera started",
        "serial": sn,
        "width": 640,
        "height": 480,
        "fps": 30,
    }


def td_get_latest_frame() -> Optional[Dict[str, Any]]:
    """获取最新帧（非阻塞，非消费 — 返回缓存引用）"""
    with _lock:
        frame = _latest_frame
        if _is_frame_fresh(frame):
            return frame
        return None  # 帧过旧，视为断开


def td_wait_first_frame(timeout_s: float = 10.0) -> bool:
    """阻塞等待首帧到达，返回 True 表示成功，False 表示超时"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with _lock:
            if _latest_frame is not None:
                return True
        time.sleep(0.01)
    return False


def td_get_depth() -> Optional[np.ndarray]:
    """获取最新深度帧（非消费）"""
    frame = td_get_latest_frame()
    if frame and "depth" in frame:
        return frame["depth"]
    return None


def td_get_ir() -> Optional[np.ndarray]:
    """获取最新 IR 帧（非消费）"""
    frame = td_get_latest_frame()
    if frame and "ir" in frame:
        return frame["ir"]
    return None


def td_get_rgb() -> Optional[np.ndarray]:
    """获取最新 RGB 帧（非消费）"""
    frame = td_get_latest_frame()
    if frame and "rgb" in frame:
        return frame["rgb"]
    return None


def td_get_pointcloud() -> Optional[np.ndarray]:
    """获取最新点云"""
    frame = td_get_latest_frame()
    if frame and "pointcloud" in frame:
        return frame["pointcloud"]
    return None


def td_get_depth_aligned() -> Optional[np.ndarray]:
    """获取对齐到 RGB 的最新深度帧（非消费）"""
    frame = td_get_latest_frame()
    if frame and "depth_aligned" in frame:
        return frame["depth_aligned"]
    return None


def td_get_stats() -> Dict[str, Any]:
    """获取统计信息"""
    with _lock:
        return {
            "running": _running,
            **_stats,
            "queue_size": _frame_queue.qsize(),
        }


def td_shutdown() -> Dict[str, Any]:
    """关闭相机并清理"""
    global _running, _camera_handle, _cam_sdk

    if not _running and _cam_sdk is None:
        return {"ok": True, "msg": "Already stopped"}

    _running = False

    if _camera_handle and _cam_sdk:
        _cam_sdk.stop_stream(_camera_handle, 0)
        _cam_sdk.close_camera(_camera_handle)
        _cam_sdk.destroy_cam_handle(_camera_handle)
        _camera_handle = None

    if _cam_sdk:
        _cam_sdk.deinit()
        _cam_sdk = None

    while not _frame_queue.empty():
        try:
            _frame_queue.get_nowait()
        except queue.Empty:
            break

    return {"ok": True, "msg": "Shutdown complete"}


# 兼容旧接口
def td_get_depth_info() -> Dict[str, Any]:
    d = td_get_depth()
    if d is not None:
        return {"ok": True, "width": d.shape[1], "height": d.shape[0], "dtype": str(d.dtype)}
    return {"ok": False}

def td_get_rgb_info() -> Dict[str, Any]:
    r = td_get_rgb()
    if r is not None:
        return {"ok": True, "width": r.shape[1], "height": r.shape[0], "channels": r.shape[2], "dtype": str(r.dtype)}
    return {"ok": False}


# ============================================================
# 主入口（用于外部测试）
# ============================================================
if __name__ == "__main__":
    result = td_init()
    print(f"Init: {result}")
    if result.get("ok"):
        print("Streaming for 10 seconds...")
        time.sleep(10)
        stats = td_get_stats()
        print(f"Stats: {stats}")
        td_shutdown()
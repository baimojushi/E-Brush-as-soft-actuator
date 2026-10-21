#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HP60C → TouchDesigner 直接桥接（使用审计项目中的 sdk.py 绑定）

数据路径：
  AngstrongCameraSdk.dll → sdk.py (ctypes) → 回调线程 → 线程安全队列
                        → TD Script TOP (深度/RGB) / Script CHOP (点云)

用法（在 TD 内部通过 mcp__tdmcp__execute_python_script 调用）：
  1. td_init()          - 初始化相机，启动流
  2. td_get_depth()     - 获取最新深度帧 (返回 numpy 数组或 bytes)
  3. td_get_rgb()       - 获取最新 RGB 帧
  4. td_get_pointcloud() - 获取最新点云
  5. td_get_stats()     - 获取统计信息
  6. td_shutdown()      - 关闭相机

依赖：
  - 审计项目的 sdk.py (已在 D:\项目\交互艺术\展厅孪生\ai\hp60c_audit\sdk.py)
  - AngstrongCameraSdk.dll (已在 SDK 目录)
  - 配置文件 hp60c_v2_00_20230704_configEncrypt.json
"""

import sys
import os
import time
import threading
import queue
import ctypes
import numpy as np
from typing import Optional, Tuple, Dict, Any

# 添加审计项目路径以导入 sdk
AUDIT_DIR = r"D:\项目\交互艺术\展厅孪生\ai\hp60c_audit"
sys.path.insert(0, AUDIT_DIR)

import sdk  # 审计项目中的 CameraSdk 类

# ============================================================
# 全局状态
# ============================================================
_cam_sdk: Optional[sdk.CameraSdk] = None
_camera_handle = None
_running = False
_frame_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=2)  # 只保留最新帧
_stats = {
    "frame_count": 0,
    "depth_frames": 0,
    "rgb_frames": 0,
    "pc_frames": 0,
    "last_frame_time": 0.0,
    "errors": 0,
}
_lock = threading.Lock()

# ============================================================
# 回调函数（在 SDK 内部线程中调用）
# ============================================================
def _stream_callback(pCamera, pData, privateData):
    global _stats
    if not _running:
        return
    data = pData.contents
    frame = {}
    ts = time.time()

    # 深度图
    if data.depthImg.data and data.depthImg.size > 0:
        w, h = data.depthImg.width, data.depthImg.height
        # 深度数据是 uint16 (mm)
        buf = np.frombuffer(
            (ctypes.c_ubyte * data.depthImg.size).from_address(data.depthImg.data),
            dtype=np.uint16
        ).copy().reshape(h, w)
        frame["depth"] = buf
        with _lock:
            _stats["depth_frames"] += 1

    # RGB 图
    if data.rgbImg.data and data.rgbImg.size > 0:
        w, h = data.rgbImg.width, data.rgbImg.height
        # RGB 数据通常是 BGR24 或 RGB24，按 size 推断
        channels = data.rgbImg.size // (w * h)
        buf = np.frombuffer(
            (ctypes.c_ubyte * data.rgbImg.size).from_address(data.rgbImg.data),
            dtype=np.uint8
        ).copy().reshape(h, w, channels)
        frame["rgb"] = buf
        with _lock:
            _stats["rgb_frames"] += 1

    # 点云
    if data.pointCloud.data and data.pointCloud.size > 0:
        w, h = data.pointCloud.width, data.pointCloud.height
        # 点云数据是 float (xyz)
        buf = np.frombuffer(
            (ctypes.c_ubyte * data.pointCloud.size).from_address(data.pointCloud.data),
            dtype=np.float32
        ).copy().reshape(-1, 3)  # (N, 3)
        frame["pointcloud"] = buf
        with _lock:
            _stats["pc_frames"] += 1

    frame["timestamp"] = ts
    frame["frame_id"] = data.depthImg.frameId if data.depthImg.data else 0

    # 非阻塞放入队列（只保留最新）
    try:
        _frame_queue.put_nowait(frame)
    except queue.Full:
        try:
            _frame_queue.get_nowait()
        except queue.Empty:
            pass
        _frame_queue.put_nowait(frame)

    with _lock:
        _stats["frame_count"] += 1
        _stats["last_frame_time"] = ts

# 保持回调引用防止 GC
_stream_cb_ref = sdk.StreamCb(_stream_callback)

# ============================================================
# TD 集成入口函数
# ============================================================
def td_init(config_path: str = None) -> Dict[str, Any]:
    """初始化相机并启动流"""
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
    # 需要构造 AS_CAM_ATTR_S
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

    # 获取流参数
    param = sdk.AS_STREAM_Param_s()
    ret = _cam_sdk.get_stream_param(_camera_handle, ctypes.byref(param))
    if ret == 0:
        print(f"[HP60C] Stream: {param.width}x{param.height} @ {param.fps}fps")

    # 注册流回调
    stream_cb = sdk.AS_CAM_Stream_Cb_s(_stream_cb_ref, None)
    ret = _cam_sdk.register_stream_callback(_camera_handle, ctypes.byref(stream_cb))
    if ret != 0:
        return {"ok": False, "msg": f"register_stream_callback failed: {ret}"}

    # 启动流
    ret = _cam_sdk.start_stream(_camera_handle, 0)
    if ret != 0:
        return {"ok": False, "msg": f"start_stream failed: {ret}"}

    _running = True
    return {
        "ok": True,
        "msg": "Camera started",
        "serial": sn,
        "width": param.width,
        "height": param.height,
        "fps": param.fps,
    }


def td_get_latest_frame() -> Optional[Dict[str, Any]]:
    """获取最新帧（非阻塞）"""
    if not _running:
        return None
    try:
        return _frame_queue.get_nowait()
    except queue.Empty:
        return None


def td_get_depth() -> Optional[np.ndarray]:
    """获取最新深度帧 (H, W) uint16"""
    frame = td_get_latest_frame()
    if frame and "depth" in frame:
        return frame["depth"]
    return None


def td_get_rgb() -> Optional[np.ndarray]:
    """获取最新 RGB 帧 (H, W, C) uint8"""
    frame = td_get_latest_frame()
    if frame and "rgb" in frame:
        return frame["rgb"]
    return None


def td_get_pointcloud() -> Optional[np.ndarray]:
    """获取最新点云 (N, 3) float32"""
    frame = td_get_latest_frame()
    if frame and "pointcloud" in frame:
        return frame["pointcloud"]
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

    # 清空队列
    while not _frame_queue.empty():
        try:
            _frame_queue.get_nowait()
        except queue.Empty:
            break

    return {"ok": True, "msg": "Shutdown complete"}


# ============================================================
# 兼容旧接口
# ============================================================
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
    import ctypes
    result = td_init()
    print(f"Init: {result}")
    if result.get("ok"):
        print("Streaming for 10 seconds...")
        time.sleep(10)
        stats = td_get_stats()
        print(f"Stats: {stats}")
        td_shutdown()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HP60C → TouchDesigner 桥接脚本（在 TD 内部通过 execute_python_script 运行）

数据路径：
  AngstrongCameraSdk.dll → Python ctypes 回调 → TD Script TOP (深度/RGB)
                        → TD Script CHOP (点云坐标)

用法（在 TD 内部）：
  通过 mcp__tdmcp__execute_python_script 调用此脚本
  或在 TD 的 Text DAT 中粘贴并执行

依赖：
  - AngstrongCameraSdk.dll (已在 D:\Express Program Files\Angstrong Viewer V2.1.10\...\RelWithDebInfo)
  - 配置文件 hp60c_v2_00_20230704_configEncrypt.json (同目录)
"""

import sys
import os
import time
import ctypes
import threading
from ctypes import (
    c_int, c_uint, c_ulong, c_ulonglong, c_char, c_char_p, c_void_p,
    c_float, c_double, c_bool, POINTER, Structure, CFUNCTYPE,
    byref, create_string_buffer, sizeof, cast, memmove
)

# ============================================================
# 路径配置
# ============================================================
SDK_DIR = r"D:\Express Program Files\Angstrong Viewer V2.1.10_240109\Angstrong Viewer V2.1.10_240109\RelWithDebInfo"
DLL_PATH = os.path.join(SDK_DIR, "AngstrongCameraSdk.dll")
CONFIG_PATH = os.path.join(SDK_DIR, "configurationfiles", "hp60c_v2_00_20230704_configEncrypt.json")

# ============================================================
# SDK 结构体定义（精简版，只保留需要的字段）
# ============================================================
AS_CAM_PTR = c_void_p

class AS_Frame_s(Structure):
    _fields_ = [
        ("type", c_int),        # AS_FRAME_Type_e
        ("width", c_uint),
        ("height", c_uint),
        ("data", c_void_p),
        ("size", c_uint),
        ("bufferSize", c_uint),
        ("frameId", c_uint),
        ("ts", c_ulonglong),
    ]

class AS_SDK_Data_s(Structure):
    _fields_ = [
        ("depthImg", AS_Frame_s),
        ("rgbImg", AS_Frame_s),
        ("irImg", AS_Frame_s),
        ("pointCloud", AS_Frame_s),
        ("yuyvImg", AS_Frame_s),
        ("peakImg", AS_Frame_s),
        ("mjpegImg", AS_Frame_s),
        ("pointCloud2", c_void_p),  # std::vector, 简化
    ]

class AS_SDK_MERGE_s(Structure):
    _fields_ = [
        ("depthImg", AS_Frame_s),
        ("pointCloud", AS_Frame_s),
    ]

AS_CAM_StreamCallback = CFUNCTYPE(None, AS_CAM_PTR, POINTER(AS_SDK_Data_s), c_void_p)
AS_CAM_MergeFrameCallback = CFUNCTYPE(None, AS_CAM_PTR, POINTER(AS_SDK_MERGE_s), c_void_p)

class AS_CAM_Stream_Cb_s(Structure):
    _fields_ = [
        ("callback", AS_CAM_StreamCallback),
        ("privateData", c_void_p),
    ]

class AS_CAM_Merge_Cb_s(Structure):
    _fields_ = [
        ("callback", AS_CAM_MergeFrameCallback),
        ("privateData", c_void_p),
    ]

class AS_STREAM_Param_s(Structure):
    _fields_ = [
        ("width", c_int),
        ("height", c_int),
        ("fps", c_int),
    ]

# ============================================================
# 全局状态
# ============================================================
_sdk = None
_camera = None
_stream_cb_ref = None  # 防止 GC
_merge_cb_ref = None

# 最新帧数据（线程安全：回调写，主线程读）
_latest_depth = None  # (width, height, bytes)
_latest_rgb = None
_latest_pc = None
_lock = threading.Lock()
_frame_count = 0
_running = False

# ============================================================
# 回调函数
# ============================================================
def _stream_callback(pCamera, pData, privateData):
    global _latest_depth, _latest_rgb, _latest_pc, _frame_count
    if not _running:
        return
    data = pData.contents
    _frame_count += 1

    with _lock:
        # 深度图
        if data.depthImg.data and data.depthImg.size > 0:
            w, h = data.depthImg.width, data.depthImg.height
            buf = ctypes.create_string_buffer(data.depthImg.size)
            memmove(buf, data.depthImg.data, data.depthImg.size)
            _latest_depth = (w, h, buf.raw)

        # RGB
        if data.rgbImg.data and data.rgbImg.size > 0:
            w, h = data.rgbImg.width, data.rgbImg.height
            buf = ctypes.create_string_buffer(data.rgbImg.size)
            memmove(buf, data.rgbImg.data, data.rgbImg.size)
            _latest_rgb = (w, h, buf.raw)

        # 点云
        if data.pointCloud.data and data.pointCloud.size > 0:
            w, h = data.pointCloud.width, data.pointCloud.height
            buf = ctypes.create_string_buffer(data.pointCloud.size)
            memmove(buf, data.pointCloud.data, data.pointCloud.size)
            _latest_pc = (w, h, buf.raw)

# ============================================================
# 初始化与清理
# ============================================================
def init_sdk():
    global _sdk, _camera, _stream_cb_ref, _merge_cb_ref, _running
    if _sdk is not None:
        return True, "Already initialized"

    if not os.path.exists(DLL_PATH):
        return False, f"DLL not found: {DLL_PATH}"

    try:
        _sdk = ctypes.CDLL(DLL_PATH)
    except OSError as e:
        return False, f"Failed to load DLL: {e}"

    # 绑定函数签名
    _sdk.AS_SDK_Init.restype = c_int
    _sdk.AS_SDK_Deinit.restype = c_int
    _sdk.AS_SDK_OpenCamera.argtypes = [AS_CAM_PTR, c_char_p]
    _sdk.AS_SDK_OpenCamera.restype = c_int
    _sdk.AS_SDK_CloseCamera.argtypes = [AS_CAM_PTR]
    _sdk.AS_SDK_CloseCamera.restype = c_int
    _sdk.AS_SDK_RegisterStreamCallback.argtypes = [AS_CAM_PTR, POINTER(AS_CAM_Stream_Cb_s)]
    _sdk.AS_SDK_RegisterStreamCallback.restype = c_int
    _sdk.AS_SDK_RegisterMergeFrameCallback.argtypes = [AS_CAM_PTR, POINTER(AS_CAM_Merge_Cb_s)]
    _sdk.AS_SDK_RegisterMergeFrameCallback.restype = c_int
    _sdk.AS_SDK_StartStream.argtypes = [AS_CAM_PTR, c_int]
    _sdk.AS_SDK_StartStream.restype = c_int
    _sdk.AS_SDK_StopStream.argtypes = [AS_CAM_PTR, c_int]
    _sdk.AS_SDK_StopStream.restype = c_int
    _sdk.AS_SDK_GetSerialNumber.argtypes = [AS_CAM_PTR, c_char_p, c_int]
    _sdk.AS_SDK_GetSerialNumber.restype = c_int
    _sdk.AS_SDK_GetStreamParam.argtypes = [AS_CAM_PTR, POINTER(AS_STREAM_Param_s)]
    _sdk.AS_SDK_GetStreamParam.restype = c_int

    # Init
    ret = _sdk.AS_SDK_Init()
    if ret != 0:
        return False, f"AS_SDK_Init failed: {ret}"

    # GetCameraList - 用变通方式
    # SDK 的 GetCameraList 接受 std::list<AS_CAM_PTR>*，Python 无法直接操作 C++ STL
    # 但 SDK 提供了 AS_SDK_CreateCamHandle + AS_SDK_StartListener 方式
    # 这里尝试用 StartListener + CreateCamHandle
    if hasattr(_sdk, 'AS_SDK_StartListener'):
        # 绑定 listener 相关
        ListenerCallback = CFUNCTYPE(None, c_void_p, c_void_p)
        class AS_LISTENER_CALLBACK_S(Structure):
            _fields_ = [
                ("onAttached", ListenerCallback),
                ("onDetached", ListenerCallback),
                ("privateData", c_void_p),
            ]
        _sdk.AS_SDK_StartListener.argtypes = [AS_LISTENER_CALLBACK_S, c_int, c_bool]
        _sdk.AS_SDK_StartListener.restype = c_int

        # 简化：直接用 listener 枚举
        cb = AS_LISTENER_CALLBACK_S(None, None, None)
        ret = _sdk.AS_SDK_StartListener(cb, 0, True)
        if ret != 0:
            return False, f"AS_SDK_StartListener failed: {ret}"

    # CreateCamHandle
    if hasattr(_sdk, 'AS_SDK_CreateCamHandle'):
        _sdk.AS_SDK_CreateCamHandle.argtypes = [POINTER(AS_CAM_PTR), c_void_p]
        _sdk.AS_SDK_CreateCamHandle.restype = c_int
        _camera = AS_CAM_PTR()
        ret = _sdk.AS_SDK_CreateCamHandle(byref(_camera), None)
        if ret != 0:
            return False, f"AS_SDK_CreateCamHandle failed: {ret}"
    else:
        return False, "AS_SDK_CreateCamHandle not available in this SDK version"

    # OpenCamera
    config_bytes = CONFIG_PATH.encode('utf-8')
    ret = _sdk.AS_SDK_OpenCamera(_camera, config_bytes)
    if ret != 0:
        return False, f"AS_SDK_OpenCamera failed: {ret}"

    # Get SN
    sn_buf = create_string_buffer(128)
    ret = _sdk.AS_SDK_GetSerialNumber(_camera, sn_buf, 128)
    sn = sn_buf.value.decode() if ret == 0 else "unknown"

    # Get stream params
    param = AS_STREAM_Param_s()
    ret = _sdk.AS_SDK_GetStreamParam(_camera, byref(param))
    if ret == 0:
        print(f"[HP60C] Stream: {param.width}x{param.height} @ {param.fps}fps")

    # Register callback
    _stream_cb_ref = AS_CAM_StreamCallback(_stream_callback)
    stream_cb = AS_CAM_Stream_Cb_s(_stream_cb_ref, None)
    _sdk.AS_SDK_RegisterStreamCallback(_camera, byref(stream_cb))

    # Start stream
    ret = _sdk.AS_SDK_StartStream(_camera, 0)
    if ret != 0:
        return False, f"AS_SDK_StartStream failed: {ret}"

    _running = True
    return True, f"Camera initialized (SN: {sn})"

def get_latest_depth():
    """返回 (width, height, raw_bytes) 或 None"""
    with _lock:
        return _latest_depth

def get_latest_rgb():
    """返回 (width, height, raw_bytes) 或 None"""
    with _lock:
        return _latest_rgb

def get_latest_pointcloud():
    """返回 (width, height, raw_bytes) 或 None"""
    with _lock:
        return _latest_pc

def get_frame_count():
    return _frame_count

def shutdown_sdk():
    global _running, _camera, _sdk
    _running = False
    if _camera and _sdk:
        _sdk.AS_SDK_StopStream(_camera, 0)
        _sdk.AS_SDK_CloseCamera(_camera)
        _camera = None
    if _sdk:
        _sdk.AS_SDK_Deinit()
        _sdk = None

# ============================================================
# TD 集成入口
# ============================================================
def td_init():
    """在 TD 内调用：初始化相机"""
    ok, msg = init_sdk()
    return {"ok": ok, "msg": msg}

def td_get_depth_info():
    """在 TD 内调用：获取最新深度帧的尺寸信息"""
    d = get_latest_depth()
    if d:
        return {"ok": True, "width": d[0], "height": d[1], "size": len(d[2])}
    return {"ok": False}

def td_get_rgb_info():
    """在 TD 内调用：获取最新 RGB 帧的尺寸信息"""
    r = get_latest_rgb()
    if r:
        return {"ok": True, "width": r[0], "height": r[1], "size": len(r[2])}
    return {"ok": False}

def td_get_stats():
    """在 TD 内调用：获取统计信息"""
    return {
        "running": _running,
        "frame_count": _frame_count,
        "has_depth": _latest_depth is not None,
        "has_rgb": _latest_rgb is not None,
        "has_pc": _latest_pc is not None,
    }

def td_shutdown():
    """在 TD 内调用：关闭相机"""
    shutdown_sdk()
    return {"ok": True, "msg": "Shutdown complete"}

# ============================================================
# 主入口（用于外部测试）
# ============================================================
if __name__ == "__main__":
    ok, msg = init_sdk()
    print(f"Init: {ok}, {msg}")
    if ok:
        print("Streaming for 10 seconds...")
        time.sleep(10)
        stats = td_get_stats()
        print(f"Stats: {stats}")
        shutdown_sdk()

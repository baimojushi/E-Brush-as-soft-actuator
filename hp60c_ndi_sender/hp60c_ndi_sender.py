#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HP60C → NDI Sender (Python + ctypes)
依赖：
  - AngstrongCameraSdk.dll (已在 D:\Express Program Files\Angstrong Viewer V2.1.10_240109\...\RelWithDebInfo)
  - Processing.NDI.Lib.x64.dll (需安装 NDI 5 SDK: https://ndi.video/tools/)
用法：
  python hp60c_ndi_sender.py --name HP60C --fps 30
"""

import sys
import os
import time
import signal
import argparse
import ctypes
from ctypes import (
    c_int, c_uint, c_ulong, c_ulonglong, c_char, c_char_p, c_void_p,
    c_float, c_double, c_bool, c_size_t, POINTER, Structure, CFUNCTYPE,
    byref, create_string_buffer, sizeof, addressof
)
from typing import Optional, Callable, List, Tuple

# ============================================================
# 路径配置（根据实际情况修改）
# ============================================================
ANGSTRONG_SDK_DIR = r"D:\Express Program Files\Angstrong Viewer V2.1.10_240109\Angstrong Viewer V2.1.10_240109\RelWithDebInfo"
NDI_SDK_DIR = r"C:\Program Files\NDI\NDI 5 SDK"  # 默认安装路径

ANGSTRONG_DLL = os.path.join(ANGSTRONG_SDK_DIR, "AngstrongCameraSdk.dll")
NDI_DLL = os.path.join(NDI_SDK_DIR, "bin", "x64", "Processing.NDI.Lib.x64.dll")

CONFIG_FILE = os.path.join(ANGSTRONG_SDK_DIR, "configurationfiles", "hp60c_v2_00_20230704_configEncrypt.json")

# ============================================================
# Angstrong SDK 结构体与常量定义
# ============================================================
AS_CAM_PTR = c_void_p

# 帧类型
class AS_FRAME_Type_e(c_int):
    AS_FRAME_TYPE_DEPTH = 0
    AS_FRAME_TYPE_RGB = 1
    AS_FRAME_TYPE_IR = 2
    AS_FRMAE_TYPE_POINTCLOUD = 3
    AS_FRAME_TYPE_YUYV = 4
    AS_FRAME_TYPE_PEAK = 5
    AS_FRAME_TYPE_MJPEG = 6
    AS_FRAME_TYPE_BUTT = 7

class AS_Frame_s(Structure):
    _fields_ = [
        ("type", AS_FRAME_Type_e),
        ("width", c_uint),
        ("height", c_uint),
        ("data", c_void_p),
        ("size", c_uint),
        ("bufferSize", c_uint),
        ("frameId", c_uint),
        ("ts", c_ulonglong),
    ]

class AS_POINTCLOUD_s(Structure):
    _fields_ = [
        ("angle", c_double),
        ("distance", c_double),
        ("quality", c_double),
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
        ("pointCloud2", c_void_p),  # std::vector<AS_POINTCLOUD_s> - 简化处理
    ]

class AS_SDK_MERGE_s(Structure):
    _fields_ = [
        ("depthImg", AS_Frame_s),
        ("pointCloud", AS_Frame_s),
    ]

# 回调函数类型
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

# 相机参数
class AS_CAM_Parameter_s(Structure):
    _fields_ = [
        ("fxir", c_float), ("fyir", c_float), ("cxir", c_float), ("cyir", c_float),
        ("fxrgb", c_float), ("fyrgb", c_float), ("cxrgb", c_float), ("cyrgb", c_float),
        ("R00", c_float), ("R01", c_float), ("R02", c_float),
        ("R10", c_float), ("R11", c_float), ("R12", c_float),
        ("R20", c_float), ("R21", c_float), ("R22", c_float),
        ("T1", c_float), ("T2", c_float), ("T3", c_float),
        ("K1ir", c_float), ("K2ir", c_float), ("K3ir", c_float), ("P1ir", c_float), ("P2ir", c_float),
        ("K1rgb", c_float), ("K2rgb", c_float), ("K3rgb", c_float), ("P1rgb", c_float), ("P2rgb", c_float),
    ]

class AS_STREAM_Param_s(Structure):
    _fields_ = [
        ("width", c_int),
        ("height", c_int),
        ("fps", c_int),
    ]

# 相机型号枚举
class AS_SDK_CAM_MODEL_E(c_int):
    AS_SDK_CAM_MODEL_UNKNOWN = 0
    AS_SDK_CAM_MODEL_NUWA_XB40 = 1
    AS_SDK_CAM_MODEL_NUWA_X100 = 2
    AS_SDK_CAM_MODEL_NUWA_HP60 = 3
    AS_SDK_CAM_MODEL_NUWA_HP60V = 4
    AS_SDK_CAM_MODEL_KUNLUN_A = 5
    AS_SDK_CAM_MODEL_KUNLUN_C = 6
    AS_SDK_CAM_MODEL_KONDYOR = 7
    AS_SDK_CAM_MODEL_KONDYOR_NET = 8
    AS_SDK_CAM_MODEL_HP60C = 9
    AS_SDK_CAM_MODEL_HP60CN = 10
    # ... 其他型号省略

# ============================================================
# NDI SDK 结构体与常量定义
# ============================================================
NDIlib_FourCC_type_BGRX = 0x58524742  # 'BGRX'
NDIlib_frame_format_type_progressive = 0

class NDIlib_video_frame_v2_t(Structure):
    _fields_ = [
        ("xres", c_int),
        ("yres", c_int),
        ("FourCC", c_int),
        ("frame_rate_N", c_int),
        ("frame_rate_D", c_int),
        ("picture_aspect_ratio", c_float),
        ("frame_format_type", c_int),
        ("timecode", c_ulonglong),
        ("p_data", c_void_p),
        ("line_stride_in_bytes", c_int),
        ("p_metadata", c_void_p),
        ("timestamp", c_ulonglong),
    ]

class NDIlib_send_create_t(Structure):
    _fields_ = [
        ("p_ndi_name", c_char_p),
        ("p_groups", c_char_p),
        ("clock_video", c_bool),
        ("clock_audio", c_bool),
    ]

class NDIlib_metadata_frame_t(Structure):
    _fields_ = [
        ("length", c_int),
        ("timecode", c_ulonglong),
        ("p_data", c_char_p),
    ]

# ============================================================
# 动态库加载
# ============================================================
def load_dll(path: str, name: str):
    if not os.path.exists(path):
        raise FileNotFoundError(f"{name} not found at: {path}")
    try:
        return ctypes.CDLL(path)
    except OSError as e:
        raise RuntimeError(f"Failed to load {name}: {e}")

print(f"[Init] Loading Angstrong SDK from: {ANGSTRONG_DLL}")
angstrong = load_dll(ANGSTRONG_DLL, "AngstrongCameraSdk")

print(f"[Init] Loading NDI SDK from: {NDI_DLL}")
ndi = load_dll(NDI_DLL, "Processing.NDI.Lib.x64")

# ============================================================
# Angstrong SDK 函数签名绑定
# ============================================================
angstrong.AS_SDK_Init.restype = c_int
angstrong.AS_SDK_Deinit.restype = c_int

angstrong.AS_SDK_GetCameraList.argtypes = [POINTER(c_void_p)]  # std::list<AS_CAM_PTR>*
angstrong.AS_SDK_GetCameraList.restype = c_int

angstrong.AS_SDK_FreeCameraList.argtypes = [POINTER(c_void_p)]
angstrong.AS_SDK_FreeCameraList.restype = c_int

angstrong.AS_SDK_OpenCamera.argtypes = [AS_CAM_PTR, c_char_p]
angstrong.AS_SDK_OpenCamera.restype = c_int

angstrong.AS_SDK_CloseCamera.argtypes = [AS_CAM_PTR]
angstrong.AS_SDK_CloseCamera.restype = c_int

angstrong.AS_SDK_RegisterStreamCallback.argtypes = [AS_CAM_PTR, POINTER(AS_CAM_Stream_Cb_s)]
angstrong.AS_SDK_RegisterStreamCallback.restype = c_int

angstrong.AS_SDK_RegisterMergeFrameCallback.argtypes = [AS_CAM_PTR, POINTER(AS_CAM_Merge_Cb_s)]
angstrong.AS_SDK_RegisterMergeFrameCallback.restype = c_int

angstrong.AS_SDK_StartStream.argtypes = [AS_CAM_PTR, c_int]
angstrong.AS_SDK_StartStream.restype = c_int

angstrong.AS_SDK_StopStream.argtypes = [AS_CAM_PTR, c_int]
angstrong.AS_SDK_StopStream.restype = c_int

angstrong.AS_SDK_GetSerialNumber.argtypes = [AS_CAM_PTR, c_char_p, c_int]
angstrong.AS_SDK_GetSerialNumber.restype = c_int

angstrong.AS_SDK_GetCameraModel.argtypes = [AS_CAM_PTR, POINTER(AS_SDK_CAM_MODEL_E)]
angstrong.AS_SDK_GetCameraModel.restype = c_int

angstrong.AS_SDK_GetStreamParam.argtypes = [AS_CAM_PTR, POINTER(AS_STREAM_Param_s)]
angstrong.AS_SDK_GetStreamParam.restype = c_int

# ============================================================
# NDI SDK 函数签名绑定
# ============================================================
ndi.NDIlib_initialize.restype = c_bool
ndi.NDIlib_destroy.restype = None

ndi.NDIlib_send_create.argtypes = [POINTER(NDIlib_send_create_t)]
ndi.NDIlib_send_create.restype = c_void_p

ndi.NDIlib_send_destroy.argtypes = [c_void_p]
ndi.NDIlib_send_destroy.restype = None

ndi.NDIlib_send_send_video_v2.argtypes = [c_void_p, POINTER(NDIlib_video_frame_v2_t)]
ndi.NDIlib_send_send_video_v2.restype = None

ndi.NDIlib_send_send_metadata.argtypes = [c_void_p, POINTER(NDIlib_metadata_frame_t)]
ndi.NDIlib_send_send_metadata.restype = None

# ============================================================
# 全局状态
# ============================================================
running = True
frame_count = 0
last_stats_time = time.time()

# 存储回调引用，防止被 GC
_stream_cb_ref = None
_merge_cb_ref = None

# ============================================================
# 回调函数
# ============================================================
def stream_callback(pCamera: AS_CAM_PTR, pData: POINTER(AS_SDK_Data_s), privateData: c_void_p):
    global frame_count, last_stats_time
    if not running:
        return
    data = pData.contents
    frame_count += 1

    # 这里只做简单的统计和转发给 NDI
    # 实际项目中可将数据放入队列，由主线程发送 NDI
    # 为演示，直接在回调里发送（注意：回调可能在 SDK 内部线程，NDI 发送需线程安全）
    # 这里简化：仅打印统计
    now = time.time()
    if now - last_stats_time >= 5.0:
        print(f"[Stats] Frames: {frame_count} | Depth: {'Y' if data.depthImg.data else 'N'} "
              f"RGB: {'Y' if data.rgbImg.data else 'N'} PC: {'Y' if data.pointCloud.data else 'N'}")
        last_stats_time = now

def merge_callback(pCamera: AS_CAM_PTR, pData: POINTER(AS_SDK_MERGE_s), privateData: c_void_p):
    # 合并帧回调，可选处理
    pass

# ============================================================
# 主逻辑
# ============================================================
def signal_handler(sig, frame):
    global running
    print("\n[Signal] Shutting down...")
    running = False

def main():
    global running, _stream_cb_ref, _merge_cb_ref

    parser = argparse.ArgumentParser(description="HP60C → NDI Sender (Python)")
    parser.add_argument("--name", default="HP60C", help="NDI source name")
    parser.add_argument("--fps", type=int, default=30, help="Target FPS")
    parser.add_argument("--config", default=CONFIG_FILE, help="Camera config file path")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("=== HP60C NDI Sender (Python) ===")
    print(f"Config: {args.config}")
    print(f"NDI Name: {args.name}")
    print(f"Target FPS: {args.fps}")

    # 1. 初始化 SDK
    ret = angstrong.AS_SDK_Init()
    if ret != 0:
        print(f"[Error] AS_SDK_Init failed: {ret}")
        return 1

    # 2. 枚举相机
    # 注意：AS_SDK_GetCameraList 接受 std::list<AS_CAM_PTR>*，这里简化用 c_void_p 数组模拟
    # 实际需要更复杂的绑定，这里演示用单相机模式
    # 我们直接创建一个 camera handle（SDK 内部会分配）
    # 但 SDK API 要求先 GetCameraList 再 OpenCamera
    # 这里用一个变通：创建一个空 list 指针传入
    cam_list_ptr = c_void_p()  # 实际上应该是 std::list<AS_CAM_PTR>*
    ret = angstrong.AS_SDK_GetCameraList(byref(cam_list_ptr))
    if ret != 0:
        print(f"[Error] AS_SDK_GetCameraList failed: {ret}")
        angstrong.AS_SDK_Deinit()
        return 1

    # 从 list 中取第一个 camera（简化：假设 list 里有数据）
    # 实际需要遍历 std::list，这里用 ctypes 很难直接操作 C++ STL 容器
    # 变通：直接用 AS_SDK_CreateCamHandle 创建 handle（如果 SDK 支持）
    # 但 SDK 头文件里有 AS_SDK_CreateCamHandle，我们可以尝试
    # 先检查是否有该函数
    if hasattr(angstrong, 'AS_SDK_CreateCamHandle'):
        angstrong.AS_SDK_CreateCamHandle.argtypes = [POINTER(AS_CAM_PTR), c_void_p]
        angstrong.AS_SDK_CreateCamHandle.restype = c_int
        camera = AS_CAM_PTR()
        ret = angstrong.AS_SDK_CreateCamHandle(byref(camera), None)
        if ret != 0:
            print(f"[Error] AS_SDK_CreateCamHandle failed: {ret}")
            angstrong.AS_SDK_FreeCameraList(byref(cam_list_ptr))
            angstrong.AS_SDK_Deinit()
            return 1
        print(f"[OK] Created camera handle via AS_SDK_CreateCamHandle")
    else:
        # 回退：从 list 取第一个（极其简化，实际不可行）
        print("[Warning] AS_SDK_CreateCamHandle not found, camera enumeration not fully implemented in Python binding")
        print("[Info] This Python version is a prototype. For production, use the C++ version.")
        angstrong.AS_SDK_FreeCameraList(byref(cam_list_ptr))
        angstrong.AS_SDK_Deinit()
        return 1

    # 3. 打开相机
    config_path = args.config.encode('utf-8')
    ret = angstrong.AS_SDK_OpenCamera(camera, config_path)
    if ret != 0:
        print(f"[Error] AS_SDK_OpenCamera failed: {ret}")
        angstrong.AS_SDK_CloseCamera(camera)
        angstrong.AS_SDK_FreeCameraList(byref(cam_list_ptr))
        angstrong.AS_SDK_Deinit()
        return 1
    print("[OK] Camera opened")

    # 4. 获取序列号
    sn_buf = create_string_buffer(128)
    ret = angstrong.AS_SDK_GetSerialNumber(camera, sn_buf, 128)
    if ret == 0:
        print(f"[OK] Camera SN: {sn_buf.value.decode()}")

    # 5. 获取型号
    model = AS_SDK_CAM_MODEL_E()
    ret = angstrong.AS_SDK_GetCameraModel(camera, byref(model))
    if ret == 0:
        print(f"[OK] Camera model: {model.value}")

    # 6. 注册流回调
    _stream_cb_ref = AS_CAM_StreamCallback(stream_callback)
    stream_cb = AS_CAM_Stream_Cb_s(_stream_cb_ref, None)
    ret = angstrong.AS_SDK_RegisterStreamCallback(camera, byref(stream_cb))
    if ret != 0:
        print(f"[Error] RegisterStreamCallback failed: {ret}")

    # 7. 注册合并帧回调（可选）
    _merge_cb_ref = AS_CAM_MergeFrameCallback(merge_callback)
    merge_cb = AS_CAM_Merge_Cb_s(_merge_cb_ref, None)
    angstrong.AS_SDK_RegisterMergeFrameCallback(camera, byref(merge_cb))

    # 8. 初始化 NDI
    if not ndi.NDIlib_initialize():
        print("[Error] NDIlib_initialize failed")
        angstrong.AS_SDK_CloseCamera(camera)
        angstrong.AS_SDK_FreeCameraList(byref(cam_list_ptr))
        angstrong.AS_SDK_Deinit()
        return 1
    print("[OK] NDI initialized")

    send_desc = NDIlib_send_create_t()
    send_desc.p_ndi_name = args.name.encode('utf-8')
    send_desc.p_groups = None
    send_desc.clock_video = True
    send_desc.clock_audio = False

    ndi_sender = ndi.NDIlib_send_create(byref(send_desc))
    if not ndi_sender:
        print("[Error] NDIlib_send_create failed")
        ndi.NDIlib_destroy()
        angstrong.AS_SDK_CloseCamera(camera)
        angstrong.AS_SDK_FreeCameraList(byref(cam_list_ptr))
        angstrong.AS_SDK_Deinit()
        return 1
    print(f"[OK] NDI sender created: {args.name}")

    # 9. 启动流
    ret = angstrong.AS_SDK_StartStream(camera, 0)
    if ret != 0:
        print(f"[Error] AS_SDK_StartStream failed: {ret}")
        ndi.NDIlib_send_destroy(ndi_sender)
        ndi.NDIlib_destroy()
        angstrong.AS_SDK_CloseCamera(camera)
        angstrong.AS_SDK_FreeCameraList(byref(cam_list_ptr))
        angstrong.AS_SDK_Deinit()
        return 1
    print("[OK] Stream started")

    print("Streaming... Press Ctrl+C to stop.")

    # 10. 主循环（仅保持运行，实际数据在回调中处理）
    try:
        while running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass

    # 11. 清理
    print("\n[Cleanup] Stopping...")
    running = False
    angstrong.AS_SDK_StopStream(camera, 0)
    ndi.NDIlib_send_destroy(ndi_sender)
    ndi.NDIlib_destroy()
    angstrong.AS_SDK_CloseCamera(camera)
    angstrong.AS_SDK_FreeCameraList(byref(cam_list_ptr))
    angstrong.AS_SDK_Deinit()
    print("[OK] Cleanup done")
    return 0

if __name__ == "__main__":
    sys.exit(main())
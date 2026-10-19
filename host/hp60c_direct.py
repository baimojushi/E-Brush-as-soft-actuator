from __future__ import annotations
import ctypes, sys, threading, time, queue
from pathlib import Path
import numpy as np

class HP60CDirectSDK:
    """Own HP60C directly in this Python process. No TD/NDI/SHM required."""
    def __init__(self, sdk_dir, config_path=None):
        self.sdk_dir = Path(sdk_dir).resolve()
        self.config_path = str(config_path or self.sdk_dir / "configurationfiles" / "hp60c_v2_00_20230704_configEncrypt.json")
        self.sdk = self.cam = self.handle = None
        self.latest = None
        self.lock = threading.Lock()
        self.running = False
        self.error = ""
        self.serial = ""
        self.count = 0
        self._cb = self._dummy = None
        # Vendor callback must never block behind inference or disk I/O.
        # A bounded queue preserves each SDK sample until the recorder receives it.
        # Overflow is an explicit data-integrity failure, not silent dropping.
        self.frames = queue.Queue(maxsize=12)
        self.frame_queue_drops = 0
        self.capture_integrity_broken = False
        self.last_frame_id = None

    def get_frame(self, timeout: float = 0.2):
        """Get the next complete SDK packet in acquisition order."""
        return self.frames.get(timeout=timeout)

    def start(self):
        if sys.platform != "win32":
            raise RuntimeError("HP60C vendor SDK requires Windows x64")

        # 关键：先设置 DLL 搜索路径，再导入 sdk
        import os
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(self.sdk_dir))
        os.environ["PATH"] = str(self.sdk_dir) + ";" + os.environ.get("PATH", "")

        sys.path.insert(0, str(self.sdk_dir))
        import sdk
        self.sdk = sdk
        self.cam = sdk.CameraSdk()
        r = self.cam.init()
        if r != 0:
            raise RuntimeError(f"SDK init failed {r}")
        _, cams = self.cam.enumerate(include_net=False)
        if not cams:
            raise RuntimeError("No HP60C found")
        info = cams[0]
        attr = sdk.AS_CAM_ATTR_S()
        attr.type = info["type"]
        if attr.type == 0:
            a = attr.attr.usbAttrs
            a.vid = info.get("vid", 0)
            a.pid = info.get("pid", 0)
            a.serial = info.get("serial", "").encode()[:63]
            a.bnum = info.get("bnum", 0)
            a.dnum = info.get("dnum", 0)
            a.port_number = info.get("port_number", 0)
            a.port_numbers = info.get("port_numbers", "").encode()[:63]
        elif attr.type == 1:
            a = attr.attr.winAttrs
            a.vid = info.get("vid", 0)
            a.pid = info.get("pid", 0)
            a.deviceID = info.get("deviceID", 0)
            a.symbol_link = info.get("symbol_link", "").encode()[:2047]
            a.location_path = info.get("location_path", "").encode()[:2047]
            a.serialPort = info.get("serialPort", "").encode()[:2047]
        else:
            a = attr.attr.netAttrs
            a.ip_addr = info.get("ip_addr", "").encode()[:63]
            a.port = info.get("port", 0)
            a.model = info.get("model", 0)
        self.handle = ctypes.c_void_p()
        r = self.cam.create_cam_handle(ctypes.byref(self.handle), ctypes.byref(attr))
        if r:
            raise RuntimeError(f"create_cam_handle failed {r}")

        # open_camera 可能返回 -82 (residual state)，等待 SDK 内部清理后重试
        r = self.cam.open_camera(self.handle, self.config_path.encode("utf-8"))
        if r == -82:
            print("open_camera returned -82, waiting for SDK cleanup...", flush=True)
            # 不销毁句柄，只等待 SDK 内部状态清理
            time.sleep(3.0)
            # 直接用同一个 handle 重试 open_camera
            r = self.cam.open_camera(self.handle, self.config_path.encode("utf-8"))
            if r == -82:
                print("Second attempt also -82, doing full cleanup...", flush=True)
                try:
                    self.cam.destroy_cam_handle(self.handle)
                except:
                    pass
                try:
                    self.cam.deinit()
                except:
                    pass
                time.sleep(3.0)
                # 重新创建
                self.cam = sdk.CameraSdk()
                r = self.cam.init()
                if r != 0:
                    raise RuntimeError(f"SDK init retry failed {r}")
                _, cams = self.cam.enumerate(include_net=False)
                info = cams[0]
                attr = sdk.AS_CAM_ATTR_S()
                attr.type = info["type"]
                if attr.type == 1:
                    a = attr.attr.winAttrs
                    a.vid = info.get("vid", 0)
                    a.pid = info.get("pid", 0)
                    a.deviceID = info.get("deviceID", 0)
                    a.symbol_link = info.get("symbol_link", "").encode()[:2047]
                    a.location_path = info.get("location_path", "").encode()[:2047]
                    a.serialPort = info.get("serialPort", "").encode()[:2047]
                self.handle = ctypes.c_void_p()
                r = self.cam.create_cam_handle(ctypes.byref(self.handle), ctypes.byref(attr))
                if r:
                    raise RuntimeError(f"create_cam_handle retry failed {r}")
                r = self.cam.open_camera(self.handle, self.config_path.encode("utf-8"))
        if r:
            raise RuntimeError(f"open_camera failed {r}")
        sn = ctypes.create_string_buffer(128)
        if self.cam.get_serial_number(self.handle, sn, 128) == 0:
            self.serial = sn.value.decode(errors="replace")

        @sdk.StreamCb
        def dummy(c, d, p):
            pass
        self._dummy = dummy
        cb = sdk.AS_CAM_Stream_Cb_s(dummy, None)
        r = self.cam.register_stream_callback(self.handle, ctypes.byref(cb))
        if r:
            raise RuntimeError(f"dummy callback failed {r}")
        r = self.cam.start_stream(self.handle, sdk.IMG_RGB | sdk.IMG_DEPTH)
        if r:
            raise RuntimeError(f"warmup failed {r}")
        time.sleep(2)
        self.cam.stop_stream(self.handle, 0)

        sp = sdk.AS_STREAM_Param_s()
        sp.width = 640
        sp.height = 480
        sp.fps = 30
        r = self.cam.set_stream_param(self.handle, sdk.MEDIA_TYPE_DEPTH, ctypes.byref(sp))
        if r:
            raise RuntimeError(f"640x480@30 rejected {r}")
        self.cam.set_timestamp_type(self.handle, sdk.TS_STEADY_CLOCK)

        @sdk.StreamCb
        def callback(c, pdata, priv):
            if not self.running or not pdata:
                return
            try:
                d = pdata.contents
                f = {"host_ns": time.perf_counter_ns(), "source": "direct_sdk"}

                def copy_bytes(fr):
                    n = int(fr.size)
                    return np.ctypeslib.as_array((ctypes.c_uint8 * n).from_address(fr.data)).copy()

                if d.depthImg.data and d.depthImg.size:
                    w, h = int(d.depthImg.width), int(d.depthImg.height)
                    depth_bytes = copy_bytes(d.depthImg)
                    if len(depth_bytes) != w * h * 2:
                        raise ValueError("Unexpected HP60C depth payload size")
                    f["depth_mm"] = depth_bytes.view(np.uint16).reshape(h, w)
                    f["depth_frame_id"] = int(d.depthImg.frameId)
                    f["depth_camera_ts"] = int(d.depthImg.ts)
                    f["packet_id"] = f["depth_frame_id"]
                if d.rgbImg.data and d.rgbImg.size:
                    w, h = int(d.rgbImg.width), int(d.rgbImg.height)
                    b = copy_bytes(d.rgbImg)
                    ch = len(b) // (w * h)
                    if ch != 3 or len(b) != w * h * 3:
                        raise ValueError("Unexpected HP60C RGB buffer channel count")
                    import cv2
                    img = cv2.cvtColor(b.reshape(h, w, 3), cv2.COLOR_BGR2RGB)
                    f["rgb"] = img
                    f["rgb_frame_id"] = int(d.rgbImg.frameId)
                    f.setdefault("packet_id", f["rgb_frame_id"])
                    f["rgb_camera_ts"] = int(d.rgbImg.ts)
                elif d.mjpegImg.data and d.mjpegImg.size:
                    try:
                        import cv2
                        img = cv2.imdecode(copy_bytes(d.mjpegImg), cv2.IMREAD_COLOR)
                        if img is not None:
                            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                            f["rgb"] = img
                            f["rgb_frame_id"] = int(d.mjpegImg.frameId)
                            f["rgb_camera_ts"] = int(d.mjpegImg.ts)
                            f.setdefault("packet_id", f["rgb_frame_id"])
                    except Exception as decode_exc:
                        f["rgb_decode_error"] = str(decode_exc)
                if d.irImg.data and d.irImg.size:
                    w, h = int(d.irImg.width), int(d.irImg.height)
                    f["ir"] = copy_bytes(d.irImg).view(np.uint16).reshape(h, w)
                if d.pointCloud.data and d.pointCloud.size and int(d.pointCloud.size) % 12 == 0:
                    f["pointcloud"] = copy_bytes(d.pointCloud).view(np.float32).reshape(-1, 3)
                if "rgb" in f and "depth_mm" in f:
                    if self.last_frame_id is not None and f["packet_id"] <= self.last_frame_id:
                        self.capture_integrity_broken = True
                        self.error = "nonmonotonic HP60C frame ID"
                    self.last_frame_id = f["packet_id"]
                    with self.lock:
                        self.latest = f
                        self.count += 1
                    try:
                        self.frames.put_nowait(f)
                    except queue.Full:
                        self.frame_queue_drops += 1
                        self.capture_integrity_broken = True
                        self.error = "HP60C acquisition queue overflow; episode incomplete"
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
                self.capture_integrity_broken = True
        self._cb = callback
        cb = sdk.AS_CAM_Stream_Cb_s(callback, None)
        r = self.cam.register_stream_callback(self.handle, ctypes.byref(cb))
        if r:
            raise RuntimeError(f"real callback failed {r}")
        self.running = True
        r = self.cam.start_stream(self.handle, sdk.IMG_RGB | sdk.IMG_DEPTH | sdk.IMG_POINTCLOUD)
        if r:
            self.running = False
            raise RuntimeError(f"stream start failed {r}")

    def poll(self):
        with self.lock:
            return self.latest

    def status(self):
        f = self.poll()
        age = None if not f else (time.perf_counter_ns() - f["host_ns"]) / 1e6
        return {
            "state": "RUNNING" if self.running and f else ("STARTING" if self.running else "OFFLINE"),
            "serial": self.serial,
            "frame_count": self.count,
            "frame_age_ms": age,
            "reason": self.error,
            "frame_queue_size": self.frames.qsize(),
            "frame_queue_drops": self.frame_queue_drops,
            "capture_integrity_broken": self.capture_integrity_broken
        }

    def close(self):
        self.running = False
        try:
            if self.cam and self.handle:
                self.cam.stop_stream(self.handle, 0)
            if self.cam and self.handle:
                self.cam.close_camera(self.handle)
            if self.cam and self.handle:
                self.cam.destroy_cam_handle(self.handle)
            if self.cam:
                self.cam.deinit()
        except Exception as e:
            self.error = str(e)
        self.handle = self.cam = None
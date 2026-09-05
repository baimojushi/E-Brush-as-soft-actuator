from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

# 将项目 host 目录加入 TouchDesigner Python 模块路径。
_THIS = Path(__file__).resolve()
_HOST = _THIS.parent.parent / "host"
if str(_HOST) not in sys.path:
    sys.path.insert(0, str(_HOST))

from brush_serial import BrushSerial
from alignment import TemporalAligner


class BrushTD:
    def __init__(self, port: str, baud: int = 921600, raw_log_path: Optional[str] = None, max_align_dt_ms: float = 25.0):
        self.serial = BrushSerial(port=port, baud=baud, raw_log_path=raw_log_path)
        self.aligner = TemporalAligner(max_dt_ms=max_align_dt_ms)
        self.latest_sensor = None
        self.latest_aligned = None

    def start(self):
        self.serial.start()
        return self

    def close(self):
        self.serial.close()

    def poll(self):
        for frame in self.serial.drain():
            self.latest_sensor = frame
            self.aligner.push_sensor(frame)
        return self.latest_sensor

    def on_depth_frame(self, frame_id: int, device_ts_us: Optional[float] = None, host_rx_ns: Optional[int] = None):
        # 在深度相机“新帧到达”的回调内调用，host_rx_ns 缺省为 perf_counter_ns。
        self.poll()
        self.latest_aligned = self.aligner.register_and_match(
            frame_id=frame_id,
            device_ts_us=device_ts_us,
            host_rx_ns=host_rx_ns,
        )
        return self.latest_aligned

    def channels(self, aligned: bool = True):
        d = self.latest_aligned if aligned and self.latest_aligned else self.latest_sensor
        if not d:
            return {}
        names = [
            "bx_mT", "by_mT", "bz_mT",
            "hall_temp_C",
            "ax_m_s2", "ay_m_s2", "az_m_s2",
            "gx_rad_s", "gy_rad_s", "gz_rad_s",
            "imu_temp_C",
            "q_w", "q_x", "q_y", "q_z",
            "sync_rtt_ms",
        ]
        out = {k: float(d.get(k, 0.0)) for k in names}
        out["frame_id"] = float(d.get("frame_id", 0))
        out["drop_flags"] = float(d.get("drop_flags", 0))
        out["device_status"] = float(d.get("device_status", 0))
        out["align_dt_ms"] = float(d.get("align_dt_ms", 0.0))
        out["dcw_frame_id"] = float(d.get("dcw_frame_id", -1))
        return out

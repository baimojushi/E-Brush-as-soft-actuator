from __future__ import annotations

import collections
import json
import math
import time
from dataclasses import dataclass
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

from brush_serial import OneWayClockFit


@dataclass
class DepthFrame:
    frame_id: int
    host_ns: int
    device_ts_us: Optional[float] = None


class TemporalAligner:
    """
    TouchDesigner/深度相机时间对齐：
    1) MCU 通过 SYNC_REQ/SYNC_RESP 映射到 host perf_counter_ns。
    2) 相机若提供 device timestamp，用 OneWayClockFit 映射到 host 时间。
    3) 在同一 host 时间轴上按最近邻配对，保留 dt_ms 与质量门。
    """

    def __init__(self, max_sensor_frames: int = 2000, max_depth_frames: int = 240, max_dt_ms: float = 25.0):
        self.sensor_frames: Deque[dict] = collections.deque(maxlen=max_sensor_frames)
        self.depth_frames: Deque[DepthFrame] = collections.deque(maxlen=max_depth_frames)
        self.camera_clock = OneWayClockFit(max_samples=120)
        self.max_dt_ns = int(max_dt_ms * 1e6)

    def push_sensor(self, frame: dict) -> None:
        self.sensor_frames.append(frame)

    def register_depth_frame(
        self,
        frame_id: int,
        device_ts_us: Optional[float] = None,
        host_rx_ns: Optional[int] = None,
    ) -> DepthFrame:
        host_rx_ns = int(host_rx_ns if host_rx_ns is not None else time.perf_counter_ns())
        if device_ts_us is None:
            host_ns = host_rx_ns
        else:
            host_ns = self.camera_clock.add(float(device_ts_us), host_rx_ns)
        d = DepthFrame(int(frame_id), int(host_ns), device_ts_us)
        self.depth_frames.append(d)
        return d

    def match_depth(self, depth_frame: DepthFrame) -> Optional[dict]:
        if not self.sensor_frames:
            return None
        nearest = min(self.sensor_frames, key=lambda s: abs(s["sample_host_ns"] - depth_frame.host_ns))
        dt_ns = int(nearest["sample_host_ns"] - depth_frame.host_ns)
        if abs(dt_ns) > self.max_dt_ns:
            return None
        out = dict(nearest)
        out["dcw_frame_id"] = depth_frame.frame_id
        out["dcw_host_ns"] = depth_frame.host_ns
        out["dcw_device_ts_us"] = depth_frame.device_ts_us
        out["align_dt_ms"] = dt_ns / 1e6
        adt = abs(out["align_dt_ms"])
        out["align_quality"] = "good" if adt <= 8.0 else ("usable" if adt <= 20.0 else "marginal")
        out["DCW_align_key"] = depth_frame.frame_id
        return out

    def register_and_match(
        self,
        frame_id: int,
        device_ts_us: Optional[float] = None,
        host_rx_ns: Optional[int] = None,
    ) -> Optional[dict]:
        return self.match_depth(self.register_depth_frame(frame_id, device_ts_us, host_rx_ns))


def estimate_time_offset_ms(
    imu_series: Sequence[Tuple[float, float]],
    camera_series: Sequence[Tuple[float, float]],
    search_ms: float = 250.0,
    step_ms: float = 1.0,
) -> Tuple[float, float]:
    """
    以角速度模长等标量做粗时间偏移交叉相关。
    输入 [(time_ms, value), ...]。返回 (offset_ms, correlation)。
    正 offset 表示把相机时间加 offset 后与 IMU 更一致。
    这是联调验收工具，不写回原始时间戳。
    """
    if len(imu_series) < 8 or len(camera_series) < 8:
        raise ValueError("时间序列过短")

    def interp(series, t):
        # 线性插值；series 要求升序。
        if t < series[0][0] or t > series[-1][0]:
            return None
        lo, hi = 0, len(series) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if series[mid][0] <= t:
                lo = mid
            else:
                hi = mid
        t0, v0 = series[lo]
        t1, v1 = series[hi]
        if t1 == t0:
            return v0
        a = (t - t0) / (t1 - t0)
        return v0 + a * (v1 - v0)

    best_off = 0.0
    best_corr = -2.0
    steps = int(2 * search_ms / step_ms) + 1

    for i in range(steps):
        off = -search_ms + i * step_ms
        pairs = []
        for t, v in camera_series:
            iv = interp(imu_series, t + off)
            if iv is not None:
                pairs.append((iv, v))
        if len(pairs) < 8:
            continue
        xa = [p[0] for p in pairs]
        ya = [p[1] for p in pairs]
        mx, my = sum(xa) / len(xa), sum(ya) / len(ya)
        vx = sum((x - mx) ** 2 for x in xa)
        vy = sum((y - my) ** 2 for y in ya)
        if vx <= 0 or vy <= 0:
            continue
        corr = sum((x - mx) * (y - my) for x, y in pairs) / math.sqrt(vx * vy)
        if corr > best_corr:
            best_corr, best_off = corr, off

    return best_off, best_corr

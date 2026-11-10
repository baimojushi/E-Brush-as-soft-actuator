"""Passive BRUSH-V1 acquisition-health metrics (no sensor ownership).

Throughput comes from *receiver parser counters*, never from GUI paint ticks.
The source may run at 200 Hz while the Qt view refreshes at roughly 60 Hz.
Each flag only describes the latest observed sample, not every received frame.
"""
from __future__ import annotations
from collections import deque
import time


class AcquisitionQualityMonitor:
    WINDOW_SECONDS = 4.0

    def __init__(self):
        self._points = deque(maxlen=256)
        self._last_connected = False

    def reset(self):
        self._points.clear()

    def update(self, status, sample=None, *, now=None):
        status = status or {}
        now = time.monotonic() if now is None else float(now)
        connected = bool(status.get("connected"))
        streaming = bool(status.get("streaming_data"))
        counts = status.get("parser_stats") or {}
        frames = max(0, int(counts.get("data_frames", status.get("valid_data_frames", 0)) or 0))
        gaps = max(0, int(counts.get("frame_id_gap", 0) or 0))
        if not connected or (self._points and (frames < self._points[-1][1] or gaps < self._points[-1][2])):
            self.reset()
        self._last_connected = connected
        if connected:
            if not self._points or frames != self._points[-1][1] or gaps != self._points[-1][2] or now - self._points[-1][0] >= .2:
                self._points.append((now, frames, gaps))
            while len(self._points) > 2 and now - self._points[1][0] > self.WINDOW_SECONDS:
                self._points.popleft()

        hz = None
        lost = None
        loss_pct = None
        if connected and len(self._points) >= 2:
            t0, n0, g0 = self._points[0]
            t1, n1, g1 = self._points[-1]
            elapsed = t1 - t0
            if elapsed >= 0.7:
                received = max(0, n1 - n0)
                lost = max(0, g1 - g0)
                hz = round(received / elapsed, 1)
                denominator = received + lost
                loss_pct = round(100 * lost / denominator, 2) if denominator else None

        flags = set(sample.get("device_status_names") or ()) if sample and streaming else set()
        drop = set(sample.get("drop_flag_names") or ()) if sample and streaming else set()
        if not streaming:
            imu = hall = "无实时数据"
        else:
            imu = "有效 / 新鲜" if {"imu_valid", "imu_fresh"} <= flags and "imu_stale" not in drop else "无效或不新鲜"
            hall = ("有效 / 未标记过期" if "hall_valid" in flags and
                    not {"hall_stale", "hall_read_fail"} & drop else "无效或过期")
        if not streaming:
            severity = "waiting"
            summary = "等待实时 BRUSH-V1 DATA 数据" if connected else "串口未连接"
        elif imu != "有效 / 新鲜":
            severity, summary = "warning", "惯性数据有效性或新鲜度异常"
        elif hall != "有效 / 未标记过期":
            severity, summary = "warning", "磁传感器数据需检查（不影响独立 IMU 观测）"
        elif (hz is not None and hz < 150) or (loss_pct is not None and loss_pct > 1):
            severity, summary = "warning", "吞吐率或帧缺口需关注（200 Hz 为设计目标）"
        else:
            severity, summary = "good", "当前观测正常；尚不等于通过硬件精度验证"
        return {
            "schema": "ebrush.acquisition_quality.v1",
            "severity": severity, "summary": summary,
            "port": status.get("port"), "connected": connected, "streaming": streaming,
            "host_receive_hz": hz, "design_target_hz": 200,
            "frame_id_gap_window": lost, "frame_gap_percent_window": loss_pct,
            "total_decoded_frames": frames, "total_frame_id_gaps": gaps,
            "last_data_age_s": status.get("last_data_age_s"),
            "imu": imu, "hall": hall,
            "device_status_flags": sorted(flags), "drop_flags": sorted(drop),
            "parser_error": status.get("error") or "",
            "source_note": "接收率由解码器 data_frames 计数增量计算；传感器状态仅为最新采样，无法替代逐帧质量分析",
        }

"""Match BRUSH-V1 samples to monotonic host camera time without inventing sync."""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
import math
import threading


@dataclass(frozen=True)
class MatchedBrush:
    frame_id: int | None
    dt_ms: float | None
    synced: bool
    payload: dict | None
    quality: str


class BrushTimestampBuffer:
    def __init__(self, max_samples: int = 1024, tolerance_ms: float = 15):
        self._items = deque(maxlen=max_samples)
        self._lock = threading.Lock()
        self.tolerance_ns = int(tolerance_ms*1e6)
        self.drops = 0

    def push(self, sample: dict) -> None:
        if not isinstance(sample, dict):
            return
        host_ns = sample.get("sample_host_ns")
        if type(host_ns) is not int or host_ns <= 0:
            return
        with self._lock:
            if self._items and host_ns <= self._items[-1][0]:
                self.drops += 1
                return
            # copy primitive fields only, never hold mutable caller dictionaries
            keys = (
                "frame_id","sample_host_ns","sample_time_us",
                "clock_synced","sync_rtt_ms","calibration_id",
                "device_status_names","drop_flag_names",
                "q_w","q_x","q_y","q_z",
                "ax_m_s2","ay_m_s2","az_m_s2"
            )
            clean = {k: sample[k] for k in keys if k in sample}
            self._items.append((host_ns, clean))

    def match(self, camera_host_ns: int) -> MatchedBrush:
        if camera_host_ns <= 0:
            return MatchedBrush(None,None,False,None,"BAD_CAMERA_TIMESTAMP")
        with self._lock:
            if not self._items:
                return MatchedBrush(None,None,False,None,"NO_BRUSH_SAMPLE")
            idx, frame = min(self._items, key=lambda item: abs(item[0]-camera_host_ns))
            delta = abs(idx-camera_host_ns)
            synced = bool(frame.get("clock_synced",False))
            if delta > self.tolerance_ns:
                return MatchedBrush(frame.get("frame_id"),round(delta/1e6,3),
                                   False,None,"TIME_GAP_EXCEEDED")
            if not synced:
                return MatchedBrush(frame.get("frame_id"),round(delta/1e6,3),
                                   False,None,"BRUSH_CLOCK_UNSYNCED")
            if any(v in frame.get("drop_flag_names",[]) for v in ("imu_stale", "imu_read_fail", "sched_overrun")):
                return MatchedBrush(frame.get("frame_id"),round(delta/1e6,3),
                                   False,None,"STALE_BRUSH_SAMPLE")
            return MatchedBrush(frame.get("frame_id"),round(delta/1e6,3),
                               True,dict(frame),"MATCHED")


class CameraClockBridge:
    """Reuse E-Brush's OneWayClockFit to convert HP60C device timestamps, if available.

    host_receive_ns must be based on time.perf_counter_ns(), the same monotonic
    host clock used by BrushSerial. A fit is not proof of hardware RGB-depth sync.
    """
    def __init__(self):
        try:
            from studio.brush_serial import OneWayClockFit
        except ImportError:
            from brush_serial import OneWayClockFit
        self.fit = OneWayClockFit()

    def add(self, camera_device_ts_us: float, host_receive_ns: int) -> tuple[int,bool]:
        approx=self.fit.add(camera_device_ts_us,host_receive_ns)
        return approx,bool(self.fit.ready)

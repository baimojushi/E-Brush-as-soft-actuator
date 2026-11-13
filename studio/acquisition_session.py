"""Explicit acquisition sessions backed by the active E-Brush device receiver.

Recording is bounded, starts only after a write-authorized operator action, and
closes a real BRLOG file before generating the handoff manifest. The receiver
owns serial traffic; this module owns file/session lifecycle only.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from pathlib import Path

try:
    from .data_handoff import save_manifest
except ImportError:
    from data_handoff import save_manifest


class AcquisitionSession:
    def __init__(self, brush_source, root, quality_provider=None, minimum_frames=150):
        self.brush = brush_source
        self.root = Path(root).resolve()
        self.quality = quality_provider or (lambda: {})
        self.minimum_frames = int(minimum_frames)
        self.lock = threading.RLock()
        self.active = None
        self.last = None
        self._timeout = None

    @staticmethod
    def _workflow_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{16,32}", value):
            raise ValueError("workflow_id 必须为 16–32 位小写十六进制标识")
        return value

    def start(self, *, workflow_id, recording_index, max_duration_seconds=60):
        with self.lock:
            workflow_id = self._workflow_id(workflow_id)
            if not isinstance(recording_index, int) or isinstance(recording_index, bool) or not 1 <= recording_index <= 20:
                raise ValueError("recording_index 必须在 1–20 之间")
            if type(max_duration_seconds) not in (int, float) or not 2 <= max_duration_seconds <= 660:
                raise ValueError("最长录制秒数必须在 2–660 范围内")
            if self.active:
                raise RuntimeError("录制会话正在进行")
            if not self.brush.status().get("streaming_data"):
                raise RuntimeError("请先连接电子毛笔并确认收到有效 DATA 帧")
            folder = self.root / "logs" / "agent_captures"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{workflow_id}_take{recording_index:02d}_{uuid.uuid4().hex[:8]}.brlog"
            self.brush.start_recording(path)
            self.active = {
                "workflow_id": workflow_id, "recording_index": recording_index,
                "path": str(path), "started_unix": time.time(), "state": "recording",
                "max_duration_seconds": float(max_duration_seconds),
            }
            # If RC Studio crashes during recording, this independent E-Brush
            # watchdog closes the file automatically. The partial log remains
            # available for diagnosis, never for automatic training.
            self._timeout = threading.Timer(float(max_duration_seconds), self._expire,
                                            args=(workflow_id,))
            self._timeout.daemon = True
            self._timeout.start()
            return self.status()

    def _expire(self, workflow_id):
        with self.lock:
            if not self.active or self.active.get("workflow_id") != workflow_id:
                return
        try:
            self.stop(workflow_id=workflow_id, abort=True, reason="安全时限已到，日志自动关闭")
        except (RuntimeError, ValueError, OSError):
            pass

    def status(self):
        with self.lock:
            if self.active:
                receiver = self.brush.recording_status()
                return {**self.active, **receiver}
            return dict(self.last) if self.last else {"state": "idle", "active": False}

    def stop(self, *, workflow_id, abort=False, reason=None):
        with self.lock:
            workflow_id = self._workflow_id(workflow_id)
            if not self.active or self.active["workflow_id"] != workflow_id:
                raise ValueError("未找到该工作流的活动录制")
            started = dict(self.active)
            if self._timeout is not None:
                self._timeout.cancel()
                self._timeout = None
            try:
                receiver = self.brush.stop_recording()
            finally:
                self.active = None
            frames = receiver.get("frames", 0)
            path = Path(started["path"])
            result = {
                **started, "active": False, "frames": frames, "finished_unix": time.time(),
                "duration_seconds": max(0, time.time() - started["started_unix"]),
                "interrupted": receiver.get("interrupted", False),
                "quality": self.quality() or {},
            }
            quality = result["quality"]
            hz = quality.get("host_receive_hz")
            gaps = quality.get("frame_gap_percent_window")
            imu = str(quality.get("imu", ""))
            quality_issue = None
            if hz is not None and float(hz) < 120:
                quality_issue = f"接收速率 {hz} Hz 偏低，请检查串口与采集环境"
            elif gaps is not None and float(gaps) > 5:
                quality_issue = f"帧编号缺口率 {gaps}% 偏高，请重新采集"
            elif "无效" in imu or "不新鲜" in imu:
                quality_issue = "IMU 数据状态异常，建议重新采集"
            result["quality_gate"] = ("rejected" if quality_issue else
                                       "measured" if (hz is not None and gaps is not None) else "unmeasured")
            if abort:
                # A partial source log is retained for troubleshooting, marked unusable.
                result.update(state="aborted", manifest=None,
                              reason=reason or "操作员终止录制，已保留原始日志")
            elif result["interrupted"]:
                result.update(state="failed", reason="连接中断；日志保留待人工检查", manifest=None)
            elif quality_issue:
                result.update(state="failed", reason=quality_issue, manifest=None)
            elif frames < self.minimum_frames:
                result.update(state="failed", reason=f"有效数据帧 {frames}，需要至少 {self.minimum_frames} 帧", manifest=None)
            elif not path.is_file() or path.stat().st_size < 12:
                result.update(state="failed", reason="原始日志内容不足", manifest=None)
            else:
                output = self.root / "runtime" / "handoffs" / (path.stem + ".handoff.json")
                try:
                    manifest = save_manifest(path, output, result["quality"])
                except Exception as exc:
                    result.update(state="failed", reason=str(exc), manifest=None)
                else:
                    result.update(state="saved", manifest=str(output), sha256=manifest["source"]["sha256"])
            self.last = result
            return dict(result)

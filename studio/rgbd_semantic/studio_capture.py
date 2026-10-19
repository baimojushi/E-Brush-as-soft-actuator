"""Capture supervision for an existing Studio camera owner.

Requires the *same* live HP60CDirectSDK instance used by Studio, which exposes
a bounded get_frame queue. Never opens a second camera or records by polling
only the latest image. This module has no Qt dependency.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hp60c_bridge import HP60CDirectBridge, HP60CPairingConfig
from .pipeline import RecorderBackpressure
from .sam3_client import Sam3TransportConfig
from .studio_bridge import EBrushVisionCoordinator

log=logging.getLogger("ebrush.studio.capture")


@dataclass(frozen=True)
class StudioCaptureConfig:
    calibration_file: str
    episode_dir: str
    timestamp_unit: str
    depth_unit_m: float
    registration_verified: bool
    host_clock_mapping_verified: bool = False
    pair_tolerance_us: float = 5000.0
    sam3_url: str = "http://127.0.0.1:6014"
    sam3_api_key: str = ""
    sam3_prompt: str = "brush"
    sam3_clip_frames: int = 5
    enable_sam3: bool = False
    sam3_read_timeout_s: float = 180.0
    request_interval_s: float = 5.0
    pose_calibration_file: str | None = None

    def __post_init__(self):
        if not self.calibration_file or not self.episode_dir:
            raise ValueError("A verified calibration file and new episode directory are required")
        if not self.registration_verified:
            raise ValueError("Must verify SDK pixel-level RGB/depth registration before recording")
        if not 1 <= self.sam3_clip_frames <= 400:
            raise ValueError("SAM3 clip length outside supported range")


class StudioCaptureSupervisor:
    """One-owner camera queue consumer; all errors are visible and fail closed.

    The Studio display may call `camera_source.poll()` concurrently, because that
    accessor reads a snapshot and does not drain the camera queue. Only this
    supervisor may call `camera_source.get_frame()`.

    If `brush_serial` is omitted, the episode records only RGB-D. Its signal.csv
    stays empty, and it cannot be exported as verified multimodal training data.
    """

    def __init__(self, camera_source: Any, config: StudioCaptureConfig,
                 brush_serial=None):
        if not callable(getattr(camera_source, "get_frame", None)):
            raise TypeError(
                "Studio camera lacks get_frame(); latest-frame poll() cannot "
                "guarantee full RGB-D recording. Supply an HP60CDirectSDK queue source."
            )
        if not callable(getattr(camera_source, "status", None)):
            raise TypeError("Camera owner must expose health status and capture-integrity flags")
        camera_status=camera_source.status()
        if not isinstance(camera_status,dict) or "capture_integrity_broken" not in camera_status \
                or "frame_queue_drops" not in camera_status or "frame_queue_size" not in camera_status:
            raise ValueError(
                "Camera owner must report capture_integrity_broken and frame_queue_drops; "
                "do not record through a latest-frame-only wrapper"
            )
        if brush_serial is not None and not isinstance(getattr(brush_serial, "frames", None), queue.Queue):
            raise TypeError("BRUSH sensor must expose exclusive decoded frames queue for full-rate recording")
        self.camera_source=camera_source
        self.config=config
        self.brush_serial=brush_serial
        self._lock=threading.Lock()
        self._stop=threading.Event()
        self._closed=False
        self._started=False
        self._fatal=None
        self._recorded=0
        self._last_camera_frame_id=None
        self._thread=None
        self.manager=None
        self.pairing=None

    def start(self, check_service: bool | None = None):
        if self._started:
            raise RuntimeError("Recording already started")
        cfg=self.config
        ep=Path(cfg.episode_dir)
        if ep.exists() and any(ep.iterdir()):
            raise FileExistsError("Recording episode must be empty; no overwrite/replay into old episode")
        conf=Sam3TransportConfig(
            base_url=cfg.sam3_url,
            api_key=cfg.sam3_api_key,
            prompt=cfg.sam3_prompt,
            max_clip_frames=cfg.sam3_clip_frames,
            read_timeout_s=cfg.sam3_read_timeout_s,
        )
        manager=EBrushVisionCoordinator(
            calibration_file=cfg.calibration_file,
            episode_dir=cfg.episode_dir,
            sam3_config=conf,
            brush_serial=self.brush_serial,
            clip_frames=cfg.sam3_clip_frames,
            request_interval_s=cfg.request_interval_s,
            require_verified_camera_clock=True,
            enable_sam3=cfg.enable_sam3,
            pose_calibration_file=cfg.pose_calibration_file,
        )
        try:
            pairing=HP60CDirectBridge(
                manager.calibration,manager.pipeline,
                HP60CPairingConfig(
                    timestamp_unit=cfg.timestamp_unit,
                    depth_unit_m=cfg.depth_unit_m,
                    max_pair_delta_us=cfg.pair_tolerance_us,
                    sdk_registration_verified=cfg.registration_verified,
                    host_clock_mapping_validated=cfg.host_clock_mapping_verified,
                    require_clock_fit=True
                )
            )
            manager.start(check_service=cfg.enable_sam3 if check_service is None else check_service)
        except Exception:
            manager.close(timeout_s=2.0)
            raise
        self.manager=manager
        self.pairing=pairing
        self._started=True
        self._thread=threading.Thread(
            name="HP60C-RGBD-Capture-Recorder",
            target=self._run,
            daemon=True,
        )
        self._thread.start()

    def _run(self):
        try:
            while True:
                c_status=self.camera_source.status()
                # When the SDK has stopped, drain every remaining queued frame
                # before closing. Never discard buffered raw evidence on Stop.
                if self._stop.is_set() and c_status.get("frame_queue_size",0)==0:
                    break
                if c_status.get("capture_integrity_broken") or c_status.get("frame_queue_drops",0):
                    raise RuntimeError("Camera SDK reports dropped/invalid frames; episode incomplete")
                m_status=self.manager.status()
                if m_status.get("recorder_fatal_error") or m_status.get("sensor_fatal_error"):
                    raise RuntimeError("Archival/sensor writer failed; episode incomplete")
                if m_status.get("brush_frame_id_gaps"):
                    raise RuntimeError("BRUSH sample ID gap; episode incomplete")
                if self.brush_serial is not None:
                    sensor_stats=getattr(self.brush_serial,"stats",{})
                    if sensor_stats.get("host_queue_drop",0):
                        raise RuntimeError("BRUSH serial queue overflow; episode incomplete")
                try:
                    packet=self.camera_source.get_frame(timeout=.10)
                except queue.Empty:
                    continue
                if not isinstance(packet,dict):
                    raise ValueError("Camera frame queue must contain callback dictionaries")
                # All camera pixel arrays must already have been copied before SDK
                # callback returns; ingestion itself performs another defensive copy.
                if self.pairing.accept(packet):
                    with self._lock:self._recorded+=1
        except Exception as exc:
            with self._lock:self._fatal=f"{type(exc).__name__}: {exc}"
            log.exception("Recording stopped due to integrity failure")
            self._stop.set()

    def status(self) -> dict:
        with self._lock:
            snapshot={
                "started":self._started,
                "closed":self._closed,
                "active":self._started and not self._stop.is_set(),
                "fatal_error":self._fatal,
                "frames_admitted":self._recorded,
                "brush_queue_connected":self.brush_serial is not None,
                "multimodal_training_ready":False, # only after QA, independent geometry, consent
            }
        if self.manager is not None:
            snapshot["pipeline"]=self.manager.status()
            snapshot["hp60c_pairing"]=self.pairing.snapshot()
        snapshot["camera"]=self.camera_source.status()
        return snapshot

    def request_stop(self):
        # Caller first stops the shared camera stream, then requests draining.
        # Otherwise new packets can be created after the archival boundary.
        current=self.camera_source.status()
        if current.get("state") == "RUNNING":
            with self._lock:
                self._fatal=self._fatal or (
                    "Recording stopped while camera SDK still running; "
                    "last-frame completeness cannot be certified")
        self._stop.set()

    def close(self, timeout_s: float = 240.0):
        if self._closed:
            return
        self.request_stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=min(5.0,timeout_s))
        if self._thread and self._thread.is_alive():
            raise TimeoutError("Capture worker still owns the SDK frame queue")
        shutdown_error=None
        try:
            if self.manager is not None:
                # Leave the archival worker running while final accepted frames
                # schedule their last short-video inference job.
                try:
                    if not self.manager.pipeline.flush(timeout_s=timeout_s):
                        raise TimeoutError(
                            "Accepted RGB-D frames / SAM3 jobs did not flush before shutdown"
                        )
                except Exception as exc:
                    shutdown_error=exc
                # Even after timeout, attempt bounded resource shutdown.
                # An unsuccessful flush is still a fatal episode-integrity error.
                try:
                    self.manager.close(timeout_s=timeout_s if shutdown_error is None else min(timeout_s,5))
                except Exception as exc:
                    shutdown_error=shutdown_error or exc
        finally:
            self._closed=True
        if shutdown_error is not None:
            raise RuntimeError("Episode shutdown incomplete") from shutdown_error
        if self._fatal:
            raise RuntimeError("Camera RGB-D recording incomplete: "+self._fatal)
        if self.manager is not None:
            stat=self.manager.status()
            if stat.get("recorder_fatal_error") or stat.get("sensor_fatal_error"):
                raise RuntimeError("Camera RGB-D or BRUSH recording incomplete")

"""Drop-in integration boundary for E-Brush Studio and the actual camera SDK."""
from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path

import numpy as np

from .adapters import CalibratedCameraAdapter
from .dataset import EpisodeVisionWriter
from .pipeline import RGBDSemanticPipeline
from .sam3_client import Sam3TransportConfig, Sam3V4BatchClient
from .types import CameraCalibration

log=logging.getLogger("ebrush.vision.bridge")


class EBrushVisionCoordinator:
    """Owns the RGB-D archive and background SAM3 worker.

    Caller supplies actual camera frames from vendor SDK. If a BrushSerial
    instance is supplied it must already be started; this class never takes
    exclusive ownership of its serial port.
    """

    def __init__(self, *, calibration_file: str | Path,
                 episode_dir: str | Path,
                 sam3_config: Sam3TransportConfig,
                 brush_serial=None, clip_frames: int = 5,
                 request_interval_s: float = 5.0,
                 require_verified_camera_clock: bool = True,
                 enable_sam3: bool = True, pose_calibration_file=None):
        obj=json.loads(Path(calibration_file).read_text(encoding="utf-8"))
        cal=CameraCalibration(**obj.get("calibration",obj))
        if not cal.geometric_ready:
            raise ValueError("Calibrated, RGB-aligned, rectified depth camera required")
        self.calibration=cal
        self.writer=EpisodeVisionWriter(episode_dir,cal)
        self.sam3=Sam3V4BatchClient(sam3_config,cal)
        mapper=None
        if pose_calibration_file is not None:
            from tools.ebrush_realtime_pose_mapper import PoseMapper
            pose_params=json.loads(Path(pose_calibration_file).read_text(encoding="utf-8"))
            mapper=PoseMapper(pose_params,heading_mode="startup_reference")
        self.pipeline=RGBDSemanticPipeline(
            cal,self.writer,self.sam3,clip_frames,
            request_interval_s=request_interval_s,
            enable_sam3=enable_sam3,pose_mapper=mapper,
        )
        self.enable_sam3=bool(enable_sam3)
        self.camera=CalibratedCameraAdapter(
            cal,self.pipeline,
            require_verified_camera_clock=require_verified_camera_clock,
        )
        from .sensor_log import SignalCSVWriter
        self.sensor_archive=SignalCSVWriter(episode_dir)
        self.sensor_fatal_error=None
        self.brush_serial=brush_serial
        self._stop=threading.Event()
        self._pump=None

    def start(self,check_service:bool = True):
        if check_service and self.enable_sam3:
            self.sam3.health()
        self.pipeline.start()
        if self.brush_serial is not None:
            self._pump=threading.Thread(target=self._pump_serial,
                                        daemon=True,name="EBrushSensorPump")
            self._pump.start()

    def _pump_serial(self):
        while not self._stop.is_set() or not self.brush_serial.frames.empty():
            source_stats=getattr(self.brush_serial,"stats",{})
            if source_stats.get("host_queue_drop",0)>0:
                self.sensor_fatal_error="BRUSH serial queue dropped samples; episode incomplete"
                self._stop.set()
                log.error(self.sensor_fatal_error)
                return
            try:
                sample=self.brush_serial.frames.get(timeout=.2)
            except queue.Empty:
                continue
            if isinstance(sample,dict):
                try:
                    self.on_brush_frame(sample)
                except (ValueError,OSError) as exc:
                    self.sensor_fatal_error=str(exc)
                    self._stop.set()
                    log.exception("E-Brush sensor recording failed; episode incomplete")
                    return

    def on_brush_frame(self, sample:dict):
        """Use this when Studio already consumes the BRUSH queue; no double readers."""
        self.sensor_archive.append(sample)
        self.pipeline.on_brush_frame(sample)

    def on_camera_sdk_frame(self, **kwargs):
        """Call from vendor callback; expects RGB, aligned_depth, frame ID and clock."""
        return self.camera.on_sdk_frame(**kwargs)

    def status(self):
        status=self.pipeline.status()
        status["calibration_id"]=self.calibration.calibration_id
        status["brush_samples_written"]=self.sensor_archive.count
        status["brush_frame_id_gaps"]=self.sensor_archive.frame_id_gaps
        status["sensor_fatal_error"]=self.sensor_fatal_error
        status["camera_geometry_verified"]=self.calibration.geometric_ready
        status["tracking_position_kind"]="visible_surface_robust_center_not_brush_tip"
        return status

    def visible_surface_world_proxy(self):
        """For UI visualization only; never a certified brush tip coordinate."""
        track=self.pipeline.status()["latest_track"]
        if not track or track["state"] not in ("OBSERVED","PREDICTED"):
            return None
        position=track.get("camera_center_m")
        if position is None or self.calibration.world_from_camera is None:
            return None
        T=np.asarray(self.calibration.world_from_camera,dtype=np.float64)
        xyz=(T[:3,:3] @ np.asarray(position) + T[:3,3]).tolist()
        return {
            "track_id":track["track_id"],
            "source_camera_frame_id":track["camera_frame_id"],
            "world_visible_surface_center_m":xyz,
            "quality_state":track["state"],
            "relative_confidence":track["confidence"],
            "object_position_type":"visible_surface_robust_center",
            "is_brush_tip":False,
            "is_training_ground_truth":False,
        }

    def close(self,timeout_s:float = 240):
        self._stop.set()
        if self._pump is not None and self._pump.is_alive():
            self._pump.join(timeout=min(10.0,timeout_s))
        pump_active=self._pump is not None and self._pump.is_alive()
        # Serial port stays owned by the Studio / caller.
        try:
            self.pipeline.close(timeout_s=timeout_s)
        finally:
            if not pump_active:
                self.sensor_archive.close()
        if pump_active:
            raise TimeoutError("BRUSH queue still being written; cannot finalize signal.csv")

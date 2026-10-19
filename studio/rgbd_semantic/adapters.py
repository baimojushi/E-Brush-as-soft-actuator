"""Camera SDK and Studio entrypoint contracts without vendor-specific inventions."""
from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from .sync import CameraClockBridge
from .types import CameraCalibration, RGBDFrame

log=logging.getLogger("ebrush.rgbd.camera")


class CalibratedCameraAdapter:
    """Call this from an actual HP60C SDK callback after SDK depth-to-RGB alignment.

    The SDK must supply depth raw units and correct calibrated geometry. The
    adapter rejects unverified alignment and never silently resizes depth.
    """
    def __init__(self, calibration: CameraCalibration, pipeline,
                 *, require_verified_camera_clock:bool = False):
        self.calib=calibration
        self.pipeline=pipeline
        self.require_verified_camera_clock=require_verified_camera_clock
        self.last_camera_frame_id=-1
        self.clock_bridge=CameraClockBridge()

    def on_sdk_frame(self, *, camera_frame_id:int, rgb:np.ndarray,
                     aligned_depth:np.ndarray,
                     camera_device_timestamp_us:float|None,
                     capture_host_ns:int|None = None,
                     rgb_depth_synchronized:bool = False,
                     host_time_mapped:bool = False):
        """Return only after frame successfully admitted to bounded recorder.

        All arrays must be exact SDK outputs; `rgb` must be RGB channel order.
        `rgb_depth_synchronized` proves camera RGB-depth capture pairing;
        `host_time_mapped` proves timestamp is from correctly fitted monotonic clock.
        """
        if camera_frame_id<=self.last_camera_frame_id:
            raise ValueError("Camera SDK emitted unordered or duplicate frame")
        if not self.calib.geometric_ready:
            raise ValueError("Verified RGB/depth alignment and rectification required")
        if not rgb_depth_synchronized:
            raise ValueError("Camera RGB/depth pairing is not verified")
        if capture_host_ns is None:
            if camera_device_timestamp_us is None:
                raise ValueError("Capture timestamp missing")
            # Arrival timestamp can be used for diagnostic recording only.
            arrival=time.perf_counter_ns()
            capture_host_ns,host_time_mapped=self.clock_bridge.add(
                camera_device_timestamp_us, arrival
            )
        if self.require_verified_camera_clock and not host_time_mapped:
            raise ValueError("Camera host clock not synchronized")
        frame=RGBDFrame(
            camera_frame_id,capture_host_ns,np.asarray(rgb),np.asarray(aligned_depth),
            self.calib.calibration_id,camera_device_timestamp_us,
            bool(rgb_depth_synchronized and host_time_mapped)
        )
        self.pipeline.on_rgbd_frame(frame)
        self.last_camera_frame_id=camera_frame_id
        return True

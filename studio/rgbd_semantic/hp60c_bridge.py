"""Translate the real HP60CDirectSDK callback dictionaries into audited RGB-D frames.

Fail-closed on incompatible RGB/depth pairing, timestamp units and alignment.
This module never fabricates camera intrinsics or pretends image resizing is calibration.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import threading

import numpy as np

from .sync import CameraClockBridge
from .types import CameraCalibration, RGBDFrame


class HP60CFrameError(ValueError):
    pass


@dataclass(frozen=True)
class HP60CPairingConfig:
    # The vendor must confirm TS_STEADY_CLOCK unit; do not assume nanoseconds.
    timestamp_unit: str
    max_pair_delta_us: float = 5000.0
    depth_unit_m: float = .001
    frame_id_policy: str = "strict_equal"
    require_clock_fit: bool = True
    sdk_registration_verified: bool = False
    # One-way USB host-arrival fitting alone does not certify a precision timebase.
    host_clock_mapping_validated: bool = False

    def __post_init__(self):
        if self.timestamp_unit not in ("us", "ns", "ms"):
            raise ValueError("Explicit HP60C timestamp_unit must be us, ns, or ms")
        if self.frame_id_policy != "strict_equal":
            raise ValueError("Unverified RGB-depth frame matching is unsupported")
        if not (.0 < self.max_pair_delta_us <= 50000):
            raise ValueError("Invalid RGB-depth timestamp tolerance")
        if not math.isfinite(self.depth_unit_m) or self.depth_unit_m<=0:
            raise ValueError("Invalid depth unit")


class HP60CDirectBridge:
    """One sequential consumer of HP60CDirectSDK.get_frame(), not .poll().

    SDK .poll() supplies only the latest buffer and silently skips intervening
    callback packets. This bridge expects the patched, bounded SDK frame queue.
    """

    def __init__(self, calibration: CameraCalibration, pipeline, pairing: HP60CPairingConfig):
        self.calibration=calibration
        self.pipeline=pipeline
        self.pairing=pairing
        if not calibration.geometric_ready:
            raise ValueError("RGB-D geometry must have independently verified alignment")
        if not pairing.sdk_registration_verified:
            raise ValueError("Vendor RGB-to-depth registration must be verified")
        if not math.isclose(calibration.depth_scale_m,pairing.depth_unit_m,rel_tol=1e-8):
            raise ValueError("HP60C depth unit conflicts with calibrated scale")
        self.clock=CameraClockBridge()
        self.last_frame_id=None
        self.last_host_ns=None
        self.stats={
            "submitted":0,"rejected":0,"clock_warmup_skipped":0,
            "frame_id_gaps":0,"last_reject":None
        }
        self.lock=threading.Lock()

    def _to_microseconds(self,value):
        if type(value) is not int or value < 0:
            raise HP60CFrameError("Missing/invalid SDK steady-clock timestamp")
        factor={"us":1.0,"ns":0.001,"ms":1000.0}[self.pairing.timestamp_unit]
        return float(value)*factor

    def convert(self, packet:dict) -> RGBDFrame | None:
        try:
            if not isinstance(packet,dict):
                raise HP60CFrameError("Missing camera callback dictionary")
            rgb_id=packet.get("rgb_frame_id")
            depth_id=packet.get("depth_frame_id")
            if type(rgb_id) is not int or type(depth_id) is not int:
                raise HP60CFrameError("RGB and depth must expose explicit frame IDs")
            if rgb_id!=depth_id:
                raise HP60CFrameError("RGB/depth frame IDs disagree")
            rgb_ts=self._to_microseconds(packet.get("rgb_camera_ts"))
            depth_ts=self._to_microseconds(packet.get("depth_camera_ts"))
            if abs(rgb_ts-depth_ts)>self.pairing.max_pair_delta_us:
                raise HP60CFrameError("RGB/depth device timestamps too far apart")

            rgb=np.asarray(packet.get("rgb"))
            depth=np.asarray(packet.get("depth_mm"))
            if rgb.dtype!=np.uint8 or rgb.shape!=(
                self.calibration.height,self.calibration.width,3):
                raise HP60CFrameError("Unexpected HP60C RGB shape/channel order")
            if depth.dtype!=np.uint16 or depth.shape!=(
                self.calibration.height,self.calibration.width):
                raise HP60CFrameError("Unexpected HP60C raw depth shape or dtype")
            # SDK current callback host_ns is USB arrival monotonic perf_counter_ns,
            # and only serves as a noisy clock-fit observation.
            host_arrival=packet.get("host_ns")
            if type(host_arrival) is not int or host_arrival<=0:
                raise HP60CFrameError("Missing callback host perf_counter_ns")
            source_id=rgb_id
            with self.lock:
                if self.last_frame_id is not None:
                    if source_id<=self.last_frame_id:
                        raise HP60CFrameError("Out-of-order/duplicate camera frame")
                    if source_id>self.last_frame_id+1:
                        self.stats["frame_id_gaps"]+=source_id-self.last_frame_id-1
                        raise HP60CFrameError(
                            "HP60C camera frame gap detected; episode incomplete")
                # Mark gap and progress even during timestamp warmup.
                self.last_frame_id=source_id
                mapped_ns,ready=self.clock.add((rgb_ts+depth_ts)/2.,host_arrival)
                if self.last_host_ns is not None and mapped_ns<=self.last_host_ns:
                    raise HP60CFrameError("Fitted camera timestamp not monotonic")
                self.last_host_ns=mapped_ns
                if self.pairing.require_clock_fit and not ready:
                    self.stats["clock_warmup_skipped"]+=1
                    return None
            f=RGBDFrame(source_id,mapped_ns,rgb,depth,
                        self.calibration.calibration_id,
                        camera_device_ts_us=(rgb_ts+depth_ts)/2.,
                        camera_clock_synced=bool(
                            ready and self.pairing.host_clock_mapping_validated))
            f.validate(self.calibration)
            return f
        except (KeyError,ValueError,TypeError) as exc:
            with self.lock:
                self.stats["rejected"]+=1
                self.stats["last_reject"]=str(exc)
            if isinstance(exc,HP60CFrameError):
                raise
            raise HP60CFrameError(str(exc)) from exc

    def accept(self, packet:dict) -> bool:
        frame=self.convert(packet)
        if frame is None:
            return False
        self.pipeline.on_rgbd_frame(frame)
        with self.lock:self.stats["submitted"]+=1
        return True

    def snapshot(self) -> dict:
        with self.lock:return dict(self.stats)

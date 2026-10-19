"""Append-only E-Brush sensor evidence in the repository's signal.csv schema."""
from __future__ import annotations

import csv
import math
import json
import time
from pathlib import Path
import threading


SIGNAL_FIELDS=(
    "frame_id calibration_id sample_time_us hall_time_us imu_time_us "
    "sample_host_ns hall_host_ns imu_host_ns host_rx_ns clock_synced sync_rtt_ms "
    "device_status drop_flags hall_x_raw hall_y_raw hall_z_raw hall_temp_raw "
    "hall_conv_status hall_device_status imu_status imu_temp_raw "
    "gyro_x_raw gyro_y_raw gyro_z_raw accel_x_raw accel_y_raw accel_z_raw "
    "bx_mT by_mT bz_mT hall_temp_C imu_temp_C "
    "gx_rad_s gy_rad_s gz_rad_s ax_m_s2 ay_m_s2 az_m_s2 "
    "q_w q_x q_y q_z"
).split()


class SignalCSVWriter:
    """One sequential writer; duplicates/reboots invalidate continuous episodes."""

    def __init__(self, episode_dir: str|Path):
        path=Path(episode_dir)/"signal.csv"
        if path.exists():
            raise FileExistsError("Existing BRUSH signal.csv; start a new episode")
        path.parent.mkdir(parents=True,exist_ok=True)
        self.path=path
        before=time.perf_counter_ns()
        self.utc_anchor_ns=time.time_ns()
        after=time.perf_counter_ns()
        self.monotonic_anchor_ns=(before+after)//2
        self.anchor_pair_uncertainty_ns=(after-before)//2
        timebase_path=path.parent/"vision"/"timebase.json"
        timebase_path.parent.mkdir(parents=True,exist_ok=True)
        with open(timebase_path,"x",encoding="utf-8") as t:
            json.dump({
                "archive_signal_clock":"posix_utc_ns_estimated",
                "vision_frame_clock":"perf_counter_monotonic_ns",
                "brush_buffer_clock":"perf_counter_monotonic_ns",
                "monotonic_anchor_ns":self.monotonic_anchor_ns,
                "utc_anchor_ns":self.utc_anchor_ns,
                "anchor_measurement_spread_ns":self.anchor_pair_uncertainty_ns,
                "warning":"Host OS clock synchronization drift/accuracy is unverified",
            },t,ensure_ascii=False,indent=2)
        self.handle=open(path,"x",encoding="utf-8",newline="",buffering=1)
        self.writer=csv.DictWriter(self.handle,fieldnames=SIGNAL_FIELDS,
                                   extrasaction="ignore")
        self.writer.writeheader()
        self.lock=threading.Lock()
        self.count=0
        self.frame_id_gaps=0
        self.last_id=None
        self.last_sample_time_us=None

    def append(self, sample:dict):
        with self.lock:
            fid=sample.get("frame_id")
            ts=sample.get("sample_time_us")
            if type(fid) is not int or not 0<=fid<2**32 or type(ts) is not int or ts<=0:
                raise ValueError("Invalid E-Brush DATA frame identity/time")
            if self.last_id is not None:
                gap=(fid-self.last_id)&0xFFFFFFFF
                if gap==0 or gap>=2**31 or ts<=self.last_sample_time_us:
                    raise ValueError("Out-of-order/rebooted BRUSH stream; close episode")
                if gap>1:
                    self.frame_id_gaps+=gap-1
            row={}
            for key in SIGNAL_FIELDS:
                value=sample.get(key)
                if key in ("sample_host_ns","hall_host_ns","imu_host_ns","host_rx_ns"):
                    if type(value) is int and value>0:
                        value=self.utc_anchor_ns+(value-self.monotonic_anchor_ns)
                if isinstance(value,float) and not math.isfinite(value):
                    value=""
                if isinstance(value,bool):
                    value=1 if value else 0
                row[key]=value if value is not None else ""
            self.writer.writerow(row)
            self.handle.flush()
            self.last_id=fid
            self.last_sample_time_us=ts
            self.count+=1

    def close(self):
        with self.lock:
            if not self.handle.closed:
                self.handle.flush()
                self.handle.close()

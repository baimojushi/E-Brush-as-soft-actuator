#!/usr/bin/env python3
"""Read-only probe of the running Studio agent API (Gate A: observe, never mutate).

Usage: py -3 tools\\diag_pose_lock.py [--host 127.0.0.1] [--port 8765] [--seconds 6]
Prints why the UP0 / reference lock is or is not engaging. Sends no commands.
"""
from __future__ import annotations
import argparse
import json
import time
import urllib.error
import urllib.request

POSE_KEYS = (
    "reference_enabled", "reference_locked", "reference_state",
    "imu_data_valid", "frame_fresh", "stale",
    "up0_tilt_deg", "up0_vertical_valid", "tilt_from_vertical_deg",
    "accel_gravity_error_m_s2", "gyro_norm_rad_s", "pose_quality",
    "frame_id", "brush_axis_x", "brush_axis_y", "brush_axis_z",
)
BRUSH_KEYS = ("frame_id", "q_w", "q_x", "q_y", "q_z", "device_status_names",
              "accel_x_raw", "accel_y_raw", "accel_z_raw")


def get(host: str, port: int, path: str):
    url = f"http://{host}:{port}/v1/state/{path}"
    with urllib.request.urlopen(url, timeout=3) as r:
        return json.load(r)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--seconds", type=float, default=6.0)
    a = p.parse_args()

    try:
        get(a.host, a.port, "meta")
    except (urllib.error.URLError, OSError) as exc:
        print(f"[ERROR] agent API unreachable at {a.host}:{a.port} -> {exc}")
        print("        Studio may be running without --agent-port, or already closed.")
        return 2

    end = time.monotonic() + a.seconds
    last = None
    while time.monotonic() < end:
        try:
            pose = get(a.host, a.port, "pose")
        except Exception as exc:
            print(f"[WARN] pose fetch failed: {exc}")
            time.sleep(1.0)
            continue

        sig = (pose.get("reference_state"), pose.get("reference_locked"))
        if sig != last:
            print(f"\n[t={time.monotonic() % 1000:7.2f}] reference_state="
                  f"{pose.get('reference_state')}  locked={pose.get('reference_locked')}")
            for k in POSE_KEYS:
                if k in pose:
                    v = pose[k]
                    if isinstance(v, float):
                        print(f"    {k:26s} = {v:+.4f}")
                    else:
                        print(f"    {k:26s} = {v}")
            last = sig
        time.sleep(0.25)

    print("\n--- calibration wizard state ---")
    for ns in ("calibration", "pose_calibration"):
        try:
            cal = get(a.host, a.port, ns)
        except Exception:
            continue
        for k in ("stage", "message", "up0_confirmation_required",
                  "stand_stability", "pickup_detected", "up_r0_tilt_deg",
                  "up_r0_locked", "reference_state"):
            if k in cal:
                print(f"    {k:26s} = {cal[k]}")
        break

    print("\n--- brush ---")
    try:
        br = get(a.host, a.port, "brush")
        for k in BRUSH_KEYS:
            if k in br:
                print(f"    {k:26s} = {br[k]}")
    except Exception as exc:
        print(f"    brush unavailable: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only live watcher for the UP0 wizard (no commands sent, no serial access).

Usage: py -3 tools\\watch_up0.py [--seconds 300] [--interval 1.0]
Prints one line per interval: stage, reference state, tilt, gyro, accel error, flags.
"""
from __future__ import annotations
import argparse
import json
import time
import urllib.request


def get(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/state/{path}", timeout=3) as r:
        return json.load(r)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--seconds", type=float, default=300.0)
    p.add_argument("--interval", type=float, default=1.0)
    a = p.parse_args()

    end = time.monotonic() + a.seconds
    t0 = time.monotonic()
    print(f"# watching {a.seconds:g}s at {a.interval:g}s interval", flush=True)
    while time.monotonic() < end:
        try:
            pose = get(a.port, "pose")
            cal = get(a.port, "calibration")
        except Exception as exc:
            print(f"{time.monotonic()-t0:7.1f}  [agent unreachable] {exc}", flush=True)
            time.sleep(a.interval)
            continue
        tilt = pose.get("up0_tilt_deg")
        print(
            f"{time.monotonic()-t0:7.1f}  stage={str(cal.get('stage')):9s}"
            f" ref={str(pose.get('reference_state')):32s}"
            f" locked={str(pose.get('reference_locked')):5s}"
            f" armed={str(pose.get('reference_enabled')):5s}"
            f" tilt={tilt if tilt is None else round(float(tilt), 2)!s:>6s}"
            f" gyro={pose.get('gyro_norm_rad_s')!s:>7s}"
            f" aerr={pose.get('accel_gravity_error_m_s2')!s:>7s}"
            f" q={pose.get('pose_quality')!s:>6s}"
            f" | {cal.get('message', '')}",
            flush=True,
        )
        time.sleep(a.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent.parent / "host"))
from brush_serial import BrushSerial, list_candidate_ports


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    if args.port is None:
        print("candidate ports:", list_candidate_ports())

    b = BrushSerial(args.port, raw_log_path=args.log)
    b.start()
    t0 = time.time()
    n = 0
    last = None

    try:
        while time.time() - t0 < args.seconds:
            frames = b.drain()
            n += len(frames)
            if frames:
                last = frames[-1]
            time.sleep(0.02)
    finally:
        b.close()

    dt = max(time.time() - t0, 1e-9)
    print(f"received data frames: {n}, rate={n/dt:.1f} Hz")
    print("hello:", json.dumps(b.latest_hello, ensure_ascii=False, indent=2))
    print("health:", json.dumps(b.latest_health, ensure_ascii=False, indent=2))
    if b.latest_health and b.latest_health.get("health_schema") == "v1.1":
        h = b.latest_health
        print(
            "i2c scan:",
            h.get("i2c_scan_addresses_hex"),
            "hall_addr:", h.get("hall_i2c_address_hex"),
            "hall_variant:", h.get("hall_variant"),
            "hall_init:", h.get("hall_init_error_name"),
        )
    print("host stats:", dict(b.stats))
    print("clock synced:", b.clock.synced, "rtt_ms:", b.clock.last_rtt_ns / 1e6)
    if last:
        keep = {
            k: last.get(k)
            for k in (
                "frame_id", "bx_mT", "by_mT", "bz_mT",
                "ax_m_s2", "ay_m_s2", "az_m_s2",
                "gx_rad_s", "gy_rad_s", "gz_rad_s",
                "device_status_names", "drop_flag_names",
                "sync_rtt_ms",
            )
        }
        print("last frame:", json.dumps(keep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

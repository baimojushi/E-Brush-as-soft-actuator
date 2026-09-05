#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Start the e-Brush serial bridge in the foreground and print live status.

Use this when you want to confirm the firmware is talking, watch for errors,
and exercise the host-side background thread before wiring it into
TouchDesigner.  For TD integration see tools/td_print_chop.py and
touchdesigner/script_chop_callbacks.py.

What it does:
  1. Auto-detect (or honour --port) the ESP32-S3 / CH343 port.
  2. Open BrushSerial, start the background reader thread, send PING + START.
  3. Print, every --interval seconds:
       - hello:        one-shot device identity (printed on arrival)
       - health:       uptime / frames / errors / heap / hall variant / i2c scan
       - data rate:    DATA frames per second (estimated)
       - link stats:   sync rtt, drop flag names, device status names
  4. On Ctrl-C, send STOP, close the port, exit cleanly.

Examples:

  python tools/start_bridge.py
  python tools/start_bridge.py --port COM7 --interval 2.0
  python tools/start_bridge.py --raw-log logs/live.brlog   # also write a .brlog
  python tools/start_bridge.py --quiet                    # only updates, no first-time dumps
"""

from __future__ import annotations

import argparse
import collections
import math
import os
import sys
import time
from typing import List, Optional

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
E_BRUSH_ROOT = os.path.normpath(os.path.join(THIS_DIR, ".."))
if E_BRUSH_ROOT not in sys.path:
    sys.path.insert(0, E_BRUSH_ROOT)

try:
    from host import brush_serial  # type: ignore
except Exception as exc:  # pragma: no cover
    print(f"[ERROR] Cannot import host.brush_serial from {E_BRUSH_ROOT}: {exc}")
    sys.exit(2)

import auto_detect_port as adp  # type: ignore


def fmt_hello(h: dict) -> str:
    parts = [
        f"boot_id=0x{h.get('boot_id', 0):08X}",
        f"fw={h.get('fw_version', '?')}",
        f"baud={h.get('serial_baud', '?')}",
        f"target_hz={h.get('stream_hz', '?')}",
        f"hall_variant={h.get('hall_variant', 0)}",
        f"imu_who_am_i=0x{h.get('imu_who_am_i', 0):02X}",
    ]
    return "HELLO  " + " ".join(parts)


def fmt_health(h: dict) -> str:
    schema = h.get("health_schema", "v1")
    if schema == "v1.1":
        scan_hex = ",".join(h.get("i2c_scan_addresses_hex", [])) or "(none)"
        bits = " ".join(h.get("device_status_names", [])) or "(none)"
        drops = " ".join(h.get("drop_flag_names", [])) or "(none)"
        return (
            f"HEALTH v1.1 "
            f"uptime={h.get('uptime_us', 0)/1e6:.1f}s "
            f"frames_sent={h.get('frames_sent', 0)}/{h.get('frames_intended', 0)} "
            f"tx_drops={h.get('tx_drops', 0)} "
            f"crc_err={h.get('rx_crc_errors_mcu', 0)} "
            f"hall_err={h.get('hall_read_errors', 0)} "
            f"imu_err={h.get('imu_read_errors', 0)} "
            f"free={h.get('free_heap', 0)} "
            f"i2c_scan={scan_hex} "
            f"hall=0x{h.get('hall_i2c_address', 0):02X}@v{h.get('hall_variant', 0)} "
            f"err={h.get('hall_init_error_name', '?')} "
            f"status=[{bits}] drops=[{drops}]"
        )
    # v1
    return (
        f"HEALTH v1   "
        f"uptime={h.get('uptime_us', 0)/1e6:.1f}s "
        f"frames_sent={h.get('frames_sent', 0)}/{h.get('frames_intended', 0)} "
        f"tx_drops={h.get('tx_drops', 0)} "
        f"crc_err={h.get('rx_crc_errors_mcu', 0)} "
        f"hall_err={h.get('hall_read_errors', 0)} "
        f"hall_reinits={h.get('hall_reinits', 0)} "
        f"free={h.get('free_heap', 0)}"
    )


def fmt_data_line(frame: dict, rate_hz: float, synced: bool, rtt_ms: float) -> str:
    bx = frame.get("bx_mT", math.nan)
    by = frame.get("by_mT", math.nan)
    bz = frame.get("bz_mT", math.nan)
    ax = frame.get("ax_m_s2", math.nan)
    ay = frame.get("ay_m_s2", math.nan)
    az = frame.get("az_m_s2", math.nan)
    qw = frame.get("q_w", math.nan)
    qx = frame.get("q_x", math.nan)
    qy = frame.get("q_y", math.nan)
    qz = frame.get("q_z", math.nan)
    drop = frame.get("drop_flags", 0)
    return (
        f"DATA   rate={rate_hz:6.1f}Hz "
        f"frame_id={frame.get('frame_id', 0)} "
        f"b=({bx:+.3f},{by:+.3f},{bz:+.3f})mT "
        f"a=({ax:+.2f},{ay:+.2f},{az:+.2f})m/s2 "
        f"q=({qw:+.3f},{qx:+.3f},{qy:+.3f},{qz:+.3f}) "
        f"drops=0x{drop:02X} "
        f"clk={'Y' if synced else 'N'} "
        f"rtt={rtt_ms:.2f}ms"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run e-Brush bridge and print live status.")
    parser.add_argument("--port", help="Preferred port (e.g. COM7). Auto-detects if omitted.")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--interval", type=float, default=2.0, help="Status print period (s).")
    parser.add_argument("--raw-log", metavar="PATH", help="Also write a .brlog capture of the wire stream.")
    parser.add_argument("--quiet", action="store_true", help="Don't dump HELLO/health on arrival; only periodic updates.")
    parser.add_argument("--hello-timeout", type=float, default=5.0, help="Seconds to wait for HELLO before continuing.")
    args = parser.parse_args()

    port = adp.auto_detect_port(preferred_port=args.port, baud=args.baud, verbose=True)
    print(f"[INFO] BrushSerial opening on {port} @ {args.baud}")
    bs = brush_serial.BrushSerial(port, baud=args.baud, raw_log_path=args.raw_log)
    bs.start()

    print(f"[INFO] Streaming started. Status updates every {args.interval:.1f}s. Ctrl-C to stop.")
    print(f"[INFO] Writing raw .brlog to {args.raw_log}" if args.raw_log else "[INFO] No raw .brlog (--raw-log not set)")

    # Track DATA frame counts for rate estimation.
    prev_data_count = 0
    prev_data_at = time.monotonic()
    hello_seen = False
    health_seen = False
    last_health_print = 0.0
    last_data_print = 0.0

    def get_data_count_from_stats() -> int:
        # brush_serial exposes bs.stats; we use 'data' as a count if it's
        # tracked, else fall back to sum of frame-related stats.
        s = bs.stats
        if "data" in s:
            return int(s["data"])
        # Try common names
        for k in ("pt_data", "rx_data", "data_frame"):
            if k in s:
                return int(s[k])
        return 0

    try:
        while True:
            now = time.monotonic()

            # HELLO: print once on first arrival (unless --quiet)
            if not hello_seen and bs.latest_hello is not None:
                if not args.quiet:
                    print(fmt_hello(bs.latest_hello))
                hello_seen = True

            # HEALTH: print once on first arrival, then every --interval s
            if bs.latest_health is not None:
                if not health_seen and not args.quiet:
                    print(fmt_health(bs.latest_health))
                    health_seen = True
                elif now - last_health_print >= args.interval:
                    print(fmt_health(bs.latest_health))
                    last_health_print = now

            # DATA rate: print periodically using the live 'latest' frame
            if bs.latest is not None and now - last_data_print >= args.interval:
                # Estimate rate from queue depth delta over the interval.
                cur = get_data_count_from_stats()
                dt = now - prev_data_at
                rate = (cur - prev_data_count) / dt if dt > 0 else 0.0
                prev_data_count = cur
                prev_data_at = now
                synced = bs.clock.synced
                rtt_ms = bs.clock.last_rtt_ns / 1e6 if math.isfinite(bs.clock.last_rtt_ns) else float("nan")
                print(fmt_data_line(bs.latest, rate, synced, rtt_ms))
                last_data_print = now

            # If we never saw HELLO, nudge the user once
            if not hello_seen and now - prev_data_at > args.hello_timeout and prev_data_count == 0:
                print("[INFO] No HELLO yet; check the cable, press RST on the board, or run auto_detect_port --list")

            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[INFO] Ctrl-C received; sending STOP, closing port.")
    finally:
        try:
            bs.stop_stream()
        except Exception:
            pass
        try:
            bs.close()
        except Exception:
            pass
        # Final summary
        print("[INFO] Final stats:")
        for k, v in sorted(bs.stats.items()):
            print(f"   {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
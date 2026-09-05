#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Read live e-Brush DATA frames from a running BrushSerial and print them in
two formats usable by TouchDesigner CHOPs:

  (1) stdout: 17-channel CSV row per frame, one row per line.
  (2) chop:  Table DAT form, columns = stable channel names.  Suitable for
      copy-paste into a TouchDesigner CHOP DAT (or feeding a Script CHOP).

The 17 channels are the project's "stable CHOP contract" for the e-Brush
data flow into HP60C's TD master project.  Names below are the single
source of truth — when extending, edit CHANNEL_ORDER here and re-run.

Why 17?  See project/td_integration_architecture.md:
  bx/by/bz_mT (3) + ax/ay/az_m_s2 (3) + gx/gy/gz_rad_s (3) + q_w/x/y/z (4) +
  drop_flags (1, bitmask) + clock_synced (1, 0/1) + sync_rtt_ms (1) + align_dt_ms (1)

Usage:

  # (a) Auto-detect port and print CSV to stdout at ~5 Hz
  python tools/td_print_chop.py

  # (b) Specific port + faster print rate
  python tools/td_print_chop.py --port COM7 --rate 30

  # (c) Also emit a TouchDesigner-ready CHOP DAT snippet (clipboard-friendly)
  python tools/td_print_chop.py --port COM7 --chop-dat /path/to/e_brush_chop.dat

  # (d) Read from a recorded .brlog instead of live (for offline replay)
  python tools/td_print_chop.py --replay /path/to/capture.brlog --rate 60
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time
from typing import Iterable, List, Optional

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
E_BRUSH_ROOT = os.path.normpath(os.path.join(THIS_DIR, ".."))
if E_BRUSH_ROOT not in sys.path:
    sys.path.insert(0, E_BRUSH_ROOT)

try:
    from host import brush_serial  # type: ignore
    from host.brush_serial import (
        cobs_decode, parse_raw_packet, _bits,
        STATUS_BITS, DROP_BITS,
        HALL_LSB_PER_MT_BY_VARIANT, HALL_INIT_ERROR_NAMES,
        HALL_TEMP_T0_RAW, HALL_TEMP_T0_C, HALL_TEMP_LSB_PER_C,
        ACCEL_MG_PER_LSB, GYRO_MDPS_PER_LSB, G0,
        DATA, HELLO, HEALTH_V1, HEALTH_V11,
        PT_DATA, PT_HEALTH, PT_HELLO,
        decode_i2c_scan_addresses,
    )
except Exception as exc:  # pragma: no cover
    print(f"[ERROR] Cannot import host.brush_serial from {E_BRUSH_ROOT}: {exc}")
    sys.exit(2)

# ---- Stable CHOP contract ----
# 17 channels, fixed order.  TD side binds to these exact names.
CHANNEL_ORDER: List[str] = [
    # Magnetic field (mT), NaN if Hall variant unknown
    "bx_mT", "by_mT", "bz_mT",
    # Linear acceleration (m/s^2)
    "ax_m_s2", "ay_m_s2", "az_m_s2",
    # Angular velocity (rad/s)
    "gx_rad_s", "gy_rad_s", "gz_rad_s",
    # Orientation quaternion (Mahony 6-axis)
    "q_w", "q_x", "q_y", "q_z",
    # Link health (consumed by the bridge, surfaced for diagnostics)
    "drop_flags",        # bitmask, 0 = clean
    "clock_synced",      # 0 / 1
    "sync_rtt_ms",        # round-trip latency ms
    "align_dt_ms",        # 0 for now (single-stream); future: depth vs brush dt
]


def frame_to_row(frame: dict) -> List[float]:
    """Project a BrushSerial._decode_data dict to the 17-channel row.

    NaN for fields that aren't ready (e.g. bx_mT when hall_variant is unknown).
    """
    def n(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return math.nan
        return float(v)

    return [
        n(frame.get("bx_mT")),
        n(frame.get("by_mT")),
        n(frame.get("bz_mT")),
        n(frame.get("ax_m_s2")),
        n(frame.get("ay_m_s2")),
        n(frame.get("az_m_s2")),
        n(frame.get("gx_rad_s")),
        n(frame.get("gy_rad_s")),
        n(frame.get("gz_rad_s")),
        n(frame.get("q_w")),
        n(frame.get("q_x")),
        n(frame.get("q_y")),
        n(frame.get("q_z")),
        n(frame.get("drop_flags")),
        1.0 if frame.get("clock_synced") else 0.0,
        n(frame.get("sync_rtt_ms")),
        0.0,  # align_dt_ms; populated later when depth frame alignment is added
    ]


def frame_to_chop_row(frame: dict, columns: List[str]) -> str:
    """Format one frame as a Tab Separated Values (TSV) row matching the CHOP
    DAT convention: <sample_index>\\t<col0>\\t<col1>...\\t<colN>.

    TD's "CHOP to DAT" can be loaded with one row per sample, columns in order.
    The first column is the sample index (0..N-1).
    """
    cells = [str(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else "" for v in frame_to_row(frame)]
    return "\t".join(cells)


def chop_dat_header() -> str:
    """CHOP DAT header: 'index' + the 17 channel names, tab-separated."""
    return "index\t" + "\t".join(CHANNEL_ORDER)


def live_loop(port: Optional[str], baud: int, rate_hz: float, chop_dat_path: Optional[str]):
    """Open BrushSerial, print 17-channel CSV rows at ~rate_hz, and optionally
    stream a CHOP DAT file as the frames arrive."""
    sys.path_importer_cache = sys.path_importer_cache  # noqa: silence linter
    import auto_detect_port as adp  # type: ignore

    resolved = adp.auto_detect_port(preferred_port=port, baud=baud, verbose=True)
    print(f"[INFO] BrushSerial opening on {resolved} @ {baud}")
    bs = brush_serial.BrushSerial(resolved, baud=baud)
    bs.start()
    print(f"[INFO] Streaming started. Printing {len(CHANNEL_ORDER)} channels @ {rate_hz} Hz.")
    print("# " + ",".join(CHANNEL_ORDER))  # CSV header to stdout

    csv_out = sys.stdout
    chop_out = open(chop_dat_path, "w", encoding="utf-8", newline="") if chop_dat_path else None
    if chop_out:
        chop_out.write(chop_dat_header() + "\n")

    sample_index = 0
    last_print = 0.0
    period = 1.0 / max(rate_hz, 0.1)
    next_print = time.monotonic()

    try:
        while True:
            now = time.monotonic()
            if now < next_print:
                # Cheap sleep; BrushSerial runs in its own thread so this is fine.
                time.sleep(min(0.005, next_print - now))
                continue

            drained = bs.drain(limit=64) if hasattr(bs, "drain") else []
            if not drained and bs.latest is not None:
                drained = [bs.latest]
                bs.latest = None  # type: ignore

            if drained:
                frame = drained[-1]
                row = frame_to_row(frame)
                csv_out.write(",".join("" if (isinstance(v, float) and math.isnan(v)) else f"{v:.6f}" for v in row) + "\n")
                csv_out.flush()
                if chop_out:
                    chop_out.write(f"{sample_index}\t" + frame_to_chop_row(frame, CHANNEL_ORDER) + "\n")
                    sample_index += 1
                last_print = now
            next_print = last_print + period
    except KeyboardInterrupt:
        print("\n[INFO] Ctrl-C; stopping stream.")
    finally:
        try:
            bs.close()
        except Exception:
            pass
        if chop_out:
            chop_out.close()


def replay_loop(replay_path: str, rate_hz: float, chop_dat_path: Optional[str]):
    """Read frames from a saved .brlog (binary BRUSH_BRL1 format: 11-byte magic
    header, then records of <Q host_rx_ns><H frame_len><frame_len bytes COBS>).
    All brush_serial imports are hoisted to module level so _decode_data_static
    can see them."""
    import struct

    state = {"hall_variant": 0, "latest_hello": None, "latest_health": None}

    with open(replay_path, "rb") as f:
        # The .brlog format begins with the magic literal b"BRUSH_BRL1\n"
        # which is 11 bytes (10 ASCII + 1 newline).  Earlier smoke tests
        # used f.read(12) which silently consumed 1 byte of the first frame.
        magic = f.read(11)
        if magic != b"BRUSH_BRL1\n":
            print(f"[ERROR] Bad .brlog header: {magic!r} (expected b'BRUSH_BRL1\\n')")
            return
        print(f"[INFO] Replaying {replay_path} (binary BRUSH_BRL1)")
        buf = f.read()

    # Walk binary records
    framed: List[Tuple[int, bytes]] = []
    pos = 0
    n = len(buf)
    while pos + 10 <= n:
        host_rx_ns, frame_len = struct.unpack_from("<QH", buf, pos)
        pos += 10
        if pos + frame_len > n:
            break
        framed.append((host_rx_ns, buf[pos:pos + frame_len]))
        pos += frame_len
    print(f"[INFO] Parsed {len(framed)} COBS frames")

    print("# " + ",".join(CHANNEL_ORDER))

    chop_out = open(chop_dat_path, "w", encoding="utf-8", newline="") if chop_dat_path else None
    if chop_out:
        chop_out.write(chop_dat_header() + "\n")

    sample_index = 0
    last_print_ns = 0
    for host_rx_ns, wire in framed:
        try:
            raw = cobs_decode(wire)
            header_tuple, payload = parse_raw_packet(raw)
        except Exception:
            continue
        magic, version, ptype, plen, flags, seq = header_tuple

        if ptype == PT_HELLO:
            vals = HELLO.unpack(payload)
            state["latest_hello"] = {
                "fw_version": f"{vals[5]}.{vals[6]}.{vals[7]}",
                "hall_variant": vals[8],
            }
            continue
        if ptype == PT_HEALTH:
            if len(payload) == HEALTH_V11.size:
                vals = HEALTH_V11.unpack(payload)
                state["latest_health"] = {
                    "hall_variant": vals[20],
                    "hall_init_error": vals[21],
                    "hall_init_error_name": HALL_INIT_ERROR_NAMES.get(vals[21], "?"),
                }
            elif len(payload) == HEALTH_V1.size:
                vals = HEALTH_V1.unpack(payload)
            continue
        if ptype != PT_DATA:
            continue

        v = DATA.unpack(payload)
        frame = _decode_data_static(v, state, host_rx_ns)
        if frame is None:
            continue
        row = frame_to_row(frame)
        print(",".join("" if (isinstance(v, float) and math.isnan(v)) else f"{v:.6f}" for v in row))
        if chop_out:
            chop_out.write(f"{sample_index}\t" + frame_to_chop_row(frame, CHANNEL_ORDER) + "\n")
            sample_index += 1
        if rate_hz > 0:
            time.sleep(1.0 / rate_hz)

    if chop_out:
        chop_out.close()


def _decode_data_static(v, state, host_rx_ns: int = 0):
    """Mirror brush_serial.BrushSerial._decode_data for offline replay.
    host_rx_ns is unused (offline mode has no host clock sync) but accepted
    to keep the call site uniform with the live path."""
    (frame_id, calibration_id,
     sample_time_us, hall_time_us, imu_time_us,
     device_status, drop_flags,
     hx, hy, hz, ht,
     hall_conv_status, hall_device_status, imu_status, _reserved,
     imu_temp, gx, gy, gz, ax, ay, az,
     qw, qx, qy, qz) = v

    hall_variant = state.get("hall_variant", 0) or 0
    if state.get("latest_health"):
        hall_variant = int(state["latest_health"].get("hall_variant", 0) or 0)
    if hall_variant not in (1, 2) and state.get("latest_hello"):
        hall_variant = int(state["latest_hello"].get("hall_variant", 0) or 0)
    state["hall_variant"] = hall_variant

    hall_lsb_per_mT = HALL_LSB_PER_MT_BY_VARIANT.get(hall_variant)
    bx_mT = hx / hall_lsb_per_mT if hall_lsb_per_mT else math.nan
    by_mT = hy / hall_lsb_per_mT if hall_lsb_per_mT else math.nan
    bz_mT = hz / hall_lsb_per_mT if hall_lsb_per_mT else math.nan

    return {
        "frame_id": frame_id,
        "drop_flags": drop_flags,
        "drop_flag_names": _bits(drop_flags, DROP_BITS),
        "device_status_names": _bits(device_status, STATUS_BITS),
        "clock_synced": False,   # offline: no live host clock to discipline
        "sync_rtt_ms": math.nan,
        "bx_mT": bx_mT, "by_mT": by_mT, "bz_mT": bz_mT,
        "ax_m_s2": ax * (ACCEL_MG_PER_LSB * 1e-3) * G0,
        "ay_m_s2": ay * (ACCEL_MG_PER_LSB * 1e-3) * G0,
        "az_m_s2": az * (ACCEL_MG_PER_LSB * 1e-3) * G0,
        "gx_rad_s": gx * (GYRO_MDPS_PER_LSB * 1e-3) * math.pi / 180.0,
        "gy_rad_s": gy * (GYRO_MDPS_PER_LSB * 1e-3) * math.pi / 180.0,
        "gz_rad_s": gz * (GYRO_MDPS_PER_LSB * 1e-3) * math.pi / 180.0,
        "q_w": qw, "q_x": qx, "q_y": qy, "q_z": qz,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Print e-Brush DATA frames in TD-CHOP format.")
    parser.add_argument("--port", help="Preferred port (e.g. COM7). Auto-detects if omitted.")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--rate", type=float, default=5.0, help="Print rate Hz (default 5)")
    parser.add_argument("--chop-dat", metavar="PATH", help="Also stream a TD-ready CHOP DAT file")
    parser.add_argument("--replay", metavar="PATH", help="Replay a saved .brlog file instead of live")
    args = parser.parse_args()

    if args.replay:
        replay_loop(args.replay, args.rate, args.chop_dat)
        return 0

    live_loop(args.port, args.baud, args.rate, args.chop_dat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
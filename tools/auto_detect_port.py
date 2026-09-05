#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Auto-detect e-Brush (ESP32-S3 / CH343) serial port and probe firmware.

Strategy:
1. If --port given, try to open it; return if successful.
2. Else: keyword scan for likely ESP32 / USB-UART / CH343 / WCH devices.
3. For each candidate: open @921600, send PING (0x84), wait for SYNC_RESP (0x11) or
   HELLO (0x02). First responder wins.

Reuses brush_serial.py for COBS / CRC32 / packet assembly so we cannot drift
from the host decoder.
"""

import argparse
import os
import sys
import time
from typing import List, Optional, Tuple

# Reuse the project's serial helpers so we never drift from the host decoder.
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
E_BRUSH_ROOT = os.path.normpath(os.path.join(THIS_DIR, ".."))
if E_BRUSH_ROOT not in sys.path:
    sys.path.insert(0, E_BRUSH_ROOT)

try:
    from host import brush_serial  # type: ignore
    from host.brush_serial import auto_detect_port as _auto_detect_port
except Exception as exc:  # pragma: no cover
    print(f"[ERROR] Cannot import host.brush_serial from {E_BRUSH_ROOT}: {exc}")
    sys.exit(2)

try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except Exception:
    print("[ERROR] pyserial is not installed. Run: pip install pyserial")
    sys.exit(2)


# VID/PID + description keywords for ESP32 / CH343 / WCH / CP210
KEYWORDS = (
    "espressif",
    "cp210",
    "usb serial",
    "jtag",
    "uart",
    "ch340",
    "ch343",
    "wch",
    "usb-uart",
    "1a86",   # WCH CH343 VID hex
    "55d3",   # CH343 PID hex
)


def list_candidate_ports() -> List[Tuple[str, str]]:
    """Return (device, description) for ports whose descriptors hit a keyword."""
    if list_ports is None:
        return []
    out: List[Tuple[str, str]] = []
    for p in list_ports.comports():
        desc = " ".join(filter(None, [p.description, p.manufacturer, p.product]))
        low = desc.lower()
        if any(k in low for k in KEYWORDS):
            out.append((p.device, desc))
    return out


def active_probe(port: str, baud: int = 921600, timeout: float = 0.6) -> Optional[dict]:
    """Open port, send one PING, look for SYNC_RESP or HELLO.
    Returns a dict with the first matching packet info, or None.
    Thin wrapper over brush_serial.active_probe; kept for back-compat.
    """
    if not brush_serial.active_probe(port, baud=baud, timeout=timeout):
        return None
    return {"port": port}


def auto_detect_port(preferred_port: Optional[str] = None, baud: int = 921600, verbose: bool = True) -> str:
    """Re-export from host.brush_serial (see that module for the implementation)."""
    return _auto_detect_port(preferred_port=preferred_port, baud=baud, verbose=verbose)


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-detect e-Brush serial port.")
    parser.add_argument("--port", help="Preferred port (e.g. COM7). Skips probe if openable.")
    parser.add_argument("--baud", type=int, default=921600, help="Baud rate (default 921600)")
    parser.add_argument("--list", action="store_true", help="List keyword candidates and exit.")
    parser.add_argument("--probe", metavar="PORT", help="Probe one specific port and exit.")
    parser.add_argument("--quiet", action="store_true", help="Less chatty output.")
    args = parser.parse_args()

    if args.list:
        cands = list_candidate_ports()
        if not cands:
            print("(no candidates)")
        else:
            for d, desc in cands:
                print(f"{d}\t{desc}")
        return 0

    if args.probe:
        result = active_probe(args.probe, baud=args.baud)
        if result:
            print(f"PROBE OK: {result}")
            return 0
        print("PROBE FAIL: no response")
        return 1

    try:
        port = auto_detect_port(preferred_port=args.port, baud=args.baud, verbose=not args.quiet)
    except RuntimeError as exc:
        print(f"[ERROR] {exc}")
        return 1
    print(port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
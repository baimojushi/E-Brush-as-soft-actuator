"""Optional extension for the existing Studio/BrushSerial BRUSH-V1 receiver.

The original BRUSH-V1 DATA/HELLO/HEALTH remains byte-for-byte compatible.
S3 emits extra packet types 0x20 (processed motion) and 0x21 (wireless health).
The current Studio source tree was not included with this handoff: import
WirelessBrushSerial instead of BrushSerial at its integration point.
"""
from __future__ import annotations

import struct
from typing import Optional
try:
    from .brush_serial import BrushSerial
except ImportError:
    from brush_serial import BrushSerial

PT_OPTIMIZED = 0x20
PT_LINK_HEALTH = 0x21
OPT = struct.Struct("<II3f3fff f I".replace(" ", ""))
LINK = struct.Struct("<7I H B B".replace(" ", ""))
assert OPT.size == 48 and LINK.size == 32


class WirelessBrushSerial(BrushSerial):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.latest_motion: Optional[dict] = None
        self.latest_link: Optional[dict] = None

    def _handle_packet(self, ptype: int, payload: bytes, host_rx_ns: int) -> None:
        if ptype == PT_OPTIMIZED:
            if len(payload) != OPT.size:
                self.stats["opt_bad_size"] += 1
                return
            v = OPT.unpack(payload)
            self.latest_motion = {
                "frame_id": v[0], "flags": v[1],
                "hall_filtered_mT": v[2:5],
                "motion_accel_g": v[5:8],
                "motion_energy": v[8],
                "jerk_g_per_s": v[9],
                "confidence": v[10],
                "missing_frames": v[11],
                "host_rx_ns": host_rx_ns,
            }
            return
        if ptype == PT_LINK_HEALTH:
            if len(payload) != LINK.size:
                self.stats["link_bad_size"] += 1
                return
            v = LINK.unpack(payload)
            self.latest_link = dict(zip((
                "packets_ok", "packets_bad", "queue_drops",
                "frame_gaps", "duplicate_frames", "c3_transport_drops",
                "usb_drops", "ble_mtu", "link_type", "link_alive",
            ), v))
            self.latest_link["host_rx_ns"] = host_rx_ns
            return
        super()._handle_packet(ptype, payload, host_rx_ns)

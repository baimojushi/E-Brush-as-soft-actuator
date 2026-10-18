"""Test the shipped Python Studio adapter without requiring hardware."""
from __future__ import annotations
import math
from pathlib import Path
import sys
import struct
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"studio"))
from wireless_studio_adapter import WirelessBrushSerial, OPT, LINK, PT_OPTIMIZED, PT_LINK_HEALTH
from accel_trail import AccelerationTrail
from brush_serial import build_packet, cobs_decode, parse_raw_packet, DATA, PT_DATA


def check(v: bool, message: str = "fail"):
    assert v, message


def test_bridge_structs():
    # The legacy Studio host expects these fixed V1 data fields.
    raw_values = [13,0,1000,996,997,0x7f,0,100,101,102,300,0,0,3,0,
                  500,1,2,3,200,201,202,1.0,0.0,0.0,0.0]
    check(len(raw_values) == len(DATA.unpack(bytes(DATA.size))))
    payload = DATA.pack(*raw_values)
    wire = build_packet(PT_DATA,0,payload)
    parsed, result = parse_raw_packet(cobs_decode(wire[:-1]))
    check(parsed[2] == PT_DATA and result == payload)
    check(OPT.size == 48 and LINK.size == 32)
    obj = WirelessBrushSerial.__new__(WirelessBrushSerial)
    from collections import Counter
    obj.stats = Counter()
    obj.latest_motion = None
    obj.latest_link = None
    opt_payload = OPT.pack(13,0,1.0,2.0,3.0,0.1,0.2,0.3,0.4,12.0,0.95,0)
    obj._handle_packet(PT_OPTIMIZED,opt_payload,123456)
    check(obj.latest_motion is not None)
    check(obj.latest_motion["frame_id"] == 13)
    check(abs(obj.latest_motion["confidence"]-.95) < 1e-6)
    link_payload = LINK.pack(100,3,2,15,1,5,8,247,1,1)
    obj._handle_packet(PT_LINK_HEALTH,link_payload,123456)
    check(obj.latest_link["frame_gaps"] == 15)
    check(obj.latest_link["ble_mtu"] == 247)
    obj._handle_packet(PT_OPTIMIZED,b"bad",123456)
    check(obj.stats["opt_bad_size"] == 1)


def test_trail():
    buf = AccelerationTrail(duration_s=.9,smoothing_s=.05,max_points=40)
    for i in range(101):
        # 100 samples across 0.5s, capacity bound 40 points.
        t = i * .005
        check(buf.add(t,(math.sin(t),math.cos(t),t),(.2,.4,.1),.4,.95))
    points = buf.primitives(.5)
    check(len(points)==40)
    check(points[-1].alpha > points[0].alpha)
    check(all(0<=p.alpha<=1 and p.width>0 for p in points))
    check(not buf.add(.5,(0,0,0),(0,0,0)))
    check(not buf.add(.51,(float('nan'),0,0),(0,0,0)))
    check(buf.add(5,(1,2,3),(0,0,0)))
    check(len(buf.primitives(5)) == 1)  # long gap breaks trail
    check(buf.primitives(6) == [])
    buf.reset()
    check(buf.primitives(6) == [])

if __name__ == "__main__":
    test_bridge_structs()
    test_trail()
    print("OK: host V1 compatibility, extension adapter, smooth/fade trail boundaries")

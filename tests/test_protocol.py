import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

from brush_serial import build_packet, cobs_decode, parse_raw_packet, PT_CMD_PING


def test_roundtrip():
    wire = build_packet(PT_CMD_PING, 123, b"abc\x00def")
    assert wire.endswith(b"\x00")
    raw = cobs_decode(wire[:-1])
    header, payload = parse_raw_packet(raw)
    assert header[2] == PT_CMD_PING
    assert header[5] == 123
    assert payload == b"abc\x00def"


if __name__ == "__main__":
    test_roundtrip()
    print("protocol roundtrip OK")

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

from brush_serial import HEALTH_V1, HEALTH_V11, decode_i2c_scan_addresses

assert HEALTH_V1.size == 52
assert HEALTH_V11.size == 88

lo = 1 << 0x22
hi = (1 << (0x44 - 0x40)) | (1 << (0x78 - 0x40))
assert decode_i2c_scan_addresses(lo, hi) == [0x22, 0x44, 0x78]

print("HEALTH v1/v1.1 compatibility OK")

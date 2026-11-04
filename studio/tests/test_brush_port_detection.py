"""Offline check of BRUSH-V1 port enumeration/selection (no hardware, no I/O).

Regression cover for the 2026-11-04 defect: Studio was pinned to a hardcoded
COM7 while the enumeration could not even see a localized (Chinese) descriptor,
and it fell back to candidates[0] when nothing answered.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

STUDIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO))
import brush_serial as bs  # noqa: E402


def port(device, desc, vid=None, pid=None, hwid=""):
    return SimpleNamespace(device=device, description=desc, manufacturer=None,
                           product=None, vid=vid, pid=pid, hwid=hwid)


BT = port("COM5", "蓝牙链接上的标准串行 (COM5)", None, None,
          r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002\7&1DF60E8C&0&EC64C99257BE")
C3_DEBUG = port("COM3", "USB 串行设备 (COM3)", 0x303A, 0x1001,
                "USB VID:PID=303A:1001 SER=98:88:E0:45:0B:C0 LOCATION=1-3:x.0")
CH343 = port("COM7", "USB-Enhanced-SERIAL CH343 (COM7)", 0x1A86, 0x55D3,
             "USB VID:PID=1A86:55D3 SER=5A84015406")
CH340_CN = port("COM8", "USB 串行设备 (COM8)", 0x1A86, 0x7523, "USB VID:PID=1A86:7523")


class FakeListPorts:
    def __init__(self, ports):
        self._ports = list(ports)

    def comports(self):
        return list(self._ports)


def patch_ports(ports):
    bs.list_ports = FakeListPorts(ports)


def patch_probe(predicate):
    calls = []

    def probe(device, baud=921600, timeout=1.5):
        calls.append(device)
        return predicate(device)

    bs.active_probe = probe
    return calls


# 1) The C3 native-USB debug port and Bluetooth ports are never candidates.
patch_ports([BT, C3_DEBUG])
assert bs.list_candidate_ports() == [], bs.list_candidate_ports()
assert bs.classify_candidates([BT, C3_DEBUG]) == []
print("1) Bluetooth + Espressif native-USB debug port excluded: OK")

# 2) Best-first ranking, and a localized descriptor does not hide a bridge.
patch_ports([BT, C3_DEBUG, CH340_CN, CH343])
assert bs.list_candidate_ports() == [
    ("COM7", CH343.description), ("COM8", CH340_CN.description)
], bs.list_candidate_ports()
print("2) CH343/CH340 selected with Chinese descriptors, CH343 first: OK")

# 3) auto_detect_port returns the port that actually answers BRUSH-V1.
calls = patch_probe(lambda dev: dev == "COM8")
assert bs.auto_detect_port(verbose=False) == "COM8"
assert calls == ["COM7", "COM8"], calls
print(f"3) first BRUSH-V1 responder wins, probed in rank order: {calls}")

# 4) No responder -> None, never a guessed port (the old candidates[0] bug).
patch_probe(lambda dev: False)
assert bs.auto_detect_port(verbose=False) is None
print("4) no responder -> None (no silent fallback): OK")

# 5) Only the C3 debug port visible: no candidates, and nothing is opened.
calls = patch_probe(lambda dev: True)
patch_ports([BT, C3_DEBUG])
assert bs.auto_detect_port(verbose=False) is None
assert calls == [], calls
print("5) C3 debug port alone -> never opened or probed: OK")


# 6) An explicit preferred port wins without probing.
class FakeSerial:
    def __init__(self, port, **kwargs):
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


bs.serial = SimpleNamespace(Serial=FakeSerial, SerialException=Exception)
calls = patch_probe(lambda dev: False)
patch_ports([CH343])
assert bs.auto_detect_port(preferred_port="COM9", verbose=False) == "COM9"
assert calls == [], calls
assert bs.is_debug_port("COM9") is False
patch_ports([C3_DEBUG])
assert bs.is_debug_port("COM3") is True
print("6) explicit --port wins; is_debug_port flags the native-USB port: OK")

print("BRUSH_PORT_DETECTION_OK")

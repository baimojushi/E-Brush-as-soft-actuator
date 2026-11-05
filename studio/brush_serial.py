from __future__ import annotations

import collections
import dataclasses
import math
import os
import queue
import struct
import threading
import time
import zlib
from typing import Deque, Dict, Iterable, List, Optional, Tuple

try:
    import serial
    from serial.tools import list_ports
except Exception as exc:  # pragma: no cover
    serial = None
    list_ports = None
    _SERIAL_IMPORT_ERROR = exc
else:
    _SERIAL_IMPORT_ERROR = None

MAGIC = 0x5242
VERSION = 1
HEADER = struct.Struct("<HBBHHI")

PT_DATA = 0x01
PT_HELLO = 0x02
PT_HEALTH = 0x03
PT_SYNC_RESP = 0x11
PT_CMD_SYNC = 0x81
PT_CMD_START = 0x82
PT_CMD_STOP = 0x83
PT_CMD_PING = 0x84

DATA = struct.Struct("<IIQQQIIhhhhBBBBhhhhhhhffff")
HELLO = struct.Struct("<IIIIHHHHBBHI")
HEALTH_V1 = struct.Struct("<QIIIIIIIIIII")
HEALTH_V11 = struct.Struct("<QIIIIIIIIIIIQQIII8B")
SYNC_REQ = struct.Struct("<QI")
SYNC_RESP = struct.Struct("<QIQQ")

STATUS_BITS = {
    0: "hall_present",
    1: "hall_valid",
    2: "hall_diag",
    3: "hall_near_sat",
    4: "imu_present",
    5: "imu_valid",
    6: "imu_fresh",
    7: "pose_valid",
    8: "pose_yaw_relative",
    9: "i2c_recovered",
    10: "spi_recovered",
    11: "streaming",
}

DROP_BITS = {
    0: "sched_overrun",
    1: "hall_read_fail",
    2: "imu_read_fail",
    3: "hall_stale",
    4: "imu_stale",
    5: "tx_backpressure",
}

HALL_LSB_PER_MT_BY_VARIANT = {
    1: 820.0,  # VER=1, _RANGE=0 -> ±40 mT
    2: 250.0,  # VER=2, _RANGE=0 -> ±133 mT
    3: 40960.0,  # MMC5983MA: 18-bit sample >>2, zero-field offset removed on C3
}

HALL_INIT_ERROR_NAMES = {
    0: "none",
    1: "manufacturer_lsb_read",
    2: "manufacturer_msb_read",
    3: "manufacturer_mismatch",
    4: "device_id_read",
    5: "device_variant_invalid",
    6: "config_write",
    7: "config_readback",
    8: "no_candidate_ack",
}

HALL_TEMP_T0_RAW = 17508.0
HALL_TEMP_T0_C = 25.0
HALL_TEMP_LSB_PER_C = 58.0

ACCEL_MG_PER_LSB = 0.122
GYRO_MDPS_PER_LSB = 35.0
G0 = 9.80665


def cobs_encode(data: bytes) -> bytes:
    if not data:
        return b"\x01"
    out = bytearray(b"\x00")
    code_index = 0
    code = 1
    for b in data:
        if b == 0:
            out[code_index] = code
            code_index = len(out)
            out.append(0)
            code = 1
        else:
            out.append(b)
            code += 1
            if code == 0xFF:
                out[code_index] = code
                code_index = len(out)
                out.append(0)
                code = 1
    out[code_index] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        code = data[i]
        if code == 0:
            raise ValueError("COBS code 0")
        i += 1
        end = i + code - 1
        if end > n:
            raise ValueError("COBS truncated")
        out.extend(data[i:end])
        i = end
        if code != 0xFF and i < n:
            out.append(0)
    return bytes(out)


def build_packet(packet_type: int, packet_seq: int, payload: bytes = b"") -> bytes:
    header = HEADER.pack(MAGIC, VERSION, packet_type, len(payload), 0, packet_seq)
    body = header + payload
    crc = struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)
    return cobs_encode(body + crc) + b"\x00"


def parse_raw_packet(raw: bytes) -> Tuple[Tuple[int, int, int, int, int, int], bytes]:
    if len(raw) < HEADER.size + 4:
        raise ValueError("packet too short")
    magic, version, ptype, plen, flags, seq = HEADER.unpack_from(raw, 0)
    if magic != MAGIC or version != VERSION:
        raise ValueError("bad magic/version")
    if len(raw) != HEADER.size + plen + 4:
        raise ValueError("bad payload length")
    expected = struct.unpack_from("<I", raw, len(raw) - 4)[0]
    actual = zlib.crc32(raw[:-4]) & 0xFFFFFFFF
    if expected != actual:
        raise ValueError("crc")
    return (magic, version, ptype, plen, flags, seq), raw[HEADER.size:-4]


def _bits(value: int, names: Dict[int, str]) -> List[str]:
    return [name for bit, name in names.items() if value & (1 << bit)]


def decode_i2c_scan_addresses(bitmap_lo: int, bitmap_hi: int) -> List[int]:
    out = []
    for addr in range(0x00, 0x80):
        if addr < 0x40:
            present = bool(bitmap_lo & (1 << addr))
        else:
            present = bool(bitmap_hi & (1 << (addr - 0x40)))
        if present:
            out.append(addr)
    return out


@dataclasses.dataclass
class SyncObservation:
    mcu_ns: float
    host_ns: float
    rtt_ns: float


class ClockDiscipline:
    """用低往返时延样本拟合 host_perf_ns ~= slope * mcu_ns + offset。"""

    def __init__(self, max_observations: int = 96):
        self.obs: Deque[SyncObservation] = collections.deque(maxlen=max_observations)
        self.slope = 1.0
        self.offset = 0.0
        self.synced = False
        self.last_rtt_ns = math.inf

    def reset(self) -> None:
        self.obs.clear()
        self.slope = 1.0
        self.offset = 0.0
        self.synced = False
        self.last_rtt_ns = math.inf

    def add(self, host_t0_ns: int, host_t3_ns: int, mcu_rx_us: int, mcu_tx_us: int) -> None:
        mcu_service_ns = max(0, int(mcu_tx_us - mcu_rx_us) * 1000)
        rtt_ns = max(0, (host_t3_ns - host_t0_ns) - mcu_service_ns)
        host_mid = (host_t0_ns + host_t3_ns) * 0.5
        mcu_mid = (mcu_rx_us + mcu_tx_us) * 500.0
        self.obs.append(SyncObservation(mcu_mid, host_mid, float(rtt_ns)))
        self.last_rtt_ns = float(rtt_ns)
        self._fit()

    def _fit(self) -> None:
        if not self.obs:
            return
        ordered = sorted(self.obs, key=lambda o: o.rtt_ns)
        keep_n = max(1, min(32, max(4, len(ordered) // 2)))
        good = ordered[:keep_n]

        if len(good) == 1:
            self.slope = 1.0
            self.offset = good[0].host_ns - good[0].mcu_ns
            self.synced = False
            return

        mx = sum(o.mcu_ns for o in good) / len(good)
        my = sum(o.host_ns for o in good) / len(good)
        var = sum((o.mcu_ns - mx) ** 2 for o in good)
        if var <= 0:
            return
        cov = sum((o.mcu_ns - mx) * (o.host_ns - my) for o in good)
        slope = cov / var

        # 允许约 ±2000 ppm 的 MCU 时钟误差，拒绝明显错误拟合。
        if 0.998 <= slope <= 1.002:
            self.slope = slope
            self.offset = my - slope * mx
            self.synced = len(good) >= 4

    def mcu_us_to_host_ns(self, mcu_us: int) -> int:
        return int(self.slope * (mcu_us * 1000.0) + self.offset)


class OneWayClockFit:
    """相机设备时间 -> host_perf_ns 的一阶拟合。用于相机 SDK 提供 device timestamp 的场景。"""

    def __init__(self, max_samples: int = 120):
        self.samples: Deque[Tuple[float, float]] = collections.deque(maxlen=max_samples)
        self.slope = 1.0
        self.offset = 0.0
        self.ready = False

    def add(self, device_ts_us: float, host_rx_ns: int) -> int:
        x = float(device_ts_us) * 1000.0
        y = float(host_rx_ns)
        self.samples.append((x, y))
        if len(self.samples) >= 8:
            self._fit()
        return self.to_host_ns(device_ts_us) if self.ready else host_rx_ns

    def _fit(self) -> None:
        pts = list(self.samples)
        # 先用 offset 中位数剔除主机调度延迟大的帧，再拟合漂移。
        offsets = sorted(y - x for x, y in pts)
        med = offsets[len(offsets) // 2]
        dev = sorted(abs((y - x) - med) for x, y in pts)
        mad = dev[len(dev) // 2] or 1.0
        good = [(x, y) for x, y in pts if abs((y - x) - med) <= max(2_000_000.0, 4.0 * mad)]
        if len(good) < 4:
            return
        mx = sum(x for x, _ in good) / len(good)
        my = sum(y for _, y in good) / len(good)
        var = sum((x - mx) ** 2 for x, _ in good)
        if var <= 0:
            return
        slope = sum((x - mx) * (y - my) for x, y in good) / var
        if 0.995 <= slope <= 1.005:
            self.slope = slope
            self.offset = my - slope * mx
            self.ready = True

    def to_host_ns(self, device_ts_us: float) -> int:
        return int(self.slope * (float(device_ts_us) * 1000.0) + self.offset)


class RawLogger:
    """只增不改的线协议日志：host_rx_ns + COBS 帧长度 + 原始 COBS 帧。"""

    def __init__(self, path: str):
        self.path = path
        self.fp = open(path, "ab", buffering=0)
        if self.fp.tell() == 0:
            self.fp.write(b"BRUSH_BRL1\n")

    def write(self, host_rx_ns: int, wire_frame_without_zero: bytes) -> None:
        self.fp.write(struct.pack("<QH", host_rx_ns, len(wire_frame_without_zero)))
        self.fp.write(wire_frame_without_zero)

    def close(self) -> None:
        try:
            self.fp.close()
        except Exception:
            pass


# The S3 bridge reaches the host through an external USB-UART adapter, so match
# VID/PID instead of description text: Windows localizes that text, and a
# Chinese build reports the Espressif native-USB port as "USB 串行设备 (COM3)"
# and the CH343 as "USB-Enhanced-SERIAL CH343 (COM7)".  Keywords alone see only
# the second one.
_BRIDGE_VIDS: Dict[int, Optional[Tuple[int, ...]]] = {
    0x1A86: (0x55D3, 0x7523, 0x5523, 0x55D4),  # WCH CH343 / CH340 / CH341
    0x10C4: None,                              # Silicon Labs CP210x
    0x0403: None,                              # FTDI
}
# Espressif USB-Serial-JTAG/CDC (303A:1001) is the *debug* port of the C3 (and
# of an S3 on its native USB).  It never carries BRUSH-V1 -- the C3 sample
# packets leave over BLE and the S3 writes the host stream to UART0 -- and
# opening it with pyserial's default DTR/RTS resets the chip into ROM download
# mode.  Excluded from auto-detect; pass --port explicitly if you ever need it.
_DEBUG_VIDS = (0x303A,)
_KEYWORDS = ("espressif", "cp210", "usb serial", "jtag", "uart",
             "ch340", "ch343", "wch", "usb-uart", "1a86", "ftdi")
_BLUETOOTH_HWID_MARKERS = ("BTHENUM", "RFCOMM")


def _port_description(entry) -> str:
    return " ".join(filter(None, (getattr(entry, "description", None),
                                  getattr(entry, "manufacturer", None),
                                  getattr(entry, "product", None))))


def classify_candidates(entries) -> List[Tuple[int, str, str]]:
    """Rank ``(rank, device, description)``; lower rank = better BRUSH-V1 bet.

    Pure function over pyserial ``ListPortInfo``-like objects, so the selection
    rules stay testable without hardware.  Rank 0 is a known USB-UART bridge,
    1 the same vendor with an unexpected PID, 2 an unknown device matched by
    keyword.  Bluetooth/virtual ports and Espressif debug ports are dropped.
    """
    ranked: List[Tuple[int, str, str]] = []
    for entry in entries:
        hwid = (getattr(entry, "hwid", "") or "").upper()
        if any(marker in hwid for marker in _BLUETOOTH_HWID_MARKERS):
            continue  # virtual port: blocks on open, never BRUSH-V1
        vid = getattr(entry, "vid", None)
        if vid in _DEBUG_VIDS:
            continue
        desc = _port_description(entry)
        if vid in _BRIDGE_VIDS:
            pids = _BRIDGE_VIDS[vid]
            rank = 0 if pids is None or getattr(entry, "pid", None) in pids else 1
        elif any(k in desc.lower() for k in _KEYWORDS):
            rank = 2
        else:
            continue
        ranked.append((rank, entry.device, desc))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return ranked


def list_candidate_ports() -> List[Tuple[str, str]]:
    """``(device, description)`` for ports that can carry BRUSH-V1, best first."""
    if list_ports is None:
        return []
    return [(device, desc)
            for _rank, device, desc in classify_candidates(list_ports.comports())]


def is_debug_port(port: str) -> bool:
    """True for an Espressif native-USB debug port (never a BRUSH-V1 bridge)."""
    if list_ports is None:
        return False
    return any(getattr(p, "device", None) == port
               and getattr(p, "vid", None) in _DEBUG_VIDS
               for p in list_ports.comports())


class BrushSerial:
    def __init__(
        self,
        port: Optional[str],
        baud: int = 921600,
        raw_log_path: Optional[str] = None,
        sync_period_s: float = 1.0,
        queue_size: int = 4096,
    ):
        if serial is None:
            raise RuntimeError(
                "未找到 pyserial。请在 TouchDesigner Python 或系统 Python 中安装 pyserial。"
            ) from _SERIAL_IMPORT_ERROR

        if not port:
            ports = list_candidate_ports()
            if len(ports) == 1:
                port = ports[0][0]
            else:
                raise RuntimeError(f"无法唯一确定串口。候选：{ports}")

        self.port = port
        self.baud = baud
        self.sync_period_s = sync_period_s
        self.ser = serial.Serial(port, baudrate=baud, timeout=0, write_timeout=0.05)
        self.clock = ClockDiscipline()
        # queue_size=0 is latest-only mode (Studio GUI does not call drain()).
        # Avoid false host_queue_drop alarms and wasted queue churn at 200 Hz.
        self.frames: Optional["queue.Queue[dict]"] = (
            queue.Queue(maxsize=queue_size) if queue_size > 0 else None)
        self.latest: Optional[dict] = None
        self.latest_hello: Optional[dict] = None
        self.latest_health: Optional[dict] = None
        self.last_protocol_error = ""
        self.last_io_error = ""

        self.stats = collections.Counter()
        self._packet_seq_out = 0
        self._last_packet_seq_in: Optional[int] = None
        self._last_frame_id: Optional[int] = None
        self._boot_id: Optional[int] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._rx_buf = bytearray()
        self._write_lock = threading.Lock()
        self._sync_nonce = 0
        self._sync_sent: Dict[int, int] = {}
        self._logger = RawLogger(raw_log_path) if raw_log_path else None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="BrushSerial", daemon=True)
        self._thread.start()
        self.send_ping()
        self.start_stream()

    def close(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self._logger:
            self._logger.close()
        try:
            self.ser.close()
        except Exception:
            pass

    def _send(self, ptype: int, payload: bytes = b"") -> None:
        wire = build_packet(ptype, self._packet_seq_out, payload)
        self._packet_seq_out = (self._packet_seq_out + 1) & 0xFFFFFFFF
        with self._write_lock:
            self.ser.write(wire)

    def send_ping(self) -> None:
        self._send(PT_CMD_PING)

    def start_stream(self) -> None:
        self._send(PT_CMD_START)

    def stop_stream(self) -> None:
        self._send(PT_CMD_STOP)

    def send_sync(self) -> None:
        nonce = self._sync_nonce = (self._sync_nonce + 1) & 0xFFFFFFFF
        t0 = time.perf_counter_ns()
        self._sync_sent[nonce] = t0
        self._send(PT_CMD_SYNC, SYNC_REQ.pack(t0, nonce))
        # 防止断线时字典无限增长
        if len(self._sync_sent) > 64:
            for k in list(self._sync_sent)[:-32]:
                self._sync_sent.pop(k, None)

    def _run(self) -> None:
        next_sync = time.monotonic()
        while self._running:
            try:
                n = self.ser.in_waiting
                data = self.ser.read(n if n > 0 else 1)
            except Exception as exc:
                self.stats["serial_read_error"] += 1
                self.last_io_error = f"{type(exc).__name__}: {exc}"
                self._running = False
                try:
                    self.ser.close()
                except Exception:
                    pass
                break

            if data:
                self.stats["serial_rx_bytes"] += len(data)
                self._rx_buf.extend(data)
                self._consume_rx()

            now = time.monotonic()
            if now >= next_sync:
                try:
                    self.send_sync()
                except Exception:
                    self.stats["serial_write_error"] += 1
                next_sync = now + self.sync_period_s

            time.sleep(0.0005)

    def _consume_rx(self) -> None:
        while True:
            try:
                idx = self._rx_buf.index(0)
            except ValueError:
                if len(self._rx_buf) > 4096:
                    self.stats["rx_resync"] += 1
                    self._rx_buf.clear()
                return

            wire = bytes(self._rx_buf[:idx])
            del self._rx_buf[: idx + 1]
            if not wire:
                continue

            host_rx_ns = time.perf_counter_ns()
            if self._logger:
                self._logger.write(host_rx_ns, wire)

            try:
                raw = cobs_decode(wire)
                header, payload = parse_raw_packet(raw)
            except ValueError as exc:
                self.stats[f"rx_{str(exc)}"] += 1
                continue

            _, _, ptype, _, _, packet_seq = header
            if self._last_packet_seq_in is not None:
                expected = (self._last_packet_seq_in + 1) & 0xFFFFFFFF
                if packet_seq != expected:
                    self.stats["packet_seq_gap"] += (packet_seq - expected) & 0xFFFFFFFF
            self._last_packet_seq_in = packet_seq

            try:
                self._handle_packet(ptype, payload, host_rx_ns)
            except Exception:
                self.stats["packet_decode_error"] += 1

    def _handle_packet(self, ptype: int, payload: bytes, host_rx_ns: int) -> None:
        if ptype == PT_SYNC_RESP:
            host_tx_ns, nonce, mcu_rx_us, mcu_tx_us = SYNC_RESP.unpack(payload)
            t0 = self._sync_sent.pop(nonce, host_tx_ns)
            self.clock.add(t0, host_rx_ns, mcu_rx_us, mcu_tx_us)
            self.stats["sync_resp"] += 1
            return

        if ptype == PT_HELLO:
            vals = HELLO.unpack(payload)
            hello = {
                "boot_id": vals[0],
                "reset_reason": vals[1],
                "capabilities": vals[2],
                "serial_baud": vals[3],
                "stream_hz": vals[4],
                "fw_version": f"{vals[5]}.{vals[6]}.{vals[7]}",
                "hall_variant": vals[8],
                "imu_who_am_i": vals[9],
                "calibration_id": vals[11],
                "host_rx_ns": host_rx_ns,
            }
            if self._boot_id is not None and hello["boot_id"] != self._boot_id:
                self.clock.reset()
                self._last_frame_id = None
                self.stats["device_reboot"] += 1
            self._boot_id = hello["boot_id"]
            self.latest_hello = hello
            return


        if ptype == PT_HEALTH:
            if len(payload) == HEALTH_V11.size:
                vals = HEALTH_V11.unpack(payload)
                scan_addresses = decode_i2c_scan_addresses(vals[12], vals[13])
                hall_addr = vals[19]
                hall_variant = vals[20]
                hall_init_error = vals[21]

                self.latest_health = {
                    "uptime_us": vals[0],
                    "frames_intended": vals[1],
                    "frames_sent": vals[2],
                    "tx_drops": vals[3],
                    "rx_crc_errors_mcu": vals[4],
                    "rx_frame_errors_mcu": vals[5],
                    "hall_read_errors": vals[6],
                    "imu_read_errors": vals[7],

                    # 旧协议字段名保留；同时给出更准确的语义别名。
                    "hall_reinits": vals[8],
                    "imu_reinits": vals[9],
                    "hall_reinit_attempts": vals[8],
                    "imu_reinit_attempts": vals[9],

                    "free_heap": vals[10],
                    "min_free_heap": vals[11],

                    "i2c_scan_bitmap_lo": vals[12],
                    "i2c_scan_bitmap_hi": vals[13],
                    "i2c_scan_duration_us": vals[14],
                    "hall_recoveries": vals[15],
                    "imu_recoveries": vals[16],
                    "i2c_scan_count": vals[17],
                    "i2c_scan_done": bool(vals[18]),
                    "i2c_scan_addresses": scan_addresses,
                    "i2c_scan_addresses_hex": [f"0x{x:02X}" for x in scan_addresses],

                    "hall_i2c_address": hall_addr,
                    "hall_i2c_address_hex": f"0x{hall_addr:02X}" if hall_addr else None,
                    "hall_variant": hall_variant,
                    "hall_init_error": hall_init_error,
                    "hall_init_error_name": HALL_INIT_ERROR_NAMES.get(
                        hall_init_error, f"unknown_{hall_init_error}"
                    ),
                    "hall_manufacturer_lsb": vals[22],
                    "hall_manufacturer_msb": vals[23],
                    "hall_device_id": vals[24],
                    "host_rx_ns": host_rx_ns,
                    "health_schema": "v1.1",
                }
            elif len(payload) == HEALTH_V1.size:
                vals = HEALTH_V1.unpack(payload)
                self.latest_health = {
                    "uptime_us": vals[0],
                    "frames_intended": vals[1],
                    "frames_sent": vals[2],
                    "tx_drops": vals[3],
                    "rx_crc_errors_mcu": vals[4],
                    "rx_frame_errors_mcu": vals[5],
                    "hall_read_errors": vals[6],
                    "imu_read_errors": vals[7],
                    "hall_reinits": vals[8],
                    "imu_reinits": vals[9],
                    "hall_reinit_attempts": vals[8],
                    "imu_reinit_attempts": vals[9],
                    "free_heap": vals[10],
                    "min_free_heap": vals[11],
                    "host_rx_ns": host_rx_ns,
                    "health_schema": "v1",
                }
            else:
                self.stats["health_unknown_size"] += 1
            return
        if ptype == PT_DATA:
            if len(payload) != DATA.size:
                self.stats["data_bad_size"] += 1
                self.stats[f"data_plen_{len(payload)}"] += 1
                self.last_protocol_error = (
                    f"DATA payload length mismatch: device={len(payload)}, "
                    f"Studio={DATA.size}; check flashed firmware protocol")
                return
            vals = DATA.unpack(payload)
            self.last_protocol_error = ""
            self.stats["data_frames"] += 1
            frame = self._decode_data(vals, host_rx_ns)

            fid = frame["frame_id"]
            if self._last_frame_id is not None:
                expected = (self._last_frame_id + 1) & 0xFFFFFFFF
                if fid != expected:
                    self.stats["frame_id_gap"] += (fid - expected) & 0xFFFFFFFF
            self._last_frame_id = fid

            self.latest = frame
            if self.frames is not None:
                try:
                    self.frames.put_nowait(frame)
                except queue.Full:
                    self.stats["host_queue_drop"] += 1
                    try:
                        self.frames.get_nowait()
                        self.frames.put_nowait(frame)
                    except Exception:
                        pass

    def _decode_data(self, v: Tuple, host_rx_ns: int) -> dict:
        (
            frame_id, calibration_id,
            sample_time_us, hall_time_us, imu_time_us,
            device_status, drop_flags,
            hx, hy, hz, ht,
            hall_conv_status, hall_device_status, imu_status, _reserved,
            imu_temp, gx, gy, gz, ax, ay, az,
            qw, qx, qy, qz,
        ) = v

        if self.clock.synced:
            sample_host_ns = self.clock.mcu_us_to_host_ns(sample_time_us)
            hall_host_ns = self.clock.mcu_us_to_host_ns(hall_time_us) if hall_time_us else 0
            imu_host_ns = self.clock.mcu_us_to_host_ns(imu_time_us) if imu_time_us else 0
        else:
            sample_host_ns = host_rx_ns
            hall_host_ns = host_rx_ns if hall_time_us else 0
            imu_host_ns = host_rx_ns if imu_time_us else 0

        hall_variant = 0
        if self.latest_health:
            hall_variant = int(self.latest_health.get("hall_variant", 0) or 0)
        if hall_variant not in (1, 2) and self.latest_hello:
            hall_variant = int(self.latest_hello.get("hall_variant", 0) or 0)

        hall_lsb_per_mT = HALL_LSB_PER_MT_BY_VARIANT.get(hall_variant)
        bx_mT = hx / hall_lsb_per_mT if hall_lsb_per_mT else math.nan
        by_mT = hy / hall_lsb_per_mT if hall_lsb_per_mT else math.nan
        bz_mT = hz / hall_lsb_per_mT if hall_lsb_per_mT else math.nan

        return {
            "frame_id": frame_id,
            "calibration_id": calibration_id,
            "sample_time_us": sample_time_us,
            "hall_time_us": hall_time_us,
            "imu_time_us": imu_time_us,
            "sample_host_ns": sample_host_ns,
            "hall_host_ns": hall_host_ns,
            "imu_host_ns": imu_host_ns,
            "host_rx_ns": host_rx_ns,
            "clock_synced": self.clock.synced,
            "sync_rtt_ms": self.clock.last_rtt_ns / 1e6 if math.isfinite(self.clock.last_rtt_ns) else math.nan,
            "device_status": device_status,
            "device_status_names": _bits(device_status, STATUS_BITS),
            "drop_flags": drop_flags,
            "drop_flag_names": _bits(drop_flags, DROP_BITS),

            # 原始码
            "hall_x_raw": hx, "hall_y_raw": hy, "hall_z_raw": hz, "hall_temp_raw": ht,
            "hall_conv_status": hall_conv_status,
            "hall_device_status": hall_device_status,
            "imu_status": imu_status,
            "imu_temp_raw": imu_temp,
            "gyro_x_raw": gx, "gyro_y_raw": gy, "gyro_z_raw": gz,
            "accel_x_raw": ax, "accel_y_raw": ay, "accel_z_raw": az,

            # 工程量，全部可由原始码重新计算
            "hall_variant": hall_variant,
            "hall_lsb_per_mT": hall_lsb_per_mT if hall_lsb_per_mT else math.nan,
            "bx_mT": bx_mT,
            "by_mT": by_mT,
            "bz_mT": bz_mT,
            "hall_temp_C": HALL_TEMP_T0_C + (ht - HALL_TEMP_T0_RAW) / HALL_TEMP_LSB_PER_C,
            "imu_temp_C": 25.0 + imu_temp / 256.0,
            "gx_rad_s": gx * (GYRO_MDPS_PER_LSB * 1e-3) * math.pi / 180.0,
            "gy_rad_s": gy * (GYRO_MDPS_PER_LSB * 1e-3) * math.pi / 180.0,
            "gz_rad_s": gz * (GYRO_MDPS_PER_LSB * 1e-3) * math.pi / 180.0,
            "ax_m_s2": ax * (ACCEL_MG_PER_LSB * 1e-3) * G0,
            "ay_m_s2": ay * (ACCEL_MG_PER_LSB * 1e-3) * G0,
            "az_m_s2": az * (ACCEL_MG_PER_LSB * 1e-3) * G0,
            "q_w": qw, "q_x": qx, "q_y": qy, "q_z": qz,
        }

    def drain(self, limit: int = 1000) -> List[dict]:
        if self.frames is None:
            return []
        out = []
        for _ in range(limit):
            try:
                out.append(self.frames.get_nowait())
            except queue.Empty:
                break
        return out


# ---------------------------------------------------------------------------
# Auto-detect helpers
# ---------------------------------------------------------------------------

# Any of these proves the port is the BRUSH-V1 bridge: DATA, HELLO, HEALTH,
# SYNC_RESP, plus the S3's supplemental 0x20 (adaptive output) / 0x21 (link).
_PROBE_HIT_TYPES = frozenset({PT_DATA, PT_HELLO, PT_HEALTH, PT_SYNC_RESP, 0x20, 0x21})


def active_probe(port: str, baud: int = 921600, timeout: float = 1.5) -> bool:
    """Open port, send one PING, look for any BRUSH-V1 reply.

    Any valid frame type counts: a running S3 whose C3 radio link is down skips
    PING -> HELLO (sendHello needs Meta) but still emits HEALTH every 1 s
    (HEALTH_INTERVAL_MS), which is why the default window covers 1.5 s.
    """
    if serial is None:
        return False
    try:
        ser = serial.Serial(port, baudrate=baud, timeout=0, write_timeout=0.1)
    except serial.SerialException:
        return False
    try:
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        time.sleep(0.1)  # let the ESP32-S3 ROM bootloader finish
        ser.write(build_packet(PT_CMD_PING, 0))
        ser.flush()
        deadline = time.monotonic() + timeout
        rx = bytearray()
        while time.monotonic() < deadline:
            n = ser.in_waiting
            if n:
                rx.extend(ser.read(n))
            while True:
                idx = rx.find(0)
                if idx <= 0:
                    break
                wire = bytes(rx[:idx])
                del rx[: idx + 1]
                if not wire:
                    continue
                try:
                    raw = cobs_decode(wire)
                    _hdr, payload = parse_raw_packet(raw)
                    ptype = _hdr[2]
                    if ptype in _PROBE_HIT_TYPES:
                        return True
                except Exception:
                    continue
            time.sleep(0.01)
        return False
    finally:
        ser.close()


def auto_detect_port(preferred_port: Optional[str] = None,
                     baud: int = 921600,
                     verbose: bool = True) -> Optional[str]:
    """Resolve the BRUSH-V1 bridge port, or None when nothing answers.

    Honors preferred_port; otherwise probes the ranked candidates.  A port that
    never replies is *not* returned: guessing used to yield a "connected but no
    data" state that was harder to diagnose than an honest empty result, and
    also picked up ports that only ever carry debug text.
    """
    if preferred_port:
        if is_debug_port(preferred_port) and verbose:
            print(f"[WARN] {preferred_port} is an Espressif native-USB debug port; "
                  "opening it can reset the chip into ROM download mode.")
        try:
            with serial.Serial(preferred_port, baudrate=baud, timeout=0.1) as s:
                s.close()
            if verbose:
                print(f"[OK] Using preferred port: {preferred_port}")
            return preferred_port
        except Exception as exc:
            if verbose:
                print(f"[WARN] Preferred port {preferred_port} unavailable: {exc}")
            print("[INFO] Falling back to auto-detect...")

    candidates = list_candidate_ports()
    if not candidates:
        if verbose:
            print("[WARN] No BRUSH-V1 candidate port found.  Visible ports:")
            for p in (list_ports.comports() if list_ports else []):
                print(f"   {p.device} - {p.description}")
            print("       Need the S3 USB-UART adapter (CH343/CH340/CP210x); the "
                  "ESP32 native-USB debug port carries no BRUSH-V1 stream.")
        return None

    if verbose:
        print(f"[INFO] Candidate ports ({len(candidates)}):")
        for d, desc in candidates:
            print(f"   {d} - {desc}")
        print(f"[INFO] Active probing @ {baud} baud...")

    for device, desc in candidates:
        if verbose:
            print(f"  -> probing {device} ({desc})")
        if active_probe(device, baud=baud):
            if verbose:
                print(f"  [HIT] {device}")
            return device

    if verbose:
        print("[WARN] No candidate answered BRUSH-V1; not guessing a port.  Probed: "
              + ", ".join(d for d, _ in candidates))
    return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="e-Brush host utilities")
    sub = parser.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list-ports", help="List candidate e-Brush serial ports")
    p_probe = sub.add_parser("probe", help="Active-probe every candidate port")
    p_probe.add_argument("--baud", type=int, default=921600)

    args = parser.parse_args()
    if args.cmd == "list-ports":
        for d, desc in list_candidate_ports():
            print(f"{d}\t{desc}")
    elif args.cmd == "probe":
        port = auto_detect_port(baud=args.baud)
        if port is None:
            print("FAIL: no BRUSH-V1 port answered")
            raise SystemExit(1)
        print(f"OK: {port}")
    else:
        parser.print_help()

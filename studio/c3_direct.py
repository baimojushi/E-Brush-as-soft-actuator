"""ESP32-C3 -> PC radio protocol decoder and BLE transport for E-Brush Studio.

No S3, COM port, USB bridge or Qt GUI is required. BLE callbacks process EVERY
sample before updating the latest-only GUI snapshot. Unit decoding is shared
with the original BRUSH-V1 receiver. The optional C3 time GATT characteristic
provides round-trip clock observations; without it camera sync stays UNSYNCED.
"""
from __future__ import annotations

import asyncio
import collections
import math
import socket
import struct
import threading
import time
from typing import Callable, Optional

try:
    from .brush_serial import (ClockDiscipline, RawLogger, build_packet,
                               decode_brush_data, DATA, HELLO, HEALTH_V1, SYNC_RESP,
                               PT_DATA, PT_HELLO, PT_HEALTH, PT_SYNC_RESP)
    from .brush_processing import Mahony6, AdaptiveFilter, TickUnwrapper
except ImportError:
    from brush_serial import (ClockDiscipline, RawLogger, build_packet,
                              decode_brush_data, DATA, HELLO, HEALTH_V1, SYNC_RESP,
                              PT_DATA, PT_HELLO, PT_HEALTH, PT_SYNC_RESP)
    from brush_processing import Mahony6, AdaptiveFilter, TickUnwrapper

BLE_NAME = 'EBrush-C3'
BLE_SERVICE = '8b962400-dad0-45a8-a8d5-4f45fd83aefe'
BLE_DATA_CHAR = '8b962401-dad0-45a8-a8d5-4f45fd83aefe'
BLE_TIME_CHAR = '8b962402-dad0-45a8-a8d5-4f45fd83aefe'

MAGIC = 0xC3E8
VERSION = 1
TYPE_SAMPLES, TYPE_META = 1, 2
RADIO_HEADER = struct.Struct('<HBBIHBB')
RADIO_SAMPLE = struct.Struct('<IIIIHH4h7h4B')
RADIO_META = struct.Struct('<8I4B')
TIME_REQ = struct.Struct('<I')
TIME_RESP = struct.Struct('<IQQ')
assert RADIO_HEADER.size == 12 and RADIO_SAMPLE.size == 46
assert RADIO_META.size == 36 and TIME_RESP.size == 20

STATUS_HALL_VALID = 1 << 1
STATUS_IMU_VALID = 1 << 5
STATUS_IMU_FRESH = 1 << 6
STATUS_POSE_VALID = 1 << 7
STATUS_POSE_YAW_RELATIVE = 1 << 8
DROP_TX_BACKPRESSURE = 1 << 5
HALL_UNITS = {1: 820.0, 2: 250.0, 3: 40960.0}


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x1021 if crc & 0x8000 else 0)) & 0xFFFF
    return crc


def make_radio_packet(packet_type: int, seq: int, payload: bytes, count: int) -> bytes:
    """Testing/diagnostics helper; layout is identical to EBrushLink::make_packet."""
    header = RADIO_HEADER.pack(MAGIC, VERSION, packet_type, seq, len(payload), count, 0)
    body = header + payload
    return body + struct.pack('<H', crc16_ccitt(body))


def parse_radio_packet(data: bytes):
    if not 14 <= len(data) <= 198:
        raise ValueError('radio_length')
    magic, version, typ, seq, payload_len, count, reserved = RADIO_HEADER.unpack_from(data)
    if magic != MAGIC or version != VERSION or reserved != 0:
        raise ValueError('radio_magic_version')
    if payload_len + RADIO_HEADER.size + 2 != len(data):
        raise ValueError('radio_payload_length')
    if typ == TYPE_SAMPLES:
        if not 1 <= count <= 4 or payload_len != count * RADIO_SAMPLE.size:
            raise ValueError('radio_sample_count')
    elif typ == TYPE_META:
        if count != 1 or payload_len != RADIO_META.size:
            raise ValueError('radio_meta_size')
    else:
        raise ValueError('radio_type')
    if struct.unpack_from('<H', data, len(data) - 2)[0] != crc16_ccitt(data[:-2]):
        raise ValueError('radio_crc16')
    return typ, seq, count, data[RADIO_HEADER.size:-2]


class C3DirectDecoder:
    """Transport-neutral decoder: feed raw C3 packets and optional BLE time replies.

    An on_frame(frame, v1_data_payload) handler may record a *derived* BRUSH-V1
    log. This is an explicitly normalized log, not the original BLE byte stream.
    """
    def __init__(self, on_frame: Optional[Callable] = None, on_meta: Optional[Callable] = None,
                 max_sync_rtt_ms: float = 25.0, transport: str = 'ble'):
        self.transport = transport
        self.stats = collections.Counter()
        self.clock = ClockDiscipline()
        self.unwrap = TickUnwrapper()
        self.pose = Mahony6()
        self.adaptive = AdaptiveFilter()
        self.latest: Optional[dict] = None
        self.latest_hello: Optional[dict] = None
        self.latest_health: Optional[dict] = None
        self.latest_motion: Optional[dict] = None
        self.latest_link: Optional[dict] = None
        self.last_protocol_error = ''
        self.last_io_error = ''
        self._boot_id = None
        self._last_bundle_seq = None
        self._last_frame_id = None
        self._last_imu_us = 0
        self._last_output_us = 0
        self._pose_ready = False
        self._sync_anchor_us = None
        self._last_clock_sync_ns = 0
        self.max_sync_rtt_ns = int(max_sync_rtt_ms * 1e6)
        self.on_frame = on_frame
        self.on_meta = on_meta

    def _reset_source(self):
        self.clock.reset()
        self.unwrap = TickUnwrapper()
        self.pose.reset()
        self.adaptive.reset()
        self._pose_ready = False
        self._last_frame_id = None
        self._last_imu_us = 0
        self._last_output_us = 0
        self._last_bundle_seq = None
        self._sync_anchor_us = None
        self._last_clock_sync_ns = 0
        self.latest = self.latest_motion = None

    def accept_clock_response(self, t0_ns: int, t3_ns: int,
                              c3_rx_us: int, c3_tx_us: int) -> bool:
        if not (0 < t0_ns < t3_ns and c3_rx_us <= c3_tx_us):
            self.stats['sync_invalid'] += 1
            return False
        rtt = (t3_ns - t0_ns) - (c3_tx_us - c3_rx_us) * 1000
        if not 0 < rtt <= self.max_sync_rtt_ns:
            self.stats['sync_high_rtt'] += 1
            return False
        self.clock.add(t0_ns, t3_ns, c3_rx_us, c3_tx_us)
        self._last_clock_sync_ns = t3_ns
        self._sync_anchor_us = c3_rx_us
        self.unwrap.anchor(c3_rx_us)
        self.stats['sync_resp'] += 1
        return True

    def _clock_trusted(self, host_rx_ns: int) -> bool:
        return bool(self.clock.synced and self._last_clock_sync_ns and
                    0 <= host_rx_ns - self._last_clock_sync_ns < 5_000_000_000)

    def accept_packet(self, data: bytes, host_rx_ns: Optional[int] = None) -> list[dict]:
        host_rx_ns = time.perf_counter_ns() if host_rx_ns is None else int(host_rx_ns)
        self.stats['radio_rx_bytes'] += len(data)
        try:
            typ, seq, count, payload = parse_radio_packet(data)
        except ValueError as exc:
            self.stats[str(exc)] += 1
            self.stats['packets_bad'] += 1
            self.last_protocol_error = str(exc)
            return []
        self.stats['packets_ok'] += 1
        self.last_protocol_error = ''
        # A reboot restarts the C3 sequence counter at zero. Inspect validated
        # META boot_id BEFORE sequence-order rejection, or a reboot with lower
        # sequence numbers could be mistaken for stale packets indefinitely.
        if typ == TYPE_META and self._boot_id is not None:
            incoming_boot_id = RADIO_META.unpack(payload)[0]
            if incoming_boot_id != self._boot_id:
                self.stats['device_reboot'] += 1
                self._reset_source()
                self._boot_id = None
        if self._last_bundle_seq is not None:
            delta = (seq - self._last_bundle_seq) & 0xFFFFFFFF
            if delta == 0 or delta > 0x7FFFFFFF:
                self.stats['reordered_packets'] += 1
                return []
            if 1 < delta < 10000:
                self.stats['bundle_seq_gap'] += delta - 1
        self._last_bundle_seq = seq
        if typ == TYPE_META:
            self._handle_meta(RADIO_META.unpack(payload), host_rx_ns)
            return []

        # Validate ALL timestamps before committing an epoch; a corrupt sample
        # in the tail of a bundle must not damage subsequent packets.
        pending = TickUnwrapper(self.unwrap.last, self.unwrap.full)
        unwrapped = []
        try:
            for i in range(count):
                unwrapped.append(pending.unwrap(RADIO_SAMPLE.unpack_from(payload, i*RADIO_SAMPLE.size)[1]))
        except ValueError:
            self.stats['nonmonotonic_timestamps'] += 1
            return []
        if self._sync_anchor_us is not None and self.unwrap.last is None:
            pending.anchor(self._sync_anchor_us)
            shift = pending.full - unwrapped[-1]
            unwrapped = [t + shift for t in unwrapped]
        self.unwrap = pending
        frames = []
        for i in range(count):
            raw = RADIO_SAMPLE.unpack_from(payload, i*RADIO_SAMPLE.size)
            item = self._process_sample(raw, unwrapped[i], host_rx_ns)
            if item is None:
                continue
            frame, packed = item
            frames.append(frame)
            self.latest = frame
            if self.on_frame:
                try:
                    self.on_frame(frame, packed)
                except Exception as exc:
                    self.stats['log_frame_error'] += 1
                    self.last_io_error = f'{type(exc).__name__}: {exc}'
        self.latest_link = {
            'packets_ok': self.stats['packets_ok'], 'packets_bad': self.stats['packets_bad'],
            'queue_drops': 0, 'frame_gaps': self.stats['frame_id_gap'],
            'duplicate_frames': self.stats['reordered_packets'] + self.stats['duplicate_frames'],
            'c3_transport_drops': (self.latest_health or {}).get('tx_drops', 0),
            'usb_drops': 0, 'ble_mtu': 0, 'link_type': (self.latest_health or {}).get('transport_id', 1), 'link_alive': 1,
            'host_rx_ns': host_rx_ns,
        }
        return frames

    def _handle_meta(self, values, host_rx_ns):
        boot_id = values[0]
        if self._boot_id is not None and boot_id != self._boot_id:
            self.stats['device_reboot'] += 1
            self._reset_source()
        self._boot_id = boot_id
        variant = values[8]
        self.latest_hello = {
            'boot_id': boot_id, 'capabilities': 0x1F, 'serial_baud': 0,
            'stream_hz': 200, 'fw_version': '2.x C3 direct',
            'hall_variant': variant, 'imu_who_am_i': values[9],
            'calibration_id': 0, 'host_rx_ns': host_rx_ns,
        }
        self.latest_health = {
            'uptime_us': 0, 'frames_intended': values[1],
            'frames_sent': self.stats['data_frames'], 'tx_drops': values[2],
            'rx_crc_errors_mcu': 0, 'rx_frame_errors_mcu': self.stats['bundle_seq_gap'],
            'hall_read_errors': values[3], 'imu_read_errors': values[4],
            'hall_reinits': values[5], 'imu_reinits': values[6],
            'hall_variant': variant, 'imu_who_am_i': values[9],
            'transport_id': values[10], 'last_sensor_time_us32': values[7],
            'health_schema': 'c3_meta_v1', 'host_rx_ns': host_rx_ns,
        }
        if self.on_meta:
            try:
                self.on_meta(host_rx_ns)
            except Exception as exc:
                self.stats['log_meta_error'] += 1
                self.last_io_error = f'{type(exc).__name__}: {exc}'

    def _process_sample(self, s, stamp_us, host_rx_ns):
        fid, _, status, drops, hall_age, imu_age = s[:6]
        gap = 0
        if self._last_frame_id is not None:
            delta = (fid - self._last_frame_id) & 0xFFFFFFFF
            if delta == 0 or delta > 10000:
                self.stats['duplicate_frames'] += 1
                return None
            gap = delta - 1
            self.stats['frame_id_gap'] += gap
        self._last_frame_id = fid
        if gap:
            drops |= DROP_TX_BACKPRESSURE
        if (status & STATUS_IMU_VALID) and (status & STATUS_IMU_FRESH):
            if self._last_imu_us:
                dt = (stamp_us - self._last_imu_us) * 1e-6
                kgyro = 35.0 * .001 * math.pi / 180.0
                kaccel = .122 * .001
                if self.pose.update(s[11]*kgyro, s[12]*kgyro, s[13]*kgyro,
                                    s[14]*kaccel, s[15]*kaccel, s[16]*kaccel, dt):
                    self._pose_ready = True
            self._last_imu_us = stamp_us
        if self._pose_ready and (status & STATUS_IMU_VALID):
            status |= STATUS_POSE_VALID | STATUS_POSE_YAW_RELATIVE
        hall_ts = stamp_us - hall_age if hall_age != 0xFFFF and hall_age <= stamp_us else 0
        imu_ts = stamp_us - imu_age if imu_age != 0xFFFF and imu_age <= stamp_us else 0
        packed = DATA.pack(
            fid, 0, stamp_us, hall_ts, imu_ts, status, drops,
            *s[6:10], s[17], s[18], s[19], 0,
            *s[10:17], *self.pose.q,
        )
        frame = decode_brush_data(DATA.unpack(packed), host_rx_ns, self.clock,
                                  self.latest_health, self.latest_hello)
        if not self._clock_trusted(host_rx_ns):
            frame['clock_synced'] = False
            frame['sample_host_ns'] = host_rx_ns
            frame['hall_host_ns'] = host_rx_ns if hall_ts else 0
            frame['imu_host_ns'] = host_rx_ns if imu_ts else 0
        # Never silently equate packet receipt and sampling for camera alignment.
        frame['transport'] = self.transport
        frame['sync_method'] = (f'{self.transport}_roundtrip' if frame['clock_synced']
                                else 'unsynced')
        variant = (self.latest_health or {}).get('hall_variant', 0)
        factor = HALL_UNITS.get(variant)
        hall = [s[6+i] / factor for i in range(3)] if factor else [0.0]*3
        dt = (stamp_us - self._last_output_us) * 1e-6 if self._last_output_us else .005
        self._last_output_us = stamp_us
        motion = self.adaptive.update(hall, bool(factor and (status & STATUS_HALL_VALID)),
                                      [s[14+i]*.000122 for i in range(3)],
                                      bool(status & STATUS_IMU_VALID), dt, gap)
        frame.update(hall_filtered_mT=motion.hall_mT, motion_accel_g=motion.motion_accel_g,
                     motion_energy=motion.motion_energy, jerk_g_per_s=motion.jerk_g_per_s,
                     motion_confidence=motion.confidence, motion_flags=motion.flags)
        self.latest_motion = {
            'frame_id': fid, 'flags': motion.flags, 'hall_filtered_mT': motion.hall_mT,
            'motion_accel_g': motion.motion_accel_g, 'motion_energy': motion.motion_energy,
            'jerk_g_per_s': motion.jerk_g_per_s, 'confidence': motion.confidence,
            'missing_frames': gap, 'host_rx_ns': host_rx_ns,
        }
        self.stats['data_frames'] += 1
        return frame, packed


class C3BleReceiver:
    """BrushSource-compatible threaded receiver using Bleak on Windows/Linux/macOS."""
    def __init__(self, name: str = BLE_NAME, address: str | None = None,
                 sync_period_s: float = 1.0, max_sync_rtt_ms: float = 25.0):
        self.name, self.address = name, address
        self.port = f'BLE:{address or name}'
        self.sync_period_s = sync_period_s
        self.decoder = C3DirectDecoder(on_frame=self._record_frame,
                                       on_meta=self._record_meta,
                                       max_sync_rtt_ms=max_sync_rtt_ms)
        self.stats = self.decoder.stats
        self._running = False
        self._connected = False
        self.connecting = False
        self._stop = threading.Event()
        self._thread = None
        self._record_lock = threading.RLock()
        self._logger = None
        self._recording_file = None
        self._last_recording_stop = None
        self._sync_observations = collections.deque(maxlen=8)
        self._sync_boot_id = None
        self._log_seq = 0
        self._nonce = 0
        self._pending_sync = {}
        self.time_sync_available = False
        self.last_io_error = ''

    @property
    def connected(self):
        return self._running and self._connected

    @property
    def latest(self):
        return self.decoder.latest

    @property
    def latest_hello(self):
        return self.decoder.latest_hello

    @property
    def latest_health(self):
        return self.decoder.latest_health

    @property
    def latest_link(self):
        link = self.decoder.latest_link
        if link:
            return dict(link, link_alive=int(self.connected))
        return None

    @property
    def last_protocol_error(self):
        return self.decoder.last_protocol_error

    def start(self):
        try:
            import bleak  # noqa: F401
        except ImportError as exc:
            raise RuntimeError('BLE 模式需要安装 bleak：pip install bleak') from exc
        if self._running:
            return
        self._stop.clear()
        self._running = True
        self.connecting = True
        self._thread = threading.Thread(target=self._thread_main, daemon=True,
                                        name='EBrushC3BLE')
        self._thread.start()

    def _thread_main(self):
        try:
            asyncio.run(self._ble_main())
        except Exception as exc:
            self.stats['ble_connection_error'] += 1
            self.last_io_error = f'{type(exc).__name__}: {exc}'
        finally:
            self._connected = False
            self.connecting = False
            self._running = False

    async def _ble_main(self):
        from bleak import BleakClient, BleakScanner
        if self.address:
            target = self.address
        else:
            target = await BleakScanner.find_device_by_name(self.name, timeout=8.0)
        if self._stop.is_set():
            return
        if target is None:
            raise RuntimeError(f'未找到 BLE 设备 {self.name}（检查 C3 是否正在广播）')
        # Bleak's native disconnected callback may be invoked on another thread;
        # only a flag is changed here. The asyncio task owns all BLE operations.
        client = BleakClient(target, disconnected_callback=lambda _: self._lost_connection())
        async with client:
            if self._stop.is_set():
                return
            self.stats['ble_connects'] += 1
            await client.start_notify(BLE_DATA_CHAR, self._on_data_notify)
            time_char = client.services.get_characteristic(BLE_TIME_CHAR)
            self.time_sync_available = time_char is not None
            if self.time_sync_available:
                await client.start_notify(BLE_TIME_CHAR, self._on_time_notify)
            self._connected = True
            self.connecting = False
            self.last_io_error = ''
            next_sync = time.monotonic()
            connected_at = time.monotonic()
            while not self._stop.is_set() and client.is_connected:
                now = time.monotonic()
                if self.time_sync_available and now >= next_sync:
                    self._nonce = (self._nonce + 1) & 0xFFFFFFFF
                    nonce = self._nonce
                    self._pending_sync[nonce] = time.perf_counter_ns()
                    if len(self._pending_sync) > 24:
                        self._pending_sync = dict(list(self._pending_sync.items())[-12:])
                    try:
                        await client.write_gatt_char(BLE_TIME_CHAR,
                                                     TIME_REQ.pack(nonce), response=True)
                    except Exception as exc:
                        self.stats['sync_write_error'] += 1
                        self.last_io_error = f'{type(exc).__name__}: {exc}'
                    next_sync = now + self.sync_period_s
                if (now - connected_at > 4.0 and not self.decoder.stats['data_frames']):
                    self.last_io_error = ('BLE 已连接但没有采样；检查订阅/传感器以及 '
                                          'C3 协商 MTU >= 63（单样本通知需要 60 字节）')
                await asyncio.sleep(.05)
            self._connected = False

    def _lost_connection(self):
        self._connected = False
        self.stats['ble_disconnects'] += 1

    def _on_data_notify(self, _sender, data: bytearray):
        self.stats['ble_notifications'] += 1
        self.decoder.accept_packet(bytes(data), time.perf_counter_ns())

    def _on_time_notify(self, _sender, data: bytearray):
        if len(data) != TIME_RESP.size:
            self.stats['sync_bad_payload'] += 1
            return
        nonce, t1, t2 = TIME_RESP.unpack(data)
        t3 = time.perf_counter_ns()
        t0 = self._pending_sync.pop(nonce, None)
        if t0 is not None:
            if self.decoder.accept_clock_response(t0, t3, t1, t2):
                self._record_time_observation(t0, t3, nonce, t1, t2)
        else:
            self.stats['sync_unmatched'] += 1

    def _record_time_observation(self, t0, t3, nonce, t1, t2):
        # A normalized .brlog needs the original host<->C3 roundtrips, not
        # just DATA timestamps. Legacy replay uses these SYNC_RESP frames to
        # reconstruct the same clock fit and align recorded RGB-D streams.
        with self._record_lock:
            observation = (t0, t3, nonce, t1, t2)
            self._sync_observations.append(observation)
            self._record_packet(PT_SYNC_RESP, SYNC_RESP.pack(t0, nonce, t1, t2), t3)

    def _record_packet(self, typ, payload, host_rx_ns):
        with self._record_lock:
            if self._logger is not None:
                wire = build_packet(typ, self._log_seq, payload)
                self._log_seq = (self._log_seq + 1) & 0xFFFFFFFF
                self._logger.write(host_rx_ns, wire[:-1])

    def _record_frame(self, frame, payload):
        self._record_packet(PT_DATA, payload, frame['host_rx_ns'])

    def _record_meta(self, host_rx_ns):
        h = self.latest_hello
        m = self.latest_health
        if h:
            with self._record_lock:
                if self._sync_boot_id is not None and self._sync_boot_id != h['boot_id']:
                    self._sync_observations.clear()
                self._sync_boot_id = h['boot_id']
            # Synthesized HELLO preserves the original BRUSH-V1 schema.
            hello = HELLO.pack(h['boot_id'], 0, 0x1F, 0, 200, 2, 1, 0,
                               h['hall_variant'], h['imu_who_am_i'], 0, 0)
            self._record_packet(PT_HELLO, hello, host_rx_ns)
        if m:
            health = HEALTH_V1.pack(0, m['frames_intended'], m['frames_sent'],
                                    m['tx_drops'], 0, m['rx_frame_errors_mcu'],
                                    m['hall_read_errors'], m['imu_read_errors'],
                                    m['hall_reinits'], m['imu_reinits'], 0, 0)
            self._record_packet(PT_HEALTH, health, host_rx_ns)

    def start_recording(self, path):
        from pathlib import Path
        with self._record_lock:
            if not self.connected:
                raise RuntimeError('BLE 尚未连接')
            if self._logger is not None:
                raise RuntimeError('已有采集会话正在录制')
            path = Path(path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                raise FileExistsError(str(path))
            self._logger = RawLogger(str(path))
            self._recording_file = str(path)
            self._last_recording_stop = None
            self._log_seq = 0
            # Prime the log with recent valid time observations so replay can
            # align from its FIRST data sample instead of waiting 4 seconds.
            for t0, t3, nonce, t1, t2 in self._sync_observations:
                self._record_packet(PT_SYNC_RESP, SYNC_RESP.pack(t0, nonce, t1, t2), t3)
            self._record_meta(time.perf_counter_ns())
            return {'path': str(path), 'data_frames_at_start': int(self.stats['data_frames'])}

    def stop_recording(self):
        with self._record_lock:
            logger, self._logger = self._logger, None
            path, self._recording_file = self._recording_file, None
            if logger:
                logger.close()
            if path is not None:
                self._last_recording_stop = {
                    'path': path, 'data_frames': int(self.stats['data_frames'])}
            return dict(self._last_recording_stop or {
                'path': None, 'data_frames': int(self.stats['data_frames'])})

    def close(self):
        self._stop.set()
        self._running = False
        thr = self._thread
        if thr and thr.is_alive() and thr is not threading.current_thread():
            thr.join(timeout=2.0)
        self._connected = False
        self.connecting = False
        self.stop_recording()


class C3WifiReceiver(C3BleReceiver):
    """Optional S3-free UDP receiver for C3 `c3_wifi_pc` + Windows hotspot.

    Host must enable the EBrush-PC hotspot, whose IPv4 gateway/address is
    assumed to be 192.168.137.1 by the matching C3 firmware. No ad-hoc AP is
    created by Studio itself. UDP packets can be lost/reordered; CRC and frame
    IDs remain mandatory. Wi-Fi is never enabled in the default BLE firmware.
    """
    def __init__(self, port: int = 5568, sync_port: int = 5569):
        super().__init__()
        self.port = f'UDP:0.0.0.0:{port}'
        self.udp_port = port
        self.sync_port = sync_port
        self._socket = None
        self.decoder.transport = 'wifi'
        self._last_packet_ns = 0
        self.time_sync_available = True

    @property
    def connected(self):
        return bool(self._running and self._connected and self._last_packet_ns and
                    time.perf_counter_ns() - self._last_packet_ns < 3_000_000_000)

    def start(self):
        if self._running:
            return
        self._stop.clear()
        self._running = True
        self.connecting = True
        self._thread = threading.Thread(target=self._udp_main, name='EBrushC3UDP', daemon=True)
        self._thread.start()

    def _udp_main(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket = sock
        try:
            sock.settimeout(.2)
            sock.bind(('0.0.0.0', self.udp_port))
            next_sync = time.monotonic()
            peer_ip = None
            while not self._stop.is_set():
                now = time.monotonic()
                if peer_ip and now >= next_sync:
                    self._nonce = (self._nonce + 1) & 0xFFFFFFFF
                    nonce = self._nonce
                    self._pending_sync[nonce] = time.perf_counter_ns()
                    if len(self._pending_sync) > 24:
                        self._pending_sync = dict(list(self._pending_sync.items())[-12:])
                    try:
                        sock.sendto(TIME_REQ.pack(nonce), (peer_ip, self.sync_port))
                    except OSError as exc:
                        self.stats['sync_write_error'] += 1
                        self.last_io_error = str(exc)
                    next_sync = now + self.sync_period_s
                try:
                    data, remote = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                stamp = time.perf_counter_ns()
                if len(data) == TIME_RESP.size:
                    nonce, t1, t2 = TIME_RESP.unpack(data)
                    t0 = self._pending_sync.pop(nonce, None)
                    if t0 is not None:
                        if self.decoder.accept_clock_response(t0, stamp, t1, t2):
                            self._record_time_observation(t0, stamp, nonce, t1, t2)
                    else:
                        self.stats['sync_unmatched'] += 1
                    continue
                self._last_packet_ns = stamp
                self._connected = True
                self.connecting = False
                peer_ip = remote[0]
                self.stats['udp_datagrams'] += 1
                self.decoder.accept_packet(data, stamp)
        except Exception as exc:
            self.stats['udp_io_error'] += 1
            self.last_io_error = f'{type(exc).__name__}: {exc}'
        finally:
            sock.close()
            self._socket = None
            self._connected = False
            self.connecting = False
            self._running = False

    def close(self):
        self._stop.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        super().close()

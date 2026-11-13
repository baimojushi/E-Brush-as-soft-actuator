"""Pure-Python offline protocol and behavior regressions for C3 -> PC Studio.

Runs without BLE hardware, Windows, PySide6, pyserial or bleak; exercises
real wire layouts, CRC, metadata, timestamp wrap, clock fit, pose/filters,
source-switch state, and legacy-format file recordings.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import types
import unittest

STUDIO = Path(__file__).resolve().parents[1] / 'studio'
sys.path.insert(0, str(STUDIO))

import brush_serial as legacy
import c3_direct as c3
from brush_processing import Mahony6, AdaptiveFilter, TickUnwrapper
from sources import BrushSource

VALID = ((1 << 0) | (1 << 1) | (1 << 4) | (1 << 5) | (1 << 6))


def sample(fid=1, tick=100000, hall=(410, -820, 1230), status=VALID,
           gyros=(2, -4, 6), accel=(0, 0, 8192), hall_age=100, imu_age=50):
    return c3.RADIO_SAMPLE.pack(
        fid, tick & 0xffffffff, status, 0, hall_age, imu_age,
        *hall, 0, 0, *gyros, *accel, 0, 0, 0, 0)


def packet(fid=1, tick=100000, seq=2, num=1, **kwargs):
    payload = b''.join(sample(fid+i, tick + 5000*i, **kwargs) for i in range(num))
    return c3.make_radio_packet(c3.TYPE_SAMPLES, seq, payload, num)


def metadata(boot=123, variant=3, seq=1, total=0, drops=0):
    meta = c3.RADIO_META.pack(boot, total, drops, 0, 0, 0, 0, 0,
                              variant, 0x6b, 1, 0)
    return c3.make_radio_packet(c3.TYPE_META, seq, meta, 1)


class TestWire(unittest.TestCase):
    def test_sizes_and_crc(self):
        for num in range(1, 5):
            p = packet(num=num)
            self.assertEqual(len(p), 14 + 46*num)
            self.assertEqual(c3.parse_radio_packet(p)[:3], (c3.TYPE_SAMPLES, 2, num))
        p = metadata()
        self.assertEqual(len(p), 50)
        self.assertEqual(c3.parse_radio_packet(p)[0], c3.TYPE_META)
        with self.assertRaisesRegex(ValueError, 'radio_crc16'):
            c3.parse_radio_packet(p[:-3] + bytes([p[-3] ^ 4]) + p[-2:])
        for bad in (b'', b'BAD', b'\x00'*60, p + b'\0'):
            with self.assertRaises(ValueError):
                c3.parse_radio_packet(bad)
        hdr = c3.RADIO_HEADER.pack(c3.MAGIC, 1, c3.TYPE_SAMPLES, 8, 0, 1, 0)
        with self.assertRaisesRegex(ValueError, 'radio_sample_count'):
            c3.parse_radio_packet(hdr + struct.pack('<H', c3.crc16_ccitt(hdr)))

    def test_bad_packet_leaves_valid_state_intact(self):
        dec = c3.C3DirectDecoder()
        dec.accept_packet(metadata())
        a = dec.accept_packet(packet())
        self.assertEqual(len(a), 1)
        bad = bytearray(packet(fid=3, seq=3))
        bad[-1] ^= 0x80
        self.assertEqual(dec.accept_packet(bad), [])
        self.assertEqual(dec.stats['packets_bad'], 1)
        self.assertEqual(dec.stats['data_frames'], 1)
        self.assertEqual(len(dec.accept_packet(packet(fid=2, seq=3, tick=105000))), 1)
        self.assertEqual(dec.stats['frame_id_gap'], 0)

    def test_sample_fields_filter_quaternion_and_meta_mmc_units(self):
        dec = c3.C3DirectDecoder()
        dec.accept_packet(metadata())
        frames = dec.accept_packet(packet(num=4), host_rx_ns=time.perf_counter_ns())
        self.assertEqual(len(frames), 4)
        first, last = frames[0], frames[-1]
        self.assertEqual(first['hall_variant'], 3)
        self.assertAlmostEqual(first['bx_mT'], 410 / 40960)
        self.assertAlmostEqual(first['by_mT'], -820 / 40960)
        self.assertTrue(math.isnan(first['hall_temp_C']))
        self.assertEqual(last['frame_id'], 4)
        self.assertFalse(first['clock_synced'])
        self.assertIn('pose_valid', last['device_status_names'])
        self.assertAlmostEqual(sum(last[k]**2 for k in ('q_w','q_x','q_y','q_z')), 1, places=5)
        self.assertEqual(dec.stats['data_frames'], 4)
        self.assertEqual(dec.latest_motion['frame_id'], 4)
        self.assertTrue(all(math.isfinite(x) for x in last['hall_filtered_mT']))
        self.assertEqual(last['transport'], 'ble')
        self.assertEqual(last['sync_method'], 'unsynced')

    def test_data_loss_duplicates_and_gap_flags(self):
        dec = c3.C3DirectDecoder()
        dec.accept_packet(metadata())
        dec.accept_packet(packet(seq=2))
        self.assertEqual(dec.accept_packet(packet(seq=2)), [])
        self.assertEqual(dec.stats['reordered_packets'], 1)
        dec.accept_packet(packet(fid=4, seq=4, tick=115000))
        self.assertEqual(dec.stats['frame_id_gap'], 2)
        self.assertEqual(dec.stats['bundle_seq_gap'], 1)
        self.assertIn('tx_backpressure', dec.latest['drop_flag_names'])
        self.assertEqual(dec.latest_motion['missing_frames'], 2)

    def test_tick_rollover_and_reboot_with_sequence_reset(self):
        dec = c3.C3DirectDecoder()
        dec.accept_packet(metadata(boot=10, seq=100))
        x = dec.accept_packet(packet(fid=0xfffffff0, tick=0xfffff000, seq=101))[0]
        y = dec.accept_packet(packet(fid=0xfffffff1, tick=0x00000388, seq=102))[0]
        self.assertGreater(y['sample_time_us'], x['sample_time_us'])
        self.assertEqual(y['sample_time_us'], 0x100000388)
        dec.accept_packet(metadata(boot=11, seq=0))
        self.assertEqual(dec.stats['device_reboot'], 1)
        z = dec.accept_packet(packet(fid=1, tick=5000, seq=1))[0]
        self.assertEqual(z['sample_time_us'], 5000)
        self.assertEqual(dec.latest_hello['boot_id'], 11)

    def test_clock_fit_and_rtt_rejection(self):
        dec = c3.C3DirectDecoder()
        dec.accept_packet(metadata(seq=1))
        origin = 10_000_000_000_000
        for i in range(8):
            t = 10_000_000 + i*1_000_000
            host_time = origin + t*1000
            self.assertTrue(dec.accept_clock_response(
                host_time - 4_000_000, host_time + 4_000_000,
                t, t + 100))
        t = 18_000_000
        host = origin + t*1000
        z = dec.accept_packet(packet(fid=1, tick=t, seq=2), host_rx_ns=host + 7_000_000)[0]
        self.assertTrue(z['clock_synced'])
        self.assertEqual(z['sync_method'], 'ble_roundtrip')
        self.assertLess(abs(z['sample_host_ns'] - host), 5_000_000)
        self.assertFalse(dec.accept_clock_response(host - 100_000_000, host,
                                                   t, t + 100))
        self.assertEqual(dec.stats['sync_high_rtt'], 1)
        stale = dec.accept_packet(packet(fid=2, tick=t+5000, seq=3),
                                  host_rx_ns=host + 7_000_000_000)[0]
        self.assertFalse(stale['clock_synced'])
        self.assertEqual(stale['sync_method'], 'unsynced')
        self.assertEqual(stale['sample_host_ns'], stale['host_rx_ns'])
        # No time GATT on old C3 -> never claim camera synchronization.
        old = c3.C3DirectDecoder()
        old.accept_packet(metadata())
        self.assertFalse(old.accept_packet(packet())[0]['clock_synced'])

    def test_nonmonotonic_bundle_discard(self):
        dec = c3.C3DirectDecoder()
        dec.accept_packet(metadata())
        dec.accept_packet(packet())
        backwards = c3.make_radio_packet(
            c3.TYPE_SAMPLES, 3, sample(2, 110000) + sample(3, 106000), 2)
        self.assertEqual(dec.accept_packet(backwards), [])
        self.assertEqual(dec.stats['nonmonotonic_timestamps'], 1)
        self.assertEqual(len(dec.accept_packet(packet(2, 105000, 4))), 1)


class TestProcessing(unittest.TestCase):
    def test_stationary_mahony_pose(self):
        p = Mahony6()
        self.assertFalse(p.update(0,0,0,0,0,1, 0.1))
        for _ in range(200):
            self.assertTrue(p.update(0.001,-.003,.002,0,0,1, 0.005))
        self.assertAlmostEqual(sum(v*v for v in p.q), 1.0, places=6)
        self.assertGreater(p.q[0], 0.99)

    def test_adaptive_filter_drop_penalty_and_recovery(self):
        f = AdaptiveFilter()
        clean = f.update((1, 2, 3), True, (0,0,1), True, .005, 0)
        self.assertEqual(clean.flags, 0)
        bad = f.update((1, 2, 3), False, (0,0,1), False, .005, 5)
        self.assertEqual(bad.flags & 7, 7)
        self.assertLess(bad.confidence, clean.confidence)
        f.reset()
        self.assertEqual(f.confidence_lp, 0)

    def test_tick_unwrapper_anchor(self):
        u = TickUnwrapper()
        self.assertEqual(u.unwrap(10), 10)
        u.anchor((1 << 32) + 50)
        self.assertEqual(u.full, (1 << 32) + 10)
        self.assertEqual(u.unwrap(80), (1 << 32) + 80)


class TestSourceAndRecording(unittest.TestCase):
    def test_serial_ble_wifi_source_creation_without_external_packages(self):
        base = STUDIO.parent
        src = BrushSource(base, transport='ble', auto_reconnect=False)
        self.assertEqual(src.transport, 'ble')
        self.assertFalse(src.status()['connected'])
        src.switch_transport('wifi')
        self.assertEqual(src.status()['transport'], 'wifi')
        src.close()

    def test_legacy_brlog_wire_and_disconnect_path_persistence(self):
        receiver = c3.C3BleReceiver()
        receiver._running = receiver._connected = True  # simulate subscribed link
        receiver.decoder.accept_packet(metadata())
        # The legacy decoder requires >=4 time sync responses to map C3 time
        # to the PC clock; the direct recorder seeds the log with observations.
        origin = 1_000_000_000_000
        for i in range(8):
            tick = 10_000_000 + 1_000_000*i
            host_ns = origin + tick*1000
            receiver._record_time_observation(
                host_ns - 4_000_000, host_ns + 4_000_000,
                i+1, tick, tick+100)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'capture.brlog'
            start = receiver.start_recording(path)
            self.assertEqual(start['data_frames_at_start'], 0)
            receiver.decoder.accept_packet(packet(num=4))
            stop = receiver.stop_recording()
            self.assertEqual(stop['data_frames'], 4)
            # Windows Path.resolve() may yield the 8.3 short-name form for
            # temp dirs (e.g. ADMINI~1 vs Administrator); compare by the
            # underlying filesystem object instead of the literal string.
            self.assertTrue(os.path.samefile(stop['path'], path))
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b'BRUSH_BRL1\n'))
            offset = len(b'BRUSH_BRL1\n')
            seen = []
            legacy_clock = legacy.ClockDiscipline()
            while offset < len(raw):
                host_ns, n = struct.unpack_from('<QH', raw, offset)
                offset += 10
                wire = raw[offset:offset+n]
                offset += n
                hdr, payload = legacy.parse_raw_packet(legacy.cobs_decode(wire))
                self.assertGreater(host_ns, 0)
                seen.append(hdr[2])
                if hdr[2] == legacy.PT_SYNC_RESP:
                    t0, nonce, rx_us, tx_us = legacy.SYNC_RESP.unpack(payload)
                    legacy_clock.add(t0, host_ns, rx_us, tx_us)
                if hdr[2] == legacy.PT_DATA:
                    self.assertEqual(len(payload), legacy.DATA.size)
                    self.assertTrue(legacy_clock.synced)
                    # Replay can retain original PC-C3 clock mapping.
                    v = legacy.DATA.unpack(payload)
                    self.assertGreater(v[2], 0)
            self.assertEqual(seen.count(legacy.PT_DATA), 4)
            self.assertEqual(seen.count(legacy.PT_SYNC_RESP), 8)
            self.assertIn(legacy.PT_HELLO, seen)
            self.assertIn(legacy.PT_HEALTH, seen)
            with self.assertRaises(FileExistsError):
                receiver.start_recording(path)
            # Simulate disconnect while recording; closing must not lose file path.
            second = Path(tmp) / 'second.brlog'
            receiver.start_recording(second)
            receiver.close()
            # compare by filesystem object: Path.resolve() may return the
            # Windows 8.3 short-name form for temp dirs (ADMINI~1 vs Administrator)
            self.assertTrue(os.path.samefile(receiver.stop_recording()['path'], second))
            self.assertTrue(second.is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)

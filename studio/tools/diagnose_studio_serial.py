#!/usr/bin/env python3
"""BRUSH-V1 (S3 USB) 5 s transport/IMU/Hall diagnostic, no GUI required.

Windows: py -3 tools\\diagnose_studio_serial.py --port COM7 --seconds 5
Only pyserial is required; close Studio / serial monitor before running.
"""
from __future__ import annotations
import argparse
import math
import sys
import time
from pathlib import Path

# Add the studio directory to path (where brush_serial.py is)
studio_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(studio_dir))

from brush_serial import auto_detect_port, BrushSerial as WirelessBrushSerial


def main() -> int:
    p = argparse.ArgumentParser(description='Check BRUSH-V1 CRC, DATA, Hall and IMU via ESP32-S3 USB')
    p.add_argument('--port', help='S3 USB serial port, e.g. COM7; do not choose C3 debug COM port')
    p.add_argument('--baud', type=int, default=921600)
    p.add_argument('--seconds', type=float, default=5.)
    args = p.parse_args()
    try:
        port = args.port or auto_detect_port(verbose=True)
        rx = WirelessBrushSerial(port, baud=args.baud)
        rx.start()
    except Exception as exc:
        print(f'[ERROR] could not open S3 port: {type(exc).__name__}: {exc}')
        return 2

    try:
        end = time.monotonic() + max(1., args.seconds)
        last_tick = 0.
        last_frame = None
        print(f'[OPEN] {port} at {args.baud}, BRUSH-V1 binary, {args.seconds:g} sec')
        while time.monotonic() < end:
            now = time.monotonic()
            if now >= last_tick + 1.:
                last_tick = now
                b = rx.latest
                if b:
                    fid = b.get('frame_id')
                    flags = '|'.join(b.get('device_status_names') or []) or '-'
                    q = [float(b[k]) for k in ('q_w', 'q_x', 'q_y', 'q_z')]
                    hall = [b.get(k) for k in ('bx_mT', 'by_mT', 'bz_mT')]
                    print(f'[DATA] frame={fid}  flags={flags}')
                    print('       Hall mT=', hall, 'raw=', [b.get(k) for k in
                          ('hall_x_raw', 'hall_y_raw', 'hall_z_raw')],
                          'variant=', b.get('hall_variant'))
                    print('       IMU raw accel=', [b.get(k) for k in
                          ('accel_x_raw', 'accel_y_raw', 'accel_z_raw')],
                          'gyro=', [b.get(k) for k in
                          ('gyro_x_raw', 'gyro_y_raw', 'gyro_z_raw')],
                          'quat=', [round(v, 5) for v in q])
                    if 'imu_present' in b.get('device_status_names', []) and \
                            'imu_valid' not in b.get('device_status_names', []):
                        print('  [WARN] IMU present but VALID bit is absent. Update C3 firmware.')
                    if not all(math.isfinite(float(x)) for x in hall):
                        print('  [WARN] Hall scaling unavailable. Check HELLO / hall_variant.')
                    if last_frame == fid:
                        print('  [WARN] No new DATA frame for this second.')
                    last_frame = fid
                else:
                    print('[WAIT] no valid DATA yet. Serial port open != binary protocol verified.')
            time.sleep(.02)
        print('[HELLO]', rx.latest_hello)
        print('[HEALTH]', rx.latest_health)
        print('[LINK]', getattr(rx, 'latest_link', None))
        print('[STATS]', dict(rx.stats))
        if not rx.stats.get('data_frames'):
            print('[FAIL] No CRC-checked 82-byte DATA: check S3 port, firmware, or device protocol.')
            return 1
        print(f'[OK] Valid DATA frames: {rx.stats["data_frames"]}. ' 
              'Rotate brush and confirm quaternion changes when IMU_VALID is present.')
        return 0
    finally:
        rx.close()


if __name__ == '__main__':
    raise SystemExit(main())

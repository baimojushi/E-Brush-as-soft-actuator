import sys
sys.path.insert(0, r'D:\项目\交互艺术\书法微更新\e-Brush\studio')
from brush_serial import BrushSerial, PT_DATA, DATA
import time

bs = BrushSerial('COM7', 921600)
bs.start()
print('BrushSerial started', flush=True)

time.sleep(1.5)

# Get raw payload from one frame
for i in range(3):
    time.sleep(0.5)
    if bs.latest:
        # Access the raw frame data - we need to capture it differently
        pass

# Instead, let's manually capture one frame and inspect raw payload
import serial
from brush_serial import cobs_decode, parse_raw_packet, build_packet, PT_CMD_PING, PT_CMD_START

ser = serial.Serial('COM7', 921600, timeout=0, write_timeout=0.1)
ser.reset_input_buffer()
ser.reset_output_buffer()

# Send PING
cmd_ping = build_packet(PT_CMD_PING, 0)
ser.write(cmd_ping)
ser.flush()
time.sleep(0.3)

# Send START
cmd_start = build_packet(PT_CMD_START, 1)
ser.write(cmd_start)
ser.flush()
time.sleep(0.3)

# Capture raw frames
rx = bytearray()
deadline = time.time() + 2
while time.time() < deadline:
    n = ser.in_waiting
    if n:
        rx.extend(ser.read(n))
    while True:
        idx = rx.find(0)
        if idx <= 0: break
        wire = bytes(rx[:idx])
        del rx[: idx + 1]
        if not wire: continue
        try:
            raw = cobs_decode(wire)
            header, payload = parse_raw_packet(raw)
            if header[2] == PT_DATA:
                print(f'Raw payload ({len(payload)} bytes):')
                print(' '.join(f'{b:02x}' for b in payload))
                # Also print as different interpretations
                import struct
                # Current DATA format
                vals = DATA.unpack(payload)
                print(f'Current unpack: frame_id={vals[0]}, cal={vals[1]}')
                print(f'  times: sample={vals[2]}, hall={vals[3]}, imu={vals[4]}')
                print(f'  status={vals[5]}, drops={vals[6]}')
                print(f'  hall_raw: hx={vals[7]}, hy={vals[8]}, hz={vals[9]}, ht={vals[10]}')
                print(f'  hall_conv={vals[11]}, hall_dev={vals[12]}, imu_status={vals[13]}, res={vals[14]}')
                print(f'  imu_temp={vals[15]}, gx={vals[16]}, gy={vals[17]}, gz={vals[18]}')
                print(f'  ax={vals[19]}, ay={vals[20]}, az={vals[21]}')
                print(f'  qw={vals[22]}, qx={vals[23]}, qy={vals[24]}, qz={vals[25]}')
                break
        except Exception as e:
            pass
    time.sleep(0.01)

ser.close()
bs.close()
print('DONE', flush=True)
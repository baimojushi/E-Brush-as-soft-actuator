import serial
import time

print("Opening COM7...", flush=True)
ser = serial.Serial('COM7', 921600, timeout=2.0)
print("Opened", flush=True)

ser.reset_input_buffer()
print("Buffer reset", flush=True)

print("Waiting 3s for spontaneous data...", flush=True)
time.sleep(3)

n = ser.in_waiting
print(f"in_waiting: {n}", flush=True)

if n:
    d = ser.read(n)
    print(f"data: {d.hex()}", flush=True)
else:
    print("No spontaneous data", flush=True)

ser.close()
print("Closed", flush=True)
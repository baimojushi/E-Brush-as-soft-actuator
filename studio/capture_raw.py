import serial
import time

print("Opening COM7...", flush=True)
ser = serial.Serial('COM7', 921600, timeout=0.01)
print("Opened", flush=True)

ser.reset_input_buffer()
print("Buffer reset", flush=True)

# Wait for some data
print("Waiting 2s for data...", flush=True)
time.sleep(2)

# Read all available
data = bytearray()
while ser.in_waiting:
    data.extend(ser.read(ser.in_waiting))
    time.sleep(0.001)

print(f"Total bytes: {len(data)}", flush=True)
print(f"First 200 bytes: {data[:200].hex()}", flush=True)
print(f"Last 50 bytes: {data[-50:].hex()}", flush=True)

# Count frames (split by 0x00)
frames = data.split(b'\x00')
print(f"Frame count: {len(frames)}", flush=True)
for i, f in enumerate(frames[:10]):
    if f:
        print(f"  Frame {i}: {len(f)} bytes, code={f[0]}", flush=True)

ser.close()
print("Done", flush=True)
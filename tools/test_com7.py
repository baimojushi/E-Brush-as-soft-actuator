import serial
from serial.tools import list_ports

ports = [(p.device, p.description, p.hwid) for p in list_ports.comports()]
print('Available ports:')
for p in ports:
    print(f'  {p[0]} | {p[1]} | {p[2]}')
print()
try:
    s = serial.Serial('COM7', 921600, timeout=0.2)
    print('COM7: OPENED OK')
    s.close()
except Exception as e:
    print(f'COM7 ERROR: {e}')
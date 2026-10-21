import sys
sys.path.insert(0, r'D:\项目\交互艺术\书法微更新\e-Brush\studio')
from brush_serial import cobs_decode, parse_raw_packet, PT_DATA, DATA, HEADER

# Use the first frame from the fresh capture (99 bytes COBS)
# First frame: 06425201015201010453d508031e0e01010101010657923bb60501010657923bb60501010657923bb605010103530c01010101010210020c0204048445b10203046efe0404efff040765df86ff72020103803f010101010101010101010105f48726a600

test_frame = bytes.fromhex('06425201015201010453d508031e0e01010101010657923bb60501010657923bb60501010657923bb605010103530c01010101010210020c0204048445b10203046efe0404efff040765df86ff72020103803f010101010101010101010105f48726a600')

print(f'Test frame: {len(test_frame)} bytes')
print(f'Ends with 00: {test_frame.endswith(b"\\x00")}')

# Split by 00
parts = test_frame.split(b'\x00')
print(f'Parts: {len(parts)}')
for i, p in enumerate(parts[:-1]):
    if not p: continue
    print(f'  Part {i}: {len(p)} bytes, code={p[0]}')
    try:
        decoded = cobs_decode(p)
        print(f'  Decoded: {len(decoded)} bytes')
        header, payload = parse_raw_packet(decoded)
        ptype = header[2]
        print(f'  Packet type: {ptype:#04x} (DATA={PT_DATA:#04x})')
        print(f'  Header plen: {header[3]}')
        print(f'  Payload size: {len(payload)}')
        if ptype == PT_DATA:
            vals = DATA.unpack(payload)
            print(f'  DATA unpacked successfully!')
            print(f'  frame_id={vals[0]}, cal_id={vals[1]}')
            print(f'  q=({vals[20]:.3f},{vals[21]:.3f},{vals[22]:.3f},{vals[23]:.3f})')
            print(f'  hall_raw=({vals[8]},{vals[9]},{vals[10]})')
            print(f'  gyro=({vals[14]},{vals[15]},{vals[16]})')
            print(f'  accel=({vals[17]},{vals[18]},{vals[19]})')
    except Exception as e:
        print(f'  Error: {e}')
        import traceback
        traceback.print_exc()
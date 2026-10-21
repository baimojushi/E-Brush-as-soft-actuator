import sys
sys.path.insert(0, r'D:\项目\交互艺术\书法微更新\e-Brush\studio')
from brush_serial import cobs_decode, parse_raw_packet, PT_DATA, DATA, PT_HELLO, HELLO, PT_HEALTH, HEALTH_V11, PT_SYNC_RESP, SYNC_RESP

# Test with one complete COBS frame from our capture
test_frame = bytes.fromhex('064252010152010104ba8d0703d30d0101010101063c7932c4040101063c7932c4040101063c7932c404010103530c01010101010224023006f8ffac45d102030459ff0304eeff010766df7aff7b020103803f0101010101010101010101010101010101010105a4a08adc00')

print('Test frame:', test_frame.hex())
print('Length:', len(test_frame))

# Split by 00
parts = test_frame.split(b'\x00')
print(f'Parts: {len(parts)}')
for i, p in enumerate(parts[:-1]):
    if not p: continue
    print(f'  Part {i}: {len(p)} bytes, starts with {p[0]:#04x}')
    try:
        decoded = cobs_decode(p)
        print(f'  Decoded: {len(decoded)} bytes')
        header, payload = parse_raw_packet(decoded)
        ptype = header[2]
        print(f'  Packet type: {ptype:#04x}')
        if ptype == PT_DATA:
            vals = DATA.unpack(payload)
            print(f'  DATA frame_id={vals[0]} q=({vals[20]:.3f},{vals[21]:.3f},{vals[22]:.3f},{vals[23]:.3f}) hall_raw=({vals[8]},{vals[9]},{vals[10]})')
        elif ptype == PT_HELLO:
            vals = HELLO.unpack(payload)
            print(f'  HELLO: boot_id={vals[0]}, fw={vals[5]}.{vals[6]}.{vals[7]}')
        elif ptype == PT_HEALTH:
            print(f'  HEALTH: {len(payload)} bytes')
        elif ptype == PT_SYNC_RESP:
            vals = SYNC_RESP.unpack(payload)
            print(f'  SYNC_RESP: host_tx_ns={vals[0]}, nonce={vals[1]}, mcu_rx_us={vals[2]}, mcu_tx_us={vals[3]}')
    except Exception as e:
        print(f'  Error: {e}')
        import traceback
        traceback.print_exc()

print('DONE')
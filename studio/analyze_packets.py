import sys
sys.path.insert(0, r'D:\项目\交互艺术\书法微更新\e-Brush\studio')
from brush_serial import cobs_decode, parse_raw_packet, PT_DATA

# Full captured raw COBS data
raw_cobs = bytes.fromhex('064252010152010104ba8d0703d30d0101010101063c7932c4040101063c7932c4040101063c7932c404010103530c01010101010224023006f8ffac45d100030059ff0300eeff010066df7aff7b020000803f0000000000000000000000000000000000000000a4a08adc00')

print(f'Total raw: {len(raw_cobs)} bytes')

# Split by 0x00
frames = raw_cobs.split(b'\x00')
print(f'Number of frames: {len(frames)}')

all_decoded = []
for i, frame in enumerate(frames[:-1]):  # last is empty after final 00
    if not frame:
        continue
    print(f'\nFrame {i}: {len(frame)} bytes, code={frame[0]}')
    try:
        decoded = cobs_decode(frame)
        print(f'  COBS decoded: {len(decoded)} bytes')
        all_decoded.append(decoded)
    except Exception as e:
        print(f'  COBS error: {e}')

print(f'\n=== Full concatenated decoded data ===')
full = b''.join(all_decoded)
print(f'Total decoded: {len(full)} bytes')
print(full.hex())

# Now try to parse as multiple packets - CORRECT header format
print(f'\n=== Attempting to parse as multiple packets ===')
pos = 0
pkt_num = 0
import struct
while pos < len(full):
    if pos + 12 > len(full):  # HEADER is 12 bytes: HBBHHI = 2+1+1+2+2+4 = 12
        print(f'Not enough for header at pos {pos}')
        break
    
    # Parse header - HEADER = struct.Struct('<HBBHHI') = 12 bytes
    header = struct.unpack('<HBBHHI', full[pos:pos+12])
    magic, version, ptype, plen, flags, seq = header
    print(f'\nPacket {pkt_num} at pos {pos}: magic={magic:#06x}, ver={version}, type={ptype:#04x}, plen={plen}, flags={flags}, seq={seq}')
    
    if pos + 12 + plen + 4 > len(full):
        print(f'  Not enough data for payload+CRC (need {plen+4}, have {len(full)-pos-12})')
        break
    
    payload = full[pos+12:pos+12+plen]
    crc = struct.unpack('<I', full[pos+12+plen:pos+12+plen+4])[0]
    print(f'  Payload: {len(payload)} bytes, CRC={crc:#010x}')
    
    # Print payload hex for analysis
    print(f'  Payload hex: {payload.hex()}')
    
    pos += 12 + plen + 4
    pkt_num += 1

print(f'\nParsed {pkt_num} packets total')
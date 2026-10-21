import sys
sys.path.insert(0, r'D:\项目\交互艺术\书法微更新\e-Brush\studio')
from brush_serial import BrushSerial
import time

print('Creating BrushSerial...', flush=True)
bs = BrushSerial('COM7', 921600)
print('Starting...', flush=True)
bs.start()
print('Started', flush=True)

for i in range(15):
    time.sleep(0.5)
    stats = dict(bs.stats)
    hello = 'Y' if bs.latest_hello else 'N'
    health = 'Y' if bs.latest_health else 'N'
    frame = 'Y' if bs.latest else 'N'
    print(f'  [{i}] H={hello} h={health} f={frame} stats={stats}', flush=True)
    if bs.latest:
        print(f'      frame_id={bs.latest.get("frame_id")} q_w={bs.latest.get("q_w"):.3f}', flush=True)

bs.close()
print('DONE', flush=True)
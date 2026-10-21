import sys
sys.path.insert(0, r'D:\项目\交互艺术\书法微更新\e-Brush\studio')
from brush_serial import BrushSerial
import time

bs = BrushSerial('COM7', 921600)
bs.start()
print('BrushSerial started', flush=True)

time.sleep(1.5)

# Print HEALTH details
print(f'HEALTH: {bs.latest_health}', flush=True)

# Check frames for hall data
for i in range(3):
    time.sleep(0.5)
    if bs.latest:
        f = bs.latest
        print(f'Frame {i}:', flush=True)
        print(f'  frame_id={f.get("frame_id")}', flush=True)
        print(f'  q=({f.get("q_w",0):.3f},{f.get("q_x",0):.3f},{f.get("q_y",0):.3f},{f.get("q_z",0):.3f})', flush=True)
        print(f'  hall_raw=({f.get("hall_x_raw")},{f.get("hall_y_raw")},{f.get("hall_z_raw")})', flush=True)
        print(f'  bx_mT={f.get("bx_mT")}, by_mT={f.get("by_mT")}, bz_mT={f.get("bz_mT")}', flush=True)
        print(f'  hall_variant={f.get("hall_variant")}', flush=True)
        print(f'  device_status_names={f.get("device_status_names")}', flush=True)

bs.close()
print('DONE', flush=True)
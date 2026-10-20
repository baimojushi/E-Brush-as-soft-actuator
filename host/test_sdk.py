import sys
sys.stdout.write('Starting...\n')
sys.stdout.flush()
from hp60c_direct import HP60CDirectSDK
sys.stdout.write('Import OK\n')
sys.stdout.flush()
sdk_dir = r'D:\Express Program Files\Angstrong Viewer V2.1.10_240109\Angstrong Viewer V2.1.10_240109\RelWithDebInfo'
sys.stdout.write(f'SDK dir: {sdk_dir}\n')
sys.stdout.flush()
try:
    s = HP60CDirectSDK(sdk_dir)
    sys.stdout.write('Created SDK object\n')
    sys.stdout.flush()
    s.start()
    sys.stdout.write('Started SDK\n')
    sys.stdout.flush()
    import time
    for i in range(20):
        f = s.poll()
        if f:
            sys.stdout.write(f'Frame: {f.get("packet_id")}, RGB: {f.get("rgb") is not None}, Depth: {f.get("depth_mm") is not None}, PC: {f.get("pointcloud") is not None}\n')
            sys.stdout.flush()
            break
        time.sleep(0.5)
    else:
        sys.stdout.write('No frames received in 10 seconds\n')
        sys.stdout.flush()
    sys.stdout.write(f'Status: {s.status()}\n')
    sys.stdout.flush()
except Exception as e:
    sys.stdout.write(f'Error: {e}\n')
    sys.stdout.flush()
    import traceback
    traceback.print_exc()
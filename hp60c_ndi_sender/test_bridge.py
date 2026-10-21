import hp60c_td_bridge_v7 as b
import time
result = b.td_init()
print('Init:', result)
if result.get('ok'):
    print('Waiting for frames...')
    for i in range(10):
        time.sleep(1)
        stats = b.td_get_stats()
        frame = b.td_get_latest_frame()
        print(f'  {i+1}s: frame_count={stats["frame_count"]} latest={frame is not None}')
    b.td_shutdown()
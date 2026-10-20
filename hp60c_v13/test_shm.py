import hp60c_shm_reader_v13 as r
import sys

attached = r.try_attach()
with open('shm_result.txt', 'w') as f:
    f.write(f'Attached: {attached}\n')
    if attached:
        status = r.get_status()
        f.write(f'State: {status.get("state")}\n')
        f.write(f'Worker PID: {status.get("worker_pid")}\n')
        f.write(f'Generation: {status.get("generation")}\n')
        f.write(f'Worker State: {status.get("worker_state")}\n')
        f.write(f'Packet ID: {status.get("packet_id")}\n')
        f.write(f'RGB Valid: {status.get("rgb_valid")}\n')
        f.write(f'Depth Valid: {status.get("depth_valid")}\n')
        f.write(f'PointCloud Valid: {status.get("pointcloud_valid")}\n')
        f.write(f'Frame Age (ms): {status.get("frame_age_ms")}\n')
        latest = r.try_get_latest(max_age_ms=500)
        if latest:
            f.write(f'Packet ID: {latest.get("packet_id")}\n')
            f.write(f'RGB: {latest.get("rgb").shape if latest.get("rgb") is not None else None}\n')
            d = latest.get('depth_mm')
            f.write(f'Depth: {d.shape if d is not None else None}\n')
            if d is not None:
                f.write(f'Depth min/max: {d.min()} {d.max()}\n')
            pc = latest.get('pointcloud')
            f.write(f'PointCloud: {pc.shape if pc is not None else None}\n')
        else:
            f.write('No latest packet\n')
    else:
        f.write('NOT ATTACHED\n')

print('Done - check shm_result.txt')
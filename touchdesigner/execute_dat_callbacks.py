# TouchDesigner Execute DAT 示例
# 启用 Frame Start。
#
# 若相机 SDK 能提供设备时间戳，优先传 device_ts_us；
# 若当前 TOP 只能确认“这一帧刚到达”，把 device_ts_us 设为 None，
# 系统会使用 time.perf_counter_ns() 做主机到达时间。

import time

BRIDGE_KEY = "brush_td_bridge"

# 请替换为你自己的深度相机帧号/时间戳取得方式。
# 这两个函数故意不猜测具体相机品牌。
def get_depth_frame_id():
    return int(absTime.frame)

def get_depth_device_timestamp_us():
    return None


def onFrameStart(frame):
    owner = me.parent()
    bridge = owner.fetch(BRIDGE_KEY, None)
    if bridge is None:
        return

    depth_frame_id = get_depth_frame_id()
    depth_ts_us = get_depth_device_timestamp_us()

    # 在“确认新深度帧”的那个回调时刻调用最准确。
    bridge.on_depth_frame(
        frame_id=depth_frame_id,
        device_ts_us=depth_ts_us,
        host_rx_ns=time.perf_counter_ns(),
    )
    return

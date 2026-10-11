# TouchDesigner 联调与深度相机对齐

## 推荐数据路径

ESP32-S3 -> USB 串口(921600) -> `BrushSerial` 后台线程 -> TouchDesigner 主线程 -> 深度相机帧时间对齐 -> Script CHOP。

串口层使用 COBS 分帧 + CRC32。MCU 的 `esp_timer_get_time()` 通过双向同步报文映射到 `time.perf_counter_ns()`。深度相机若能提供自己的设备时间戳，再做一层相机设备时钟到主机时间轴的一阶拟合。最终统一到主机单调时间轴，再按最近邻生成 `DCW_align_key` 与 `align_dt_ms`。

## 一次性配置

1. 把本目录 `touchdesigner/` 与 `host/` 放在 `.toe` 同级项目目录。
2. TouchDesigner Python 安装 `pyserial`：
   ```python
   import subprocess, sys
   subprocess.check_call([sys.executable, "-m", "pip", "install", "pyserial"])
   ```
3. 修改 `start_bridge_example.py` 中 `PORT`，执行一次。
4. 建立一个 Script CHOP，Callbacks DAT 使用 `script_chop_callbacks.py`。
5. 建立一个 Execute DAT，启用 Frame Start，Callbacks 使用 `execute_dat_callbacks.py`。
6. 把 `get_depth_frame_id()` 与 `get_depth_device_timestamp_us()` 替换成实际深度相机节点/SDK 的取值方式。

## 深度相机时间戳优先级

- 最优：相机 SDK 的设备时间戳 + 新帧回调到达时的 `perf_counter_ns()`。
- 可用：只有新帧到达时的 `perf_counter_ns()`。
- 不建议：TouchDesigner 渲染帧号乘固定帧周期推算时间。渲染抖动会直接进入对齐误差。

## 联调验收

观察 Script CHOP 中：
- `sync_rtt_ms`：USB 串口同步往返时延。
- `align_dt_ms`：深度帧与最近传感帧的时间差。
- `drop_flags`：任何非 0 值都应记录。
- `device_status`：Hall、IMU、有无重初始化、是否近饱和。
- `dcw_frame_id`：对应的深度相机帧键。

建议先做 2~5 分钟静态运行，再做快速摆动。快速摆动时可把相机姿态角速度模长与 IMU 角速度模长送入 `host/alignment.py::estimate_time_offset_ms()` 做粗时延交叉验证。

## 空间对齐

`host/calibration.py` 提供 Kabsch 刚体标定：

1. 从深度相机得到至少 3 个非共线标志点在相机坐标系的位置。
2. 给出这些点在纸面坐标系的位置。
3. `fit_rigid_transform(camera_xyz, paper_xyz)` 得到 `T_paper_camera`。
4. 用 `rms_error()` 验证残差；标定矩阵和残差作为独立元数据保存。
5. 任何相机支架、钢板、笔端组件移动后重新标定。

固连 Hall 与 IMU 的轴系变换也应作为独立标定矩阵保存，不能通过改写原始传感数据实现“对齐”。

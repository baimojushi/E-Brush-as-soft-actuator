# BRUSH 串口协议 V1

## 分帧

每个包：
1. `PacketHeader`
2. payload
3. IEEE CRC32（header+payload）
4. 整包做 COBS
5. 结尾 `0x00`

字节序全部为小端。串口默认 921600 bit/s，数据链路不输出文本日志。

## 关键包

- `0x01 DATA`：200 Hz 主数据帧。
- `0x02 HELLO`：启动/`PING` 后设备信息。
- `0x03 HEALTH`：1 Hz 统计。
- `0x11 SYNC_RESP`：时钟同步应答。
- `0x81 CMD_SYNC`、`0x82 CMD_START`、`0x83 CMD_STOP`、`0x84 CMD_PING`。

## DATA 语义

原始码始终保留：
- Hall：`hall_x_raw/y_raw/z_raw/temp_raw`
- IMU：温度、三轴角速度、三轴加速度原始码

派生量：
- Hall A2 ±133 mT：250 LSB/mT
- LSM6DSR ±4g：0.122 mg/LSB
- LSM6DSR ±1000 dps：35 mdps/LSB
- 姿态四元数为 6 轴融合派生量，绝对航向不受约束，`STATUS_POSE_YAW_RELATIVE=1`

时间：
- `sample_time_us`：MCU 主帧计划时刻
- `hall_time_us`：最近一次成功 Hall 读取时刻
- `imu_time_us`：最近一次成功 IMU 读取时刻
- 上位机用 SYNC 双向交换映射到 `time.perf_counter_ns()`

## 丢包原则

`frame_id` 对每个计划主帧递增，即使 USB 发送失败也递增。上位机用 `frame_id` 缺口识别传输丢失。

`drop_flags`：
- bit0 调度超时
- bit1 Hall 读取失败
- bit2 IMU 读取失败
- bit3 Hall 数据陈旧
- bit4 IMU 数据陈旧
- bit5 发送背压

不插值填补原始数据。

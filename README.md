# BRUSH ESP32-S3 + 移动 Hall A2 + IMU660RB + TouchDesigner

本版本只纳入：
- 移动 Hall A2：TMAG5273 A2
- 位姿传感：IMU660RB，按 LSM6DSRTR 兼容寄存器实现
- ESP32-S3 开发板

明确排除：
- 参考 Hall A1
- ADS1220
- ESP32-S3 Demo ADC / GPIO1
- 任何握持模拟采集

## 1. 接线

### 移动 Hall A2 / TMAG5273 A2

- GPIO8 -> SCL
- GPIO9 -> SDA
- GPIO4 <- INT
- 3V3 -> VCC
- GND -> GND 与 GND(TEST)
- SCL/SDA 按图纸保留 4.7 kΩ 上拉

### IMU660RB / LSM6DSRTR

- GPIO12 -> SCL/SPC = SPI SCLK
- GPIO11 -> SDA/SDI = SPI MOSI
- GPIO13 <- SA0/SDO = SPI MISO
- GPIO10 -> CS
- 3V3 -> VCC
- GND -> GND

IMU660RB 各批次 6P 物理排列可能不同，按模块丝印信号名接线。

GPIO19/GPIO20 不使用，保留 ESP32-S3 原生 USB 能力。

## 2. 固件参数

- 主帧 200 Hz
- TMAG5273 A2：I2C 400 kHz，0x35，三轴连续测量，32x 平均，低噪声，±133 mT
- Hall INT：转换完成锁存中断；漏中断时仍有主周期轮询兜底
- LSM6DSR：四线 SPI Mode 3，8 MHz，208 Hz，±4g，±1000 dps
- 串口：921600 bit/s
- 输出：COBS + CRC32 二进制帧
- 时钟：ESP32 `esp_timer_get_time()` + 主机双向同步
- 姿态：6 轴 Mahony 类融合四元数，仅作为派生显示量；绝对航向随时间漂移

## 3. 烧录

安装 PlatformIO 后：

```bash
cd BRUSH_ESP32S3_TouchDesigner_v1
pio run -e esp32-s3-devkitc-1-uart -t upload --upload-port COM7
```

把 `COM7` 换成实际端口。

若开发板实际使用 ESP32-S3 原生 USB CDC：

```bash
pio run -e esp32-s3-devkitc-1-native-usb -t upload
```

固件运行后同一串口是二进制数据链路，不要用普通串口监视器观察文本。

## 4. 上位机冒烟测试

```bash
python -m pip install pyserial
python tools/smoke_test.py --port COM7 --seconds 15 --log capture.brlog
```

健康目标：
- 接收帧率接近 200 Hz
- `clock synced: True`
- `frame_id_gap == 0`
- `drop_flag_names` 长期为空
- `hall_variant == 2`
- `imu_who_am_i == 107`（0x6B）
- `hall_read_errors/imu_read_errors` 不持续增长

## 5. TouchDesigner

见 `touchdesigner/README_TOUCHDESIGNER.md`。

核心原则：
- ESP32 原始时间戳不由 TouchDesigner 重写。
- 主机只建立时钟映射和 `DCW_align_key`。
- 深度相机与 ESP32 不共时钟时，先统一到主机单调时间轴。
- 相机支架、钢板、笔端拆装后，空间标定重新执行。
- 原始 `.brlog` 只增不改；所有融合、姿态、空间变换结果放派生数据。

## 6. 产品级冗余

固件已包含：
- Hall 身份校验、TI manufacturer ID 校验、A2 变体校验
- IMU WHO_AM_I=0x6B 校验
- Hall I2C 总线 9 时钟恢复 + STOP
- Hall/IMU 独立重初始化，不因单个传感器掉线阻塞全链
- Hall 锁存 INT + 定时轮询双路径
- IMU STATUS_REG 新鲜度判定
- 主循环超时丢弃旧相位，禁止补发历史帧
- `frame_id` 缺口、CRC32、设备状态、丢包原因、重初始化次数
- 二进制链路无文本日志污染
- 6 轴姿态显式标记“航向相对”
- 原始码与工程量分层，工程量可完全从原始码复算

## 7. 首次实机必须做的三项确认

1. **IMU660RB 模块丝印**：确认 6P 信号顺序与图纸信号名一致。
2. **Hall 磁场范围**：若实际磁场接近 ±133 mT，先排查磁路/间距；不要直接放宽到 ±266 mT 掩盖饱和问题。需要放宽时改 `SENSOR_CONFIG_2` 与主机换算系数并生成新标定编号。
3. **深度相机时间戳**：优先使用相机 SDK 原生设备时间戳；只有到达时间时，记录该模式并把对齐允差单独标记。

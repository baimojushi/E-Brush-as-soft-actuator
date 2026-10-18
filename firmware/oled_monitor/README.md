# e-Brush 机载 OLED 监视器 (firmware/oled_monitor)

ESP32-S3 板载 0.96" SSD1306 (128×64, I2C) 实时显示霍尔 (TMAG5273) 与
IMU (IMU660RB/LSM6DSRTR) 数据的参考实现。

## 文件

| 文件 | 说明 |
|---|---|
| `DESIGN_OLED_I2C.md` | 设计文档: 总线方案 / 引脚 / 地址 / 时序 / 接入 / 验收 |
| `platformio.ini` | PlatformIO 工程配置 (espressif32 + esp32-s3-devkitc-1 + Arduino) |
| `src/oled_monitor.ino` | 参考实现: SSD1306 I2C 驱动 + 开机黄宾虹动画 + 双页布局 + 20 Hz 刷新 |
| `src/font.h` | 中景园取模字体, 已做 ESP32 兼容 (avr/pgmspace.h → 平台判断) |

## 快速使用

1. **核对引脚**: 打开 `src/oled_monitor.ino`, 改 `OLED_SDA` / `OLED_SCL`
   为板子上未被占用的 GPIO (默认占位 4/5, 必须对照原理图确认)。
   同时核对 `BOOT_BUTTON_PIN` (默认 GPIO0 / 板载 BOOT)。
2. **编译/烧录**: 在本目录跑 `pio run` (PlatformIO 工程已配好,
   espressif32 6.10.0 + esp32-s3-devkitc-1 + Arduino 3.20017)。
   ★ Windows 单盘机器: PlatformIO 硬编码 `E:\` 路径, 先执行
   `subst E: D:\` (当前 session 有效, 重启失效)。
3. **接真实数据**: 在 `read_sensor_snapshot()` 的 `TODO(用户)` 处填入固件
   已有 TMAG5273 / IMU 读取结果; 未接入前屏幕显示 sin/cos 演示动画。
4. **并入主循环**: 在固件主循环 (非 6.667 kHz 中断!) 调用 `oled_uploop()`
   (按钮扫描与开机/数据页切换已包含在内)。

## 核心设计

- **专用第二 I2C 总线 (Wire1 / I2C1)**, 与传感器总线物理隔离 —— 防止
  1 KB/次的全屏刷新挤占 6.667 kHz 采样时序 (sched_overrun)。
- OLED 地址自动探测 `0x3C` → `0x3D`。
- 显示刷新 20 Hz; 每 2 s 双页轮换 (数据页 / 状态页)。
- 工程单位与 `host/brush_serial.py` 同口径: accel 0.122 mg/LSB (±4g),
  gyro 35 mdps/LSB (±1000dps), 霍尔变体 LSB/mT 可配。

### 开机页 (黄宾虹头像) + 按钮切换

- **开机默认显示黄宾虹像素头像动画** (两张 1024 字节全屏帧, 按
  `HuangBinhong_OLED_Boot_Animation.ino` 原始时序循环:
  HUANG_1 1.2s → HUANG_2 700ms → HUANG_1 300ms → HUANG_2 2.5s → HUANG_1 800ms)。
- **按钮 (默认 GPIO0 / 板载 BOOT, 上拉、按下=低电平)** 在
  「开机画面」与「传感器数据监视」之间切换; 数据页保留原双页 2s 自动轮换。
- 按钮引脚在 `oled_monitor.ino` 顶部 `#define BOOT_BUTTON_PIN` 改,
  并按实际电平确认 (上拉/下拉)。

## 依赖

- Arduino-ESP32 (Wire / Wire1, ESP32-S3)
- 无需第三方 OLED 库 (复用中景园 font.h 自带驱动/字体)

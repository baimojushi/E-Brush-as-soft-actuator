# e-Brush ESP32-S3 机载 0.96" OLED (SSD1306) I2C 连接设计与霍尔/IMU 实时显示

> 目标固件: e-Brush firmware v1.1.0 (ESP32-S3 + TMAG5273 Hall + IMU660RB/LSM6DSRTR)
> OLED 资料: `D:\项目\电子电路\0.96OLED_arduino_I2C例程` (中景园 SSD1306 例程 + font.h)
> 数据契约: `e-Brush/docs/CHOP_CHANNEL_CONTRACT.md` (霍尔 mT / IMU 加速度 / 陀螺仪 / 四元数)
> 参考实现: 本目录 `oled_monitor.ino` (完整可烧录的单文件示例)
>
> ★ v1.2 开机页: 开机默认显示黄宾虹像素头像动画 (两张 1024 字节全屏帧,
>   源 `D:\BaiduNetdiskDownload\HuangBinhong_OLED_Boot_Animation.ino`), 按钮
>   (默认 GPIO0 / 板载 BOOT, 上拉、按下=低电平) 在「开机画面」与「传感器数据
>   监视」之间切换; 数据页保留原双页 2s 自动轮换。

---

## 1. 结论 (TL;DR)

| 决策点 | 结论 |
|---|---|
| OLED 型号 | 0.96" SSD1306, 128×64, I2C |
| I2C 从机地址 | `0x3C` (7-bit); 若探测失败试 `0x3D` (程序自动探测) |
| 总线方案 | **专用第二 I2C 总线 (ESP32-S3 I2C1 / `Wire1`)**, 与传感器总线物理隔离 |
| 引脚 | 程序顶部 `#define OLED_SDA / OLED_SCL` 可改, 默认占位 GPIO4/GPIO5 (**必须按你的板子原理图核对**) |
| 显示刷新率 | 20 Hz (远低于传感器 6.667 kHz 采样率, 人眼足够) |
| 全屏刷新成本 | 1024 B GRAM + 24 B 命令, 400 kHz 下约 1 ms |
| 字体 | 复用中景园 `font.h` (已做 ESP32 兼容, 见本目录 font.h) |

## 2. 为什么 OLED 要走专用第二 I2C 总线 (核心设计决策)

霍尔 (TMAG5273) 与 IMU 在固件里按 **6.667 kHz** 中断采样, 主循环 200 Hz 出帧
(见 `docs/td_integration_architecture.md` §1)。该采样调度是**时序敏感**的:

- 文档故障表明确警告: `drop_flags` bit 0 `sched_overrun` flickers above 1 Hz
  通常是 **"IMU/Hall I²C contention"** — 传感器总线抢时间片的后果。
- 一块 0.96" SSD1306 全屏刷新 = 1 KB 数据 + 地址/命令开销, 400 kHz 下约
  1 ms, 100 kHz 下约 4–5 ms。若把它挂在传感器同一条 I2C 总线上, 每次显示
  刷新都会挤占采样窗口, 直接放大 `sched_overrun` 风险。

ESP32-S3 内置 **I2C0 与 I2C1 两个独立控制器**, `Wire` = I2C0, `Wire1` = I2C1
(Arduino-ESP32)。因此设计为:

```
                    I2C0 (时序敏感, 保持现状不动)          I2C1 (新增, 专职显示)
ESP32-S3 ──┬── SDA0/SCL0 ── TMAG5273 (I2C, 6.667kHz 采样)
           └── ...(IMU 按固件实际接法, I2C 或 SPI)          SDA1/SCL1 ── SSD1306 (0x3C)
```

- 显示刷新只发生在**主循环**里 (低优先级空闲时刻), 与中断采样无总线竞争。
- 传感器总线上不新增任何设备, 现有 v1.1.0 的 I2C 扫描 (`i2c_scan_bitmap`,
  health v1.1 里 0x3C/0x3D 不会多出 OLED) 语义不变。

> 替代方案 (仅当 ESP32-S3 没有空闲 GPIO 时才考虑): 与传感器共享 I2C0。
> 代价: 显示刷新期间传感读 I2C 可能超时 → 触发固件已有的 i2c 恢复逻辑,
> 加剧 `sched_overrun`。如被迫共享, 至少把 OLED 刷新降到 5–10 Hz 并把
> I2C 时钟降档, 且只做"部分页刷新"而非整屏。

## 3. 引脚选择

ESP32-S3 的 GPIO 中, 供 I2C1 自由选择的前提是**不冲突**:
- SPI flash/PSRAM、USB-JTAG (GPIO19/20)、Strapping (GPIO0/3/45/46)、
  UART0 (GPIO43/44, 下载/日志默认)、板载 RGB/按键/供电脚。

**因为 e-Brush 固件源码不在本工作区, 我无法直接读你的原理图**, 所以
`oled_monitor.ino` 把引脚做成顶部宏, 默认占位 `GPIO4 / GPIO5`
(ESP32-S3 上常见空闲), 接线前请对照你的板子原理图/引脚图确认, 例如:

| 引脚 | 用途 | 备注 |
|---|---|---|
| OLED_SDA | I2C1 数据 | 需 4.7 kΩ 上拉到 3.3 V (多数 OLED 模块板载) |
| OLED_SCL | I2C1 时钟 | 同上 |
| VCC | 3.3 V | 与 MCU 同源; 禁止 5 V |
| GND | 共地 | |

> 参考: ESP32 classic 上常见的 21/22 (SDA/SCL) 是 ESP32 默认 I2C0;
> **ESP32-S3 的默认 I2C0 引脚是 GPIO8/GPIO9**, 本方案不用默认值、显式指定。

## 4. 电气与接线要点

1. **3.3 V 供电**: SSD1306 模块 VCC 接 3.3 V (与传感器同轨), 不接 5 V。
2. **上拉电阻**: 两条线各自到 3.3 V 的 4.7 kΩ 上拉。模块若板载上拉则免。
   专用总线方案里 Sensor 总线的上拉与 OLED 总线互不影响。
3. **I2C 时钟**: 400 kHz (Fast Mode)。OLED 支持 400 kHz; 若布线长/干扰大,
   降到 100 kHz 并把 `OLED_REFRESH_MS` 相应调大。线材建议 <10 cm 短引线。
4. **地址**: SSD1306 默认 7-bit 地址 `0x3C` (例程里 `0x78` 是带写位后的
   8-bit 形式, 即 0x3C<<1)。模块背面若有 0x3D/0x7A 跳线则另一地址。
   程序开机自动探测 0x3C→0x3D。

## 5. 软件设计 (对应 oled_monitor.ino)

```
setup()
 ├─ Wire1(或共享 Wire) 初始化: begin(SDA, SCL, 400000)
 ├─ 地址探测 0x3C / 0x3D
 └─ SSD1306 初始化序列 (中景园例程命令原样保留, 含 charge pump 0x8D/0x14)
loop()
 ├─ read_sensor_snapshot(&s)     ← ★ 唯一需要接真实数据的函数 (用户填)
 ├─ 每 2 s 翻页 (显示 2 页: 霍尔+IMU 数据页 / 四元数+状态页)
 └─ 每 50 ms (20Hz) 整屏重绘 (GRAM 全刷, 约 1 ms)
```

- **GRAM 全缓存**: `uint8_t oled_gram[128][8]`, 与例程一致的页寻址模式
  (0x20→0x02); 全部先画进缓存, 再一次 `oled_refresh()` 上屏, 避免闪烁。
- **Wire 缓冲限制**: ESP32 Arduino 默认 I2C 发送缓冲约 128 B, 页刷新
  以 32 B 分块发送。
- **传感器工程单位对齐 host 侧契约** (`host/brush_serial.py`):
  - 加速度: `mg_per_lsb = 0.122` (即 ±4g 配置) → 显示 `g = raw × 0.000122`
  - 陀螺: `mdps_per_lsb = 35.0` (±1000dps) → 显示 `dps = raw × 0.035`
  - 霍尔: 变体 1 → 820 LSB/mT, 变体 2 → 250 LSB/mT; 未接变体信息时显示原始码
  - 单位统一口径后, OLED 读数可与 TouchDesigner CHOP 数值直接对照。
- **显示布局** (8×16 字体 → 每行 16 字符, 共 4 行, 双页轮换):
  - 页0: 标题 / 霍尔 Hx Hy Hz (mT 或原始码) / 加速度 Ax Ay Az (g) / 陀螺 Gx Gy Gz (dps)
  - 页1: 标题 / 四元数 q0 q1 q2 / 霍尔温度 + drop flags / 运行时间 + 空闲堆
- **开机页 (v1.2)**: 上电默认显示黄宾虹像素头像动画 (两张 1024 字节全屏帧,
  源 `D:\BaiduNetdiskDownload\HuangBinhong_OLED_Boot_Animation.ino`, 8 页纵向
  小端模式, 与 `oled_gram[128][8]` 同布局), 时序 1.2s→700ms→300ms→2.5s→800ms
  循环。**按钮** (默认 GPIO0 / 板载 BOOT, 上拉、按下=低电平, 50ms 去抖)
  在「开机画面」与「传感器数据监视」之间切换; 数据页保留原双页 2s 自动轮换。
- **自带演示数据**: `read_sensor_snapshot()` 未接入前输出 sin/cos 动画值,
  不接任何传感器也能先验证 OLED 通路与布局 (见函数内 TODO)。

## 6. 接入现有固件 v1.1.0 的步骤

1. 把本目录 (`firmware/oled_monitor/`) 的 `oled_monitor.ino` + `font.h`
   并入你的 PlatformIO/Arduino 工程 (或按工程结构拆分: `oled_drv .cpp/.h`
   + 显示任务函数)。
2. **核对按钮引脚**: 打开 `oled_monitor.ino`, 改 `BOOT_BUTTON_PIN`
   为板子上未被占用的 GPIO (默认占位 GPIO0 / 板载 BOOT), 并确认是上拉
   还是下拉 (本代码按"上拉、按下=低电平"处理; 下拉则把 `== LOW` 改 `== HIGH`)。
3. `read_sensor_snapshot()` 内替换 TODO 演示值:
   - 霍尔: 固件已有 TMAG5273 读取结果 (hx/hy/hz/温度), 可能需除以
     LSB/mT 变体系数或透传原始码;
   - IMU: 加速度/陀螺/四元数 (Mahony 输出) 直接拷贝指针或结构体。
4. 在**主循环**里调用 `oled_uploop()` (即 loop 里的刷新调度),
   **不要放进 6.667 kHz 中断或 200 Hz 采样回调**, 避免阻塞采样。
   `oled_uploop()` 内已包含按钮扫描与开机/数据页切换。
5. 上电自检: 断掉传感器总线, OLED 仍应显示黄宾虹头像动画 → 证明两总线隔离。
6. 联调: 对比 OLED 数值与 `host/tools/td_print_chop.py` 打印的
   `bx_mT / ax_m_s2 / gx_rad_s` 是否一致。

## 7. 测试与验收清单

> **已验证 (2026-09-12)**: `oled_monitor.ino` 在 PlatformIO `espressif32 6.10.0`
> + `esp32-s3-devkitc-1` 板型 + Arduino 框架 3.20017 下编译通过
> (RAM 6.1% = 19872 B, Flash 8.6% = 288809 B), Wire 库自动链接。
> 编译过程中发现并修复两点 .ino 特性问题:
> 1. **.ino 草图预处理器** 会用正则收集全部函数并集中前插原型 → 自定义类型
>    `sensor_snapshot_t` 必须声明于文件首个函数之前 (代码内已注释说明);
> 2. **避免在 .ino 顶部使用 `#if/#else`** 分支 (预处理器按行解析易错),
>    共享总线方案改为注释示例。

- [x] 编译验证 (esp32-s3-devkitc-1, 见上)
- [x] **v1.2 编译验证 (2026-09-23)**: `pio run` 在
  `firmware/oled_monitor/` (PlatformIO 工程, `platformio.ini`:
  espressif32 6.10.0 + esp32-s3-devkitc-1 + Arduino 3.20017) 下
  **编译通过**。RAM 6.1% (19,880/327,680 B), Flash 8.7% (291,921/3,342,336 B),
  无编译错误/警告。黄宾虹两张 1 KB flash 帧计入 Flash。
  **★ Windows 陷阱**: PlatformIO 6.1.19 硬编码 `E:\` 做 appstate 路径,
  单盘机器无 E: → `FileNotFoundError: 'E:\\'`。解决: `subst E: D:\`
  (当前 shell session 有效, 重启失效; 详见 `user/platformio_subst_fix.md`)。
  工程源文件必须放在 `src/` 子目录 (PlatformIO 要求), `font.h` 与
  `oled_monitor.ino` 同级即可。
- [ ] 确认 OLED 模块上电即亮 (初始化序列正常, 显示自检页)
- [ ] `Serial` 输出: 开机打印 `OLED addr=0x3C found` (或 `NOT FOUND`)
- [ ] 上电即显示黄宾虹头像动画 (HUANG_1→HUANG_2→HUANG_1→HUANG_2→HUANG_1,
      周期 5.5s, 与 HuangBinhong_OLED_Boot_Animation.ino 原始 loop() 一致)
- [ ] 按下按钮 → 切到传感器数据监视页; 再按 → 回到开机画面 (Serial 打印
      `page -> BOOT (HuangBinhong)` / `page -> DATA (sensor monitor)`)
- [ ] 磁铁靠近 → 页0 霍尔数字明显变化, 方向与量级合理
- [ ] 晃动毛笔 → 加速度/陀螺联动; 静止时加速度 ≈ (0, 0, +1) g
- [ ] 连续运行 10 min: 无 `sched_overrun` 增长 (对照 `drop_flags` 契约 bit0)
- [ ] 断掉 OLED 电源线, 传感器流不应受任何影响 (总线隔离验证)

## 8. 风险与回退

| 风险 | 影响 | 对策 |
|---|---|---|
| 所选 GPIO 被板载器件占用 | OLED 无响应/异常 | 用 I2C 扫描定位空闲引脚, 改宏重烧 |
| GPIO4/5 与固件 SPI/PSRAM 冲突 | 不稳定 | 对照原理图换用其他空闲脚 |
| 接线过长/无上拉 | 花屏/无响应 | 短引线、4.7 kΩ 上拉、降 100 kHz |
| 与传感器共享总线的被迫方案 | sched_overrun 加剧 | 见 §2 替代方案约束 |

## 9. 文件清单

| 文件 | 说明 |
|---|---|
| `oled_monitor.ino` | 参考实现: SSD1306 I2C 驱动 + 布局 + 20Hz 刷新 + 传感器接入点 |
| `font.h` | 中景园取模字体 (已做 ESP32 兼容: `<avr/pgmspace.h>` → 平台判断) |
| `README.md` | 烧录/接入摘要 |
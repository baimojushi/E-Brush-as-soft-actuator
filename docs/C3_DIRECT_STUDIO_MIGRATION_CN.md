# E-Brush：取消 ESP32-S3，电脑 Studio 直连 C3（实施及验收手册）

**状态：代码重构与离线回归已完成；仍需在 Windows + 实际 C3 上验证 BLE MTU、200 Hz 实际吞吐、漂移与 RGB-D 对齐。** 本文件只说明本次 S3 精简改造。没有修改传感器的电气供电方案，也没有声称已经验证过实机。

## 一、改造范围

**新默认数据路径：**

```text
MMC5983MA (GPIO5 SDA / GPIO6 SCL) + LSM6DSR (SPI)
                 |
           ESP32-C3 笔端
      采样 / 状态 / 32 位时间戳
                 |
     BLE EBrushLink Notify + CRC16
     BLE 时间特征 (PC-C3 往返对时)
                 |
       Windows 电脑 Studio
  Bleak 后台接收 / CRC / 丢包 / 时间戳展开
      Mahony 姿态 / Hall 自适应滤波
                 |
          统一 BRUSH-V1 数据字典
        采集质量 / 标定 / RGB-D / UI
               .brlog / Agent
```

ESP32-S3 **不再是运行必需设备**；原先的 S3 BLE/Wi-Fi 固件和 USB 转发仍保留，作为兼容对照与出问题时回退使用。原本接在 S3 上的 OLED、按钮和 LED 不能由电脑软件直接替代其物理显示；Studio 只替代了功能上的信息呈现。

| 功能 | 迁移结果 | 实现位置 |
|---|---|---|
| BLE 扫描、订阅、连接、断连通知 | 已新增 | `studio/c3_direct.py` `C3BleReceiver` |
| EBrushLink 校验、序号/丢帧、重启 | 已新增 | `studio/c3_direct.py` `C3DirectDecoder` |
| C3 时间戳回绕、时钟映射 | 已新增 | `studio/brush_processing.py`、`studio/c3_direct.py` |
| 电脑主动对时 | C3 新增 20 字节时间回复通知，Studio 往返拟合 | `src/c3_node.cpp`、`studio/c3_direct.py` |
| Mahony 6 轴姿态融合 | Python 移植，**逐样本处理** | `studio/brush_processing.py` |
| Hall 自适应滤波、运动强度/置信度 | Python 移植 | `studio/brush_processing.py` |
| Wi-Fi 备用传输 | 可选 PC 热点 UDP 直连固件 | `c3_wifi_pc`、`C3WifiReceiver` |
| 现有 Studio 标定、相机、Agent 录制 | 保持下游字典兼容；新增统一传输选择 | `studio/brush_serial.py`、`studio/sources.py`、`studio/main.py` |
| `.brlog` 录制 | BLE/Wi-Fi 直连时记录合成的 BRUSH-V1 DATA/HELLO/HEALTH/SYNC_RESP，以兼容原解析器 | `studio/c3_direct.py` |

注意 `.brlog` 是**标准化后的 BRUSH-V1 记录**，不是 BLE 空口原始字节。C3 原始 Hall/IMU 值、标志位、设备微秒时间戳被保留；姿态四元数来自 Studio 移植的 Mahony 算法。新接收链不包含 S3 的实体 OLED 交互或 S3 专属硬件诊断。

## 二、Windows 操作步骤（建议先用 BLE）

1. **接线保持 C3 原样。** MMC5983MA `SDA→GPIO5`、`SCL→GPIO6`，模块有上拉时不再串联之前造成无 ACK 的两个 1 kΩ 电阻；`3V3`、`GND` 供电与共地。LSM6DSR 仍沿用工程内原本的 SPI 引脚。对笔端断电后再插拔，先确认无短路。
2. **安装依赖**：在解压目录的 PowerShell 执行 `powershell -ExecutionPolicy Bypass -File .\SETUP_STUDIO.ps1`。脚本会安装 `bleak`、`PySide6` 等依赖，需电脑支持 BLE。
3. **烧录新版 C3**，使用 PlatformIO：

   ```powershell
   pio run -e c3_ble
   pio run -e c3_ble -t upload --upload-port COM3
   ```

   `COM3` 是示例，根据实际设备管理器修改。**不需要烧录或插接 ESP32-S3。** 打开 C3 串口启动日志请用 `115200` 波特率；运行期间不得把 USB 调试口当作 S3 的 BRUSH-V1 串口。
4. **启动 Studio**：

   ```powershell
   .\RUN_STUDIO.bat
   # 或带 Agent：
   powershell -ExecutionPolicy Bypass -File .\START_AGENT_LIVE.ps1 -CameraBackend none
   ```

   `RUN_STUDIO.bat` 和 Agent 实机启动脚本默认选择 `ble`，也可在 Studio “毛笔连接 / 重连”下拉框选“C3 · 蓝牙直连”。C3 正常广播名 `EBrush-C3`，Studio 自动扫描、连接和订阅。
5. UI 应先报告“BLE 已连接”，随后报告有效 DATA 帧数、Hall/IMU 数据。等到至少 4 次合格往返对时观测后，设备行显示“时钟：已同步”；否则显示未同步，**不能把未同步数据用于需要精确相机时间对齐的实验**。
6. 将笔静止、转动、靠近/远离磁体，检查三个轴数值变化、四元数是否连续，观察 5～10 分钟丢帧与断连。再启动相机做同步实验，最后测试录制/Agent 交接。

### BLE MTU 和吞吐风险

EBrushLink 单个样本包为 **60 字节**（ATT MTU **≥63** 才能用一个 Notify 承载）；4 样本合包为 **198 字节**（MTU **≥201**）。C3 会按协商 MTU 调整每包样本数，过小的 MTU 可能导致仅能连接、没有采样。Windows 蓝牙芯片/驱动对 MTU 的实际协商能力必须用实机检查；**200 Hz 为设计值，不代表电脑已稳定达到**。如果只看到连接而没有 DATA，先查 C3 串口、传感器、蓝牙适配器及 MTU，不要恢复那两个 1 kΩ 串联电阻。

### 相机时钟与研究数据安全

C3 用 `esp_timer_get_time()` 提供 64 位微秒时间回复。Studio 利用往返时间观测拟合 PC 的 `perf_counter_ns` 时基，并用同一时基向相机同步器提供 `sample_host_ns`。为了避免失真，往返服务时间扣除后的时延必须不超过 **25 ms**，近期 **5 秒内**有有效同步且至少 4 个拟合样本，才标记 `clock_synced=True`。高时延、缺少新版时间特征或断连时会显示**未同步**。此时仍可观察原始传感器数据，但不应认定满足 15 ms 相机匹配窗口。

## 三、兼容 S3 及 Wi-Fi 备用

**保留旧 S3** 用于交叉比较，不是默认硬件：

```powershell
.\RUN_STUDIO.bat --brush-transport serial --port COM7
# 带 Agent：
powershell -ExecutionPolicy Bypass -File .\START_AGENT_LIVE.ps1 -BrushTransport serial -BrushPort COM7
```

如果 BLE 带宽或 Windows 驱动限制无法满足需求，可用 **PC 热点模式**。它与旧 `c3_wifi`（对接 S3 的 `EBrush-S3` 热点）**不相同**：

1. 在 Windows 启用“移动热点”，设置名称 `EBrush-PC`，并为测试固件配置相同密码（工程中默认 `Change-Me-Now-2026` **仅供原型验证，正式使用前必须改密**）。
2. 确认 PC 热点网关地址为 `192.168.137.1`（`ipconfig` 检查）；若不同，先同步调整 `include/node_config.h` 的 `WIFI_HOST_IP`。
3. 烧录 `pio run -e c3_wifi_pc -t upload --upload-port COM3`。
4. Windows 防火墙允许 Studio 接收 UDP `5568`，以及电脑发送、接收对时 UDP `5569` 的相关响应。
5. 启动 `RUN_STUDIO.bat --brush-transport wifi`，或在 Studio 下拉菜单选 Wi-Fi 模式。

Wi-Fi 备用不等于 BLE 与 Wi-Fi 同时运行；这两种方式必须分别刷入 C3 固件。不要混淆旧 S3 热点固件和新电脑热点固件。

## 四、离线验证及最终硬件验收

在工程根目录：

```sh
sh tests/run_local_tests.sh
```

测试包括：C3 与 S3 的语法检查（含新增 C3-PC Wi-Fi 分支）、CRC16、1～4 样本合包、异常数据丢弃、计数与丢帧、C3 重启后的序号复位、32 位时间戳回绕与锚定、Mahony/自适应滤波、Windows 串口候选筛选，以及直连接收后的 `.brlog` BRUSH-V1 解析与时间同步重放。该测试不使用真实蓝牙或相机。

| 验收项 | 当前状态 | 实机如何测 |
|---|---|---|
| 原有单元测试与 Linux 模拟编译 | 已运行通过 | `sh tests/run_local_tests.sh` |
| C3-PC 解码/姿态/日志转换单元测试 | 已运行通过 | `python tests/test_c3_direct.py` |
| PlatformIO 实际 ESP32-C3 编译/烧录 | 本环境缺少工具链 | Windows PlatformIO 执行 `pio run -e c3_ble` |
| Windows BLE 连接与 MTU | **待实机** | 确认有效帧率、协商 MTU 与 5～10 分钟稳定性 |
| PC-C3 时间同步/RGB-D 15ms 匹配 | **待实机** | 统计合格 RTT、同步中断、实际误差分布 |
| 真实笔压/姿态标定与 RC Studio 交接 | **待实机** | 重做 Hall 外参标定，录制并导入 `.brlog` |

原有 BLE 连 S3 的路径不代表新版电脑蓝牙已测通。只有完成上述实机验收，才能把 S3 从**实际硬件装配**中彻底移除；本次已把它从**Studio 默认软件链路**和**默认固件构建目标**中移除。

## 五、维护与回退注意

- C3 有**五分钟静止后深度睡眠**的原有策略；进入深睡后 Studio 无法以 BLE 唤醒，需按 C3 `RST` 硬件复位。此行为本轮没有擅自改动。
- 录制中断后 Studio 标记 `interrupted`，保留已生成文件，不自动把重连后的采样拼到同一录制中。对 RC Studio 训练应根据采集质量排除被中断的数据。
- 不要在传感器数据入口进行两次 Mahony 计算；直连只由 `C3DirectDecoder` 逐帧融合，旧 S3 兼容模式继续沿用原 S3 输出。
- 正式采样中建议记录软件版本、BLE MTU、合格 RTT、丢帧率、CPU 负载和相机同步状态，作为是否永久省去 S3 的证据。

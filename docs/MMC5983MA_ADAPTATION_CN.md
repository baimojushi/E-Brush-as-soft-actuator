# E-Brush MMC5983MA（I²C）适配说明

基于上传的 `(8).zip` 修改，保持旧 TMAG5273 及 46B C3 样本 / 82B BRUSH-V1 DATA
通信结构兼容；旧 Studio 如未更新 `studio/brush_serial.py`，不认识新 `hall_variant=3`。

## 连接（ESP32-C3）

| C3 | MMC5983MA I²C 模块 | 说明 |
|---|---|---|
| GPIO5 | SDA | I²C 数据 |
| GPIO6 | SCL | I²C 时钟 |
| 3V3 | VDD、VDDIO | 确认模块可用 3.3V，不可直接用 5V 给裸芯片供电 |
| GND | GND | 共地 |
| 3V3 | SPI_CS | **I²C 下必须拉高**，有些模块已拉高 |
| GPIO7 | INT | 非必需；此驱动轮询 status/ready |

推荐 SDA/SCL 上拉至 3.3V（例如 2.7k~4.7kΩ；确认模块是否自带）；裸芯片
CAP 按厂商应用电路接 10uF，旁路去耦紧靠芯片。工厂 I²C 地址 0x30，
PRODUCT_ID 寄存器 0x2F 应为 0x30。

## 数据约定

- MMC 原始寄存器：18 位无符号，零场码 131072，16384 LSB/G。
- C3 对每轴执行 `(raw18 >> 2) - 32768`，传输有符号 `int16_t`。
- `Meta.hall_variant = 3`、`HELLO/HEALTH.hall_variant = 3`。
- S3 和 Studio 使用 **40960 counts/mT** 换算；量程约 ±0.8 mT。
- MMC 目前使用连续 200Hz（BW=01，CM_Freq=110），自动/周期 SET。
  GPIO7/INT 未使用；数据就绪通过 MMC status bit0，读取后 W1C 清除。
- 未启用 MMC 温度转换，Studio 将 `hall_temp_C` 标为 NaN，不会误用 TMAG 温标。
- 原 TMAG5273 地址及单位依旧兼容，variant 1/2 不变。
- 本次未修改 C3/S3 协议包的长度、CRC、IMU 姿态融合算法、BLE UUID。

## 烧录及运行

需要 **C3 和 S3 都刷本版**，且电脑 Studio 使用本版目录。示例：

```bat
pio run -e c3_ble -t upload --upload-port COM3
pio run -e s3_ble -t upload --upload-port COM7
py -3 studio\main.py --camera-backend none --port COM7
```

先关闭占用串口的 Studio/监视器，并把 COM3/COM7 改为实际端口。
C3 的 115200 日志启动时应出现 `Hall ready: YES` 和
`Hall I2C addr: 0x30, model: MMC5983MA, variant: 3`。
C3 故意不在每一个 200Hz 周期打印，否则会拖慢 BLE 发送。
如果启动失败，可使用独立自检环境：

```bat
pio run -e c3_mmc5983_test -t upload --upload-port COM3
pio device monitor -p COM3 -b 115200
```

自检固件在 C3 上单独运行，**会临时替换生产 BLE 固件**；验证后必须
重新刷 `c3_ble`。正常输出 ID=0x30，Bx/By/Bz 会在旋转传感器时变化。

## 重要注意

传感器安装方向、零偏与磁场响应与 TMAG5273 不同；换型号后，旧的霍尔外参
和基线不能继续作为已验证结果使用。请重新完成霍尔基线和外参标定。
MMC5983MA 是 3 轴 AMR 磁力计，不是线性 Hall 效应芯片，界面沿用「Hall」
仅出于兼容项目命名。

厂商手册：MEMSIC `MMC5983MA Datasheet Rev A`：
https://www.memsic.com/Public/Uploads/uploadfile/files/20220119/MMC5983MADatasheetRevA.pdf

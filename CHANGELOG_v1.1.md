# V1.1 — Hall 启动诊断与时序健壮性

## 已修改

1. ESP32-S3 每次启动在 Hall I²C 总线上扫描一次，结果通过 HEALTH 重复上报。
2. 自动探测 TMAG5273 四个工厂地址：0x35 / 0x22 / 0x78 / 0x44。
3. 移除“移动 Hall A2 通道 = TMAG5273 VER=2”的硬编码。
4. 读取 TI manufacturer ID、DEVICE_ID，并报告初始化失败阶段。
5. 上位机根据 DEVICE_ID.VER 动态换算磁场：
   - VER=1 + `_RANGE=0` -> 820 LSB/mT（±40 mT）
   - VER=2 + `_RANGE=0` -> 250 LSB/mT（±133 mT）
   - 未识别版本时 `bx/by/bz_mT` 返回 NaN，不伪造工程量。
6. 修复 `scheduled_us - sensor_time_us` 的 uint64 下溢导致的假 `hall_stale / imu_stale`。
7. Hall/IMU 运行期恢复门限改为连续 3 次读取失败，避免累计历史错误触发误重置。
8. HEALTH 保持 V1 前 52 bytes 不变，追加 V1.1 诊断字段；主机解析器兼容两种长度。
9. 成功运行期恢复后自动发送新的 HELLO，刷新设备身份状态。

## 重要澄清

本上传工程的 IMU660RB 由 `SPIClass imuSpi(FSPI)` 驱动，GPIO12/11/13/10 为 SPI。
因此 `imu_who_am_i=0x6B` 证明 IMU SPI 链路正常，不证明 GPIO8/GPIO9 的 Hall I²C 链路正常。

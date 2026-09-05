#pragma once
#include <Arduino.h>

namespace BrushConfig {

// 图纸冻结：移动 Hall A2
static constexpr uint8_t PIN_HALL_SCL = 8;
static constexpr uint8_t PIN_HALL_SDA = 9;
static constexpr uint8_t PIN_HALL_INT = 4;

// 图纸冻结：IMU660RB / LSM6DSRTR 四线 SPI
static constexpr uint8_t PIN_SPI_SCLK = 12;
static constexpr uint8_t PIN_SPI_MOSI = 11;
static constexpr uint8_t PIN_SPI_MISO = 13;
static constexpr uint8_t PIN_IMU_CS   = 10;

// GPIO19/GPIO20 保留给 ESP32-S3 原生 USB，不使用。
// 参考 Hall A1(GPIO5/6/7)、ADS1220(GPIO15/16) 与 Demo ADC(GPIO1) 本版本全部不初始化。

static constexpr uint32_t SERIAL_BAUD = 921600;
static constexpr uint32_t I2C_HZ = 400000;
static constexpr uint32_t SPI_HZ = 8000000;  // LSM6DSR 规格允许最高 10 MHz

// 主数据帧频率。IMU 配置为 208 Hz，主控按 200 Hz 打包，便于 TouchDesigner 联调。
static constexpr uint32_t STREAM_HZ = 200;
static constexpr uint32_t STREAM_PERIOD_US = 1000000UL / STREAM_HZ;

// TMAG5273 A2：默认 7-bit 地址 0x35；±133 mT 档位为 250 LSB/mT。
// 32 次平均时三轴更新约 400 SPS，兼顾噪声与 200 Hz 主帧。
static constexpr uint8_t HALL_I2C_ADDR = 0x35;
static constexpr uint8_t HALL_EXPECTED_VARIANT = 2;
static constexpr uint8_t HALL_CONV_AVG_CODE = 5;
static constexpr bool HALL_LOW_NOISE = true;
static constexpr float HALL_LSB_PER_MT = 250.0f;

// LSM6DSR：208 Hz，±4 g，±1000 dps。
static constexpr float IMU_ACCEL_MG_PER_LSB = 0.122f;
static constexpr float IMU_GYRO_MDPS_PER_LSB = 35.0f;

// 设备恢复与健康统计
static constexpr uint32_t SENSOR_REINIT_INTERVAL_MS = 500;
static constexpr uint32_t HEALTH_PERIOD_MS = 1000;
static constexpr uint32_t HALL_STALE_US = 15000;
static constexpr uint32_t IMU_STALE_US  = 15000;

// 标定集编号。量产时由构建系统/配置区写入，不覆盖原始数据。
#ifndef BRUSH_CALIBRATION_ID
#define BRUSH_CALIBRATION_ID 0u
#endif
static constexpr uint32_t CALIBRATION_ID = BRUSH_CALIBRATION_ID;

}  // namespace BrushConfig

#pragma once
#include <stdint.h>

// Only pins actually accessible on the ESP32-C3-DevKitM-1 reference board.
// ESP32-S3 original GPIO12/13 were FLASH pins on C3 and MUST NOT be copied.
// Avoid C3 strap GPIO2/8/9, USB GPIO18/19, and flash GPIO11..17.
// Pin planning for the current C3 pen board:
//   - Hall TMAG5273 uses the right-side contiguous cluster GPIO5/GPIO6,
//     with GPIO7 reserved for a future DRDY/INT wire if needed.
//   - IMU LSM6DSR stays on the left side to reduce crossover wiring.
#ifndef EB_PIN_HALL_SDA
#define EB_PIN_HALL_SDA 5
#endif
#ifndef EB_PIN_HALL_SCL
#define EB_PIN_HALL_SCL 6
#endif
#ifndef EB_PIN_HALL_INT
#define EB_PIN_HALL_INT 7
#endif
#ifndef EB_PIN_SPI_SCK
#define EB_PIN_SPI_SCK 4
#endif
#ifndef EB_PIN_SPI_MOSI
#define EB_PIN_SPI_MOSI 3
#endif
#ifndef EB_PIN_SPI_MISO
#define EB_PIN_SPI_MISO 1
#endif
#ifndef EB_PIN_IMU_CS
#define EB_PIN_IMU_CS 0
#endif

// Changing pins requires changing the physical wiring first.
namespace NodeConfig {
constexpr int HALL_SDA = EB_PIN_HALL_SDA;
constexpr int HALL_SCL = EB_PIN_HALL_SCL;
constexpr int HALL_INT = EB_PIN_HALL_INT;  // optional, currently reserved/not wired in firmware
constexpr int IMU_SCK = EB_PIN_SPI_SCK;
constexpr int IMU_MOSI = EB_PIN_SPI_MOSI;
constexpr int IMU_MISO = EB_PIN_SPI_MISO;
constexpr int IMU_CS = EB_PIN_IMU_CS;
constexpr uint32_t SAMPLE_HZ = 200;
constexpr uint32_t PERIOD_US = 1000000 / SAMPLE_HZ;
constexpr uint32_t SENSOR_RETRY_US = 500000;
constexpr uint32_t MAX_STALE_US = 15000;
// ★ 低功耗待机: 连续 5 分钟无"大幅度运动" → 进入深度睡眠,
//    按 RST 键硬件唤醒重启即可恢复工作 (传感器/BLE 全停, 电流 ~μA)。
//    注意: 本固件用 esp_timer_get_time() 取时间, 单位是微秒 (μs)，
//    所以待机阈值必须是微秒值 (5*60*1e6)，写成 5*60*1000 会变成 0.3 秒就休眠。
constexpr uint32_t STANDBY_IDLE_US  = 5 * 60 * 1000000u;  // 5 min in microseconds
constexpr int16_t  MOTION_DELTA_LSB = 2000;              // 加速度相邻采样差阈值 (~0.12g)
constexpr uint32_t I2C_HZ = 400000;
constexpr uint32_t SPI_HZ = 8000000;
constexpr uint8_t HALL_AVG_CODE = 2;  // 4 averages, less sensor conversion latency
constexpr uint8_t HALL_ADDRS[] = {0x35, 0x22, 0x78, 0x44};
constexpr size_t HALL_ADDR_COUNT = sizeof(HALL_ADDRS) / sizeof(HALL_ADDRS[0]);

constexpr const char* WIFI_SSID = "EBrush-S3";
#ifndef EBRUSH_WIFI_PASSWORD
#define EBRUSH_WIFI_PASSWORD "Change-Me-Now-2026"
#endif
constexpr const char* WIFI_PASSWORD = EBRUSH_WIFI_PASSWORD;
constexpr uint16_t WIFI_PORT = 5568;
constexpr const char* BLE_NAME = "EBrush-C3";
constexpr const char* BLE_SERVICE = "8b962400-dad0-45a8-a8d5-4f45fd83aefe";
constexpr const char* BLE_DATA_CHAR = "8b962401-dad0-45a8-a8d5-4f45fd83aefe";
}  // namespace NodeConfig

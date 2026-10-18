#pragma once
#include <Arduino.h>
#include <stddef.h>
#include <stdint.h>

namespace BrushProtocol {

static constexpr uint16_t MAGIC = 0x5242;   // little-endian bytes: 42 52 = "BR"
static constexpr uint8_t VERSION = 1;
static constexpr size_t MAX_PAYLOAD = 192;
static constexpr size_t MAX_RAW_PACKET = 256;
static constexpr size_t MAX_WIRE_PACKET = 272;

enum class PacketType : uint8_t {
  DATA        = 0x01,
  HELLO       = 0x02,
  HEALTH      = 0x03,
  SYNC_RESP   = 0x11,

  CMD_SYNC    = 0x81,
  CMD_START   = 0x82,
  CMD_STOP    = 0x83,
  CMD_PING    = 0x84,
};

enum DeviceStatus : uint32_t {
  STATUS_HALL_PRESENT        = 1u << 0,
  STATUS_HALL_VALID          = 1u << 1,
  STATUS_HALL_DIAG           = 1u << 2,
  STATUS_HALL_NEAR_SAT       = 1u << 3,
  STATUS_IMU_PRESENT         = 1u << 4,
  STATUS_IMU_VALID           = 1u << 5,
  STATUS_IMU_FRESH           = 1u << 6,
  STATUS_POSE_VALID          = 1u << 7,
  STATUS_POSE_YAW_RELATIVE   = 1u << 8,
  STATUS_I2C_RECOVERED       = 1u << 9,
  STATUS_SPI_RECOVERED       = 1u << 10,
  STATUS_STREAMING           = 1u << 11,
};

enum DropFlags : uint32_t {
  DROP_NONE              = 0,
  DROP_SCHED_OVERRUN     = 1u << 0,
  DROP_HALL_READ_FAIL    = 1u << 1,
  DROP_IMU_READ_FAIL     = 1u << 2,
  DROP_HALL_STALE        = 1u << 3,
  DROP_IMU_STALE         = 1u << 4,
  DROP_TX_BACKPRESSURE   = 1u << 5,
};

enum Capabilities : uint32_t {
  CAP_HALL_A2       = 1u << 0,
  CAP_IMU_6AXIS     = 1u << 1,
  CAP_QUAT_6AXIS    = 1u << 2,
  CAP_CLOCK_SYNC    = 1u << 3,
  CAP_HEALTH        = 1u << 4,
  CAP_I2C_SCAN_DIAGNOSTICS = 1u << 5,
};

#pragma pack(push, 1)

struct PacketHeader {
  uint16_t magic;
  uint8_t version;
  uint8_t type;
  uint16_t payload_len;
  uint16_t flags;
  uint32_t packet_seq;
};

struct DataPayload {
  uint32_t frame_id;
  uint32_t calibration_id;

  uint64_t sample_time_us;
  uint64_t hall_time_us;
  uint64_t imu_time_us;

  uint32_t device_status;
  uint32_t drop_flags;

  int16_t hall_x_raw;
  int16_t hall_y_raw;
  int16_t hall_z_raw;
  int16_t hall_temp_raw;
  uint8_t hall_conv_status;
  uint8_t hall_device_status;
  uint8_t imu_status;
  uint8_t reserved0;

  int16_t imu_temp_raw;
  int16_t gyro_x_raw;
  int16_t gyro_y_raw;
  int16_t gyro_z_raw;
  int16_t accel_x_raw;
  int16_t accel_y_raw;
  int16_t accel_z_raw;

  // 6 轴姿态融合派生量：只用于实时显示/联调。
  // 无绝对航向观测，yaw 为相对量，会随时间漂移；原始 IMU 始终保留。
  float q_w;
  float q_x;
  float q_y;
  float q_z;
};

struct HelloPayload {
  uint32_t boot_id;
  uint32_t reset_reason;
  uint32_t capabilities;
  uint32_t serial_baud;
  uint16_t stream_hz;
  uint16_t fw_major;
  uint16_t fw_minor;
  uint16_t fw_patch;
  uint8_t hall_variant;
  uint8_t imu_who_am_i;
  uint16_t reserved0;
  uint32_t calibration_id;
};

struct HealthPayload {
  // V1 基础字段，顺序保持不变，便于主机兼容旧日志。
  uint64_t uptime_us;
  uint32_t frames_intended;
  uint32_t frames_sent;
  uint32_t tx_drops;
  uint32_t rx_crc_errors;
  uint32_t rx_frame_errors;
  uint32_t hall_read_errors;
  uint32_t imu_read_errors;

  // 兼容旧字段名：这里统计的是“失败状态下的重新初始化尝试次数”。
  uint32_t hall_reinits;
  uint32_t imu_reinits;
  uint32_t free_heap;
  uint32_t min_free_heap;

  // V1.1 追加诊断字段。
  // bit N 对应 7-bit I²C 地址 N；扫描只在每次 MCU 启动后执行一次。
  uint64_t i2c_scan_bitmap_lo;  // 0x00..0x3F
  uint64_t i2c_scan_bitmap_hi;  // 0x40..0x7F
  uint32_t i2c_scan_duration_us;
  uint32_t hall_recoveries;     // 运行期从失败状态恢复成功次数
  uint32_t imu_recoveries;

  uint8_t i2c_scan_count;
  uint8_t i2c_scan_done;
  uint8_t hall_i2c_address;     // 0 = 尚未确认 TMAG5273
  uint8_t hall_variant;         // DEVICE_ID.VER: 1 / 2，0 = 未确认
  uint8_t hall_init_error;      // Tmag5273::InitError
  uint8_t hall_manufacturer_lsb;
  uint8_t hall_manufacturer_msb;
  uint8_t hall_device_id;
};

struct SyncRequestPayload {
  uint64_t host_tx_ns;
  uint32_t nonce;
};

struct SyncResponsePayload {
  uint64_t host_tx_ns;
  uint32_t nonce;
  uint64_t mcu_rx_us;
  uint64_t mcu_tx_us;
};

#pragma pack(pop)

uint32_t crc32_ieee(const uint8_t* data, size_t len);
size_t cobs_encode(const uint8_t* input, size_t length, uint8_t* output, size_t output_capacity);
size_t cobs_decode(const uint8_t* input, size_t length, uint8_t* output, size_t output_capacity);

// 生成：header + payload + CRC32 -> COBS -> 0x00
size_t build_wire_packet(
    PacketType type,
    uint32_t packet_seq,
    const void* payload,
    uint16_t payload_len,
    uint8_t* wire_out,
    size_t wire_capacity);

// 解析已完成 COBS 解码的数据；payload_out 指向 raw_buffer 内部。
bool validate_raw_packet(
    uint8_t* raw_buffer,
    size_t raw_len,
    PacketHeader*& header_out,
    uint8_t*& payload_out);

}  // namespace BrushProtocol

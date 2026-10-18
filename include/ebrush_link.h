#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace EBrushLink {
constexpr uint16_t MAGIC = 0xC3E8;
constexpr uint8_t VERSION = 1;
constexpr uint8_t TYPE_SAMPLES = 1;
constexpr uint8_t TYPE_META = 2;
constexpr size_t MAX_SAMPLES = 4;

#pragma pack(push, 1)
struct Header {
  uint16_t magic;
  uint8_t version;
  uint8_t type;
  uint32_t bundle_seq;
  uint16_t payload_len;
  uint8_t count;
  uint8_t reserved;
};

// Bytes in these structures are little-endian. Missing samples never get interpolated.
struct Sample {
  uint32_t frame_id;
  uint32_t sensor_time_us32;
  uint32_t status;      // BrushProtocol::DeviceStatus
  uint32_t drop_flags;  // BrushProtocol::DropFlags
  uint16_t hall_age_us; // 0xffff = no measurement
  uint16_t imu_age_us;
  int16_t hx, hy, hz, ht;
  int16_t it, gx, gy, gz, ax, ay, az;
  uint8_t hall_conv, hall_device, imu_status, reserved;
};

struct Meta {
  uint32_t boot_id;
  uint32_t sample_count;
  uint32_t transport_drops;
  uint32_t hall_errors;
  uint32_t imu_errors;
  uint32_t hall_reinits;
  uint32_t imu_reinits;
  uint32_t last_sensor_time_us32;
  uint8_t hall_variant;
  uint8_t imu_whoami;
  uint8_t transport_id; // 1=BLE; 2=Wi-Fi
  uint8_t reserved;
};
#pragma pack(pop)

static_assert(sizeof(Header) == 12, "header packing");
static_assert(sizeof(Sample) == 46, "sample packing");
static_assert(sizeof(Meta) == 36, "meta packing");
constexpr size_t MAX_PACKET = sizeof(Header) + MAX_SAMPLES * sizeof(Sample) + 2;
static_assert(MAX_PACKET <= 244, "must fit ATT MTU 247 notification");

uint16_t crc16(const uint8_t* data, size_t size);
size_t make_packet(uint8_t type, uint32_t bundle_seq, const void* payload,
                   uint16_t payload_len, uint8_t count, uint8_t* output,
                   size_t output_capacity);
// Safe against malformed length, count, CRC, type, and versions.
bool parse_packet(const uint8_t* data, size_t size, Header& header,
                  const uint8_t*& payload);

// C3 32-bit microsecond tick wraps. Stored S3 as a monotonically increasing epoch.
struct TickUnwrapper {
  bool initialized = false;
  uint32_t last = 0;
  uint64_t high = 0;
  void reset() { initialized = false; last = 0; high = 0; }
  bool unwrap(uint32_t tick, uint64_t& out);
};
}  // namespace EBrushLink

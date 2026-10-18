#include "ebrush_link.h"

namespace EBrushLink {
uint16_t crc16(const uint8_t* data, size_t size) {
  uint16_t crc = 0xffff;
  for (size_t i = 0; i < size; ++i) {
    crc ^= static_cast<uint16_t>(data[i]) << 8;
    for (int bit = 0; bit < 8; ++bit) {
      crc = static_cast<uint16_t>((crc << 1) ^ ((crc & 0x8000) ? 0x1021 : 0));
    }
  }
  return crc;
}

size_t make_packet(uint8_t type, uint32_t bundle_seq, const void* payload,
                   uint16_t payload_len, uint8_t count, uint8_t* output,
                   size_t capacity) {
  const bool is_samples = type == TYPE_SAMPLES;
  const bool is_meta = type == TYPE_META;
  if (!output || (!payload && payload_len) || (!is_samples && !is_meta)) return 0;
  if (is_samples && (count == 0 || count > MAX_SAMPLES ||
      payload_len != count * sizeof(Sample))) return 0;
  if (is_meta && (count != 1 || payload_len != sizeof(Meta))) return 0;
  const size_t len = sizeof(Header) + payload_len + 2;
  if (len > MAX_PACKET || capacity < len) return 0;
  Header header{MAGIC, VERSION, type, bundle_seq, payload_len, count, 0};
  memcpy(output, &header, sizeof(header));
  memcpy(output + sizeof(header), payload, payload_len);
  const uint16_t check = crc16(output, sizeof(header) + payload_len);
  output[len-2] = static_cast<uint8_t>(check & 0xff);
  output[len-1] = static_cast<uint8_t>(check >> 8);
  return len;
}

bool parse_packet(const uint8_t* data, size_t size, Header& header,
                  const uint8_t*& payload) {
  payload = nullptr;
  if (!data || size < sizeof(Header) + 2 || size > MAX_PACKET) return false;
  memcpy(&header, data, sizeof(Header));
  if (header.magic != MAGIC || header.version != VERSION || header.reserved != 0) return false;
  if (size != sizeof(Header) + static_cast<size_t>(header.payload_len) + 2) return false;
  if (header.type == TYPE_SAMPLES) {
    if (header.count == 0 || header.count > MAX_SAMPLES ||
        header.payload_len != header.count * sizeof(Sample)) return false;
  } else if (header.type == TYPE_META) {
    if (header.count != 1 || header.payload_len != sizeof(Meta)) return false;
  } else {
    return false;
  }
  const uint16_t expected = static_cast<uint16_t>(data[size-2] | (data[size-1] << 8));
  if (crc16(data, size-2) != expected) return false;
  payload = data + sizeof(Header);
  return true;
}

bool TickUnwrapper::unwrap(uint32_t tick, uint64_t& out) {
  if (!initialized) {
    initialized = true;
    last = tick;
    high = tick;
    out = high;
    return true;
  }
  // Difference modulo 2^32, only advance for forward deltas < 2^31.
  const uint32_t d = tick - last;
  if (d > 0x7fffffffu) return false;
  high += d;
  last = tick;
  out = high;
  return true;
}
}  // namespace EBrushLink

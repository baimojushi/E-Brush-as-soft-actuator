#include "brush_protocol.h"
#include <string.h>

namespace BrushProtocol {

uint32_t crc32_ieee(const uint8_t* data, size_t len) {
  uint32_t crc = 0xFFFFFFFFu;
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (uint8_t b = 0; b < 8; ++b) {
      const uint32_t mask = -(crc & 1u);
      crc = (crc >> 1) ^ (0xEDB88320u & mask);
    }
  }
  return crc ^ 0xFFFFFFFFu;
}

size_t cobs_encode(const uint8_t* input, size_t length, uint8_t* output, size_t output_capacity) {
  if (!output || output_capacity == 0) return 0;

  size_t read_index = 0;
  size_t write_index = 1;
  size_t code_index = 0;
  uint8_t code = 1;

  while (read_index < length) {
    if (write_index >= output_capacity) return 0;

    if (input[read_index] == 0) {
      output[code_index] = code;
      code = 1;
      code_index = write_index++;
      ++read_index;
    } else {
      output[write_index++] = input[read_index++];
      ++code;
      if (code == 0xFF) {
        output[code_index] = code;
        code = 1;
        code_index = write_index++;
        if (write_index > output_capacity) return 0;
      }
    }
  }

  if (code_index >= output_capacity) return 0;
  output[code_index] = code;
  return write_index;
}

size_t cobs_decode(const uint8_t* input, size_t length, uint8_t* output, size_t output_capacity) {
  if (!input || !output || length == 0) return 0;

  size_t read_index = 0;
  size_t write_index = 0;

  while (read_index < length) {
    const uint8_t code = input[read_index];
    if (code == 0) return 0;
    ++read_index;

    for (uint8_t i = 1; i < code; ++i) {
      if (read_index >= length || write_index >= output_capacity) return 0;
      output[write_index++] = input[read_index++];
    }

    if (code != 0xFF && read_index < length) {
      if (write_index >= output_capacity) return 0;
      output[write_index++] = 0;
    }
  }
  return write_index;
}

size_t build_wire_packet(
    PacketType type,
    uint32_t packet_seq,
    const void* payload,
    uint16_t payload_len,
    uint8_t* wire_out,
    size_t wire_capacity) {

  if (payload_len > MAX_PAYLOAD || !wire_out || wire_capacity < 2) return 0;

  uint8_t raw[MAX_RAW_PACKET];
  const size_t body_len = sizeof(PacketHeader) + payload_len;
  const size_t raw_len = body_len + sizeof(uint32_t);
  if (raw_len > sizeof(raw)) return 0;

  PacketHeader header{};
  header.magic = MAGIC;
  header.version = VERSION;
  header.type = static_cast<uint8_t>(type);
  header.payload_len = payload_len;
  header.flags = 0;
  header.packet_seq = packet_seq;

  memcpy(raw, &header, sizeof(header));
  if (payload_len && payload) {
    memcpy(raw + sizeof(header), payload, payload_len);
  }

  const uint32_t crc = crc32_ieee(raw, body_len);
  memcpy(raw + body_len, &crc, sizeof(crc));

  const size_t encoded_len = cobs_encode(raw, raw_len, wire_out, wire_capacity - 1);
  if (encoded_len == 0 || encoded_len + 1 > wire_capacity) return 0;

  wire_out[encoded_len] = 0x00;
  return encoded_len + 1;
}

bool validate_raw_packet(
    uint8_t* raw_buffer,
    size_t raw_len,
    PacketHeader*& header_out,
    uint8_t*& payload_out) {

  header_out = nullptr;
  payload_out = nullptr;

  if (!raw_buffer || raw_len < sizeof(PacketHeader) + sizeof(uint32_t)) return false;

  auto* header = reinterpret_cast<PacketHeader*>(raw_buffer);
  if (header->magic != MAGIC || header->version != VERSION) return false;
  if (header->payload_len > MAX_PAYLOAD) return false;

  const size_t expected = sizeof(PacketHeader) + header->payload_len + sizeof(uint32_t);
  if (raw_len != expected) return false;

  uint32_t crc_rx = 0;
  memcpy(&crc_rx, raw_buffer + expected - sizeof(uint32_t), sizeof(uint32_t));
  const uint32_t crc_calc = crc32_ieee(raw_buffer, expected - sizeof(uint32_t));
  if (crc_rx != crc_calc) return false;

  header_out = header;
  payload_out = raw_buffer + sizeof(PacketHeader);
  return true;
}

}  // namespace BrushProtocol

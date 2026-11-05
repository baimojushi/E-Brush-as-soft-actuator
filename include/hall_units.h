#pragma once
#include <stdint.h>

// Stable Hall model IDs transported in EBrushLink::Meta / BRUSH-V1 HELLO+HEALTH.
// The existing C3 -> S3 -> Studio payload sizes and version stay unchanged.
namespace HallUnits {
constexpr uint8_t TMAG5273_A1 = 1;
constexpr uint8_t TMAG5273_A2 = 2;
constexpr uint8_t MMC5983MA = 3;

constexpr float lsbPerMilliTesla(uint8_t variant) {
  return variant == TMAG5273_A1 ? 820.0f :
         variant == TMAG5273_A2 ? 250.0f :
         variant == MMC5983MA ? 40960.0f : 0.0f;
}

// MMC5983MA 18-bit unsigned sample: 131072 at zero field, 16384 LSB/G.
// Send its upper 16 bits with the zero-field offset removed. This represents
// +/- 8 G without overflow: 4096 LSB/G = 40960 LSB/mT on the old int16 link.
// The lower 2 bits are intentionally discarded; do NOT cast unsigned18 to int16.
constexpr int16_t mmc18ToLink16(uint32_t raw18) {
  return static_cast<int16_t>(static_cast<int32_t>((raw18 & 0x3ffffu) >> 2) - 32768);
}
}

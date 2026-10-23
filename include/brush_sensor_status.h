#pragma once
#include <stdint.h>
#include "brush_protocol.h"

// C3 timestamps are 32-bit microseconds and wrap every ~71.6 minutes.
// An unsigned 32-bit subtraction is intentionally wrap-safe for small ages.
namespace BrushSensors {
inline uint32_t sampleAgeUs32(uint32_t now_us32, uint32_t last_us32) {
  return last_us32 ? static_cast<uint32_t>(now_us32 - last_us32) : UINT32_MAX;
}

inline uint32_t imuValidityBits(bool sensor_ready, bool read_fresh,
                                uint32_t now_us32, uint32_t last_us32,
                                uint32_t max_stale_us) {
  if (!sensor_ready || sampleAgeUs32(now_us32, last_us32) > max_stale_us) {
    return 0u;
  }
  // VALID means a recent (possibly cached) IMU sample exists;
  // FRESH means this cycle produced a new accelerometer+gyro observation.
  return BrushProtocol::STATUS_IMU_VALID |
      (read_fresh ? BrushProtocol::STATUS_IMU_FRESH : 0u);
}
}  // namespace BrushSensors

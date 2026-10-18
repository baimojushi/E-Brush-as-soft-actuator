#pragma once
#include <stdint.h>

namespace Adaptive {
struct Output {
  float hall_mT[3]{};
  float motion_accel_g[3]{};
  float motion_energy = 0;
  float confidence = 0;
  float jerk_g_per_s = 0;
  uint32_t flags = 0;
};

class Filter {
 public:
  void reset();
  Output update(const float hall_mT[3], bool hall_valid,
                const float accel_g[3], bool imu_valid,
                float dt_s, uint32_t missing_frames);
 private:
  bool hall_ready_ = false;
  bool accel_ready_ = false;
  float hall_lp_[3]{};
  float grav_lp_[3]{};
  float motion_lp_[3]{};
  float prev_motion_[3]{};
  float confidence_lp_ = 0;
};
} // namespace Adaptive

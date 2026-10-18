#include "s3_adaptive.h"
#include <math.h>

namespace Adaptive {
namespace {
float clamp(float x, float lo, float hi) {
  if (!(x == x)) return lo;  // Reject NaNs.
  return x < lo ? lo : (x > hi ? hi : x);
}
float length3(const float v[3]) {
  return sqrtf(v[0]*v[0] + v[1]*v[1] + v[2]*v[2]);
}
}
void Filter::reset() { *this = Filter{}; }
Output Filter::update(const float hall[3], bool hall_valid,
                      const float accel[3], bool imu_valid,
                      float dt, uint32_t missed) {
  Output out{};
  dt = clamp(dt, 0.002f, 0.050f);
  float trust = (hall_valid ? 0.5f : 0.f) + (imu_valid ? 0.5f : 0.f);
  if (missed) {
    trust *= 1.f / (1.f + clamp(static_cast<float>(missed), 0.f, 50.f)*0.10f);
    out.flags |= 4;
  }
  if (!hall_valid) out.flags |= 1;
  if (!imu_valid) out.flags |= 2;

  if (hall_valid) {
    if (!hall_ready_) {
      for (int i = 0; i < 3; ++i) hall_lp_[i] = hall[i];
      hall_ready_ = true;
    } else {
      float diff_sq = 0;
      for (int i = 0; i < 3; ++i) {
        const float d = hall[i] - hall_lp_[i];
        diff_sq += d*d;
      }
      const float diff = sqrtf(diff_sq);
      if (diff > 15.f) {
        trust *= 0.6f;
        out.flags |= 8; // high innovation; not a hard removal
      }
      // Responsive on movement, low-pass only extreme innovations.
      const float alpha = diff > 15.f ? 0.15f : clamp(dt / (0.014f + dt), 0.1f, 0.7f);
      for (int i = 0; i < 3; ++i) hall_lp_[i] += alpha * (hall[i] - hall_lp_[i]);
    }
  }
  if (imu_valid) {
    if (!accel_ready_) {
      for (int i = 0; i < 3; ++i) grav_lp_[i] = accel[i];
      accel_ready_ = true;
    }
    // Slowly estimate gravity as a baseline. This is a motion visualization
    // signal, NOT absolute translation or metric linear acceleration.
    const float grav_alpha = clamp(dt / (0.45f + dt), 0.002f, 0.15f);
    const float motion_alpha = clamp(dt / (0.025f + dt), 0.05f, 0.45f);
    for (int i = 0; i < 3; ++i) {
      grav_lp_[i] += grav_alpha * (accel[i] - grav_lp_[i]);
      const float dynamic_g = accel[i] - grav_lp_[i];
      motion_lp_[i] += motion_alpha * (dynamic_g - motion_lp_[i]);
    }
    float jerk_vec[3]{};
    for (int i = 0; i < 3; ++i) {
      jerk_vec[i] = (motion_lp_[i] - prev_motion_[i]) / dt;
      prev_motion_[i] = motion_lp_[i];
    }
    out.jerk_g_per_s = clamp(length3(jerk_vec), 0, 200.f);
    out.motion_energy = clamp(length3(motion_lp_), 0, 16.f);
  }
  for (int i = 0; i < 3; ++i) {
    out.hall_mT[i] = hall_lp_[i];
    out.motion_accel_g[i] = motion_lp_[i];
  }
  confidence_lp_ += 0.28f * (trust - confidence_lp_);
  out.confidence = clamp(confidence_lp_, 0.f, 1.f);
  return out;
}
} // namespace Adaptive

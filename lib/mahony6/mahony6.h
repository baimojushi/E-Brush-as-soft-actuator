#pragma once

class Mahony6 {
 public:
  void reset();
  bool update(float gx_rad_s, float gy_rad_s, float gz_rad_s,
              float ax_g, float ay_g, float az_g,
              float dt_s);
  void quaternion(float& w, float& x, float& y, float& z) const;

 private:
  float q0_ = 1.0f;
  float q1_ = 0.0f;
  float q2_ = 0.0f;
  float q3_ = 0.0f;
  float integral_x_ = 0.0f;
  float integral_y_ = 0.0f;
  float integral_z_ = 0.0f;
};

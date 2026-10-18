#include "mahony6.h"
#include <math.h>

void Mahony6::reset() {
  q0_ = 1.0f; q1_ = q2_ = q3_ = 0.0f;
  integral_x_ = integral_y_ = integral_z_ = 0.0f;
}

bool Mahony6::update(float gx, float gy, float gz,
                     float ax, float ay, float az,
                     float dt) {
  if (!(dt > 0.0002f && dt < 0.05f)) return false;

  const float an2 = ax * ax + ay * ay + az * az;
  if (an2 < 1e-8f) return false;

  const float inv_an = 1.0f / sqrtf(an2);
  ax *= inv_an; ay *= inv_an; az *= inv_an;

  // 由当前四元数估计重力方向
  const float vx = 2.0f * (q1_ * q3_ - q0_ * q2_);
  const float vy = 2.0f * (q0_ * q1_ + q2_ * q3_);
  const float vz = q0_ * q0_ - q1_ * q1_ - q2_ * q2_ + q3_ * q3_;

  // 测量重力与估计重力的叉乘误差
  const float ex = ay * vz - az * vy;
  const float ey = az * vx - ax * vz;
  const float ez = ax * vy - ay * vx;

  // 对毛笔动态，积分项关闭以避免长时间线加速度造成积分偏置。
  constexpr float Kp = 1.8f;
  gx += Kp * ex;
  gy += Kp * ey;
  gz += Kp * ez;

  const float half_dt = 0.5f * dt;
  const float qa = q0_, qb = q1_, qc = q2_, qd = q3_;

  q0_ += (-qb * gx - qc * gy - qd * gz) * half_dt;
  q1_ += ( qa * gx + qc * gz - qd * gy) * half_dt;
  q2_ += ( qa * gy - qb * gz + qd * gx) * half_dt;
  q3_ += ( qa * gz + qb * gy - qc * gx) * half_dt;

  const float qn2 = q0_ * q0_ + q1_ * q1_ + q2_ * q2_ + q3_ * q3_;
  if (qn2 < 1e-8f) {
    reset();
    return false;
  }
  const float inv_qn = 1.0f / sqrtf(qn2);
  q0_ *= inv_qn; q1_ *= inv_qn; q2_ *= inv_qn; q3_ *= inv_qn;
  return true;
}

void Mahony6::quaternion(float& w, float& x, float& y, float& z) const {
  w = q0_; x = q1_; y = q2_; z = q3_;
}

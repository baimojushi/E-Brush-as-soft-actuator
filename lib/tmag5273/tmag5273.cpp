#include "tmag5273.h"

namespace {
constexpr uint8_t REG_DEVICE_CONFIG_1 = 0x00;
constexpr uint8_t REG_DEVICE_CONFIG_2 = 0x01;
constexpr uint8_t REG_SENSOR_CONFIG_1 = 0x02;
constexpr uint8_t REG_SENSOR_CONFIG_2 = 0x03;
constexpr uint8_t REG_T_CONFIG        = 0x07;
constexpr uint8_t REG_INT_CONFIG_1    = 0x08;
constexpr uint8_t REG_DEVICE_ID       = 0x0D;
constexpr uint8_t REG_MANUF_LSB       = 0x0E;
constexpr uint8_t REG_MANUF_MSB       = 0x0F;
constexpr uint8_t REG_T_MSB_RESULT    = 0x10;
constexpr uint8_t REG_DEVICE_STATUS   = 0x1C;
}

bool Tmag5273::begin(TwoWire& wire, uint8_t address, uint8_t conv_avg_code, bool low_noise) {
  wire_ = &wire;
  address_ = address;
  delay(3);

  if (!verifyIdentity()) return false;

  // 不启用 I2C CRC，便于与通用 I2C 控制器直接互操作。
  // CONV_AVG=101b -> 32x 平均。
  if (!writeReg(REG_DEVICE_CONFIG_1, static_cast<uint8_t>((conv_avg_code & 0x07u) << 2))) return false;

  // MAG_CH_EN=0111b -> X/Y/Z 全开。
  if (!writeReg(REG_SENSOR_CONFIG_1, 0x70)) return false;

  // A2 默认量程：X/Y/Z ±133 mT。ANGLE/THRESHOLD 全关，保留纯原始磁场语义。
  if (!writeReg(REG_SENSOR_CONFIG_2, 0x00)) return false;

  // 温度通道开启，原始温度码随帧保留。
  if (!writeReg(REG_T_CONFIG, 0x01)) return false;

  // RSLT_INT=1；INT_STATE=0(锁存)；INT_MODE=001b(INT 引脚)。
  // 锁存中断避免 10 us 脉冲在高负载时漏采；任意有效 I2C 寻址会清除锁存。
  if (!writeReg(REG_INT_CONFIG_1, 0x84)) return false;

  // LP_LN=1(低噪声，可配置)；I2C glitch filter 保持开启；OPERATING_MODE=10b 连续测量。
  const uint8_t dev_cfg2 = static_cast<uint8_t>((low_noise ? 0x10 : 0x00) | 0x02);
  if (!writeReg(REG_DEVICE_CONFIG_2, dev_cfg2)) return false;

  // 回读关键配置，避免总线瞬态导致“写入成功但状态未生效”。
  uint8_t v = 0;
  if (!readReg(REG_SENSOR_CONFIG_1, v) || (v & 0xF0u) != 0x70u) return false;
  if (!readReg(REG_DEVICE_CONFIG_2, v) || (v & 0x03u) != 0x02u) return false;

  return true;
}

bool Tmag5273::verifyIdentity() {
  if (!wire_) return false;

  uint8_t lsb = 0, msb = 0, dev = 0;
  if (!readReg(REG_MANUF_LSB, lsb)) return false;
  if (!readReg(REG_MANUF_MSB, msb)) return false;
  if (lsb != 0x49 || msb != 0x54) return false;  // TI manufacturer ID

  if (!readReg(REG_DEVICE_ID, dev)) return false;
  variant_ = dev & 0x03u;
  return variant_ == 1 || variant_ == 2;
}

bool Tmag5273::readSample(Sample& out) {
  uint8_t buf[9] = {};
  if (!readBytes(REG_T_MSB_RESULT, buf, sizeof(buf))) return false;

  out.temp_raw = static_cast<int16_t>((static_cast<uint16_t>(buf[0]) << 8) | buf[1]);
  out.x_raw    = static_cast<int16_t>((static_cast<uint16_t>(buf[2]) << 8) | buf[3]);
  out.y_raw    = static_cast<int16_t>((static_cast<uint16_t>(buf[4]) << 8) | buf[5]);
  out.z_raw    = static_cast<int16_t>((static_cast<uint16_t>(buf[6]) << 8) | buf[7]);
  out.conv_status = buf[8];

  if (out.conv_status & 0x02u) {
    uint8_t ds = 0;
    if (readDeviceStatus(ds)) out.device_status = ds;
  } else {
    out.device_status = 0;
  }
  return (out.conv_status & 0x01u) != 0;
}

bool Tmag5273::readDeviceStatus(uint8_t& status) {
  return readReg(REG_DEVICE_STATUS, status);
}

bool Tmag5273::writeReg(uint8_t reg, uint8_t value) {
  if (!wire_) return false;
  wire_->beginTransmission(address_);
  wire_->write(reg);
  wire_->write(value);
  return wire_->endTransmission(true) == 0;
}

bool Tmag5273::readReg(uint8_t reg, uint8_t& value) {
  return readBytes(reg, &value, 1);
}

bool Tmag5273::readBytes(uint8_t start_reg, uint8_t* dst, size_t len) {
  if (!wire_ || !dst || len == 0) return false;

  wire_->beginTransmission(address_);
  wire_->write(start_reg);
  if (wire_->endTransmission(false) != 0) return false;

  const size_t got = wire_->requestFrom(static_cast<int>(address_), static_cast<int>(len), static_cast<int>(true));
  if (got != len) {
    while (wire_->available()) (void)wire_->read();
    return false;
  }

  for (size_t i = 0; i < len; ++i) {
    if (!wire_->available()) return false;
    dst[i] = static_cast<uint8_t>(wire_->read());
  }
  return true;
}

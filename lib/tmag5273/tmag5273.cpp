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
  variant_ = 0;
  device_id_raw_ = 0;
  manufacturer_lsb_ = 0;
  manufacturer_msb_ = 0;
  last_init_error_ = InitError::NONE;
  delay(3);

  if (!verifyIdentity()) return false;

  // CONV_AVG=101b -> 32x 平均；I²C CRC 保持关闭。
  if (!writeReg(REG_DEVICE_CONFIG_1, static_cast<uint8_t>((conv_avg_code & 0x07u) << 2))) {
    last_init_error_ = InitError::CONFIG_WRITE;
    return false;
  }

  // MAG_CH_EN=0111b -> X/Y/Z 全开。
  if (!writeReg(REG_SENSOR_CONFIG_1, 0x70)) {
    last_init_error_ = InitError::CONFIG_WRITE;
    return false;
  }

  // _RANGE=0：
  // VER=1 -> ±40 mT；VER=2 -> ±133 mT。
  // 主机按 DEVICE_ID.VER 选择灵敏度，原始 16-bit 码保持不变。
  if (!writeReg(REG_SENSOR_CONFIG_2, 0x00)) {
    last_init_error_ = InitError::CONFIG_WRITE;
    return false;
  }

  // 温度通道开启。
  if (!writeReg(REG_T_CONFIG, 0x01)) {
    last_init_error_ = InitError::CONFIG_WRITE;
    return false;
  }

  // 转换结果中断，锁存，经 INT 引脚输出。
  if (!writeReg(REG_INT_CONFIG_1, 0x84)) {
    last_init_error_ = InitError::CONFIG_WRITE;
    return false;
  }

  // LP_LN=1(低噪声，可配置)；OPERATING_MODE=10b 连续测量。
  const uint8_t dev_cfg2 = static_cast<uint8_t>((low_noise ? 0x10 : 0x00) | 0x02);
  if (!writeReg(REG_DEVICE_CONFIG_2, dev_cfg2)) {
    last_init_error_ = InitError::CONFIG_WRITE;
    return false;
  }

  uint8_t v = 0;
  if (!readReg(REG_SENSOR_CONFIG_1, v) || (v & 0xF0u) != 0x70u) {
    last_init_error_ = InitError::CONFIG_READBACK;
    return false;
  }
  if (!readReg(REG_DEVICE_CONFIG_2, v) || (v & 0x03u) != 0x02u) {
    last_init_error_ = InitError::CONFIG_READBACK;
    return false;
  }

  last_init_error_ = InitError::NONE;
  return true;
}

bool Tmag5273::verifyIdentity() {
  if (!wire_) return false;

  uint8_t lsb = 0, msb = 0, dev = 0;

  if (!readReg(REG_MANUF_LSB, lsb)) {
    last_init_error_ = InitError::MANUFACTURER_LSB_READ;
    return false;
  }
  manufacturer_lsb_ = lsb;

  if (!readReg(REG_MANUF_MSB, msb)) {
    last_init_error_ = InitError::MANUFACTURER_MSB_READ;
    return false;
  }
  manufacturer_msb_ = msb;

  if (lsb != 0x49 || msb != 0x54) {
    last_init_error_ = InitError::MANUFACTURER_MISMATCH;
    return false;
  }

  if (!readReg(REG_DEVICE_ID, dev)) {
    last_init_error_ = InitError::DEVICE_ID_READ;
    return false;
  }
  device_id_raw_ = dev;
  variant_ = dev & 0x03u;

  if (variant_ != 1 && variant_ != 2) {
    last_init_error_ = InitError::DEVICE_VARIANT_INVALID;
    return false;
  }

  last_init_error_ = InitError::NONE;
  return true;
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

  const size_t got = wire_->requestFrom(
      static_cast<int>(address_),
      static_cast<int>(len),
      static_cast<int>(true));

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

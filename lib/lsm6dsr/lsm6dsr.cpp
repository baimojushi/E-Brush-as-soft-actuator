#include "lsm6dsr.h"

namespace {
constexpr uint8_t REG_WHO_AM_I = 0x0F;
constexpr uint8_t REG_CTRL1_XL = 0x10;
constexpr uint8_t REG_CTRL2_G  = 0x11;
constexpr uint8_t REG_CTRL3_C  = 0x12;
constexpr uint8_t REG_CTRL4_C  = 0x13;
constexpr uint8_t REG_STATUS   = 0x1E;
constexpr uint8_t REG_OUT_TEMP_L = 0x20;
}

bool Lsm6dsr::begin(SPIClass& spi, uint8_t cs_pin, uint32_t spi_hz) {
  spi_ = &spi;
  cs_pin_ = cs_pin;
  spi_hz_ = spi_hz;

  pinMode(cs_pin_, OUTPUT);
  digitalWrite(cs_pin_, HIGH);
  delay(2);

  if (!verifyIdentity()) return false;

  // 软件复位
  if (!writeReg(REG_CTRL3_C, 0x01)) return false;
  const uint32_t t0 = millis();
  uint8_t v = 0;
  do {
    delay(1);
    if (!readReg(REG_CTRL3_C, v)) return false;
  } while ((v & 0x01u) && (millis() - t0 < 100));
  if (v & 0x01u) return false;

  // BDU=1，IF_INC=1，四线 SPI(SIM=0)
  if (!writeReg(REG_CTRL3_C, 0x44)) return false;

  // CTRL1_XL: ODR=208 Hz(0101), FS=±4g(10), LPF2 off -> 0x58
  if (!writeReg(REG_CTRL1_XL, 0x58)) return false;

  // CTRL2_G: ODR=208 Hz(0101), FS=±1000 dps(10), FS_125=0, FS_4000=0 -> 0x58
  if (!writeReg(REG_CTRL2_G, 0x58)) return false;

  // I2C_disable=1，避免模块上未使用的 I2C 入口带来干扰。
  if (!writeReg(REG_CTRL4_C, 0x04)) return false;

  // 回读
  if (!readReg(REG_CTRL1_XL, v) || v != 0x58) return false;
  if (!readReg(REG_CTRL2_G, v) || v != 0x58) return false;
  if (!verifyIdentity()) return false;

  return true;
}

bool Lsm6dsr::verifyIdentity() {
  if (!spi_) return false;
  uint8_t v = 0;
  if (!readReg(REG_WHO_AM_I, v)) return false;
  who_am_i_ = v;
  return v == 0x6B;
}

bool Lsm6dsr::readSample(Sample& out, bool& fresh) {
  uint8_t status = 0;
  if (!readReg(REG_STATUS, status)) return false;
  out.status = status;

  // GDA(bit1) 与 XLDA(bit0) 同时到新数据时视为 fresh。
  fresh = (status & 0x03u) == 0x03u;

  uint8_t buf[14] = {};
  if (!readBytes(REG_OUT_TEMP_L, buf, sizeof(buf))) return false;

  auto le16 = [](const uint8_t* p) -> int16_t {
    return static_cast<int16_t>(static_cast<uint16_t>(p[0]) |
                                (static_cast<uint16_t>(p[1]) << 8));
  };

  out.temp_raw = le16(buf + 0);
  out.gx_raw = le16(buf + 2);
  out.gy_raw = le16(buf + 4);
  out.gz_raw = le16(buf + 6);
  out.ax_raw = le16(buf + 8);
  out.ay_raw = le16(buf + 10);
  out.az_raw = le16(buf + 12);
  return true;
}

bool Lsm6dsr::writeReg(uint8_t reg, uint8_t value) {
  if (!spi_) return false;
  SPISettings settings(spi_hz_, MSBFIRST, SPI_MODE3);
  spi_->beginTransaction(settings);
  digitalWrite(cs_pin_, LOW);
  spi_->transfer(reg & 0x7Fu);
  spi_->transfer(value);
  digitalWrite(cs_pin_, HIGH);
  spi_->endTransaction();
  return true;
}

bool Lsm6dsr::readReg(uint8_t reg, uint8_t& value) {
  return readBytes(reg, &value, 1);
}

bool Lsm6dsr::readBytes(uint8_t start_reg, uint8_t* dst, size_t len) {
  if (!spi_ || !dst || len == 0) return false;
  SPISettings settings(spi_hz_, MSBFIRST, SPI_MODE3);
  spi_->beginTransaction(settings);
  digitalWrite(cs_pin_, LOW);
  spi_->transfer(start_reg | 0x80u);
  for (size_t i = 0; i < len; ++i) dst[i] = spi_->transfer(0x00);
  digitalWrite(cs_pin_, HIGH);
  spi_->endTransaction();
  return true;
}

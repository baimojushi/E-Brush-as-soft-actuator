#include "mmc5983ma.h"

namespace {
constexpr uint8_t REG_DATA = 0x00;
constexpr uint8_t REG_STATUS = 0x08;
constexpr uint8_t REG_CTRL0 = 0x09;
constexpr uint8_t REG_CTRL1 = 0x0A;
constexpr uint8_t REG_CTRL2 = 0x0B;
constexpr uint8_t REG_PRODUCT_ID = 0x2F;
constexpr uint8_t PRODUCT_ID = 0x30;
constexpr uint8_t STATUS_MAG_DONE = 0x01;
}

bool Mmc5983ma::begin(TwoWire& wire, uint8_t address) {
  wire_ = &wire;
  address_ = address;
  product_id_ = 0;
  error_ = InitError::NONE;
  delay(12);  // power-up / OTP load needs approximately 10 ms

  if (!readReg(REG_PRODUCT_ID, product_id_)) {
    error_ = InitError::PRODUCT_ID_READ;
    return false;
  }
  if (product_id_ != PRODUCT_ID) {
    error_ = InitError::PRODUCT_ID_MISMATCH;
    return false;
  }

  // Stop any pre-existing CMM before reconfiguring; use BW=01 (4 ms).
  // MEMSIC Rev A p15-16: BW=01, CM_Freq=110 -> 200 Hz typical.
  if (!writeReg(REG_CTRL2, 0x00) ||
      !writeReg(REG_CTRL1, 0x01)) {
    error_ = InitError::CONFIG_WRITE;
    return false;
  }
  // Restore sensor magnetization, then enable automatic SET/RESET.
  if (!writeReg(REG_CTRL0, 0x08)) {
    error_ = InitError::CONFIG_WRITE;
    return false;
  }
  delay(1);
  if (!writeReg(REG_CTRL0, 0x20) ||
      !writeReg(REG_STATUS, STATUS_MAG_DONE)) {
    error_ = InitError::CONFIG_WRITE;
    return false;
  }
  // En_prd_set=1; PRD_SET=011 (100 measurements); Cmm_en=1;
  // CM_Freq=110 (200 Hz). AUTO_SR_EN in CTRL0 enables the periodic coil pulse.
  if (!writeReg(REG_CTRL2, 0xBE)) {
    error_ = InitError::CONFIG_WRITE;
    return false;
  }
  return true;
}

uint32_t Mmc5983ma::unpack18(const uint8_t* b, uint8_t axis) {
  if (!b || axis > 2) return 0;
  const uint8_t offset = static_cast<uint8_t>(axis * 2);
  const uint8_t low_shift = static_cast<uint8_t>(6 - axis * 2);
  return (static_cast<uint32_t>(b[offset]) << 10) |
         (static_cast<uint32_t>(b[offset + 1]) << 2) |
         ((b[6] >> low_shift) & 0x03u);
}

bool Mmc5983ma::readSample(Sample& out, bool& fresh) {
  fresh = false;
  if (!wire_) return false;
  uint8_t status = 0;
  if (!readReg(REG_STATUS, status)) return false;
  if (!(status & STATUS_MAG_DONE)) return true;

  uint8_t bytes[7] = {};
  if (!readBytes(REG_DATA, bytes, sizeof(bytes))) return false;
  // MMC STATUS is write-1-to-clear; without this, every poll would read as
  // fresh even when the magnetometer has produced no new measurement.
  if (!writeReg(REG_STATUS, STATUS_MAG_DONE)) return false;

  Sample value{};
  value.x_raw = HallUnits::mmc18ToLink16(unpack18(bytes, 0));
  value.y_raw = HallUnits::mmc18ToLink16(unpack18(bytes, 1));
  value.z_raw = HallUnits::mmc18ToLink16(unpack18(bytes, 2));
  value.conv_status = status;
  value.device_status = status;
  out = value;
  fresh = true;
  return true;
}

bool Mmc5983ma::writeReg(uint8_t reg, uint8_t val) {
  if (!wire_) return false;
  wire_->beginTransmission(address_);
  wire_->write(reg);
  wire_->write(val);
  return wire_->endTransmission(true) == 0;
}

bool Mmc5983ma::readReg(uint8_t reg, uint8_t& val) {
  return readBytes(reg, &val, 1);
}

bool Mmc5983ma::readBytes(uint8_t reg, uint8_t* bytes, size_t n) {
  if (!wire_ || !bytes || n == 0) return false;
  wire_->beginTransmission(address_);
  wire_->write(reg);
  if (wire_->endTransmission(false) != 0) return false;
  const size_t received = wire_->requestFrom(
      static_cast<int>(address_), static_cast<int>(n), static_cast<int>(true));
  if (received != n) {
    while (wire_->available()) (void)wire_->read();
    return false;
  }
  for (size_t i = 0; i < n; ++i) {
    if (!wire_->available()) return false;
    bytes[i] = static_cast<uint8_t>(wire_->read());
  }
  return true;
}

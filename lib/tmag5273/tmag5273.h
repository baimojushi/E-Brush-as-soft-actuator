#pragma once
#include <Arduino.h>
#include <Wire.h>

class Tmag5273 {
 public:
  enum class InitError : uint8_t {
    NONE = 0,
    MANUFACTURER_LSB_READ = 1,
    MANUFACTURER_MSB_READ = 2,
    MANUFACTURER_MISMATCH = 3,
    DEVICE_ID_READ = 4,
    DEVICE_VARIANT_INVALID = 5,
    CONFIG_WRITE = 6,
    CONFIG_READBACK = 7,
    NO_CANDIDATE_ACK = 8,
  };

  struct Sample {
    int16_t temp_raw = 0;
    int16_t x_raw = 0;
    int16_t y_raw = 0;
    int16_t z_raw = 0;
    uint8_t conv_status = 0;
    uint8_t device_status = 0;
  };

  bool begin(TwoWire& wire, uint8_t address, uint8_t conv_avg_code, bool low_noise);
  bool readSample(Sample& out);
  bool readDeviceStatus(uint8_t& status);
  bool verifyIdentity();

  uint8_t variant() const { return variant_; }
  uint8_t address() const { return address_; }
  uint8_t deviceIdRaw() const { return device_id_raw_; }
  uint8_t manufacturerLsb() const { return manufacturer_lsb_; }
  uint8_t manufacturerMsb() const { return manufacturer_msb_; }
  InitError lastInitError() const { return last_init_error_; }

 private:
  bool writeReg(uint8_t reg, uint8_t value);
  bool readReg(uint8_t reg, uint8_t& value);
  bool readBytes(uint8_t start_reg, uint8_t* dst, size_t len);

  TwoWire* wire_ = nullptr;
  uint8_t address_ = 0x35;
  uint8_t variant_ = 0;
  uint8_t device_id_raw_ = 0;
  uint8_t manufacturer_lsb_ = 0;
  uint8_t manufacturer_msb_ = 0;
  InitError last_init_error_ = InitError::NONE;
};

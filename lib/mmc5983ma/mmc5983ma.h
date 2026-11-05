#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <stdint.h>
#include "hall_units.h"

// MEMSIC MMC5983MA, I2C (0x30), 200 Hz continuous conversion.
// Only magnetic data are sampled; the temperature conversion is not enabled.
class Mmc5983ma {
 public:
  enum class InitError : uint8_t {
    NONE = 0,
    PRODUCT_ID_READ = 9,
    PRODUCT_ID_MISMATCH = 10,
    CONFIG_WRITE = 11,
    STATUS_READ = 12,
  };
  struct Sample {
    int16_t temp_raw = 0;       // Not available in this mode (Studio -> NaN).
    int16_t x_raw = 0;          // signed 16-bit (40960 counts / mT)
    int16_t y_raw = 0;
    int16_t z_raw = 0;
    uint8_t conv_status = 0;   // MMC register 0x08, NOT TMAG conversion bits
    uint8_t device_status = 0; // same MMC status byte for troubleshooting
  };

  bool begin(TwoWire& wire, uint8_t address = 0x30);
  // true means the bus operation was successful; fresh=false means no new
  // measurement at this polling instant, so the caller must retain its last
  // value and timestamp rather than counting a sample or I2C failure.
  bool readSample(Sample& out, bool& fresh);
  uint8_t productId() const { return product_id_; }
  uint8_t address() const { return address_; }
  InitError lastInitError() const { return error_; }
  // Public for deterministic host-side decoding regression tests.
  static uint32_t unpack18(const uint8_t* bytes, uint8_t axis);

 private:
  bool writeReg(uint8_t reg, uint8_t val);
  bool readReg(uint8_t reg, uint8_t& val);
  bool readBytes(uint8_t reg, uint8_t* bytes, size_t n);

  TwoWire* wire_ = nullptr;
  uint8_t address_ = 0x30;
  uint8_t product_id_ = 0;
  InitError error_ = InitError::NONE;
};

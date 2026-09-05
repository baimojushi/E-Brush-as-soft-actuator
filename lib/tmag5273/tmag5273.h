#pragma once
#include <Arduino.h>
#include <Wire.h>

class Tmag5273 {
 public:
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

 private:
  bool writeReg(uint8_t reg, uint8_t value);
  bool readReg(uint8_t reg, uint8_t& value);
  bool readBytes(uint8_t start_reg, uint8_t* dst, size_t len);

  TwoWire* wire_ = nullptr;
  uint8_t address_ = 0x35;
  uint8_t variant_ = 0;
};

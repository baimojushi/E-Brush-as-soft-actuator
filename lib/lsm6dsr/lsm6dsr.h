#pragma once
#include <Arduino.h>
#include <SPI.h>

class Lsm6dsr {
 public:
  struct Sample {
    uint8_t status = 0;
    int16_t temp_raw = 0;
    int16_t gx_raw = 0;
    int16_t gy_raw = 0;
    int16_t gz_raw = 0;
    int16_t ax_raw = 0;
    int16_t ay_raw = 0;
    int16_t az_raw = 0;
  };

  bool begin(SPIClass& spi, uint8_t cs_pin, uint32_t spi_hz);
  bool readSample(Sample& out, bool& fresh);
  bool verifyIdentity();
  uint8_t whoAmI() const { return who_am_i_; }

 private:
  bool writeReg(uint8_t reg, uint8_t value);
  bool readReg(uint8_t reg, uint8_t& value);
  bool readBytes(uint8_t start_reg, uint8_t* dst, size_t len);

  SPIClass* spi_ = nullptr;
  uint8_t cs_pin_ = 0;
  uint32_t spi_hz_ = 8000000;
  uint8_t who_am_i_ = 0;
};

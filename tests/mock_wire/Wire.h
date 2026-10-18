#pragma once
#include "Arduino.h"
#include <array>

// Injectable I2C model used only in the native Hall conversion test.
struct TwoWire {
  explicit TwoWire(int) {}
  std::array<uint8_t, 32> regs{};
  uint8_t reg = 0;
  uint8_t ptr = 0;
  int rx_remaining = 0;
  bool write_phase = false;
  bool fail_next_read = false;
  bool begin(int,int,uint32_t) { return true; }
  void end() {}
  void setTimeOut(uint16_t) {}
  void beginTransmission(uint8_t) { write_phase = false; }
  size_t write(uint8_t value) {
    if (!write_phase) { reg = value; ptr = value; write_phase = true; }
    else if (reg < regs.size()) regs[reg++] = value;
    return 1;
  }
  uint8_t endTransmission(bool=true) { return 0; }
  uint8_t requestFrom(int,int n,bool) {
    if (fail_next_read) { fail_next_read = false; return 0; }
    rx_remaining = n;
    return static_cast<uint8_t>(n);
  }
  int available() { return rx_remaining; }
  int read() { if (!rx_remaining--) return -1; return ptr < regs.size() ? regs[ptr++] : 0; }
};

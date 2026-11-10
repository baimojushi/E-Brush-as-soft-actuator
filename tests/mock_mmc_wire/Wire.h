#pragma once
#include "Arduino.h"
#include <array>
struct TwoWire {
  explicit TwoWire(int) {}
  std::array<uint8_t, 64> regs{};
  uint8_t index=0, ptr=0;
  bool writing=false, fail_next_read=false, fail_next_write=false;
  size_t remaining=0;
  int read_count=0;
  bool begin(int,int,uint32_t) { return true; }
  void end() {}
  void setTimeOut(uint16_t) {}
  void beginTransmission(uint8_t) { writing=false; }
  size_t write(uint8_t value) {
    if (!writing) { ptr = index = value; writing=true; }
    else if (index < regs.size()) {
      if (index == 0x08) regs[index] &= static_cast<uint8_t>(~value); // W1C
      else regs[index] = value;
      ++index;
    }
    return 1;
  }
  uint8_t endTransmission(bool=true) {
    if (fail_next_write) { fail_next_write=false; return 4; }
    return 0;
  }
  uint8_t requestFrom(int,int n,bool) {
    if (fail_next_read) { fail_next_read=false; return 0; }
    remaining = static_cast<size_t>(n);
    ++read_count;
    return static_cast<uint8_t>(n);
  }
  int available() { return static_cast<int>(remaining); }
  int read() {
    if (!remaining--) return -1;
    return ptr < regs.size() ? regs[ptr++] : 0;
  }
};

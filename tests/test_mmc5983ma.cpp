#include "mmc5983ma.h"
#include <cassert>
#include <cmath>
#include <cstdio>

static void setAxis(TwoWire& wire, int axis, uint32_t raw18) {
  wire.regs[axis*2] = static_cast<uint8_t>(raw18 >> 10);
  wire.regs[axis*2+1] = static_cast<uint8_t>(raw18 >> 2);
  wire.regs[6] |= static_cast<uint8_t>((raw18 & 3) << (6-axis*2));
}

int main() {
  static_assert(HallUnits::mmc18ToLink16(0) == -32768,"negative full scale");
  static_assert(HallUnits::mmc18ToLink16(131072) == 0,"zero field");
  static_assert(HallUnits::mmc18ToLink16(262143) == 32767,"positive full scale");
  static_assert(HallUnits::lsbPerMilliTesla(3) == 40960.f,"MMC scale");
  static_assert(HallUnits::lsbPerMilliTesla(1) == 820.f,"TMAG A1 scale");
  static_assert(HallUnits::lsbPerMilliTesla(2) == 250.f,"TMAG A2 scale");
  static_assert(HallUnits::lsbPerMilliTesla(0) == 0.f,"unknown scale");

  TwoWire wire(0);
  Mmc5983ma m;
  // Do not treat an ACK/unknown sensor as MMC5983MA.
  wire.regs[0x2F] = 0x22;
  assert(!m.begin(wire));
  assert(m.lastInitError() == Mmc5983ma::InitError::PRODUCT_ID_MISMATCH);
  wire.fail_next_read = true;
  assert(!m.begin(wire));
  assert(m.lastInitError() == Mmc5983ma::InitError::PRODUCT_ID_READ);
  wire.regs[0x2F] = 0x30;
  assert(m.begin(wire));
  assert(m.productId() == 0x30);
  assert(wire.regs[0x09] == 0x20); // Auto SET/RESET
  assert(wire.regs[0x0A] == 0x01); // BW=01, 4ms
  assert(wire.regs[0x0B] == 0xBE); // 200Hz, periodic SET

  Mmc5983ma::Sample sample{};
  bool fresh=true;
  assert(m.readSample(sample,fresh));
  assert(!fresh);                 // no new conversion, no false success
  // Prepare 18-bit values: -0.4mT, zero, +0.4mT.
  setAxis(wire, 0, 65536);
  setAxis(wire, 1, 131072);
  setAxis(wire, 2, 196608);
  wire.regs[0x08] = 1;
  assert(m.readSample(sample,fresh));
  assert(fresh);
  assert(sample.x_raw == -16384);
  assert(sample.y_raw == 0);
  assert(sample.z_raw == 16384);
  assert(std::abs(sample.x_raw / HallUnits::lsbPerMilliTesla(3) + .4f) < 1e-6f);
  assert(wire.regs[0x08] == 0);  // W1C prevents fake repeated fresh reads
  fresh=true;
  assert(m.readSample(sample,fresh) && !fresh);
  assert(sample.x_raw == -16384);
  wire.regs[0x08] = 1;
  wire.fail_next_read = true;
  fresh=true;
  assert(!m.readSample(sample,fresh));
  assert(!fresh);
  wire.regs[0x08] = 1;
  // Boundaries: full negative -> signed -32768, max -> signed +32767.
  wire.regs[6] = 0;
  setAxis(wire,0,0);
  setAxis(wire,1,262143);
  setAxis(wire,2,131072);
  assert(m.readSample(sample,fresh) && fresh);
  assert(sample.x_raw == -32768 && sample.y_raw == 32767 && sample.z_raw == 0);
  std::puts("OK: MMC ID, config, 18-to-16 signed conversion, W1C, fresh, I2C errors");
}

#include "tmag5273.h"
#include <cassert>
#include <cstdio>
int main() {
  TwoWire fake(0);
  fake.regs[0x0E] = 0x49;
  fake.regs[0x0F] = 0x54;
  fake.regs[0x0D] = 1;
  Tmag5273 hall;
  assert(hall.begin(fake, 0x35, 2, false));
  assert(hall.variant() == 1);
  // Readable conversion registers, conversion-ready bit not yet set.
  Tmag5273::Sample result{};
  bool fresh = true;
  fake.regs[0x18] = 0;
  assert(hall.readSample(result, fresh));
  assert(!fresh);
  // Legacy call preserves its old semantics (no fresh conversion == false).
  assert(!hall.readSample(result));
  // New conversion is valid, with signed big-endian magnetic raw code.
  fake.regs[0x12] = 0xff;
  fake.regs[0x13] = 0x38; // -200
  fake.regs[0x18] = 0x01;
  assert(hall.readSample(result, fresh));
  assert(fresh && result.x_raw == -200);
  assert(hall.readSample(result));
  // Actual I2C failure is distinct from an empty new-data slot.
  fake.fail_next_read = true;
  assert(!hall.readSample(result, fresh));
  assert(!fresh);
  std::puts("OK: Hall not-ready vs. actual I2C error and legacy semantics");
}

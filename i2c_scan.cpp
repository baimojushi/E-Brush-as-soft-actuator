// I2C bus scanner for C3 DevKitM-1
// Scans all 7-bit addresses on the Hall I2C bus (GPIO5=SDA, GPIO6=SCL)
// Uses endTransmission(true) to release bus after each probe - avoids
// "Repeated Start" state that corrupts the scan when a phantom ACK is seen.
#include <Arduino.h>
#include <Wire.h>

constexpr int I2C_SDA = 5;
constexpr int I2C_SCL = 6;
constexpr int I2C_HZ = 50000;

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== I2C Scanner Starting ===");

  // Configure bus - do NOT enable internal pull-ups, rely on external only
  Wire.begin(I2C_SDA, I2C_SCL, I2C_HZ);
  Wire.setTimeOut(0);  // Disable timeout so we see the real error

  Serial.printf("I2C SDA=GPIO%d SCL=GPIO%d Hz=%d\n", I2C_SDA, I2C_SCL, I2C_HZ);

  // Scan all 7-bit addresses - use endTransmission(true) to release bus
  Serial.println("Scanning 0x08-0x77...");
  uint8_t found = 0;
  for (uint8_t addr = 0x08; addr <= 0x77; ++addr) {
    Wire.beginTransmission(addr);
    uint8_t err = Wire.endTransmission(true);  // true = release bus (STOP)
    if (err == 0) {
      Serial.printf("  [FOUND] 0x%02X (%d)\n", addr, addr);
      ++found;
    }
  }
  Serial.printf("Scan complete. Found %d device(s).\n", found);

  // Extended scan
  Serial.println("Scanning extended range 0x00-0x07 and 0x78-0x7F...");
  for (uint8_t addr = 0x00; addr <= 0x07; ++addr) {
    Wire.beginTransmission(addr);
    uint8_t err = Wire.endTransmission(true);
    if (err == 0) {
      Serial.printf("  [FOUND] 0x%02X (%d)\n", addr, addr);
      ++found;
    }
  }
  for (uint8_t addr = 0x78; addr <= 0x7F; ++addr) {
    Wire.beginTransmission(addr);
    uint8_t err = Wire.endTransmission(true);
    if (err == 0) {
      Serial.printf("  [FOUND] 0x%02X (%d)\n", addr, addr);
      ++found;
    }
  }
  Serial.printf("Total found: %d\n", found);

  // Read TMAG5273 registers at candidate addresses
  const uint8_t candidates[] = {0x35, 0x22, 0x78, 0x44};
  for (uint8_t addr : candidates) {
    Serial.printf("\nTrying TMAG5273 at 0x%02X...\n", addr);
    Wire.beginTransmission(addr);
    uint8_t err = Wire.endTransmission(true);
    if (err != 0) {
      Serial.printf("  No ACK (err=%d)\n", err);
      continue;
    }
    Serial.printf("  ACK received!\n");
    uint8_t reg = 0x00;
    Wire.beginTransmission(addr);
    Wire.write(reg);
    Wire.endTransmission(true);
    uint8_t buf[8] = {0};
    size_t n = Wire.requestFrom(addr, sizeof(buf));
    if (n == sizeof(buf)) {
      for (size_t i = 0; i < n; ++i) buf[i] = Wire.read();
      Serial.printf("  REG 0x00-07: %02X %02X %02X %02X %02X %02X %02X %02X\n",
        buf[0], buf[1], buf[2], buf[3], buf[4], buf[5], buf[6], buf[7]);
    } else {
      Serial.printf("  Read failed (got %d bytes)\n", n);
    }
  }
}

void loop() {
  delay(5000);
  Serial.println("--- Rescanning ---");
  uint8_t found = 0;
  for (uint8_t addr = 0x08; addr <= 0x77; ++addr) {
    Wire.beginTransmission(addr);
    uint8_t err = Wire.endTransmission(true);
    if (err == 0) {
      Serial.printf("[FOUND] 0x%02X\n", addr);
      ++found;
    }
  }
  Serial.printf("Rescan found %d device(s)\n", found);
}
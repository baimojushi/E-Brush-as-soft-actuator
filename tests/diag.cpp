/*
 * MMC5983MA 循环诊断固件
 * - 启动：快速扫描一次
 * - 之后：每 5 秒重扫一次；若发现 0x30 -> 进入持续读数模式
 * - 读数模式：每 100 ms 打印 XYZ (mG)，若连续 3 次读失败则回到扫描态
 */

#include <Arduino.h>
#include <Wire.h>
#include <SparkFun_MMC5983MA_Arduino_Library.h>

constexpr int kSDA = 5;
constexpr int kSCL = 6;
constexpr uint8_t TARGET_ADDR = 0x30;

SFE_MMC5983MA mag;

enum class State { SCANNING, READING };
State state = State::SCANNING;
uint32_t lastScan = 0;
uint8_t readFailures = 0;

uint8_t probeAddr(uint8_t addr, bool slow)
{
  if (slow) Wire.setTimeOut(200);
  Wire.beginTransmission(addr);
  uint8_t err = Wire.endTransmission(true);
  if (slow) Wire.setTimeOut(10);
  return err;
}

void scanAndTryInit()
{
  Serial.println("\n[SCAN] 0x08..0x77 (10 kHz):");
  uint8_t found = 0;
  for (uint8_t a = 0x08; a <= 0x77; ++a) {
    if (probeAddr(a, false) == 0) {
      Serial.printf("  ACK @ 0x%02X\n", a);
      ++found;
    }
  }
  Serial.printf("[SCAN] Total: %u device(s)\n", found);

  if (probeAddr(TARGET_ADDR, true) == 0) {
    Serial.println("[TRY] SparkFun begin() @ 0x30 (slow timeout)...");
    Wire.setTimeOut(200);
    bool ok = mag.begin(Wire);
    Wire.setTimeOut(10);

    if (ok) {
      Serial.println("[OK] begin() -> entering READING mode");
      Wire.setTimeOut(200);
      mag.softReset();
      Wire.setTimeOut(10);
      state = State::READING;
      readFailures = 0;
      return;
    }
    Serial.println("[FAIL] begin()");
  }
  state = State::SCANNING;
}

void setup()
{
  Serial.begin(115200);
  while (!Serial) { delay(10); }
  delay(500);
  Serial.println("\n=== MMC5983MA Loop Diag ===");
  Serial.println("SDA=5, SCL=6, 10 kHz scan, 400 kHz init");
  Serial.println("CS must be tied HIGH externally (3.3V)");

  Wire.begin(kSDA, kSCL, 10000);
  Wire.setTimeOut(10);

  scanAndTryInit();
}

void loop()
{
  static uint32_t lastHb = 0;
  uint32_t now = millis();

  // 心跳
  if (now - lastHb >= 5000) {
    lastHb = now;
    Serial.printf("[HB] uptime=%lus state=%s\n", now / 1000,
                  state == State::SCANNING ? "SCANNING" : "READING");
  }

  if (state == State::SCANNING) {
    if (now - lastScan >= 5000) {      // 每 5 秒重扫
      lastScan = now;
      scanAndTryInit();
    }
  } else { // READING
    float x = mag.getMeasurementX();
    float y = mag.getMeasurementY();
    float z = mag.getMeasurementZ();
    // getMeasurementX/Y/Z return float; check for valid readings (non-NaN, reasonable range)
    if (isfinite(x) && isfinite(y) && isfinite(z)) {
      Serial.printf("X: %.1f mG  Y: %.1f mG  Z: %.1f mG\n", x, y, z);
      readFailures = 0;
    } else {
      if (++readFailures >= 3) {
        Serial.println("[WARN] 3 consecutive read failures -> back to SCANNING");
        state = State::SCANNING;
      }
    }
    delay(100); // 10 Hz 读数
  }
}
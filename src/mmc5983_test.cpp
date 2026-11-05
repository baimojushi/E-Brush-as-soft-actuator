// Standalone ESP32-C3 USB CDC MMC5983MA diagnostic at 115200 baud.
// This sketch temporarily replaces the BLE sampling firmware.
#include <Arduino.h>
#include <Wire.h>
#include "mmc5983ma.h"
#include "node_config.h"
#include "hall_units.h"

TwoWire hallWire(0);
Mmc5983ma sensor;
bool ready = false;
uint32_t nextRetryMs = 0;
uint32_t nextPrintMs = 0;

void setup() {
  Serial.begin(115200);
  delay(1200);
  Serial.println("MMC5983MA / ESP32-C3 I2C diagnostic");
  Serial.printf("SDA=GPIO%d SCL=GPIO%d 0x30\n",
                NodeConfig::HALL_SDA, NodeConfig::HALL_SCL);
  hallWire.begin(NodeConfig::HALL_SDA, NodeConfig::HALL_SCL, 100000);
  hallWire.setTimeOut(20);
}

void loop() {
  const uint32_t now = millis();
  if (!ready) {
    if ((int32_t)(now - nextRetryMs) < 0) { delay(10); return; }
    nextRetryMs = now + 2000;
    ready = sensor.begin(hallWire, 0x30);
    Serial.printf("MMC init: %s, ID=0x%02X, error=%u\n",
                  ready ? "OK" : "FAIL", sensor.productId(),
                  static_cast<unsigned>(sensor.lastInitError()));
    if (!ready) { delay(20); return; }
  }
  Mmc5983ma::Sample sample{};
  bool fresh = false;
  if (!sensor.readSample(sample, fresh)) {
    Serial.println("MMC measurement I2C ERROR (check CS, SDA, SCL, power)");
    ready = false;
    nextRetryMs = millis() + 500;
  } else if (fresh && (int32_t)(now - nextPrintMs) >= 0) {
    nextPrintMs = now + 100;
    Serial.printf("raw16=(%d,%d,%d)  B_mT=(%.5f,%.5f,%.5f)\n",
                  sample.x_raw, sample.y_raw, sample.z_raw,
                  sample.x_raw / HallUnits::lsbPerMilliTesla(3),
                  sample.y_raw / HallUnits::lsbPerMilliTesla(3),
                  sample.z_raw / HallUnits::lsbPerMilliTesla(3));
  }
  delay(2);
}

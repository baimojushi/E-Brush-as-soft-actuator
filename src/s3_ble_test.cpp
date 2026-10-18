// Minimal BLE test for ESP32-S3: just advertise "EBrush-S3" with service UUID
// Enables USB CDC for debug output (921600)

#include <Arduino.h>
#include <WiFi.h>
#include <NimBLEDevice.h>

constexpr const char* BLE_NAME = "EBrush-S3";
constexpr const char* BLE_SERVICE = "8b962400-dad0-45a8-a8d5-4f45fd83aefe";
constexpr const char* BLE_DATA_CHAR = "8b962401-dad0-45a8-a8d5-4f45fd83aefe";

uint32_t next_status = 0;
bool adv_started = false;

void setup() {
  Serial.begin(921600);
  delay(1000);
  Serial.println("\n=== S3 Minimal BLE Test ===");
  
  Serial.printf("Chip: %s Rev %d\n", ESP.getChipModel(), ESP.getChipRevision());
  Serial.printf("MAC: %s\n", WiFi.macAddress().c_str());
  Serial.printf("Free heap: %d\n", ESP.getFreeHeap());

  // Init NimBLE
  Serial.println("Initializing NimBLE...");
  NimBLEDevice::init(BLE_NAME);
  NimBLEDevice::setMTU(247);
  NimBLEDevice::setPower(3);
  Serial.println("NimBLE init done");

  NimBLEServer* server = NimBLEDevice::createServer();
  Serial.println("Server created");

  NimBLEService* svc = server->createService(BLE_SERVICE);
  Serial.printf("Service created: %s\n", BLE_SERVICE);

  NimBLECharacteristic* chr = svc->createCharacteristic(
    BLE_DATA_CHAR, NIMBLE_PROPERTY::NOTIFY);
  Serial.printf("Characteristic created: %s\n", BLE_DATA_CHAR);

  svc->start();
  Serial.println("Service started");

  NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
  adv->addServiceUUID(BLE_SERVICE);
  adv->setMinInterval(160);
  adv->setMaxInterval(240);
  adv->setName(BLE_NAME);
  
  Serial.println("Starting advertising...");
  NimBLEDevice::startAdvertising();
  adv_started = true;
  Serial.println("Advertising STARTED!");

  // Verify
  delay(100);
  Serial.printf("Is advertising: %s\n", NimBLEDevice::getAdvertising()->isAdvertising() ? "YES" : "NO");
}

void loop() {
  uint32_t now = millis();
  if (now >= next_status) {
    next_status = now + 5000;
    Serial.printf("Heap: %d, Adv: %s\n", 
      ESP.getFreeHeap(),
      NimBLEDevice::getAdvertising()->isAdvertising() ? "ON" : "OFF");
  }
  delay(1);
}
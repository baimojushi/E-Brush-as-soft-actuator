// S3 Native USB test: explicitly initialize USB CDC
#include <Arduino.h>
#include <USB.h>
#include <WiFi.h>
#include <NimBLEDevice.h>

constexpr const char* BLE_NAME = "EBrush-S3";
constexpr const char* BLE_SERVICE = "8b962400-dad0-45a8-a8d5-4f45fd83aefe";

uint32_t next_status = 0;
bool adv_started = false;

void setup() {
  // Explicitly initialize USB CDC
  USB.begin();
  Serial.begin(921600);
  
  // Wait for host to open CDC port
  uint32_t start = millis();
  while (!Serial && millis() - start < 5000) {
    delay(10);
  }
  delay(500);
  
  Serial.println("\n=== S3 Native USB Test ===");
  
  Serial.printf("Chip: %s Rev %d\n", ESP.getChipModel(), ESP.getChipRevision());
  Serial.printf("MAC: %s\n", WiFi.macAddress().c_str());
  Serial.printf("Free heap: %d\n", ESP.getFreeHeap());
  Serial.printf("USB CDC connected: %s\n", Serial ? "YES" : "NO");

  // Init NimBLE
  Serial.println("Initializing NimBLE...");
  NimBLEDevice::init(BLE_NAME);
  NimBLEDevice::setMTU(247);
  NimBLEDevice::setPower(9);  // Max power
  Serial.println("NimBLE init done");

  NimBLEServer* server = NimBLEDevice::createServer();
  Serial.println("Server created");

  NimBLEService* svc = server->createService(BLE_SERVICE);
  Serial.printf("Service created: %s\n", BLE_SERVICE);

  NimBLECharacteristic* chr = svc->createCharacteristic(
    "8b962401-dad0-45a8-a8d5-4f45fd83aefe", NIMBLE_PROPERTY::NOTIFY);
  Serial.println("Characteristic created");

  svc->start();
  Serial.println("Service started");

  NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
  adv->addServiceUUID(BLE_SERVICE);
  adv->setMinInterval(160);
  adv->setMaxInterval(240);
  adv->setName(BLE_NAME);
  
  NimBLEAdvertisementData scanData;
  scanData.setCompleteServices(NimBLEUUID(BLE_SERVICE));
  adv->setScanResponseData(scanData);
  
  Serial.println("Starting advertising...");
  NimBLEDevice::startAdvertising();
  adv_started = true;
  Serial.println("Advertising STARTED!");

  delay(100);
  bool isAdv = NimBLEDevice::getAdvertising()->isAdvertising();
  Serial.printf("Is advertising: %s\n", isAdv ? "YES" : "NO");
}

void loop() {
  uint32_t now = millis();
  if (now >= next_status) {
    next_status = now + 5000;
    bool isAdv = NimBLEDevice::getAdvertising()->isAdvertising();
    Serial.printf("Heap: %d, Adv: %s, USB: %s\n", 
      ESP.getFreeHeap(), 
      isAdv ? "ON" : "OFF",
      Serial ? "ON" : "OFF");
  }
  delay(1);
}
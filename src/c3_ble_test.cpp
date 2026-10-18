// Minimal BLE test for ESP32-C3: just advertise "EBrush-C3" with service UUID
// Enables Serial for debug output (115200)

#include <Arduino.h>
#include <WiFi.h>
#include <NimBLEDevice.h>

constexpr const char* BLE_NAME = "EBrush-C3";
constexpr const char* BLE_SERVICE = "8b962400-dad0-45a8-a8d5-4f45fd83aefe";
constexpr const char* BLE_DATA_CHAR = "8b962401-dad0-45a8-a8d5-4f45fd83aefe";

constexpr int LED_PIN = 8;
uint32_t next_led = 0;
bool led_state = false;
bool adv_started = false;

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== C3 Minimal BLE Test ===");
  
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  next_led = millis() + 500;

  Serial.printf("Chip: %s Rev %d\n", ESP.getChipModel(), ESP.getChipRevision());
  Serial.printf("MAC: %s\n", WiFi.macAddress().c_str());
  Serial.printf("Free heap: %d\n", ESP.getFreeHeap());

  // Init NimBLE
  Serial.println("Initializing NimBLE...");
  NimBLEDevice::init(BLE_NAME);
  NimBLEDevice::setMTU(247);
  NimBLEDevice::setPower(-6);
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
  if (now >= next_led) {
    led_state = !led_state;
    digitalWrite(LED_PIN, led_state);
    next_led = now + 500;
    
    // Periodic status
    static uint32_t last_status = 0;
    if (now - last_status >= 5000) {
      last_status = now;
      Serial.printf("Heap: %d, Adv: %s\n", 
        ESP.getFreeHeap(),
        NimBLEDevice::getAdvertising()->isAdvertising() ? "ON" : "OFF");
    }
  }
  delay(1);
}
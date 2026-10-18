#pragma once
#include "Arduino.h"
#include "WiFi.h"
struct WiFiUDP { bool beginPacket(IPAddress,uint16_t){return true;} size_t write(const uint8_t*,size_t n){return n;} int endPacket(){return 1;} void begin(uint16_t){} int parsePacket(){return 0;} int read(){return 0;} int read(uint8_t*,int){return 0;} int available(){return 0;} };

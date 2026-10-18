#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <cmath>
#define OUTPUT 1
#define INPUT 0
#define HIGH 1
#define LOW 0
#define FSPI 1
#define ARDUINO_USB_CDC_ON_BOOT 1
inline void pinMode(int,int){}
inline void digitalWrite(int,int){}
inline void delay(unsigned){}
inline void delayMicroseconds(unsigned){}
inline uint32_t millis(){return 0;}
inline bool setCpuFrequencyMhz(uint32_t){return true;}
struct FakeSerial { void begin(uint32_t){}; int availableForWrite(){return 1000;} size_t write(const uint8_t*,size_t){return 100;} int available(){return 0;} int read(){return 0;} };
extern FakeSerial Serial;
struct FakeESP { uint32_t getFreeHeap(){return 200000;} uint32_t getMinFreeHeap(){return 200000;} };
extern FakeESP ESP;

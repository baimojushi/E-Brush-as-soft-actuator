#pragma once
#include <stdint.h>
#include <stddef.h>
#include <cstdio>
#include <stdlib.h>
#include <string.h>
#include <cmath>
#define PROGMEM
#define IRAM_ATTR
#define INPUT_PULLUP 2
#define OUTPUT_OPEN_DRAIN 3
#define OUTPUT 1
#define INPUT 0
#define HIGH 1
#define LOW 0
#define FSPI 1
#define ARDUINO_USB_CDC_ON_BOOT 1
inline void pinMode(int,int){}
inline void digitalWrite(int,int){}
inline int digitalRead(int){return HIGH;}
inline void delay(unsigned){}
inline void delayMicroseconds(unsigned){}
inline uint32_t millis(){return 0;}
inline bool setCpuFrequencyMhz(uint32_t){return true;}
struct FakeSerial {
  void begin(uint32_t){}; int availableForWrite(){return 1000;}
  size_t write(const uint8_t*,size_t){return 100;} int available(){return 0;}
  int read(){return 0;}
  void println(const char*){} void print(const char*){} void flush(){}
  template <typename... A> void printf(const char*, A...){}
};
extern FakeSerial Serial;
struct FakeESP { uint32_t getFreeHeap(){return 200000;} uint32_t getMinFreeHeap(){return 200000;} };
extern FakeESP ESP;

inline uint8_t pgm_read_byte(const void* p) { return *static_cast<const uint8_t*>(p); }

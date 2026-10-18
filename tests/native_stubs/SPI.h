#pragma once
#include "Arduino.h"
#define MSBFIRST 1
#define SPI_MODE3 3
struct SPISettings { SPISettings(uint32_t,int,int){} };
struct SPIClass { explicit SPIClass(int){} void begin(int,int,int,int){} void beginTransaction(const SPISettings&){} uint8_t transfer(uint8_t){return 0;} void endTransaction(){} };

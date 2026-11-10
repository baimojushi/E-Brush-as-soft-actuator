#pragma once
#include "Arduino.h"
struct TwoWire { explicit TwoWire(int){} bool begin(int,int,uint32_t){return true;} void end(){} void setTimeOut(uint16_t){} void beginTransmission(uint8_t){} size_t write(uint8_t){return 1;} size_t write(const uint8_t*,size_t n){return n;} uint8_t endTransmission(bool=true){return 0;} uint8_t requestFrom(int,int,bool){return 0;} int available(){return 0;} int read(){return 0;}};

extern TwoWire Wire1;

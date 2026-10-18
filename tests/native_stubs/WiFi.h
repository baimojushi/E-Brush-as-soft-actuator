#pragma once
#include <stdint.h>
#define WIFI_STA 1
#define WIFI_AP 2
#define WL_CONNECTED 3
struct IPAddress { IPAddress(uint8_t,uint8_t,uint8_t,uint8_t){} };
struct WiFiClass { void mode(int){} void setAutoReconnect(bool){} void setSleep(bool){} void begin(const char*,const char*){} int status(){return 0;} void reconnect(){} bool softAP(const char*,const char*,int,bool,int){return true;} };
extern WiFiClass WiFi;

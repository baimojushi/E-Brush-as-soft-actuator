#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string>
namespace NIMBLE_PROPERTY { constexpr int NOTIFY = 1; }
class NimBLEUUID { public: explicit NimBLEUUID(const char*){} };
class NimBLEConnInfo { public: uint16_t getConnHandle(){return 1;} };
class NimBLEServer;
class NimBLEClient;
class NimBLECharacteristic;
class NimBLECharacteristicCallbacks { public: virtual ~NimBLECharacteristicCallbacks()=default; virtual void onSubscribe(NimBLECharacteristic*,NimBLEConnInfo&,uint16_t){} };
class NimBLEServerCallbacks { public: virtual ~NimBLEServerCallbacks()=default; virtual void onConnect(NimBLEServer*,NimBLEConnInfo&){} virtual void onDisconnect(NimBLEServer*,NimBLEConnInfo&,int){} };
class NimBLECharacteristic { public: void setCallbacks(NimBLECharacteristicCallbacks*){} bool notify(const uint8_t*,size_t,uint16_t=0){return true;} };
class NimBLEService { public: NimBLECharacteristic* createCharacteristic(const char*,int){return nullptr;} void start(){} };
class NimBLEServer { public: void setCallbacks(NimBLEServerCallbacks*,bool=false){}; NimBLEService* createService(const char*){return nullptr;} void updateConnParams(uint16_t,uint16_t,uint16_t,uint16_t,uint16_t){} uint16_t getPeerMTU(uint16_t){return 247;} bool setDataLen(uint16_t,uint16_t){return true;} };
class NimBLEAdvertising { public: void addServiceUUID(const char*){} void setMinInterval(uint16_t){} void setMaxInterval(uint16_t){} };
class NimBLEAdvertisedDevice { public: bool isAdvertisingService(const NimBLEUUID&)const{return true;} };
class NimBLEScanCallbacks { public: virtual ~NimBLEScanCallbacks()=default; virtual void onResult(const NimBLEAdvertisedDevice*){} };
class NimBLEScan { public: void setScanCallbacks(NimBLEScanCallbacks*,bool=false){} void setInterval(uint16_t){} void setWindow(uint16_t){} void setActiveScan(bool){} bool isScanning(){return true;} bool start(uint32_t){return true;} void stop(){} };
class NimBLERemoteCharacteristic;
typedef void (*notifyCB_t)(NimBLERemoteCharacteristic*,uint8_t*,size_t,bool);
class NimBLERemoteCharacteristic { public: bool canNotify(){return true;} bool subscribe(bool,notifyCB_t){return true;} };
class NimBLERemoteService { public: NimBLERemoteCharacteristic* getCharacteristic(const char*){return nullptr;} };
class NimBLEClientCallbacks { public: virtual ~NimBLEClientCallbacks()=default; virtual void onDisconnect(NimBLEClient*,int){} };
class NimBLEClient { public: void setClientCallbacks(NimBLEClientCallbacks*,bool=false){} void setConnectionParams(uint16_t,uint16_t,uint16_t,uint16_t){} void setConnectTimeout(uint32_t){} bool connect(const NimBLEAdvertisedDevice*){return true;} NimBLERemoteService* getService(const char*){return nullptr;} void disconnect(){} uint16_t getMTU(){return 247;} bool setDataLen(uint16_t){return true;} };
class NimBLEDevice { public: static void init(const char*){} static void setMTU(uint16_t){} static void setPower(int8_t){} static NimBLEServer* createServer(){return nullptr;} static NimBLEServer* getServer(){return nullptr;} static NimBLEAdvertising* getAdvertising(){return nullptr;} static bool startAdvertising(){return true;} static NimBLEScan* getScan(){return nullptr;} static NimBLEClient* createClient(){return nullptr;} static bool deleteClient(NimBLEClient*){return true;} };

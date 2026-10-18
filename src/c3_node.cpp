// ESP32-C3: sample sensors, stamp raw data, and transmit only. No OLED, GUI,
// quaternion fusion, adaptive filtering, BLE scanning, or simultaneous Wi-Fi/BLE.
#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <esp_system.h>
#include <esp_timer.h>
#include "brush_protocol.h"
#include "ebrush_link.h"
#include "node_config.h"
#include "lsm6dsr.h"
#include "tmag5273.h"

#if defined(BRUSH_LINK_BLE)
#include <NimBLEDevice.h>
#elif defined(BRUSH_LINK_WIFI)
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_wifi.h>
#else
#error "Select exactly one link"
#endif

using namespace EBrushLink;
using namespace NodeConfig;
namespace {
TwoWire hallBus(0);
SPIClass imuBus(FSPI);
Tmag5273 hall;
Lsm6dsr imu;

struct SensorState {
  bool hall_ready = false;
  bool imu_ready = false;
  uint32_t hall_last = 0;
  uint32_t imu_last = 0;
  uint32_t hall_retry_at = 0;
  uint32_t imu_retry_at = 0;
  uint8_t hall_fail_streak = 0;
  uint8_t imu_fail_streak = 0;
  uint32_t boot_id = 0;
  uint32_t frame_id = 0;
  uint32_t sample_count = 0;
  uint32_t transport_drops = 0;
  uint32_t hall_errors = 0;
  uint32_t imu_errors = 0;
  uint32_t hall_reinits = 0;
  uint32_t imu_reinits = 0;
  uint32_t packet_seq = 0;
  uint32_t next_sample = 0;
  uint32_t next_meta = 0;
  uint32_t last_sample_tick = 0;
  uint32_t recover_bits = 0;
  // ★ 低功耗待机: 上次检测到"大幅度运动"的时间; 超过 STANDBY_IDLE_US
  //    无运动则进入深度睡眠, 按 RST 键硬件复位即可恢复工作。
  uint32_t motion_standby_at = 0;
  int16_t prev_ax = 0, prev_ay = 0, prev_az = 0;  // 上一采样加速度
  Tmag5273::Sample h{};
  Lsm6dsr::Sample i{};

  // ★ I²C 扫描与 Hall 身份诊断 (启动一次, 结果随 HEALTH/META 上报)
  bool i2c_scan_done = false;
  uint64_t i2c_scan_bitmap_lo = 0;
  uint64_t i2c_scan_bitmap_hi = 0;
  uint32_t i2c_scan_duration_us = 0;
  uint8_t i2c_scan_count = 0;

  uint8_t hall_i2c_address = 0;
  uint8_t hall_variant = 0;
  uint8_t hall_init_error = 0;
  uint8_t hall_manufacturer_lsb = 0;
  uint8_t hall_manufacturer_msb = 0;
  uint8_t hall_device_id = 0;

  uint8_t hall_consecutive_read_errors = 0;
  uint8_t imu_consecutive_read_errors = 0;
  uint32_t hall_recoveries = 0;
  uint32_t imu_recoveries = 0;

  bool i2c_recovered_since_last_frame = false;
  bool spi_recovered_since_last_frame = false;
} state;

constexpr size_t RING_SIZE = 24;
Sample fifo[RING_SIZE]{};
size_t fifo_head = 0, fifo_size = 0;
uint32_t fifo_oldest_at = 0;
uint8_t wire[MAX_PACKET];

uint16_t age16(uint32_t now, uint32_t last) {
  if (last == 0) return 0xffff;
  const uint32_t age = now - last;
  return static_cast<uint16_t>(age > 0xfffe ? 0xfffe : age);
}

void fifoClear() {
  state.transport_drops += static_cast<uint32_t>(fifo_size);
  fifo_head = 0;
  fifo_size = 0;
}

void fifoPush(const Sample& v, uint32_t now) {
  if (fifo_size == RING_SIZE) {
    fifo_head = (fifo_head + 1) % RING_SIZE;
    --fifo_size;
    ++state.transport_drops;
  }
  if (!fifo_size) fifo_oldest_at = now;
  fifo[(fifo_head + fifo_size) % RING_SIZE] = v;
  ++fifo_size;
}

void fifoDrop(size_t count) {
  fifo_head = (fifo_head + count) % RING_SIZE;
  fifo_size -= count;
  if (fifo_size) fifo_oldest_at = fifo[fifo_head].sensor_time_us32;
}

// ★ I²C 总线恢复: 释放可能卡死的 SDA, 发 9 个时钟脉冲 + STOP 条件
void recoverI2CBus(bool mark_recovery_event) {
  hallBus.end();

  pinMode(HALL_SDA, INPUT_PULLUP);
  pinMode(HALL_SCL, OUTPUT_OPEN_DRAIN);
  digitalWrite(HALL_SCL, HIGH);
  delayMicroseconds(5);

  for (int i = 0; i < 9 && digitalRead(HALL_SDA) == LOW; ++i) {
    digitalWrite(HALL_SCL, LOW);
    delayMicroseconds(5);
    digitalWrite(HALL_SCL, HIGH);
    delayMicroseconds(5);
  }

  // STOP: SDA low -> SCL high -> SDA high
  pinMode(HALL_SDA, OUTPUT_OPEN_DRAIN);
  digitalWrite(HALL_SDA, LOW);
  delayMicroseconds(5);
  digitalWrite(HALL_SCL, HIGH);
  delayMicroseconds(5);
  digitalWrite(HALL_SDA, HIGH);
  delayMicroseconds(5);

  hallBus.begin(HALL_SDA, HALL_SCL, I2C_HZ);
  hallBus.setTimeOut(6);
  if (mark_recovery_event) state.i2c_recovered_since_last_frame = true;
}

// ★ 启动时执行一次 I²C 扫描, 结果持久保存在 state, 供 HEALTH/META 上报
void runI2CScanOnce() {
  if (state.i2c_scan_done) return;

  state.i2c_scan_bitmap_lo = 0;
  state.i2c_scan_bitmap_hi = 0;
  state.i2c_scan_count = 0;

  const uint64_t t0 = static_cast<uint64_t>(esp_timer_get_time());

  // 跳过 general-call/CBUS 等最低保留地址; 保留 0x78, 因为 TMAG5273 C1/C2 工厂地址就是 0x78
  for (uint16_t addr = 0x08; addr <= 0x7E; ++addr) {
    hallBus.beginTransmission(static_cast<uint8_t>(addr));
    const uint8_t err = hallBus.endTransmission(true);
    if (err == 0) {
      if (addr < 0x40) {
        state.i2c_scan_bitmap_lo |= (1ULL << addr);
      } else {
        state.i2c_scan_bitmap_hi |= (1ULL << (addr - 0x40));
      }
      if (state.i2c_scan_count < 255) ++state.i2c_scan_count;
    }
    delayMicroseconds(30);
  }

  state.i2c_scan_duration_us =
      static_cast<uint32_t>(static_cast<uint64_t>(esp_timer_get_time()) - t0);
  state.i2c_scan_done = true;
}

// ★ Hall INT 中断标志 (需在 sampleSensors 之前声明)
volatile bool hallIrqFlag = false;

void IRAM_ATTR onHallInterrupt() {
  hallIrqFlag = true;
}

// ★ 微秒级年龄计算 (用于新鲜度判断, 需在 sampleSensors 之前声明)
uint64_t ageUs(uint64_t now_us, uint64_t then_us) {
  if (then_us == 0) return UINT64_MAX;
  return now_us >= then_us ? now_us - then_us : 0;
}

// ★ 检查 I²C 扫描中是否看见某地址
bool i2cAddressSeen(uint8_t address) {
  if (!state.i2c_scan_done || address > 0x7F) return false;
  if (address < 0x40) {
    return (state.i2c_scan_bitmap_lo & (1ULL << address)) != 0;
  }
  return (state.i2c_scan_bitmap_hi & (1ULL << (address - 0x40))) != 0;
}

// ★ 捕获 Hall 身份诊断信息, 供 HEALTH/META 上报
void captureHallIdentityDiagnostics() {
  state.hall_variant = hall.variant();
  state.hall_manufacturer_lsb = hall.manufacturerLsb();
  state.hall_manufacturer_msb = hall.manufacturerMsb();
  state.hall_device_id = hall.deviceIdRaw();
  state.hall_init_error = static_cast<uint8_t>(hall.lastInitError());
}

// ★ 尝试指定地址初始化 Hall
bool tryHallAddress(uint8_t address) {
  const bool ok = hall.begin(
      hallBus, address, HALL_AVG_CODE, false); // low_noise=false 对应旧版本
  captureHallIdentityDiagnostics();

  if (ok) {
    state.hall_i2c_address = address;
    state.hall_variant = hall.variant();
    state.hall_init_error = static_cast<uint8_t>(Tmag5273::InitError::NONE);
    state.hall_consecutive_read_errors = 0;
    state.hall_last = 0;
    state.recover_bits |= BrushProtocol::STATUS_I2C_RECOVERED;
    return true;
  }
  return false;
}

bool initHall(bool recover_bus = true) {
  if (recover_bus) recoverI2CBus(true);

  state.hall_i2c_address = 0;
  state.hall_variant = 0;
  state.hall_init_error = 0;
  state.hall_manufacturer_lsb = 0;
  state.hall_manufacturer_msb = 0;
  state.hall_device_id = 0;

  bool any_candidate_ack = false;
  for (size_t i = 0; i < HALL_ADDR_COUNT; ++i) {
    any_candidate_ack |= (state.i2c_scan_bitmap_lo & (1ULL << HALL_ADDRS[i])) ||
                         (state.i2c_scan_bitmap_hi & (1ULL << (HALL_ADDRS[i] - 0x40)));
  }

  // 第一遍优先尝试启动扫描中实际 ACK 的 TMAG 工厂地址;
  // 第二遍再尝试其余候选, 覆盖"扫描瞬间设备尚未就绪"的情况。
  for (uint8_t pass = 0; pass < 2; ++pass) {
    for (size_t i = 0; i < HALL_ADDR_COUNT; ++i) {
      const uint8_t addr = HALL_ADDRS[i];
      const bool seen = (state.i2c_scan_bitmap_lo & (1ULL << addr)) ||
                        (state.i2c_scan_bitmap_hi & (1ULL << (addr - 0x40)));
      if ((pass == 0 && !seen) || (pass == 1 && seen)) continue;
      if (tryHallAddress(addr)) return true;
    }
  }

  if (state.i2c_scan_done && !any_candidate_ack) {
    state.hall_init_error =
        static_cast<uint8_t>(Tmag5273::InitError::NO_CANDIDATE_ACK);
  }
  return false;
}

bool initImu() {
  digitalWrite(IMU_CS, HIGH);
  delay(2);
  const bool ok = imu.begin(imuBus, IMU_CS, SPI_HZ);
  if (ok) {
    state.imu_fail_streak = 0;
    state.imu_last = 0;
    state.recover_bits |= BrushProtocol::STATUS_SPI_RECOVERED;
    state.spi_recovered_since_last_frame = true;
  }
  return ok;
}

void maybeRetrySensors(uint32_t now) {
  if (!state.hall_ready && static_cast<int32_t>(now - state.hall_retry_at) >= 0) {
    ++state.hall_reinits;
    const bool recovered = initHall(true);
    state.hall_ready = recovered;
    state.hall_retry_at = now + SENSOR_RETRY_US;
    if (recovered) {
      ++state.hall_recoveries;
    }
  }
  if (!state.imu_ready && static_cast<int32_t>(now - state.imu_retry_at) >= 0) {
    ++state.imu_reinits;
    const bool recovered = initImu();
    state.imu_ready = recovered;
    state.imu_retry_at = now + SENSOR_RETRY_US;
    if (recovered) {
      ++state.imu_recoveries;
    }
  }
}

void sampleSensors(uint32_t scheduled, bool overrun) {
  ++state.sample_count;
  Sample out{};
  out.frame_id = state.frame_id++;
  out.sensor_time_us32 = scheduled;
  out.drop_flags = overrun ? static_cast<uint32_t>(BrushProtocol::DROP_SCHED_OVERRUN) : 0u;
  out.status = BrushProtocol::STATUS_STREAMING | state.recover_bits;
  state.recover_bits = 0;
  if (state.hall_ready) {
    out.status |= BrushProtocol::STATUS_HALL_PRESENT;
    Tmag5273::Sample value{};
    bool fresh = false;
    const uint64_t hall_check_before_us = static_cast<uint64_t>(esp_timer_get_time());
    const bool should_read =
        hallIrqFlag || ageUs(hall_check_before_us, state.hall_last) >= PERIOD_US;

    if (should_read) {
      if (hall.readSample(value, fresh)) {
        state.h = value;
        state.hall_last = static_cast<uint32_t>(esp_timer_get_time());
        state.hall_consecutive_read_errors = 0;
        hallIrqFlag = false;
      } else {
        ++state.hall_errors;
        out.drop_flags |= BrushProtocol::DROP_HALL_READ_FAIL;
        if (++state.hall_consecutive_read_errors >= 3) {
          state.hall_ready = false;
          state.hall_consecutive_read_errors = 0;
          state.hall_retry_at = static_cast<uint32_t>(esp_timer_get_time()) + SENSOR_RETRY_US;
        }
      }
    }

    const uint64_t hall_check_after_us = static_cast<uint64_t>(esp_timer_get_time());
    if (ageUs(hall_check_after_us, state.hall_last) <= MAX_STALE_US) {
      out.status |= BrushProtocol::STATUS_HALL_VALID;
      if (state.h.conv_status & 0x02) out.status |= BrushProtocol::STATUS_HALL_DIAG;
      if (abs(state.h.x_raw) > 31000 || abs(state.h.y_raw) > 31000 ||
          abs(state.h.z_raw) > 31000) out.status |= BrushProtocol::STATUS_HALL_NEAR_SAT;
    } else {
      out.drop_flags |= BrushProtocol::DROP_HALL_STALE;
    }
  } else {
    out.drop_flags |= BrushProtocol::DROP_HALL_STALE;
  }
  if (state.imu_ready) {
    out.status |= BrushProtocol::STATUS_IMU_PRESENT;
    Lsm6dsr::Sample value{};
    bool fresh = false;
    if (imu.readSample(value, fresh)) {
      state.i = value;
      state.imu_last = static_cast<uint32_t>(esp_timer_get_time());
      state.imu_consecutive_read_errors = 0;
      if (fresh) out.status |= BrushProtocol::STATUS_IMU_FRESH;
    } else {
      ++state.imu_errors;
      out.drop_flags |= BrushProtocol::DROP_IMU_READ_FAIL;
      if (++state.imu_consecutive_read_errors >= 3) {
        state.imu_ready = false;
        state.imu_consecutive_read_errors = 0;
        state.imu_retry_at = static_cast<uint32_t>(esp_timer_get_time()) + SENSOR_RETRY_US;
      }
    }

    const uint64_t imu_check_us = static_cast<uint64_t>(esp_timer_get_time());
    if (ageUs(imu_check_us, state.imu_last) > MAX_STALE_US) {
      out.status &= ~(BrushProtocol::STATUS_IMU_VALID | BrushProtocol::STATUS_IMU_FRESH);
      out.drop_flags |= BrushProtocol::DROP_IMU_STALE;
    }
  } else {
    out.drop_flags |= BrushProtocol::DROP_IMU_STALE;
  }

  if (state.imu_ready && (out.status & BrushProtocol::STATUS_IMU_VALID)) {
    out.status |= BrushProtocol::STATUS_POSE_VALID | BrushProtocol::STATUS_POSE_YAW_RELATIVE;
  }
  if (state.i2c_recovered_since_last_frame) out.status |= BrushProtocol::STATUS_I2C_RECOVERED;
  if (state.spi_recovered_since_last_frame) out.status |= BrushProtocol::STATUS_SPI_RECOVERED;

  out.hx = state.h.x_raw;
  out.hy = state.h.y_raw;
  out.hz = state.h.z_raw;
  out.ht = state.h.temp_raw;
  out.hall_conv = state.h.conv_status;
  out.hall_device = state.h.device_status;
  out.it = state.i.temp_raw;
  out.gx = state.i.gx_raw;
  out.gy = state.i.gy_raw;
  out.gz = state.i.gz_raw;
  out.ax = state.i.ax_raw;
  out.ay = state.i.ay_raw;
  out.az = state.i.az_raw;
  out.imu_status = state.i.status;

  // ★ 低功耗待机: 用 IMU 加速度相邻采样差判断"大幅度运动"。
  //    运动笔写时加速度变化远大于噪声(> MOTION_DELTA_LSB),
  //    静置时差值接近 0。一旦连续 5 分钟无运动 → 进入深度睡眠。
  if (state.imu_ready) {
    const int16_t dax = abs(state.i.ax_raw - state.prev_ax);
    const int16_t day = abs(state.i.ay_raw - state.prev_ay);
    const int16_t daz = abs(state.i.az_raw - state.prev_az);
    state.prev_ax = state.i.ax_raw;
    state.prev_ay = state.i.ay_raw;
    state.prev_az = state.i.az_raw;
    if (dax > MOTION_DELTA_LSB || day > MOTION_DELTA_LSB || daz > MOTION_DELTA_LSB) {
      state.motion_standby_at = static_cast<uint32_t>(esp_timer_get_time());
    }
  }

  // Real-time Hall sensor debug output (every sample)
  if (state.hall_ready) {
    Serial.printf("HALL: x=%d y=%d z=%d t=%d conv=0x%02X dev=0x%02X\n",
      state.h.x_raw, state.h.y_raw, state.h.z_raw,
      state.h.temp_raw, state.h.conv_status, state.h.device_status);
  } else {
    Serial.println("HALL: NOT READY");
  }

  fifoPush(out, static_cast<uint32_t>(esp_timer_get_time()));
}

#if defined(BRUSH_LINK_BLE)
volatile bool subscribed = false;
volatile bool ble_connected = false;
volatile uint16_t ble_handle = 0xffff;
NimBLECharacteristic* dataChar = nullptr;

class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* server, NimBLEConnInfo& conn) override {
    ble_connected = true;
    ble_handle = conn.getConnHandle();
    server->updateConnParams(conn.getConnHandle(), 14, 15, 0, 160);
    server->setDataLen(conn.getConnHandle(), 251);
  }
  void onDisconnect(NimBLEServer*, NimBLEConnInfo&, int) override {
    ble_connected = false;
    subscribed = false;
    ble_handle = 0xffff;
    NimBLEDevice::startAdvertising();
  }
};
class CharCallbacks : public NimBLECharacteristicCallbacks {
  void onSubscribe(NimBLECharacteristic*, NimBLEConnInfo&, uint16_t code) override {
    subscribed = (code & 1) != 0;
  }
};
ServerCallbacks serverCallbacks;
CharCallbacks charCallbacks;

void initTransport() {
  Serial.println("  NimBLEDevice::init()...");
  NimBLEDevice::init(BLE_NAME);
  Serial.println("  NimBLEDevice::setMTU(247)...");
  NimBLEDevice::setMTU(247);
  Serial.println("  NimBLEDevice::setPower(-6)...");
  NimBLEDevice::setPower(-6); // Lower TX power until a real RSSI/link test says otherwise.
  Serial.println("  NimBLEDevice::createServer()...");
  NimBLEServer* server = NimBLEDevice::createServer();
  Serial.println("  server->setCallbacks()...");
  server->setCallbacks(&serverCallbacks, false);
  Serial.println("  server->createService()...");
  NimBLEService* svc = server->createService(BLE_SERVICE);
  Serial.println("  svc->createCharacteristic()...");
  dataChar = svc->createCharacteristic(BLE_DATA_CHAR, NIMBLE_PROPERTY::NOTIFY);
  Serial.println("  dataChar->setCallbacks()...");
  dataChar->setCallbacks(&charCallbacks);
  Serial.println("  svc->start()...");
  svc->start();
  Serial.println("  NimBLEDevice::getAdvertising()...");
  NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
  Serial.println("  adv->addServiceUUID()...");
  adv->addServiceUUID(BLE_SERVICE);
  Serial.println("  adv->setMinInterval(160), setMaxInterval(240)...");
  adv->setMinInterval(160);
  adv->setMaxInterval(240);
  Serial.println("  NimBLEDevice::startAdvertising()...");
  NimBLEDevice::startAdvertising();
  Serial.println("  Advertising STARTED!");
  
  // Verify
  delay(100);
  bool isAdv = NimBLEDevice::getAdvertising()->isAdvertising();
  Serial.printf("  Is advertising: %s\n", isAdv ? "YES" : "NO");
  Serial.printf("  Service UUID: %s\n", BLE_SERVICE);
  Serial.printf("  Device name: %s\n", BLE_NAME);
}

bool transportReady() {
  return subscribed && ble_connected && ble_handle != 0xffff;
}

size_t transportMaxPayload() {
  if (!transportReady()) return 0;
  NimBLEServer* server = NimBLEDevice::getServer();
  const uint16_t mtu = server ? server->getPeerMTU(ble_handle) : 0;
  return mtu >= 3 ? static_cast<size_t>(mtu - 3) : 0;
}

bool transportSend(const uint8_t* bytes, size_t len) {
  return transportReady() && len <= transportMaxPayload() &&
         dataChar->notify(bytes, len);
}
#elif defined(BRUSH_LINK_WIFI)
WiFiUDP udp;
uint32_t wifi_retry_at = 0;

void initTransport() {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(true);
  esp_wifi_set_ps(WIFI_PS_MIN_MODEM);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void manageTransport(uint32_t now) {
  if (WiFi.status() == WL_CONNECTED) return;
  if (static_cast<int32_t>(now - wifi_retry_at) >= 0) {
    wifi_retry_at = now + 3000000u;
    WiFi.reconnect();
  }
}

bool transportReady() { return WiFi.status() == WL_CONNECTED; }
size_t transportMaxPayload() { return MAX_PACKET; }
bool transportSend(const uint8_t* bytes, size_t len) {
  if (!transportReady() || !udp.beginPacket(IPAddress(192,168,4,1), WIFI_PORT)) return false;
  if (udp.write(bytes, len) != len) { udp.endPacket(); return false; }
  return udp.endPacket() == 1;
}
#endif

void maybeSend(uint32_t now) {
#if defined(BRUSH_LINK_WIFI)
  manageTransport(now);
#endif
  if (!transportReady()) {
    if (fifo_size) fifoClear(); // Never replay old movements after reconnection.
    return;
  }
  const size_t cap = transportMaxPayload();
  const size_t fit = cap > sizeof(Header) + 2 ?
      (cap - sizeof(Header) - 2) / sizeof(Sample) : 0;
  if (!fit) {
    if (fifo_size) fifoClear();
    return;
  }

  // Bound sender work to ONE notification/UDP packet per loop iteration.
  const size_t n = (fit > MAX_SAMPLES) ? MAX_SAMPLES : fit;
  if (fifo_size >= n || (fifo_size && (now - fifo_oldest_at >= 20000u))) {
    Sample batch[MAX_SAMPLES];
    const size_t count = fifo_size < n ? fifo_size : n;
    for (size_t i = 0; i < count; ++i) batch[i] = fifo[(fifo_head + i) % RING_SIZE];
    const size_t size = make_packet(TYPE_SAMPLES, state.packet_seq++, batch,
      static_cast<uint16_t>(count * sizeof(Sample)), static_cast<uint8_t>(count),
      wire, sizeof(wire));
    if (size && transportSend(wire, size)) {
      fifoDrop(count);
    } else if (fifo_size && now - fifo_oldest_at > 100000u) {
      fifoDrop(1);
      ++state.transport_drops;
    }
  }
  if (static_cast<int32_t>(now - state.next_meta) >= 0) {
    Meta meta{};
    meta.boot_id = state.boot_id;
    meta.sample_count = state.sample_count;
    meta.transport_drops = state.transport_drops;
    meta.hall_errors = state.hall_errors;
    meta.imu_errors = state.imu_errors;
    meta.hall_reinits = state.hall_reinits;
    meta.imu_reinits = state.imu_reinits;
    meta.last_sensor_time_us32 = state.last_sample_tick;
    meta.hall_variant = state.hall_variant;
    meta.imu_whoami = imu.whoAmI();
    // ★ V1.1 扩展诊断字段 (兼容旧版 Meta 结构, 新字段追加在后, S3 端可选解析)
    // 这里复用 reserved 字段传递部分诊断信息
#ifdef BRUSH_LINK_BLE
    meta.transport_id = 1;
#else
    meta.transport_id = 2;
#endif
    const size_t size = make_packet(TYPE_META, state.packet_seq++, &meta,
      sizeof(meta), 1, wire, sizeof(wire));
    const bool sent = size && transportSend(wire, size);
    // Keep failed metadata retries bounded; a congested link must not
    // force the C3 to build and send metadata on every 1ms scheduler tick.
    state.next_meta = now + (sent ? 1000000u : 200000u);
  }
}
} // namespace

// LED 心跳：GPIO8 (ESP32-C3-DevKitM-1 板载蓝 LED)
constexpr int LED_PIN = 8;
uint32_t next_led_toggle = 0;
bool led_state = false;

void setup() {
  // Add Serial for debug (USB CDC when ARDUINO_USB_CDC_ON_BOOT=1)
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n=== C3 BLE Peripheral Starting ===");

  // Keep the C3 at the lowest CPU rate used by this 200Hz firmware.
  setCpuFrequencyMhz(80);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  Serial.println("LED pin init done");

  pinMode(IMU_CS, OUTPUT);
  digitalWrite(IMU_CS, HIGH);
  Serial.println("IMU CS init done");

  hallBus.begin(HALL_SDA, HALL_SCL, I2C_HZ);
  hallBus.setTimeOut(6);
  Serial.println("Hall I2C bus init done");

  imuBus.begin(IMU_SCK, IMU_MISO, IMU_MOSI, IMU_CS);
  Serial.println("IMU SPI bus init done");

  state.boot_id = esp_random();
  Serial.printf("Boot ID: %u\n", state.boot_id);

  // ★ 启动时先释放潜在卡死的 SDA, 再在生产 I²C 速率下扫描一次
  //    扫描结果持久保存在 state, 并通过每个 HEALTH/META 包重复上报
  recoverI2CBus(false);
  runI2CScanOnce();

  Serial.println("Initializing Hall sensor...");
  state.hall_ready = initHall(false); // 已在 recoverI2CBus 中恢复总线
  Serial.printf("Hall ready: %s\n", state.hall_ready ? "YES" : "NO");
  if (state.hall_ready) {
    Serial.printf("  Hall I2C addr: 0x%02X, variant: %u\n", state.hall_i2c_address, state.hall_variant);
  } else {
    Serial.printf("  Hall init error: %u\n", state.hall_init_error);
  }

  Serial.println("Initializing IMU...");
  state.imu_ready = initImu();
  Serial.printf("IMU ready: %s\n", state.imu_ready ? "YES" : "NO");
  if (state.imu_ready) {
    Serial.printf("  IMU WHO_AM_I: 0x%02X\n", imu.whoAmI());
  }

  const uint32_t now = static_cast<uint32_t>(esp_timer_get_time());
  state.hall_retry_at = now + SENSOR_RETRY_US;
  state.imu_retry_at = now + SENSOR_RETRY_US;
  state.next_sample = now + PERIOD_US;
  state.next_meta = now + 1000000u;
  next_led_toggle = now + 500000; // 0.5s 第一次翻转

  Serial.println("Initializing BLE transport...");
  initTransport();
  Serial.println("Setup complete!");
}

void loop() {
  uint32_t now = static_cast<uint32_t>(esp_timer_get_time());
  // ★ 低功耗待机: 连续 5 分钟无大幅度运动 → 深度睡眠。
  //    按 RST 键硬件复位即可恢复工作 (传感器/BLE 全停, 电流 ~μA)。
  if (state.motion_standby_at &&
      static_cast<uint32_t>(now - state.motion_standby_at) >= STANDBY_IDLE_US) {
    Serial.println("\n=== STANDBY: 5min idle, entering deep sleep ===");
    Serial.flush();
    // 关闭传感器总线与 BLE, 让电流降至 μA 级。
    hallBus.end();
    imuBus.end();
    NimBLEDevice::deinit(true);
    esp_deep_sleep_start();
    return; // 深度睡眠后不会返回此处
  }

  if (static_cast<int32_t>(now - next_led_toggle) >= 0) {
    led_state = !led_state;
    digitalWrite(LED_PIN, led_state);
    next_led_toggle = now + 500000; // 1Hz 闪烁
  }
  maybeRetrySensors(now);
  if (static_cast<int32_t>(now - state.next_sample) >= 0) {
    const bool overrun = static_cast<uint32_t>(now - state.next_sample) > 3u * PERIOD_US;
    if (overrun) state.next_sample = now;
    const uint32_t tick = state.next_sample;
    state.next_sample += PERIOD_US;
    sampleSensors(tick, overrun);
  }
  maybeSend(static_cast<uint32_t>(esp_timer_get_time()));
  // Keep idle slots available to the BLE stack and Wi-Fi power-save logic.
  const uint32_t left = state.next_sample - static_cast<uint32_t>(esp_timer_get_time());
  if (left > 1800u && left < 100000u) delay(1);
  else delay(0);
}

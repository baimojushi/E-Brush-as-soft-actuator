// ESP32-S3: sole radio central/AP, timestamp re-mapper, fusion, adaptive pipeline,
// USB Studio BRUSH-V1 stream. The ESP32-C3 never runs these heavy features.
#include <Arduino.h>
#include <esp_timer.h>
#include <esp_system.h>
#include <math.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include "brush_protocol.h"
#include "ebrush_link.h"
#include "engine_config.h"
#include "font.h"
#include "mahony6.h"
#include "node_config.h"
#include "s3_adaptive.h"
#include <Wire.h>
#include <esp32-hal-rgb-led.h>

// ★ oled_display.h 必须在 namespace { ... } 内部 (g_ble 定义之后) include，
//    否则它的 static 函数会落到全局作用域，与 namespace 内的同名函数冲突。

#if defined(BRUSH_LINK_BLE)
#include <NimBLEDevice.h>
#elif defined(BRUSH_LINK_WIFI)
#include <WiFi.h>
#include <WiFiUdp.h>
#else
#error "Select BLE or Wi-Fi target"
#endif

using namespace BrushProtocol;
using namespace EBrushLink;
namespace {

#pragma pack(push, 1)
struct OptimizedPayload {
  uint32_t source_frame_id;
  uint32_t flags;
  float hall_mT[3];
  float motion_accel_g[3];
  float motion_energy;
  float jerk_g_per_s;
  float confidence;
  uint32_t missed_frames;
};
struct LinkHealthPayload {
  uint32_t packets_ok;
  uint32_t packets_bad;
  uint32_t transport_queue_drops;
  uint32_t source_frame_gaps;
  uint32_t duplicate_frames;
  uint32_t c3_transport_drops;
  uint32_t usb_drops;
  uint16_t ble_mtu;
  uint8_t link_type;
  uint8_t link_alive;
};
#pragma pack(pop)
static_assert(sizeof(OptimizedPayload) == 48, "BR OPT protocol packing");
static_assert(sizeof(LinkHealthPayload) == 32, "BR link health packing");

struct RadioMessage {
  uint64_t rx_time_s3_us;
  uint16_t size;
  uint8_t data[MAX_PACKET];
};
QueueHandle_t rxQueue = nullptr;
uint32_t queueDrops = 0;

struct Engine {
  uint32_t usb_seq = 0;
  uint32_t packets_ok = 0;
  uint32_t packets_bad = 0;
  uint32_t usb_drops = 0;
  uint32_t usb_data_sent = 0;
  uint32_t rx_good_frames = 0;
  uint32_t missing_frames = 0;
  uint32_t duplicates = 0;
  uint32_t transport_gaps = 0;
  uint32_t last_packet_seq = 0;
  uint32_t last_source_frame_id = 0;
  uint32_t last_boot_id = 0;
  uint32_t last_meta_us32 = 0;
  uint64_t last_link_us = 0;
  uint64_t last_imu_s3_us = 0;
  uint64_t last_output_us = 0;
  uint16_t negotiated_mtu = 0;
  bool have_packet_seq = false;
  bool have_source_frame = false;
  bool have_meta = false;
  bool stream_to_usb = true;
  uint8_t hall_variant = 0;
  Meta meta{};
  TickUnwrapper unwrap;
  Mahony6 pose;
  Adaptive::Filter adaptive;
  float qw = 1, qx = 0, qy = 0, qz = 0;
  bool pose_ready = false;
  bool have_clock_offset = false;
  double time_offset = 0;
  uint64_t last_mapped_time = 0;
  uint32_t next_health_ms = 0;
  uint32_t next_hello_ms = 0;
} e;

// ★ OLED 显示层 (SSD1306 I2C1 + 黄宾虹开机动画 + 四页面 + 按钮切换)。
//    S3 本机无本地传感器, 数据全部来自 C3 经 BLE 上传的 Sample 帧。
//    receiveFrame() 每帧把解析出的原始码写入 g_ble, oled_display.h 只做快照。
//    定义在 namespace { ... } 内部, 故必须在此处 include (g_ble 定义之后)。
struct BleSnapshot {
  int16_t hall_x_raw = 0, hall_y_raw = 0, hall_z_raw = 0, hall_temp_raw = 0;
  uint8_t hall_conv_status = 0, hall_device_status = 0;
  uint8_t device_status = 0, drop_flags = 0;
  int16_t accel_x_raw = 0, accel_y_raw = 0, accel_z_raw = 0;
  int16_t gyro_x_raw = 0, gyro_y_raw = 0, gyro_z_raw = 0;
  int16_t imu_temp_raw = 0, imu_status = 0;
  float q_w = 1.f, q_x = 0.f, q_y = 0.f, q_z = 0.f;
  bool hall_ok = false, imu_ok = false, pose_ok = false;
} g_ble;

#include "oled_display.h"

uint8_t hostRxBuf[MAX_WIRE_PACKET]{};
size_t hostRxLen = 0;

bool usbWrite(PacketType type, const void* data, uint16_t size) {
  uint8_t buf[MAX_WIRE_PACKET];
  const size_t n = build_wire_packet(type, e.usb_seq++, data, size, buf, sizeof(buf));
  if (!n || Serial.availableForWrite() < static_cast<int>(n)) {
    ++e.usb_drops;
    return false;
  }
  if (Serial.write(buf, n) != n) {
    ++e.usb_drops;
    return false;
  }
  return true;
}

void sendHello() {
  if (!e.have_meta) return;
  HelloPayload hello{};
  hello.boot_id = e.last_boot_id;
  hello.capabilities = CAP_HALL_A2 | CAP_IMU_6AXIS | CAP_QUAT_6AXIS |
                      CAP_CLOCK_SYNC | CAP_HEALTH;
  hello.serial_baud = EngineConfig::USB_SERIAL_BAUD;
  hello.stream_hz = NodeConfig::SAMPLE_HZ;
  hello.fw_major = 2;
  hello.fw_minor = 0;
  hello.fw_patch = 0;
  hello.hall_variant = e.meta.hall_variant;
  hello.imu_who_am_i = e.meta.imu_whoami;
  (void)usbWrite(PacketType::HELLO, &hello, sizeof(hello));
}

void sendHealth() {
  HealthPayload health{};
  health.uptime_us = static_cast<uint64_t>(esp_timer_get_time());
  health.frames_intended = e.have_meta ? e.meta.sample_count : e.rx_good_frames;
  health.frames_sent = e.usb_data_sent;
  health.tx_drops = e.meta.transport_drops + e.usb_drops;
  health.rx_crc_errors = e.packets_bad;
  health.rx_frame_errors = e.duplicates + e.transport_gaps + queueDrops;
  health.hall_read_errors = e.meta.hall_errors;
  health.imu_read_errors = e.meta.imu_errors;
  health.hall_reinits = e.meta.hall_reinits;
  health.imu_reinits = e.meta.imu_reinits;
  health.free_heap = ESP.getFreeHeap();
  health.min_free_heap = ESP.getMinFreeHeap();
  health.hall_variant = e.meta.hall_variant;
  (void)usbWrite(PacketType::HEALTH, &health, sizeof(health));

  LinkHealthPayload link{};
  link.packets_ok = e.packets_ok;
  link.packets_bad = e.packets_bad;
  link.transport_queue_drops = queueDrops;
  link.source_frame_gaps = e.missing_frames;
  link.duplicate_frames = e.duplicates;
  link.c3_transport_drops = e.meta.transport_drops;
  link.usb_drops = e.usb_drops;
  link.ble_mtu = e.negotiated_mtu;
#ifdef BRUSH_LINK_BLE
  link.link_type = 1;
#else
  link.link_type = 2;
#endif
  link.link_alive = (e.last_link_us && static_cast<uint64_t>(esp_timer_get_time()) - e.last_link_us < 2500000ULL) ? 1 : 0;
  (void)usbWrite(static_cast<PacketType>(0x21), &link, sizeof(link));
}

void sourceReboot() {
  e.have_source_frame = false;
  e.unwrap.reset();
  e.have_clock_offset = false;
  e.last_mapped_time = 0;
  e.last_imu_s3_us = 0;
  e.last_output_us = 0;
  e.pose.reset();
  e.adaptive.reset();
  e.qw = 1.f; e.qx = e.qy = e.qz = 0.f;
  e.pose_ready = false;
  e.have_packet_seq = false;
}

void receiveMeta(const Meta& m) {
  const bool reset = e.have_meta && (e.last_boot_id != m.boot_id);
  e.meta = m;
  e.last_boot_id = m.boot_id;
  e.hall_variant = m.hall_variant;
  e.have_meta = true;
  if (reset) sourceReboot();
  if (!e.last_meta_us32 || reset) sendHello();
  e.last_meta_us32 = static_cast<uint32_t>(esp_timer_get_time());
}

// Translate the C3 microsecond epoch to S3's epoch. Min-latency envelope
// reduces queuing-jitter bias without pretending BLE RX time == sensor time.
void observeClock(uint64_t source_newest, uint64_t s3_rx_us) {
  const double proposed = static_cast<double>(s3_rx_us) - static_cast<double>(source_newest);
  if (!e.have_clock_offset) {
    e.time_offset = proposed;
    e.have_clock_offset = true;
    return;
  }
  const double error = proposed - e.time_offset;
  // Reject stale radio packets for updating the clock estimate.
  if (error > 1000000.0 || error < -1000000.0) return;
  const double step = error < -250.0 ? -250.0 : (error > 20.0 ? 20.0 : error);
  e.time_offset += step;
}

uint64_t mapTime(uint64_t t_c3) {
  const double value = static_cast<double>(t_c3) + e.time_offset;
  uint64_t t = value < 0.0 ? 0 : static_cast<uint64_t>(value);
  if (e.last_mapped_time && t <= e.last_mapped_time) t = e.last_mapped_time + 1;
  e.last_mapped_time = t;
  return t;
}

void updateImu(const Sample& s, uint64_t timestamp_s3) {
  if ((s.status & STATUS_IMU_VALID) == 0 || (s.status & STATUS_IMU_FRESH) == 0) return;
  if (e.last_imu_s3_us == 0) {
    e.last_imu_s3_us = timestamp_s3;
    return;
  }
  const float dt = static_cast<float>(timestamp_s3 - e.last_imu_s3_us) * 1e-6f;
  e.last_imu_s3_us = timestamp_s3;
  if (dt <= 0.0002f || dt >= 0.05f) return;
  constexpr float kGyro = 35.f * .001f * .01745329252f;
  constexpr float kAccel = .122f * .001f;
  if (e.pose.update(s.gx*kGyro, s.gy*kGyro, s.gz*kGyro,
                    s.ax*kAccel, s.ay*kAccel, s.az*kAccel, dt)) {
    e.pose.quaternion(e.qw, e.qx, e.qy, e.qz);
    e.pose_ready = true;
  }
}

void receiveFrame(const Sample& s, uint64_t c3_t_unwrapped, uint32_t& previous_id) {
  uint32_t gap = 0;
  if (e.have_source_frame) {
    const uint32_t delta = s.frame_id - e.last_source_frame_id;
    if (delta == 0 || delta > EngineConfig::MAX_ACCEPTED_FRAME_GAP) {
      ++e.duplicates;
      return;
    }
    gap = delta - 1;
    e.missing_frames += gap;
  }
  e.last_source_frame_id = s.frame_id;
  e.have_source_frame = true;
  previous_id = s.frame_id;
  const uint64_t t_s3 = mapTime(c3_t_unwrapped);
  updateImu(s, t_s3);

  DataPayload raw{};
  raw.frame_id = s.frame_id;
  raw.calibration_id = 0;
  raw.sample_time_us = t_s3;
  raw.hall_time_us = (s.hall_age_us == 0xffff || s.hall_age_us > t_s3) ? 0 : t_s3 - s.hall_age_us;
  raw.imu_time_us = (s.imu_age_us == 0xffff || s.imu_age_us > t_s3) ? 0 : t_s3 - s.imu_age_us;
  raw.device_status = s.status;
  if (e.pose_ready && (s.status & STATUS_IMU_VALID)) {
    raw.device_status |= STATUS_POSE_VALID | STATUS_POSE_YAW_RELATIVE;
  }
  raw.drop_flags = s.drop_flags | (gap ? static_cast<uint32_t>(DROP_TX_BACKPRESSURE) : 0u);
  raw.hall_x_raw = s.hx; raw.hall_y_raw = s.hy; raw.hall_z_raw = s.hz;
  raw.hall_temp_raw = s.ht;
  raw.hall_conv_status = s.hall_conv;
  raw.hall_device_status = s.hall_device;
  raw.imu_status = s.imu_status;
  raw.imu_temp_raw = s.it;
  raw.gyro_x_raw = s.gx; raw.gyro_y_raw = s.gy; raw.gyro_z_raw = s.gz;
  raw.accel_x_raw = s.ax; raw.accel_y_raw = s.ay; raw.accel_z_raw = s.az;
  raw.q_w = e.qw; raw.q_x = e.qx; raw.q_y = e.qy; raw.q_z = e.qz;

  // ★ OLED 快照: 本机无本地传感器, 把 C3 上传的原始码 + 姿态写入 g_ble,
  //    oled_display.h 的 read_sensor_snapshot() 每 50ms 取一次快照渲染。
  g_ble.hall_x_raw = s.hx; g_ble.hall_y_raw = s.hy; g_ble.hall_z_raw = s.hz;
  g_ble.hall_temp_raw = s.ht;
  g_ble.hall_conv_status = s.hall_conv;
  g_ble.hall_device_status = s.hall_device;
  g_ble.device_status = raw.device_status;
  g_ble.drop_flags = raw.drop_flags;
  g_ble.accel_x_raw = s.ax; g_ble.accel_y_raw = s.ay; g_ble.accel_z_raw = s.az;
  g_ble.gyro_x_raw = s.gx; g_ble.gyro_y_raw = s.gy; g_ble.gyro_z_raw = s.gz;
  g_ble.imu_temp_raw = s.it; g_ble.imu_status = s.imu_status;
  g_ble.q_w = e.qw; g_ble.q_x = e.qx; g_ble.q_y = e.qy; g_ble.q_z = e.qz;
  g_ble.hall_ok = (s.status & STATUS_HALL_VALID) != 0;
  g_ble.imu_ok = (s.status & STATUS_IMU_VALID) != 0;
  g_ble.pose_ok = e.pose_ready;

  if (e.stream_to_usb && usbWrite(PacketType::DATA, &raw, sizeof(raw)))
    ++e.usb_data_sent;
  ++e.rx_good_frames;

  const bool hall_unit_known = e.hall_variant == 1 || e.hall_variant == 2;
  const float lsb_per_mT = e.hall_variant == 1 ? 820.f : 250.f;
  float hall_mT[3] = {s.hx / lsb_per_mT, s.hy / lsb_per_mT, s.hz / lsb_per_mT};
  float acceleration_g[3] = {s.ax*.000122f, s.ay*.000122f, s.az*.000122f};
  const float dt = e.last_output_us ?
      static_cast<float>(t_s3 - e.last_output_us) * 1e-6f : .005f;
  e.last_output_us = t_s3;
  const Adaptive::Output opt = e.adaptive.update(hall_mT,
      hall_unit_known && (s.status & STATUS_HALL_VALID) != 0, acceleration_g,
      (s.status & STATUS_IMU_VALID) != 0, dt, gap);

  // Supplemental V1 packet: old Studio host ignores unknown type safely.
  if ((s.frame_id % 4u) == 0 && e.stream_to_usb) {
    OptimizedPayload p{};
    p.source_frame_id = s.frame_id;
    p.flags = opt.flags;
    for (int k = 0; k < 3; ++k) {
      p.hall_mT[k] = opt.hall_mT[k];
      p.motion_accel_g[k] = opt.motion_accel_g[k];
    }
    p.motion_energy = opt.motion_energy;
    p.jerk_g_per_s = opt.jerk_g_per_s;
    p.confidence = opt.confidence;
    p.missed_frames = gap;
    (void)usbWrite(static_cast<PacketType>(0x20), &p, sizeof(p));
  }
}

void handleRadioPacket(const RadioMessage& msg) {
  Header hdr{};
  const uint8_t* payload = nullptr;
  if (!parse_packet(msg.data, msg.size, hdr, payload)) {
    ++e.packets_bad;
    return;
  }
  ++e.packets_ok;
  if (e.last_link_us && msg.rx_time_s3_us - e.last_link_us > 5000000ULL) {
    sourceReboot(); // an unknown-length disconnect invalidates source tick epoch
  }
  e.last_link_us = msg.rx_time_s3_us;
  if (e.have_packet_seq) {
    const uint32_t delta = hdr.bundle_seq - e.last_packet_seq;
    if (delta > 1 && delta < 10000) e.transport_gaps += delta - 1;
  }
  e.have_packet_seq = true;
  e.last_packet_seq = hdr.bundle_seq;
  if (hdr.type == TYPE_META) {
    Meta info{};
    memcpy(&info, payload, sizeof(info));
    receiveMeta(info);
    return;
  }
  Sample samples[MAX_SAMPLES]{};
  uint64_t clock_values[MAX_SAMPLES]{};
  memcpy(samples, payload, static_cast<size_t>(hdr.count)*sizeof(Sample));
  // Unwrap into a temporary state so a corrupt/reordered packet cannot
  // poison the epoch if one of its later samples goes backward.
  TickUnwrapper pending_unwrap = e.unwrap;
  for (size_t i = 0; i < hdr.count; ++i) {
    if (!pending_unwrap.unwrap(samples[i].sensor_time_us32, clock_values[i])) {
      ++e.duplicates;
      return;
    }
  }
  e.unwrap = pending_unwrap;
  observeClock(clock_values[hdr.count-1], msg.rx_time_s3_us);
  uint32_t previous_id = e.last_source_frame_id;
  for (size_t i = 0; i < hdr.count; ++i) receiveFrame(samples[i], clock_values[i], previous_id);
}

void handleHostCommand(const PacketHeader* h, const uint8_t* payload, uint64_t received_us) {
  switch (static_cast<PacketType>(h->type)) {
    case PacketType::CMD_SYNC: {
      if (h->payload_len != sizeof(SyncRequestPayload)) return;
      SyncRequestPayload req{};
      memcpy(&req, payload, sizeof(req));
      SyncResponsePayload resp{};
      resp.host_tx_ns = req.host_tx_ns;
      resp.nonce = req.nonce;
      resp.mcu_rx_us = received_us;
      resp.mcu_tx_us = static_cast<uint64_t>(esp_timer_get_time());
      (void)usbWrite(PacketType::SYNC_RESP, &resp, sizeof(resp));
      break;
    }
    case PacketType::CMD_START: e.stream_to_usb = true; sendHello(); break;
    case PacketType::CMD_STOP: e.stream_to_usb = false; break;
    case PacketType::CMD_PING: sendHello(); break;
    default: break;
  }
}

void processHostCommands() {
  // Host uses USB CDC to S3 only, NEVER UART between C3 and S3.
  while (Serial.available() > 0) {
    int b = Serial.read();
    if (b < 0) break;
    if (b == 0) {
      if (!hostRxLen) continue;
      const uint64_t arrival = static_cast<uint64_t>(esp_timer_get_time());
      uint8_t raw[MAX_RAW_PACKET];
      const size_t len = cobs_decode(hostRxBuf, hostRxLen, raw, sizeof(raw));
      hostRxLen = 0;
      PacketHeader* hdr = nullptr;
      uint8_t* payload = nullptr;
      if (len && validate_raw_packet(raw, len, hdr, payload))
        handleHostCommand(hdr, payload, arrival);
    } else if (hostRxLen < sizeof(hostRxBuf)) {
      hostRxBuf[hostRxLen++] = static_cast<uint8_t>(b);
    } else hostRxLen = 0;
  }
}

#if defined(BRUSH_LINK_BLE)
const NimBLEAdvertisedDevice* advertised = nullptr;
volatile bool shouldConnect = false;
volatile bool connected = false;
NimBLEClient* radioClient = nullptr;

void notificationCB(NimBLERemoteCharacteristic*, uint8_t* bytes, size_t length, bool) {
  if (!rxQueue || !bytes || length > MAX_PACKET) {
    ++queueDrops;
    return;
  }
  RadioMessage msg{};
  msg.rx_time_s3_us = static_cast<uint64_t>(esp_timer_get_time());
  msg.size = static_cast<uint16_t>(length);
  memcpy(msg.data, bytes, length);
  if (xQueueSend(rxQueue, &msg, 0) != pdTRUE) ++queueDrops;
}

class ScanCallbacks : public NimBLEScanCallbacks {
  void onResult(const NimBLEAdvertisedDevice* device) override {
    if (!connected && !shouldConnect && device->isAdvertisingService(NimBLEUUID(NodeConfig::BLE_SERVICE))) {
      advertised = device;
      shouldConnect = true;
      NimBLEDevice::getScan()->stop();
    }
  }
};
class ClientCallbacks : public NimBLEClientCallbacks {
  void onDisconnect(NimBLEClient*, int) override {
    connected = false;
  }
};
ScanCallbacks scanCallbacks;
ClientCallbacks clientCallbacks;

void beginScan() {
  NimBLEScan* scan = NimBLEDevice::getScan();
  if (!scan->isScanning()) scan->start(0);
}

void initRadio() {
  NimBLEDevice::init("EBrush-S3-Central");
  NimBLEDevice::setMTU(247);
  NimBLEDevice::setPower(3);
  NimBLEScan* scan = NimBLEDevice::getScan();
  scan->setScanCallbacks(&scanCallbacks, false);
  scan->setInterval(80);
  scan->setWindow(65);
  scan->setActiveScan(false);
  beginScan();
}

void manageRadio() {
  if (shouldConnect) {
    shouldConnect = false;
    if (radioClient) {
      NimBLEDevice::deleteClient(radioClient);
      radioClient = nullptr;
    }
    radioClient = NimBLEDevice::createClient();
    radioClient->setClientCallbacks(&clientCallbacks, false);
    radioClient->setConnectionParams(12, 18, 0, 160);
    radioClient->setConnectTimeout(5000);
    if (radioClient->connect(advertised)) {
      radioClient->setDataLen(251);
      NimBLERemoteService* service = radioClient->getService(NodeConfig::BLE_SERVICE);
      NimBLERemoteCharacteristic* data = service ? service->getCharacteristic(NodeConfig::BLE_DATA_CHAR) : nullptr;
      if (data && data->canNotify() && data->subscribe(true, notificationCB)) {
        connected = true;
        e.negotiated_mtu = radioClient->getMTU();
      } else radioClient->disconnect();
    }
    if (!connected) beginScan();
  } else if (!connected && !NimBLEDevice::getScan()->isScanning()) {
    beginScan();
  }
}
#elif defined(BRUSH_LINK_WIFI)
WiFiUDP udp;
void initRadio() {
  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false); // AP is the S3 high-energy side.
  WiFi.softAP(NodeConfig::WIFI_SSID, NodeConfig::WIFI_PASSWORD, 1, false, 1);
  udp.begin(NodeConfig::WIFI_PORT);
}
void manageRadio() {
  const int bytes = udp.parsePacket();
  if (bytes <= 0) return;
  RadioMessage msg{};
  msg.rx_time_s3_us = static_cast<uint64_t>(esp_timer_get_time());
  if (bytes > static_cast<int>(sizeof(msg.data))) {
    while (udp.available()) (void)udp.read();
    ++queueDrops;
    return;
  }
  msg.size = static_cast<uint16_t>(udp.read(msg.data, bytes));
  if (!rxQueue || msg.size != bytes || xQueueSend(rxQueue, &msg, 0) != pdTRUE) ++queueDrops;
}
#endif
} // namespace

// LED 状态指示：GPIO 48 (ESP32-S3-DevKitC-1 板载 WS2812 RGB LED)
constexpr int LED_PIN = 48;
enum LedMode { SCANNING, CONNECTED, ERROR };
LedMode led_mode = SCANNING;
uint32_t next_led_toggle = 0;
bool led_on = false;

void updateLed() {
  uint32_t now = millis();
  uint32_t interval = 0;

  switch (led_mode) {
    case SCANNING: interval = 100; break;   // 快闪 = 扫描中
    case CONNECTED: interval = 500; break;  // 慢闪 = 已连接
    case ERROR: interval = 0; break;        // 常亮 = 错误
  }

  if (interval == 0) {
    // 常亮红色 (错误)
    if (!led_on) {
      neopixelWrite(LED_PIN, 32, 0, 0);  // 红色
      led_on = true;
    }
    return;
  }

  if (static_cast<int32_t>(now - next_led_toggle) >= 0) {
    led_on = !led_on;
    if (led_on) {
      // 根据模式设置颜色
      if (led_mode == SCANNING) {
        neopixelWrite(LED_PIN, 0, 0, 32);  // 蓝色 = 扫描中
      } else { // CONNECTED
        neopixelWrite(LED_PIN, 0, 32, 0);  // 绿色 = 已连接
      }
    } else {
      neopixelWrite(LED_PIN, 0, 0, 0);  // 熄灭
    }
    next_led_toggle = now + interval;
  }
}

void setLedMode(LedMode mode) {
  led_mode = mode;
  next_led_toggle = millis();
  led_on = false;
}

void setup() {
  // 初始化 LED (WS2812 on GPIO 48)
  neopixelWrite(LED_PIN, 0, 0, 0);

  Serial.begin(EngineConfig::USB_SERIAL_BAUD);
  delay(500);
  Serial.println("\n=== S3 BLE Central Starting ===");
  Serial.printf("USB Serial baud: %d\n", EngineConfig::USB_SERIAL_BAUD);
  rxQueue = xQueueCreate(EngineConfig::TX_QUEUE_LENGTH, sizeof(RadioMessage));
  e.next_health_ms = millis() + EngineConfig::HEALTH_INTERVAL_MS;
  e.next_hello_ms = millis() + EngineConfig::HELLO_INTERVAL_MS;
  initRadio();

  // ★ OLED 初始化 (专用 I2C1 / Wire1, SDA=GPIO15, SCL=GPIO16, 与传感器总线物理隔离)
  oled_init();

  // ★ OLED 按钮: 信号脚 GPIO5, 另一端接 GND (上拉输入, 按下=低电平)。
  //    必须在 setup() 显式配置, 否则引脚悬空 digitalRead 不确定, 按钮失效。
  pinMode(BOOT_BUTTON_PIN, INPUT_PULLUP);
}

void loop() {
  processHostCommands();
  manageRadio();
  RadioMessage msg{};
  // A bounded drain protects the USB event loop from bursty wireless batches.
  for (int i = 0; rxQueue && i < 5 && xQueueReceive(rxQueue, &msg, 0) == pdTRUE; ++i)
    handleRadioPacket(msg);

  // ★ OLED 显示任务: 按钮扫描 + 四态页面切换 + 50ms 节流刷新
  oled_uploop();
  const uint32_t now = millis();
  if (static_cast<int32_t>(now - e.next_health_ms) >= 0) {
    e.next_health_ms = now + EngineConfig::HEALTH_INTERVAL_MS;
    sendHealth();
  }
  if (static_cast<int32_t>(now - e.next_hello_ms) >= 0) {
    e.next_hello_ms = now + EngineConfig::HELLO_INTERVAL_MS;
    sendHello();
  }
  
  // 更新 LED 状态
  updateLed();
  
  // 检查连接状态并更新 LED
  if (e.last_link_us && static_cast<uint64_t>(esp_timer_get_time()) - e.last_link_us < 2500000ULL) {
    if (led_mode != CONNECTED) setLedMode(CONNECTED);
  } else if (led_mode == CONNECTED) {
    setLedMode(SCANNING);
  }
  
  delay(1);
}

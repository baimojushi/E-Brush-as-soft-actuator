#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <esp_system.h>
#include <esp_timer.h>

#include "brush_config.h"
#include "brush_protocol.h"
#include "tmag5273.h"
#include "lsm6dsr.h"
#include "mahony6.h"

using namespace BrushConfig;
using namespace BrushProtocol;

namespace {

TwoWire hallWire(0);
SPIClass imuSpi(FSPI);
Tmag5273 hall;
Lsm6dsr imu;
Mahony6 poseFilter;

volatile bool hallIrqFlag = false;

struct Counters {
  uint32_t frames_intended = 0;
  uint32_t frames_sent = 0;
  uint32_t tx_drops = 0;
  uint32_t rx_crc_errors = 0;
  uint32_t rx_frame_errors = 0;
  uint32_t hall_read_errors = 0;
  uint32_t imu_read_errors = 0;
  uint32_t hall_reinits = 0;
  uint32_t imu_reinits = 0;
} counters;

struct Runtime {
  bool hall_ok = false;
  bool imu_ok = false;
  bool streaming = true;

  uint32_t boot_id = 0;
  uint32_t packet_seq = 0;
  uint32_t frame_id = 0;

  uint64_t next_sample_us = 0;
  uint64_t next_health_us = 0;
  uint64_t next_hall_reinit_us = 0;
  uint64_t next_imu_reinit_us = 0;

  uint64_t hall_time_us = 0;
  uint64_t imu_time_us = 0;
  uint64_t last_imu_filter_us = 0;

  bool i2c_recovered_since_last_frame = false;
  bool spi_recovered_since_last_frame = false;

  Tmag5273::Sample hall_sample{};
  Lsm6dsr::Sample imu_sample{};
  float q_w = 1.0f, q_x = 0.0f, q_y = 0.0f, q_z = 0.0f;
} rt;

uint8_t rxWire[MAX_WIRE_PACKET];
size_t rxWireLen = 0;

void IRAM_ATTR onHallInterrupt() {
  hallIrqFlag = true;
}

bool serialWritePacket(PacketType type, const void* payload, uint16_t payload_len) {
  uint8_t wire[MAX_WIRE_PACKET];
  const size_t n = build_wire_packet(type, rt.packet_seq++, payload, payload_len, wire, sizeof(wire));
  if (n == 0) {
    ++counters.tx_drops;
    return false;
  }

  // 避免上位机断开时永久阻塞采集线程。
  // 某些 USB CDC 实现 availableForWrite() 语义不同，保留一个很小等待窗口。
  const uint64_t deadline = esp_timer_get_time() + 1500;
  while (Serial.availableForWrite() < static_cast<int>(n) && esp_timer_get_time() < deadline) {
    delayMicroseconds(50);
  }
  if (Serial.availableForWrite() < static_cast<int>(n)) {
    ++counters.tx_drops;
    return false;
  }

  const size_t written = Serial.write(wire, n);
  if (written != n) {
    ++counters.tx_drops;
    return false;
  }
  return true;
}

void sendHello() {
  HelloPayload p{};
  p.boot_id = rt.boot_id;
  p.reset_reason = static_cast<uint32_t>(esp_reset_reason());
  p.capabilities = CAP_HALL_A2 | CAP_IMU_6AXIS | CAP_QUAT_6AXIS | CAP_CLOCK_SYNC | CAP_HEALTH;
  p.serial_baud = SERIAL_BAUD;
  p.stream_hz = STREAM_HZ;
#ifdef BRUSH_FW_VERSION_MAJOR
  p.fw_major = BRUSH_FW_VERSION_MAJOR;
  p.fw_minor = BRUSH_FW_VERSION_MINOR;
  p.fw_patch = BRUSH_FW_VERSION_PATCH;
#else
  p.fw_major = 1; p.fw_minor = 0; p.fw_patch = 0;
#endif
  p.hall_variant = hall.variant();
  p.imu_who_am_i = imu.whoAmI();
  p.calibration_id = CALIBRATION_ID;
  (void)serialWritePacket(PacketType::HELLO, &p, sizeof(p));
}

void sendHealth() {
  HealthPayload p{};
  p.uptime_us = static_cast<uint64_t>(esp_timer_get_time());
  p.frames_intended = counters.frames_intended;
  p.frames_sent = counters.frames_sent;
  p.tx_drops = counters.tx_drops;
  p.rx_crc_errors = counters.rx_crc_errors;
  p.rx_frame_errors = counters.rx_frame_errors;
  p.hall_read_errors = counters.hall_read_errors;
  p.imu_read_errors = counters.imu_read_errors;
  p.hall_reinits = counters.hall_reinits;
  p.imu_reinits = counters.imu_reinits;
  p.free_heap = ESP.getFreeHeap();
  p.min_free_heap = ESP.getMinFreeHeap();
  (void)serialWritePacket(PacketType::HEALTH, &p, sizeof(p));
}

void recoverI2CBus() {
  hallWire.end();

  pinMode(PIN_HALL_SDA, INPUT_PULLUP);
  pinMode(PIN_HALL_SCL, OUTPUT_OPEN_DRAIN);
  digitalWrite(PIN_HALL_SCL, HIGH);
  delayMicroseconds(5);

  // 若从机在数据位中途掉电，最多 9 个时钟释放 SDA。
  for (int i = 0; i < 9 && digitalRead(PIN_HALL_SDA) == LOW; ++i) {
    digitalWrite(PIN_HALL_SCL, LOW);
    delayMicroseconds(5);
    digitalWrite(PIN_HALL_SCL, HIGH);
    delayMicroseconds(5);
  }

  // 生成一个 STOP 条件。
  pinMode(PIN_HALL_SDA, OUTPUT_OPEN_DRAIN);
  digitalWrite(PIN_HALL_SDA, LOW);
  delayMicroseconds(5);
  digitalWrite(PIN_HALL_SCL, HIGH);
  delayMicroseconds(5);
  digitalWrite(PIN_HALL_SDA, HIGH);
  delayMicroseconds(5);

  hallWire.begin(PIN_HALL_SDA, PIN_HALL_SCL, I2C_HZ);
  hallWire.setTimeOut(8);
  rt.i2c_recovered_since_last_frame = true;
}

bool initHall() {
  recoverI2CBus();
  const bool ok = hall.begin(hallWire, HALL_I2C_ADDR, HALL_CONV_AVG_CODE, HALL_LOW_NOISE);
  if (ok && hall.variant() != HALL_EXPECTED_VARIANT) {
    return false;
  }
  if (ok) {
    hallIrqFlag = false;
    rt.hall_time_us = 0;
  }
  return ok;
}

bool initImu() {
  digitalWrite(PIN_IMU_CS, HIGH);
  delay(2);
  const bool ok = imu.begin(imuSpi, PIN_IMU_CS, SPI_HZ);
  if (ok) {
    poseFilter.reset();
    rt.last_imu_filter_us = 0;
    rt.imu_time_us = 0;
    rt.spi_recovered_since_last_frame = true;
  }
  return ok;
}

void maybeReinitSensors(uint64_t now_us) {
  if (!rt.hall_ok && now_us >= rt.next_hall_reinit_us) {
    ++counters.hall_reinits;
    rt.hall_ok = initHall();
    rt.next_hall_reinit_us = now_us + static_cast<uint64_t>(SENSOR_REINIT_INTERVAL_MS) * 1000ULL;
  }
  if (!rt.imu_ok && now_us >= rt.next_imu_reinit_us) {
    ++counters.imu_reinits;
    rt.imu_ok = initImu();
    rt.next_imu_reinit_us = now_us + static_cast<uint64_t>(SENSOR_REINIT_INTERVAL_MS) * 1000ULL;
  }
}

void updatePoseFromImu(uint64_t imu_time_us, const Lsm6dsr::Sample& s) {
  if (rt.last_imu_filter_us == 0) {
    rt.last_imu_filter_us = imu_time_us;
    return;
  }
  const float dt = static_cast<float>(imu_time_us - rt.last_imu_filter_us) * 1e-6f;
  rt.last_imu_filter_us = imu_time_us;

  constexpr float DEG_TO_RAD_F = 0.01745329251994329577f;
  const float gx = static_cast<float>(s.gx_raw) * (IMU_GYRO_MDPS_PER_LSB * 0.001f) * DEG_TO_RAD_F;
  const float gy = static_cast<float>(s.gy_raw) * (IMU_GYRO_MDPS_PER_LSB * 0.001f) * DEG_TO_RAD_F;
  const float gz = static_cast<float>(s.gz_raw) * (IMU_GYRO_MDPS_PER_LSB * 0.001f) * DEG_TO_RAD_F;

  const float ax = static_cast<float>(s.ax_raw) * (IMU_ACCEL_MG_PER_LSB * 0.001f);
  const float ay = static_cast<float>(s.ay_raw) * (IMU_ACCEL_MG_PER_LSB * 0.001f);
  const float az = static_cast<float>(s.az_raw) * (IMU_ACCEL_MG_PER_LSB * 0.001f);

  if (poseFilter.update(gx, gy, gz, ax, ay, az, dt)) {
    poseFilter.quaternion(rt.q_w, rt.q_x, rt.q_y, rt.q_z);
  }
}

void acquireAndSendFrame(uint64_t scheduled_us, bool scheduler_overrun) {
  ++counters.frames_intended;

  uint32_t status = 0;
  uint32_t drops = scheduler_overrun ? DROP_SCHED_OVERRUN : DROP_NONE;

  if (rt.streaming) status |= STATUS_STREAMING;

  // Hall：优先使用锁存 INT 提示；即使 IRQ 被漏掉，也按主帧周期轮询，
  // 防止单次边沿丢失造成链路永久停顿。
  if (rt.hall_ok) {
    status |= STATUS_HALL_PRESENT;
    const bool should_read = hallIrqFlag || (scheduled_us - rt.hall_time_us >= STREAM_PERIOD_US);
    if (should_read) {
      Tmag5273::Sample sample{};
      if (hall.readSample(sample)) {
        rt.hall_sample = sample;
        rt.hall_time_us = static_cast<uint64_t>(esp_timer_get_time());
        hallIrqFlag = false;
      } else {
        ++counters.hall_read_errors;
        drops |= DROP_HALL_READ_FAIL;
        // 连续失败交给下一个周期的身份校验/重初始化。
        if ((counters.hall_read_errors % 3u) == 0u) {
          rt.hall_ok = false;
          rt.next_hall_reinit_us = scheduled_us + static_cast<uint64_t>(SENSOR_REINIT_INTERVAL_MS) * 1000ULL;
        }
      }
    }

    if (rt.hall_time_us && scheduled_us - rt.hall_time_us <= HALL_STALE_US) {
      status |= STATUS_HALL_VALID;
    } else {
      drops |= DROP_HALL_STALE;
    }

    if (rt.hall_sample.conv_status & 0x02u) status |= STATUS_HALL_DIAG;
    const int16_t lim = 31000;
    if (abs(rt.hall_sample.x_raw) > lim ||
        abs(rt.hall_sample.y_raw) > lim ||
        abs(rt.hall_sample.z_raw) > lim) {
      status |= STATUS_HALL_NEAR_SAT;
    }
  } else {
    drops |= DROP_HALL_STALE;
  }

  // IMU：图纸未分配 DRDY 引脚，因此读取 STATUS_REG 做新鲜度判定。
  if (rt.imu_ok) {
    status |= STATUS_IMU_PRESENT;
    Lsm6dsr::Sample sample{};
    bool fresh = false;
    if (imu.readSample(sample, fresh)) {
      rt.imu_sample = sample;
      rt.imu_time_us = static_cast<uint64_t>(esp_timer_get_time());
      status |= STATUS_IMU_VALID;
      if (fresh) {
        status |= STATUS_IMU_FRESH;
        updatePoseFromImu(rt.imu_time_us, sample);
      }
    } else {
      ++counters.imu_read_errors;
      drops |= DROP_IMU_READ_FAIL;
      if ((counters.imu_read_errors % 3u) == 0u) {
        rt.imu_ok = false;
        rt.next_imu_reinit_us = scheduled_us + static_cast<uint64_t>(SENSOR_REINIT_INTERVAL_MS) * 1000ULL;
      }
    }

    if (!rt.imu_time_us || scheduled_us - rt.imu_time_us > IMU_STALE_US) {
      status &= ~(STATUS_IMU_VALID | STATUS_IMU_FRESH);
      drops |= DROP_IMU_STALE;
    }
  } else {
    drops |= DROP_IMU_STALE;
  }

  if (rt.imu_time_us) {
    status |= STATUS_POSE_VALID | STATUS_POSE_YAW_RELATIVE;
  }
  if (rt.i2c_recovered_since_last_frame) status |= STATUS_I2C_RECOVERED;
  if (rt.spi_recovered_since_last_frame) status |= STATUS_SPI_RECOVERED;

  DataPayload p{};
  p.frame_id = rt.frame_id++;
  p.calibration_id = CALIBRATION_ID;
  p.sample_time_us = scheduled_us;
  p.hall_time_us = rt.hall_time_us;
  p.imu_time_us = rt.imu_time_us;
  p.device_status = status;
  p.drop_flags = drops;

  p.hall_x_raw = rt.hall_sample.x_raw;
  p.hall_y_raw = rt.hall_sample.y_raw;
  p.hall_z_raw = rt.hall_sample.z_raw;
  p.hall_temp_raw = rt.hall_sample.temp_raw;
  p.hall_conv_status = rt.hall_sample.conv_status;
  p.hall_device_status = rt.hall_sample.device_status;
  p.imu_status = rt.imu_sample.status;

  p.imu_temp_raw = rt.imu_sample.temp_raw;
  p.gyro_x_raw = rt.imu_sample.gx_raw;
  p.gyro_y_raw = rt.imu_sample.gy_raw;
  p.gyro_z_raw = rt.imu_sample.gz_raw;
  p.accel_x_raw = rt.imu_sample.ax_raw;
  p.accel_y_raw = rt.imu_sample.ay_raw;
  p.accel_z_raw = rt.imu_sample.az_raw;

  p.q_w = rt.q_w; p.q_x = rt.q_x; p.q_y = rt.q_y; p.q_z = rt.q_z;

  if (serialWritePacket(PacketType::DATA, &p, sizeof(p))) {
    ++counters.frames_sent;
  } else {
    // 本帧无法发送时，frame_id 已递增；serialWritePacket() 已统计 tx_drops。
    // 上位机可以通过 frame_id 缺口识别丢失。
  }

  rt.i2c_recovered_since_last_frame = false;
  rt.spi_recovered_since_last_frame = false;
}

void handleCommand(const PacketHeader* h, const uint8_t* payload, uint64_t mcu_rx_us) {
  if (!h) return;
  const PacketType type = static_cast<PacketType>(h->type);

  switch (type) {
    case PacketType::CMD_SYNC: {
      if (h->payload_len != sizeof(SyncRequestPayload)) return;
      SyncRequestPayload req{};
      memcpy(&req, payload, sizeof(req));

      SyncResponsePayload resp{};
      resp.host_tx_ns = req.host_tx_ns;
      resp.nonce = req.nonce;
      resp.mcu_rx_us = mcu_rx_us;
      resp.mcu_tx_us = static_cast<uint64_t>(esp_timer_get_time());
      (void)serialWritePacket(PacketType::SYNC_RESP, &resp, sizeof(resp));
      break;
    }

    case PacketType::CMD_START:
      rt.streaming = true;
      break;

    case PacketType::CMD_STOP:
      rt.streaming = false;
      break;

    case PacketType::CMD_PING:
      sendHello();
      break;

    default:
      break;
  }
}

void processSerialRx() {
  while (Serial.available() > 0) {
    const int b = Serial.read();
    if (b < 0) return;

    if (b == 0) {
      const uint64_t mcu_rx_us = static_cast<uint64_t>(esp_timer_get_time());
      if (rxWireLen == 0) continue;

      uint8_t raw[MAX_RAW_PACKET];
      const size_t raw_len = cobs_decode(rxWire, rxWireLen, raw, sizeof(raw));
      rxWireLen = 0;

      if (raw_len == 0) {
        ++counters.rx_frame_errors;
        continue;
      }

      PacketHeader* h = nullptr;
      uint8_t* payload = nullptr;
      if (!validate_raw_packet(raw, raw_len, h, payload)) {
        // 不能区分 CRC 与其他格式错误时，先做一次显式 CRC 判定用于统计。
        if (raw_len >= sizeof(PacketHeader) + sizeof(uint32_t)) {
          const size_t body_len = raw_len - sizeof(uint32_t);
          uint32_t rx_crc = 0;
          memcpy(&rx_crc, raw + body_len, sizeof(rx_crc));
          if (crc32_ieee(raw, body_len) != rx_crc) ++counters.rx_crc_errors;
          else ++counters.rx_frame_errors;
        } else {
          ++counters.rx_frame_errors;
        }
        continue;
      }

      handleCommand(h, payload, mcu_rx_us);
    } else {
      if (rxWireLen < sizeof(rxWire)) {
        rxWire[rxWireLen++] = static_cast<uint8_t>(b);
      } else {
        // 过长帧：丢到下一个 0 分隔符重新同步。
        ++counters.rx_frame_errors;
        rxWireLen = 0;
      }
    }
  }
}

}  // namespace

void setup() {
#ifndef BRUSH_NATIVE_USB
  // HardwareSerial 可显式放大缓冲区；原生 USB CDC 的接口由 Arduino Core 版本决定。
  Serial.setRxBufferSize(4096);
  Serial.setTxBufferSize(8192);
#endif
  Serial.begin(SERIAL_BAUD);

  pinMode(PIN_IMU_CS, OUTPUT);
  digitalWrite(PIN_IMU_CS, HIGH);

  pinMode(PIN_HALL_INT, INPUT);  // 图纸已有 4.7 kΩ 外部上拉
  attachInterrupt(digitalPinToInterrupt(PIN_HALL_INT), onHallInterrupt, FALLING);

  hallWire.begin(PIN_HALL_SDA, PIN_HALL_SCL, I2C_HZ);
  hallWire.setTimeOut(8);

  imuSpi.begin(PIN_SPI_SCLK, PIN_SPI_MISO, PIN_SPI_MOSI, PIN_IMU_CS);

  rt.boot_id = esp_random();
  const uint64_t now = static_cast<uint64_t>(esp_timer_get_time());

  rt.hall_ok = initHall();
  rt.imu_ok = initImu();

  rt.next_hall_reinit_us = now + static_cast<uint64_t>(SENSOR_REINIT_INTERVAL_MS) * 1000ULL;
  rt.next_imu_reinit_us = rt.next_hall_reinit_us;
  rt.next_sample_us = now + STREAM_PERIOD_US;
  rt.next_health_us = now + static_cast<uint64_t>(HEALTH_PERIOD_MS) * 1000ULL;

  // 给上位机串口打开留少量时间，同时保持无文本日志，避免污染二进制协议。
  delay(50);
  sendHello();
}

void loop() {
  processSerialRx();

  const uint64_t now = static_cast<uint64_t>(esp_timer_get_time());
  maybeReinitSensors(now);

  if (rt.streaming && now >= rt.next_sample_us) {
    bool overrun = false;

    // 若延迟超过 3 个周期，直接重定相位，避免“补发”旧样本造成长时间突发。
    if (now - rt.next_sample_us > 3ULL * STREAM_PERIOD_US) {
      overrun = true;
      rt.next_sample_us = now;
    }

    const uint64_t scheduled = rt.next_sample_us;
    rt.next_sample_us += STREAM_PERIOD_US;
    acquireAndSendFrame(scheduled, overrun);
  }

  if (now >= rt.next_health_us) {
    rt.next_health_us = now + static_cast<uint64_t>(HEALTH_PERIOD_MS) * 1000ULL;
    sendHealth();
  }

  delay(0);
}

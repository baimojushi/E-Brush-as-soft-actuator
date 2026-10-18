#pragma once
#include <stdint.h>
#include "node_config.h"
namespace EngineConfig {
constexpr uint32_t USB_SERIAL_BAUD = 921600;
constexpr uint32_t HELLO_INTERVAL_MS = 5000;
constexpr uint32_t HEALTH_INTERVAL_MS = 1000;
constexpr uint8_t TX_QUEUE_LENGTH = 16;
constexpr uint32_t MAX_ACCEPTED_FRAME_GAP = 10000;
}

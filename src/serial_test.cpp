// Minimal serial test for C3/S3:
// - C3 (USB Serial/JTAG): Serial maps to native USB when ARDUINO_USB_MODE=1 && ARDUINO_USB_CDC_ON_BOOT=1
// - S3 (UART0):   Serial maps to CH343 when ARDUINO_USB_CDC_ON_BOOT=0
#include <Arduino.h>

#if defined(ARDUINO_USB_MODE) && ARDUINO_USB_MODE == 1 && \
    defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT == 1
// C3 with USB Serial/JTAG enabled
#define TEST_LABEL "C3 USB Serial/JTAG"
#elif defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT == 1
// S3 or C3 with USB CDC enabled (TinyUSB)
#define TEST_LABEL "USB CDC"
#else
// S3 UART0 or C3 without USB CDC
#define TEST_LABEL "S3 UART0"
#endif

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.print("=== BOOT: ");
  Serial.print(TEST_LABEL);
  Serial.println(" ready ===");
}

void loop() {
  Serial.print("HEARTBEAT ");
  Serial.print(TEST_LABEL);
  Serial.print(" uptime_ms=");
  Serial.println((unsigned long)millis());
  delay(1000);
}
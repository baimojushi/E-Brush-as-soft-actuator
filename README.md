# E-Brush C3 -> S3 wireless split, 2.0 (prototype)

**BLE is the default. Wi-Fi is a separate recovery firmware target, not a
simultaneously enabled second radio. There is no C3--S3 UART link.**

```
TMAG5273 I2C --+
                +-- ESP32-C3 (80 MHz, raw sensor packets, timestamps, no GUI)
LSM6DSR SPI ----+               |
                            BLE Notify (default)
                            or Wi-Fi UDP (fallback)
                                |
                                v
                         ESP32-S3 (central/AP)
                    CRC + frame gap + clock unwrap
                    quaternion + adaptive Hall/motion
                                |
                      S3 native USB-CDC / BRUSH-V1
                                |
                           e-Brush Studio
```

## Build and flash

Requires **PlatformIO**, a correctly wired ESP32-C3 DevKitM-1-compatible
board and an ESP32-S3 DevKitC-1-compatible board. Two devices, flashed
**separately**, with the correct port selected in PlatformIO.

Default, BLE:

```sh
pio run -e c3_ble
pio run -e s3_ble
pio run -e c3_ble -t upload --upload-port <C3_PORT>
pio run -e s3_ble -t upload --upload-port <S3_PORT>
```

If BLE throughput / RF range is insufficient, **flash both devices** to the
Wi-Fi backup pair; neither firmware attempts to enable both radio stacks:

```sh
pio run -e c3_wifi
pio run -e s3_wifi
pio run -e c3_wifi -t upload --upload-port <C3_PORT>
pio run -e s3_wifi -t upload --upload-port <S3_PORT>
```

For the Wi-Fi build replace the prototype AP password in
`include/node_config.h`, or pass the same
`-DEBRUSH_WIFI_PASSWORD='"<16+_char_unique_password>"'` compile-time flag
in **both** builds. Default prototype password is not production security.
The S3 is the SoftAP, the C3 a power-save station; UDP destination is
192.168.4.1:5568. BLE uses a single GATT notification characteristic with
CRC-protected application bundles; target ATT MTU is 247 bytes.

## IMPORTANT: C3 sensor wiring is NOT the S3 wiring

| Device | Pin | ESP32-C3 GPIO |
|--------|-----|----------------|
| Hall TMAG5273 | SDA | 4 |
| Hall TMAG5273 | SCL | 5 |
| IMU LSM6DSR | SPI SCK | 6 |
| IMU LSM6DSR | SPI MOSI | 7 |
| IMU LSM6DSR | SPI MISO | 3 |
| IMU LSM6DSR | SPI CS | 10 |
| Both sensors | VCC, GND | regulated 3V3, GND |

These pin numbers are **only for the selected C3 DevKitM-1 reference**.
GPIO4--7 are also JTAG pins; do not attempt simultaneous external JTAG use.
Never copy the former ESP32-S3 pin numbers, especially GPIO12/13, onto a C3;
GPIO12--17 are tied to flash on common C3 modules. GPIO2, 8, 9 are boot
straps; GPIO18/19 may be used for USB. Verify the actual board pinout before
applying power. The C3 is the pen endpoint; the S3 stays off-pen.

## What runs on which board

C3 (low-energy side): 80 MHz CPU, TMAG5273 I2C continuous conversion with
4x averaging, LSM6DSR SPI 208 Hz ODR, monotonic microsecond timestamp,
sensor-age/status/retry flags, bounded 24-sample sender FIFO, up to four
raw samples per BLE notification / UDP datagram, periodic status metadata.
No serial debug loop, no large floating-point processing, no displays,
no Wi-Fi in BLE firmware and no BLE in Wi-Fi firmware.

S3 (high-energy side): radio central / access point, CRC check and discard
malformed packets, queue overrun/frame loss counters, 32-bit tick unwrap,
source clock mapping, six-axis Mahony pose, adaptive Hall/motion confidence,
original raw BRUSH-V1 DATA/HELLO/HEALTH/SYNC protocol over S3 **USB CDC**;
extra extension packets type `0x20` (processed motion) and `0x21` (link health).
The S3 may use a USB cable to the computer, but the C3--S3 hop is wireless.

Existing Studio V1 readers still receive the original raw DATA protocol.
`studio/wireless_studio_adapter.py` adds optional decoded extension channels.
`studio/accel_trail.py` provides translucent time-decaying trails; the
trajectory's calibrated 2D/3D position must come from an external pose mapper
/ camera tracker, NOT integrated raw acceleration. See
`studio/STUDIO_INTEGRATION.txt` for integration steps. **The current Studio
v1.2 GUI source was not part of the files in this handoff**, so this package
is an integration-ready adapter, not a verified Studio v1.2 GUI patch.

## Verification included / not included

```sh
sh tests/run_local_tests.sh
```

Local tests check 1--4 sample link bundles, metadata/CRC, malformed lengths,
32-bit tick rollover, adaptive filter validity and reset behavior, legacy
BRUSH-V1 host decoding, 0x20/0x21 decoding, trail fade, NaNs, timestamp
repeats and disconnect gaps. Four firmware targets are syntax checked with
mock Arduino/NimBLE/Wi-Fi types using `g++ -Wall -Wextra -Werror`.

**Not yet verified:** real PlatformIO cross-compilation, BLE pairing/MTU
negotiation/throughput on the boards, Wi-Fi power save on the boards, or
real battery current draw. PlatformIO and downloadable toolchains are not
available in this build sandbox; the local mock test is not a hardware build.
Original cabled Hall tests sometimes achieved only ~80 frames/s and had
I2C dropouts; nominal 200 Hz is a scheduling target, not a measured result.

## Battery / magnetic precautions

Power the pen via a protected Li-ion cell with a **proper regulated 3.3 V**
rail and adequate pulse-current capability. Do not connect a full 4.2 V cell
to the ESP32-C3 3V3 pin. Isolate RF module, battery ferromagnetic parts and
switching regulators from TMAG5273; keep I2C leads short and mechanically
restrained. Place a current meter / battery analyzer in the *C3* supply to
verify averaged current (idle/streaming/failed RF), peak brownout conditions,
Hall RMS noise, frame gaps and actual throughput. At 200 Hz the node cannot
stay in deep sleep. BLE TX-power starts at -6 dBm to save power but is not
suitable for every antenna and operating range; tune after measurements.

## Suggested test sequence (no UART C3--S3 tests)

1. Power and flash both boards individually; wire only the two local sensors
   to C3. The two boards should **not** be wired together.
2. Flash matching BLE images and let S3 connect automatically. Attach only
   S3's native USB to the computer, open the original BRUSH-V1 Studio reader.
3. Check `hall_valid`, `imu_valid`, actual `frame_id` / timestamp spacing,
   BLE negotiated MTU (require >60 to fit one sample), 0x21 frame gaps and
   per-sensor retry counters. Move/flex the pen gently.
4. Compare battery idle and streaming currents and Hall noise before/after
   BLE bursts. Validate power supply and cable strain independently.
5. Only if BLE remains bottlenecked, flash the **matching** C3+S3 Wi-Fi
   backup images; re-check frame gaps, Hall noise and battery demand.

## Protocol details

See `docs/PROTOCOL.md`. All radio packets have application CRC16, a packet
sequence number, sample count and C3 timestamps. S3 validates before acting
on any data; lost samples are flagged, never silently interpolated.

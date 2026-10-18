# Radio link and Studio compatibility, v2.0

All wire integers are little-endian. The C3 sensor link uses these tightly
packed layouts from `include/ebrush_link.h`:

| Field / packet | Packed size | Notes |
|----------------|-------------|-------|
| Header | 12 bytes | magic 0xC3E8, version 1, type, sequence, length, sample count |
| Sensor sample | 46 bytes | frame id, C3 us32, status, drops, ages, Hall/IMU raw |
| Samples notification | 12 + 46 x N + 2 bytes | N=1..4, CRC16/CCITT-FALSE |
| Sensor metadata | 36 bytes | C3 reboot id, error and overflow counters, variant |
| Metadata packet | 50 bytes | always count=1 |

The packet CRC16 initial value is 0xffff, polynomial 0x1021; BLE Notify and
Wi-Fi UDP transport the exact same application packet. BLE uses ATT MTU 247
(target) to fit the 4-sample 198-byte packet in one Notify. It **must**
negotiate at least MTU 63 for a single sample; otherwise only META can pass.
The C3 drops old FIFO samples on disconnect and never replays old strokes.
The buffer is bounded to 24 frames. The S3 rejects packets with bad CRC,
unknown version/type, inconsistent length/count, and out-of-order timestamps.

The S3-to-host channel preserves existing `BrushProtocol` V1 COBS + CRC32
`DATA=1`, `HELLO=2`, `HEALTH=3`, `SYNC_RESP=0x11`. The S3 is connected to
the computer over its native USB CDC. We deliberately **did not** add UART
between boards. Original Hall/IMU values and flags remain unmodified.
Additional optional host-only V1 packet types:

* `0x20` `OptimizedPayload` (48 bytes): source frame id, filter flags,
  filtered Hall in mT[3], motion acceleration in g[3], motion energy,
  jerk g/s, confidence, missing-frame count.
* `0x21` `LinkHealthPayload` (32 bytes): received/failed radio packets,
  receiver queue drops, missing and duplicate source frames, C3 sender
  ring drops, USB drops, negotiated BLE MTU, link type and link-alive bit.

Host readers ignoring unknown packet types remain valid. In the Python
adapter, frame linking uses `frame_id`. Filtered Hall is *not* an externally
calibrated absolute pressure measurement. Relative acceleration is useful
for trails' intensity and stroke width, not absolute trajectory position.

### Radio reset handling

C3 `boot_id` metadata detects restarts. S3 resets its sample sequence, C3
clock epoch, quaternion and adaptive filter when the boot ID changes. Radio
packets received after a >5-second link gap also reset the time anchor.
The Studio trail explicitly resets after a long time gap, so disconnected
positions are never joined by an artificial streak.

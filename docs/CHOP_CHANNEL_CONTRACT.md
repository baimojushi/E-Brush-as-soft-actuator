# e-Brush → TouchDesigner CHOP Channel Contract

This document defines the **stable 17-channel interface** that the e-Brush firmware
exposes to TouchDesigner via the `serial_bridge` Script CHOP (see
`touchdesigner/script_chop_callbacks.py`).  The contract is versioned together
with the firmware; any change to the channel names, order, or semantics must be
reflected here and in the TD side.

## Channel Order (fixed)

| Index | Channel Name   | Unit / Meaning                                 | Notes |
|-------|----------------|-----------------------------------------------|-------|
| 0     | `bx_mT`        | Magnetic field X (milliTesla)                 | `NaN` if Hall variant unknown |
| 1     | `by_mT`        | Magnetic field Y (milliTesla)                 | `NaN` if Hall variant unknown |
| 2     | `bz_mT`        | Magnetic field Z (milliTesla)                 | `NaN` if Hall variant unknown |
| 3     | `ax_m_s2`      | Linear acceleration X (m/s²)                  | Includes gravity |
| 4     | `ay_m_s2`      | Linear acceleration Y (m/s²)                  | Includes gravity |
| 5     | `az_m_s2`      | Linear acceleration Z (m/s²)                  | Includes gravity |
| 6     | `gx_rad_s`     | Angular velocity X (rad/s)                    | |
| 7     | `gy_rad_s`     | Angular velocity Y (rad/s)                    | |
| 8     | `gz_rad_s`     | Angular velocity Z (rad/s)                    | |
| 9     | `q_w`          | Orientation quaternion w                      | Mahony 6-axis filter |
| 10    | `q_x`          | Orientation quaternion x                      | |
| 11    | `q_y`          | Orientation quaternion y                      | |
| 12    | `q_z`          | Orientation quaternion z                      | |
| 13    | `drop_flags`   | Bitmask of transient error flags              | See `drop_flag_names` below |
| 14    | `clock_synced` | 0 = free-running, 1 = host clock disciplined | |
| 15    | `sync_rtt_ms`  | Round-trip time (ms) between host and MCU     | `NaN` if unsynced |
| 16    | `align_dt_ms`  | Reserved for future depth↔brush alignment     | Currently always 0 |

## Bitmasks

### `drop_flags` (uint8)

| Bit | Name               | Meaning |
|-----|--------------------|---------|
| 0   | `sched_overrun`    | Main loop missed its deadline |
| 1   | `hall_read_fail`   | Hall sensor read I/O error |
| 2   | `imu_read_fail`    | IMU sensor read I/O error |
| 3   | `hall_stale`       | Hall data older than allowed |
| 4   | `imu_stale`        | IMU data older than allowed |
| 5   | `tx_backpressure`  | Host-side TX queue backpressure |

### `device_status_names` (derived from `device_status` uint16)

| Bit | Name               | Meaning |
|-----|--------------------|---------|
| 0   | `hall_present`     | Hall sensor responded to probe |
| 1   | `hall_valid`       | Hall data passes sanity checks |
| 2   | `hall_diag`        | Hall self-test passed |
| 3   | `hall_near_sat`    | Hall near saturation |
| 4   | `imu_present`      | IMU sensor responded to probe |
| 5   | `imu_valid`        | IMU data passes sanity checks |
| 6   | `imu_fresh`        | IMU data is fresh (not stale) |
| 7   | `pose_valid`       | Mahony filter output is valid |
| 8   | `pose_yaw_relative`| Yaw is relative (no absolute reference) |
| 9   | `i2c_recovered`    | I²C bus recovered from timeout |
| 10  | `spi_recovered`    | SPI bus recovered from timeout |
| 11  | `streaming`        | Main loop is actively streaming DATA frames |

## Usage in TouchDesigner

The `serial_bridge` Script CHOP (see `touchdesigner/script_chop_callbacks.py`)
writes these 17 channels into a Null CHOP named `serial_bridge/out1`.  Downstream
nodes can bind to them by expression, e.g.:

    op('serial_bridge/out1')['bx_mT']
    op('serial_bridge/out1')['ax_m_s2']

or by dragging the Null CHOP’s output onto a CHOP-to-DAT and inspecting the
columns.

## Version

This contract corresponds to e-Brush firmware **v1.1.0** (see
`platformio.ini: BRUSH_FW_VERSION = 1.1.0`).  Earlier firmware (v1.0.0) did
not provide a stable CHOP contract and should not be used with this document.
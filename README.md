# e-Brush

TouchDesigner integration for the e-Brush soft-actuator pen (ESP32-S3 +
TMAG5273 Hall sensor + IMU660RB / LSM6DSRTR).  This repo contains:

- `host/brush_serial.py`        — async COBS/CRC32 reader, v1 / v1.1 health
- `tools/auto_detect_port.py`    — keyword + active PING port discovery
- `tools/start_bridge.py`        — start the bridge, print live status
- `tools/td_print_chop.py`       — print the 17-channel CHOP contract
- `docs/CHOP_CHANNEL_CONTRACT.md`— the stable interface for the .toe

The e-Brush is a sub-module of the larger HP60C installation (RGB-D camera
+ 笔 detection + 板检测 + displace output).  This project supplies the
pen-only half of that pipeline and aligns its 200 Hz data stream into the
HP60C master project's 30 Hz depth tick via the `depth_aligner` Execute
DAT (see `touchdesigner/script_chop_callbacks.py` and the architecture
note below).

## Quick start

```bash
# 1. Make sure the ESP32-S3 is plugged in (CH343 driver, COM7 on this box)
# 2. List candidate ports
python tools/auto_detect_port.py --list

# 3. Stream 14 seconds of data into a TD-ready .dat file
python tools/td_print_chop.py --replay D:\BaiduNetdiskDownload\capture_v11.brlog \
    --chop-dat logs/replay_chop.dat

# 4. Live bridge (Ctrl-C to stop)
python tools/start_bridge.py --port COM7 --interval 2.0 \
    --raw-log logs/live.brlog
```

## Architecture

```
HP60C RGB-D (30Hz) ─▶ on_depth_frame(...) ─┐
                                           ├──▶ TemporalAligner (插值)
e-Brush 笔 (200Hz) ─▶ latest brush frame ─┘                   │
                                                               ▼
                                                17 channels per depth tick
                                                  (see CHOP_CHANNEL_CONTRACT.md)
```

The CHOP contract is the single source of truth for downstream nodes in
the .toe.  Names, units, bitmasks are documented in
`docs/CHOP_CHANNEL_CONTRACT.md` and the helper `frame_to_row` in
`tools/td_print_chop.py`.

## Firmware version

This tooling matches **e-Brush firmware v1.1.0** (TMAG5273 I2C scan at boot,
dynamic variant detection, health V1.1 schema with 88-byte payload).  See
`docs/CHOP_CHANNEL_CONTRACT.md` for the protocol level.

## License

TBD.
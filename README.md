# e-Brush

TouchDesigner integration for the e-Brush soft-actuator pen (ESP32-S3 +
TMAG5273 Hall sensor + IMU660RB / LSM6DSRTR).  This repo contains:

- `host/brush_serial.py`        — async COBS/CRC32 reader, v1 / v1.1 health
- `tools/auto_detect_port.py`    — keyword + active PING port discovery
- `tools/start_bridge.py`        — start the bridge, print live status
- `tools/td_print_chop.py`       — print the 17-channel CHOP contract
- `tools/check_td_imports.py`    — offline smoke test for the TD-side modules
- `touchdesigner/depth_aligner.py`         — 200 Hz brush → 30 Hz depth aligner
- `touchdesigner/script_chop_callbacks.py` — Script CHOP callbacks DAT
- `docs/CHOP_CHANNEL_CONTRACT.md`           — the stable interface for the .toe
- `docs/td_integration_architecture.md`     — how the contract is fulfilled at the .toe

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
`docs/CHOP_CHANNEL_CONTRACT.md` and the helper `project_frame_to_row` in
`touchdesigner/depth_aligner.py` (with the same content as
`tools/td_print_chop.py:frame_to_row`, kept in sync by hand).  The
host-side bridge adds two drop-flag bits (`bridge_stale_frame` = bit 6,
`bridge_no_frame` = bit 7) — see `docs/td_integration_architecture.md`
§5.

## One-time .toe integration (the artist)

```text
1.  Drop a Script CHOP named `serial_bridge` into your master project.
2.  On its Callbacks page, set the DAT to the absolute path of
    `touchdesigner/script_chop_callbacks.py`.  TouchDesigner will call
    onSetupParameters() and add Port / Baud / RawLog / AutoStart /
    Verbose / ResyncClock / DepthTick to the Custom page.
3.  Drop a Null CHOP named `out1` anywhere in the project.
4.  Wire `serial_bridge` (output 0) → `out1` (input 0).
5.  From the depth pipeline, pulse `op('serial_bridge').par.DepthTick`
    every time a new depth frame is processed (the 30 Hz render tick).
6.  Bind downstream nodes to `op('serial_bridge/out1')['bx_mT']`, etc.
```

Before opening the .toe in TD, sanity-check the TD-side modules load
without errors with `python tools/check_td_imports.py` — this catches
`sys.path` / relative-import issues before they bite inside TD.

## Firmware version

This tooling matches **e-Brush firmware v1.1.0** (TMAG5273 I2C scan at boot,
dynamic variant detection, health V1.1 schema with 88-byte payload).  See
`docs/CHOP_CHANNEL_CONTRACT.md` for the protocol level.

## License

TBD.
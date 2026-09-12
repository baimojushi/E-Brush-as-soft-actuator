# e-Brush → TouchDesigner Integration Architecture

This document describes the data path that takes the e-Brush 200 Hz DATA
stream and feeds it into the HP60C TouchDesigner master project at the
**30 Hz depth tick**.  The single source of truth for the channel contract
is `docs/CHOP_CHANNEL_CONTRACT.md`; this document is about *how* the
contract is fulfilled at the .toe boundary and the choices that drove the
design.

> **Audience:** the artist who owns the HP60C master project, the engineer
> who will field-troubleshoot, and a future agent that has to extend the
> pipeline.

## 1. The problem in one diagram

```
                              (a) 200 Hz DATA frames, COBS/CRC32, COM/USB
ESP32-S3 ──── serial ──────►  host/brush_serial.py
                              └─ bs.latest: Optional[dict] = most-recent frame
                                            bs.clock: 1PPS disciplining
                                            bs.drain(): all frames since last tick
                                              │
                                              │ (in-process, no socket)
                                              ▼
                              touchdesigner/script_chop_callbacks.py
                              ├─ onSetupParameters(parMenu)        — declared knobs
                              ├─ onCook(scriptOp)                    — fill 17 channels
                              └─ onPulse(parName)                   — depth tick trigger
                                            │
                                            │ Null CHOP "serial_bridge/out1"
                                            ▼
                              (b) 30 Hz depth tick
                              RGB-D camera (HP60C) ──► onDepthFrame(...)
                                            │
                                            │ depth_aligner.align(brush_latest, t_depth)
                                            ▼
                              touchdesigner/depth_aligner.py
                              └─ latest_aligned: 17-ch snapshot stamped at t_depth
                                            │
                                            ▼
                              HP60C displacement output
                              (TOX, 渲染层, 后续 displace / 笔触建模)
```

There are two independent clocks:
- (a) **e-Brush** runs at **200 Hz** in the firmware, decimated from the
  6.667 kHz Hall + 6.667 kHz IMU interrupt.  The serial link delivers a
  DATA frame roughly every 5 ms when streaming is on.
- (b) **RGB-D** runs at **30 Hz** in the HP60C master project.  Each
  depth frame is the **rendering tick** — visuals advance once per
  depth frame.

The bridge is the *temporal aligner*: at every depth tick it picks the
most recent brush frame (and a one-step holdover if the brush is stale),
attaches a `align_dt_ms` value (the gap between the brush's
`sample_host_ns` and the depth's `host_rx_ns`), and presents a stable
17-channel snapshot that the rest of the .toe can bind to.

## 2. What runs in TouchDesigner, what runs in the host

| Component | Where | Why |
|-----------|-------|-----|
| `host/brush_serial.py` | **host process** (the `serial_bridge` Script CHOP runs in TD's Python, so "host" = TD's Python interpreter) | Needs `pyserial` and a worker thread for the COBS/CRC32 state machine.  The TD interpreter exposes both. |
| `touchdesigner/depth_aligner.py` | TD Python (imported by the callbacks DAT) | Pure data, no I/O.  Deterministic given inputs.  Easy to unit-test by faking `bs.latest`. |
| `touchdesigner/script_chop_callbacks.py` | TD Script CHOP callbacks DAT | The only TD-shaped artifact: declares parameters, fills the 17 channels, owns the depth-tick `pulse` parameter. |
| `serial_bridge` Script CHOP | Live in the .toe | Hosts the callbacks DAT.  Its `out1` Null is what the rest of the .toe binds to. |
| `depth_tick` pulse on the Script CHOP | A button-style `pulse` parameter | Fired by the HP60C master's depth pipeline (`onDepthFrame` in HP60C) once per depth frame.  This is the alignment moment. |

The 17 channels are written **on every cook** (which is fine — CHOP
cooks are cheap), and the 30 Hz alignment snapshot is written **on every
`depth_tick` pulse** (which is when downstream nodes should re-evaluate).

## 3. Why a Script CHOP, not a Serial CHOP

`serialCHOP` in TD is fine for ASCII streams, but it does not own a
worker thread, does not implement COBS/CRC32, and does not have a
"latest" semantic — it produces samples at the device's rate.  A
Script CHOP lets us:

1. Run a background `BrushSerial` thread so the GUI stays responsive.
2. Decimate the 200 Hz stream to 30 Hz at the depth tick without losing
   frames in between.
3. Surface 17 stable, named channels in a Null CHOP
   (`serial_bridge/out1`) so downstream binding is by channel name
   instead of by index — survives firmware-side reorderings that
   preserve channel *names*.

The 17-channel contract is the **only** thing the .toe depends on.
A future change to a name, order, unit, or bitmask must update
`docs/CHOP_CHANNEL_CONTRACT.md` *and* `tools/td_print_chop.py`'s
`CHANNEL_ORDER` *and* the callbacks' `CHANNEL_NAMES` list in
`script_chop_callbacks.py`, in that order.

## 4. The depth-alignment algorithm

Pseudocode for the alignment step (see `depth_aligner.py` for the
real implementation):

```python
def align(bs, t_depth_host_ns):
    frame = bs.latest
    if frame is None:
        return stale_placeholder()  # all NaN, drop_flags |= 1<<6

    # align_dt_ms: positive = brush frame AHEAD of depth frame,
    #              negative = brush frame BEHIND.
    align_dt_ms = (frame["sample_host_ns"] - t_depth_host_ns) / 1e6

    if not bs.clock.synced:
        # Pre-discipline: trust the depth frame's host clock for the
        # brush timestamp proxy.  Drop clock_synced in the snapshot.
        pass

    if align_dt_ms > STALE_MS:
        # Brush has gone silent for too long.  Stale placeholder.
        return stale_placeholder(extra_drop_bit=7)

    snapshot = project_frame_to_row(frame)  # 17 floats, see CHANNEL_ORDER
    snapshot["align_dt_ms"] = align_dt_ms  # 16th index
    snapshot["stale"] = False
    return snapshot
```

`STALE_MS` defaults to **50 ms** — that's `30 Hz / 2`, i.e. one missed
depth frame.  If the brush is running at 200 Hz and we miss one frame,
`align_dt_ms` will still be ≤ 5 ms.  At 50 ms we know the bridge has
genuinely lost contact and downstream nodes should blank the visual
rather than animate against a stuck pose.

`align_dt_ms` is intentionally **not zero-centered**: the operator can
see the alignment skew and react to e.g. a 12 ms gap as "the brush is
running a little hot, USB latency is creeping up" — a useful early
warning before the link goes fully stale.

## 5. The drop_flag_names extension

The contract's `drop_flags` bitmask is **extended** by one bit by this
bridge, with the same wire-format byte (uint8) but a new bit-6/7
semantic *added at the .toe boundary*:

| Bit | Name (from firmware) | Notes |
|-----|----------------------|-------|
| 0–5 | (firmware bits, see CHOP_CHANNEL_CONTRACT.md) | Preserved verbatim. |
| **6** | `bridge_stale_frame` | Set by `depth_aligner` when the brush frame is older than `STALE_MS`. |
| **7** | `bridge_no_frame` | Set by `depth_aligner` when `bs.latest is None` at the depth tick (no DATA frame has ever arrived). |

Bits 6 and 7 are written by the *host* (TD) and **never** by the
firmware.  They live only on the Null CHOP `serial_bridge/out1` — the
firmware byte on the wire is unchanged.  The contract doc only
documents firmware bits 0–5, so a TD operator who only reads
`CHOP_CHANNEL_CONTRACT.md` will not be misled.

## 6. Failure modes and the operator's signals

| Symptom in the .toe | Likely cause | First thing to check |
|---------------------|--------------|----------------------|
| All 17 channels are `NaN` (or 0), drop_flags bit 7 lit, no movement for >1 s | `bs` thread crashed or never started | TD Textport: `[INFO] BrushSerial opening on ...` line.  If absent, the callbacks DAT's `onCook` did not reach `bs.start()`. |
| Channels animate, `clock_synced = 0`, `sync_rtt_ms = NaN` | Host clock discipline never locked | `bs.clock.synced` will be `False` for the first ~5 s of streaming, then should flip True after 3 successful 1PPS round-trips.  If it never does, check COM port and baud. |
| `bx_mT/by_mT/bz_mT` are NaN but other channels work | Hall variant unknown (firmware v1.0 or `HALL_INIT_ERROR != 0`) | `bs.latest_hello["hall_variant"]` and `bs.latest_health["hall_init_error_name"]`.  v1.1 firmware logs the I²C scan result on boot. |
| `drop_flags` bit 0 (`sched_overrun`) flickers above 1 Hz | Brush firmware's main loop is missing deadlines — usually IMU/Hall I²C contention | `bs.latest_health["device_status_names"]` — bit 9/10 (`i2c_recovered`/`spi_recovered`) will tell you which bus. |
| `align_dt_ms` steadily grows over minutes | USB latency creep or `ClockDiscipline` drift | `bs.clock` exposes the offset; the operator can pulse the `Resync Clock` button to force a fresh round-trip. |

## 7. One-time .toe setup (the artist)

```text
1.  Drop a Script CHOP named `serial_bridge` into your master project.
2.  In its parameters, set the "DAT" callback selector to point at
    `touchdesigner/script_chop_callbacks.py`.  (If you store your .toe
    externally via `toeexpand` / `toecollapse`, the relative path is
    `../touchdesigner/script_chop_callbacks.py`.)
3.  Drop a Null CHOP named `out1` anywhere in the project.
4.  Wire `serial_bridge` → `out1` (output 0 → input 0).
5.  From your depth pipeline, wire a `CHOP Execute` DAT that pulses
    `serial_bridge.par.DepthTick` every time a new depth frame is
    processed (this is the 30 Hz render tick).
6.  Bind downstream nodes to `op('serial_bridge/out1')['bx_mT']` etc.
```

The depth pipeline must pulse `DepthTick` for the alignment snapshot to
update.  The 17 channels are always present on the output Null (set on
every cook from `bs.latest`), but `align_dt_ms` and the bridge's
drop-flag bits are only refreshed at the depth tick.

## 8. Versioning

This document and the files it describes (`script_chop_callbacks.py`,
`depth_aligner.py`, `CHOP_CHANNEL_CONTRACT.md`, `td_print_chop.py`) all
target **e-Brush firmware v1.1.0** (see `platformio.ini: BRUSH_FW_VERSION`).
Earlier firmware v1.0.0 will not work — v1.0 did not provide the
`latest_health` payload that this bridge needs to detect the Hall
variant correctly.

When a firmware minor-version bump changes a channel name, units, or
the DATA struct layout, the update order is:
1. `host/brush_serial.py` — adapt the struct unpacking.
2. `docs/CHOP_CHANNEL_CONTRACT.md` — document the new contract.
3. `tools/td_print_chop.py` — update `CHANNEL_ORDER`.
4. `touchdesigner/script_chop_callbacks.py` — update `CHANNEL_NAMES`.
5. `touchdesigner/depth_aligner.py` — if unit/scale changes, update
   the float-cast sites.
6. This document — bump the version line.

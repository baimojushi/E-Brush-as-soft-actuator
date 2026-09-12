"""
Script CHOP callbacks for the e-Brush serial bridge in TouchDesigner.

HOW THIS FITS INTO TOUCHDESIGNER
================================
This file is a **callbacks DAT** — it gets loaded as the `Callbacks` parameter
of a `serial_bridge` Script CHOP.  The artist wires the Script CHOP's
`Callbacks` par to the absolute path of this file (e.g.
`<project>/touchdesigner/script_chop_callbacks.py`).  TouchDesigner then
calls the exported functions below at the right moments.

The seven custom parameters declared in `onSetupParameters` appear on the
Script CHOP's `Custom` page (TD's parMenu mechanism).  They are
preserved across re-cooks of this DAT as long as the same names stay
declared, so downstream UI references (e.g. another Script CHOP
binding to `op('serial_bridge').par.DepthTick`) survive edits.

PUBLIC CALLBACKS (TD looks for these by name)
=============================================
  onSetupParameters(scriptOp) -> list[tuple]
      Declared parameters: Port, Baud, RawLog, AutoStart, Verbose,
      ResyncClock (pulse), DepthTick (pulse).  See `parMenu` below for
      the (name, default, help) tuple shape.

  onCook(scriptOp)
      Per-cook: pull the latest DATA frame from the BrushSerial worker
      thread and write the 17 contract channels into the CHOP.  Cheap
      — called on every cook regardless of source rate.  The 30 Hz
      depth alignment happens in onPulse(DepthTick) and overwrites
      the channels with a depth-time-stamped snapshot.

  onPulse(parName, scriptOp)
      Two custom pulses:
        DepthTick   — fired by the HP60C depth pipeline at every depth
                      frame (30 Hz).  Picks the freshest brush frame
                      (or a stale placeholder), stamps `align_dt_ms`,
                      possibly sets host-side drop bits, writes the 17
                      channels.
        ResyncClock — ask the brush for a fresh 1PPS host-clock
                      round-trip.

  onDestroy(scriptOp)
      Stop the BrushSerial cleanly: send STOP, join the worker thread,
      close the COM port.  Avoids leaving the port held when the .toe
      closes or the operator is deleted.

CHANNEL CONTRACT
================
The 17-channel contract is the single source of truth for downstream
nodes.  If you change it, update CHANNEL_NAMES here, CHANNEL_ORDER in
tools/td_print_chop.py, and docs/CHOP_CHANNEL_CONTRACT.md in lockstep.
"""

from __future__ import annotations

import math
import os
import sys
import time
from typing import List, Optional

# ---------------------------------------------------------------------------
# Imports of the e-Brush host package.  When the .toe is stored next to
# the e-Brush repo (typical layout), the relative path from this DAT
# <repo>/touchdesigner/script_chop_callbacks.py back to <repo>/host/ is
# .. / host.  We also probe a few conventional locations so this DAT
# works whether the .toe was launched as a single .toe file, an
# externalized .toe.dir, or a saved-to-folder build.
# ---------------------------------------------------------------------------

_THIS_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else None
_CANDIDATES: List[Optional[str]] = [
    _THIS_DIR,
    os.path.normpath(os.path.join(_THIS_DIR, "..")) if _THIS_DIR else None,
    os.path.normpath(os.path.join(_THIS_DIR, "..", "..")) if _THIS_DIR else None,
    os.getcwd(),
    os.path.dirname(os.getcwd()) if os.getcwd() else None,
]
for _cand in _CANDIDATES:
    if _cand and _cand not in sys.path and os.path.isdir(os.path.join(_cand, "host")):
        sys.path.insert(0, _cand)

try:
    from host import brush_serial          # type: ignore
except Exception as _exc:  # pragma: no cover - exercised in tools/check_td_imports.py
    # Don't crash the operator at module load; surface a friendly
    # error in onCook.  Many TD cooks fire before onSetupParameters,
    # and a bad import should not blank the project.
    brush_serial = None        # type: ignore
    _IMPORT_ERROR: Optional[Exception] = _exc
else:
    _IMPORT_ERROR = None

# `auto_detect_port` lives in tools/, not host/.  Add the repo root
# to sys.path (the parent of touchdesigner/) and import it as
# `tools.auto_detect_port` so the import path matches the canonical
# repo layout.  E_BRUSH_ROOT is not bound here because it lives in
# the host probe above; recompute locally for the tools import.
if _THIS_DIR:
    _repo_root = os.path.normpath(os.path.join(_THIS_DIR, ".."))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
try:
    from tools import auto_detect_port  # type: ignore
except Exception as _exc:  # pragma: no cover
    auto_detect_port = None  # type: ignore
    _AUTO_DETECT_IMPORT_ERROR: Optional[Exception] = _exc
else:
    _AUTO_DETECT_IMPORT_ERROR = None

# depth_aligner is part of the same touchdesigner/ directory.  Try the
# package import first; fall back to a same-directory import for TD
# builds that don't put the parent dir on sys.path as a package.
depth_aligner = None  # type: ignore
_ALIGNER_ERROR: Optional[Exception] = None
try:
    from touchdesigner import depth_aligner as _da  # type: ignore
    depth_aligner = _da
except Exception:
    try:
        import depth_aligner as _da  # type: ignore
        depth_aligner = _da
    except Exception as _exc:
        _ALIGNER_ERROR = _exc


# Channel contract — fixed order.  See docs/CHOP_CHANNEL_CONTRACT.md.
# MUST stay in sync with tools/td_print_chop.py:CHANNEL_ORDER and
# touchdesigner/depth_aligner.py:CHANNEL_NAMES.
CHANNEL_NAMES: List[str] = [
    "bx_mT", "by_mT", "bz_mT",
    "ax_m_s2", "ay_m_s2", "az_m_s2",
    "gx_rad_s", "gy_rad_s", "gz_rad_s",
    "q_w", "q_x", "q_y", "q_z",
    "drop_flags",
    "clock_synced",
    "sync_rtt_ms",
    "align_dt_ms",
]
assert len(CHANNEL_NAMES) == 17, "CHANNEL_NAMES must remain 17 entries."


# ---------------------------------------------------------------------------
# Bridge lifecycle — the BrushSerial instance lives on the Script CHOP's
# storage (parent().storage) so it survives a re-cook of this DAT and is
# shared with onDestroy.
# ---------------------------------------------------------------------------

_BRIDGE_KEY = "brush_serial"
_last_drop_reasons: dict = {}  # keyed by Script CHOP path


def _get_bridge(scriptOp):
    parent = scriptOp.parent()
    if parent is None:
        return None
    store = parent.storage if hasattr(parent, "storage") else None
    if store is None:
        return None
    return store.get(_BRIDGE_KEY)


def _set_bridge(scriptOp, bs):
    parent = scriptOp.parent()
    if parent is None:
        return
    store = parent.storage if hasattr(parent, "storage") else None
    if store is None:
        return
    store[_BRIDGE_KEY] = bs


def _verbose(scriptOp) -> bool:
    try:
        return bool(int(scriptOp.par.Verbose.eval()))
    except Exception:
        return False


def _start_bridge(scriptOp, *, port: Optional[str], baud: int,
                  raw_log: Optional[str]) -> "Optional[object]":
    if brush_serial is None or auto_detect_port is None:
        print("[serial_bridge] host.brush_serial not importable; bridge not started.")
        print(f"  reason: {_IMPORT_ERROR}")
        return None
    if _get_bridge(scriptOp) is not None:
        return _get_bridge(scriptOp)

    resolved = auto_detect_port.auto_detect_port(
        preferred_port=port, baud=baud, verbose=_verbose(scriptOp),
    )
    if _verbose(scriptOp):
        print(f"[serial_bridge] opening {resolved} @ {baud}")
    bs = brush_serial.BrushSerial(resolved, baud=baud, raw_log_path=raw_log)
    bs.start()
    _set_bridge(scriptOp, bs)
    if _verbose(scriptOp):
        print("[serial_bridge] streaming started")
    return bs


def _stop_bridge(scriptOp):
    bs = _get_bridge(scriptOp)
    if bs is None:
        return
    try:
        bs.stop_stream()
    except Exception:
        pass
    try:
        bs.close()
    except Exception:
        pass
    _set_bridge(scriptOp, None)


# ---------------------------------------------------------------------------
# Per-cook writer
# ---------------------------------------------------------------------------

def _ensure_channels(scriptOp):
    """Make sure the CHOP has the 17 contract channels.  Idempotent —
    re-asserts on every cook so a contract-channel addition picks up
    without restarting the project."""
    existing = {c.name for c in scriptOp.chans}
    for name in CHANNEL_NAMES:
        if name not in existing:
            scriptOp.appendChan(name)


def _write_row(scriptOp, row: List[float]):
    """Write 17 floats into the CHOP.  Always one sample on every write
    so downstream bindings see a single current value, not a stale
    rolling buffer."""
    _ensure_channels(scriptOp)
    n = min(len(row), len(CHANNEL_NAMES))
    for i in range(n):
        name = CHANNEL_NAMES[i]
        v = row[i]
        if v is None or (isinstance(v, float) and math.isnan(v)):
            # NaN: the cleanest "no data" signal; downstream bindings
            # can detect it without us picking a magic zero.
            scriptOp[name] = [float("nan")]
        else:
            scriptOp[name] = [float(v)]


# ---------------------------------------------------------------------------
# Public TD callbacks
# ---------------------------------------------------------------------------

def onSetupParameters(scriptOp) -> list:
    """Parameter menu.  TouchDesigner calls this whenever the DAT
    re-cooks; the returned list of (name, default, help) tuples becomes
    the Script CHOP's Custom page.

    The first cook of the DAT creates the parMenu; subsequent cooks
    must return the same set of names so TD doesn't drop existing
    references.  Renames are tracked in docs/td_integration_architecture.md.
    """
    return [
        ("Port",        "",       "Preferred COM port (e.g. COM7). Auto-detects if blank."),
        ("Baud",        921600,   "Serial baud rate. Default 921600."),
        ("RawLog",      "",       "Optional .brlog path; the wire stream is also written here."),
        ("AutoStart",   True,     "Open the serial port and start streaming on first cook."),
        ("Verbose",     True,     "Print [serial_bridge] status messages to the Textport."),
        ("ResyncClock", 0,        "Pulse to force a fresh 1PPS host-clock round-trip."),
        ("DepthTick",   0,        "Pulse from the depth pipeline at every depth frame (30 Hz)."),
    ]


def onCook(scriptOp):
    """Per-cook: pull the latest frame and write the 17 channels.

    The brush worker thread is constantly updating `bs.latest`.  We
    sample it at whatever cook rate the CHOP has.  The 30 Hz depth
    alignment happens in onPulse(DepthTick) — that overwrites the
    channels with a depth-time-stamped snapshot.  The row here is a
    best-effort 'whatever the brush had at the cook moment'.
    """
    if brush_serial is None or depth_aligner is None:
        if int(time.monotonic() * 2) % 2 == 0:
            print(f"[serial_bridge] onCook: import missing ({_IMPORT_ERROR or _ALIGNER_ERROR}); skipping.")
        _write_row(scriptOp, [float("nan")] * 17)
        return

    if bool(int(scriptOp.par.AutoStart.eval())) and _get_bridge(scriptOp) is None:
        port = str(scriptOp.par.Port.eval()).strip() or None
        baud = int(scriptOp.par.Baud.eval())
        raw_log = str(scriptOp.par.RawLog.eval()).strip() or None
        _start_bridge(scriptOp, port=port, baud=baud, raw_log=raw_log)

    bs = _get_bridge(scriptOp)
    if bs is None or bs.latest is None:
        _write_row(scriptOp, [float("nan")] * 17)
        return

    # Best-effort row from the latest frame; align_dt_ms=0 here because
    # at the cook rate we have no depth timestamp to compare against.
    # The depth-aligned overwrite happens in onPulse(DepthTick).
    row = depth_aligner.project_frame_to_row(dict(bs.latest, align_dt_ms=0.0))
    _write_row(scriptOp, row)


def onPulse(parName, scriptOp):
    """Handle custom pulses.  We compare case-insensitively because
    TouchDesigner's parameter name matching can be case-flipped across
    builds and Python attribute lookup.  The Custom page names we
    declared are 'DepthTick' and 'ResyncClock'."""
    bs = _get_bridge(scriptOp)
    if bs is None:
        return
    pn = (parName or "").lower()
    if pn == "depthtick":
        if depth_aligner is None:
            return
        snap = depth_aligner.align(bs, time.perf_counter_ns())
        _write_row(scriptOp, snap["row"])
        _log_transition(scriptOp, snap)
        return
    if pn == "resyncclock":
        try:
            bs.send_sync()
            if _verbose(scriptOp):
                print("[serial_bridge] sent PT_CMD_SYNC; awaiting host-clock round-trip")
        except Exception as exc:
            print(f"[serial_bridge] ResyncClock failed: {exc}")
        return


def _log_transition(scriptOp, snap):
    """Print the first state-transition of each drop_reason.  Avoids
    30 Hz Textport spam while still telling the operator when the
    bridge went stale or lost the link."""
    global _last_drop_reasons
    parent = scriptOp.parent()
    key = parent.path if parent is not None else "serial_bridge"
    prev = _last_drop_reasons.get(key, 0)
    if snap["drop_reason"] != prev:
        _last_drop_reasons[key] = snap["drop_reason"]
        if snap["drop_reason"] & depth_aligner.DROP_BRIDGE_NO_FRAME:
            print("[serial_bridge] DepthTick: no DATA frame has ever arrived from the brush.")
        elif snap["drop_reason"] & depth_aligner.DROP_BRIDGE_STALE:
            align_dt_ms = snap.get("align_dt_ms", float("nan"))
            fid = snap.get("raw_frame_id", "?")
            print(f"[serial_bridge] DepthTick: STALE frame_id={fid} align_dt_ms={align_dt_ms:.1f}")


def onDestroy(scriptOp):
    """Operator is being deleted or the project is closing — stop the
    brush thread cleanly so the COM port is released."""
    _stop_bridge(scriptOp)

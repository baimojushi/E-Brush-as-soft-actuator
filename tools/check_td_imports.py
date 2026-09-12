#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Offline smoke test for the e-Brush TouchDesigner-side modules.

Run this from the repo root **before** opening the .toe, so any
sys.path / relative-import / API-mismatch errors show up in plain
Python (no TouchDesigner required):

    python tools/check_td_imports.py

What it checks
--------------
1. `host.brush_serial` and `host.auto_detect_port` import cleanly from
   the e-Brush repo root.
2. `touchdesigner.depth_aligner` and `touchdesigner.script_chop_callbacks`
   import cleanly (the same imports the callbacks DAT will perform at
   cook time inside TD).
3. The 17-channel contract is identical between the two halves:
   `tools/td_print_chop.py:CHANNEL_ORDER` and
   `touchdesigner/depth_aligner.py:CHANNEL_NAMES`.
4. `depth_aligner.align` is deterministic for a stub bs (no live
   serial needed): a fake `bs` with a fixed `latest` dict and a
   `clock.synced=True` flag returns a 17-row snapshot with the
   expected `align_dt_ms` sign and NaN handling for missing fields.
5. `script_chop_callbacks` exports the four callback names TD looks
   for: onCook, onPulse, onDestroy, onSetupParameters.

Exit code 0 = all green, 1 = at least one check failed.

Note
----
This script imports the e-Brush host/ package the same way the
callbacks DAT does.  The path probe in
`script_chop_callbacks.py` is the same probe, so a green run here
strongly predicts a green load inside TD.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import types
from typing import List, Tuple

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
E_BRUSH_ROOT = os.path.normpath(os.path.join(THIS_DIR, ".."))

# Make host/ importable.  Same as the DAT's probe.
if E_BRUSH_ROOT not in sys.path:
    sys.path.insert(0, E_BRUSH_ROOT)


def _section(name: str) -> None:
    print(f"\n=== {name} ===")


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}", file=sys.stderr)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    if ok:
        _ok(f"{name}{(' — ' + detail) if detail else ''}")
    else:
        _fail(f"{name}{(' — ' + detail) if detail else ''}")
    return ok


def check_host_imports() -> List[str]:
    """host.brush_serial + host.auto_detect_port must import cleanly."""
    failures: List[str] = []
    _section("host/ imports")
    try:
        import host.brush_serial as bs  # type: ignore
    except Exception as exc:
        failures.append(f"import host.brush_serial: {exc!r}")
        _fail(f"import host.brush_serial: {exc!r}")
        return failures
    if not _check("host.brush_serial.BrushSerial", hasattr(bs, "BrushSerial")):
        failures.append("host.brush_serial.BrushSerial missing")

    # auto_detect_port lives in tools/, not host/.  We probe the same
    # sys.path setup the callbacks DAT uses.
    try:
        import tools.auto_detect_port as adp  # type: ignore
    except Exception as exc:
        failures.append(f"import tools.auto_detect_port: {exc!r}")
        _fail(f"import tools.auto_detect_port: {exc!r}")
        return failures
    if not _check("tools.auto_detect_port.auto_detect_port", hasattr(adp, "auto_detect_port")):
        failures.append("tools.auto_detect_port.auto_detect_port missing")
    return failures


def check_td_imports() -> List[str]:
    """touchdesigner.depth_aligner + script_chop_callbacks must import."""
    failures: List[str] = []
    _section("touchdesigner/ imports")
    try:
        import touchdesigner.depth_aligner as da  # type: ignore
    except Exception as exc:
        failures.append(f"import touchdesigner.depth_aligner: {exc!r}")
        _fail(f"import touchdesigner.depth_aligner: {exc!r}")
        return failures
    _check("depth_aligner.CHANNEL_NAMES", isinstance(getattr(da, "CHANNEL_NAMES", None), list))
    _check("depth_aligner.CHANNEL_NAMES length == 17", len(da.CHANNEL_NAMES) == 17,
           detail=f"len={len(da.CHANNEL_NAMES)}")
    _check("depth_aligner.align callable", callable(getattr(da, "align", None)))
    _check("depth_aligner.project_frame_to_row callable", callable(getattr(da, "project_frame_to_row", None)))
    _check("depth_aligner.DROP_BRIDGE_NO_FRAME == 1<<7", da.DROP_BRIDGE_NO_FRAME == 1 << 7)
    _check("depth_aligner.DROP_BRIDGE_STALE    == 1<<6", da.DROP_BRIDGE_STALE == 1 << 6)

    try:
        import touchdesigner.script_chop_callbacks as cb  # type: ignore
    except Exception as exc:
        failures.append(f"import touchdesigner.script_chop_callbacks: {exc!r}")
        _fail(f"import touchdesigner.script_chop_callbacks: {exc!r}")
        return failures
    for fname in ("onCook", "onPulse", "onDestroy", "onSetupParameters"):
        if not _check(f"callbacks.{fname} callable", callable(getattr(cb, fname, None))):
            failures.append(f"callbacks.{fname} not callable")
    _check("callbacks.CHANNEL_NAMES length == 17", len(cb.CHANNEL_NAMES) == 17,
           detail=f"len={len(cb.CHANNEL_NAMES)}")
    return failures


def check_contract_consistency() -> List[str]:
    """tools/td_print_chop.py:CHANNEL_ORDER and
    touchdesigner/depth_aligner.py:CHANNEL_NAMES must agree."""
    failures: List[str] = []
    _section("17-channel contract consistency")
    try:
        import tools.td_print_chop as pc  # type: ignore
    except Exception as exc:
        failures.append(f"import tools.td_print_chop: {exc!r}")
        _fail(f"import tools.td_print_chop: {exc!r}")
        return failures
    try:
        import touchdesigner.depth_aligner as da  # type: ignore
    except Exception as exc:
        failures.append(f"import touchdesigner.depth_aligner: {exc!r}")
        _fail(f"import touchdesigner.depth_aligner: {exc!r}")
        return failures

    expected = pc.CHANNEL_ORDER
    actual = da.CHANNEL_NAMES
    if not _check("CHANNEL_ORDER == CHANNEL_NAMES", expected == actual,
                  detail=f"td_print_chop={expected} depth_aligner={actual}"):
        failures.append("CHANNEL_ORDER != CHANNEL_NAMES")
    return failures


class _StubBrush:
    """Minimal stand-in for brush_serial.BrushSerial for align() tests.

    Mirrors the surface depth_aligner reads: .latest (Optional[dict]) and
    .clock.synced (bool).  No I/O, no thread.
    """
    def __init__(self, latest=None, synced=True):
        self.latest = latest
        self.clock = types.SimpleNamespace(synced=synced, last_rtt_ns=float("nan"))


def _sample_frame(t_host_ns: int) -> dict:
    """A self-consistent 17-field frame stamped at t_host_ns."""
    return {
        "frame_id": 42,
        "bx_mT": 1.234, "by_mT": -0.5, "bz_mT": 0.125,
        "ax_m_s2": 0.01, "ay_m_s2": -0.02, "az_m_s2": 9.81,
        "gx_rad_s": 0.0, "gy_rad_s": 0.0, "gz_rad_s": 0.0,
        "q_w": 1.0, "q_x": 0.0, "q_y": 0.0, "q_z": 0.0,
        "drop_flags": 0,
        "clock_synced": True,
        "sync_rtt_ms": 1.5,
        "sample_host_ns": t_host_ns,
    }


def check_align_deterministic() -> List[str]:
    """depth_aligner.align must be deterministic given a stub bs."""
    failures: List[str] = []
    _section("depth_aligner.align determinism (stub bs)")
    try:
        import touchdesigner.depth_aligner as da  # type: ignore
    except Exception as exc:
        failures.append(f"import touchdesigner.depth_aligner: {exc!r}")
        _fail(f"import touchdesigner.depth_aligner: {exc!r}")
        return failures

    # Case A: depth frame 5 ms after the brush — align_dt_ms should be -5
    t_depth = 1_000_000_000
    t_brush = t_depth - 5_000_000  # 5 ms earlier
    bs = _StubBrush(latest=_sample_frame(t_brush), synced=True)
    snap = da.align(bs, t_depth)
    if not _check("case A: not stale", snap["stale"] is False, detail=f"stale={snap['stale']}"):
        failures.append("case A expected not stale")
    if not _check("case A: drop_reason == 0", snap["drop_reason"] == 0,
                  detail=f"drop_reason={snap['drop_reason']}"):
        failures.append("case A expected drop_reason == 0")
    if not _check("case A: align_dt_ms ~ -5", abs(snap["align_dt_ms"] - (-5.0)) < 1e-6,
                  detail=f"align_dt_ms={snap['align_dt_ms']}"):
        failures.append("case A expected align_dt_ms ~= -5")
    if not _check("case A: row length == 17", len(snap["row"]) == 17,
                  detail=f"row_len={len(snap['row'])}"):
        failures.append("case A expected 17-row")
    if not _check("case A: bx_mT == 1.234", snap["row"][0] == 1.234,
                  detail=f"row[0]={snap['row'][0]}"):
        failures.append("case A expected bx_mT=1.234")

    # Case B: brush is 100 ms older than depth frame — should be STALE
    t_brush_late = t_depth - 100_000_000
    bs2 = _StubBrush(latest=_sample_frame(t_brush_late), synced=True)
    snap2 = da.align(bs2, t_depth)
    if not _check("case B: stale=True", snap2["stale"] is True):
        failures.append("case B expected stale")
    if not _check("case B: drop_reason has STALE bit",
                  bool(snap2["drop_reason"] & da.DROP_BRIDGE_STALE)):
        failures.append("case B expected DROP_BRIDGE_STALE set")
    # When stale, all non-drop channel values should be NaN (placeholder)
    if not _check("case B: bx_mT NaN when stale",
                  isinstance(snap2["row"][0], float) and math.isnan(snap2["row"][0]),
                  detail=f"row[0]={snap2['row'][0]!r}"):
        failures.append("case B expected bx_mT NaN")

    # Case C: no frame at all — DROP_BRIDGE_NO_FRAME
    bs3 = _StubBrush(latest=None, synced=False)
    snap3 = da.align(bs3, t_depth)
    if not _check("case C: stale=True (no frame)", snap3["stale"] is True):
        failures.append("case C expected stale")
    if not _check("case C: drop_reason has NO_FRAME bit",
                  bool(snap3["drop_reason"] & da.DROP_BRIDGE_NO_FRAME)):
        failures.append("case C expected DROP_BRIDGE_NO_FRAME set")
    if not _check("case C: align_dt_ms NaN", math.isnan(snap3["align_dt_ms"]),
                  detail=f"align_dt_ms={snap3['align_dt_ms']}"):
        failures.append("case C expected align_dt_ms NaN")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline smoke test for the e-Brush TD modules.")
    args = parser.parse_args()

    print(f"check_td_imports — repo root: {E_BRUSH_ROOT}")

    failures: List[str] = []
    failures += check_host_imports()
    failures += check_td_imports()
    failures += check_contract_consistency()
    failures += check_align_deterministic()

    _section("summary")
    if failures:
        print(f"  {len(failures)} check(s) failed:")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("  all green — safe to open the .toe")
    return 0


if __name__ == "__main__":
    sys.exit(main())

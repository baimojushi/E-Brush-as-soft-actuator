"""depth_aligner: align the e-Brush 200 Hz DATA stream to the HP60C 30 Hz
depth tick.

The TD Script CHOP callbacks (`touchdesigner/script_chop_callbacks.py`)
hold the live `brush_serial.BrushSerial` instance in `mod('serial_bridge').store`.
At every depth tick (a `pulse` on the `DepthTick` parameter) the callbacks
call `align(bs, t_depth_host_ns)`.  This module is a pure function: given
the BrushSerial + the host-clock timestamp of the depth frame, it returns
a stable 17-channel snapshot that downstream nodes can bind to.

Why a separate module?  It is deterministic given inputs and has no TD
or serial I/O of its own, so:

  * It can be unit-tested in plain Python (see tools/check_td_imports.py).
  * The same code can later be ported to a non-TD scheduler if needed.
  * The callbacks file stays small and TD-shaped.

The 17 channel names and the wire-vs-host drop_flags extension are
documented in `docs/CHOP_CHANNEL_CONTRACT.md` and
`docs/td_integration_architecture.md` (§5).  In short: the host side
sets bits 6 (`bridge_stale_frame`) and 7 (`bridge_no_frame`) on the
output Null only — the firmware byte is passed through untouched.
"""

from __future__ import annotations

import math
from typing import List, Optional

# Channel contract — fixed order.  See docs/CHOP_CHANNEL_CONTRACT.md.
# MUST stay in sync with tools/td_print_chop.py:CHANNEL_ORDER.
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

# Host-side extension to the firmware drop_flags byte.  Bits 0-5 are the
# firmware's own flags (see contract doc).  Bits 6-7 are set by this
# bridge; the firmware byte on the wire is never modified.
DROP_BRIDGE_NO_FRAME = 1 << 7   # bs.latest is None — no DATA ever arrived
DROP_BRIDGE_STALE    = 1 << 6   # latest frame is older than STALE_NS

# Default staleness window.  At 30 Hz, a missed depth frame is ~33 ms;
# STALE_NS=50ms catches one missed frame and a touch of USB jitter
# without false-staling on a single missing brush frame.
DEFAULT_STALE_NS = 50_000_000  # 50 ms in nanoseconds


def _finite_or_nan(value, default=float("nan")):
    """Return float(value) if it is finite, else default (NaN by default)."""
    if value is None:
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def project_frame_to_row(frame: dict) -> List[float]:
    """Project a BrushSerial._decode_data dict to the 17-channel row.

    Mirrors tools/td_print_chop.py:frame_to_row.  Kept in sync by hand;
    if you change one, change the other.  The two are deliberately not
    sharing an import because `td_print_chop.py` is the artist-facing
    helper (CLI) and `depth_aligner.py` runs inside TD — pulling CLI
    imports into TD's Python is not free.
    """
    bx = _finite_or_nan(frame.get("bx_mT"))
    by = _finite_or_nan(frame.get("by_mT"))
    bz = _finite_or_nan(frame.get("bz_mT"))
    ax = _finite_or_nan(frame.get("ax_m_s2"))
    ay = _finite_or_nan(frame.get("ay_m_s2"))
    az = _finite_or_nan(frame.get("az_m_s2"))
    gx = _finite_or_nan(frame.get("gx_rad_s"))
    gy = _finite_or_nan(frame.get("gy_rad_s"))
    gz = _finite_or_nan(frame.get("gz_rad_s"))
    qw = _finite_or_nan(frame.get("q_w"))
    qx = _finite_or_nan(frame.get("q_x"))
    qy = _finite_or_nan(frame.get("q_y"))
    qz = _finite_or_nan(frame.get("q_z"))
    drop = _finite_or_nan(frame.get("drop_flags"), default=0.0)
    clock_synced = 1.0 if frame.get("clock_synced") else 0.0
    rtt = _finite_or_nan(frame.get("sync_rtt_ms"))
    align_dt = _finite_or_nan(frame.get("align_dt_ms"), default=0.0)
    return [
        bx, by, bz,
        ax, ay, az,
        gx, gy, gz,
        qw, qx, qy, qz,
        drop,
        clock_synced,
        rtt,
        align_dt,
    ]


def _stale_row(reason_bit: int) -> List[float]:
    """Return a 17-channel all-NaN row that carries a single bridge drop bit.

    Used when the brush stream has gone silent — downstream nodes should
    blank the visual rather than animate against a stuck pose.
    """
    nan = float("nan")
    return [
        nan, nan, nan,    # bx, by, bz
        nan, nan, nan,    # ax, ay, az
        nan, nan, nan,    # gx, gy, gz
        nan, nan, nan, nan,  # q_w, q_x, q_y, q_z
        float(reason_bit),  # drop_flags
        0.0,                # clock_synced = 0
        nan,                # sync_rtt_ms
        nan,                # align_dt_ms
    ]


def align(bs, t_depth_host_ns: int, *, stale_ns: int = DEFAULT_STALE_NS):
    """Compute the 17-channel snapshot for one depth tick.

    Parameters
    ----------
    bs : brush_serial.BrushSerial
        The live serial bridge.  Must expose `.latest` (Optional[dict]) and
        `.clock.synced` (bool).  The bridge holds the worker thread; this
        function only reads.
    t_depth_host_ns : int
        Host-clock nanosecond timestamp of the depth frame that just
        arrived.  Pass e.g. `time.perf_counter_ns()` measured in the
        callbacks DAT at the moment the depth tick fires.
    stale_ns : int
        Threshold in nanoseconds; if the brush's `sample_host_ns` is
        older than this relative to `t_depth_host_ns`, return a stale
        placeholder.  Default 50 ms.

    Returns
    -------
    dict
        Keys:
            row          : List[float] of 17 floats (see CHANNEL_NAMES)
            stale        : bool
            drop_reason  : int   — 0 if fine, else DROP_BRIDGE_NO_FRAME or
                                   DROP_BRIDGE_STALE
            align_dt_ms  : float — gap between brush sample and depth frame
            raw_frame_id : Optional[int] — useful for debug logging

    The function does not mutate the brush_serial object.
    """
    frame = getattr(bs, "latest", None)
    if frame is None:
        return {
            "row": _stale_row(DROP_BRIDGE_NO_FRAME),
            "stale": True,
            "drop_reason": DROP_BRIDGE_NO_FRAME,
            "align_dt_ms": float("nan"),
            "raw_frame_id": None,
        }

    sample_ns = frame.get("sample_host_ns") or 0
    align_dt_ms = (sample_ns - int(t_depth_host_ns)) / 1e6
    # align_dt_ms is positive when the brush frame is AHEAD of the
    # depth frame, and negative when it is BEHIND (i.e. the brush
    # is older than the depth tick).  Stale means the brush is too
    # far BEHIND the depth frame, so the staleness test is on the
    # negative side: depth's host clock has moved past the brush
    # sample by more than stale_ns.

    # Use a copy so we can attach align_dt_ms without mutating the dict
    # the brush_serial thread also writes to.  brush_serial puts the
    # _decode_data dict in bs.latest; the next DATA packet replaces it.
    stamped = dict(frame)
    stamped["align_dt_ms"] = align_dt_ms

    if -align_dt_ms * 1e6 > stale_ns:
        # Brush frame is older than the staleness window relative to
        # this depth tick.  We return an all-NaN placeholder carrying
        # both the host-side STALE bit AND the firmware's own drop_flags
        # so the operator can still see firmware-side errors (e.g.
        # `sched_overrun`).  Downstream nodes should blank the visual
        # rather than animate against a stuck pose — see
        # docs/td_integration_architecture.md §5.
        row = project_frame_to_row(stamped)
        wire_drop = int(row[13]) if not math.isnan(row[13]) else 0
        stale_row = _stale_row(wire_drop | DROP_BRIDGE_STALE)
        # Preserve the real align_dt_ms in the placeholder so the
        # operator can see how stale this snapshot was.
        stale_row[16] = align_dt_ms
        return {
            "row": stale_row,
            "stale": True,
            "drop_reason": wire_drop | DROP_BRIDGE_STALE,
            "align_dt_ms": align_dt_ms,
            "raw_frame_id": frame.get("frame_id"),
        }

    return {
        "row": project_frame_to_row(stamped),
        "stale": False,
        "drop_reason": 0,
        "align_dt_ms": align_dt_ms,
        "raw_frame_id": frame.get("frame_id"),
    }

"""Qt-independent width planning for E-Brush Studio's responsive workspace.

This module is deliberately independent of Qt so a regression check can run on
machines without a window system. Widths are Qt logical pixels (DPI-aware).
"""
from __future__ import annotations

MIN_TILE_WIDTH = 430
MAX_AUTO_COLUMNS = 3
GAP = 6
OUTER_MARGIN = 8


def column_count(viewport_width: int, visible_count: int, mode: str = "auto") -> int:
    """Return columns without forcing crowded tiles into available space.

    Explicit two-column mode may scroll horizontally on narrow monitors instead
    of shrinking a calibration form until buttons/text are clipped.
    """
    n = max(0, int(visible_count))
    if n == 0:
        return 0
    if mode in ("one", "tabs"):
        return 1
    if mode == "two":
        return min(n, 2)
    if mode != "auto":
        raise ValueError(f"unknown view layout: {mode}")
    width = max(0, int(viewport_width) - 2 * OUTER_MARGIN)
    possible = max(1, (width + GAP) // (MIN_TILE_WIDTH + GAP))
    return min(n, MAX_AUTO_COLUMNS, possible)


def forced_minimum_width(columns: int, mode: str) -> int:
    """Explicit multi-column selection scrolls instead of crushing widgets."""
    if mode == "two" and columns == 2:
        return 2 * MIN_TILE_WIDTH + GAP + 2 * OUTER_MARGIN
    return 0

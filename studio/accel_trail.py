"""Continuous acceleration-aware trajectory for PySide6 e-Brush Studio.

Input position should come from calibrated PoseMapper / vision 3D; acceleration
alone is NOT used to integrate position because inertial drift is unbounded.
All values are time-based, not dependent on GUI refresh rate.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
import math
from typing import Callable, Deque, Iterable, List, Optional, Sequence, Tuple

Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class TrailPoint:
    time_s: float
    position: Vec3
    accel_energy: float
    pressure: float
    confidence: float
    alpha: float = 1.0
    width: float = 1.0


class AccelerationTrail:
    def __init__(self, duration_s: float = 0.8, smoothing_s: float = 0.045,
                 max_points: int = 200):
        if duration_s <= 0 or smoothing_s <= 0 or max_points < 2:
            raise ValueError("invalid trail settings")
        self.duration_s = duration_s
        self.smoothing_s = smoothing_s
        self._history: Deque[TrailPoint] = deque(maxlen=max_points)
        self._smooth: Optional[Vec3] = None
        self._last_t: Optional[float] = None
        self._energy = 0.0

    def reset(self) -> None:
        self._history.clear()
        self._smooth = None
        self._last_t = None
        self._energy = 0.0

    def add(self, time_s: float, xyz: Sequence[float], motion_accel_g: Sequence[float],
            pressure: float = 0.0, confidence: float = 1.0) -> bool:
        if len(xyz) != 3 or len(motion_accel_g) != 3:
            return False
        values = [float(time_s), *map(float, xyz), *map(float, motion_accel_g),
                  float(pressure), float(confidence)]
        if not all(math.isfinite(x) for x in values):
            return False
        if self._last_t is not None and time_s <= self._last_t:
            return False  # Discard reorders and duplicate timestamps.
        if self._last_t is not None and time_s - self._last_t > self.duration_s:
            self.reset()  # No phantom segments across radio disconnect.
        dt = 0.005 if self._last_t is None else max(1e-5, time_s - self._last_t)
        a = 1.0 - math.exp(-dt / self.smoothing_s)
        if self._smooth is None:
            self._smooth = (float(xyz[0]), float(xyz[1]), float(xyz[2]))
        else:
            self._smooth = tuple(old + a * (new - old) for old, new in zip(self._smooth, xyz))  # type: ignore[assignment]
        energy = math.sqrt(sum(float(v) ** 2 for v in motion_accel_g))
        self._energy += a * (energy - self._energy)
        self._last_t = time_s
        self._history.append(TrailPoint(time_s, self._smooth,
                                        self._energy, min(1., max(0., pressure)),
                                        min(1., max(0., confidence))))
        self._expire(time_s)
        return True

    def _expire(self, now_s: float) -> None:
        while self._history and now_s - self._history[0].time_s > self.duration_s:
            self._history.popleft()

    def primitives(self, now_s: float) -> List[TrailPoint]:
        if not math.isfinite(now_s):
            return []
        self._expire(now_s)
        ret = []
        for p in self._history:
            age = max(0., now_s - p.time_s)
            fade = math.exp(-3.5 * age / self.duration_s)
            alpha = max(0., min(1., fade * p.confidence))
            width = 1.6 + 3.2 * min(1., p.accel_energy / 2.) + 2.5 * p.pressure
            ret.append(TrailPoint(p.time_s, p.position, p.accel_energy,
                                  p.pressure, p.confidence, alpha, width))
        return ret


def render_pyside6_trail(painter, points: Iterable[TrailPoint],
                        project: Callable[[Vec3], Tuple[float, float]]) -> None:
    """Paint a smooth, translucent continuous stroke on an existing QPainter.

    Example: render_pyside6_trail(painter, trail.primitives(now), project_3d)
    """
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

    pts = list(points)
    if len(pts) < 2:
        return
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    path_pts = [QPointF(*project(p.position)) for p in pts]
    # Quadratic smoothing across midpoints; segment-by-segment alpha gradient.
    for i in range(1, len(pts)):
        previous, current = pts[i-1], pts[i]
        start, end = path_pts[i-1], path_pts[i]
        if previous.alpha <= 0 and current.alpha <= 0:
            continue
        color = QColor(223, 108, 52, round(190 * current.alpha))
        painter.setPen(QPen(color, max(.5, current.width), Qt.SolidLine,
                            Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath(start)
        if i < len(pts)-1:
            half = QPointF((end.x()+path_pts[i+1].x()) / 2,
                           (end.y()+path_pts[i+1].y()) / 2)
            path.quadTo(end, half)
        else:
            path.lineTo(end)
        painter.drawPath(path)
        # Motion flash, not an opaque marker hiding other geometry.
        if current.accel_energy > 0.16:
            painter.setPen(Qt.NoPen)
            glow = QColor(245, 166, 77, round(27 * current.alpha))
            painter.setBrush(glow)
            radius = 1.5 + min(8., current.accel_energy * 3.)
            painter.drawEllipse(end, radius, radius)
    painter.restore()

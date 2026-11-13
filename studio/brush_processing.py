"""Host-side equivalent of S3 Mahony6 and Adaptive::Filter.

Runs once per C3 sensor sample on the receiver's worker thread, *not* once
per Studio GUI refresh. Constants and validity rules follow lib/mahony6 and
src/s3_adaptive.cpp; position is never inferred by integrating acceleration.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def _clamp(value: float, lo: float, hi: float) -> float:
    if not math.isfinite(value):
        return lo
    return max(lo, min(hi, value))


def _length3(v) -> float:
    return math.sqrt(sum(float(x) * float(x) for x in v))


class Mahony6:
    def __init__(self):
        self.reset()

    def reset(self):
        self.q = [1.0, 0.0, 0.0, 0.0]

    def update(self, gx: float, gy: float, gz: float,
               ax: float, ay: float, az: float, dt: float) -> bool:
        if not (0.0002 < dt < 0.05):
            return False
        an2 = ax * ax + ay * ay + az * az
        if not math.isfinite(an2) or an2 < 1e-8:
            return False
        inv = 1.0 / math.sqrt(an2)
        ax *= inv
        ay *= inv
        az *= inv
        q0, q1, q2, q3 = self.q
        vx = 2.0 * (q1 * q3 - q0 * q2)
        vy = 2.0 * (q0 * q1 + q2 * q3)
        vz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3
        ex = ay * vz - az * vy
        ey = az * vx - ax * vz
        ez = ax * vy - ay * vx
        gx += 1.8 * ex
        gy += 1.8 * ey
        gz += 1.8 * ez
        h = dt * 0.5
        quat = [
            q0 + (-q1 * gx - q2 * gy - q3 * gz) * h,
            q1 + ( q0 * gx + q2 * gz - q3 * gy) * h,
            q2 + ( q0 * gy - q1 * gz + q3 * gx) * h,
            q3 + ( q0 * gz + q1 * gy - q2 * gx) * h,
        ]
        norm2 = sum(x * x for x in quat)
        if not math.isfinite(norm2) or norm2 < 1e-8:
            self.reset()
            return False
        self.q = [x / math.sqrt(norm2) for x in quat]
        return True


@dataclass
class AdaptiveResult:
    hall_mT: tuple[float, float, float] = (0.0, 0.0, 0.0)
    motion_accel_g: tuple[float, float, float] = (0.0, 0.0, 0.0)
    motion_energy: float = 0.0
    confidence: float = 0.0
    jerk_g_per_s: float = 0.0
    flags: int = 0


class AdaptiveFilter:
    def __init__(self):
        self.reset()

    def reset(self):
        self.hall_ready = False
        self.accel_ready = False
        self.hall_lp = [0.0, 0.0, 0.0]
        self.grav_lp = [0.0, 0.0, 0.0]
        self.motion_lp = [0.0, 0.0, 0.0]
        self.prev_motion = [0.0, 0.0, 0.0]
        self.confidence_lp = 0.0

    def update(self, hall, hall_valid: bool, accel, imu_valid: bool,
               dt: float, missed: int) -> AdaptiveResult:
        dt = _clamp(dt, 0.002, 0.05)
        trust = (0.5 if hall_valid else 0.0) + (0.5 if imu_valid else 0.0)
        flags = 0
        if missed:
            trust /= 1.0 + _clamp(float(missed), 0.0, 50.0) * 0.10
            flags |= 4
        if not hall_valid:
            flags |= 1
        if not imu_valid:
            flags |= 2

        if hall_valid:
            if not self.hall_ready:
                self.hall_lp = list(hall)
                self.hall_ready = True
            else:
                diff = _length3([hall[i] - self.hall_lp[i] for i in range(3)])
                if diff > 15.0:
                    trust *= 0.6
                    flags |= 8
                alpha = 0.15 if diff > 15.0 else _clamp(dt / (0.014 + dt), 0.1, 0.7)
                for i in range(3):
                    self.hall_lp[i] += alpha * (hall[i] - self.hall_lp[i])

        energy = 0.0
        jerk = 0.0
        if imu_valid:
            if not self.accel_ready:
                self.grav_lp = list(accel)
                self.accel_ready = True
            grav_alpha = _clamp(dt / (0.45 + dt), 0.002, 0.15)
            motion_alpha = _clamp(dt / (0.025 + dt), 0.05, 0.45)
            for i in range(3):
                self.grav_lp[i] += grav_alpha * (accel[i] - self.grav_lp[i])
                dynamic_g = accel[i] - self.grav_lp[i]
                self.motion_lp[i] += motion_alpha * (dynamic_g - self.motion_lp[i])
            jerk_vec = []
            for i in range(3):
                jerk_vec.append((self.motion_lp[i] - self.prev_motion[i]) / dt)
                self.prev_motion[i] = self.motion_lp[i]
            jerk = _clamp(_length3(jerk_vec), 0.0, 200.0)
            energy = _clamp(_length3(self.motion_lp), 0.0, 16.0)
        self.confidence_lp += 0.28 * (trust - self.confidence_lp)
        return AdaptiveResult(tuple(self.hall_lp), tuple(self.motion_lp),
                              energy, _clamp(self.confidence_lp, 0.0, 1.0), jerk, flags)


@dataclass
class TickUnwrapper:
    """Restore C3's 32-bit sensor timer; anchor it against a 64-bit BLE sync reply."""
    last: int | None = None
    full: int = 0

    def unwrap(self, tick: int) -> int:
        tick = int(tick) & 0xFFFFFFFF
        if self.last is None:
            self.last, self.full = tick, tick
            return self.full
        delta = (tick - self.last) & 0xFFFFFFFF
        if delta > 0x7FFFFFFF:
            raise ValueError('nonmonotonic C3 tick')
        self.full += delta
        self.last = tick
        return self.full

    def anchor(self, full_c3_us: int):
        if self.last is None:
            return
        # The BLE time reply and current samples must be within ~35 minutes.
        period = 1 << 32
        periods = (int(full_c3_us) - self.full + period // 2) // period
        self.full += periods * period

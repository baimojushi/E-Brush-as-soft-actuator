#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
e-Brush real-time pose remapper
===============================

Input:
  Existing e-Brush BRUSH protocol V1 through brush_serial.py
  q_w q_x q_y q_z, ax/ay/az, gx/gy/gz

Output:
  One JSON object per DATA frame on stdout, optionally CSV.

Default startup workflow:
  1) Put brush in UP_R0:
       shaft vertical, tip down / tail up;
       R0 index mark faces board +X (right).
  2) Start this script and keep still for ~1.5 s.
  3) It prints "REFERENCE LOCKED" to stderr.
  4) Then move normally.

Important:
  This is a 6-axis IMU. Gravity gives absolute tilt but NOT absolute heading.
  Therefore default "startup_reference" intentionally re-zeros heading at each
  launch from a known physical pose. It does not pretend yaw is magnetic north.

Coordinate frames:
  board +X = right on paper
  board +Y = away from writer
  board +Z = up from paper
  brush +Z = tip -> tail/shaft
  brush +X = R0 index-mark direction
"""

from __future__ import annotations
import argparse, csv, json, math, os, sys, time
from collections import deque
from pathlib import Path

import numpy as np

DEFAULT_BRUSH_HOST_DIR = r"D:\项目\交互艺术\书法微更新\e-Brush\host"
DEFAULT_BAUD = 921600


def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


def load_brush_serial(host_dir: str):
    host = Path(host_dir)
    if host.exists():
        sys.path.insert(0, str(host))
    try:
        from brush_serial import BrushSerial, auto_detect_port
        return BrushSerial, auto_detect_port
    except Exception as exc:
        raise RuntimeError(
            "Cannot import brush_serial.py. Expected host directory:\n"
            f"  {host_dir}\n"
            "Use --brush-host-dir to override it.\n"
            f"Original error: {exc}"
        ) from exc


def normalize(v):
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def q_wxyz_to_R(q):
    """Hamilton quaternion; returns R_world_from_sensor."""
    w, x, y, z = map(float, q)
    n = math.sqrt(w*w + x*x + y*y + z*z)
    if n < 1e-12:
        raise ValueError("zero quaternion")
    w, x, y, z = w/n, x/n, y/n, z/n
    return np.array([
        [1-2*(y*y+z*z), 2*(x*y-z*w),   2*(x*z+y*w)],
        [2*(x*y+z*w),   1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w),   2*(y*z+x*w),   1-2*(x*x+y*y)],
    ], dtype=float)


def Rz(rad):
    c, s = math.cos(rad), math.sin(rad)
    return np.array([[c,-s,0.0],[s,c,0.0],[0.0,0.0,1.0]])


def yaw_from_R(R):
    return math.atan2(float(R[1,0]), float(R[0,0]))


def wrap_pi(a):
    return (a + math.pi) % (2*math.pi) - math.pi


def matrix_to_quat_wxyz(R):
    """Stable 3x3 rotation matrix -> Hamilton wxyz."""
    m = np.asarray(R, dtype=float)
    tr = float(np.trace(m))
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w = 0.25*s
        x = (m[2,1]-m[1,2])/s
        y = (m[0,2]-m[2,0])/s
        z = (m[1,0]-m[0,1])/s
    else:
        i = int(np.argmax(np.diag(m)))
        if i == 0:
            s = math.sqrt(1+m[0,0]-m[1,1]-m[2,2])*2
            w=(m[2,1]-m[1,2])/s; x=.25*s
            y=(m[0,1]+m[1,0])/s; z=(m[0,2]+m[2,0])/s
        elif i == 1:
            s = math.sqrt(1+m[1,1]-m[0,0]-m[2,2])*2
            w=(m[0,2]-m[2,0])/s; x=(m[0,1]+m[1,0])/s
            y=.25*s; z=(m[1,2]+m[2,1])/s
        else:
            s = math.sqrt(1+m[2,2]-m[0,0]-m[1,1])*2
            w=(m[1,0]-m[0,1])/s; x=(m[0,2]+m[2,0])/s
            y=(m[1,2]+m[2,1])/s; z=.25*s
    q = np.array([w,x,y,z], dtype=float)
    q /= np.linalg.norm(q)
    if q[0] < 0:
        q = -q
    return q


def quat_average_wxyz(qs):
    qs = [normalize(q) for q in qs]
    if not qs:
        raise ValueError("no quaternions")
    ref = qs[0]
    arr = np.array([(-q if np.dot(q, ref) < 0 else q) for q in qs])
    q = normalize(arr.mean(axis=0))
    return q


class PoseMapper:
    def __init__(self, cal, heading_mode="startup_reference",
                 reference_seconds=1.5, reference_heading_deg=0.0):
        self.cal = cal
        self.C = np.asarray(cal["accelerometer"]["C"], float)
        self.abias = np.asarray(cal["accelerometer"]["bias_m_s2"], float)
        self.g = float(cal["accelerometer"]["gravity_m_s2"])
        self.A = np.asarray(cal["rigid_mount"]["R_imu_from_brush"], float)
        self.heading_mode = heading_mode
        self.reference_seconds = float(reference_seconds)
        self.reference_heading = math.radians(float(reference_heading_deg))
        self.reference_qs = []
        self.reference_t0 = None
        self.R_board_from_fusion = None
        self.reference_locked = False

        if heading_mode == "calibration_session":
            self.R_board_from_fusion = np.asarray(
                cal["calibration_session_board_alignment"]
                   ["R_board_from_fusion_calibration_session"], float)
            self.reference_locked = True
        elif heading_mode == "relative":
            # Same acquisition mechanism as startup_reference, but does not
            # require UP_R0; it simply defines current brush heading as zero.
            pass
        elif heading_mode != "startup_reference":
            raise ValueError("heading_mode must be startup_reference, relative, or calibration_session")

    def _try_lock_reference(self, q, host_t):
        if self.reference_locked:
            return
        if self.reference_t0 is None:
            self.reference_t0 = host_t
        self.reference_qs.append(np.asarray(q, float))
        if host_t - self.reference_t0 < self.reference_seconds:
            return

        q0 = quat_average_wxyz(self.reference_qs)
        Rf_i = q_wxyz_to_R(q0)
        Rf_b = Rf_i @ self.A

        # Heading is defined by brush +X projected to horizontal.
        # In startup_reference, UP_R0 means this should point board +X.
        # In relative mode, current +X is also defined as heading zero.
        x = Rf_b[:,0]
        horizontal = math.hypot(float(x[0]), float(x[1]))
        if horizontal < 0.25:
            raise RuntimeError(
                "Cannot lock heading: brush +X is too close to vertical. "
                "Use UP_R0 for startup_reference."
            )
        current = math.atan2(float(x[1]), float(x[0]))
        correction = self.reference_heading - current
        self.R_board_from_fusion = Rz(correction)
        self.reference_locked = True
        eprint(
            "REFERENCE LOCKED: fusion->board yaw correction = "
            f"{math.degrees(correction):+.3f} deg"
        )

    def map_frame(self, f):
        q = np.array([f["q_w"], f["q_x"], f["q_y"], f["q_z"]], float)
        qn = float(np.linalg.norm(q))
        if not np.isfinite(qn) or qn < 0.5:
            return None

        now = time.perf_counter()
        self._try_lock_reference(q, now)

        a_raw = np.array([f["ax_m_s2"], f["ay_m_s2"], f["az_m_s2"]], float)
        a_cal = self.C @ (a_raw - self.abias)
        a_norm = float(np.linalg.norm(a_cal))
        accel_gravity_error = abs(a_norm - self.g)

        Rf_i = q_wxyz_to_R(q)
        Rf_b = Rf_i @ self.A

        # Even before heading lock, shaft tilt is valid in fusion gravity frame.
        shaft_f = Rf_b[:,2]
        tilt_from_vertical = math.degrees(
            math.acos(max(-1.0, min(1.0, float(shaft_f[2]))))
        )

        out = {
            "frame_id": int(f.get("frame_id", -1)),
            "mcu_sample_time_us": int(f.get("sample_time_us", f.get("mcu_sample_time_us", 0))),
            "reference_locked": bool(self.reference_locked),
            "heading_absolute": False,
            "q_input_norm": qn,
            "accel_cal_x": float(a_cal[0]),
            "accel_cal_y": float(a_cal[1]),
            "accel_cal_z": float(a_cal[2]),
            "accel_norm_m_s2": a_norm,
            "accel_gravity_error_m_s2": accel_gravity_error,
            "tilt_from_vertical_deg": tilt_from_vertical,
        }

        if not self.reference_locked:
            return out

        Rb_b = self.R_board_from_fusion @ Rf_b
        shaft = Rb_b[:,2]
        bx = Rb_b[:,0]
        qbrush = matrix_to_quat_wxyz(Rb_b)

        # Shaft azimuth is undefined when shaft is nearly vertical.
        horiz = math.hypot(float(shaft[0]), float(shaft[1]))
        shaft_az = (
            math.degrees(math.atan2(float(shaft[1]), float(shaft[0])))
            if horiz > 1e-4 else None
        )

        # Brush-frame Euler is only a convenience view. Matrix/quaternion and
        # brush axes are the canonical outputs.
        pitch_x = math.degrees(math.atan2(float(shaft[0]), float(shaft[2])))
        pitch_y = math.degrees(math.atan2(float(shaft[1]), float(shaft[2])))
        heading = math.degrees(math.atan2(float(bx[1]), float(bx[0])))

        out.update({
            "brush_axis_x": float(shaft[0]),
            "brush_axis_y": float(shaft[1]),
            "brush_axis_z": float(shaft[2]),
            "brush_x_axis_x": float(bx[0]),
            "brush_x_axis_y": float(bx[1]),
            "brush_x_axis_z": float(bx[2]),
            "tilt_x_deg": pitch_x,
            "tilt_y_deg": pitch_y,
            "shaft_azimuth_deg": shaft_az,
            "heading_rel_deg": heading,
            "brush_q_w": float(qbrush[0]),
            "brush_q_x": float(qbrush[1]),
            "brush_q_y": float(qbrush[2]),
            "brush_q_z": float(qbrush[3]),
            "pose_quality": float(max(0.0, min(1.0, 1.0 - accel_gravity_error / 2.0))),
        })
        return out


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="e-Brush real-time calibrated pose remapper")
    ap.add_argument("--calibration", default=str(here / "ebrush_pose_calibration.json"))
    ap.add_argument("--brush-host-dir", default=DEFAULT_BRUSH_HOST_DIR)
    ap.add_argument("--port", default=None, help="e.g. COM7; default auto-detect")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument(
        "--heading-mode",
        choices=["startup_reference", "relative", "calibration_session"],
        default="startup_reference",
        help="startup_reference is recommended; calibration_session is only for replaying the old calibration boot"
    )
    ap.add_argument("--reference-seconds", type=float, default=1.5)
    ap.add_argument("--reference-heading-deg", type=float, default=0.0)
    ap.add_argument("--csv", default=None, help="optional output CSV path")
    ap.add_argument("--raw-log", default=None, help="optional BRUSH wire raw log path")
    args = ap.parse_args()

    cal = json.loads(Path(args.calibration).read_text(encoding="utf-8"))
    BrushSerial, auto_detect_port = load_brush_serial(args.brush_host_dir)
    port = args.port or auto_detect_port(verbose=True)

    eprint(f"Opening {port} @ {args.baud}")
    if args.heading_mode == "startup_reference":
        eprint("STARTUP REFERENCE: hold brush in UP_R0, still, until REFERENCE LOCKED.")

    mapper = PoseMapper(
        cal,
        heading_mode=args.heading_mode,
        reference_seconds=args.reference_seconds,
        reference_heading_deg=args.reference_heading_deg,
    )
    bs = BrushSerial(port=port, baud=args.baud, raw_log_path=args.raw_log)
    bs.start()

    csv_f = None
    csv_w = None
    try:
        while True:
            try:
                f = bs.frames.get(timeout=0.5)
            except Exception:
                continue
            if not isinstance(f, dict):
                continue
            needed = ("q_w","q_x","q_y","q_z","ax_m_s2","ay_m_s2","az_m_s2")
            if not all(k in f for k in needed):
                continue
            out = mapper.map_frame(f)
            if out is None:
                continue

            print(json.dumps(out, ensure_ascii=False, separators=(",", ":")), flush=True)

            if args.csv:
                if csv_f is None:
                    csv_f = open(args.csv, "w", newline="", encoding="utf-8")
                    csv_w = csv.DictWriter(csv_f, fieldnames=list(out.keys()))
                    csv_w.writeheader()
                # Field set grows after reference lock: reopen with canonical superset.
                if set(out.keys()) != set(csv_w.fieldnames):
                    csv_f.close()
                    csv_f = open(args.csv, "w", newline="", encoding="utf-8")
                    fields = [
                        "frame_id","mcu_sample_time_us","reference_locked","heading_absolute",
                        "q_input_norm","accel_cal_x","accel_cal_y","accel_cal_z",
                        "accel_norm_m_s2","accel_gravity_error_m_s2","tilt_from_vertical_deg",
                        "brush_axis_x","brush_axis_y","brush_axis_z",
                        "brush_x_axis_x","brush_x_axis_y","brush_x_axis_z",
                        "tilt_x_deg","tilt_y_deg","shaft_azimuth_deg","heading_rel_deg",
                        "brush_q_w","brush_q_x","brush_q_y","brush_q_z","pose_quality"
                    ]
                    csv_w = csv.DictWriter(csv_f, fieldnames=fields)
                    csv_w.writeheader()
                csv_w.writerow(out)
                csv_f.flush()
    except KeyboardInterrupt:
        eprint("\nStopped.")
    finally:
        if csv_f:
            csv_f.close()
        try:
            bs.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

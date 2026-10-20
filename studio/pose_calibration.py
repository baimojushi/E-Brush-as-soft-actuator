from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

STAGES = (
    "IDLE", "ON_STAND", "PICK_UP", "UP_R0",
    "X_TEST", "Y_TEST", "ROLL_TEST", "REVIEW", "COMMITTED",
)
UP0_DEFINITION = {
    "tip": "down_toward_paper",
    "shaft": "vertical",
    "tail": "up_away_from_paper",
    "brush_plus_z": "tip_to_tail_same_as_board_plus_z",
    "r0_index_mark": "toward_board_plus_x_writer_right",
    "r0_verified_by": "manual_confirmation_not_six_axis_imu",
}


class PoseCalibrationSession:
    """Explicit UP0-armed workflow. Never rewrite the sensor's base calibration.

    Six-axis IMU can validate verticality and stillness but CANNOT verify the
    absolute direction of the R0 roll index around a vertical shaft. The user
    must confirm the R0 marker points toward board +X before arming reference.
    """

    def __init__(self, root, pose_mapper, mapper_debug):
        self.root = Path(root)
        self.pose = pose_mapper
        self.mapper = mapper_debug
        self.dir = self.root / "config" / "pose_mapping_profiles"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.active_path = self.root / "config" / "pose_mapping_profile_active.json"
        self.reset()

    def reset(self):
        self.stage = "IDLE"
        self.started_unix = None
        self.samples = {}
        self.results = {}
        self.message = "Ready"
        self._still_t0 = None
        self._stand_n = 0
        self._last_valid_stand_t = None
        self._motion_seen = False
        self._stage_t0 = None
        self._roll0 = None
        self._up0_confirmed = False
        self._last_up0_pose = None
        self._last_up0_pose_at = None

    def start(self):
        if self.pose.heading_mode != "startup_reference":
            raise RuntimeError("UP0 wizard requires heading_mode=startup_reference")
        self.pose.disarm_reference()
        self.reset()
        self.started_unix = time.time()
        self._enter("ON_STAND")
        return self.state()

    def cancel(self):
        self.pose.disarm_reference()
        self.reset()
        return self.state()

    def _forget_from(self, stage):
        dependencies = {
            "ON_STAND": ("stand_stability", "pickup_detected", "up_r0_locked",
                         "up_r0_tilt_deg", "reference_state",
                         "x_live", "x_test", "y_live", "y_test",
                         "roll_live_delta_deg", "roll_test", "up0_confirmation"),
            "PICK_UP": ("pickup_detected", "up_r0_locked", "up_r0_tilt_deg",
                        "reference_state", "x_live", "x_test", "y_live",
                        "y_test", "roll_live_delta_deg", "roll_test",
                        "up0_confirmation"),
            "UP_R0": ("up_r0_locked", "up_r0_tilt_deg", "reference_state",
                      "x_live", "x_test", "y_live", "y_test",
                      "roll_live_delta_deg", "roll_test", "up0_confirmation"),
            "X_TEST": ("x_live", "x_test", "y_live", "y_test",
                       "roll_live_delta_deg", "roll_test"),
            "Y_TEST": ("y_live", "y_test", "roll_live_delta_deg", "roll_test"),
            "ROLL_TEST": ("roll_live_delta_deg", "roll_test"),
        }
        for key in dependencies.get(stage, ()):
            self.results.pop(key, None)
        if stage == "ON_STAND":
            self.samples.pop("stand", None)

    def _enter(self, stage):
        self._forget_from(stage)
        self.stage = stage
        self._stage_t0 = time.monotonic()
        self._still_t0 = None
        self._stand_n = 0
        self._last_valid_stand_t = None
        if stage in ("ON_STAND", "PICK_UP", "UP_R0"):
            self.pose.disarm_reference()
        if stage == "UP_R0":
            self._up0_confirmed = False
            self._last_up0_pose = None
        self._last_up0_pose_at = None
        if stage == "ROLL_TEST":
            self._roll0 = None
        self.message = {
            "ON_STAND": "Keep brush still on stand",
            "PICK_UP": "Remove brush from stand",
            "UP_R0": "Place brush UP0 and confirm R0 points to board +X",
            "UP_R0_ARMED": "Hold UP0 still until reference locks",
            "X_TEST": "Tilt brush tail toward board +X / right",
            "Y_TEST": "Tilt brush tail toward board +Y / away",
            "ROLL_TEST": "Keep shaft near vertical and rotate brush about its shaft",
            "REVIEW": "Review and commit mapping",
        }.get(stage, stage)

    def confirm_up0(self):
        """User acknowledges absolute R0 direction; vertical still checked by IMU."""
        if self.stage != "UP_R0":
            raise RuntimeError("请先进入 UP0 参考姿态步骤")
        p = self._last_up0_pose
        if (not p or not p.get("frame_fresh", True)
                or self._last_up0_pose_at is None
                or time.monotonic() - self._last_up0_pose_at > 0.5):
            raise RuntimeError("最近 0.5 秒内没有收到有效的新 IMU 位姿数据")
        tilt = float(p.get("up0_tilt_deg", math.inf))
        if not math.isfinite(tilt) or tilt > self.pose.ref_vertical_max:
            raise RuntimeError(
                f"笔杆未竖直：当前倾角 {tilt:.1f}°，允许 ≤ {self.pose.ref_vertical_max:.1f}°"
            )
        if not bool(p.get("imu_data_valid", False)):
            raise RuntimeError("缺少有效加速度、陀螺仪或四元数数据")
        self._up0_confirmed = True
        self.results["up0_confirmation"] = {
            "r0_mark_board_plus_x": True,
            "verified_by": "human_operator",
            "timestamp_unix": time.time(),
            "tilt_deg_at_confirmation": tilt,
        }
        self.pose.arm_reference()
        self.message = "Hold UP0 still until reference locks"
        return self.state()

    def advance(self):
        """No bypass: UI/remote 'next' may never skip an unvalidated stage."""
        requirements = {
            "ON_STAND": bool(self.samples.get("stand")),
            "PICK_UP": bool(self.results.get("pickup_detected")),
            "UP_R0": bool(self._up0_confirmed and self.results.get("up_r0_locked")),
            "X_TEST": bool(self.results.get("x_test", {}).get("pass")),
            "Y_TEST": bool(self.results.get("y_test", {}).get("pass")),
            "ROLL_TEST": bool(self.results.get("roll_test", {}).get("relative_pass")),
        }
        if self.stage not in requirements or not requirements[self.stage]:
            raise RuntimeError("当前步骤尚未通过，不能跳过。请按提示完成验证或重试。")
        next_step = {
            "ON_STAND": "PICK_UP", "PICK_UP": "UP_R0", "UP_R0": "X_TEST",
            "X_TEST": "Y_TEST", "Y_TEST": "ROLL_TEST", "ROLL_TEST": "REVIEW",
        }
        self._enter(next_step[self.stage])
        return self.state()

    def retry(self):
        if self.stage in ("IDLE", "COMMITTED"):
            raise RuntimeError("请先开始一次新的姿态校准")
        target = "UP_R0" if self.stage == "REVIEW" else self.stage
        self._enter(target)
        if target == "PICK_UP":
            self._motion_seen = False
        return self.state()

    def _metrics(self, b, pose):
        if not b or not pose:
            return float("inf"), float("inf")
        if not pose.get("imu_data_valid", False):
            return float("inf"), float("inf")
        return (
            float(pose.get("accel_gravity_error_m_s2", math.inf)),
            float(pose.get("gyro_norm_rad_s", math.inf)),
        )

    def update(self, brush, pose):
        if self.stage in ("IDLE", "COMMITTED", "REVIEW") or not brush or not pose:
            return self.state()
        if not pose.get("frame_fresh", True):
            return self.state()
        now = time.monotonic()
        aerr, gyro = self._metrics(brush, pose)
        still = gyro < 0.08 and aerr < 0.5

        if self.stage in ("X_TEST", "Y_TEST", "ROLL_TEST") and not pose.get("reference_locked"):
            self._enter("UP_R0")
            self.message = "Reference lost; repeat UP0 confirmation"
            return self.state()
        # A transmitted packet is not automatically a new IMU observation.
        # Never trigger pickup or an axis/roll test using missing/stale IMU.
        if self.stage != "ON_STAND" and not pose.get("imu_data_valid", False):
            return self.state()

        if self.stage == "ON_STAND":
            if not pose.get("imu_data_valid", False):
                # A transmitted frame without IMU_FRESH must neither accrue
                # samples nor reset a legitimate acquisition at higher TX Hz.
                if (self._still_t0 is not None and
                        (self._last_valid_stand_t is None
                         or now-self._last_valid_stand_t > 0.25)):
                    self._still_t0 = None
                    self._stand_n = 0
                    self.results["stand_stability"] = 0.0
                return self.state()
            if still:
                if self._last_valid_stand_t is not None and now-self._last_valid_stand_t > 0.25:
                    self._still_t0 = None
                    self._stand_n = 0
                self._last_valid_stand_t = now
                if self._still_t0 is None:
                    self._still_t0 = now
                self._stand_n += 1
                progress = max(0.0, min(1.0, (now - self._still_t0) / 1.5))
                self.results["stand_stability"] = progress
                if progress >= 1 and self._stand_n >= 20:
                    self.samples["stand"] = {
                        "gravity_error_m_s2": aerr,
                        "gyro_norm_rad_s": gyro,
                        "sample_count": self._stand_n,
                        "unix": time.time(),
                    }
                    self._enter("PICK_UP")
            else:
                self._still_t0 = None
                self._stand_n = 0
                self.results["stand_stability"] = 0.0
        elif self.stage == "PICK_UP":
            if gyro > 0.18 or aerr > 0.9:
                self._motion_seen = True
                self.results["pickup_detected"] = True
                self._enter("UP_R0")
        elif self.stage == "UP_R0":
            if pose.get("imu_data_valid"):
                self._last_up0_pose = dict(pose)
                self._last_up0_pose_at = now
            self.results["reference_state"] = self.pose.reference_state
            self.results["up_r0_tilt_deg"] = float(pose.get("up0_tilt_deg", math.inf))
            if self._up0_confirmed and self.pose.reference_locked and pose.get("reference_locked"):
                if float(pose.get("up0_tilt_deg", math.inf)) <= self.pose.ref_vertical_max:
                    self.results["up_r0_locked"] = True
                    self.results["reference_state"] = "LOCKED"
                    self._enter("X_TEST")
        elif self.stage == "X_TEST" and pose.get("reference_locked"):
            tx = float(pose.get("tilt_x_deg", math.nan))
            ty = float(pose.get("tilt_y_deg", math.nan))
            self.results["x_live"] = {"tilt_x_deg": tx, "tilt_y_deg": ty}
            if math.isfinite(tx) and math.isfinite(ty) and tx >= 15 and abs(tx) >= 1.5 * abs(ty):
                self.results["x_test"] = {"pass": True, "tilt_x_deg": tx, "cross_y_deg": ty}
                self._enter("Y_TEST")
        elif self.stage == "Y_TEST" and pose.get("reference_locked"):
            tx = float(pose.get("tilt_x_deg", math.nan))
            ty = float(pose.get("tilt_y_deg", math.nan))
            self.results["y_live"] = {"tilt_x_deg": tx, "tilt_y_deg": ty}
            if math.isfinite(tx) and math.isfinite(ty) and ty >= 15 and abs(ty) >= 1.5 * abs(tx):
                self.results["y_test"] = {"pass": True, "tilt_y_deg": ty, "cross_x_deg": tx}
                self._enter("ROLL_TEST")
        elif self.stage == "ROLL_TEST" and pose.get("reference_locked"):
            tilt = float(pose.get("tilt_from_vertical_deg", math.inf))
            r = pose.get("roll_about_brush_rel_deg")
            if r is not None and math.isfinite(float(r)) and tilt < 15:
                if self._roll0 is None:
                    self._roll0 = float(r)
                delta = (float(r) - self._roll0 + 180) % 360 - 180
                self.results["roll_live_delta_deg"] = delta
                if abs(delta) >= 45:
                    self.results["roll_test"] = {
                        "relative_pass": True,
                        "delta_deg": delta,
                        "absolute_verified": False,
                    }
                    self._enter("REVIEW")
        return self.state()

    def quality(self):
        return sum((
            20 if self.samples.get("stand") else 0,
            25 if self.results.get("up_r0_locked") and self.results.get("up0_confirmation") else 0,
            20 if self.results.get("x_test", {}).get("pass") else 0,
            20 if self.results.get("y_test", {}).get("pass") else 0,
            15 if self.results.get("roll_test", {}).get("relative_pass") else 0,
        ))

    def can_commit(self):
        r = self.results
        return (self.stage == "REVIEW" and bool(self.samples.get("stand"))
                and bool(r.get("up0_confirmation")) and bool(r.get("up_r0_locked"))
                and bool(r.get("x_test", {}).get("pass"))
                and bool(r.get("y_test", {}).get("pass"))
                and bool(r.get("roll_test", {}).get("relative_pass"))
                and bool(self.pose.reference_locked))

    def state(self):
        return {
            "schema": "ebrush.pose_calibration_session.v2",
            "stage": self.stage,
            "message": self.message,
            "started_unix": self.started_unix,
            "quality_score": self.quality(),
            "results": self.results,
            "up0_definition": UP0_DEFINITION,
            "up0_confirmation_required": self.stage == "UP_R0" and not self._up0_confirmed,
            "reference_state": self.pose.reference_state,
            "mapper": self.mapper.state(),
            "can_commit": self.can_commit(),
            "limitations": {"heading_absolute": False, "roll_absolute_verified": False,
                            "r0_index_mark_sensor_verified": False},
        }

    def commit(self, name=None):
        if not self.can_commit():
            raise RuntimeError("校准未完成：UP0 人工确认、X/Y/ROLL 验证全部通过后才能保存")
        if not name:
            name = time.strftime("posemap_%Y%m%d_%H%M%S")
        name = "".join(c for c in str(name) if c.isalnum() or c in "-_") or "posemap"
        mv = self.mapper.save(name)
        prof = {
            "schema": "ebrush.pose_mapping.v3", "name": name,
            "created_unix": time.time(), "parent": self._active_name(),
            "sensor_calibration": self.pose.cal.get("created_from"),
            "rigid_mount": {"R_imu_from_brush": np.asarray(self.pose.A).tolist()},
            "startup_reference": {
                "method": "UP_R0",
                "definition": UP0_DEFINITION,
                "heading_absolute": False,
                "reference_state": self.results.get("reference_state"),
            },
            "validation": self.results, "quality_score": self.quality(),
            "mapper_override": mv,
            "limitations": {"absolute_heading": False, "absolute_roll": "UNVERIFIED",
                            "r0_direction": "USER_CONFIRMED"},
        }
        p = self.dir / (name + ".json")
        p.write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
        self.active_path.write_text(
            json.dumps({"active": name, "path": str(p)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.stage = "COMMITTED"
        self.message = "Mapping profile committed"
        return prof

    def _active_name(self):
        try:
            return json.loads(self.active_path.read_text(encoding="utf-8")).get("active")
        except (OSError, json.JSONDecodeError):
            return None

    def list_profiles(self):
        out = []
        for p in sorted(self.dir.glob("*.json"), reverse=True):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({
                    "name": d.get("name", p.stem), "quality_score": d.get("quality_score"),
                    "created_unix": d.get("created_unix"),
                    "active": d.get("name") == self._active_name(),
                })
            except (OSError, json.JSONDecodeError):
                pass
        return out

    def load_profile(self, name):
        # Loading a profile only changes debug overrides, never the IMU base matrix.
        if not name or Path(name).name != name or not all(c.isalnum() or c in "-_" for c in name):
            raise ValueError("invalid profile name")
        p = self.dir / (name + ".json")
        d = json.loads(p.read_text(encoding="utf-8"))
        mo = d.get("mapper_override", {})
        self.mapper.board_deg = np.asarray(mo.get("board_xyz_deg", [0, 0, 0]), float)
        self.mapper.brush_deg = np.asarray(mo.get("brush_xyz_deg", [0, 0, 0]), float)
        self.mapper.active_version = name
        self.mapper.persist()
        self.active_path.write_text(json.dumps(
            {"active": name, "path": str(p)}, ensure_ascii=False, indent=2
        ), encoding="utf-8")
        return d

#!/usr/bin/env python3
"""verify_schema_v1.py — ARCHIVE_SCHEMA_V1 一致性检查（仅标准库）。

用途：
  1) 证明归档可脱离 TouchDesigner / 厂商 SDK / numpy-pandas 完整解析；
  2) 校验 episode.json 必填键与枚举、signal.csv 列契约、时间基准自洽、
     工程量可由原始码 + 换算常数重算（host-v1 常数表内置，作参考实现）。

用法：
  python verify_schema_v1.py <episode_dir>
退出码 0=PASS；1=FAIL。任何 FAIL 项带原因输出。
"""
import csv
import json
import math
import sys

SCHEMA_MAJOR_MINOR = (1, 0)

# —— host-v1 换算常数（参考实现；正式以 device.conversion_constants_version 为准）——
ACCEL_MG_PER_LSB = 0.488      # ±16g
GYRO_MDPS_PER_LSB = 8.75      # ±250 dps
G0 = 9.80665
IMU_TEMP_T0 = 25.0
IMU_TEMP_LSB_PER_C = 256.0

COLUMNS = (
    "frame_id calibration_id sample_time_us hall_time_us imu_time_us "
    "sample_host_ns hall_host_ns imu_host_ns host_rx_ns clock_synced sync_rtt_ms "
    "device_status drop_flags "
    "hall_x_raw hall_y_raw hall_z_raw hall_temp_raw "
    "hall_conv_status hall_device_status imu_status "
    "imu_temp_raw gyro_x_raw gyro_y_raw gyro_z_raw accel_x_raw accel_y_raw accel_z_raw "
    "bx_mT by_mT bz_mT hall_temp_C imu_temp_C "
    "gx_rad_s gy_rad_s gz_rad_s ax_m_s2 ay_m_s2 az_m_s2 q_w q_x q_y q_z"
).split()

LICENSE_MODES = {"self_only", "timed_public", "paid_public", "free_forever"}
VERDICTS = {"ok", "suspect", "excluded"}
ANON_LEVELS = {"anonymous", "pseudonymous", "identified"}

fails = []


def fail(msg):
    fails.append(msg)


def close(a, b, tol):
    return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= tol


def main(ep_dir):
    with open(ep_dir + "/episode.json", "r", encoding="utf-8") as f:
        ep = json.load(f)

    ver = str(ep.get("schema_version", "")).split(".")
    try:
        if (int(ver[0]), int(ver[1])) != SCHEMA_MAJOR_MINOR:
            fail("schema major.minor 不匹配: %s" % ep.get("schema_version"))
    except Exception:
        fail("schema_version 非法")

    for key in ("episode_id", "site_id", "writer_id", "created_utc",
                "device", "calibration", "timebase", "conditions", "quality", "license"):
        if key not in ep:
            fail("episode.json 缺必填键 %s" % key)

    dev = ep.get("device", {})
    for key in ("brush_sn", "firmware_version", "hall_variant", "imu_model",
                "conversion_constants_version"):
        if key not in dev:
            fail("device 缺 %s" % key)
    if ep.get("license", {}).get("mode") not in LICENSE_MODES:
        fail("license.mode 非法: %r" % ep["license"].get("mode"))
    q = ep.get("quality", {})
    if q.get("verdict") not in VERDICTS:
        fail("quality.verdict 非法: %r" % q.get("verdict"))
    if ep.get("conditions", {}).get("writer_anon_level") not in ANON_LEVELS:
        fail("writer_anon_level 非法")

    with open(ep_dir + "/signal.csv", "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    hdr = list(rows[0].keys()) if rows else []
    if hdr != COLUMNS:
        fail("signal.csv 列契约不匹配（顺序/命名必须与 §6.1 一致）\n  got: %s" % hdr)

    if len(rows) != q.get("frames_written"):
        fail("frames_written=%r != csv 行数=%d" % (q.get("frames_written"), len(rows)))

    lsb = float(dev.get("hall_lsb_per_mT") or math.nan)
    off = int(ep["timebase"]["offset_ns_host_minus_mcu"])
    synced_ok = 0
    prev_fid = None
    prev_ns = None
    for i, r in enumerate(rows):
        fid = int(r["frame_id"])
        if prev_fid is not None and fid != prev_fid + 1:
            fail("frame_id 跳变 %d->%d (row %d)，应记入 quality.frame_id_gaps" % (prev_fid, fid, i))
        prev_fid = fid
        ns = int(r["sample_host_ns"])
        if prev_ns is not None and ns < prev_ns:
            fail("sample_host_ns 非单调 row %d" % i)
        prev_ns = ns
        if r["clock_synced"] == "1":
            synced_ok += 1
            if not close(ns, off + int(r["sample_time_us"]) * 1000, 5e6):
                fail("row %d 时间基准不自洽(host != offset + mcu*1e3)" % i)
        for raw_c, eng_c in (("hall_x_raw", "bx_mT"), ("hall_y_raw", "by_mT"), ("hall_z_raw", "bz_mT")):
            if r[raw_c] and r[eng_c] != "" and not close(float(r[eng_c]), int(r[raw_c]) * lsb, 1e-3):
                fail("row %d %s 与 %s*lsb 偏差>1e-3" % (i, eng_c, raw_c))
        for raw_c, eng_c, k in (("accel_x_raw", "ax_m_s2", ACCEL_MG_PER_LSB * 1e-3 * G0),
                                ("accel_y_raw", "ay_m_s2", ACCEL_MG_PER_LSB * 1e-3 * G0),
                                ("accel_z_raw", "az_m_s2", ACCEL_MG_PER_LSB * 1e-3 * G0),
                                ("gyro_x_raw", "gx_rad_s", GYRO_MDPS_PER_LSB * 1e-3 * math.pi / 180),
                                ("gyro_y_raw", "gy_rad_s", GYRO_MDPS_PER_LSB * 1e-3 * math.pi / 180),
                                ("gyro_z_raw", "gz_rad_s", GYRO_MDPS_PER_LSB * 1e-3 * math.pi / 180)):
            if r[raw_c] and r[eng_c] != "" and not close(float(r[eng_c]), int(r[raw_c]) * k, 0.01):
                fail("row %d %s 重算偏差>0.01" % (i, eng_c))
        if r["imu_temp_raw"] and not close(float(r["imu_temp_C"]),
                                           IMU_TEMP_T0 + int(r["imu_temp_raw"]) / IMU_TEMP_LSB_PER_C, 0.05):
            fail("row %d imu_temp_C 重算偏差>0.05" % i)
        qn = math.sqrt(sum(float(r[c]) ** 2 for c in ("q_w", "q_x", "q_y", "q_z")))
        if not close(qn, 1.0, 1e-3):
            fail("row %d 四元数未归一化 (|q|=%.4f)" % (i, qn))

    if synced_ok == 0 and len(rows) > 0 and ep["timebase"]["clock_disciplined_at_start"]:
        fail("clock_disciplined_at_start=true 但全帧 clock_synced=0")

    print("=" * 62)
    print("episode:", ep.get("episode_id"), "| frames:", len(rows),
          "| synced:", synced_ok, "| schema:", ep.get("schema_version"))
    if fails:
        for m in fails:
            print("FAIL:", m)
        return 1
    print("PASS: 结构、列契约、时间基准、工程量重算、归一化、枚举 全部通过")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1].rstrip("/\\")))

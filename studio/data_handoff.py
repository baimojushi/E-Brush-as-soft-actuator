"""Produce a portable, explicit E-Brush -> RC Studio *manifest*.

E-Brush does not preprocess sessions, train ESNs or write to RC Studio.
Only metadata and an SHA-256 digest are written; source logs remain untouched.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ALLOWED_SUFFIXES = {".brlog", ".csv"}


def make_manifest(source_path, quality=None):
    source = Path(source_path).expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError("只支持已存在的 .brlog 或 .csv 原始/归档数据文件")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    # Snapshot is observation at manifest creation, NOT a quality certification
    q = quality or {}
    return {
        "schema": "ebrush.handoff.v1", "created_utc": datetime.now(timezone.utc).isoformat(),
        "sender": "E-Brush Studio", "receiver": "RC Studio",
        "source": {"absolute_path": str(source), "name": source.name,
                   "size_bytes": source.stat().st_size, "sha256": digest.hexdigest()},
        "target_project_hint": "EBrush Motion Predictor V1",
        "task_hint": {"prediction_target": "future angular speed", "horizon_ms": 50,
                      "acquisition_target_hz": 200},
        "observed_quality_snapshot": {
            k: q.get(k) for k in (
                "host_receive_hz", "frame_gap_percent_window", "imu", "hall", "severity", "summary")
        },
        "boundary": {
            "ebrush_owns": "sensor acquisition, calibration, raw log and manifest",
            "rcstudio_owns": "log parsing, episode segmentation, ESN training, validation, models and experiments",
            "effects": "manifest only; no upload, RC Studio command or training is initiated",
        },
    }


def save_manifest(source_path, manifest_path, quality=None):
    path = Path(manifest_path).expanduser()
    if path.suffix.lower() != ".json":
        raise ValueError("移交清单必须为 .json")
    manifest = make_manifest(source_path, quality)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never silently overwrite an existing manifest.
    with path.open("x", encoding="utf-8") as out:
        json.dump(manifest, out, ensure_ascii=False, indent=2)
    return manifest

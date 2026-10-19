"""Human-reviewed training-set export; never convert inference-only labels to GT."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def accepted_annotations(episode_dir: str|Path):
    episode=Path(episode_dir)
    manifest_file=episode/"episode.json"
    if not manifest_file.is_file():
        raise ValueError("Training export requires independently supplied episode.json")
    manifest=json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("schema_version")!="1.0.0":
        raise ValueError("E-Brush episode schema version must be 1.0.0")
    license_data=manifest.get("license",{})
    if not isinstance(license_data,dict) or not license_data.get("mode"):
        raise ValueError("E-Brush episode requires recorded license.mode")
    if license_data.get("mode")!="self_only" and not license_data.get("consent_captured_utc"):
        raise ValueError("Non-self-only use requires recorded consent")
    if manifest.get("quality",{}).get("verdict")!="ok":
        raise ValueError("Episode quality verdict must be ok")
    signal_file=episode/"signal.csv"
    if not signal_file.is_file() or len(signal_file.read_bytes().splitlines())<2:
        raise ValueError("Training export requires nonempty BRUSH signal.csv")
    root=episode/"vision"
    timebase=json.loads((root/"timebase.json").read_text(encoding="utf-8"))
    if (timebase.get("archive_signal_clock")!="posix_utc_ns_estimated"
        or timebase.get("vision_frame_clock")!="perf_counter_monotonic_ns"):
        raise ValueError("Archive timebase conversion metadata is missing or unsupported")
    calib_file=root/"calibration.json"
    conf=json.loads(calib_file.read_text(encoding="utf-8"))
    calibration=conf["calibration"]
    if not (calibration.get("verified") and calibration.get("depth_aligned_to_color")
            and calibration.get("color_rectified")):
        raise ValueError("Only geometrically verified RGB-D calibration can be exported")
    collected=[]
    for annotation in sorted((root/"annotations").glob("*.json")):
        data=json.loads(annotation.read_text(encoding="utf-8"))
        if data.get("label_status")!="CANDIDATE":
            continue
        reviews=[]
        for f in (root/"reviews").glob(annotation.stem+"_*.json"):
            item=json.loads(f.read_text(encoding="utf-8"))
            if item.get("annotation")==annotation.name:
                reviews.append(item)
        # Multiple contradictory reviews are intentionally fail-closed.
        if len(reviews)!=1 or reviews[0].get("decision")!="ACCEPTED":
            continue
        frame_id=int(data["camera_frame_id"])
        meta_file=root/"frames"/f"{frame_id:08d}.json"
        if not meta_file.exists():
            raise RuntimeError("Accepted annotation missing RGB-D source frame")
        raw=json.loads(meta_file.read_text(encoding="utf-8"))
        if not raw.get("camera_clock_synced"):
            # Time-basis mismatch can poison multimodal trajectory targets.
            continue
        def verified(ref,sha):
            file=root/ref
            if not file.resolve().is_relative_to(root.resolve()):
                raise ValueError("Unsafe source file path")
            blob=file.read_bytes()
            if hashlib.sha256(blob).hexdigest()!=sha:
                raise ValueError(f"Checksum mismatch: {ref}")
            return str(file.relative_to(Path(episode_dir)))
        record={
            "camera_frame_id":frame_id,
            "capture_host_ns":raw["capture_host_ns"],
            "capture_utc_estimated_ns":(
                timebase["utc_anchor_ns"]
                + int(raw["capture_host_ns"])
                - timebase["monotonic_anchor_ns"]
            ),
            "timebase_anchor_spread_ns":timebase["anchor_measurement_spread_ns"],
            "calibration_sha256":conf["calibration_sha256"],
            "color_file":verified(raw["color_file"],raw["color_sha256"]),
            "depth_file":verified(raw["depth_file"],raw["depth_sha256"]),
            "mask_file":verified(data["mask_file"],data["mask_sha256"]),
            "model_version":data["model_version"],
            "prompt":data["prompt"],
            "mask_score":data["mask_score"],
            "review_id":reviews[0]["review_id"],
            "reviewer":reviews[0]["reviewer"],
            "quality_status":"HUMAN_REVIEWED",
            "spatial_point_kind":data["geometry"]["position_kind"],
            "camera_surface_center_m":data["geometry"]["camera_center_m"],
            "world_surface_center_m":data["geometry"]["world_center_m"],
            "persistent_track_id":None,  # must be assigned by separate verified tracker review
            "brush_sample_frame_id":(
                (raw.get("matched_brush") or {}).get("frame_id")
            ),
            "brush_clock_synced":bool(
                (raw.get("matched_brush") or {}).get("synced",False)
            ),
            "multimodal_sensor_supervision_eligible":bool(
                (raw.get("matched_brush") or {}).get("synced",False)
                and (raw.get("matched_brush") or {}).get("quality")=="MATCHED"
                and manifest.get("quality",{}).get("frame_id_gaps",0)==0
                and manifest.get("quality",{}).get("host_queue_drops",0)==0
            ),
        }
        collected.append(record)
    return collected


def export_training_index(episode_dir: str|Path, output_path:str|Path):
    records=accepted_annotations(episode_dir)
    output=Path(output_path)
    if output.exists():
        raise FileExistsError("Training index already exists; use a new versioned export")
    output.parent.mkdir(parents=True,exist_ok=True)
    # Do not label empty/untested episodes as validated datasets.
    if not records:
        raise ValueError("No independently accepted annotations were found")
    with open(output,"x",encoding="utf-8") as f:
        for row in records:
            f.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+"\n")
    return len(records)

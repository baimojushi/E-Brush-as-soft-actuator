"""Studio full-video SAM3 jobs: bounded asynchronous upload, exact-frame mask
return, atomic per-chunk checkpoints, resumability and read-only preview.

No video frames are intentionally subsampled. Long videos are partitioned into
clips <= server max_frames. Each returned mask is mapped to an original frame
index in a manifest. All outputs remain UNVERIFIED_CANDIDATE for 3D labels.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import math
import os
import queue
import re
import shutil
import threading
import time
import uuid
import zipfile

import cv2
import numpy as np

try:
    from .sam3_client import composite_overlay
    from .sam3_transport import Sam3HTTPTransport, RemoteMask, parse_mask_zip, sha256_file
except ImportError:
    from sam3_client import composite_overlay
    from sam3_transport import Sam3HTTPTransport, RemoteMask, parse_mask_zip, sha256_file


JOB_ID_RE = re.compile(r"[a-f0-9]{32}\Z")
SUPPORTED_VIDEO = {".mp4", ".avi", ".mov", ".mkv", ".webm"}


def _atomic_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _atomic_jsonl(path: Path, rows):
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class VideoJobManager:
    def __init__(self, runtime_dir, transport: Sam3HTTPTransport,
                 *, chunk_frames=50, max_frame_pixels=307200,
                 max_source_bytes=4 * 1024**3, queue_size=3,
                 event_sink=None):
        self.root = Path(runtime_dir) / "sam3_video_jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.transport = transport
        self.chunk_frames = int(chunk_frames)
        if not 1 <= self.chunk_frames <= 400:
            raise ValueError("Chunk size must be between 1 and 400")
        self.max_frame_pixels = int(max_frame_pixels)
        self.max_source_bytes = int(max_source_bytes)
        self._queue = queue.Queue(maxsize=max(1, int(queue_size)))
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._guard = threading.RLock()
        self._jobs = {}
        self._event_sink = event_sink
        self._busy_job = None
        self._events_path = self.root / "events.jsonl"
        for path in sorted(self.root.glob("*/job.json")):
            try:
                j = json.loads(path.read_text(encoding="utf-8"))
                if JOB_ID_RE.fullmatch(j.get("job_id", "")):
                    if j.get("state") in ("RUNNING", "QUEUED"):
                        j["state"] = "INTERRUPTED"
                        j["error"] = "Studio restarted; use sam3.video.retry to resume"
                        _atomic_json(path, j)
                    self._jobs[j["job_id"]] = j
            except (OSError, ValueError, KeyError):
                continue
        self._thread = threading.Thread(target=self._run, name="SAM3VideoJobWorker", daemon=True)
        self._thread.start()

    def _event(self, event, **fields):
        row = {"timestamp_unix": time.time(), "event": event, **fields}
        with self._guard:
            with self._events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if self._event_sink:
            try: self._event_sink("sam3.video." + event, **fields)
            except Exception: pass

    def _location(self, job_id):
        if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
            raise ValueError("Invalid SAM3 job id")
        return self.root / job_id

    def _update(self, job_id, **updates):
        with self._guard:
            j = dict(self._jobs[job_id])
            j.update(updates, updated_unix=time.time())
            _atomic_json(self._location(job_id) / "job.json", j)
            self._jobs[job_id] = j
        return dict(j)

    def enqueue(self, path: str, prompt="calligraphy brush", frame_manifest_path=None):
        source = Path(path).expanduser().resolve()
        if source.suffix.lower() not in SUPPORTED_VIDEO:
            raise ValueError("Choose a supported video file (.mp4/.avi/.mov/.mkv/.webm)")
        if not source.is_file() or source.stat().st_size <= 0:
            raise FileNotFoundError("Source video is missing/empty")
        if source.stat().st_size > self.max_source_bytes:
            raise ValueError("Video exceeds 4GiB source bound")
        if not str(prompt).strip():
            raise ValueError("SAM3 prompt is empty")
        candidate = (Path(frame_manifest_path).expanduser().resolve()
                     if frame_manifest_path else source.with_suffix(source.suffix + ".frames.jsonl"))
        if frame_manifest_path and not candidate.is_file():
            raise FileNotFoundError("Explicit frame manifest does not exist")
        if candidate.exists() and (not candidate.is_file() or candidate.stat().st_size > 256 * 1024**2):
            raise ValueError("Video frame manifest must be a file smaller than 256MiB")
        with self._guard:
            if self._queue.full():
                raise RuntimeError("Full-video job queue is full")
            jid = uuid.uuid4().hex
            target = self._location(jid)
            target.mkdir(parents=True, exist_ok=False)
            job = {"job_id": jid, "source_original": str(source),
                   "source_filename": source.name, "source_ext": source.suffix.lower(),
                   "prompt": str(prompt),
                   "frame_manifest_source": str(candidate) if candidate.is_file() else None,
                   "state": "QUEUED", "error": "",
                   "created_unix": time.time(), "updated_unix": time.time(),
                   "frames_total": None, "frames_processed": 0,
                   "chunks_completed": 0, "masks_total": 0,
                   "label_status": "CANDIDATE", "mode": "batch_full_video"}
            _atomic_json(target / "job.json", job)
            self._jobs[jid] = job
            self._queue.put_nowait(jid)
        self._event("queued", job_id=jid, source=source.name)
        return dict(job)

    def jobs(self):
        with self._guard:
            return sorted((dict(j) for j in self._jobs.values()),
                          key=lambda j: j["created_unix"], reverse=True)

    def status(self, job_id=None):
        with self._guard:
            if job_id is not None:
                self._location(job_id)
                if job_id not in self._jobs:
                    raise KeyError("Unknown job ID")
                return dict(self._jobs[job_id])
            jobs = list(self._jobs.values())
            return {"busy_job": self._busy_job, "queued": self._queue.qsize(),
                    "paused": self._pause.is_set(),
                    "worker_alive": self._thread.is_alive(),
                    "jobs_total": len(jobs),
                    "jobs_completed": sum(j["state"] == "COMPLETED" for j in jobs),
                    "jobs_failed": sum(j["state"] in ("FAILED", "INTERRUPTED") for j in jobs),
                    "output_dir": str(self.root), "chunk_frames": self.chunk_frames,
                    "transport": "http-batch-full-video"}

    def pause(self):
        self._pause.set()
        return self.status()

    def resume(self):
        self._pause.clear()
        return self.status()

    def retry(self, job_id):
        with self._guard:
            self._location(job_id)
            if job_id not in self._jobs:
                raise KeyError("Unknown job")
            state = self._jobs[job_id]["state"]
            if state not in ("FAILED", "INTERRUPTED", "PAUSED"):
                raise ValueError("Only FAILED, INTERRUPTED or PAUSED jobs can be retried")
            if self._queue.full():
                raise RuntimeError("Video upload queue full")
            self._update(job_id, state="QUEUED", error="")
            self._queue.put_nowait(job_id)
        self._event("retried", job_id=job_id)
        return self.status(job_id)

    def _run(self):
        while not self._stop.is_set():
            if self._pause.wait(.0):
                self._stop.wait(.2)
                continue
            try:
                jid = self._queue.get(timeout=.2)
            except queue.Empty:
                continue
            with self._guard:
                self._busy_job = jid
            try:
                self._process(jid)
            except Exception as exc:
                self._update(jid, state="FAILED", error=f"{type(exc).__name__}: {exc}")
                self._event("failed", job_id=jid, error=f"{type(exc).__name__}: {exc}")
            finally:
                with self._guard:
                    self._busy_job = None
                self._queue.task_done()

    @staticmethod
    def _infer_size(w, h, max_pixels):
        if w < 2 or h < 2:
            raise ValueError("Video dimensions too small")
        s = min(1., math.sqrt(max_pixels / (w * h)))
        # mp4v writes even dimensions; store the precise pixel transform.
        rw = max(2, (int(w * s) // 2) * 2)
        rh = max(2, (int(h * s) // 2) * 2)
        while rw * rh > max_pixels:
            if rw >= rh: rw -= 2
            else: rh -= 2
        return rw, rh

    def _spool_source(self, jid):
        j = self.status(jid)
        out = self._location(jid) / ("source" + j["source_ext"])
        if out.is_file():
            stored_sha = sha256_file(out)
            if j.get("source_sha256") and stored_sha != j["source_sha256"]:
                raise RuntimeError("Spool source checksum mismatch; manual intervention required")
            if not j.get("source_sha256"):
                self._update(jid, source_sha256=stored_sha,
                             source_size_bytes=out.stat().st_size)
            self._stage_manifest(jid)
            return out
        src = Path(j["source_original"])
        if not src.is_file() or src.stat().st_size > self.max_source_bytes:
            raise FileNotFoundError("Source video moved or exceeded size limit before staging")
        if shutil.disk_usage(out.parent).free < src.stat().st_size + 100 * 1024**2:
            raise OSError("Not enough free space to stage full source video")
        partial = out.with_suffix(out.suffix + ".part")
        hasher = hashlib.sha256()
        try:
            with src.open("rb") as inf, partial.open("wb") as dst:
                while True:
                    b = inf.read(1024 * 1024)
                    if not b: break
                    dst.write(b)
                    hasher.update(b)
                dst.flush()
                os.fsync(dst.fileno())
            os.replace(partial, out)
        finally:
            partial.unlink(missing_ok=True)
        self._update(jid, source_sha256=hasher.hexdigest(),
                     source_size_bytes=out.stat().st_size)
        self._stage_manifest(jid)
        return out

    def _stage_manifest(self, jid):
        job = self.status(jid)
        manifest = job.get("frame_manifest_source")
        if manifest is None:
            return None
        dest = self._location(jid) / "source.frames.jsonl"
        if dest.is_file():
            if sha256_file(dest) != job.get("frame_manifest_sha256"):
                raise RuntimeError("Source frame sidecar integrity mismatch")
            return dest
        src = Path(manifest)
        if not src.is_file() or src.stat().st_size > 256 * 1024**2:
            raise FileNotFoundError("Camera frame identity sidecar unavailable")
        part = dest.with_suffix(".jsonl.part")
        try:
            shutil.copyfile(src, part)
            digest = sha256_file(part)
            os.replace(part, dest)
        finally:
            part.unlink(missing_ok=True)
        self._update(jid, frame_manifest_sha256=digest)
        return dest

    def _validated_cache(self, folder: Path, expected_count: int):
        meta = folder / "chunk.json"
        if not meta.is_file():
            return None
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
            if (d["frames_count"] != expected_count or d["clip_index"] < 0
                    or len(d["frames"]) != expected_count):
                return None
            start = d["frames"][0]["video_frame_index"]
            if [r["video_frame_index"] for r in d["frames"]] != list(range(start, start + expected_count)):
                return None
            if sha256_file(folder / "response.zip") != d["zip_sha256"]:
                return None
            for row in d["frames"]:
                for obj in row["objects"]:
                    p = folder / obj["mask_relative"]
                    if not p.is_file() or sha256_file(p) != obj["sha256"]:
                        return None
            return d
        except (KeyError, ValueError, OSError, TypeError):
            return None

    def _consume_chunk(self, jid, clip_index, frames, mp4_path, shape):
        target = self._location(jid)
        completed = target / "chunks" / f"chunk_{clip_index:05d}"
        cache = self._validated_cache(completed, len(frames))
        if cache is not None:
            return cache
        if completed.exists():
            shutil.rmtree(completed)
        working = target / "chunks" / f"chunk_{clip_index:05d}.working"
        if working.exists():
            shutil.rmtree(working)
        working.mkdir(parents=True)
        try:
            raw = self.transport.batch(str(mp4_path), self.status(jid)["prompt"], len(frames))
            metadata, decoded = parse_mask_zip(raw, len(frames), shape)
            (working / "response.zip").write_bytes(raw)
            results = []
            for local_i, ref in enumerate(frames):
                objects = []
                for item in decoded[local_i]:
                    name = f"f{local_i:04d}_o{item.object_id}.png"
                    ok, png = cv2.imencode(".png", item.mask.astype(np.uint8) * 255)
                    if not ok:
                        raise OSError("Could not encode local mask")
                    (working / name).write_bytes(png.tobytes())
                    objects.append({"object_id": item.object_id, "score": item.score,
                                    "mask_relative": name,
                                    "sha256": hashlib.sha256(png.tobytes()).hexdigest(),
                                    "mask_shape": [shape[0], shape[1]]})
                results.append({**ref, "chunk_index": clip_index,
                                "clip_frame_index": local_i, "objects": objects,
                                "status": "CANDIDATE" if objects else "NO_OBJECT",
                                "label_status": "CANDIDATE"})
            doc = {"clip_index": clip_index, "frames_count": len(frames),
                   "frames": results, "server_meta": metadata,
                   "zip_sha256": hashlib.sha256(raw).hexdigest(),
                   "model_backend": metadata.get("backend", "sam3"),
                   "object_id_scope": "frame", "temporal_tracking": False}
            _atomic_json(working / "chunk.json", doc)
            os.replace(working, completed)
            return doc
        except Exception:
            shutil.rmtree(working, ignore_errors=True)
            raise

    def _process(self, jid):
        self._update(jid, state="RUNNING", error="")
        self._event("started", job_id=jid)
        video = self._spool_source(jid)
        cv = cv2.VideoCapture(str(video))
        if not cv.isOpened():
            raise RuntimeError("Cannot decode source video")
        shape = None
        clip = []
        chunk_i = 0
        total = 0
        complete_rows = []
        all_masks = 0
        source_h = source_w = 0
        fps = cv.get(cv2.CAP_PROP_FPS)
        sidecar_file = self._location(jid) / "source.frames.jsonl"
        sidecar = sidecar_file.open("r", encoding="utf-8") if sidecar_file.is_file() else None
        last_source_id = -1
        last_source_ts = -1
        if not math.isfinite(fps) or fps <= 0 or fps > 240:
            cv.release()
            if sidecar is not None: sidecar.close()
            raise ValueError("Video FPS metadata missing or unreasonable")
        expected = int(cv.get(cv2.CAP_PROP_FRAME_COUNT))
        if expected > 1000000:
            cv.release()
            if sidecar is not None: sidecar.close()
            raise ValueError("Video frame count exceeds safety bound")
        folder = self._location(jid)
        self._update(jid, frames_total=expected if expected > 0 else None)
        (folder / "chunks").mkdir(exist_ok=True)
        try:
            def flush_chunk():
                nonlocal clip, chunk_i, all_masks
                if not clip:
                    return
                clip_file = folder / "chunks" / f"upload_{chunk_i:05d}.mp4"
                if not clip_file.is_file():
                    raise OSError("Chunk MP4 encoding failed")
                try:
                    doc = self._consume_chunk(jid, chunk_i, [x[0] for x in clip],
                                              clip_file, shape)
                finally:
                    clip_file.unlink(missing_ok=True)
                for row in doc["frames"]:
                    row = dict(row)
                    row["mask_directory"] = f"chunks/chunk_{chunk_i:05d}"
                    all_masks += len(row["objects"])
                    complete_rows.append(row)
                chunk_i += 1
                clip = []
                _atomic_jsonl(folder / "frames.jsonl", complete_rows)
                self._update(jid, frames_processed=len(complete_rows),
                             chunks_completed=chunk_i, masks_total=all_masks)
                self._event("chunk_complete", job_id=jid,
                            completed_frames=len(complete_rows), masks_total=all_masks)

            writer = None
            try:
                while True:
                    if self._stop.is_set():
                        raise RuntimeError("Studio shutting down; resume with sam3.video.retry")
                    ok, frame = cv.read()
                    if not ok:
                        break
                    h, w = frame.shape[:2]
                    if shape is None:
                        source_h, source_w = h, w
                        iw, ih = self._infer_size(w, h, self.max_frame_pixels)
                        shape = (ih, iw)
                        self._update(jid, source_shape=[h, w],
                                     inference_shape=[ih, iw], fps=fps)
                    if (h, w) != (source_h, source_w):
                        raise ValueError("Source video frame dimensions changed")
                    if not clip:
                        target_mp4 = folder / "chunks" / f"upload_{chunk_i:05d}.mp4"
                        writer = cv2.VideoWriter(str(target_mp4),
                                  cv2.VideoWriter_fourcc(*"mp4v"), fps, (shape[1], shape[0]))
                        if not writer.isOpened():
                            raise RuntimeError("MP4 writer unavailable for upload chunk")
                    resized = (cv2.resize(frame, (shape[1], shape[0]),
                                          interpolation=cv2.INTER_AREA)
                               if (w, h) != (shape[1], shape[0]) else frame)
                    writer.write(resized)
                    if sidecar is not None:
                        line = sidecar.readline()
                        if not line:
                            raise ValueError("Frame sidecar shorter than decoded video")
                        marker = json.loads(line)
                        if int(marker.get("video_frame_index", -1)) != total:
                            raise ValueError("Sidecar frame index mismatch")
                        source_id = int(marker["source_frame_id"])
                        source_ts = marker.get("source_timestamp_ns")
                        source_ts = int(source_ts) if source_ts is not None else None
                        if source_id <= last_source_id or (source_ts is not None and source_ts <= last_source_ts):
                            raise ValueError("Non-monotonic sidecar source identity/clock")
                        last_source_id = source_id
                        if source_ts is not None:
                            last_source_ts = source_ts
                    else:
                        source_id, source_ts = total, None
                    clip.append(({"video_frame_index": total,
                                  "source_frame_id": source_id,
                                  "source_timestamp_ns": source_ts,
                                  "video_time_ms": round(total * 1000 / fps, 3),
                                  "original_shape": [h, w],
                                  "inference_shape": list(shape),
                                  "scale_x": shape[1] / w, "scale_y": shape[0] / h,
                                  "coordinate_transform": "resize_no_crop_no_pad_nearest_mask_inverse"},))
                    total += 1
                    if len(clip) == self.chunk_frames:
                        writer.release()
                        writer = None
                        flush_chunk()
                        if self._pause.is_set():
                            self._update(jid, state="PAUSED")
                            self._event("paused", job_id=jid)
                            return
                if writer is not None:
                    writer.release()
                    writer = None
                flush_chunk()
                if sidecar is not None and sidecar.readline():
                    raise ValueError("Frame sidecar longer than decoded video")
            finally:
                if writer is not None:
                    writer.release()
        finally:
            cv.release()
            if sidecar is not None:
                sidecar.close()
        if total == 0:
            raise ValueError("Video contains no decodable frames")
        if expected > 0 and total != expected:
            raise RuntimeError(f"Decoded {total} of {expected} declared frames; refusing incomplete output")
        if len(complete_rows) != total or [r["video_frame_index"] for r in complete_rows] != list(range(total)):
            raise RuntimeError("Noncontiguous frame mapping; upload not complete")
        summary = {"job_id": jid, "input_sha256": self.status(jid)["source_sha256"],
                   "frames_total": total, "frames_processed": total,
                   "chunks_total": chunk_i, "masks_total": all_masks,
                   "fps": fps, "original_shape": [source_h, source_w],
                   "inference_shape": list(shape), "mask_encoding": "png",
                   "source_timestamp_policy": ("copied_external_sidecar_not_verified_sync"
                                               if sidecar_file.is_file() else "video_index_not_camera_clock"),
                   "label_status": "CANDIDATE", "temporal_tracking": False,
                   "object_id_scope": "frame", "video_complete": True}
        _atomic_json(folder / "manifest.json", summary)
        self._update(jid, state="COMPLETED", frames_total=total,
                     frames_processed=total, chunks_completed=chunk_i,
                     masks_total=all_masks, error="")
        self._event("completed", job_id=jid, frames=total, masks=all_masks)

    def get_frame(self, job_id, frame_index):
        """Preview saved remote masks on *original* decoded video pixels.

        Read only; never changes a mask. No fake alignment for missing masks.
        """
        j = self.status(job_id)
        base = self._location(job_id)
        if j["state"] != "COMPLETED":
            raise RuntimeError("Full-video job is not complete")
        idx = int(frame_index)
        if idx < 0 or idx >= int(j["frames_total"]):
            raise IndexError("Video frame out of range")
        with (base / "frames.jsonl").open("r", encoding="utf-8") as f:
            for n, line in enumerate(f):
                if n == idx:
                    row = json.loads(line)
                    break
            else:
                raise RuntimeError("Frame result missing from manifest")
        if row.get("video_frame_index") != idx:
            raise RuntimeError("Frame mapping corruption")
        cap = cv2.VideoCapture(str(base / ("source" + j["source_ext"])))
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, bgr = cap.read()
            if not ok:
                raise RuntimeError("Cannot read original video preview frame")
        finally:
            cap.release()
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        masks = []
        for obj in row["objects"]:
            png = base / row["mask_directory"] / obj["mask_relative"]
            if sha256_file(png) != obj["sha256"]:
                raise RuntimeError("Saved mask hash mismatch")
            mask = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
            if mask is None or list(mask.shape) != row["inference_shape"]:
                raise RuntimeError("Saved mask missing or dimension mismatch")
            native = cv2.resize(mask, (rgb.shape[1], rgb.shape[0]),
                                interpolation=cv2.INTER_NEAREST) > 127
            masks.append(RemoteMask(int(obj["object_id"]), float(obj["score"]), native))
        image = composite_overlay(rgb, masks,
                                  f"Video frame {idx + 1}/{j['frames_total']} | masks {len(masks)}")
        return image, row

    def get_manifest_path(self, job_id):
        j = self.status(job_id)
        if j["state"] != "COMPLETED":
            raise RuntimeError("Video job is not completed")
        path = self._location(job_id) / "manifest.json"
        if not path.is_file():
            raise RuntimeError("Result manifest missing")
        return path

    def get_source_path(self, job_id):
        j = self.status(job_id)
        if j["state"] != "COMPLETED":
            raise RuntimeError("Video job is not completed")
        source = self._location(job_id) / ("source" + j["source_ext"])
        if not source.is_file() or sha256_file(source) != j.get("source_sha256"):
            raise RuntimeError("Source checksum mismatch")
        return source

    def get_results_zip(self, job_id):
        """Downloadable full results: manifest, every mask, frame mapping.

        Source original is offered separately so downloading masks does not
        duplicate multi-gigabyte inputs. ZIP is built on disk, not in RAM.
        """
        self.get_manifest_path(job_id)
        root = self._location(job_id)
        dest = root / "sam3_results.zip"
        # Atomic on-disk packaging. AgentControl may serve previous complete ZIP.
        with self._guard:
            if dest.is_file():
                return dest
            tmp = root / "sam3_results.zip.part"
            try:
                with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED,
                                     compresslevel=1, allowZip64=True) as archive:
                    for name in ("job.json", "manifest.json", "frames.jsonl"):
                        archive.write(root / name, arcname=name)
                    for d in sorted((root / "chunks").glob("chunk_?????")):
                        for p in sorted(d.iterdir()):
                            if p.name != "response.zip" and p.is_file():
                                archive.write(p, arcname=str(p.relative_to(root)).replace("\\", "/"))
                os.replace(tmp, dest)
            finally:
                tmp.unlink(missing_ok=True)
            return dest

    def get_mask_path(self, job_id, frame_index, object_id):
        self.get_manifest_path(job_id)
        idx = int(frame_index)
        oid = int(object_id)
        if idx < 0 or oid < 0:
            raise ValueError("Invalid frame/object id")
        rows = self._location(job_id) / "frames.jsonl"
        with rows.open("r", encoding="utf-8") as f:
            for n, line in enumerate(f):
                if n == idx:
                    row = json.loads(line)
                    break
            else:
                raise KeyError("Frame not found")
        if row["video_frame_index"] != idx:
            raise RuntimeError("Result frame mapping corrupted")
        for obj in row["objects"]:
            if obj["object_id"] == oid:
                path = self._location(job_id) / row["mask_directory"] / obj["mask_relative"]
                if not path.is_file() or sha256_file(path) != obj["sha256"]:
                    raise RuntimeError("Result mask checksum mismatch")
                return path
        raise KeyError("Object not found in frame")

    def close(self):
        self._stop.set()
        self._thread.join(timeout=3)
        return {"worker_stopped": not self._thread.is_alive()}

"""Production Studio live SAM3 client: latest-frame admission, complete masks,
strict frame identity, real HTTP inference and agent-friendly telemetry.

This is *not* 20 FPS full-frame segmentation. Every accepted keyframe is a
separate immutable source frame. Intermediate frames are counted as skipped.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

try:  # Support both `python studio/main.py` and `import studio.sam3_client`.
    from .sam3_transport import (Sam3HTTPTransport, Sam3ProtocolError,
                                 choose_brush_mask, decode_single_json,
                                 resize_for_inference)
except ImportError:
    from sam3_transport import (Sam3HTTPTransport, Sam3ProtocolError,
                                choose_brush_mask, decode_single_json,
                                resize_for_inference)


@dataclass
class SamResult:
    frame_id: int
    host_ns: int
    mask: np.ndarray | None
    score: float = 0.0
    bbox: tuple | None = None
    latency_ms: float = 0.0
    error: str = ""
    prompt: str = ""
    camera_context: object = None
    masks: list = field(default_factory=list)
    frame_status: str = "UNVERIFIED_CANDIDATE"
    sequence: int = 0
    transform: dict = field(default_factory=dict)


def composite_overlay(rgb, masks, label="", alpha=0.42):
    """Return an RGB overlay with *all* frame-local masks in different colours."""
    if rgb is None:
        return None
    image = np.ascontiguousarray(np.asarray(rgb)[:, :, :3].copy())
    colours = ((245, 110, 55), (45, 195, 160), (255, 202, 90),
               (150, 125, 235), (224, 108, 179))
    for i, item in enumerate(masks):
        mask = item.mask if hasattr(item, "mask") else item
        if mask is None or mask.shape != image.shape[:2]:
            continue
        colour = np.asarray(colours[i % len(colours)], np.float32)
        selected = image[mask]
        image[mask] = np.clip(selected * (1 - alpha) + colour * alpha, 0, 255).astype(np.uint8)
        border = cv2.Canny(mask.astype(np.uint8) * 255, 50, 150) > 0
        image[border] = colour.astype(np.uint8)
    if label:
        cv2.putText(image, label[:90], (12, 26), cv2.FONT_HERSHEY_SIMPLEX,
                    .58, (255, 255, 255), 2, cv2.LINE_AA)
    return image


class SAM3Client:
    def __init__(self, base_url: str, api_key: str = "", prompt: str = "calligraphy brush",
                 jpeg_quality: int = 88, timeout_s: float = 180.,
                 clip_frames: int = 30, clip_fps: int = 30,
                 min_interval_s: float = 1., runtime_dir=None,
                 max_frame_pixels: int = 307200, transport=None,
                 max_live_files: int = 500, min_score: float = 0.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.prompt = str(prompt)
        self.jpeg_quality = int(jpeg_quality)  # legacy UI setting, PNG avoids loss
        self.timeout = float(timeout_s)
        self.clip_frames = int(clip_frames)  # also used for queued full-video chunks
        self.clip_fps = int(clip_fps)
        self.min_interval = max(.05, float(min_interval_s))
        self.max_frame_pixels = int(max_frame_pixels)
        self.min_score = max(0.0, min(1.0, float(min_score)))
        self.transport = transport or Sam3HTTPTransport(base_url, api_key, timeout_s)
        self.runtime_dir = Path(runtime_dir) if runtime_dir else None
        self.max_live_files = max(1, int(max_live_files))
        self._event_sink = None
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._pending = None  # latest *accepted* frame only
        self._result: SamResult | None = None
        self._next_allowed = 0.
        self._sequence = 0
        self._enabled = True
        self._stats = dict(received=0, accepted=0, rate_skipped=0, replaced=0,
                           completed=0, failed=0, masks_saved=0,
                           mask_files_pruned=0, last_error="",
                           last_frame_id=-1, last_latency_ms=None,
                           last_completed_monotonic_ns=None)
        if self.runtime_dir:
            (self.runtime_dir / "live_masks").mkdir(parents=True, exist_ok=True)
            self._records_file = self.runtime_dir / "live_results.jsonl"
        else:
            self._records_file = None
        self.thread = threading.Thread(target=self._run, name="SAM3LiveWorker", daemon=True)
        self.thread.start()

    def set_event_sink(self, callback):
        self._event_sink = callback

    def _event(self, name, **metadata):
        if self._event_sink:
            try:
                self._event_sink(name, **metadata)
            except Exception:
                pass

    def health(self):
        return self.transport.health()

    def set_enabled(self, enabled: bool):
        with self._lock:
            self._enabled = bool(enabled)
            if not enabled:
                self._pending = None
            return {"enabled": self._enabled}

    def reset_error(self):
        with self._lock:
            self._stats["last_error"] = ""
            return self.status()

    def status(self):
        with self._lock:
            d = dict(self._stats)
            d.update(enabled=self._enabled, pending=int(self._pending is not None),
                     busy=bool(self._busy), min_interval_s=self.min_interval,
                     prompt=self.prompt, transport="http-single-keyframes",
                     object_id_scope="frame", temporal_tracking=False,
                     session_policy="stateless_per_frame",
                     # no mask bytes/base64 in agent state
                     output_dir=str(self.runtime_dir) if self.runtime_dir else None,
                     remote_request=self.transport.status() if hasattr(self.transport, "status") else {})
            return d

    _busy = False

    @staticmethod
    def _snapshot_context(context, rgb):
        """Copy the specific camera data needed by vision validation only."""
        if context is None:
            return {"rgb": rgb.copy()}
        if not isinstance(context, dict):
            raise ValueError("Camera context must be a dictionary")
        ctx = {k: v for k, v in context.items()
               if k not in ("rgb", "pointcloud", "depth_mm")}
        ctx["rgb"] = rgb.copy()
        for key in ("pointcloud", "depth_mm"):
            if context.get(key) is not None:
                ctx[key] = np.asarray(context[key]).copy()
        return ctx

    def submit(self, rgb: np.ndarray, frame_id: int, host_ns: int, camera_context=None):
        """Nonblocking latest-frame selection. Never stores an unbounded video buffer."""
        if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError("Expected uint8 RGB camera frame")
        frame_id, host_ns = int(frame_id), int(host_ns)
        if frame_id < 0 or host_ns <= 0:
            raise ValueError("Camera frame ID and timestamp must be positive")
        now = time.monotonic()
        with self._lock:
            self._stats["received"] += 1
            if self._stop.is_set() or not self._enabled or now < self._next_allowed:
                self._stats["rate_skipped"] += 1
                return False
            self._next_allowed = now + self.min_interval
            self._stats["accepted"] += 1
            if self._pending is not None:
                self._stats["replaced"] += 1
            self._pending = (rgb.copy(), frame_id, host_ns,
                             str(self.prompt), self._snapshot_context(camera_context, rgb))
        self._wake.set()
        return True

    def latest(self):
        with self._lock:
            return self._result

    def _save_result(self, result: SamResult):
        if not self.runtime_dir:
            return
        paths = []
        out = self.runtime_dir / "live_masks"
        # Timestamps prevent reconnection/reset frame IDs overwriting previous masks.
        stamp = str(time.time_ns())
        for item in result.masks:
            path = out / f"f{result.frame_id}_{stamp}_o{item.object_id}.png"
            ok, encoded = cv2.imencode(".png", item.mask.astype(np.uint8) * 255)
            if not ok:
                raise OSError("Cannot encode local SAM3 result mask")
            path.write_bytes(encoded.tobytes())
            paths.append({"object_id": item.object_id, "score": item.score,
                          "mask_file": path.name})
        record = {"event": "live_result", "frame_id": result.frame_id,
                  "host_ns": result.host_ns, "status": result.frame_status,
                  "objects": paths, "latency_ms": result.latency_ms,
                  "transform": result.transform, "prompt": result.prompt,
                  "error": result.error, "model_backend": "hf_sam3",
                  "temporal_tracking": False, "object_id_scope": "frame",
                  "label_status": "CANDIDATE", "saved_at_ns": int(stamp),
            "live_mask_retention": f"latest_{self.max_live_files}_files"}
        with self._records_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        with self._lock:
            self._stats["masks_saved"] += len(paths)
        # Local retention is bounded; archived full-video jobs are NOT pruned here.
        images = sorted(out.glob("f*_o*.png"), key=lambda p: p.stat().st_mtime_ns)
        pruned = 0
        for old in images[:-self.max_live_files]:
            try:
                old.unlink()
                pruned += 1
            except OSError: pass
        if pruned:
            with self._lock:
                self._stats["mask_files_pruned"] += pruned
            self._event("sam3.live.mask_retention_pruned", count=pruned)

    def _run(self):
        while not self._stop.is_set():
            self._wake.wait(.25)
            self._wake.clear()
            with self._lock:
                job, self._pending = self._pending, None
                if job is not None:
                    self._busy = True
            if job is None:
                continue
            rgb, fid, hns, prompt, context = job
            started = time.perf_counter()
            masks = []
            transform = {}
            error = ""
            try:
                infer, transform = resize_for_inference(rgb, self.max_frame_pixels)
                bgr = cv2.cvtColor(infer, cv2.COLOR_RGB2BGR)
                ok, encoded = cv2.imencode(".png", bgr)
                if not ok:
                    raise RuntimeError("PNG encoding failed")
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp.write(encoded.tobytes())
                    path = Path(tmp.name)
                try:
                    raw = self.transport.single(str(path), prompt)
                    if len(raw) > 32 * 1024 * 1024:
                        raise Sam3ProtocolError("Single JSON response exceeds 32MB")
                    data = json.loads(raw)
                    masks = decode_single_json(data, infer.shape[:2], rgb.shape[:2])
                finally:
                    path.unlink(missing_ok=True)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            mask, score, bbox = choose_brush_mask(masks, self.min_score)
            status = "ERROR" if error else ("CANDIDATE" if masks else "NO_OBJECT")
            with self._lock:
                self._sequence += 1
                res = SamResult(fid, hns, mask, score, bbox,
                                round((time.perf_counter() - started) * 1000, 2),
                                error, prompt, context, masks, status,
                                self._sequence, transform)
                self._result = res
                self._stats["completed" if not error else "failed"] += 1
                self._stats["last_frame_id"] = fid
                self._stats["last_latency_ms"] = res.latency_ms
                self._stats["last_completed_monotonic_ns"] = time.monotonic_ns()
                self._stats["last_error"] = error
                self._busy = False
            try:
                self._save_result(res)
            except Exception as exc:
                with self._lock:
                    self._stats["last_error"] = f"LOCAL_ARCHIVE_ERROR: {type(exc).__name__}: {exc}"
                self._event("sam3.live.archive_error", frame_id=fid, error=str(exc))
            self._event("sam3.live.result", frame_id=fid, status=status,
                        objects=len(masks), latency_ms=res.latency_ms, error=error)

    def close(self):
        self._stop.set()
        self._wake.set()
        self.thread.join(timeout=3.0)
        return {"worker_stopped": not self.thread.is_alive(), **self.status()}

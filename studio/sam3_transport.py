"""SAM3 v4 wire contracts, bounded decoding and real image-coordinate provenance.

All remote operations are explicit; this module never creates a SAM3 model.
Input images are RGB. OpenCV upload data is BGR. Masks are always boolean in
*source* image coordinates. Object IDs are only valid within a source frame.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
import threading
from urllib.parse import urlsplit
import zipfile

import cv2
import numpy as np
import requests


class Sam3ProtocolError(ValueError):
    """Remote response is incomplete, corrupt, ambiguous, or unsafe to use."""


MASK_NAME = re.compile(r"frame_(\d+)_obj_(\d+)(?:_score([0-9]+(?:\.[0-9]+)?))?\.png\Z")
MAX_MASK_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 100 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024


def validate_base_url(url: str, key: str = "") -> str:
    p = urlsplit(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError("SAM3 base URL must be an http(s) URL without credentials, query or fragment")
    if p.scheme == "http" and p.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Remote SAM3 access requires HTTPS or a localhost tunnel")
    if p.hostname not in ("127.0.0.1", "localhost", "::1") and not key:
        raise ValueError("Non-local SAM3 requires authentication")
    return url.rstrip("/")


def resize_for_inference(rgb: np.ndarray, max_pixels: int = 307200) -> tuple[np.ndarray, dict]:
    a = np.asarray(rgb)
    if a.dtype != np.uint8 or a.ndim != 3 or a.shape[2] != 3 or not a.size:
        raise ValueError("Expected nonempty HxWx3 uint8 RGB")
    h, w = a.shape[:2]
    if w * h > max_pixels:
        scale = math.sqrt(max_pixels / (w * h))
        rw, rh = max(1, int(w * scale)), max(1, int(h * scale))
        while rw * rh > max_pixels:
            if rw >= rh: rw -= 1
            else: rh -= 1
        resized = cv2.resize(a, (rw, rh), interpolation=cv2.INTER_AREA)
    else:
        rh, rw, resized = h, w, a
    return resized, {
        "original_shape": [h, w], "inference_shape": [rh, rw],
        "scale_x": rw / w, "scale_y": rh / h,
        "coordinate_transform": "resize_no_crop_no_pad_nearest_mask_inverse",
    }


def _extract_png(data: str) -> np.ndarray:
    if not isinstance(data, str) or not data or len(data) > MAX_MASK_BYTES * 2:
        raise Sam3ProtocolError("Invalid/oversized PNG mask base64")
    if data.startswith("data:"):
        if not data.startswith("data:image/png;base64,"):
            raise Sam3ProtocolError("Unsupported mask data URI")
        data = data.split(",", 1)[1]
    try:
        b = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise Sam3ProtocolError("Invalid mask base64") from exc
    if len(b) > MAX_MASK_BYTES:
        raise Sam3ProtocolError("PNG mask byte cap exceeded")
    m = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_GRAYSCALE)
    if m is None:
        raise Sam3ProtocolError("Cannot decode mask PNG")
    return m > 127


def _valid_score(value) -> float:
    try:
        score = float(value)
    except (ValueError, TypeError) as exc:
        raise Sam3ProtocolError("Mask score is missing/non-numeric") from exc
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise Sam3ProtocolError("Mask score must be finite in [0,1]")
    return score


@dataclass
class RemoteMask:
    object_id: int
    score: float
    mask: np.ndarray


def decode_single_json(payload: dict, inference_shape: tuple[int, int],
                       original_shape: tuple[int, int]) -> list[RemoteMask]:
    """Reject unknown masks rather than returning a misleading successful blank result."""
    if not isinstance(payload, dict) or payload.get("success") is False:
        raise Sam3ProtocolError("SAM3 reported an invalid/failed single result")
    root = payload.get("result", payload)
    if not isinstance(root, dict):
        raise Sam3ProtocolError("Single result is not an object")
    objects = root.get("objects")
    if objects is None:
        objects = root.get("masks")
    if objects is None:
        if "mask_png_base64" in root:
            objects = [root]
        else:
            raise Sam3ProtocolError("Single response has no recognized mask field")
    if not isinstance(objects, list) or len(objects) > 64:
        raise Sam3ProtocolError("Invalid count of detected objects")
    result = []
    used = set()
    for i, item in enumerate(objects):
        if not isinstance(item, dict):
            raise Sam3ProtocolError("Mask entry is not an object")
        obj_id = int(item.get("object_id", i))
        if obj_id < 0 or obj_id in used:
            raise Sam3ProtocolError("Duplicate/negative object ID")
        used.add(obj_id)
        png = item.get("mask_png_base64", item.get("png_base64"))
        if png is None and isinstance(item.get("mask"), dict):
            png = item["mask"].get("data", item["mask"].get("mask_png_base64"))
        if png is None:
            raise Sam3ProtocolError("Unsupported mask encoding (expected PNG base64)")
        mask = _extract_png(png)
        if mask.shape == inference_shape:
            mask = cv2.resize(mask.astype(np.uint8), (original_shape[1], original_shape[0]),
                              interpolation=cv2.INTER_NEAREST) > 0
        elif mask.shape != original_shape:
            raise Sam3ProtocolError(f"Mask size {mask.shape} matches neither inference nor source image")
        score = _valid_score(item.get("score", item.get("confidence", root.get("score"))))
        result.append(RemoteMask(obj_id, score, mask))
    return result


def choose_brush_mask(masks: list[RemoteMask]):
    """Choose one candidate for legacy shaft estimation; retain all masks separately."""
    best = max((m for m in masks if np.any(m.mask)),
               key=lambda m: (m.score, int(m.mask.sum())), default=None)
    if best is None:
        return None, 0.0, None
    ys, xs = np.nonzero(best.mask)
    return best.mask, best.score, (int(xs.min()), int(ys.min()),
                                  int(xs.max()) + 1, int(ys.max()) + 1)


def _bounded_response(resp, cap=MAX_RESPONSE_BYTES) -> bytes:
    data = bytearray()
    for chunk in resp.iter_content(chunk_size=1024 * 1024):
        if chunk:
            data.extend(chunk)
            if len(data) > cap:
                raise Sam3ProtocolError("SAM3 response exceeds configured byte limit")
    return bytes(data)


class Sam3HTTPTransport:
    def __init__(self, base_url: str, api_key: str = "", timeout_s: float = 180.0,
                 retries: int = 2, session_factory=requests.Session):
        self.base_url = validate_base_url(base_url, api_key)
        self.api_key = api_key
        self.timeout_s = max(2.0, float(timeout_s))
        self.retries = max(0, min(int(retries), 3))
        self._session_factory = session_factory
        # Studio never competes against itself for the service's one GPU lease.
        self._request_lock = threading.Lock()
        self._active_mode = None
        self._last_request_ms = None

    def status(self):
        return {"request_active": self._request_lock.locked(),
                "active_mode": self._active_mode,
                "last_request_ms": self._last_request_ms}

    def headers(self):
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def health(self):
        with self._session_factory() as s:
            s.trust_env = False
            r = s.get(self.base_url + "/health/ready", headers=self.headers(), timeout=(4, 6))
            r.raise_for_status()
            return r.json()

    def capabilities(self):
        with self._session_factory() as s:
            s.trust_env = False
            r = s.get(self.base_url + "/v1/capabilities", headers=self.headers(), timeout=(4, 6))
            r.raise_for_status()
            return r.json()

    def _post(self, path: str, file_path: str, fields: dict) -> tuple[bytes, str]:
        """Retry transient errors; uploads are limited to one encoded clip at a time.

        `requests(files=...)` may buffer the multipart clip; never pass the
        entire multi-GB source file, only a bounded numbered MP4 chunk.
        Received responses are always streamed under a strict byte cap.
        """
        last = None
        for attempt in range(self.retries + 1):
            try:
                with self._session_factory() as s:
                    s.trust_env = False
                    with open(file_path, "rb") as src:
                        with s.post(self.base_url + path, headers=self.headers(),
                                    data=fields,
                                    files={"file": (Path(file_path).name, src,
                                                    "video/mp4" if file_path.lower().endswith(".mp4") else "image/png")},
                                    timeout=(5, self.timeout_s), stream=True) as r:
                            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                                last = RuntimeError(f"SAM3 temporary HTTP {r.status_code}")
                            else:
                                r.raise_for_status()
                                return _bounded_response(r), r.headers.get("Content-Type", "")
            except (requests.Timeout, requests.ConnectionError) as exc:
                last = exc
                if attempt >= self.retries:
                    raise
            if attempt < self.retries:
                time.sleep(min(2.0 ** attempt, 4.0))
        raise last or RuntimeError("SAM3 POST failed")

    def _serialize(self, mode, path, filename, fields):
        with self._request_lock:
            start = time.perf_counter()
            self._active_mode = mode
            try:
                return self._post(path, filename, fields)[0]
            finally:
                self._last_request_ms = round((time.perf_counter() - start) * 1000, 2)
                self._active_mode = None

    def single(self, png_path: str, prompt: str) -> bytes:
        return self._serialize("single", "/v1/segment", png_path,
                               {"mode": "single", "text_prompt": prompt})

    def batch(self, mp4_path: str, prompt: str, frames: int) -> bytes:
        return self._serialize("batch", "/video/segment?batch_mode=true", mp4_path,
                               {"mode": "batch", "text_prompt": prompt,
                                "max_frames": str(frames), "frame_stride": "1",
                                "return_masks_png": "true"})


def parse_mask_zip(raw: bytes, expected_frames: int, shape: tuple[int, int]):
    """Strict ZIP parser: ordered *clip-relative* frame index, all object masks.

    Returns `(metadata, {clip_frame_idx: [RemoteMask]})` including empty frames.
    If a server silently skips a frame, fail rather than mislabel the next frame.
    """
    if len(raw) > MAX_RESPONSE_BYTES:
        raise Sam3ProtocolError("Response ZIP too large")
    if expected_frames < 1 or expected_frames > 400:
        raise ValueError("Chunk length must be between 1 and 400")
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise Sam3ProtocolError("Invalid response ZIP") from exc
    with z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if (len(infos) > expected_frames * 64 + 8 or len(set(names)) != len(names)
                or sum(x.file_size for x in infos) > MAX_UNCOMPRESSED_BYTES):
            raise Sam3ProtocolError("Oversized or duplicate ZIP entries")
        if "meta.json" not in names or z.getinfo("meta.json").file_size > 3 * 1024 * 1024:
            raise Sam3ProtocolError("Missing/oversized ZIP metadata")
        try:
            metadata = json.loads(z.read("meta.json"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise Sam3ProtocolError("Invalid ZIP metadata JSON") from exc
        if not isinstance(metadata, dict) or metadata.get("success") is False:
            raise Sam3ProtocolError("SAM3 ZIP reports failure")
        if metadata.get("num_frames") != expected_frames:
            raise Sam3ProtocolError("ZIP num_frames does not match uploaded chunk")
        for k in ("frame_indices", "processed_frame_indices"):
            if k in metadata and metadata[k] != list(range(expected_frames)):
                raise Sam3ProtocolError(f"SAM3 returned non-contiguous {k}")
        scores = {}
        rows = metadata.get("frame_results")
        if rows is not None:
            if not isinstance(rows, list) or len(rows) != expected_frames:
                raise Sam3ProtocolError("ZIP frame_results count mismatch")
            for i, row in enumerate(rows):
                if row.get("frame_index") != i:
                    raise Sam3ProtocolError("ZIP frame_result index mismatch")
                for obj in row.get("objects", []):
                    key = (i, int(obj["object_id"]))
                    if key in scores:
                        raise Sam3ProtocolError("Duplicate metadata object")
                    scores[key] = _valid_score(obj.get("score"))
        results = {i: [] for i in range(expected_frames)}
        seen = set()
        for info in infos:
            n = info.filename
            if n == "meta.json":
                continue
            if "/" in n or "\\" in n or n.startswith(".") or info.is_dir():
                raise Sam3ProtocolError("Unexpected or unsafe ZIP member path")
            match = MASK_NAME.fullmatch(n)
            if not match:
                raise Sam3ProtocolError(f"Unknown SAM3 ZIP member: {n}")
            i, obj = int(match.group(1)), int(match.group(2))
            if i >= expected_frames or (i, obj) in seen:
                raise Sam3ProtocolError("Out-of-range/duplicate mask identity")
            seen.add((i, obj))
            if info.file_size > MAX_MASK_BYTES:
                raise Sam3ProtocolError("Individual mask exceeds byte cap")
            m = cv2.imdecode(np.frombuffer(z.read(n), np.uint8), cv2.IMREAD_GRAYSCALE)
            if m is None or m.shape != shape:
                raise Sam3ProtocolError(f"Mask {n} does not match frame size {shape}")
            score = scores.get((i, obj), match.group(3))
            results[i].append(RemoteMask(obj, _valid_score(score), m > 127))
        if rows is not None:
            for i, row in enumerate(rows):
                declared = {int(x["object_id"]) for x in row.get("objects", [])
                            if int(x.get("mask_pixels", 1)) > 0}
                if declared != {x.object_id for x in results[i]}:
                    raise Sam3ProtocolError(f"Mask and metadata disagree in frame {i}")
    return metadata, results


def sha256_file(path):
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()

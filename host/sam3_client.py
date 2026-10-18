from __future__ import annotations
import io, json, os, queue, threading, time, zipfile, tempfile
from dataclasses import dataclass
import cv2, numpy as np, requests

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

class SAM3Client:
    """
    Asynchronous SAM3 video segmentation client adapted for real-time use.
    Buffers frames into short clips and processes via /video/segment endpoint.
    """
    def __init__(self, base_url: str, api_key: str = "", prompt: str = "calligraphy brush",
                 jpeg_quality: int = 88, timeout_s: float = 30.0,
                 clip_frames: int = 30, clip_fps: int = 30,
                 min_interval_s: float = 0.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.prompt = prompt
        self.jpeg_quality = int(jpeg_quality)
        self.timeout = float(timeout_s)
        self.clip_frames = int(clip_frames)
        self.clip_fps = int(clip_fps)
        self.min_interval = float(min_interval_s)

        self._pending_frames: list[tuple[np.ndarray, int, int, str, object]] = []
        self._lock = threading.Lock()
        self._result: SamResult | None = None
        self._stop = False
        self._wake = threading.Event()
        self._last_call = 0.0
        self._frame_buffer: list[tuple[np.ndarray, int, int, str, object]] = []

        self.thread = threading.Thread(target=self._run, name="SAM3Worker", daemon=True)
        self.thread.start()

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def health(self):
        r = requests.get(self.base_url + "/health", headers=self._headers(), timeout=min(5, self.timeout))
        r.raise_for_status()
        return r.json()

    def submit(self, rgb: np.ndarray, frame_id: int, host_ns: int, camera_context: object = None):
        """Submit a frame for segmentation. Frames are buffered into clips."""
        with self._lock:
            self._frame_buffer.append((np.asarray(rgb).copy(), int(frame_id), int(host_ns), str(self.prompt), camera_context))

    def latest(self):
        with self._lock:
            return self._result

    @staticmethod
    def _decode_zip_masks(content: bytes, shape: tuple[int, int]):
        masks = []
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            for n in z.namelist():
                if not n.lower().endswith(".png"):
                    continue
                a = np.frombuffer(z.read(n), np.uint8)
                m = cv2.imdecode(a, cv2.IMREAD_GRAYSCALE)
                if m is None:
                    continue
                score = 0.0
                try:
                    s = n.rsplit("score", 1)[1].rsplit(".png", 1)[0]
                    score = float(s)
                except:
                    pass
                masks.append((m > 0, score, n))
        return masks

    @staticmethod
    def _pick(masks: list, prev_mask: np.ndarray | None = None):
        if not masks:
            return None, 0.0, None
        best = None
        bestv = -1e9
        for m, score, n in masks:
            ys, xs = np.nonzero(m)
            if len(xs) < 20:
                continue
            w = max(1, xs.max() - xs.min() + 1)
            h = max(1, ys.max() - ys.min() + 1)
            elong = max(w, h) / max(1, min(w, h))
            area = float(m.sum())
            iou = 0.0
            if prev_mask is not None and prev_mask.shape == m.shape:
                inter = np.logical_and(m, prev_mask).sum()
                union = np.logical_or(m, prev_mask).sum()
                iou = float(inter / max(1, union))
            v = float(score) + 0.05 * min(elong, 8) / 8 + 0.20 * iou
            if v > bestv:
                bestv = v
                best = (m, score, (int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)))
        return best if best else (None, 0.0, None)

    def _write_clip_to_temp(self, frames: list[np.ndarray]) -> str:
        """Write frames to a temporary MP4 file."""
        if not frames:
            return ""
        h, w = frames[0].shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        tmp = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        tmp_path = tmp.name
        tmp.close()
        writer = cv2.VideoWriter(tmp_path, fourcc, self.clip_fps, (w, h))
        for frame in frames:
            writer.write(frame)
        writer.release()
        return tmp_path

    def _run(self):
        prev_mask = None
        while not self._stop:
            self._wake.wait(0.2)
            self._wake.clear()

            # Collect frames for a clip
            with self._lock:
                frames_to_process = self._frame_buffer[-self.clip_frames:] if self._frame_buffer else []
                self._frame_buffer.clear()

            if not frames_to_process:
                continue

            wait = self.min_interval - (time.perf_counter() - self._last_call)
            if wait > 0:
                time.sleep(wait)

            t0 = time.perf_counter()
            clip_path = ""
            try:
                # Write clip to temp file
                frame_arrays = [f[0] for f in frames_to_process]
                clip_path = self._write_clip_to_temp(frame_arrays)

                # Submit to SAM3 /video/segment
                with open(clip_path, 'rb') as f:
                    files = {'file': (os.path.basename(clip_path), f, 'video/mp4')}
                    data = {
                        'text_prompt': frames_to_process[-1][3],  # Use latest prompt
                        'max_frames': str(self.clip_frames),
                        'frame_stride': '1',
                        'return_masks_png': 'true'
                    }
                    r = requests.post(
                        self.base_url + "/video/segment",
                        headers=self._headers(),
                        files=files,
                        data=data,
                        timeout=self.timeout
                    )
                    r.raise_for_status()

                # Parse ZIP response
                masks = self._decode_zip_masks(r.content, frame_arrays[0].shape[:2])

                # Get result for the latest frame (last in clip)
                if masks:
                    # The ZIP contains masks for all frames in order
                    # We want the last frame's best mask
                    mask, score, bbox = self._pick(masks, prev_mask)
                    prev_mask = mask if mask is not None else prev_mask

                    # Use metadata from the last frame in the clip
                    _, fid, hns, prompt, camera_context = frames_to_process[-1]
                    res = SamResult(
                        fid, hns, mask, score, bbox,
                        (time.perf_counter() - t0) * 1000, "", prompt, camera_context
                    )
                else:
                    _, fid, hns, prompt, camera_context = frames_to_process[-1]
                    res = SamResult(
                        fid, hns, None, 0, None,
                        (time.perf_counter() - t0) * 1000,
                        "No valid masks returned", prompt, camera_context
                    )

            except Exception as e:
                if frames_to_process:
                    _, fid, hns, prompt, camera_context = frames_to_process[-1]
                else:
                    fid, hns, prompt, camera_context = -1, int(time.perf_counter_ns()), self.prompt, None
                res = SamResult(
                    fid, hns, None, 0, None,
                    (time.perf_counter() - t0) * 1000,
                    f"{type(e).__name__}: {e}", prompt, camera_context
                )
            finally:
                if clip_path and os.path.exists(clip_path):
                    try:
                        os.unlink(clip_path)
                    except:
                        pass

            self._last_call = time.perf_counter()
            with self._lock:
                self._result = res

    def close(self):
        self._stop = True
        self._wake.set()
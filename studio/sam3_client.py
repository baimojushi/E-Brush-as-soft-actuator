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
    paper_mask: np.ndarray | None = None
    paper_score: float = 0.0
    masks: list = None  # all decoded binary masks for this result

class SAM3Client:
    """
    Asynchronous SAM3 video segmentation client adapted for real-time use.
    Buffers frames into short clips and processes via /video/segment endpoint.
    """
    def __init__(self, base_url: str, api_key: str = "", prompt: str = "calligraphy brush",
                 jpeg_quality: int = 88, timeout_s: float = 30.0,
                 clip_frames: int = 30, clip_fps: int = 30,
                 min_interval_s: float = 0.0,
                 runtime_dir: "Path | None" = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.prompt = prompt
        self.jpeg_quality = int(jpeg_quality)
        self.timeout = float(timeout_s)
        self.clip_frames = int(clip_frames)
        self.clip_fps = int(clip_fps)
        self.min_interval = float(min_interval_s)
        self.runtime_dir = runtime_dir

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
        """Submit a frame for segmentation. Frames are buffered into clips. Returns True if accepted."""
        with self._lock:
            self._frame_buffer.append((np.asarray(rgb).copy(), int(frame_id), int(host_ns), str(self.prompt), camera_context))
            if len(self._frame_buffer) > self.clip_frames:
                self._replaced_count = getattr(self, "_replaced_count", 0) + 1
                self._frame_buffer = self._frame_buffer[-self.clip_frames:]
            self._accepted_count = getattr(self, "_accepted_count", 0) + 1
        self._wake.set()
        return True

    def status(self):
        with self._lock:
            return {
                "pending": len(self._frame_buffer),
                "accepted": getattr(self, "_accepted_count", 0),
                "completed": getattr(self, "_completed_count", 0),
                "replaced": getattr(self, "_replaced_count", 0),
                "masks_saved": getattr(self, "_masks_saved", 0),
            }

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

    @staticmethod
    def _pick_brush(masks: list, prev_mask: np.ndarray | None = None):
        """挑选毛笔掩码：偏好细长形状（长宽比 > 3）。"""
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
            iou = 0.0
            if prev_mask is not None and prev_mask.shape == m.shape:
                inter = np.logical_and(m, prev_mask).sum()
                union = np.logical_or(m, prev_mask).sum()
                iou = float(inter / max(1, union))
            # 毛笔偏好：细长 + 高分 + 时序一致
            v = float(score) + 0.10 * min(elong, 8) / 8 + 0.20 * iou
            # 纸面通常很大（面积 > 40% 画面），排除
            total_area = m.size if m.size else 1
            if m.sum() / total_area > 0.4:
                v -= 0.5  # 降权
            if v > bestv:
                bestv = v
                best = (m, score, (int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)))
        return best if best else (None, 0.0, None)

    @staticmethod
    def _pick_paper(masks: list):
        """挑选纸掩码：偏好大面积（纸面通常占画面大部分）。"""
        if not masks:
            return None, 0.0
        best = None
        best_score = -1e9
        for m, score, n in masks:
            ys, xs = np.nonzero(m)
            if len(xs) < 50:
                continue
            area = int(m.sum())
            total_area = m.size if m.size else 1
            fraction = area / total_area
            # 纸面特征：面积大（> 20% 画面）
            if fraction < 0.15:
                continue
            w = max(1, xs.max() - xs.min() + 1)
            h = max(1, ys.max() - ys.min() + 1)
            elong = max(w, h) / max(1, min(w, h))
            # 纸面通常近似矩形/方形，长宽比 < 5
            if elong > 5:
                continue
            v = float(score) + 0.2 * fraction
            if v > best_score:
                best_score = v
                best = m
        if best is None:
            return None, 0.0
        return best, best_score

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
                    # 分离毛笔和纸掩码
                    brush_mask, brush_score, brush_bbox = self._pick_brush(masks, prev_mask)
                    paper_mask, paper_score = self._pick_paper(masks)
                    prev_mask = brush_mask if brush_mask is not None else prev_mask
                    # 只保留有内容（非全 False）的二值掩码
                    all_masks = [m[0] for m in masks if np.any(m[0])]

                    # Use metadata from the last frame in the clip
                    _, fid, hns, prompt, camera_context = frames_to_process[-1]

                    # 保存掩码到 runtime_dir（如果提供）
                    if self.runtime_dir is not None:
                        import pathlib
                        masks_dir = pathlib.Path(self.runtime_dir) / "live_masks"
                        masks_dir.mkdir(parents=True, exist_ok=True)
                        for i, m in enumerate(all_masks):
                            ok, buf = cv2.imencode(".png", (m.astype(np.uint8) * 255))
                            if ok:
                                out_path = masks_dir / f"f{fid:06d}_o{i}.png"
                                out_path.write_bytes(buf.tobytes())
                        with self._lock:
                            self._masks_saved = getattr(self, "_masks_saved", 0) + len(all_masks)
                            self._completed_count = getattr(self, "_completed_count", 0) + 1
                    res = SamResult(
                        fid, hns, brush_mask, brush_score, brush_bbox,
                        (time.perf_counter() - t0) * 1000, "", prompt, camera_context,
                        paper_mask=paper_mask,
                        paper_score=paper_score,
                        masks=all_masks,
                    )
                else:
                    _, fid, hns, prompt, camera_context = frames_to_process[-1]
                    res = SamResult(
                        fid, hns, None, 0, None,
                        (time.perf_counter() - t0) * 1000,
                        "No valid masks returned", prompt, camera_context,
                        masks=[],
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


def composite_overlay(rgb: np.ndarray, masks: list, label: str = "") -> np.ndarray:
    """将 SAM3 掩码叠加到 RGB 图像上（红色半透明）。

    masks: list of RemoteMask (来自 sam3_transport.parse_mask_zip) 或
           list of np.ndarray (二值掩码)。
    """
    import numpy as np
    a = np.asarray(rgb).copy()
    if a.ndim == 3 and a.shape[2] == 4:
        a = a[:, :, :3]
    for m in masks:
        if hasattr(m, "mask"):
            binary = m.mask
        else:
            binary = np.asarray(m, bool)
        if binary.shape[:2] == a.shape[:2]:
            a[binary] = (a[binary] * 0.5 + np.array([255, 0, 0], dtype=np.uint8) * 0.5).astype(np.uint8)
    if label:
        import cv2
        cv2.putText(a, label, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return a
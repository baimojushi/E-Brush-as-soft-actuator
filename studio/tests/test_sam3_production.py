"""Offline executable contract tests with an HTTP stand-in for SAM3 6015.
No GPU, SDK, camera or GUI is required. Real service still needs commissioning.
"""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import zipfile

import cv2
import numpy as np
import pytest
import requests

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from sam3_client import SAM3Client, composite_overlay
from sam3_transport import (Sam3HTTPTransport, Sam3ProtocolError,
                            decode_single_json, parse_mask_zip,
                            resize_for_inference)
from sam3_video_jobs import VideoJobManager
from agent_control import AgentControl


def png_mask(shape, area=(3, 2, 20, 12)):
    m = np.zeros(shape, dtype=np.uint8)
    x0, y0, x1, y1 = area
    m[y0:min(y1, shape[0]), x0:min(x1, shape[1])] = 255
    ok, blob = cv2.imencode(".png", m)
    assert ok
    return blob.tobytes()


def make_result_zip(n, shape=(24, 32)):
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as z:
        rows = [{"frame_index": i,
                 "objects": [{"object_id": 7, "score": .82, "mask_pixels": 170}]}
                for i in range(n)]
        z.writestr("meta.json", json.dumps({"success": True, "num_frames": n,
                   "frame_indices": list(range(n)), "frame_results": rows,
                   "backend": "hf", "text_prompt": "brush"}))
        for i in range(n):
            z.writestr(f"frame_{i:06d}_obj_7_score0.82.png", png_mask(shape))
    return mem.getvalue()


class FakeHandler(BaseHTTPRequestHandler):
    calls = []
    fail_once = False
    request_delay = 0.

    def log_message(self, *args):
        pass

    def do_GET(self):
        b = b'{"status":"ready","backend":"hf-pool"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.__class__.calls.append(self.path)
        if self.__class__.fail_once:
            self.__class__.fail_once = False
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.__class__.request_delay:
            time.sleep(self.__class__.request_delay)
        if self.path.startswith("/v1/segment"):
            encoded = base64.b64encode(png_mask((24,32))).decode("ascii")
            payload = json.dumps({"objects": [{"object_id": 1,
                         "score": .82, "mask_png_base64": encoded},
                        {"object_id": 2, "score": .4,
                         "mask_png_base64": encoded}]}).encode()
            ctype = "application/json"
        elif self.path.startswith("/video/segment"):
            match = re.search(br'name="max_frames"\r\n\r\n(\d+)\r\n', body)
            assert match, "Request must include frame count"
            assert b"ftyp" in body, "Request must upload an encoded MP4"
            payload = make_result_zip(int(match.group(1)))
            ctype = "application/zip"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def remote():
    FakeHandler.calls = []
    FakeHandler.fail_once = False
    FakeHandler.request_delay = 0.
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def wait_until(fn, timeout=10):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if fn():
            return
        time.sleep(.03)
    raise AssertionError("Operation did not finish within test timeout")


def make_video(path, count=7, shape=(24,32), fps=20):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                        fps, (shape[1],shape[0]))
    assert w.isOpened()
    for i in range(count):
        frame = np.full((*shape, 3), (i * 25) % 255, np.uint8)
        w.write(frame)
    w.release()


def test_single_all_objects_exact_frame(remote, tmp_path):
    sam = SAM3Client(remote, prompt="brush", min_interval_s=.05,
                    runtime_dir=tmp_path, timeout_s=5)
    try:
        rgb = np.full((24,32,3), 120, np.uint8)
        assert sam.submit(rgb, 88, 123456, {"rgb": rgb})
        wait_until(lambda: sam.latest() is not None)
        r = sam.latest()
        assert r.frame_id == 88 and r.host_ns == 123456
        assert not r.error, r.error
        assert len(r.masks) == 2 and r.mask.shape == (24,32)
        assert len(list((tmp_path / "live_masks").glob("*.png"))) == 2
        assert sam.status()["completed"] == 1
        assert sam.status()["masks_saved"] == 2
        img = composite_overlay(rgb, r.masks)
        assert img.shape == rgb.shape and np.any(img != rgb)
        assert '/v1/segment' in FakeHandler.calls[0]
    finally:
        sam.close()


def test_live_bounded_admission_and_malformed_reply(remote, tmp_path):
    FakeHandler.request_delay = .15
    sam = SAM3Client(remote, prompt="brush", min_interval_s=.05,
                    runtime_dir=tmp_path, timeout_s=5)
    try:
        rgb = np.full((24,32,3), 100, np.uint8)
        assert sam.submit(rgb, 1, 1)
        time.sleep(.08)
        assert sam.submit(rgb, 2, 2)
        time.sleep(.08)
        assert sam.submit(rgb, 3, 3)
        wait_until(lambda: sam.latest() is not None and sam.latest().frame_id == 3)
        assert sam.status()["pending"] == 0
        assert sam.status()["accepted"] == 3
        assert sam.status()["replaced"] <= 1
    finally:
        sam.close()


def test_strict_zip_rejects_shifted_indices_and_bad_shape():
    raw = make_result_zip(3)
    meta, decoded = parse_mask_zip(raw, 3, (24,32))
    assert [len(decoded[i]) for i in range(3)] == [1,1,1]
    with pytest.raises(Sam3ProtocolError):
        parse_mask_zip(raw, 4, (24,32))
    with pytest.raises(Sam3ProtocolError):
        parse_mask_zip(raw, 3, (20,30))
    bad = {"objects": [{"score": .4, "mask_png_base64": base64.b64encode(png_mask((12,14))).decode()}]}
    with pytest.raises(Sam3ProtocolError):
        decode_single_json(bad, (24,32), (24,32))


def test_frame_inverse_transform_portrait():
    im = np.zeros((2668,4000,3),dtype=np.uint8)
    small, transform = resize_for_inference(im)
    assert small.shape[:2][0] * small.shape[:2][1] <= 307200
    assert transform["original_shape"] == [2668,4000]
    encoded = base64.b64encode(png_mask(small.shape[:2],(3,2,20,12))).decode()
    masks = decode_single_json({"mask_png_base64": encoded, "score": .8},
                               small.shape[:2], im.shape[:2])
    assert masks[0].mask.shape == im.shape[:2]


def test_full_video_chunks_download_and_agent(remote, tmp_path):
    video = tmp_path / "real.mp4"
    make_video(video, 7)
    t = Sam3HTTPTransport(remote, timeout_s=5, retries=0)
    manager = VideoJobManager(tmp_path, t, chunk_frames=3, max_frame_pixels=307200)
    agent = AgentControl(tmp_path / "agent", "127.0.0.1", 0)
    agent.register_artifact_provider(manager)
    agent.start()
    try:
        jid = manager.enqueue(str(video), "brush")["job_id"]
        wait_until(lambda: manager.status(jid)["state"] in ("COMPLETED", "FAILED"), 15)
        status = manager.status(jid)
        assert status["state"] == "COMPLETED", status["error"]
        assert status["frames_processed"] == 7
        assert status["chunks_completed"] == 3
        assert status["masks_total"] == 7
        assert len([x for x in FakeHandler.calls if 'video/segment' in x]) == 3
        rowfile = manager.root / jid / "frames.jsonl"
        rows = [json.loads(x) for x in rowfile.read_text().splitlines()]
        assert [r["source_frame_id"] for r in rows] == list(range(7))
        assert [r["clip_frame_index"] for r in rows] == [0,1,2,0,1,2,0]
        assert all(len(r["objects"]) == 1 for r in rows)
        img, row = manager.get_frame(jid, 6)
        assert img.shape == (24,32,3) and row["source_frame_id"] == 6
        archive = manager.get_results_zip(jid)
        with zipfile.ZipFile(archive) as z:
            assert "frames.jsonl" in z.namelist()
            assert "manifest.json" in z.namelist()
            assert sum(x.endswith('.png') for x in z.namelist()) == 7
        addr = f"http://127.0.0.1:{agent.server.server_address[1]}"
        response = requests.get(addr + f"/v1/sam3/jobs/{jid}/download", timeout=5)
        assert response.status_code == 200 and len(response.content) == archive.stat().st_size
        response = requests.get(addr + f"/v1/sam3/jobs/{jid}/download",
                                headers={"Range": "bytes=0-19"}, timeout=5)
        assert response.status_code == 206 and response.content == archive.read_bytes()[:20]
        assert requests.get(addr + f"/v1/sam3/jobs/{jid}/manifest", timeout=5).status_code == 200
        assert requests.get(addr + f"/v1/sam3/jobs/{jid}/masks/6/7", timeout=5).status_code == 200
        assert requests.get(addr + f"/v1/sam3/jobs/{jid}/source", timeout=5).content == video.read_bytes()
        assert requests.get(addr + "/v1/sam3/jobs/../../etc/passwd", timeout=5).status_code != 200
    finally:
        agent.close()
        manager.close()


def test_failed_video_can_resume_from_saved_chunk(remote, tmp_path):
    video = tmp_path / "source.mp4"
    make_video(video, 5)
    class TemporaryBrokenTransport:
        def __init__(self):
            self.base = Sam3HTTPTransport(remote, timeout_s=5, retries=0)
            self.calls = 0
            self.break_at = 2
        def batch(self, *args):
            self.calls += 1
            if self.calls == self.break_at:
                raise ConnectionError("simulated transient failure")
            return self.base.batch(*args)
    tr = TemporaryBrokenTransport()
    manager = VideoJobManager(tmp_path, tr, chunk_frames=3)
    try:
        jid = manager.enqueue(str(video), "brush")["job_id"]
        wait_until(lambda: manager.status(jid)["state"] == "FAILED", 12)
        assert manager.status(jid)["frames_processed"] == 3
        tr.break_at = -1
        manager.retry(jid)
        wait_until(lambda: manager.status(jid)["state"] in ("COMPLETED", "FAILED"), 12)
        assert manager.status(jid)["state"] == "COMPLETED"
        # First chunk restored from verified checkpoint instead of re-uploading.
        assert tr.calls == 3
        assert manager.status(jid)["frames_processed"] == 5
    finally:
        manager.close()


def test_video_job_rejects_untrusted_source(tmp_path):
    manager = VideoJobManager(tmp_path, Sam3HTTPTransport("http://127.0.0.1:6015"))
    try:
        (tmp_path / "notes.txt").write_text("not video")
        with pytest.raises(ValueError):
            manager.enqueue(str(tmp_path / "notes.txt"))
    finally:
        manager.close()


def test_frame_identity_sidecar_preserved_not_fabricated(remote, tmp_path):
    video = tmp_path / "capture.mp4"
    make_video(video, 4)
    manifest = tmp_path / "capture.mp4.frames.jsonl"
    with manifest.open("w", encoding="utf-8") as f:
        for i in range(4):
            f.write(json.dumps({"video_frame_index":i, "source_frame_id":100+i,
                                "source_timestamp_ns":123000+i*50000}) + "\n")
    mgr = VideoJobManager(tmp_path, Sam3HTTPTransport(remote, timeout_s=5, retries=0), chunk_frames=2)
    try:
        jid = mgr.enqueue(str(video), "brush")["job_id"]
        wait_until(lambda: mgr.status(jid)["state"] in ("COMPLETED", "FAILED"))
        assert mgr.status(jid)["state"] == "COMPLETED", mgr.status(jid)["error"]
        rows = [json.loads(x) for x in (mgr.root / jid / "frames.jsonl").read_text().splitlines()]
        assert [x["source_frame_id"] for x in rows] == [100,101,102,103]
        assert [x["source_timestamp_ns"] for x in rows] == [123000+i*50000 for i in range(4)]
        assert mgr.status(jid)["frame_manifest_sha256"]
        assert (mgr.root / jid / "source.frames.jsonl").read_bytes() == manifest.read_bytes()
        summary = json.loads((mgr.root / jid / "manifest.json").read_text())
        assert summary["source_timestamp_policy"] == "copied_external_sidecar_not_verified_sync"
    finally:
        mgr.close()


def test_sidecar_mismatch_fails_closed(remote, tmp_path):
    video = tmp_path / "broken.mp4"
    make_video(video, 3)
    with (tmp_path / "broken.mp4.frames.jsonl").open("w") as f:
        f.write(json.dumps({"video_frame_index":0,"source_frame_id":10,"source_timestamp_ns":10})+'\n')
        f.write(json.dumps({"video_frame_index":1,"source_frame_id":11,"source_timestamp_ns":20})+'\n')
    mgr = VideoJobManager(tmp_path, Sam3HTTPTransport(remote, timeout_s=5, retries=0), chunk_frames=2)
    try:
        jid = mgr.enqueue(str(video), "brush")["job_id"]
        wait_until(lambda: mgr.status(jid)["state"] == "FAILED")
        assert 'shorter than decoded video' in mgr.status(jid)["error"]
        assert not (mgr.root / jid / "manifest.json").exists()
    finally:
        mgr.close()


def test_agent_refuses_public_unauthenticated_listener(tmp_path):
    ctl = AgentControl(tmp_path, "0.0.0.0", 0)
    with pytest.raises(ValueError, match="loopback"):
        ctl.start()

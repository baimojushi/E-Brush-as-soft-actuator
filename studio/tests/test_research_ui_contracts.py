"""Hardware-free tests for E-Brush-only observation/handoff boundary."""
from __future__ import annotations
import json
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO))
from quality_monitor import AcquisitionQualityMonitor
from data_handoff import make_manifest, save_manifest
from material_theme import PALETTES, style_sheet
from rcstudio_bridge import probe_rcstudio


class QualityMonitorTests(unittest.TestCase):
    @staticmethod
    def good_sample():
        return {"device_status_names": ["hall_valid", "imu_valid", "imu_fresh"],
                "drop_flag_names": []}

    @staticmethod
    def status(frames, gaps=0, streaming=True):
        return {"connected": True, "streaming_data": streaming, "port": "COM7",
                "parser_stats": {"data_frames": frames, "frame_id_gap": gaps}}

    def test_counter_based_receive_rate_not_gui_tick_rate(self):
        q = AcquisitionQualityMonitor()
        result = None
        for tick in range(61):
            # UI only refreshes 20 times/s, while parser sees 200 frames/s.
            result = q.update(self.status(tick * 10), self.good_sample(), now=tick * .05)
        self.assertEqual(result["host_receive_hz"], 200.0)
        self.assertEqual(result["frame_gap_percent_window"], 0)
        self.assertEqual(result["severity"], "good")
        self.assertEqual(result["imu"], "有效 / 新鲜")

    def test_stale_hall_is_not_good(self):
        q = AcquisitionQualityMonitor()
        q.update(self.status(100), self.good_sample(), now=1)
        bad = self.good_sample()
        bad["drop_flag_names"] = ["hall_stale"]
        result = q.update(self.status(300), bad, now=2)
        self.assertEqual(result["severity"], "warning")
        self.assertIn("过期", result["hall"])

    def test_counter_reset_on_receiver_reconnect(self):
        q = AcquisitionQualityMonitor()
        q.update(self.status(1000, 100), self.good_sample(), now=10)
        q.update(self.status(1100, 105), self.good_sample(), now=11)
        result = q.update(self.status(10, 0), self.good_sample(), now=12)
        self.assertIsNone(result["host_receive_hz"])
        self.assertIsNone(result["frame_gap_percent_window"])
        self.assertEqual(result["total_decoded_frames"], 10)

    def test_no_device_never_looks_good(self):
        q = AcquisitionQualityMonitor()
        result = q.update({"connected": False, "parser_stats": {"data_frames": 40000}}, self.good_sample(), now=20)
        self.assertEqual(result["severity"], "waiting")
        self.assertEqual(result["imu"], "无实时数据")

    def test_frame_id_gap_count_not_naively_samples(self):
        q = AcquisitionQualityMonitor()
        q.update(self.status(1000, 200), self.good_sample(), now=1)
        result = q.update(self.status(1100, 210), self.good_sample(), now=2)
        self.assertAlmostEqual(result["frame_gap_percent_window"], 100*10/110, places=2)


class HandoffTests(unittest.TestCase):
    def test_handoff_file_only_does_not_modify_raw_file(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "live.brlog"
            raw = b"abc\x00\xff" * 11
            log.write_bytes(raw)
            manifest = Path(d) / "handoff.json"
            rec = save_manifest(log, manifest, {"host_receive_hz": 180.2, "severity": "warning"})
            self.assertEqual(log.read_bytes(), raw)
            self.assertEqual(rec["source"]["size_bytes"], len(raw))
            self.assertEqual(len(rec["source"]["sha256"]), 64)
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["schema"], "ebrush.handoff.v1")
            self.assertIn("no upload", rec["boundary"]["effects"])
            with self.assertRaises(FileExistsError):
                save_manifest(log, manifest)

    def test_cannot_handoff_unrelated_or_nonexistent(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "model.pkl"
            file.write_bytes(b"model")
            with self.assertRaises(ValueError):
                make_manifest(file)
            with self.assertRaises(FileNotFoundError):
                make_manifest(Path(d) / "notthere.csv")


class ThemeTests(unittest.TestCase):
    def test_both_themes_have_semantic_tokens(self):
        self.assertEqual(set(PALETTES), {"light", "dark"})
        for mode in PALETTES:
            s = style_sheet(mode)
            self.assertIn("QToolButton:checked", s)
            self.assertIn(PALETTES[mode]["primary"], s)
            self.assertNotIn("#000000", s)


class _TestHTTP(BaseHTTPRequestHandler):
    request_methods = []
    def do_GET(self):
        self.request_methods.append("GET")
        content = b'{"app":"RCStudio","port_role":"rc_training_only"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)
    def do_POST(self):
        self.request_methods.append("POST")
        self.send_error(403)
    def log_message(self, *_):
        pass


class BridgeTests(unittest.TestCase):
    def test_probe_only_gets_and_uses_loopback(self):
        _TestHTTP.request_methods.clear()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _TestHTTP)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            res = probe_rcstudio(port=server.server_port)
            self.assertTrue(res["reachable"])
            self.assertTrue(res["verified"])
            self.assertEqual(_TestHTTP.request_methods, ["GET"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

"""Loopback-only, token-scoped control and workflow event sink for E-Brush.

The server exposes sensor acquisition APIs. RC Studio remains the sole owner of
training, experiment datasets and model artifacts. Tokens never enter event logs.
"""
from __future__ import annotations

import copy
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SCHEMA_VERSION = "ebrush.agent.v2"


def _j(x):
    try:
        import numpy as np
        if isinstance(x, np.ndarray):
            return x.tolist()
        if isinstance(x, np.generic):
            return x.item()
    except ImportError:
        pass
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    if isinstance(x, dict):
        return {str(k): _j(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, deque)):
        return [_j(v) for v in x]
    return str(x)


def _get(d, path):
    cur = d
    for p in [x for x in path.split(".") if x]:
        if not isinstance(cur, dict) or p not in cur:
            raise KeyError(path)
        cur = cur[p]
    return cur


class AgentControl:
    """Read/write Bearer scopes; localhost identity is public, state requires a token."""
    def __init__(self, runtime_dir, host="127.0.0.1", port=8765):
        self.dir = Path(runtime_dir).resolve()
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "snapshots").mkdir(exist_ok=True)
        self.host, self.port = host, int(port)
        self.lock = threading.RLock()
        self.s = {"meta": {"schema": SCHEMA_VERSION, "started_unix": time.time(), "pid": os.getpid()}}
        self.commands, self.params = {}, {}
        self.events, self.workflow_events = deque(maxlen=500), deque(maxlen=1000)
        self.server = None
        self.events_path = self.dir / "events.jsonl"
        self.workflow_path = self.dir / "workflow_events.jsonl"
        self.state_path = self.dir / "state.json"
        self.session = None
        self.tokens = self._credentials()
        self.register_command("ping", lambda a: {"pong": True}, "Liveness check")
        self.register_command("snapshot", lambda a: self.snapshot(), "Persist current state")
        self.register_command("diagnose", lambda a: self.diagnose(), "Deterministic diagnosis")

    def _credentials(self):
        path = self.dir / "agent_credentials.json"
        if not path.exists():
            tokens = {"read_token": secrets.token_urlsafe(32), "write_token": secrets.token_urlsafe(32)}
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(tokens, handle)
        keys = json.loads(path.read_text(encoding="utf-8"))
        if any(not isinstance(keys.get(k), str) or len(keys[k]) < 24 for k in ("read_token", "write_token")):
            raise ValueError("Invalid E-Brush agent credentials")
        return keys

    def publish(self, namespace, value):
        with self.lock:
            self.s[str(namespace)] = _j(value)
            self.s["meta"]["updated_unix"] = time.time()

    def event(self, event, **fields):
        rec = {"t_unix": time.time(), "event": str(event), **_j(fields)}
        with self.lock:
            self.events.append(rec)
            with self.events_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def workflow_event(self, record):
        if not isinstance(record, dict):
            raise ValueError("workflow event must be an object")
        wid = record.get("workflow_id")
        if not isinstance(wid, str) or not re.fullmatch(r"[a-f0-9]{16,32}", wid):
            raise ValueError("Invalid workflow_id")
        rec = {
            "workflow_id": wid,
            "sequence": int(record.get("sequence", 0)),
            "status": str(record.get("status", ""))[:50],
            "stage": str(record.get("stage", ""))[:80],
            "message": str(record.get("message", ""))[:500],
            "progress": max(0.0, min(1.0, float(record.get("progress", 0)))),
            "t_unix": float(record.get("t_unix", time.time())),
        }
        with self.lock:
            # Idempotence avoids duplicate visible entries after a network retry.
            if any(v["workflow_id"] == wid and v["sequence"] == rec["sequence"] for v in self.workflow_events):
                return {"accepted": True, "duplicate": True}
            self.workflow_events.append(rec)
            self.publish("workflow", rec)
            with self.workflow_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"accepted": True, "duplicate": False}

    def register_command(self, name, fn, description=""):
        self.commands[str(name)] = {"fn": fn, "description": description}

    def register_parameter(self, path, getter, setter, description="", type_name=""):
        self.params[str(path)] = {"get": getter, "set": setter,
                                   "description": description, "type": type_name}

    def schema(self):
        with self.lock:
            return {"schema": SCHEMA_VERSION,
                    "commands": {k: {"description": v["description"]} for k, v in self.commands.items()},
                    "parameters": {k: {"description": v["description"], "type": v["type"]} for k, v in self.params.items()},
                    "namespaces": sorted(self.s)}

    def state(self, path=""):
        with self.lock:
            snap = copy.deepcopy(self.s)
        return _get(snap, path) if path else snap

    def execute(self, name, args=None):
        cmd = self.commands.get(name)
        if not cmd:
            raise KeyError("Unknown command " + str(name))
        self.event("command", name=name)
        return _j(cmd["fn"](args or {}))

    def set_parameter(self, path, value):
        param = self.params.get(path)
        if not param:
            raise KeyError("Parameter not writable: " + str(path))
        output = param["set"](value)
        self.event("parameter_set", path=path)
        return _j(output)

    def snapshot(self):
        stamp = time.strftime("%Y%m%d_%H%M%S") + "_%03d" % (int(time.time() * 1000) % 1000)
        file = self.dir / "snapshots" / ("state_" + stamp + ".json")
        file.write_text(json.dumps(self.state(), ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(file)}

    def diagnose(self):
        snap = self.state()
        checks = []
        def add(name, status, detail=""):
            checks.append({"name": name, "status": status, "detail": detail})
        cam = snap.get("camera", {})
        add("camera", "PASS" if cam.get("state") == "RUNNING" else "WARN", cam.get("reason", ""))
        brush = snap.get("brush", {})
        add("brush", "PASS" if not brush.get("error") else "WARN", brush.get("error", ""))
        pose = snap.get("pose", {})
        add("pose_reference", "PASS" if pose.get("reference_locked") else "WARN", pose.get("reference_state", ""))
        sam = snap.get("sam3", {})
        add("sam3", "PASS" if sam.get("last_frame", -1) >= 0 and not sam.get("last_error") else "WARN", sam.get("last_error", ""))
        vis = snap.get("vision", {})
        if vis.get("ok"):
            depth = float(vis.get("depth_valid_ratio", 0))
            rms = float(vis.get("line_rms_mm", 999))
            add("depth_valid_ratio", "PASS" if depth >= 0.65 else "WARN", f"{depth:.3f}")
            add("line_fit", "PASS" if rms <= 7 else "WARN", f"RMS {rms:.2f} mm")
        else:
            add("vision", "WARN", vis.get("reason", "waiting"))
        validation = snap.get("validation", {})
        err = validation.get("shaft_error_deg")
        add("shaft_error", "SKIP" if err is None else ("PASS" if float(err) < 6 else "WARN"),
            "camera→board unavailable" if err is None else f"{float(err):.2f} deg")
        return {"schema": SCHEMA_VERSION, "checks": checks}

    def flush_state(self):
        self.state_path.write_text(json.dumps(self.state(), ensure_ascii=False, indent=2), encoding="utf-8")

    def start(self):
        if self.host != "127.0.0.1":
            raise ValueError("E-Brush agent control accepts loopback host 127.0.0.1 only")
        ctl = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def sendj(self, obj, status=200):
                blob = json.dumps(_j(obj), ensure_ascii=False, allow_nan=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(blob)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(blob)

            def body(self):
                n = int(self.headers.get("Content-Length", "-1"))
                if n < 0 or n > 32768:
                    raise ValueError("Request body limit: 32 KiB")
                obj = json.loads(self.rfile.read(n))
                if not isinstance(obj, dict):
                    raise ValueError("Expected JSON object")
                return obj

            def handle_request(self):
                try:
                    host = self.headers.get("Host", "")
                    if host not in (f"127.0.0.1:{self.server.server_port}",
                                    f"localhost:{self.server.server_port}", "127.0.0.1", "localhost"):
                        return self.sendj({"error": "Host blocked"}, 403)
                    if self.headers.get("Origin"):
                        return self.sendj({"error": "Browser Origin blocked"}, 403)
                    path = urlsplit(self.path).path
                    if self.command == "GET" and path == "/v1/health":
                        return self.sendj({"app": "EBrushStudio", "schema": SCHEMA_VERSION,
                                           "port_role": "acquisition_only"})
                    auth = self.headers.get("Authorization", "")
                    token = auth[7:] if auth.startswith("Bearer ") else ""
                    can_write = hmac.compare_digest(token, ctl.tokens["write_token"])
                    can_read = can_write or hmac.compare_digest(token, ctl.tokens["read_token"])
                    if not can_read:
                        return self.sendj({"error": "Bearer token required"}, 401)
                    if self.command == "POST" and not can_write:
                        return self.sendj({"error": "Write token required"}, 403)
                    if self.command == "GET":
                        if path == "/v1/state":
                            return self.sendj(ctl.state())
                        if path.startswith("/v1/state/"):
                            return self.sendj(ctl.state(path[10:].replace("/", ".")))
                        if path == "/v1/schema":
                            return self.sendj(ctl.schema())
                        if path == "/v1/diagnose":
                            return self.sendj(ctl.diagnose())
                        if path == "/v1/acquisition/status":
                            return self.sendj(ctl.session.status() if ctl.session else {"state": "unavailable"})
                        if path == "/v1/workflow/events":
                            q = parse_qs(urlsplit(self.path).query)
                            since = max(0, int(q.get("after", ["0"])[0]))
                            return self.sendj({"events": [e for e in ctl.workflow_events if e["sequence"] > since]})
                        return self.sendj({"error": "Unknown endpoint"}, 404)
                    if self.command == "POST":
                        d = self.body()
                        if path == "/v1/command":
                            return self.sendj({"ok": True, "result": ctl.execute(d["name"], d.get("args"))})
                        if path == "/v1/set":
                            return self.sendj({"ok": True, "result": ctl.set_parameter(d["path"], d.get("value"))})
                        if path == "/v1/workflow/event":
                            return self.sendj(ctl.workflow_event(d))
                        if path in ("/v1/acquisition/start", "/v1/acquisition/stop", "/v1/acquisition/abort"):
                            if ctl.session is None:
                                return self.sendj({"error": "Capture service unavailable"}, 503)
                            if path.endswith("/start"):
                                result = ctl.session.start(workflow_id=d["workflow_id"],
                                                           recording_index=d["recording_index"],
                                                           max_duration_seconds=d.get("max_duration_seconds", 60))
                                ctl.event("capture_started", workflow_id=d["workflow_id"])
                            else:
                                result = ctl.session.stop(workflow_id=d["workflow_id"],
                                                          abort=path.endswith("/abort"))
                                ctl.event("capture_stopped", workflow_id=d["workflow_id"],
                                          state=result["state"])
                            return self.sendj(result)
                        return self.sendj({"error": "Unknown endpoint"}, 404)
                    return self.sendj({"error": "Method not supported"}, 405)
                except (ValueError, TypeError, KeyError, RuntimeError, OSError, json.JSONDecodeError) as exc:
                    return self.sendj({"error": f"{type(exc).__name__}: {exc}"}, 400)

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True,
                         name="EBrushAgentHTTP").start()
        self.event("agent_control_started", host=self.host,
                   port=self.server.server_port, schema=SCHEMA_VERSION)

    def close(self):
        try:
            self.flush_state()
        except OSError:
            pass
        if self.server:
            self.server.shutdown()
            self.server.server_close()

"""Read-only probe of the separate RC Studio service.

No POST, no model/experiment writes and no serial port access. The protocol
is intentionally not guessed: an unrecognized 8766 service is NOT called RC Studio.
"""
from __future__ import annotations
import json
from urllib import request, error


def probe_rcstudio(*, port=8766, timeout=0.65):
    port = int(port)
    if not (1 <= port <= 65535):
        raise ValueError("Invalid local port")
    base = f"http://127.0.0.1:{port}"
    outcomes = []
    for endpoint in ("/v1/state", "/health"):
        try:
            req = request.Request(base + endpoint, headers={"Accept": "application/json"}, method="GET")
            with request.urlopen(req, timeout=timeout) as response:
                raw = response.read(8192)
                code = response.status
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                payload = {}
            text = json.dumps(payload, ensure_ascii=False).lower()
            verified = isinstance(payload, dict) and ("rcstudio" in text or "rc studio" in text)
            return {"reachable": True, "verified": verified, "endpoint": endpoint,
                    "http_status": code,
                    "message": "检测到 RC Studio 标识（只读）" if verified else "检测到 HTTP 服务，但未验证为 RC Studio；不执行任何写操作"}
        except error.HTTPError as exc:
            outcomes.append(f"{endpoint}: HTTP {exc.code}")
        except (error.URLError, TimeoutError, OSError) as exc:
            outcomes.append(f"{endpoint}: {type(exc).__name__}")
    return {"reachable": False, "verified": False, "endpoint": None,
            "http_status": None, "message": "未确认 RC Studio 接口；" + "; ".join(outcomes)}

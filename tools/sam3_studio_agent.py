"""Local-only CLI for Studio's Agent API: status, upload, retry, download.

Uses the existing Studio process as sole owner of SAM3, camera and job files.
The CLI never loads another model or opens a second HP60C SDK instance.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import requests


def main():
    p = argparse.ArgumentParser(description="Monitor/control Studio SAM3 production jobs")
    p.add_argument("--agent-url", default="http://127.0.0.1:8765")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("diagnose")
    sub.add_parser("status")
    sub.add_parser("jobs")
    upload = sub.add_parser("upload")
    upload.add_argument("video", type=Path)
    upload.add_argument("--prompt", default="calligraphy brush")
    upload.add_argument("--manifest", type=Path,
                        help="Optional video-frame-index -> HP60C source-frame-id/timestamp JSONL")
    job = sub.add_parser("job")
    job.add_argument("id")
    retry = sub.add_parser("retry")
    retry.add_argument("id")
    for name in ("pause", "resume"):
        sub.add_parser(name)
    download = sub.add_parser("download")
    download.add_argument("id")
    download.add_argument("--kind", choices=["download", "source", "manifest"], default="download")
    download.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    base = a.agent_url.rstrip("/")
    if not (base.startswith("http://127.0.0.1:") or base.startswith("http://localhost:")):
        p.error("Use a local Agent API or an authenticated tunnel exposing localhost")
    try:
        if a.cmd == "download":
            url = f"{base}/v1/sam3/jobs/{a.id}/{a.kind}"
            # Append to partial download using HTTP Range; restart if server
            # ignores the range. Never present partial data as complete.
            dest = a.output.expanduser()
            part = dest.with_name(dest.name + ".part")
            offset = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            with requests.get(url, headers=headers, timeout=(5, 180), stream=True) as r:
                r.raise_for_status()
                mode = "ab" if offset and r.status_code == 206 else "wb"
                with part.open(mode) as out:
                    for block in r.iter_content(1024 * 1024):
                        if block:
                            out.write(block)
            part.replace(dest)
            print(json.dumps({"downloaded": str(dest), "bytes": dest.stat().st_size}, ensure_ascii=False))
            return 0
        if a.cmd == "diagnose":
            method, url, body = "GET", "/v1/diagnose", None
        elif a.cmd == "status":
            method, url, body = "GET", "/v1/state/sam3", None
        elif a.cmd == "jobs":
            method, url, body = "GET", "/v1/sam3/jobs", None
        elif a.cmd == "job":
            method, url, body = "GET", f"/v1/sam3/jobs/{a.id}", None
        else:
            method, url = "POST", "/v1/command"
            if a.cmd == "upload":
                args = {"path": str(a.video.resolve()), "prompt": a.prompt}
                if a.manifest: args["frame_manifest_path"] = str(a.manifest.resolve())
                name = "sam3.video.submit"
            elif a.cmd == "retry":
                args, name = {"job_id": a.id}, "sam3.video.retry"
            else:
                args, name = {}, "sam3.video." + a.cmd
            body = {"name": name, "args": args}
        r = requests.request(method, base + url, json=body, timeout=(4, 60))
        r.raise_for_status()
        print(json.dumps(r.json(), ensure_ascii=False, indent=2))
        return 0
    except requests.RequestException as e:
        print(f"Agent request failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

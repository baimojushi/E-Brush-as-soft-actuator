"""Verify HP60CSource starts the HP60C pipeline when shared memory is missing."""
import os
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\项目\交互艺术\书法微更新\e-Brush")
sys.path.insert(0, str(ROOT / "studio"))
sys.path.insert(0, str(ROOT / "hp60c_v13"))
os.environ["HP60C_SUPERVISOR_MODE"] = "mock"
import hp60c_shm_reader_v13 as shm_reader  # noqa: E402

if shm_reader.get_status().get("attached"):
    print("SKIP: a live HP60C pipeline already owns the shared memory")
    raise SystemExit(0)

from sources import HP60CSource  # noqa: E402

HP60CSource.RECONNECT_TICK_S = 0.05
HP60CSource.SUPERVISOR_GRACE_S = 0.2
HP60CSource.STALE_RECONNECT_S = 1.0

src = HP60CSource(ROOT, "shm", auto_reconnect=True, auto_start_supervisor=True)
try:
    src.start()
    end = time.monotonic() + 30
    frame = None
    while time.monotonic() < end and frame is None:
        frame = src.poll()
        time.sleep(0.05)
    st = src.status()
    print(f"state={st['state']} supervisor_pid={st['supervisor_pid']} "
          f"starts={st['supervisor_starts']} frames={st['frames_delivered']} "
          f"error={st['supervisor_error']}")
    assert st["supervisor_starts"] == 1, st
    assert st["supervisor_pid"], st
    assert frame is not None and st["state"] == "ONLINE", st
    assert st["frames_delivered"] > 0
finally:
    src.stop_supervisor()
    src.close()
print("CAMERA_SUPERVISOR_AUTOSTART_OK")

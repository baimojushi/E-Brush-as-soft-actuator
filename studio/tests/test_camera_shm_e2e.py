"""End-to-end HP60CSource(shm) auto-reconnect against the mock supervisor."""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\项目\交互艺术\书法微更新\e-Brush")
SHM_DIR = ROOT.parent / "hp60c_v13"
SUP = SHM_DIR / "hp60c_supervisor_v13.py"
sys.path.insert(0, str(ROOT / "studio"))
from sources import HP60CSource  # noqa: E402


def start_supervisor():
    return subprocess.Popen(
        [sys.executable, "-u", str(SUP), "--mode", "mock", "--mock-fps", "30"],
        cwd=str(SHM_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def kill_tree(proc):
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def pump_until(src, pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        frame = src.poll()
        if pred(frame):
            return True
        time.sleep(0.05)
    return False


sup = start_supervisor()
try:
    src = HP60CSource(ROOT, "shm", auto_reconnect=True,
                      auto_start_supervisor=False)
    src.start()
    assert pump_until(src, lambda f: f is not None, 30), "no frame from mock supervisor"
    st = src.status()
    print(f"live      -> state={st['state']} frames={src.frames_delivered} "
          f"gen={st.get('generation')} age_s={st['last_frame_age_s']}")

    kill_tree(sup)
    time.sleep(0.5)
    # Frames must stop being reported, and the stale reader must be dropped.
    gone = pump_until(src, lambda f: f is None, 8)
    print(f"supervisor down -> frame_gone={gone} attempts={src.reconnect_attempts}")
    assert gone, "camera kept reporting frames after the supervisor died"
    end = time.monotonic() + 8
    while time.monotonic() < end and src.reconnect_attempts < 2:
        src.poll()
        time.sleep(0.05)
    st = src.status()
    print(f"reader dropped -> state={st['state']} attached={st.get('attached')} "
          f"attempts={src.reconnect_attempts} retry_in_s={st['retry_in_s']}")
    assert src.reconnect_attempts >= 2, "stale reader was never re-created"
    assert not st.get("attached"), "stale shared memory still attached after drop"

    frames_at_restart = src.frames_delivered
    sup = start_supervisor()
    resumed = pump_until(src, lambda f: f is not None, 30)
    assert resumed, "camera did not recover after the supervisor restarted"
    assert src.frames_delivered > frames_at_restart
    st = src.status()
    print(f"restarted -> state={st['state']} frames={src.frames_delivered} "
          f"gen={st.get('generation')} age_s={st['last_frame_age_s']}")
    src.close()
finally:
    kill_tree(sup)
print("CAMERA_SHM_E2E_OK")

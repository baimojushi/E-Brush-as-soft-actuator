"""Offline check of HP60CSource auto-reconnect (no SDK, no SHM)."""
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\项目\交互艺术\书法微更新\e-Brush")
sys.path.insert(0, str(ROOT / "studio"))
from sources import HP60CSource  # noqa: E402

HP60CSource.RECONNECT_TICK_S = 0.05
HP60CSource.RECONNECT_MIN_S = 0.1
HP60CSource.RECONNECT_MAX_S = 0.2
HP60CSource.STALE_RECONNECT_S = 0.3


class FakeImpl:
    def __init__(self, name, frames=10_000):
        self.name = name
        self.status_map = object()      # make _impl_dead treat it as attached
        self.frames = frames
        self.packet = 0
        self.closed = False

    def try_attach(self):
        return True

    def try_get_latest(self, max_age_ms=500):
        if self.frames <= 0:
            return None
        self.frames -= 1
        self.packet += 1
        return {"packet_id": self.packet, "generation": 1,
                "host_receive_ns": time.perf_counter_ns(), "fresh": True}

    def get_status(self):
        return {"state": "ONLINE", "attached": True,
                "packet_id": self.packet, "source": self.name}

    def close(self):
        self.closed = True


def pump_until(src, pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        src.poll()
        if pred():
            return True
        time.sleep(0.02)
    return False


# 1) Failed open is retried, then frames flow once the factory succeeds.
attempts = {"n": 0}


def flaky_factory():
    attempts["n"] += 1
    if attempts["n"] <= 2:
        raise RuntimeError("camera not ready")
    return FakeImpl("flaky")


src = HP60CSource(ROOT, "shm", auto_reconnect=True,
                  auto_start_supervisor=False)
src._open_impl = flaky_factory
src.start()
assert src.poll() is None, "no frame before the factory succeeds"
assert pump_until(src, lambda: src.frames_delivered > 0)
st = src.status()
print(f"late connect -> frames={st['frames_delivered']} "
      f"attempts={st['reconnect_attempts']} state={st['state']}")
assert st["reconnect_attempts"] >= 3
src.close()

# 2) An attached-but-silent source is dropped and re-created; new frames flow.
made = []


def factory():
    impl = FakeImpl(f"impl{len(made)}", frames=40)
    made.append(impl)
    return impl


src = HP60CSource(ROOT, "shm", auto_reconnect=True,
                  auto_start_supervisor=False)
src._open_impl = factory
src.start()
assert pump_until(src, lambda: src.frames_delivered >= 20)
first = made[0]
assert pump_until(src, lambda: len(made) >= 2 and first.closed)
assert pump_until(src, lambda: src.frames_delivered > 45)
st = src.status()
print(f"stall recovery -> frames={st['frames_delivered']} "
      f"attempts={st['reconnect_attempts']} impls={len(made)} state={st['state']}")
assert len(made) >= 2, "stalled source was not re-created"
src.close()

# 3) backend=none never retries and reports DISABLED.
src = HP60CSource(ROOT, "none", auto_reconnect=True)
src.start()
time.sleep(0.2)
st = src.status()
print(f"disabled -> state={st['state']} attempts={st['reconnect_attempts']}")
assert st["state"] == "DISABLED" and st["reconnect_attempts"] == 0
src.close()

print("CAMERA_RECONNECT_LOGIC_OK")

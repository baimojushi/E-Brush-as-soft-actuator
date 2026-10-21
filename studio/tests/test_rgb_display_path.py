"""Offscreen check of the real display path: SHM frame -> HotPatch -> Img."""
import os
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(r"D:\项目\交互艺术\书法微更新\e-Brush")
SHM = ROOT.parent / "hp60c_v13"
sys.path.insert(0, str(ROOT / "studio"))
sys.path.insert(0, str(SHM))

import hp60c_shm_reader_v13 as reader  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

app = QtWidgets.QApplication([])
from main import Img  # noqa: E402
from core import HotPatch  # noqa: E402

rd = reader.Reader()
frame = None
end = time.monotonic() + 8
while time.monotonic() < end:
    frame = rd.try_get_latest(max_age_ms=500)
    if frame and frame.get("rgb") is not None:
        break
    time.sleep(0.05)
assert frame is not None, "no camera frame from shared memory"

patch = HotPatch(ROOT / "patches" / "visual_patch.py")
out = patch.run({"camera": frame},
                {"near": 0.0, "far": 4000.0,
                 "colormap": {"colormap_type": 3}})
assert patch.error == "", patch.error
assert out.get("rgb") is not None, "patch produced no rgb"

w = Img()
w.resize(320, 240)
w.set(out["rgb"])
app.processEvents()
shown = w.i.image
print(f"patch_error={patch.error!r} rgb={out['rgb'].shape} "
      f"widget_image={None if shown is None else shown.shape} "
      f"mean={float(out['rgb'].mean()):.1f}")
assert shown is not None and shown.shape == out["rgb"].shape
rd.close()
print("RGB_DISPLAY_PATH_OK")

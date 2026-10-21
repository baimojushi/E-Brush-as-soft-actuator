"""Offline check: the restored patch must return the camera's RGB frame."""
import sys
from pathlib import Path

ROOT = Path(r"D:\项目\交互艺术\书法微更新\e-Brush")
sys.path.insert(0, str(ROOT / "studio"))
from core import HotPatch  # noqa: E402
import numpy as np  # noqa: E402

rgb = np.zeros((480, 640, 3), np.uint8)
rgb[..., 0] = 200
depth = np.full((480, 640), 1000, np.uint16)
depth[0, 0] = 0  # invalid pixel must normalise to 0, not 0.25

hp = HotPatch(ROOT / "patches" / "visual_patch.py")
out = hp.run({"camera": {"rgb": rgb, "depth_mm": depth}},
             {"near": 0.0, "far": 4000.0,
              "colormap": {"colormap_type": 3}})
print(f"error={hp.error!r} keys={sorted(out)} "
      f"rgb={None if 'rgb' not in out else (out['rgb'].shape, out['rgb'].dtype)} "
      f"depth={None if 'depth' not in out else (out['depth'].shape, float(out['depth'].min()), float(out['depth'].max()))}")
assert hp.error == "", hp.error
assert out.get("rgb") is rgb
assert out["depth"].min() == 0.0 and out["depth"].max() == 0.25
print("VISUAL_PATCH_OK")

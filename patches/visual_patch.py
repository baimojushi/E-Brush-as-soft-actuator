"""Live visual patch consumed by core.HotPatch.

Restored into e-Brush/patches: main.py routes the colour view through this
module, so a missing file leaves the RGB panel empty (HotPatch swallows the
FileNotFoundError and returns {}).
"""
import numpy as np


def process(state, params):
    cam = state.get("camera") or {}
    out = {}
    if cam.get("rgb") is not None:
        out["rgb"] = cam["rgb"]
    d = cam.get("depth_mm")
    if d is not None:
        d = np.asarray(d, np.float32)
        near = params["near"]
        far = params["far"]
        x = np.clip((d - near) / max(1, far - near), 0, 1)
        x[d <= 0] = 0
        out["depth"] = x
        out["composite"] = np.dstack([x, x, x])
    return out

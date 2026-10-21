"""Check that the repo copy of the HP60C pipeline is preferred."""
import sys
from pathlib import Path

ROOT = Path(r"D:\项目\交互艺术\书法微更新\e-Brush")
sys.path.insert(0, str(ROOT / "studio"))
from sources import HP60CSource  # noqa: E402

src = HP60CSource(ROOT, "shm", auto_reconnect=False,
                  auto_start_supervisor=False)
v13 = src._pipeline_dir("hp60c_v13")
ndi = src._pipeline_dir("hp60c_ndi_sender")
print(f"v13={v13}\nndi={ndi}")
assert v13 == ROOT / "hp60c_v13", v13
assert ndi == ROOT / "hp60c_ndi_sender", ndi
assert (v13 / "hp60c_supervisor_v13.py").is_file()
assert (ndi / "hp60c_td_bridge_v7.py").is_file()
print("PIPELINE_PATHS_OK")

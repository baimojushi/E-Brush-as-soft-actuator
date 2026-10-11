# 在 TouchDesigner Text DAT / Textport 中执行一次。
# 将 PORT 改为实际串口，例如 Windows: COM7，macOS/Linux: /dev/ttyACM0。
#
# 建议把本项目 touchdesigner 与 host 目录放在 .toe 同级目录。

from pathlib import Path
import sys

PROJECT_DIR = Path(project.folder)
TD_DIR = PROJECT_DIR / "touchdesigner"
if str(TD_DIR) not in sys.path:
    sys.path.insert(0, str(TD_DIR))

from brush_td import BrushTD

PORT = "COM7"  # 修改这里
RAW_LOG = str(PROJECT_DIR / "capture.brlog")

bridge = BrushTD(PORT, baud=921600, raw_log_path=RAW_LOG, max_align_dt_ms=25.0).start()

# 把脚本所在 Base COMP 作为 owner；若在 Textport 执行，改成实际 Base COMP 路径。
owner = me.parent()
old = owner.fetch("brush_td_bridge", None)
if old is not None:
    try:
        old.close()
    except Exception:
        pass
owner.store("brush_td_bridge", bridge)
print("BRUSH bridge started:", PORT)

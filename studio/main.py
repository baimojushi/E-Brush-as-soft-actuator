import argparse,json,sys,time,os,csv,math
from collections import deque
from pathlib import Path
import numpy as np, cv2
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent;sys.path.insert(0,str(HERE))
from sources import HP60CSource,BrushSource
from pose_mapper import PoseMapper
from core import HotPatch
from sam3_client import SAM3Client
from vision_pose import VisionPoseEstimator,compare_axes
from agent_control import AgentControl
from mapper_debug import MapperDebug
from scene3d import Scene3D
from pose_calibration import PoseCalibrationSession
from pose_calibration_ui import PoseCalibrationPanel
from hall_mount import HallMountModel
from hall_panel import HallPanel
from hall_extrinsic import HallExtrinsicCalibration
from hall_extrinsic_ui import HallExtrinsicPanel
from PySide6 import QtCore,QtWidgets,QtGui
import pyqtgraph as pg

class Img(pg.GraphicsLayoutWidget):
    def __init__(self):super().__init__();v=self.addViewBox(lockAspect=True);self.i=pg.ImageItem(axisOrder="row-major");v.addItem(self.i);v.invertY(True)
    def set(self,a):
        if a is not None:self.i.setImage(np.asarray(a),autoLevels=True)

def overlay(rgb,mask=None,axis_text=None):
    if rgb is None:return None
    a=np.asarray(rgb).copy()
    if a.ndim==3 and a.shape[2]==4:a=a[:,:,:3]
    if mask is not None and mask.shape==a.shape[:2]:
        edge=cv2.Canny((mask.astype(np.uint8)*255),50,150)>0
        a[edge]=255
    if axis_text:cv2.putText(a,axis_text,(15,30),cv2.FONT_HERSHEY_SIMPLEX,.65,(255,255,255),2,cv2.LINE_AA)
    return a

# ---- Guofeng colormap (256-entry interpolated) ----
# Define key colors at specific indices, then interpolate to full 256
_GUOFENG_KEY = {
    0:   [0, 0, 0],       # 墨黑
    10:  [10, 2, 1],
    20:  [20, 4, 2],
    30:  [30, 6, 3],
    40:  [40, 8, 4],
    50:  [50, 10, 5],
    60:  [60, 12, 6],
    70:  [70, 14, 7],
    80:  [80, 16, 8],
    90:  [90, 18, 9],
    100: [100, 20, 10],
    110: [110, 25, 10],
    120: [120, 30, 10],
    130: [130, 35, 10],
    140: [140, 40, 10],
    150: [150, 45, 10],
    160: [160, 50, 10],
    170: [170, 55, 10],
    180: [180, 60, 10],
    190: [190, 65, 10],
    200: [200, 70, 10],
    210: [210, 80, 15],
    220: [220, 90, 20],
    230: [230, 100, 25],
    240: [240, 110, 30],
    250: [250, 120, 35],
    255: [255, 130, 40],
}

_GUOFENG_LUT = np.zeros((256, 3), dtype=np.uint8)
prev_idx = 0
prev_color = _GUOFENG_KEY[0]
for i in range(256):
    if i in _GUOFENG_KEY:
        prev_idx = i
        prev_color = _GUOFENG_KEY[i]
        _GUOFENG_LUT[i] = prev_color
    else:
        # Find next defined key
        next_idx = min([k for k in _GUOFENG_KEY.keys() if k > i] + [255])
        next_color = _GUOFENG_KEY[next_idx]
        t = (i - prev_idx) / (next_idx - prev_idx)
        _GUOFENG_LUT[i] = [int(prev_color[j] * (1-t) + next_color[j] * t) for j in range(3)]

def apply_colormap(depth_mm, colormap_type=3, mix=1.0, brightness=0.0, contrast=1.0,
                   depth_min=0.0, depth_max=1.0, near_mm=100.0, far_mm=850.0):
    """Apply colormap to depth image with adjustable parameters."""
    if depth_mm is None:
        return None
    d = depth_mm.astype(np.float32)
    # Normalize by physical range
    d = (d - near_mm) / (far_mm - near_mm)
    d = np.clip(d, depth_min, depth_max)
    # Stretch to [0,1] within selected range
    rng = depth_max - depth_min
    if rng > 1e-6:
        d = (d - depth_min) / rng
    else:
        d = np.zeros_like(d)
    # Brightness/contrast
    d = d * contrast + brightness
    d = np.clip(d, 0.0, 1.0)
    # Select colormap
    if colormap_type == 0:  # JET
        lut = cv2.applyColorMap((d*255).astype(np.uint8), cv2.COLORMAP_JET)
    elif colormap_type == 1:  # Rainbow
        lut = cv2.applyColorMap((d*255).astype(np.uint8), cv2.COLORMAP_RAINBOW)
    elif colormap_type == 2:  # Ironbow
        lut = cv2.applyColorMap((d*255).astype(np.uint8), cv2.COLORMAP_BONE)
    else:  # Guofeng
        idx = (d * 255).astype(np.uint8)
        lut = _GUOFENG_LUT[idx]
        lut = cv2.cvtColor(lut, cv2.COLOR_RGB2BGR)
    # Mix with grayscale
    if mix < 1.0:
        gray = cv2.cvtColor((d*255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        lut = (lut * mix + gray * (1.0 - mix)).astype(np.uint8)
    return lut

def overlay(rgb,mask=None,axis_text=None):
    if rgb is None:return None
    a=np.asarray(rgb).copy()
    if a.ndim==3 and a.shape[2]==4:a=a[:,:,:3]
    if mask is not None and mask.shape==a.shape[:2]:
        edge=cv2.Canny((mask.astype(np.uint8)*255),50,150)>0
        a[edge]=255
    if axis_text:cv2.putText(a,axis_text,(15,30),cv2.FONT_HERSHEY_SIMPLEX,.65,(255,255,255),2,cv2.LINE_AA)
    return a

class ValidationPanel(QtWidgets.QWidget):
    def __init__(self):
        super().__init__();L=QtWidgets.QVBoxLayout(self)
        self.big=QtWidgets.QLabel("SAM3 waiting…");self.big.setStyleSheet("font-size:22px;font-weight:700");L.addWidget(self.big)
        self.detail=QtWidgets.QPlainTextEdit();self.detail.setReadOnly(True);self.detail.setMaximumHeight(330);L.addWidget(self.detail)
        self.plot=pg.PlotWidget(title="IMU ↔ Vision shaft error");self.plot.setLabel("left","deg");self.plot.setLabel("bottom","samples")
        self.curve=self.plot.plot();self.err=deque(maxlen=600);L.addWidget(self.plot)
    def update_result(self,d):
        if not d:return
        e=d.get("shaft_error_deg")
        self.big.setText(("SHAFT ERROR %.2f°"%e) if e is not None else "Vision shaft OK — camera→board rotation not enabled")
        self.detail.setPlainText(json.dumps(d,ensure_ascii=False,indent=2,default=lambda x:np.asarray(x).tolist()))
        self.err.append(np.nan if e is None else e);self.curve.setData(list(self.err))

# ---- Depth Colormap Control Panel ----
class ColormapControlPanel(QtWidgets.QWidget):
    def __init__(self, on_change_callback):
        super().__init__()
        self.on_change = on_change_callback
        L = QtWidgets.QVBoxLayout(self)
        L.setContentsMargins(8,8,8,8)
        L.setSpacing(8)

        # Title
        title = QtWidgets.QLabel("深度图色彩控制")
        title.setStyleSheet("font-size:14px;font-weight:700;color:#222;")
        L.addWidget(title)

        # Colormap type
        self.cmb_type = QtWidgets.QComboBox()
        self.cmb_type.addItems(["JET (经典)", "Rainbow (彩虹)", "Ironbow (铁黑)", "Guofeng 国风 (默认)"])
        self.cmb_type.setCurrentIndex(3)
        self.cmb_type.currentIndexChanged.connect(self._emit)
        L.addWidget(QtWidgets.QLabel("色图类型"))
        L.addWidget(self.cmb_type)

        # Mix
        self.sld_mix = self._make_slider("混合度", 0, 100, 100, "与灰度混合比例")
        L.addWidget(self.sld_mix[0]); L.addWidget(self.sld_mix[1])

        # Brightness
        self.sld_bright = self._make_slider("亮度", -100, 100, 0, "亮度偏移")
        L.addWidget(self.sld_bright[0]); L.addWidget(self.sld_bright[1])

        # Contrast
        self.sld_contrast = self._make_slider("对比度", 10, 300, 100, "对比度倍数")
        L.addWidget(self.sld_contrast[0]); L.addWidget(self.sld_contrast[1])

        # Depth range min/max
        self.sld_dmin = self._make_slider("深度范围Min", 0, 99, 0, "深度范围下限(%)")
        self.sld_dmax = self._make_slider("深度范围Max", 1, 100, 100, "深度范围上限(%)")
        L.addWidget(QtWidgets.QLabel("深度范围裁剪"))
        L.addWidget(self.sld_dmin[0]); L.addWidget(self.sld_dmin[1])
        L.addWidget(self.sld_dmax[0]); L.addWidget(self.sld_dmax[1])

        # Near/Far
        self.spn_near = QtWidgets.QSpinBox(); self.spn_near.setRange(0, 10000); self.spn_near.setValue(0); self.spn_near.setSuffix(" mm")
        self.spn_far = QtWidgets.QSpinBox(); self.spn_far.setRange(0, 10000); self.spn_far.setValue(4000); self.spn_far.setSuffix(" mm")
        self.spn_near.valueChanged.connect(self._emit)
        self.spn_far.valueChanged.connect(self._emit)
        L.addWidget(QtWidgets.QLabel("物理距离范围"))
        row_nf = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(row_nf); h.setContentsMargins(0,0,0,0)
        h.addWidget(QtWidgets.QLabel("近")); h.addWidget(self.spn_near); h.addWidget(QtWidgets.QLabel("远")); h.addWidget(self.spn_far)
        L.addWidget(row_nf)

        # Reset button
        btn_reset = QtWidgets.QPushButton("重置为国风默认")
        btn_reset.clicked.connect(self._reset_defaults)
        L.addWidget(btn_reset)

        L.addStretch()

    def _make_slider(self, label, min_v, max_v, default, tooltip):
        lbl = QtWidgets.QLabel(label)
        lbl.setToolTip(tooltip)
        sld = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        sld.setRange(min_v, max_v)
        sld.setValue(default)
        sld.setToolTip(tooltip)
        val_lbl = QtWidgets.QLabel(str(default))
        val_lbl.setMinimumWidth(40)
        sld.valueChanged.connect(lambda v: (val_lbl.setText(str(v)), self._emit()))
        return lbl, sld, val_lbl

    def _emit(self):
        if self.on_change:
            self.on_change()

    def _reset_defaults(self):
        self.cmb_type.setCurrentIndex(3)
        self.sld_mix[1].setValue(100)
        self.sld_bright[1].setValue(0)
        self.sld_contrast[1].setValue(100)
        self.sld_dmin[1].setValue(0)
        self.sld_dmax[1].setValue(100)
        self.spn_near.setValue(100)
        self.spn_far.setValue(850)
        self._emit()

    def get_params(self):
        return {
            "colormap_type": self.cmb_type.currentIndex(),
            "mix": self.sld_mix[1].value() / 100.0,
            "brightness": self.sld_bright[1].value() / 100.0,
            "contrast": self.sld_contrast[1].value() / 100.0,
            "depth_min": self.sld_dmin[1].value() / 100.0,
            "depth_max": self.sld_dmax[1].value() / 100.0,
            "near_mm": float(self.spn_near.value()),
            "far_mm": float(self.spn_far.value()),
        }

class Win(QtWidgets.QMainWindow):
    def __init__(self, a):
        super().__init__()
        self.resize(1600, 960)
        self.setWindowTitle("e-Brush Studio v1.0 — 彩色/深度/SAM3/验证/三维/霍尔/姿态/外参")

        cfg = json.loads((ROOT/"config"/"vision_validation.json").read_text(encoding="utf-8"))
        self.cfg = cfg

        # Sources
        self.hp = HP60CSource(ROOT, a.camera_backend)
        self.hp.start()
        self.br = BrushSource(ROOT/"vendor"/"ebrush", a.port, a.baud)
        if not a.no_brush:
            self.br.start()

        # Core pipelines
        self.pose = PoseMapper(ROOT/"config"/"ebrush_pose_calibration.json", heading_mode=a.heading_mode)
        self.mapper_debug = MapperDebug(ROOT)
        self.pose_cal = PoseCalibrationSession(ROOT, self.pose, self.mapper_debug)
        self.hall = HallMountModel(ROOT)
        self.hall_cal = HallExtrinsicCalibration(ROOT, self.hall, self.pose, self.mapper_debug)
        self.patch = HotPatch(ROOT/"patches"/"visual_patch.py")
        self.state = {"camera": None, "brush": None, "pose": None, "hall": None}

        # SAM3
        sc = cfg["sam3"]
        key = os.environ.get(sc.get("api_key_env", "SAM3_API_KEY"), "")
        self.sam = SAM3Client(
            a.sam3_url or sc["base_url"], key,
            a.sam3_prompt or sc["prompt"],
            sc["jpeg_quality"], sc["timeout_s"],
            sc.get("clip_frames", 30), sc.get("clip_fps", 30),
            sc["min_interval_s"]
        )

        # Vision
        vc = cfg["vision"]
        self.vest = VisionPoseEstimator(vc["erode_px"], vc["ransac_threshold_mm"], vc["min_points"])
        cc = cfg["camera_to_board"]
        self.Rbc = np.asarray(cc["R_board_from_camera"], float) if cc.get("enabled") else None

        self.last_submitted = -1
        self.last_sam_result = -1
        self.last_validation = None

        # Agent
        self.agent = AgentControl(ROOT/"runtime", a.agent_host, a.agent_port)
        self._register_agent_commands()
        self.agent.start()
        self._last_agent_flush = 0

        # Colormap state
        self.colormap_params = {
            "colormap_type": 3, "mix": 1.0, "brightness": 0.0, "contrast": 1.0,
            "depth_min": 0.0, "depth_max": 1.0, "near_mm": 0.0, "far_mm": 4000.0
        }

        # ---- UI: Toggle Grid Layout ----
        self.rgb = Img()
        self.dep = Img()
        self.maskview = Img()
        self.val = ValidationPanel()
        self.scene3d = Scene3D()
        self.hallui = HallPanel()
        self.calui = PoseCalibrationPanel()
        self.hallcalui = HallExtrinsicPanel()

        self._views = [
            ("彩色图像", self.rgb),
            ("深度图", self.dep),
            ("SAM3 掩膜叠加", self.maskview),
            ("IMU ↔ 视觉验证", self.val),
            ("三维场景", self.scene3d),
            ("霍尔磁通", self.hallui),
            ("姿态映射校准", self.calui),
            ("霍尔外参标定", self.hallcalui),
        ]
        self._view_visible = [True] * len(self._views)  # all visible by default

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Top toolbar: view toggles
        toolbar = QtWidgets.QWidget()
        tb_layout = QtWidgets.QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(4, 4, 4, 4)
        tb_layout.setSpacing(6)

        self._toggle_buttons = []
        for i, (name, _) in enumerate(self._views):
            btn = QtWidgets.QToolButton()
            btn.setText(name)
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setMinimumWidth(100)
            btn.setMinimumHeight(32)
            btn.setStyleSheet("""
                QToolButton { background:#333; color:#fff; border:1px solid #555; border-radius:4px; padding:4px 8px; font-weight:600; }
                QToolButton:checked { background:#0078d4; border-color:#0078d4; }
                QToolButton:hover:!checked { background:#444; }
            """)
            btn.toggled.connect(lambda checked, idx=i: self._on_view_toggled(idx, checked))
            tb_layout.addWidget(btn)
            self._toggle_buttons.append(btn)

        tb_layout.addStretch()
        main_layout.addWidget(toolbar)

        # Grid container for views
        self._grid_container = QtWidgets.QWidget()
        self._grid_layout = QtWidgets.QGridLayout(self._grid_container)
        self._grid_layout.setContentsMargins(4, 4, 4, 4)
        self._grid_layout.setSpacing(6)
        self._group_boxes = []  # track group boxes for proper cleanup
        main_layout.addWidget(self._grid_container, 1)

        # Initialize grid
        self._rebuild_grid()

        # ---- Docks ----
        # Right dock: Runtime + Colormap
        d = QtWidgets.QDockWidget("Runtime / 色彩控制")
        w = QtWidgets.QWidget()
        f = QtWidgets.QVBoxLayout(w)
        f.setSpacing(8)

        # Runtime status
        self.prompt = QtWidgets.QLineEdit(self.sam.prompt)
        self.prompt.editingFinished.connect(lambda: setattr(self.sam, "prompt", self.prompt.text().strip()))
        self.clip_frames = QtWidgets.QSpinBox(); self.clip_frames.setRange(5, 120); self.clip_frames.setValue(self.sam.clip_frames)
        self.clip_frames.valueChanged.connect(lambda v: setattr(self.sam, "clip_frames", int(v)))
        self.clip_fps = QtWidgets.QSpinBox(); self.clip_fps.setRange(10, 60); self.clip_fps.setValue(self.sam.clip_fps)
        self.clip_fps.valueChanged.connect(lambda v: setattr(self.sam, "clip_fps", int(v)))

        self.status = QtWidgets.QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(200)

        b = QtWidgets.QPushButton("Reset UP_R0 reference")
        b.clicked.connect(self.pose.reset_reference)
        h = QtWidgets.QPushButton("检查 SAM3")
        h.clicked.connect(self.check_health)

        f.addWidget(QtWidgets.QLabel("SAM3 Prompt")); f.addWidget(self.prompt)
        f.addWidget(QtWidgets.QLabel("Clip Frames")); f.addWidget(self.clip_frames)
        f.addWidget(QtWidgets.QLabel("Clip FPS")); f.addWidget(self.clip_fps)
        f.addWidget(h); f.addWidget(b); f.addWidget(self.status)

        # Colormap control panel
        self.colormap_panel = ColormapControlPanel(self._on_colormap_change)
        f.addWidget(QtWidgets.QLabel("—— 深度图色彩映射 ——"))
        f.addWidget(self.colormap_panel)

        # Brush serial reconnection panel
        self.brush_panel = QtWidgets.QGroupBox("毛笔串口重连")
        self.brush_panel.setStyleSheet("font-weight:600; font-size:12px;")
        bl = QtWidgets.QVBoxLayout(self.brush_panel)
        bl.setContentsMargins(8, 8, 8, 8)
        bl.setSpacing(6)

        self.brush_status_lbl = QtWidgets.QLabel("状态: 检测中...")
        self.brush_status_lbl.setStyleSheet("font-size:12px;")
        bl.addWidget(self.brush_status_lbl)

        self.brush_port_lbl = QtWidgets.QLabel("端口: 未连接")
        self.brush_port_lbl.setStyleSheet("font-size:12px; color:#888;")
        bl.addWidget(self.brush_port_lbl)

        row = QtWidgets.QWidget()
        rh = QtWidgets.QHBoxLayout(row)
        rh.setContentsMargins(0, 0, 0, 0)
        self.btn_brush_reconnect = QtWidgets.QPushButton("手动重连")
        self.btn_brush_reconnect.clicked.connect(self._manual_brush_reconnect)
        self.btn_brush_reconnect.setMinimumHeight(28)
        self.chk_auto_reconnect = QtWidgets.QCheckBox("自动重连")
        self.chk_auto_reconnect.setChecked(True)
        self.chk_auto_reconnect.toggled.connect(self._toggle_auto_reconnect)
        rh.addWidget(self.btn_brush_reconnect)
        rh.addWidget(self.chk_auto_reconnect)
        bl.addWidget(row)

        f.addWidget(self.brush_panel)

        d.setWidget(w)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, d)

        # Left dock: Mapper debug
        md = QtWidgets.QDockWidget("映射快捷调试")
        mw = QtWidgets.QWidget()
        mf = QtWidgets.QFormLayout(mw)
        self.map_spins = []
        for group, label in (("board", "纸面坐标 XYZ °"), ("brush", "毛笔/安装 XYZ °")):
            row = QtWidgets.QWidget()
            hl = QtWidgets.QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            for i, axis in enumerate("XYZ"):
                sp = QtWidgets.QDoubleSpinBox()
                sp.setRange(-180, 180)
                sp.setDecimals(2)
                sp.setSingleStep(0.5)
                arr = self.mapper_debug.board_deg if group == "board" else self.mapper_debug.brush_deg
                sp.setValue(float(arr[i]))
                sp.valueChanged.connect(lambda v, g=group, i=i: self._mapper_spin(g, i, v))
                hl.addWidget(QtWidgets.QLabel(axis))
                hl.addWidget(sp)
                self.map_spins.append((group, i, sp))
            mf.addRow(label, row)
        self.map_name = QtWidgets.QLineEdit()
        self.map_versions = QtWidgets.QComboBox()
        self._refresh_versions()
        bs = QtWidgets.QPushButton("保存映射版本")
        bs.clicked.connect(self._save_mapping)
        bl = QtWidgets.QPushButton("加载 / 回退所选版本")
        bl.clicked.connect(self._load_mapping)
        brs = QtWidgets.QPushButton("重置调试修正")
        brs.clicked.connect(self._reset_mapping)
        mf.addRow("版本名称", self.map_name)
        mf.addRow("历史版本", self.map_versions)
        mf.addRow(bs); mf.addRow(bl); mf.addRow(brs)
        md.setWidget(mw)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, md)

        # Calibration panels connections
        self.calui.startRequested.connect(lambda: self.pose_cal.start())
        self.calui.advanceRequested.connect(lambda: self.pose_cal.advance())
        self.calui.retryRequested.connect(lambda: self.pose_cal.retry())
        self.calui.cancelRequested.connect(lambda: self.pose_cal.cancel())
        self.calui.commitRequested.connect(self._cal_commit)
        self.calui.loadRequested.connect(self._cal_load)
        self._refresh_cal_profiles()

        self.hallcalui.startRequested.connect(lambda: self._hall_ui_call(self.hall_cal.start, self.state.get("brush"), self.state.get("pose")))
        self.hallcalui.captureRequested.connect(lambda: self._hall_ui_call(self.hall_cal.arm))
        self.hallcalui.undoRequested.connect(lambda: self._hall_ui_call(self.hall_cal.undo))
        self.hallcalui.solveRequested.connect(lambda: self._hall_ui_call(self.hall_cal.solve))
        self.hallcalui.cancelRequested.connect(lambda: self._hall_ui_call(self.hall_cal.cancel))
        self.hallcalui.commitRequested.connect(lambda name: self._hall_ui_call(self.hall_cal.commit, name or None))
        self.hallcalui.loadRequested.connect(lambda name: self._hall_ui_call(self.hall_cal.load, name))
        self._refresh_hall_profiles()

        # Timer
        self.t = QtCore.QTimer()
        self.t.timeout.connect(self.tick)
        self.t.start(16)

    # ---- Toggle Grid Methods ----
    def _on_view_toggled(self, idx, checked):
        self._view_visible[idx] = checked
        self._rebuild_grid()

    def _rebuild_grid(self):
        # Clear grid: properly detach widgets to avoid C++ object deletion
        for gb in self._group_boxes:
            # Detach the view widget first to prevent C++ cascading delete
            # Only detach direct child (the view widget), not all descendants
            layout = gb.layout()
            if layout:
                for i in range(layout.count()):
                    item = layout.itemAt(i)
                    if item and item.widget():
                        item.widget().setParent(None)
            self._grid_layout.removeWidget(gb)
            gb.setParent(None)
            gb.deleteLater()
        self._group_boxes.clear()

        # Collect visible views
        visible = [(name, widget) for i, (name, widget) in enumerate(self._views) if self._view_visible[i]]

        if not visible:
            return

        n = len(visible)
        n_cols = math.ceil(math.sqrt(n))
        n_rows = math.ceil(n / n_cols)

        for idx, (name, widget) in enumerate(visible):
            row = idx // n_cols
            col = idx % n_cols

            # Create group box for each view
            gb = QtWidgets.QGroupBox(name)
            gb.setStyleSheet("""
                QGroupBox { font-weight:600; font-size:12px; border:1px solid #444; border-radius:4px; margin-top:8px; padding-top:4px; }
                QGroupBox::title { subcontrol-origin:margin; left:8px; padding:0 4px; color:#ccc; }
            """)
            vbox = QtWidgets.QVBoxLayout(gb)
            vbox.setContentsMargins(2, 10, 2, 2)
            vbox.setSpacing(0)
            vbox.addWidget(widget)

            self._grid_layout.addWidget(gb, row, col)
            self._group_boxes.append(gb)

    # ---- Colormap callback ----
    def _on_colormap_change(self):
        self.colormap_params = self.colormap_panel.get_params()
        # Visual patch will pick up new params via HotPatch

    # ---- Brush serial reconnection callbacks ----
    def _update_brush_status(self):
        if hasattr(self, 'br') and self.br:
            s = self.br.status()
            if s.get("connected"):
                self.brush_status_lbl.setText("状态: 已连接 ✓")
                self.brush_status_lbl.setStyleSheet("font-size:12px; color:#0a0;")
                self.brush_port_lbl.setText(f"端口: {s.get('port', '未知')}")
            else:
                self.brush_status_lbl.setText("状态: 未连接 ✗")
                self.brush_status_lbl.setStyleSheet("font-size:12px; color:#c00;")
                self.brush_port_lbl.setText(f"端口: {s.get('port', '未检测到')}")
                if s.get("ever_connected"):
                    self.brush_port_lbl.setText(self.brush_port_lbl.text() + " (曾连接)")
            if s.get("auto_reconnect"):
                self.chk_auto_reconnect.setChecked(True)
            else:
                self.chk_auto_reconnect.setChecked(False)

    def _manual_brush_reconnect(self):
        if hasattr(self, 'br') and self.br:
            self.br.close()
            self.br.auto_reconnect = self.chk_auto_reconnect.isChecked()
            self.br.start()
            self._update_brush_status()

    def _toggle_auto_reconnect(self, checked):
        if hasattr(self, 'br') and self.br:
            self.br.auto_reconnect = checked
            if checked:
                self.br._start_reconnect_thread()
            else:
                self.br._stop_reconnect = True

    # ---- Agent commands ----
    def _register_agent_commands(self):
        self.agent.register_command("reset_reference", lambda x: (self.pose.reset_reference() or {"ok": True}), "重置 UP_R0")
        self.agent.register_command("sam3_health", lambda x: self.sam.health(), "SAM3 /health")
        self.agent.register_parameter("sam3.prompt", lambda: self.sam.prompt, lambda v: (setattr(self.sam, "prompt", str(v)) or self.sam.prompt), "SAM3 text prompt", "string")
        self.agent.register_parameter("sam3.clip_frames", lambda: self.sam.clip_frames, lambda v: (setattr(self.sam, "clip_frames", int(v)) or self.sam.clip_frames), "SAM3 clip frames", "int")
        self.agent.register_parameter("sam3.clip_fps", lambda: self.sam.clip_fps, lambda v: (setattr(self.sam, "clip_fps", int(v)) or self.sam.clip_fps), "SAM3 clip FPS", "int")
        self.agent.register_parameter("vision.ransac_threshold_mm", lambda: self.vest.ransac_mm, lambda v: (setattr(self.vest, "ransac_mm", float(v)) or self.vest.ransac_mm), "RANSAC threshold", "float")
        self.agent.register_parameter("vision.erode_px", lambda: self.vest.erode_px, lambda v: (setattr(self.vest, "erode_px", int(v)) or self.vest.erode_px), "Mask erosion", "int")
        for i, n in enumerate(("x", "y", "z")):
            self.agent.register_parameter("mapper.board_" + n + "_deg", lambda i=i: float(self.mapper_debug.board_deg[i]), lambda v, i=i: self.mapper_debug.set_board(i, v), "Board-frame debug rotation", "float")
            self.agent.register_parameter("mapper.brush_" + n + "_deg", lambda i=i: float(self.mapper_debug.brush_deg[i]), lambda v, i=i: self.mapper_debug.set_brush(i, v), "Brush/mount debug rotation", "float")
        self.agent.register_command("mapper.reset", lambda a: self.mapper_debug.reset(), "Reset mapper debug override")
        self.agent.register_command("mapper.save", lambda a: self.mapper_debug.save(a.get("name")), "Save mapper version")
        self.agent.register_command("mapper.load", lambda a: self.mapper_debug.load(a["name"]), "Rollback/load mapper version")
        self.agent.register_command("mapper.list", lambda a: self.mapper_debug.list_versions(), "List mapper versions")
        self.agent.register_command("calibration.start", lambda a: self.pose_cal.start(), "Start pose mapping calibration")
        self.agent.register_command("calibration.next", lambda a: self.pose_cal.advance(), "Advance/skip calibration stage")
        self.agent.register_command("calibration.retry", lambda a: self.pose_cal.retry(), "Retry current calibration stage")
        self.agent.register_command("calibration.cancel", lambda a: self.pose_cal.cancel(), "Cancel calibration")
        self.agent.register_command("calibration.commit", lambda a: self.pose_cal.commit(a.get("name")), "Commit mapping profile")
        self.agent.register_command("calibration.list", lambda a: self.pose_cal.list_profiles(), "List mapping profiles")
        self.agent.register_command("calibration.load", lambda a: self.pose_cal.load_profile(a["name"]), "Load/rollback mapping profile")
        self.agent.register_command("hall_extrinsic.start", lambda a: self.hall_cal.start(self.state.get("brush"), self.state.get("pose")), "开始 Hall 外参标定")
        self.agent.register_command("hall_extrinsic.capture", lambda a: self.hall_cal.arm(), "采集一个静止姿态")
        self.agent.register_command("hall_extrinsic.undo", lambda a: self.hall_cal.undo(), "撤销最近一组姿态")
        self.agent.register_command("hall_extrinsic.solve", lambda a: self.hall_cal.solve(), "多姿态求解候选外参")
        self.agent.register_command("hall_extrinsic.cancel", lambda a: self.hall_cal.cancel(), "取消 Hall 外参标定")
        self.agent.register_command("hall_extrinsic.commit", lambda a: self.hall_cal.commit(a.get("name")), "验证通过后保存")
        self.agent.register_command("hall_extrinsic.list", lambda a: self.hall_cal.list_profiles(), "列出外参版本")
        self.agent.register_command("hall_extrinsic.load", lambda a: self.hall_cal.load(a["name"]), "加载/回退 Hall 外参")

    # ---- Tick loop ----
    def tick(self):
        # Update brush serial status in UI
        self._update_brush_status()

        c = self.hp.poll()
        if c is not None:
            self.state["camera"] = c

        b = self.br.latest()
        if b:
            self.state["brush"] = b
            self.state["pose"] = self.mapper_debug.apply(self.pose.map_frame(b))
            self.state["hall"] = self.hall.process(b, self.state.get("pose"))
            self.hall_cal.update(b, self.state.get("pose"))
            if self.state["hall"] is not None:
                self.state["hall"]["runtime_mapping"] = self.hall_cal.runtime_mapping_status()
            self.pose_cal.update(b, self.state.get("pose"))
        else:
            self.state["hall"] = None

        self.calui.update_state(self.pose_cal.state())
        self.hallui.update_data(self.state.get("hall"))
        self.hallcalui.update_state(self.hall_cal.state())

        # Camera → SAM3
        c = self.state["camera"]
        if c is not None:
            rgb = c.get("rgb")
            fid = int(c.get("packet_id", -1))
            hns = int(c.get("host_ns", time.perf_counter_ns()))
            if rgb is not None and fid != self.last_submitted:
                self.sam.submit(rgb, fid, hns, camera_context=c)
                self.last_submitted = fid

        sr = self.sam.latest()
        if sr is not None and sr.frame_id != self.last_sam_result:
            self.last_sam_result = sr.frame_id
            if sr.mask is not None:
                vp = self.vest.estimate(sr.mask, sr.camera_context)
                d = {"sam_frame_id": sr.frame_id, "sam_score": sr.score, "sam_latency_ms": sr.latency_ms, "sam_error": sr.error}
                if vp:
                    d.update({k: v for k, v in vp.items() if k != "mask"})
                pose = self.state.get("pose")
                if vp and vp.get("ok") and pose and pose.get("reference_locked"):
                    ia = np.array([pose.get("brush_axis_x"), pose.get("brush_axis_y"), pose.get("brush_axis_z")], float)
                    cmp = compare_axes(ia, vp["axis_camera"], self.Rbc)
                    if cmp:
                        d.update(cmp)
                    d["imu_axis_board"] = ia
                    d["imu_pose_quality"] = pose.get("pose_quality")
                self.last_validation = d
                self.val.update_result(d)
                txt = "axis cam " + np.array2string(vp["axis_camera"], precision=3) if vp and vp.get("ok") else "vision fit failed"
                self.maskview.set(overlay(sr.camera_context.get("rgb"), sr.mask, txt))
            else:
                self.last_validation = {"sam_frame_id": sr.frame_id, "sam_error": sr.error, "sam_latency_ms": sr.latency_ms}
                self.val.update_result(self.last_validation)

        # Visual patch with colormap
        o = self.patch.run(self.state, {
            "near": self.colormap_params["near_mm"],
            "far": self.colormap_params["far_mm"],
            "colormap": self.colormap_params,
        })
        self.rgb.set(o.get("rgb"))

        # Apply colormap to depth view using RAW depth_mm from camera
        c = self.state.get("camera")
        if c is not None and c.get("depth_mm") is not None:
            d = c["depth_mm"]
            params = self.colormap_params
            print(f"[DEBUG TICK] depth range: {d.min()}-{d.max()}, valid: {(d>0).sum()}/{d.size}, params: near={params['near_mm']}, far={params['far_mm']}, dmin={params['depth_min']}, dmax={params['depth_max']}")
            dep_colored = apply_colormap(d, **params)
            if dep_colored is not None:
                self.dep.set(dep_colored)
                print(f"[DEBUG TICK] depth colored shape: {dep_colored.shape}, range: {dep_colored.min()}-{dep_colored.max()}")

        # SAM3 mask overlay
        sr = self.sam.latest()
        if sr is not None and sr.mask is not None:
            rgb_ctx = sr.camera_context.get("rgb") if sr.camera_context else None
            if rgb_ctx is not None:
                self.maskview.set(overlay(rgb_ctx, sr.mask, "SAM3 mask"))

        # Agent publish
        rs = {
            "camera": self.hp.status(),
            "brush_port": self.br.port,
            "brush_error": self.br.error,
            "reference": self.pose.reference_state,
            "sam_last_frame": self.last_sam_result,
            "sam_prompt": self.sam.prompt,
            "camera_to_board_enabled": self.Rbc is not None
        }
        self.status.setPlainText(json.dumps(rs, ensure_ascii=False, indent=2))
        self.agent.publish("camera", rs["camera"])
        self.agent.publish("brush", {"port": self.br.port, "error": self.br.error})
        self.agent.publish("pose", self.state.get("pose") or {"reference_state": self.pose.reference_state, "reference_locked": False})
        srn = self.sam.latest()
        self.agent.publish("sam3", {"last_frame": self.last_sam_result, "prompt": self.sam.prompt, "last_error": getattr(srn, "error", "") if srn else "waiting", "latency_ms": getattr(srn, "latency_ms", None) if srn else None, "score": getattr(srn, "score", None) if srn else None})
        self.agent.publish("vision", self.last_validation or {"ok": False, "reason": "waiting"})
        self.agent.publish("validation", self.last_validation or {})
        self.agent.publish("mapper", self.mapper_debug.state())
        self.agent.publish("calibration", self.pose_cal.state())
        self.agent.publish("hall", self.state.get("hall") or {"present": False})
        self.agent.publish("hall_extrinsic", self.hall_cal.state())
        vp_scene = self.last_validation or {}
        if "origin" in vp_scene and "origin_camera_mm" not in vp_scene:
            vp_scene = dict(vp_scene, origin_camera_mm=vp_scene["origin"])
        self.scene3d.update_scene(self.state.get("camera"), self.state.get("pose"), vp_scene, self.state.get("hall"))
        self.agent.publish("scene3d", {"imu_pose_available": bool(self.state.get("pose") and self.state["pose"].get("reference_locked")), "vision_pose_available": bool(vp_scene.get("ok")), "camera_to_board_enabled": self.Rbc is not None, "hall_present": bool(self.state.get("hall") and self.state["hall"].get("present")), "note": "vision geometry remains camera-frame until camera_to_board is enabled"})
        now = time.monotonic()
        if now - self._last_agent_flush >= 1:
            self.agent.flush_state()
            self._last_agent_flush = now

    # ---- Calibration UI callbacks ----
    def _refresh_hall_profiles(self):
        self.hallcalui.set_profiles(self.hall_cal.list_profiles())

    def _hall_ui_call(self, callback, *args):
        try:
            result = callback(*args)
            self.hallcalui.update_state(self.hall_cal.state())
            self._refresh_hall_profiles()
            if isinstance(result, dict) and result.get("status") == "UNVERIFIED_BASELINE":
                QtWidgets.QMessageBox.warning(self, "恢复原始映射",
                    "已恢复未标定的原始 Hall 安装方向。该方向仅用于安全回退，并非通过外参验证的解。")
            return result
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "霍尔外参标定", str(e))
            return None

    def _refresh_cal_profiles(self):
        self.calui.set_profiles(self.pose_cal.list_profiles())

    def _cal_commit(self, name):
        try:
            self.pose_cal.commit(name or None)
            self._refresh_cal_profiles()
            self._refresh_versions()
        except Exception as e:
            self.status.setPlainText("校准保存错误: " + repr(e))

    def _cal_load(self, name):
        if not name:
            return
        try:
            self.pose_cal.load_profile(name)
            self._sync_mapper_spins()
            self._refresh_versions()
            self._refresh_cal_profiles()
        except Exception as e:
            self.status.setPlainText("校准回退错误: " + repr(e))

    def _mapper_spin(self, group, i, v):
        (self.mapper_debug.set_board if group == "board" else self.mapper_debug.set_brush)(i, v)

    def _refresh_versions(self):
        cur = self.mapper_debug.active_version
        self.map_versions.clear()
        for d in self.mapper_debug.list_versions():
            self.map_versions.addItem(d["name"])
        if cur:
            j = self.map_versions.findText(cur)
            if j >= 0:
                self.map_versions.setCurrentIndex(j)

    def _sync_mapper_spins(self):
        for g, i, sp in self.map_spins:
            a = self.mapper_debug.board_deg if g == "board" else self.mapper_debug.brush_deg
            sp.blockSignals(True)
            sp.setValue(float(a[i]))
            sp.blockSignals(False)

    def _save_mapping(self):
        self.mapper_debug.save(self.map_name.text().strip() or None)
        self._refresh_versions()

    def _load_mapping(self):
        n = self.map_versions.currentText()
        if n:
            self.mapper_debug.load(n)
            self._sync_mapper_spins()

    def _reset_mapping(self):
        self.mapper_debug.reset()
        self._sync_mapper_spins()

    def check_health(self):
        try:
            self.status.setPlainText(json.dumps(self.sam.health(), ensure_ascii=False, indent=2))
        except Exception as e:
            self.status.setPlainText("SAM3 health ERROR: " + repr(e))

    def closeEvent(self, e):
        self.agent.close()
        self.sam.close()
        self.hp.close()
        self.br.close()
        super().closeEvent(e)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--camera-backend", choices=["direct", "shm", "none"], default="shm")
    p.add_argument("--port")
    p.add_argument("--baud", type=int, default=921600)
    p.add_argument("--heading-mode", choices=["startup_reference", "relative", "calibration_session"], default="startup_reference")
    p.add_argument("--no-brush", action="store_true")
    p.add_argument("--sam3-url")
    p.add_argument("--sam3-prompt")
    p.add_argument("--agent-host", default="127.0.0.1")
    p.add_argument("--agent-port", type=int, default=8765)
    a = p.parse_args()
    q = QtWidgets.QApplication(sys.argv)
    q.setStyle("Fusion")
    # Dark palette
    pal = QtGui.QPalette()
    pal.setColor(QtGui.QPalette.Window, QtGui.QColor(30, 30, 30))
    pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(220, 220, 220))
    pal.setColor(QtGui.QPalette.Base, QtGui.QColor(25, 25, 25))
    pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(40, 40, 40))
    pal.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor(255, 255, 220))
    pal.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor(0, 0, 0))
    pal.setColor(QtGui.QPalette.Text, QtGui.QColor(220, 220, 220))
    pal.setColor(QtGui.QPalette.Button, QtGui.QColor(50, 50, 50))
    pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(220, 220, 220))
    pal.setColor(QtGui.QPalette.BrightText, QtGui.QColor(255, 0, 0))
    pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(0, 120, 215))
    pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))
    q.setPalette(pal)
    w = Win(a)
    w.show()
    return q.exec()

if __name__ == "__main__":
    raise SystemExit(main())
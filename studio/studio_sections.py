"""Local navigation presets; never create extra copies of camera/3D views."""
from __future__ import annotations

try:
    from .learning_support import anchor as _help_anchor
except ImportError:
    from learning_support import anchor as _help_anchor
from PySide6 import QtCore, QtWidgets

SECTIONS = (
    ("overview", "总览"), ("sensors", "传感器"), ("camera", "相机"),
    ("calibration", "标定"), ("diagnostics", "诊断"), ("handoff", "RC 交接"),
)

VIEW_PRESETS = {
    "overview": ("采集质量", "彩色图像", "三维场景", "霍尔磁通"),
    "sensors": ("采集质量", "霍尔磁通", "三维场景"),
    "camera": ("彩色图像", "深度图", "SAM3 掩膜叠加"),
    "calibration": ("姿态映射校准", "霍尔外参标定", "三维场景"),
    "diagnostics": ("采集质量", "IMU ↔ 视觉验证", "Agent 操作审计"),
    "handoff": ("RC Studio 数据交接",),
}


class SectionNavigation(QtWidgets.QWidget):
    sectionChanged = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("studio_navigation")
        wrapper = QtWidgets.QHBoxLayout(self)
        wrapper.setContentsMargins(8, 5, 8, 5)
        wrapper.setSpacing(8)
        scroll = QtWidgets.QScrollArea()
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidgetResizable(False)
        scroll.setFixedHeight(48)
        rail = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(rail)
        lay.setContentsMargins(1, 0, 1, 0)
        lay.setSpacing(6)
        self.buttons = {}
        for key, label in SECTIONS:
            btn = _help_anchor(QtWidgets.QToolButton(), 'eb.nav.' + key)
            btn.setText(label)
            btn.setCheckable(True)
            btn.setMinimumHeight(33)
            btn.setToolTip("RC Studio 独立运行；本页只交换数据清单" if key == "handoff" else label)
            btn.clicked.connect(lambda checked=False, k=key: self.select(k))
            lay.addWidget(btn)
            self.buttons[key] = btn
        rail.adjustSize()
        scroll.setWidget(rail)
        wrapper.addWidget(scroll, 1)
        self.current = None

    def select(self, key, *, emit=True):
        if key not in self.buttons:
            raise ValueError(key)
        self.current = key
        for name, button in self.buttons.items():
            button.blockSignals(True)
            button.setChecked(name == key)
            button.blockSignals(False)
        if emit:
            self.sectionChanged.emit(key)

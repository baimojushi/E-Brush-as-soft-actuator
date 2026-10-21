from __future__ import annotations

import json
from PySide6 import QtCore, QtWidgets


class PoseCalibrationPanel(QtWidgets.QWidget):
    startRequested = QtCore.Signal()
    advanceRequested = QtCore.Signal()
    retryRequested = QtCore.Signal()
    cancelRequested = QtCore.Signal()
    confirmUp0Requested = QtCore.Signal()
    commitRequested = QtCore.Signal(str)
    loadRequested = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        self.title = QtWidgets.QLabel("姿态映射校准 · UP0 / UP_R0")
        self.title.setStyleSheet("font-size:19px;font-weight:600")
        self.title.setWordWrap(True)
        self.stage = QtWidgets.QLabel("IDLE")
        self.stage.setStyleSheet("font-size:22px;font-weight:700")
        self.stage.setWordWrap(True)
        self.msg = QtWidgets.QLabel("准备就绪")
        self.msg.setWordWrap(True)

        self.definition = QtWidgets.QLabel(
            "UP0 正确定义：笔尖向下，笔尾向上，笔杆垂直纸面；"
            "笔上的 R0 非对称标记朝纸面 +X（书写者右侧）。\n"
            "注意：六轴 IMU 只能验证是否竖直及静止，不能自行识别 R0 的绝对方向。"
        )
        self.definition.setWordWrap(True)
        self.definition.setStyleSheet("padding:8px; border:1px solid #777; border-radius:4px;")

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)

        # Long action labels previously occupied a single 700+ px row,
        # silently setting an unusably large minimum width on this pane.
        # Keep every action but use a compact two-column control grid.
        row = QtWidgets.QGridLayout()
        row.setHorizontalSpacing(5)
        row.setVerticalSpacing(5)
        self.start_button = QtWidgets.QPushButton("开始校准")
        self.start_button.clicked.connect(self.startRequested.emit)
        self.up0_button = QtWidgets.QPushButton("确认 R0 朝 +X，开始采集 UP0")
        self.up0_button.clicked.connect(self.confirmUp0Requested.emit)
        self.next_button = QtWidgets.QPushButton("下一步（不可跳过验证）")
        self.next_button.clicked.connect(self.advanceRequested.emit)
        self.retry_button = QtWidgets.QPushButton("重试当前步骤")
        self.retry_button.clicked.connect(self.retryRequested.emit)
        self.cancel_button = QtWidgets.QPushButton("取消校准")
        self.cancel_button.clicked.connect(self.cancelRequested.emit)
        self.up0_button.setText("确认 R0 朝 +X，采集 UP0")
        self.next_button.setText("下一步（自动验证）")
        for b in (self.start_button, self.up0_button, self.next_button,
                  self.retry_button, self.cancel_button):
            b.setMinimumWidth(0)
        row.addWidget(self.start_button, 0, 0)
        row.addWidget(self.retry_button, 0, 1)
        row.addWidget(self.up0_button, 1, 0, 1, 2)
        row.addWidget(self.next_button, 2, 0)
        row.addWidget(self.cancel_button, 2, 1)
        row.setColumnStretch(0, 1)
        row.setColumnStretch(1, 1)

        save = QtWidgets.QGridLayout()
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText("映射配置名称")
        self.commit = QtWidgets.QPushButton("验证通过后保存")
        self.commit.clicked.connect(lambda: self.commitRequested.emit(self.name.text().strip()))
        save.addWidget(self.name, 0, 0)
        save.addWidget(self.commit, 0, 1)
        save.setColumnStretch(0, 2)
        save.setColumnStretch(1, 1)

        versions = QtWidgets.QGridLayout()
        self.profiles = QtWidgets.QComboBox()
        self.load = QtWidgets.QPushButton("加载 / 回退")
        self.load.clicked.connect(lambda: self.loadRequested.emit(self.profiles.currentText()))
        versions.addWidget(self.profiles, 0, 0)
        versions.addWidget(self.load, 0, 1)
        versions.setColumnStretch(0, 2)
        versions.setColumnStretch(1, 1)

        self.detail = QtWidgets.QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(100)
        for w in (self.title, self.definition, self.stage, self.msg, self.progress):
            layout.addWidget(w)
        layout.addLayout(row)
        layout.addLayout(save)
        layout.addLayout(versions)
        layout.addWidget(self.detail, 1)
        self.update_state({"stage": "IDLE", "message": "Ready"})

    def set_profiles(self, items):
        current = self.profiles.currentText()
        self.profiles.clear()
        for item in items:
            self.profiles.addItem(item["name"])
        i = self.profiles.findText(current)
        if i >= 0:
            self.profiles.setCurrentIndex(i)

    def update_state(self, state):
        stage = state.get("stage", "?")
        stage_zh = {
            "IDLE": "空闲", "ON_STAND": "第 1 步 · 毛笔在笔架上",
            "PICK_UP": "第 2 步 · 拿起毛笔", "UP_R0": "第 3 步 · UP0 参考姿态",
            "X_TEST": "第 4 步 · +X 倾斜验证", "Y_TEST": "第 5 步 · +Y 倾斜验证",
            "ROLL_TEST": "第 6 步 · 绕笔轴旋转验证",
            "REVIEW": "检查校准结果", "COMMITTED": "已保存",
        }
        msg_zh = {
            "Ready": "准备就绪",
            "Keep brush still on stand": "请将毛笔放在笔架上，保持 1.5 秒静止",
            "Remove brush from stand": "请从笔架拿起毛笔，软件需检测到真实运动",
            "Place brush UP0 and confirm R0 points to board +X": (
                "将笔尖向下、笔杆竖直，R0 标记朝纸面 +X（书写者右侧）。"
                "确认无误后点击“确认 R0 朝 +X”，再保持静止 1.5 秒。"),
            "Hold UP0 still until reference locks": (
                "已人工确认 R0 朝 +X。请保持笔尖向下、笔杆竖直且静止，等待锁定。"),
            "Tilt brush tail toward board +X / right": "请将笔尾向纸面 +X / 右侧倾斜",
            "Tilt brush tail toward board +Y / away": "请将笔尾向纸面 +Y / 远离书写者倾斜",
            "Keep shaft near vertical and rotate brush about its shaft": (
                "笔杆接近竖直，绕笔杆轴线旋转至少 45°，验证相对旋转"),
            "Review and commit mapping": "确认所有步骤通过，再保存配置",
            "Mapping profile committed": "映射已保存",
            "Reference lost; repeat UP0 confirmation": "参考失效，请重新确认 UP0",
        }
        self.stage.setText(stage_zh.get(stage, stage))
        self.msg.setText(msg_zh.get(state.get("message", ""), state.get("message", "")))
        self.progress.setValue(int(state.get("quality_score", 0)))
        self.up0_button.setEnabled(stage == "UP_R0" and state.get("up0_confirmation_required", False))
        self.next_button.setEnabled(False)  # Stages auto-advance only after passing verification.
        self.retry_button.setEnabled(stage not in ("IDLE", "COMMITTED"))
        self.cancel_button.setEnabled(stage not in ("IDLE", "COMMITTED"))
        self.commit.setEnabled(bool(state.get("can_commit")))
        self.detail.setPlainText(json.dumps(state, ensure_ascii=False, indent=2))

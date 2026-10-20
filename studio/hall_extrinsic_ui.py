"""Chinese UI for versioned Hall→Brush extrinsic calibration."""
from __future__ import annotations
import json
from PySide6 import QtWidgets, QtCore


class HallExtrinsicPanel(QtWidgets.QWidget):
    startRequested=QtCore.Signal()
    captureRequested=QtCore.Signal()
    undoRequested=QtCore.Signal()
    solveRequested=QtCore.Signal()
    cancelRequested=QtCore.Signal()
    commitRequested=QtCore.Signal(str)
    loadRequested=QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        outer=QtWidgets.QVBoxLayout(self)
        title=QtWidgets.QLabel("霍尔 ↔ 毛笔外参标定")
        title.setStyleSheet("font-size:22px;font-weight:600")
        self.stage=QtWidgets.QLabel("待开始")
        self.stage.setStyleSheet("font-size:21px;font-weight:600")
        self.hint=QtWidgets.QLabel(
            "先在“姿态映射校准”完成 UP_R0 参考锁定。"
            "在尽量固定的空间位置旋转毛笔，每改变一个朝向后静止采样。")
        self.hint.setWordWrap(True)
        self.progress=QtWidgets.QProgressBar()
        self.progress.setRange(0,11)
        self.train=QtWidgets.QLabel("训练：0 / 8")
        self.val=QtWidgets.QLabel("独立验证：0 / 3")
        self.pending=QtWidgets.QLabel("当前姿态：未采集")
        stat=QtWidgets.QHBoxLayout()
        for w in [self.train,self.val,self.pending]:
            stat.addWidget(w)

        sample_bar=QtWidgets.QHBoxLayout()
        self.start=QtWidgets.QPushButton("开始新标定")
        self.capture=QtWidgets.QPushButton("采集当前静止姿态")
        self.undo=QtWidgets.QPushButton("撤销上次")
        for b,sig in [(self.start,self.startRequested),(self.capture,self.captureRequested),(self.undo,self.undoRequested)]:
            b.clicked.connect(sig.emit);sample_bar.addWidget(b)

        action_bar=QtWidgets.QHBoxLayout()
        self.solve=QtWidgets.QPushButton("求解候选外参")
        self.cancel=QtWidgets.QPushButton("取消会话")
        self.solve.clicked.connect(self.solveRequested.emit)
        self.cancel.clicked.connect(self.cancelRequested.emit)
        action_bar.addWidget(self.solve);action_bar.addWidget(self.cancel)

        versions=QtWidgets.QFormLayout()
        self.name=QtWidgets.QLineEdit()
        self.name.setPlaceholderText("例如：hall_good_01（可留空自动命名）")
        self.commit=QtWidgets.QPushButton("验证通过 → 接受并保存")
        self.commit.clicked.connect(lambda:self.commitRequested.emit(self.name.text().strip()))
        self.profile=QtWidgets.QComboBox()
        self.rollback=QtWidgets.QPushButton("加载 / 回退选中版本")
        self.rollback.clicked.connect(lambda:self.loadRequested.emit(str(self.profile.currentData())))
        version_row=QtWidgets.QHBoxLayout()
        version_row.addWidget(self.profile);version_row.addWidget(self.rollback)
        versions.addRow("新版本名称",self.name)
        versions.addRow(self.commit)
        versions.addRow("历史版本",version_row)

        self.report=QtWidgets.QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setPlaceholderText("这里将显示训练与独立验证结果。")
        self.report.setMinimumHeight(200)

        outer.addWidget(title);outer.addWidget(self.stage);outer.addWidget(self.hint)
        outer.addWidget(self.progress);outer.addLayout(stat)
        outer.addLayout(sample_bar);outer.addLayout(action_bar);outer.addLayout(versions)
        outer.addWidget(self.report,1)

        warning=QtWidgets.QLabel(
            "注意：此流程假设磁场在测量区域内近似均匀、静止。"
            "有随笔运动的永磁体或靠近磁性钢板时，可能无法满足假设；验证失败会阻止保存。")
        warning.setWordWrap(True)
        outer.addWidget(warning)
        self.update_state({"stage":"IDLE","train_count":0,"validation_count":0,
                           "candidate":None,"validation":None,"message":"准备就绪"})

    def set_profiles(self,profiles):
        prior=self.profile.currentData()
        self.profile.clear()
        for item in profiles:
            name=item["name"]
            label=("原始映射（未标定，可回退）" if not item.get("verified",True) else name)
            self.profile.addItem(label,userData=name)
        ix=self.profile.findData(prior)
        if ix>=0:self.profile.setCurrentIndex(ix)
        self.rollback.setEnabled(bool(profiles))

    def update_state(self,s):
        stages={"IDLE":"未开始","COLLECT":"采集训练姿态",
                "VALIDATE":"独立验证","READY":"验证通过，等待保存",
                "COMMITTED":"已保存外参","INVALID":"参考失效，请重新开始"}
        stage=s.get("stage","IDLE")
        self.stage.setText(stages.get(stage,stage))
        self.hint.setText(s.get("message",""))
        n=int(s.get("train_count",0));v=int(s.get("validation_count",0))
        self.train.setText(f"训练：{n} / {s.get('train_target',8)}")
        self.val.setText(f"独立验证：{v} / {s.get('validation_target',3)}")
        self.progress.setValue(min(8,n)+min(3,v))
        frames=int(s.get("pending_frames",0))
        sec=float(s.get("pending_hold_s",0))
        self.pending.setText(
            f"采样中：{frames} 帧，保持 {sec:.2f}s" if s.get("armed")
            else "当前姿态：未开始采集")
        self.start.setEnabled(stage in ("IDLE","INVALID","COMMITTED","COLLECT","VALIDATE","READY"))
        self.capture.setEnabled(stage in ("COLLECT","VALIDATE") and not s.get("armed",False))
        self.undo.setEnabled((n+v)>0 and stage in ("COLLECT","VALIDATE","READY"))
        self.solve.setEnabled(bool(s.get("can_solve")))
        self.cancel.setEnabled(stage in ("COLLECT","VALIDATE","READY","INVALID"))
        self.commit.setEnabled(bool(s.get("can_commit")))

        candidate=s.get("candidate")
        validation=s.get("validation")
        if candidate:
            mat=candidate["R_brush_from_hall"]
            lines=[
                f"训练数量：{candidate['training_count']}",
                f"训练世界磁场 RMS：{candidate['training_rms_mT']:.5f} mT",
                f"旧版训练 RMS：{candidate['baseline_training_rms_mT']:.5f} mT",
                f"旋转姿态跨度：{candidate['excitation']['pose_span_deg']:.1f}°",
                f"霍尔方向变化跨度：{candidate['excitation']['hall_vector_span_deg']:.1f}°",
                f"可观测性比值：{candidate['excitation']['excitation_ratio']:.3f}",
                f"磁场模长 CV：{candidate['field_magnitude_cv']:.1%}",
                "",
                "候选 R_brush_from_hall:",
            ]
            lines += ["  "+", ".join(f"{float(x):+.6f}" for x in r) for r in mat]
            if candidate.get("R_imu_from_hall"):
                lines+=["", "传感器外参 R_imu_from_hall:"]
                lines += ["  "+", ".join(f"{float(x):+.6f}" for x in r)
                          for r in candidate["R_imu_from_hall"]]
        else:
            lines=[
                "标定步骤：",
                "1. 先完成 UP_R0，保持当前启动参考不变",
                "2. 尽量围绕固定空间点旋转毛笔；每个姿态静止 0.75 秒",
                "3. 收集至少 8 个分布在不同轴向的训练姿态",
                "4. 求解后再收集 3 个未参与求解的独立验证姿态",
                "5. 验证通过才能保存，历史版本可一键回退",
            ]
        if validation:
            lines+=["", "独立验证："+("通过 ✓" if validation["passed"] else "未通过"),
                    f"验证 RMS：{validation['rms_mT']:.5f} mT",
                    f"原始版本验证 RMS：{validation['baseline_rms_mT']:.5f} mT",
                    f"验证阈值：{validation['threshold_mT']:.5f} mT"]
        self.report.setPlainText("\n".join(lines))

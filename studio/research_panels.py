"""Passive quality and handoff views for E-Brush Studio.

Both panels intentionally have no dependency on RC Studio's training code.
"""
from __future__ import annotations

try:
    from .learning_support import anchor as _help_anchor
except ImportError:
    from learning_support import anchor as _help_anchor
import time
from PySide6 import QtCore, QtWidgets


def _heading(text, name="section_heading"):
    label = QtWidgets.QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    return label


def _caption(text):
    return _heading(text, "studio_caption")


class _Metric(QtWidgets.QWidget):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("metric_card")
        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(4)
        box.addWidget(_caption(title))
        self.value = _heading("—", "metric_value")
        box.addWidget(self.value)
        self.setMinimumWidth(125)


class AcquisitionQualityPanel(QtWidgets.QWidget):
    """Dashboard driven by monotonically increasing parser counters."""
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addWidget(_heading("实时采集质量"))
        layout.addWidget(_caption("只读观测 · 来源：BRUSH-V1 解码器计数与最新传感器状态"))
        self.summary = _heading("等待设备数据", "quality_waiting")
        layout.addWidget(self.summary)
        row = QtWidgets.QGridLayout()
        row.setSpacing(8)
        self.rate = _Metric("主机接收速率（近 4 秒）")
        self.loss = _Metric("帧编号缺口率（近 4 秒）")
        self.imu = _Metric("IMU 最新状态")
        self.hall = _Metric("Hall 最新状态")
        for n, widget in enumerate((self.rate, self.loss, self.imu, self.hall)):
            row.addWidget(widget, n // 2, n % 2)
        layout.addLayout(row)
        self.details = QtWidgets.QLabel("等待状态计数")
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        layout.addWidget(_caption("200 Hz 是设备设计目标。请以实测接收率验证实际频率；UI 轮询与设备采样分别统计。"))
        layout.addStretch()

    def update_snapshot(self, data):
        level = data.get("severity", "waiting")
        self.summary.setObjectName("quality_" + level)
        self.summary.setText(data.get("summary", "—"))
        self.summary.style().unpolish(self.summary)
        self.summary.style().polish(self.summary)
        hz = data.get("host_receive_hz")
        pct = data.get("frame_gap_percent_window")
        self.rate.value.setText("—" if hz is None else f"{hz:.1f} Hz")
        self.loss.value.setText("—" if pct is None else f"{pct:.2f}%")
        self.imu.value.setText(data.get("imu", "—"))
        self.hall.value.setText(data.get("hall", "—"))
        for label in (self.imu.value, self.hall.value):
            label.setStyleSheet("font-size:15px; font-weight:700")
        flags = ", ".join(data.get("drop_flags") or ()) or "无"
        self.details.setText(
            f"端口：{data.get('port') or '未连接'}  |  累计解码：{data.get('total_decoded_frames', 0):,} 帧\n"
            f"累计编号缺口：{data.get('total_frame_id_gaps', 0):,}  |  最新丢弃标志：{flags}\n"
            f"解析器信息：{data.get('parser_error') or '无已报告错误'}")


class AgentAuditPanel(QtWidgets.QWidget):
    """Display recent operations on the existing AgentControl event stream."""
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(_heading("Agent 操作记录"))
        layout.addWidget(_caption("显示 E-Brush 本地控制接口最近的操作事件；训练和模型操作不属于本服务"))
        self.entries = _help_anchor(QtWidgets.QPlainTextEdit(), 'eb.view.audit')
        self.entries.setReadOnly(True)
        layout.addWidget(self.entries, 1)
        self._last_key = None

    def update_events(self, entries):
        recs = list(entries)[-60:]
        key = (len(recs), recs[-1].get("t_unix") if recs else None)
        if key == self._last_key:
            return
        self._last_key = key
        lines = []
        for rec in recs:
            event = rec.get("event", "event")
            stamp = time.strftime("%H:%M:%S", time.localtime(rec.get("t_unix", 0)))
            subject = rec.get("name") or rec.get("path") or ""
            # Avoid displaying request payloads, tokens, or arbitrary command arguments.
            lines.append(f"{stamp}    {event}    {subject}")
        self.entries.setPlainText("\n".join(lines) if lines else "尚无 Agent 操作")


class RCStudioHandoffPanel(QtWidgets.QWidget):
    """Explicit boundary: only check localhost service and save source manifest."""
    probeRequested = QtCore.Signal()
    exportRequested = QtCore.Signal()

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(14)
        layout.addWidget(_heading("E-Brush → RC Studio 数据交接"))
        layout.addWidget(_caption("两套独立应用 · 无内嵌训练 · 无串口共享 · 不写入 RC Studio 数据库"))
        ownership = _help_anchor(QtWidgets.QGroupBox("责任边界"), 'eb.view.handoff')
        owners = QtWidgets.QVBoxLayout(ownership)
        text = QtWidgets.QLabel(
            "E-Brush Studio：负责设备、传感器、实时采集、标定、原始日志与移交清单。\n\n"
            "RC Studio：独立负责日志解析与清洗、采样片段、ESN 训练、基线评估、模型及实验管理。\n\n"
            "EBrush Motion Predictor V1 是 RC Studio 的内部研究项目，不在此处重复实现。"
        )
        text.setWordWrap(True)
        owners.addWidget(text)
        layout.addWidget(ownership)
        actions = QtWidgets.QHBoxLayout()
        self.btn_probe = _help_anchor(QtWidgets.QPushButton("检查 RC Studio（GET 只读）"), 'eb.handoff.probe')
        self.btn_probe.clicked.connect(self.probeRequested.emit)
        self.btn_export = _help_anchor(QtWidgets.QPushButton("生成数据移交清单…"), 'eb.handoff.export')
        self.btn_export.setObjectName("primary_action")
        self.btn_export.clicked.connect(self.exportRequested.emit)
        actions.addWidget(self.btn_probe)
        actions.addWidget(self.btn_export)
        layout.addLayout(actions)
        self.service = QtWidgets.QLabel("RC Studio 127.0.0.1:8766 · 未检查")
        self.service.setWordWrap(True)
        layout.addWidget(self.service)
        self.export_info = _help_anchor(QtWidgets.QPlainTextEdit(), 'eb.handoff.info')
        self.export_info.setReadOnly(True)
        self.export_info.setPlaceholderText("移交记录（仅本地 JSON 元数据；不会上传、训练或修改原始日志）")
        self.export_info.setMaximumHeight(200)
        layout.addWidget(self.export_info)
        layout.addWidget(_caption("生成清单后，请在 RC Studio 导入 .brlog 或 CSV 原始文件；清单提供数据路径与校验信息。"))
        layout.addStretch()

    def show_probe(self, result):
        self.service.setText(result.get("message", "未知状态") + " · 127.0.0.1:8766")

    def show_export(self, filename, source):
        self.export_info.setPlainText(f"清单已保存：{filename}\n原始数据：{source}\n未复制、转换或上传数据；未触发 RC 训练。")


class AgentWorkflowPanel(QtWidgets.QWidget):
    """Material-inspired workflow timeline sourced from local AgentControl."""
    abortRequested = QtCore.Signal()
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addWidget(_heading("Agent 工作流 · 实时执行时间线"))
        layout.addWidget(_caption(
            "E-Brush 显示采集、标定和数据移交的现场状态；RC Studio 负责工作流审批、导入、训练与指标。"
            "每一次录制都需要操作员确认已做好准备。"))
        self.current = _heading("等待 Agent 工作流", "metric_value")
        layout.addWidget(self.current)
        self.progress = _help_anchor(QtWidgets.QProgressBar(), 'eb.workflow.progress')
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.timeline = _help_anchor(QtWidgets.QPlainTextEdit(), 'eb.workflow.timeline')
        self.timeline.setReadOnly(True)
        self.abort_button = _help_anchor(QtWidgets.QPushButton("紧急终止当前录制"), 'eb.workflow.abort')
        self.abort_button.setObjectName("danger_action")
        self.abort_button.clicked.connect(self.abortRequested.emit)
        self.abort_button.setEnabled(False)
        layout.addWidget(self.abort_button)
        self.timeline.setMinimumHeight(270)
        layout.addWidget(self.timeline, 1)
        self._signature = None

    def update_workflow(self, events, acquisition=None):
        events = list(events)[-100:]
        current = events[-1] if events else None
        state = acquisition or {}
        self.abort_button.setEnabled(bool(state.get("active")))
        signature = (len(events), current.get("t_unix") if current else None,
                     state.get("state"), state.get("frames"))
        if signature == self._signature:
            return
        self._signature = signature
        if current:
            self.current.setText(f"{current['stage']} · {current['status']}")
            self.progress.setValue(round(100 * current.get("progress", 0)))
            lines = [
                f"{time.strftime('%H:%M:%S', time.localtime(item.get('t_unix', 0)))}  "
                f"{item.get('stage', '')} · {item.get('status', '')}  {item.get('message', '')}"
                for item in events
            ]
            self.timeline.setPlainText("\n".join(lines))
            self.timeline.verticalScrollBar().setValue(self.timeline.verticalScrollBar().maximum())
        if state.get("active"):
            self.current.setText(f"正在录制第 {state.get('recording_index')} 份 · {state.get('frames', 0)} 帧")

"""Compact Qt control surface for full-video upload and complete result preview.

Only UI glue; all IO, HTTP retries and ZIP decoding live in background worker.
"""
from __future__ import annotations

try:
    from .learning_support import anchor as _help_anchor
except ImportError:
    from learning_support import anchor as _help_anchor

import json
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets


class SAM3ProductionPanel(QtWidgets.QWidget):
    def __init__(self, jobs, live, preview_callback, live_callback, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.live = live
        self.preview_callback = preview_callback
        self.live_callback = live_callback
        self._selected = ""
        self._last_status = None
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        title = QtWidgets.QLabel("SAM3 完整视频上传 / 掩码下发")
        title.setStyleSheet("font-weight:700")
        v.addWidget(title)
        note = QtWidgets.QLabel("原视频完整解码分块上传；所有帧的掩码按源帧号归档。\n"
                                "在线掩码为异步关键帧，不表示 20 FPS 全帧语义推理。")
        note.setWordWrap(True)
        v.addWidget(note)
        self.upload = _help_anchor(QtWidgets.QPushButton("选择视频并提交完整处理"), 'eb.sam.job.upload')
        self.upload.clicked.connect(self._upload)
        v.addWidget(self.upload)
        row = QtWidgets.QHBoxLayout()
        self.selector = _help_anchor(QtWidgets.QComboBox(), 'eb.sam.job.selector')
        self.selector.currentIndexChanged.connect(self._select)
        row.addWidget(self.selector, 1)
        self.retry = _help_anchor(QtWidgets.QPushButton("失败续跑"), 'eb.sam.job.retry')
        self.retry.clicked.connect(self._retry)
        row.addWidget(self.retry)
        v.addLayout(row)
        row2 = QtWidgets.QHBoxLayout()
        self.pause = _help_anchor(QtWidgets.QPushButton("暂停队列"), 'eb.sam.job.pause')
        self.pause.clicked.connect(self._toggle_pause)
        row2.addWidget(self.pause)
        self.dir_btn = _help_anchor(QtWidgets.QPushButton("打开结果目录"), 'eb.sam.job.directory')
        self.dir_btn.clicked.connect(self._open_dir)
        row2.addWidget(self.dir_btn)
        self.live_btn = _help_anchor(QtWidgets.QPushButton("返回实时掩码"), 'eb.sam.job.live')
        self.live_btn.clicked.connect(self.live_callback)
        row2.addWidget(self.live_btn)
        v.addLayout(row2)
        self.progress = _help_anchor(QtWidgets.QProgressBar(), 'eb.sam.job.progress')
        self.progress.setRange(0, 100)
        v.addWidget(self.progress)
        self.detail = QtWidgets.QLabel("等待上传")
        self.detail.setWordWrap(True)
        v.addWidget(self.detail)
        self.seek = _help_anchor(QtWidgets.QSlider(QtCore.Qt.Horizontal), 'eb.sam.job.seek')
        self.seek.setRange(0, 0)
        self.seek.sliderReleased.connect(lambda: self._preview(self.seek.value()))
        self.seek.valueChanged.connect(lambda i: self.frame_lbl.setText(f"预览目标帧：{i}（松开滑块加载）"))
        v.addWidget(self.seek)
        self.frame_lbl = QtWidgets.QLabel("处理结果：等待")
        v.addWidget(self.frame_lbl)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(800)
        self.refresh()

    def _warn(self, title, exc):
        QtWidgets.QMessageBox.warning(self, title, f"{type(exc).__name__}: {exc}")

    def _upload(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择完整源视频", "", "Videos (*.mp4 *.avi *.mov *.mkv *.webm)")
        if not path:
            return
        try:
            job = self.jobs.enqueue(path, self.live.prompt)
            self._selected = job["job_id"]
            self.refresh()
        except Exception as exc:
            self._warn("视频上传失败", exc)

    def _select(self, _index):
        self._selected = self.selector.currentData() or ""
        self.refresh()

    def _retry(self):
        if not self._selected:
            return
        try:
            self.jobs.retry(self._selected)
            self.refresh()
        except Exception as exc:
            self._warn("无法恢复作业", exc)

    def _toggle_pause(self):
        try:
            if self.jobs.status()["paused"]:
                self.jobs.resume()
            else:
                self.jobs.pause()
            self.refresh()
        except Exception as exc:
            self._warn("队列控制失败", exc)

    def _open_dir(self):
        path = self.jobs.root / self._selected if self._selected else self.jobs.root
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def _preview(self, idx):
        if not self._selected:
            return
        try:
            if self.jobs.status(self._selected)["state"] != "COMPLETED":
                return
            image, row = self.jobs.get_frame(self._selected, idx)
            self.preview_callback(image)
            self.frame_lbl.setText(
                f"原视频帧 {idx} · {len(row['objects'])} 个掩码 · "
                f"状态 {row['status']} · 标签候选(CANDIDATE)")
        except Exception as exc:
            self.frame_lbl.setText(f"预览错误：{exc}")

    def refresh(self):
        try:
            all_jobs = self.jobs.jobs()
            selected = self._selected
            self.selector.blockSignals(True)
            self.selector.clear()
            for job in all_jobs:
                name = f"{job['job_id'][:8]} · {job['state']} · {job['source_filename']}"
                self.selector.addItem(name, job["job_id"])
            if not selected and all_jobs:
                selected = all_jobs[0]["job_id"]
            idx = self.selector.findData(selected)
            if idx >= 0:
                self.selector.setCurrentIndex(idx)
                self._selected = selected
            self.selector.blockSignals(False)
            self.pause.setText("继续队列" if self.jobs.status()["paused"] else "暂停队列")
            if not self._selected:
                return
            job = self.jobs.status(self._selected)
            total = job.get("frames_total")
            done = int(job.get("frames_processed", 0))
            self.progress.setValue(min(100, round(done / total * 100)) if total else 0)
            details = (f"状态：{job['state']} · 已返回 {done}/{total or '?'} 帧 · "
                       f"掩码 {job.get('masks_total', 0)} · 分块 {job.get('chunks_completed', 0)}\n"
                       f"输出：{self.jobs.root / self._selected}\n"
                       f"错误：{job.get('error') or '无'}")
            if details != self._last_status:
                self.detail.setText(details)
                self._last_status = details
            if job["state"] == "COMPLETED":
                mx = max(0, total - 1)
                if self.seek.maximum() != mx:
                    self.seek.setRange(0, mx)
                    self._preview(self.seek.value())
        except Exception as exc:
            self.detail.setText(f"诊断状态读取失败：{type(exc).__name__}: {exc}")

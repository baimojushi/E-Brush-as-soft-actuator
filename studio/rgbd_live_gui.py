"""Standalone live E-Brush RGB-D Studio interface (no missing Studio v08 modules).

Safe boundaries:
* One HP60C SDK owner; SDK queue drained by StudioCaptureSupervisor.
* GUI polling uses read-only .poll snapshot and does not touch the recorder queue.
* SAM3 is opt-in and records candidate annotations only.
* Stop drains writer/inference off the Qt event loop; errors remain visible.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading

import numpy as np


def build_parser():
    p=argparse.ArgumentParser(description="Independent HP60C RGB-D + SAM3 Studio")
    p.add_argument("--sdk-dir",type=Path,required=True)
    p.add_argument("--calibration",type=Path,required=True)
    p.add_argument("--episode",type=Path,required=True)
    p.add_argument("--timestamp-unit",choices=["ns","us","ms"],required=True)
    p.add_argument("--depth-unit-m",type=float,required=True)
    p.add_argument("--registration-verified",action="store_true")
    p.add_argument("--host-clock-mapping-verified",action="store_true")
    p.add_argument("--brush-port",default=None)
    p.add_argument("--pose-calibration",default=None)
    p.add_argument("--enable-sam3",action="store_true")
    p.add_argument("--sam3-url",default="http://127.0.0.1:6014")
    p.add_argument("--clip-frames",type=int,default=5)
    p.add_argument("--request-interval-s",type=float,default=5)
    return p


def make_main_window(args):
    from PySide6 import QtCore,QtGui,QtWidgets
    from host.hp60c_direct import HP60CDirectSDK
    from studio.rgbd_semantic.studio_capture import StudioCaptureConfig,StudioCaptureSupervisor

    class LiveWindow(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("e-Brush RGB-D / SAM3 采集与语义标注")
            self.resize(1120,730)
            self.camera=None
            self.brush=None
            self.recorder=None
            self.ending=False
            self.stop_thread=None
            self.stop_error=None
            self.stop_done=threading.Event()
            self._close_when_finished=False

            root=QtWidgets.QWidget(self)
            self.setCentralWidget(root)
            layout=QtWidgets.QVBoxLayout(root)
            heading=QtWidgets.QLabel("HP60C + E-Brush + SAM3 Meta")
            heading.setStyleSheet("font-size:20px;font-weight:bold")
            layout.addWidget(heading)
            layout.addWidget(QtWidgets.QLabel(
                "采集结果默认处于候选状态；深度配准、标定和时间同步需独立验证。"))
            row=QtWidgets.QHBoxLayout()
            self.btn_start=QtWidgets.QPushButton("开始采集")
            self.btn_stop=QtWidgets.QPushButton("停止并归档")
            self.btn_stop.setEnabled(False)
            row.addWidget(self.btn_start)
            row.addWidget(self.btn_stop)
            layout.addLayout(row)
            frame=QtWidgets.QHBoxLayout()
            self.color_view=QtWidgets.QLabel("等待彩色图")
            self.depth_view=QtWidgets.QLabel("等待深度图")
            for label in (self.color_view,self.depth_view):
                label.setAlignment(QtCore.Qt.AlignCenter)
                label.setMinimumSize(320,240)
                label.setStyleSheet("background:#202028;color:#eeeeee")
                frame.addWidget(label)
            layout.addLayout(frame)
            self.status_text=QtWidgets.QPlainTextEdit()
            self.status_text.setReadOnly(True)
            layout.addWidget(self.status_text)
            self.notice=QtWidgets.QLabel("状态：未启动")
            layout.addWidget(self.notice)
            self.btn_start.clicked.connect(self.start_capture)
            self.btn_stop.clicked.connect(self.stop_capture)
            self.timer=QtCore.QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(80)

        @staticmethod
        def _pixmap_rgb(rgb):
            image=np.ascontiguousarray(rgb[:,:,:3])
            h,w=image.shape[:2]
            qimage=QtGui.QImage(image.data,w,h,w*3,QtGui.QImage.Format_RGB888).copy()
            return QtGui.QPixmap.fromImage(qimage)

        def _display(self,label,pixmap):
            label.setPixmap(pixmap.scaled(label.size(),QtCore.Qt.KeepAspectRatio,
                                          QtCore.Qt.SmoothTransformation))

        def start_capture(self):
            if self.recorder is not None or self.ending:
                return
            self.btn_start.setEnabled(False)
            try:
                if not args.registration_verified:
                    raise ValueError("必须先验证 HP60C 的 RGB/depth 配准")
                import os
                cfg=StudioCaptureConfig(
                    calibration_file=str(args.calibration),
                    episode_dir=str(args.episode),
                    timestamp_unit=args.timestamp_unit,
                    depth_unit_m=args.depth_unit_m,
                    registration_verified=True,
                    host_clock_mapping_verified=args.host_clock_mapping_verified,
                    sam3_url=args.sam3_url,
                    sam3_api_key=os.getenv("SAM3_API_KEY",""),
                    sam3_clip_frames=args.clip_frames,
                    request_interval_s=args.request_interval_s,
                    enable_sam3=args.enable_sam3,
                    pose_calibration_file=args.pose_calibration,
                )
                self.camera=HP60CDirectSDK(args.sdk_dir)
                self.camera.start()
                if args.brush_port:
                    from studio.brush_serial import BrushSerial
                    self.brush=BrushSerial(port=args.brush_port)
                    self.brush.start()
                self.recorder=StudioCaptureSupervisor(self.camera,cfg,self.brush)
                # Do not block acquisition while synchronously probing SAM3;
                # the model runs in a separate queued worker.
                self.recorder.start(check_service=False)
                self.btn_stop.setEnabled(True)
                self.notice.setText("正在采集：SDK 队列与后台存档独立于界面刷新")
            except Exception as exc:
                self.notice.setText(f"启动失败：{type(exc).__name__}: {exc}")
                self.btn_start.setEnabled(True)
                if self.recorder is not None:
                    try:self.recorder.close(timeout_s=5)
                    except Exception:pass
                    self.recorder=None
                if self.camera is not None:
                    self.camera.close()
                    self.camera=None
                if self.brush is not None:
                    self.brush.close()
                    self.brush=None

        def stop_capture(self):
            if self.ending:return
            self.ending=True
            self.btn_stop.setEnabled(False)
            self.btn_start.setEnabled(False)
            self.notice.setText("正在停止采集，等待原始数据和 SAM3 任务完整归档…")
            def shut_down():
                try:
                    # Quiesce producers first so all queued callback buffers
                    # can be drained without a moving end-of-stream boundary.
                    if self.camera is not None:self.camera.close()
                    if self.brush is not None:self.brush.close()
                    if self.recorder is not None:
                        self.recorder.close(timeout_s=240)
                except Exception as exc:
                    self.stop_error=f"{type(exc).__name__}: {exc}"
                finally:
                    if self.camera is not None:self.camera.close()
                    if self.brush is not None:self.brush.close()
                    self.stop_done.set()

            self.stop_thread=threading.Thread(
                target=shut_down,name="StudioShutdown",daemon=True)
            self.stop_thread.start()

        def tick(self):
            try:
                if self.camera is not None:
                    frame=self.camera.poll()
                    if frame:
                        rgb=frame.get("rgb")
                        if isinstance(rgb,np.ndarray) and rgb.ndim==3 and rgb.shape[2]>=3:
                            self._display(self.color_view,self._pixmap_rgb(rgb))
                        depth=frame.get("depth_mm")
                        if isinstance(depth,np.ndarray) and depth.ndim==2:
                            good=depth[np.isfinite(depth)&(depth>0)]
                            top=float(np.percentile(good,95)) if good.size else 1.
                            scaled=np.clip(np.asarray(depth,dtype=float)*255/max(top,1),0,255)
                            overlay=np.stack([scaled,scaled,scaled],axis=-1).astype(np.uint8)
                            self._display(self.depth_view,self._pixmap_rgb(overlay))
                if self.recorder is not None:
                    status=self.recorder.status()
                    self.status_text.setPlainText(
                        json.dumps(status,ensure_ascii=False,indent=2,default=str))
                    if status["fatal_error"] and not self.ending:
                        self.notice.setText("采集数据完整性错误："+status["fatal_error"])
                        self.stop_capture()
                if self.ending and self.stop_done.is_set():
                    self.ending=False
                    if self.stop_error:
                        self.notice.setText("归档不完整，请隔离本次 episode："+self.stop_error)
                    else:
                        self.notice.setText("采集停止，已执行数据归档与会话清理")
                    self.btn_stop.setEnabled(False)
                    if self._close_when_finished:
                        self.timer.stop()
                        self._close_when_finished=False
                        self.close()
            except Exception as exc:
                self.notice.setText(f"界面刷新异常：{type(exc).__name__}: {exc}")

        def closeEvent(self,event):
            if self.recorder is not None and not self.stop_done.is_set():
                event.ignore()
                self._close_when_finished=True
                self.stop_capture()
                return
            super().closeEvent(event)

    return LiveWindow()


def main(argv=None):
    args=build_parser().parse_args(argv)
    from PySide6 import QtWidgets
    app=QtWidgets.QApplication(sys.argv[:1])
    window=make_main_window(args)
    window.show()
    return app.exec()


if __name__=="__main__":
    raise SystemExit(main())

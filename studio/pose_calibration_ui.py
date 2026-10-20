from __future__ import annotations
from PySide6 import QtWidgets,QtCore
import json

class PoseCalibrationPanel(QtWidgets.QWidget):
 startRequested=QtCore.Signal();advanceRequested=QtCore.Signal();retryRequested=QtCore.Signal();cancelRequested=QtCore.Signal()
 commitRequested=QtCore.Signal(str);loadRequested=QtCore.Signal(str)
 def __init__(self):
  super().__init__();L=QtWidgets.QVBoxLayout(self)
  self.title=QtWidgets.QLabel("姿态映射校准");self.title.setStyleSheet("font-size:20px;font-weight:600")
  self.stage=QtWidgets.QLabel("IDLE");self.stage.setStyleSheet("font-size:28px;font-weight:700")
  self.msg=QtWidgets.QLabel("准备就绪");self.msg.setWordWrap(True)
  self.progress=QtWidgets.QProgressBar();self.progress.setRange(0,100)
  self.detail=QtWidgets.QPlainTextEdit();self.detail.setReadOnly(True)
  row=QtWidgets.QHBoxLayout()
  for txt,sig in [("开始校准",self.startRequested),("下一步 / 跳过",self.advanceRequested),("重试当前步骤",self.retryRequested),("取消校准",self.cancelRequested)]:
   b=QtWidgets.QPushButton(txt);b.clicked.connect(sig.emit);row.addWidget(b)
  save=QtWidgets.QHBoxLayout();self.name=QtWidgets.QLineEdit();self.name.setPlaceholderText("映射配置名称")
  self.commit=QtWidgets.QPushButton("接受并保存");self.commit.clicked.connect(lambda:self.commitRequested.emit(self.name.text().strip()))
  save.addWidget(self.name);save.addWidget(self.commit)
  versions=QtWidgets.QHBoxLayout();self.profiles=QtWidgets.QComboBox();self.load=QtWidgets.QPushButton("加载 / 回退")
  self.load.clicked.connect(lambda:self.loadRequested.emit(self.profiles.currentText()));versions.addWidget(self.profiles);versions.addWidget(self.load)
  for w in (self.title,self.stage,self.msg,self.progress):L.addWidget(w)
  L.addLayout(row);L.addLayout(save);L.addLayout(versions);L.addWidget(self.detail)
 def set_profiles(self,items):
  cur=self.profiles.currentText();self.profiles.clear()
  for x in items:self.profiles.addItem(x["name"])
  i=self.profiles.findText(cur)
  if i>=0:self.profiles.setCurrentIndex(i)
 def update_state(self,s):
  stage=s.get("stage","?")
  stage_zh={"IDLE":"空闲","ON_STAND":"毛笔在笔架","PICK_UP":"拿起毛笔","UP_R0":"UP_R0 参考姿态","X_TEST":"+X 倾斜验证","Y_TEST":"+Y 倾斜验证","ROLL_TEST":"绕笔轴旋转验证","REVIEW":"检查校准结果","COMMITTED":"已保存"}.get(stage,stage)
  msg=s.get("message","")
  msg_zh={"Ready":"准备就绪","Keep brush still on stand":"请保持毛笔静置在笔架上","Remove brush from stand":"请从笔架拿起毛笔","Hold UP_R0 still until reference locks":"请保持 UP_R0 姿态静止，直到参考锁定","Tilt brush tail toward board +X / right":"请将笔尾向纸面 +X / 右侧倾斜","Tilt brush tail toward board +Y / away":"请将笔尾向纸面 +Y / 远离书写者方向倾斜","Keep shaft near vertical and rotate brush about its shaft":"保持笔杆接近竖直，并绕笔轴旋转毛笔","Review and commit mapping":"检查校准结果，确认后保存映射","Mapping profile committed":"映射配置已保存"}.get(msg,msg)
  self.stage.setText(stage_zh);self.msg.setText(msg_zh)
  self.progress.setValue(int(s.get("quality_score",0)));self.commit.setEnabled(bool(s.get("can_commit")))
  self.detail.setPlainText(json.dumps(s,ensure_ascii=False,indent=2))

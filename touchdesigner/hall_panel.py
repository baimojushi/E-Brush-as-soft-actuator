from __future__ import annotations
import math, json
import numpy as np
from PySide6 import QtWidgets, QtCore, QtGui

class FluxPreview(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(260)
        self.data=None
    def set_data(self, d):
        self.data=d
        self.update()

    def _draw_centered_component(self, p, cx, cy, width, label, value, vmax, color):
        p.setPen(QtGui.QPen(QtGui.QColor(70,80,95),2))
        p.drawLine(cx-width/2, cy, cx+width/2, cy)
        p.setPen(QtGui.QPen(QtGui.QColor(90,100,115),1))
        for k in range(-4,5):
            x=cx + (width/2)*k/4
            p.drawLine(x, cy-5, x, cy+5)
        p.setPen(QtGui.QPen(QtGui.QColor(220,225,235),1))
        p.drawText(QtCore.QRectF(cx-width/2, cy-20, width, 16), QtCore.Qt.AlignCenter, f"{label}  居中局部瞬时分量")
        vmax=max(vmax,1e-6)
        half=(width*0.42)*min(1.0, abs(value)/vmax)
        x1=cx-half if value<0 else cx
        x2=cx+half if value>0 else cx
        p.setPen(QtGui.QPen(color,6))
        p.drawLine(x1, cy, x2, cy)
        p.setBrush(color)
        p.drawEllipse(QtCore.QPointF(cx + math.copysign(half, value if abs(value)>1e-12 else 1.0), cy), 4,4)
        p.setPen(QtGui.QPen(QtGui.QColor(215,220,230),1))
        p.drawText(QtCore.QRectF(cx-width/2, cy+8, width, 18), QtCore.Qt.AlignCenter, f"{value:+.4f} mT")

    def paintEvent(self, e):
        p=QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(18,22,28))
        w,h=self.width(),self.height()
        lx0=18; lx1=w*0.48; cx=(lx0+lx1)/2; bw=(lx1-lx0)*0.84
        if self.data and self.data.get("present"):
            bc=self.data.get("brush_components_mT",{})
            vals=[float(bc.get("x",0)), float(bc.get("y",0)), float(bc.get("z",0))]
            vmax=max(0.05, max(abs(v) for v in vals))
            ys=[h*0.24, h*0.50, h*0.76]
            cols=[QtGui.QColor(255,90,90), QtGui.QColor(85,230,120), QtGui.QColor(90,145,255)]
            labs=["Brush X","Brush Y","Brush Z"]
            for y,v,c,l in zip(ys,vals,cols,labs):
                self._draw_centered_component(p,cx,y,bw,l,v,vmax,c)
        else:
            p.setPen(QtGui.QPen(QtGui.QColor(180,190,205),1))
            p.drawText(QtCore.QRectF(0,0,lx1,h), QtCore.Qt.AlignCenter, "无霍尔数据")

        rx0=w*0.52; rx1=w-18; ry0=18; ry1=h-18
        p.setPen(QtGui.QPen(QtGui.QColor(70,80,95),1))
        p.drawRect(QtCore.QRectF(rx0,ry0,rx1-rx0,ry1-ry0))
        p.setPen(QtGui.QPen(QtGui.QColor(190,200,215),1))
        p.drawText(QtCore.QRectF(rx0,ry0-4,rx1-rx0,18), QtCore.Qt.AlignCenter, "世界坐标磁通云图（整体趋势）")
        mx=(rx0+rx1)/2; my=(ry0+ry1)/2
        p.setPen(QtGui.QPen(QtGui.QColor(85,95,110),1))
        p.drawLine(mx,ry0+8,mx,ry1-8); p.drawLine(rx0+8,my,rx1-8,my)
        p.drawText(mx+4, ry0+20, "Y+"); p.drawText(rx1-24, my-4, "X+")
        if self.data and self.data.get("present") and self.data.get("pose_locked"):
            pts=np.asarray(self.data.get("world_history_xyz",[]),float)
            if pts.size:
                lim=max(0.05, float(np.max(np.abs(pts[:,:2]))))
                scale=min((rx1-rx0)*0.36, (ry1-ry0)*0.36)/lim
                p.setPen(QtCore.Qt.NoPen)
                for v in pts:
                    x=mx + v[0]*scale; y=my - v[1]*scale
                    col=QtGui.QColor(70,220,255,55)
                    rr=2.0 + min(5.0, abs(v[2])*10.0)
                    p.setBrush(col); p.drawEllipse(QtCore.QPointF(x,y), rr, rr)
                mean=self.data.get("world_mean_mT",{}); std=self.data.get("world_std_mT",{})
                mv=np.array([float(mean.get("x",0)),float(mean.get("y",0)),float(mean.get("z",0))],float)
                x=mx + mv[0]*scale; y=my - mv[1]*scale
                p.setPen(QtGui.QPen(QtGui.QColor(255,245,120),3)); p.drawLine(mx,my,x,y)
                p.setBrush(QtGui.QColor(255,245,120)); p.drawEllipse(QtCore.QPointF(x,y),5,5)
                p.setPen(QtGui.QPen(QtGui.QColor(215,220,230),1))
                p.drawText(QtCore.QRectF(rx0+10,ry1-44,rx1-rx0-20,16), QtCore.Qt.AlignLeft, f"mean = [{mv[0]:+.3f}, {mv[1]:+.3f}, {mv[2]:+.3f}] mT")
                p.drawText(QtCore.QRectF(rx0+10,ry1-26,rx1-rx0-20,16), QtCore.Qt.AlignLeft, f"std  = [{float(std.get('x',0)):.4f}, {float(std.get('y',0)):.4f}, {float(std.get('z',0)):.4f}] mT")
            else:
                p.setPen(QtGui.QPen(QtGui.QColor(180,190,205),1))
                p.drawText(QtCore.QRectF(rx0,ry0,rx1-rx0,ry1-ry0), QtCore.Qt.AlignCenter, "等待姿态锁定后累积世界磁通")
        else:
            p.setPen(QtGui.QPen(QtGui.QColor(180,190,205),1))
            p.drawText(QtCore.QRectF(rx0,ry0,rx1-rx0,ry1-ry0), QtCore.Qt.AlignCenter, "需要霍尔数据与已锁定姿态")

class HallPanel(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        L=QtWidgets.QVBoxLayout(self)
        title=QtWidgets.QLabel("霍尔三轴 / 磁通可视化")
        title.setStyleSheet("font-size:20px;font-weight:600")
        self.preview=FluxPreview()
        self.form=QtWidgets.QFormLayout()
        self.labels={}
        for key,label in [
            ("sensor_xyz","Sensor B (mT)"),("brush_xyz","Brush B (mT)"),("world_xyz","World B instant (mT)"),
            ("world_mean","World B mean (mT)"),("world_std","World B std (mT)"),("B_total_mT","|B| (mT)"),
            ("method","Mount / solve"),("note","说明")
        ]:
            q=QtWidgets.QLabel("—"); q.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            self.labels[key]=q; self.form.addRow(label,q)
        self.detail=QtWidgets.QPlainTextEdit(); self.detail.setReadOnly(True)
        L.addWidget(title); L.addWidget(self.preview); L.addLayout(self.form); L.addWidget(self.detail)

    def update_data(self, d):
        self.preview.set_data(d)
        if not d or not d.get("present"):
            msg="未检测到霍尔三轴数据"
            for q in self.labels.values(): q.setText("—")
            self.labels["note"].setText(msg)
            self.detail.setPlainText(msg)
            return
        sc=d.get("sensor_components_mT",{}); bc=d.get("brush_components_mT",{})
        wc=d.get("world_components_mT"); wm=d.get("world_mean_mT"); ws=d.get("world_std_mT")
        self.labels["sensor_xyz"].setText(f'[{sc.get("x",0):+.3f}, {sc.get("y",0):+.3f}, {sc.get("z",0):+.3f}]')
        self.labels["brush_xyz"].setText(f'[{bc.get("x",0):+.3f}, {bc.get("y",0):+.3f}, {bc.get("z",0):+.3f}]')
        self.labels["world_xyz"].setText("—" if not wc else f'[{wc.get("x",0):+.3f}, {wc.get("y",0):+.3f}, {wc.get("z",0):+.3f}]')
        self.labels["world_mean"].setText("—" if not wm else f'[{wm.get("x",0):+.3f}, {wm.get("y",0):+.3f}, {wm.get("z",0):+.3f}]')
        self.labels["world_std"].setText("—" if not ws else f'[{ws.get("x",0):.4f}, {ws.get("y",0):.4f}, {ws.get("z",0):.4f}]')
        self.labels["B_total_mT"].setText(f'{d.get("B_total_mT",0):.4f}')
        self.labels["method"].setText(str(d.get("method","")))
        rt=d.get("runtime_mapping",{})
        self.labels["note"].setText(
            "警告："+str(rt.get("warning")) if not rt.get("compatible",True) else
            "局部三轴为瞬时量；整体云图采用世界坐标表示，并假设外部磁场近似恒定。")
        self.detail.setPlainText(json.dumps(d,ensure_ascii=False,indent=2))

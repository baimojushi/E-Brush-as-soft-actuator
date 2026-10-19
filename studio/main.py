import argparse,json,sys,time,os,csv
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
from PySide6 import QtCore,QtWidgets
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
        # no dependency on exact RGB/BGR semantics for the diagnostic overlay
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

class Win(QtWidgets.QMainWindow):
    def __init__(self,a):
        super().__init__();self.resize(1550,940);self.setWindowTitle("e-Brush Studio v1.2 — 霍尔外参标定 / 三维磁通 / 姿态映射")
        cfg=json.loads((ROOT/"config"/"vision_validation.json").read_text(encoding="utf-8"));self.cfg=cfg
        self.hp=HP60CSource(ROOT,a.camera_backend);self.hp.start();self.br=BrushSource(ROOT/"vendor"/"ebrush",a.port,a.baud)
        if not a.no_brush:self.br.start()
        self.pose=PoseMapper(ROOT/"config"/"ebrush_pose_calibration.json",heading_mode=a.heading_mode)
        self.mapper_debug=MapperDebug(ROOT)
        self.pose_cal=PoseCalibrationSession(ROOT,self.pose,self.mapper_debug)
        self.hall=HallMountModel(ROOT)
        self.hall_cal=HallExtrinsicCalibration(ROOT,self.hall,self.pose,self.mapper_debug)
        self.patch=HotPatch(ROOT/"patches"/"visual_patch.py");self.state={"camera":None,"brush":None,"pose":None,"hall":None}
        sc=cfg["sam3"];key=os.environ.get(sc.get("api_key_env","SAM3_API_KEY"),"")
        self.sam=SAM3Client(
            a.sam3_url or sc["base_url"], key,
            a.sam3_prompt or sc["prompt"],
            sc["jpeg_quality"], sc["timeout_s"],
            sc.get("clip_frames", 30), sc.get("clip_fps", 30),
            sc["min_interval_s"]
        )
        vc=cfg["vision"];self.vest=VisionPoseEstimator(vc["erode_px"],vc["ransac_threshold_mm"],vc["min_points"])
        cc=cfg["camera_to_board"];self.Rbc=np.asarray(cc["R_board_from_camera"],float) if cc.get("enabled") else None
        self.last_submitted=-1;self.last_sam_result=-1;self.last_validation=None
        self.agent=AgentControl(ROOT/"runtime",a.agent_host,a.agent_port)
        self.agent.register_command("reset_reference",lambda x:(self.pose.reset_reference() or {"ok":True}),"重置 UP_R0")
        self.agent.register_command("sam3_health",lambda x:self.sam.health(),"SAM3 /health")
        self.agent.register_parameter("sam3.prompt",lambda:self.sam.prompt,lambda v:(setattr(self.sam,"prompt",str(v)) or self.sam.prompt),"SAM3 text prompt","string")
        self.agent.register_parameter("sam3.clip_frames",lambda:self.sam.clip_frames,lambda v:(setattr(self.sam,"clip_frames",int(v)) or self.sam.clip_frames),"SAM3 clip frames","int")
        self.agent.register_parameter("sam3.clip_fps",lambda:self.sam.clip_fps,lambda v:(setattr(self.sam,"clip_fps",int(v)) or self.sam.clip_fps),"SAM3 clip FPS","int")
        self.agent.register_parameter("vision.ransac_threshold_mm",lambda:self.vest.ransac_mm,lambda v:(setattr(self.vest,"ransac_mm",float(v)) or self.vest.ransac_mm),"RANSAC threshold","float")
        self.agent.register_parameter("vision.erode_px",lambda:self.vest.erode_px,lambda v:(setattr(self.vest,"erode_px",int(v)) or self.vest.erode_px),"Mask erosion","int")
        for i,n in enumerate(("x","y","z")):
            self.agent.register_parameter("mapper.board_"+n+"_deg",lambda i=i:float(self.mapper_debug.board_deg[i]),lambda v,i=i:self.mapper_debug.set_board(i,v),"Board-frame debug rotation","float")
            self.agent.register_parameter("mapper.brush_"+n+"_deg",lambda i=i:float(self.mapper_debug.brush_deg[i]),lambda v,i=i:self.mapper_debug.set_brush(i,v),"Brush/mount debug rotation","float")
        self.agent.register_command("mapper.reset",lambda a:self.mapper_debug.reset(),"Reset mapper debug override")
        self.agent.register_command("mapper.save",lambda a:self.mapper_debug.save(a.get("name")),"Save mapper version")
        self.agent.register_command("mapper.load",lambda a:self.mapper_debug.load(a["name"]),"Rollback/load mapper version")
        self.agent.register_command("mapper.list",lambda a:self.mapper_debug.list_versions(),"List mapper versions")
        self.agent.register_command("calibration.start",lambda a:self.pose_cal.start(),"Start pose mapping calibration")
        self.agent.register_command("calibration.next",lambda a:self.pose_cal.advance(),"Advance/skip calibration stage")
        self.agent.register_command("calibration.retry",lambda a:self.pose_cal.retry(),"Retry current calibration stage")
        self.agent.register_command("calibration.cancel",lambda a:self.pose_cal.cancel(),"Cancel calibration")
        self.agent.register_command("calibration.commit",lambda a:self.pose_cal.commit(a.get("name")),"Commit mapping profile")
        self.agent.register_command("calibration.list",lambda a:self.pose_cal.list_profiles(),"List mapping profiles")
        self.agent.register_command("calibration.load",lambda a:self.pose_cal.load_profile(a["name"]),"Load/rollback mapping profile")
        self.agent.register_command("hall_extrinsic.start",lambda a:self.hall_cal.start(self.state.get("brush"),self.state.get("pose")),"开始 Hall 外参标定")
        self.agent.register_command("hall_extrinsic.capture",lambda a:self.hall_cal.arm(),"采集一个静止姿态")
        self.agent.register_command("hall_extrinsic.undo",lambda a:self.hall_cal.undo(),"撤销最近一组姿态")
        self.agent.register_command("hall_extrinsic.solve",lambda a:self.hall_cal.solve(),"多姿态求解候选外参")
        self.agent.register_command("hall_extrinsic.cancel",lambda a:self.hall_cal.cancel(),"取消 Hall 外参标定")
        self.agent.register_command("hall_extrinsic.commit",lambda a:self.hall_cal.commit(a.get("name")),"验证通过后保存")
        self.agent.register_command("hall_extrinsic.list",lambda a:self.hall_cal.list_profiles(),"列出外参版本")
        self.agent.register_command("hall_extrinsic.load",lambda a:self.hall_cal.load(a["name"]),"加载/回退 Hall 外参")
        self.agent.start();self._last_agent_flush=0

        tabs=QtWidgets.QTabWidget();self.setCentralWidget(tabs);self.rgb=Img();self.dep=Img();self.maskview=Img();self.val=ValidationPanel();self.scene3d=Scene3D();self.calui=PoseCalibrationPanel();self.hallui=HallPanel();self.hallcalui=HallExtrinsicPanel()
        for w,n in [(self.rgb,"彩色图像"),(self.dep,"深度图"),(self.maskview,"SAM3 掩膜叠加"),(self.val,"IMU ↔ 视觉验证"),(self.scene3d,"三维场景"),(self.hallui,"霍尔磁通"),(self.calui,"姿态映射校准"),(self.hallcalui,"霍尔外参标定")]:tabs.addTab(w,n)
        self.calui.startRequested.connect(lambda:self.pose_cal.start());self.calui.advanceRequested.connect(lambda:self.pose_cal.advance())
        self.calui.retryRequested.connect(lambda:self.pose_cal.retry());self.calui.cancelRequested.connect(lambda:self.pose_cal.cancel())
        self.calui.commitRequested.connect(self._cal_commit);self.calui.loadRequested.connect(self._cal_load);self._refresh_cal_profiles()
        self.hallcalui.startRequested.connect(lambda:self._hall_ui_call(self.hall_cal.start,self.state.get("brush"),self.state.get("pose")))
        self.hallcalui.captureRequested.connect(lambda:self._hall_ui_call(self.hall_cal.arm))
        self.hallcalui.undoRequested.connect(lambda:self._hall_ui_call(self.hall_cal.undo))
        self.hallcalui.solveRequested.connect(lambda:self._hall_ui_call(self.hall_cal.solve))
        self.hallcalui.cancelRequested.connect(lambda:self._hall_ui_call(self.hall_cal.cancel))
        self.hallcalui.commitRequested.connect(lambda name:self._hall_ui_call(self.hall_cal.commit,name or None))
        self.hallcalui.loadRequested.connect(lambda name:self._hall_ui_call(self.hall_cal.load,name))
        self._refresh_hall_profiles()
        d=QtWidgets.QDockWidget("Runtime");w=QtWidgets.QWidget();f=QtWidgets.QFormLayout(w)
        self.prompt=QtWidgets.QLineEdit(self.sam.prompt);self.prompt.editingFinished.connect(lambda:setattr(self.sam,"prompt",self.prompt.text().strip()))
        self.clip_frames=QtWidgets.QSpinBox();self.clip_frames.setRange(5,120);self.clip_frames.setValue(self.sam.clip_frames)
        self.clip_frames.valueChanged.connect(lambda v:setattr(self.sam,"clip_frames",int(v)))
        self.clip_fps=QtWidgets.QSpinBox();self.clip_fps.setRange(10,60);self.clip_fps.setValue(self.sam.clip_fps)
        self.clip_fps.valueChanged.connect(lambda v:setattr(self.sam,"clip_fps",int(v)))
        self.status=QtWidgets.QPlainTextEdit();self.status.setReadOnly(True)
        b=QtWidgets.QPushButton("Reset UP_R0 reference");b.clicked.connect(self.pose.reset_reference)
        h=QtWidgets.QPushButton("检查 SAM3");h.clicked.connect(self.check_health)
        f.addRow("SAM3 prompt",self.prompt)
        f.addRow("Clip frames",self.clip_frames)
        f.addRow("Clip FPS",self.clip_fps)
        f.addRow(h);f.addRow(b);f.addRow(self.status);d.setWidget(w);self.addDockWidget(QtCore.Qt.RightDockWidgetArea,d)
        md=QtWidgets.QDockWidget("映射快捷调试");mw=QtWidgets.QWidget();mf=QtWidgets.QFormLayout(mw);self.map_spins=[]
        for group,label in (("board","纸面坐标 XYZ °"),("brush","毛笔/安装 XYZ °")):
            row=QtWidgets.QWidget();hl=QtWidgets.QHBoxLayout(row);hl.setContentsMargins(0,0,0,0)
            for i,axis in enumerate("XYZ"):
                sp=QtWidgets.QDoubleSpinBox();sp.setRange(-180,180);sp.setDecimals(2);sp.setSingleStep(.5)
                arr=self.mapper_debug.board_deg if group=="board" else self.mapper_debug.brush_deg;sp.setValue(float(arr[i]))
                sp.valueChanged.connect(lambda v,g=group,i=i:self._mapper_spin(g,i,v));hl.addWidget(QtWidgets.QLabel(axis));hl.addWidget(sp);self.map_spins.append((group,i,sp))
            mf.addRow(label,row)
        self.map_name=QtWidgets.QLineEdit();self.map_versions=QtWidgets.QComboBox();self._refresh_versions()
        bs=QtWidgets.QPushButton("保存映射版本");bs.clicked.connect(self._save_mapping)
        bl=QtWidgets.QPushButton("加载 / 回退所选版本");bl.clicked.connect(self._load_mapping)
        brs=QtWidgets.QPushButton("重置调试修正");brs.clicked.connect(self._reset_mapping)
        mf.addRow("版本名称",self.map_name);mf.addRow("历史版本",self.map_versions);mf.addRow(bs);mf.addRow(bl);mf.addRow(brs)
        md.setWidget(mw);self.addDockWidget(QtCore.Qt.LeftDockWidgetArea,md)
        self.t=QtCore.QTimer();self.t.timeout.connect(self.tick);self.t.start(16)

    def _refresh_hall_profiles(self):
        self.hallcalui.set_profiles(self.hall_cal.list_profiles())
    def _hall_ui_call(self,callback,*args):
        try:
            result=callback(*args)
            self.hallcalui.update_state(self.hall_cal.state())
            self._refresh_hall_profiles()
            if isinstance(result,dict) and result.get("status")=="UNVERIFIED_BASELINE":
                QtWidgets.QMessageBox.warning(self,"恢复原始映射",
                    "已恢复未标定的原始 Hall 安装方向。该方向仅用于安全回退，并非通过外参验证的解。")
            return result
        except Exception as e:
            QtWidgets.QMessageBox.warning(self,"霍尔外参标定",str(e))
            return None

    def _refresh_cal_profiles(self):
        self.calui.set_profiles(self.pose_cal.list_profiles())
    def _cal_commit(self,name):
        try:self.pose_cal.commit(name or None);self._refresh_cal_profiles();self._refresh_versions()
        except Exception as e:self.status.setPlainText("校准保存错误: "+repr(e))
    def _cal_load(self,name):
        if not name:return
        try:self.pose_cal.load_profile(name);self._sync_mapper_spins();self._refresh_versions();self._refresh_cal_profiles()
        except Exception as e:self.status.setPlainText("校准回退错误: "+repr(e))
    def _mapper_spin(self,group,i,v):
        (self.mapper_debug.set_board if group=="board" else self.mapper_debug.set_brush)(i,v)
    def _refresh_versions(self):
        cur=self.mapper_debug.active_version;self.map_versions.clear()
        for d in self.mapper_debug.list_versions():self.map_versions.addItem(d["name"])
        if cur:
            j=self.map_versions.findText(cur)
            if j>=0:self.map_versions.setCurrentIndex(j)
    def _sync_mapper_spins(self):
        for g,i,sp in self.map_spins:
            a=self.mapper_debug.board_deg if g=="board" else self.mapper_debug.brush_deg
            sp.blockSignals(True);sp.setValue(float(a[i]));sp.blockSignals(False)
    def _save_mapping(self):
        self.mapper_debug.save(self.map_name.text().strip() or None);self._refresh_versions()
    def _load_mapping(self):
        n=self.map_versions.currentText()
        if n:self.mapper_debug.load(n);self._sync_mapper_spins()
    def _reset_mapping(self):
        self.mapper_debug.reset();self._sync_mapper_spins()
    def check_health(self):
        try:self.status.setPlainText(json.dumps(self.sam.health(),ensure_ascii=False,indent=2))
        except Exception as e:self.status.setPlainText("SAM3 health ERROR: "+repr(e))

    def tick(self):
        c=self.hp.poll()
        if c is not None:self.state["camera"]=c
        b=self.br.latest()
        if b:
            self.state["brush"]=b;self.state["pose"]=self.mapper_debug.apply(self.pose.map_frame(b))
            self.state["hall"]=self.hall.process(b,self.state.get("pose"))
            self.hall_cal.update(b,self.state.get("pose"))
            if self.state["hall"] is not None:
                self.state["hall"]["runtime_mapping"]=self.hall_cal.runtime_mapping_status()
            self.pose_cal.update(b,self.state.get("pose"))
        else:
            self.state["hall"]=None
        self.calui.update_state(self.pose_cal.state());self.hallui.update_data(self.state.get("hall"))
        self.hallcalui.update_state(self.hall_cal.state())

        c=self.state["camera"]
        if c is not None:
            rgb=c.get("rgb");fid=int(c.get("packet_id",-1));hns=int(c.get("host_ns",time.perf_counter_ns()))
            if rgb is not None and fid!=self.last_submitted:
                # Exact same packet is retained as context until SAM response returns.
                self.sam.submit(rgb,fid,hns,camera_context=c);self.last_submitted=fid

        sr=self.sam.latest()
        if sr is not None and sr.frame_id!=self.last_sam_result:
            self.last_sam_result=sr.frame_id
            if sr.mask is not None:
                vp=self.vest.estimate(sr.mask,sr.camera_context)
                d={"sam_frame_id":sr.frame_id,"sam_score":sr.score,"sam_latency_ms":sr.latency_ms,"sam_error":sr.error}
                if vp:d.update({k:v for k,v in vp.items() if k!="mask"})
                pose=self.state.get("pose")
                if vp and vp.get("ok") and pose and pose.get("reference_locked"):
                    ia=np.array([pose.get("brush_axis_x"),pose.get("brush_axis_y"),pose.get("brush_axis_z")],float)
                    cmp=compare_axes(ia,vp["axis_camera"],self.Rbc)
                    if cmp:d.update(cmp)
                    d["imu_axis_board"]=ia
                    d["imu_pose_quality"]=pose.get("pose_quality")
                self.last_validation=d;self.val.update_result(d)
                txt="axis cam "+np.array2string(vp["axis_camera"],precision=3) if vp and vp.get("ok") else "vision fit failed"
                self.maskview.set(overlay(sr.camera_context.get("rgb"),sr.mask,txt))
            else:
                self.last_validation={"sam_frame_id":sr.frame_id,"sam_error":sr.error,"sam_latency_ms":sr.latency_ms};self.val.update_result(self.last_validation)

        o=self.patch.run(self.state,{"near":100.0,"far":850.0})
        self.rgb.set(o.get("rgb"))
        self.dep.set(o.get("depth"))
        rs={"camera":self.hp.status(),"brush_port":self.br.port,"brush_error":self.br.error,"reference":self.pose.reference_state,"sam_last_frame":self.last_sam_result,"sam_prompt":self.sam.prompt,"camera_to_board_enabled":self.Rbc is not None}
        self.status.setPlainText(json.dumps(rs,ensure_ascii=False,indent=2))
        self.agent.publish("camera",rs["camera"]);self.agent.publish("brush",{"port":self.br.port,"error":self.br.error})
        self.agent.publish("pose",self.state.get("pose") or {"reference_state":self.pose.reference_state,"reference_locked":False})
        srn=self.sam.latest();self.agent.publish("sam3",{"last_frame":self.last_sam_result,"prompt":self.sam.prompt,"last_error":getattr(srn,"error","") if srn else "waiting","latency_ms":getattr(srn,"latency_ms",None) if srn else None,"score":getattr(srn,"score",None) if srn else None})
        self.agent.publish("vision",self.last_validation or {"ok":False,"reason":"waiting"});self.agent.publish("validation",self.last_validation or {})
        self.agent.publish("mapper",self.mapper_debug.state())
        self.agent.publish("calibration",self.pose_cal.state())
        self.agent.publish("hall",self.state.get("hall") or {"present":False})
        self.agent.publish("hall_extrinsic",self.hall_cal.state())
        vp_scene=self.last_validation or {}
        if "origin" in vp_scene and "origin_camera_mm" not in vp_scene:vp_scene=dict(vp_scene,origin_camera_mm=vp_scene["origin"])
        self.scene3d.update_scene(self.state.get("camera"),self.state.get("pose"),vp_scene,self.state.get("hall"))
        self.agent.publish("scene3d",{"imu_pose_available":bool(self.state.get("pose") and self.state["pose"].get("reference_locked")),"vision_pose_available":bool(vp_scene.get("ok")),"camera_to_board_enabled":self.Rbc is not None,"hall_present":bool(self.state.get("hall") and self.state["hall"].get("present")),"note":"vision geometry remains camera-frame until camera_to_board is enabled"})
        now=time.monotonic()
        if now-self._last_agent_flush>=1:self.agent.flush_state();self._last_agent_flush=now

    def closeEvent(self,e):self.agent.close();self.sam.close();self.hp.close();self.br.close();super().closeEvent(e)

def main():
    p=argparse.ArgumentParser();p.add_argument("--camera-backend",choices=["direct","shm","none"],default="direct");p.add_argument("--port");p.add_argument("--baud",type=int,default=921600)
    p.add_argument("--heading-mode",choices=["startup_reference","relative","calibration_session"],default="startup_reference");p.add_argument("--no-brush",action="store_true")
    p.add_argument("--sam3-url");p.add_argument("--sam3-prompt");p.add_argument("--agent-host",default="127.0.0.1");p.add_argument("--agent-port",type=int,default=8765);a=p.parse_args()
    q=QtWidgets.QApplication(sys.argv);w=Win(a);w.show();return q.exec()
if __name__=="__main__":raise SystemExit(main())

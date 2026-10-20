"""Hall↔Brush extrinsic calibration workflow.

Collect static *paired* Hall/IMU poses. Calibrate 8+ training poses and
independently validate against 3+ held-out poses before versioned commit.
No vendor SDK, Qt dependency, or changes to PoseMapper.
"""
from __future__ import annotations
import copy
import json
import math
import os
import re
import threading
import time
import uuid
from pathlib import Path

import numpy as np
from hall_extrinsic_solver import (
    is_so3, project_so3, rotation_distance_deg, field_values,
    error_stats, solve_hall_extrinsic,
)
from mapper_debug import euler_xyz_deg

MIN_TRAIN=8
MIN_VALIDATE=3
HOLD_SECONDS=0.75
MIN_FRAMES=18
MIN_SEPARATION_DEG=23.0

def _v(d, prefix):
    return [float(d[prefix+"_"+a]) for a in "xyz"]

def _pose_matrix(p):
    return np.column_stack([
        _v(p,"brush_x_axis"),
        _v(p,"brush_y_axis"),
        _v(p,"brush_axis"),
    ])

def _atomic_json(path, obj, overwrite=True):
    p=Path(path)
    p.parent.mkdir(parents=True,exist_ok=True)
    if not overwrite and p.exists():
        raise FileExistsError(f"配置名称已存在，请使用新名称：{p.stem}")
    temp=p.with_suffix(p.suffix+"."+uuid.uuid4().hex+".tmp")
    try:
        temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
        os.replace(temp,p)
    finally:
        if temp.exists(): temp.unlink()

def _profile_name(name):
    if not name:
        return time.strftime("hallmap_%Y%m%d_%H%M%S")+"_"+str(int(time.time()*1000)%1000).zfill(3)
    name=str(name).strip()
    if not re.fullmatch(r"[a-zA-Z0-9_\-]{1,64}",name):
        raise ValueError("版本名称只支持 1–64 位字母、数字、下划线和连字符")
    return name

class HallExtrinsicCalibration:
    def __init__(self,root, hall, pose_mapper, mapper_debug):
        self.root=Path(root)
        self.hall=hall
        self.pose_mapper=pose_mapper
        self.mapper_debug=mapper_debug
        self.profile_dir=self.root/"config"/"hall_extrinsic_profiles"
        self.profile_dir.mkdir(parents=True,exist_ok=True)
        self.active_path=self.root/"config"/"hall_extrinsic_active.json"
        self.lock=threading.RLock()
        self.baseline_name="__initial_mount__"
        self.baseline_path=self.profile_dir/(self.baseline_name+".json")
        self._reset()
        self._ensure_baseline()
        self.active_mapping_ref=self._load_active_mapping_ref()

    def _load_active_mapping_ref(self):
        name=self._active_name()
        if not name or name==self.baseline_name:return None
        try:
            d=json.loads((self.profile_dir/(name+".json")).read_text(encoding="utf-8"))
            return d.get("pose_mapping_reference")
        except (OSError,ValueError):return None

    def runtime_mapping_status(self):
        """Check that an applied calibrated mount still matches the current Brush frame."""
        with self.lock:
            ref=self.active_mapping_ref
            if not ref:
                return {"compatible":True,"verified":False,
                        "warning":"当前使用初始或未验证的 Hall 外参"}
            cur=self._mapping_info()
            good=(np.allclose(ref.get("pose_mount_matrix"),cur["pose_mount_matrix"],atol=1e-5)
                  and np.allclose(ref.get("mapper_brush_xyz_deg"),cur["mapper_brush_xyz_deg"],atol=1e-5))
            return {"compatible":bool(good),"verified":True,
                    "warning":None if good else "PoseMapper 安装/Brush Debug 发生变更，当前 Hall 外参已失配"}

    def _ensure_baseline(self):
        # Preserve the exact pre-v1.2 mapping across app restarts.
        if self.baseline_path.exists():return
        data=copy.deepcopy(self.hall.data)
        R=np.asarray(data["R_brush_from_hall"],float)
        if not is_so3(R): raise ValueError("现有 Hall 安装矩阵不是合法 SO(3)")
        _atomic_json(self.baseline_path,{
            "schema":"ebrush.hall_extrinsic_baseline.v1",
            "name":self.baseline_name,
            "created_unix":time.time(),
            "mount_config":data,
            "R_brush_from_hall":R.tolist(),
            "note":"升级前的原始 Hall 安装方向；未经过多姿态外参验证"
        },overwrite=False)

    def _reset(self):
        self.stage="IDLE"
        self.message="准备就绪；请先完成 UP_R0 并锁定 PoseMapper 参考"
        self.started=None
        self.armed=False
        self.pending=[]
        self.pending_t0=None
        self.last_frame_id=None
        self.last_calibration_id=None
        self.last_capture_note=""
        self.train=[]
        self.validation=[]
        self.candidate=None
        self.validation_report=None
        self.ref_fusion_to_board=None
        self.mapping_fingerprint=None
        self.reference_frame=None
        self.rejected_frames=0
        self.accepted_frames=0

    def _mapping_info(self):
        s=self.mapper_debug.state()
        return {
            "pose_calibration_created_from":self.pose_mapper.cal.get("created_from"),
            "pose_mount_matrix":np.asarray(self.pose_mapper.A,float).tolist(),
            "heading_mode":self.pose_mapper.heading_mode,
            "mapper_board_xyz_deg":[float(x) for x in s["board_xyz_deg"]],
            "mapper_brush_xyz_deg":[float(x) for x in s["brush_xyz_deg"]],
        }

    def _invalidate(self,reason):
        self.stage="INVALID"
        self.message=str(reason)+"；请重新开始会话"
        self.armed=False
        self.pending.clear()
        self.pending_t0=None

    def start(self,brush,pose):
        with self.lock:
            if not pose or not pose.get("reference_locked") or not self.pose_mapper.reference_locked:
                raise RuntimeError("请先完成 PoseMapper UP_R0 参考锁定")
            if not brush or not all(x in brush for x in ("bx_mT","by_mT","bz_mT")):
                raise RuntimeError("没有有效的霍尔数据；请先连接 e-Brush")
            h=np.array([brush[x] for x in ("bx_mT","by_mT","bz_mT")],float)
            if not np.isfinite(h).all():raise RuntimeError("霍尔三轴数值无效")
            if self.pose_mapper.R_board_from_fusion is None:
                raise RuntimeError("当前 PoseMapper 不具有有效的 board-frame reference")
            self._reset()
            self.started=time.time()
            self.stage="COLLECT"
            self.message="将毛笔固定在近似不变的位置，改变朝向。每个方向静止后点击“采集姿态”；先收集至少 8 个不同方向。"
            self.ref_fusion_to_board=np.asarray(self.pose_mapper.R_board_from_fusion,float).copy()
            self.mapping_fingerprint=self._mapping_info()
            self.reference_frame=pose.get("frame_id")
            self.last_calibration_id=brush.get("calibration_id")
            return self.state()

    def cancel(self):
        with self.lock:
            self._reset()
            return self.state()

    def arm(self):
        with self.lock:
            if self.stage not in ("COLLECT","VALIDATE"):
                raise RuntimeError("只有训练/独立验证阶段可以采集姿态")
            if self.armed: raise RuntimeError("正在采集当前姿态，请保持静止")
            self.armed=True
            self.pending.clear()
            self.pending_t0=None
            self.message="保持当前姿态静止约 0.75 秒，系统会自动截取配对 Hall/IMU 数据"
            return self.state()

    def _guard(self,pose):
        if not pose or not pose.get("reference_locked") or not self.pose_mapper.reference_locked:
            self._invalidate("PoseMapper 参考失效")
            return False
        if (self.pose_mapper.R_board_from_fusion is None or
            not np.allclose(self.ref_fusion_to_board,self.pose_mapper.R_board_from_fusion,atol=1e-6) or
            self._mapping_info()!=self.mapping_fingerprint):
            self._invalidate("PoseMapper / MapperDebug 映射在采样期间发生变化")
            return False
        return True

    def update(self,brush,pose,now=None):
        """Accept one GUI-observed source frame at a time; never invent 200 Hz samples."""
        with self.lock:
            if self.stage not in ("COLLECT","VALIDATE","READY"):
                return self.state()
            if not self._guard(pose):return self.state()
            if self.stage=="READY" or not self.armed or not brush:
                return self.state()
            now=time.monotonic() if now is None else float(now)
            fid=brush.get("frame_id")
            if fid is not None:
                fid=int(fid)
                if self.last_frame_id is not None:
                    if fid<self.last_frame_id:
                        self._invalidate("e-Brush 帧计数回退，可能发生重启")
                        return self.state()
                    if fid==self.last_frame_id:
                        return self.state()
                self.last_frame_id=fid
            cal_id=brush.get("calibration_id")
            if self.last_calibration_id is not None and cal_id is not None:
                if int(cal_id)!=int(self.last_calibration_id):
                    self._invalidate("e-Brush calibration_id 发生变化")
                    return self.state()
            try:
                B=np.array([brush[k] for k in ("bx_mT","by_mT","bz_mT")],float)
                R=_pose_matrix(pose)
            except (KeyError,ValueError,TypeError):
                return self._reject("等待配对 Hall / IMU 数据")
            if not (np.isfinite(B).all() and is_so3(R,atol=1e-3)):
                return self._reject("霍尔/姿态包含无效值")
            if not (0.015<float(np.linalg.norm(B))<133.0*1.8):
                return self._reject("霍尔磁场模长超出可标定范围")
            stat=brush.get("device_status_names")
            if stat is not None and "hall_valid" not in stat:
                return self._reject("霍尔数据标志无效")
            if "hall_stale" in brush.get("drop_flag_names",[]):
                return self._reject("霍尔数据过期")
            if brush.get("hall_time_us") and brush.get("imu_time_us"):
                if abs(float(brush["hall_time_us"])-float(brush["imu_time_us"]))>30000:
                    return self._reject("Hall/IMU 采样间隔超过 30 ms")
            if bool(pose.get("stale",False)):
                return self._reject("IMU 姿态更新超时")
            gyro=float(pose.get("gyro_norm_rad_s",99))
            grav=float(pose.get("accel_gravity_error_m_s2",99))
            if gyro>0.07 or grav>0.38 or float(pose.get("pose_quality",1.0))<0.7:
                return self._reject("仍有运动：保持毛笔静止")
            if self.pending and rotation_distance_deg(self.pending[0]["R"],R)>2.8:
                return self._reject("姿态在采样窗口内变化，重新计时")
            if self.pending_t0 is None:self.pending_t0=now
            tdelta=None
            if brush.get("hall_time_us") and brush.get("imu_time_us"):
                tdelta=abs(float(brush["hall_time_us"])-float(brush["imu_time_us"]))*0.001
            self.pending.append({"B":B,"R":R,"frame_id":fid,"time":now,"skew_ms":tdelta})
            self.accepted_frames+=1
            if len(self.pending)>=MIN_FRAMES and now-self.pending_t0>=HOLD_SECONDS:
                self._finalize_pending()
            return self.state()

    def _reject(self,reason):
        self.rejected_frames+=1
        self.pending.clear()
        self.pending_t0=None
        self.message=str(reason)
        return self.state()

    def _finalize_pending(self):
        if not self.pending:return
        b=np.asarray([s["B"] for s in self.pending],float)
        R=project_so3(np.mean([s["R"] for s in self.pending],axis=0))
        B=b.mean(axis=0)
        scatter=float(np.linalg.norm(b.std(axis=0)))
        if scatter>max(0.04,0.15*np.linalg.norm(B)):
            self._reject(f"当前姿态磁场波动过大（σ={scatter:.3f} mT），请远离移动磁源")
            return
        old=self.train+self.validation
        mindeg=min([rotation_distance_deg(s["R"],R) for s in old],default=180.)
        if mindeg<MIN_SEPARATION_DEG:
            self.armed=False;self.pending.clear();self.pending_t0=None
            self.message=f"姿态与已有样本仅相差 {mindeg:.1f}°；请至少换一个约 {MIN_SEPARATION_DEG:.0f}° 的方向再采样"
            return
        skews=[s["skew_ms"] for s in self.pending if s["skew_ms"] is not None]
        sample={"R":R,"B":B,"samples":len(self.pending),"field_std_mT":scatter,
                "field_strength_mT":float(np.linalg.norm(B)),
                "frame_id":self.pending[-1]["frame_id"],
                "hall_imu_skew_median_ms":float(np.median(skews)) if skews else None,
                "captured_unix":time.time()}
        target=self.train if self.stage=="COLLECT" else self.validation
        target.append(sample)
        self.armed=False;self.pending.clear();self.pending_t0=None
        self.message=f"已记录第 {len(target)} 个{'训练' if self.stage=='COLLECT' else '独立验证'}姿态。请改变朝向后再次采样。"
        if self.stage=="VALIDATE" and len(self.validation)>=MIN_VALIDATE:
            self._evaluate_validation()

    def undo(self):
        with self.lock:
            if self.armed:self.armed=False;self.pending.clear();self.pending_t0=None
            if self.stage=="COLLECT" and self.train:
                self.train.pop();self.message="已撤销最近一组训练姿态"
            elif self.stage in ("VALIDATE","READY") and self.validation:
                self.validation.pop();self.stage="VALIDATE";self.validation_report=None
                self.message="已撤销最近一组验证姿态"
            return self.state()

    def solve(self):
        with self.lock:
            if self.stage!="COLLECT":raise RuntimeError("当前不处于训练采样阶段")
            if self.armed:raise RuntimeError("请先完成当前姿态的采样")
            if len(self.train)<MIN_TRAIN:
                raise RuntimeError(f"至少需要 {MIN_TRAIN} 个不同的训练姿态，目前仅 {len(self.train)} 个")
            R=np.array([s["R"] for s in self.train],float)
            B=np.array([s["B"] for s in self.train],float)
            result=solve_hall_extrinsic(R,B,self.hall.R_brush_from_hall)
            threshold=max(0.025,0.15*result["field_magnitude_mean_mT"])
            if result["training_rms_mT"]>threshold:
                raise RuntimeError(f"训练世界磁场 RMS {result['training_rms_mT']:.4f} mT 超过 {threshold:.4f} mT；可能不是均匀静态磁场")
            # Sensor-to-sensor orientation: IMU<-Hall is invariant under
            # changes in the board/world heading reference.
            A=np.asarray(self.mapping_fingerprint["pose_mount_matrix"],float)
            Rbd=euler_xyz_deg(self.mapping_fingerprint["mapper_brush_xyz_deg"])
            Q=np.asarray(result["R_brush_from_hall"],float)
            result["R_imu_from_hall"]=(A@Rbd@Q).tolist()
            self.candidate=result
            self.stage="VALIDATE"
            self.message="求解完成。现在换至少 3 个未参与求解的新方向做独立验证，不要重新调整 Mapper。"
            self.validation.clear()
            return self.state()

    def _evaluate_validation(self):
        R=np.array([s["R"] for s in self.validation],float)
        H=np.array([s["B"] for s in self.validation],float)
        q=np.asarray(self.candidate["R_brush_from_hall"],float)
        Bw=np.asarray(self.candidate["world_field_mT"],float)
        stat=error_stats(R,H,q,target=Bw)
        threshold=max(0.025,0.15*self.candidate["field_magnitude_mean_mT"])
        # Assess the PREVIOUS mount using its own training field estimate,
        # not the new solution's world field.
        trainR=np.array([s["R"] for s in self.train],float)
        trainB=np.array([s["B"] for s in self.train],float)
        baseQ=self.hall.R_brush_from_hall
        baseBw=field_values(trainR,trainB,baseQ).mean(axis=0)
        base=error_stats(R,H,baseQ,target=baseBw)
        passed=(stat["rms_mT"]<=threshold and
                stat["rms_mT"]<=base["rms_mT"]+0.015)
        self.validation_report={
            "rms_mT":stat["rms_mT"],"median_mT":stat["median_mT"],
            "max_mT":stat["max_mT"],"baseline_rms_mT":base["rms_mT"],
            "threshold_mT":threshold,"passed":bool(passed),
            "count":len(self.validation)}
        self.stage="READY" if passed else "VALIDATE"
        self.message=("独立验证通过；可以保存外参" if passed else
                      f"验证误差 {stat['rms_mT']:.4f} mT 超限/较旧版本退化。请调整环境或重新采集验证姿态")
        if not passed:
            # Do not auto-append endlessly on subsequent frames.
            self.armed=False

    def state(self):
        with self.lock:
            s={
                "schema":"ebrush.hall_extrinsic_session.v1",
                "stage":self.stage,"message":self.message,
                "armed":self.armed,"pending_frames":len(self.pending),
                "pending_hold_s":0.0 if not self.pending else float(self.pending[-1]["time"]-self.pending_t0),
                "train_count":len(self.train),"train_target":MIN_TRAIN,
                "validation_count":len(self.validation),"validation_target":MIN_VALIDATE,
                "can_solve":self.stage=="COLLECT" and len(self.train)>=MIN_TRAIN and not self.armed,
                "can_commit":self.stage=="READY" and bool(self.validation_report and self.validation_report["passed"]),
                "candidate":copy.deepcopy(self.candidate),"validation":copy.deepcopy(self.validation_report),
                "source_mount_method":self.hall.data.get("method"),
                "active_profile":self._active_name(),
                "runtime_mapping":self.runtime_mapping_status(),
                "rejected_frames":self.rejected_frames,"accepted_frames":self.accepted_frames,
            }
            return s

    def _active_name(self):
        try:
            return json.loads(self.active_path.read_text(encoding="utf-8"))["active"]
        except (OSError,KeyError,ValueError):return None

    def list_profiles(self):
        with self.lock:
            out=[]
            for p in sorted(self.profile_dir.glob("*.json"),reverse=True):
                try:
                    d=json.loads(p.read_text(encoding="utf-8"))
                    is_baseline=d.get("schema")=="ebrush.hall_extrinsic_baseline.v1"
                    out.append({"name":d["name"],"created_unix":d["created_unix"],
                                "validation_rms_mT":None if is_baseline else d["validation"]["rms_mT"],
                                "verified":not is_baseline,
                                "active":d["name"]==self._active_name()})
                except (OSError,ValueError,KeyError):continue
            return out

    def _check_profile_compatibility(self,prof):
        source=prof.get("pose_mapping_reference",{})
        current=self._mapping_info()
        src_mount=source.get("pose_mount_matrix")
        if src_mount is not None and not np.allclose(
                src_mount,current["pose_mount_matrix"],atol=1e-5):
            raise RuntimeError("旧 Hall 外参与当前 PoseMapper rigid mount 不兼容；请先恢复对应姿态映射")
        src_brush=source.get("mapper_brush_xyz_deg")
        if src_brush is not None and not np.allclose(
                src_brush,current["mapper_brush_xyz_deg"],atol=1e-5):
            raise RuntimeError("旧 Hall 外参与当前 Brush/Mount debug 旋转不兼容；请先恢复对应映射版本")

    def _apply_profile(self,prof):
        R=np.asarray(prof["R_brush_from_hall"],float)
        if not is_so3(R):raise ValueError("保存的 Hall 外参矩阵不是 SO(3)")
        baseline=prof.get("schema")=="ebrush.hall_extrinsic_baseline.v1"
        if baseline:
            data=copy.deepcopy(prof["mount_config"])
            data["active_profile"]=prof["name"]
        else:
            data=copy.deepcopy(self.hall.data)
            data["schema"]="ebrush.hall_mount.v3"
            data["method"]="multipose_static_uniform_field_fit"
            data["R_brush_from_hall"]=R.tolist()
            data["active_profile"]=prof["name"]
            data["calibration_quality"]={
                "training_rms_mT":prof["solution"]["training_rms_mT"],
                "validation_rms_mT":prof["validation"]["rms_mT"],
                "validation_passed":prof["validation"]["passed"],
                "pose_span_deg":prof["solution"]["excitation"]["pose_span_deg"],
                "excitation_ratio":prof["solution"]["excitation"]["excitation_ratio"],
            }
        # Backup old on-disk data before replacing; rollback on I/O failure.
        with self.hall.lock:
            previous=copy.deepcopy(self.hall.data)
            _atomic_json(self.hall.path,data)
            try:
                self.hall.data=data
                self.hall.R_brush_from_hall=R
                self.hall.history_world.clear()
                self.hall.history_local.clear()
                _atomic_json(self.active_path,{"active":prof["name"],"saved_unix":time.time()})
                self.active_mapping_ref=(None if baseline else prof.get("pose_mapping_reference"))
            except Exception:
                _atomic_json(self.hall.path,previous)
                self.hall.data=previous
                self.hall.R_brush_from_hall=np.asarray(previous["R_brush_from_hall"],float)
                raise

    def commit(self,name=None):
        with self.lock:
            if self.stage!="READY" or not (self.validation_report and self.validation_report["passed"]):
                raise RuntimeError("必须先通过至少 3 个独立姿态的外参验证")
            if not self._guard({"reference_locked":bool(self.pose_mapper.reference_locked)}):
                raise RuntimeError("PoseMapper reference / mapping 已变化")
            name=_profile_name(name)
            path=self.profile_dir/(name+".json")
            if path.exists():raise FileExistsError("外参版本名已存在；为了可回退不允许覆盖")
            profile={
                "schema":"ebrush.hall_extrinsic_profile.v1",
                "name":name,"created_unix":time.time(),"parent":self._active_name(),
                "sensor":"TMAG5273",
                "R_brush_from_hall":self.candidate["R_brush_from_hall"],
                "R_imu_from_hall":self.candidate["R_imu_from_hall"],
                "frame_conventions":{
                    "R_brush_from_hall":"Hall vector coordinates -> current mapped Brush frame",
                    "R_imu_from_hall":"Hall vector coordinates -> physical IMU sensor axes",
                    "brush_positive_z":"tip-to-tail",
                    "hall_components":"TMAG5273 X/Y/Z in mT",
                },
                "solution":self.candidate,"validation":self.validation_report,
                "pose_mapping_reference":self.mapping_fingerprint,
                "train_samples":[self._serialize_sample(s) for s in self.train],
                "validation_samples":[self._serialize_sample(s) for s in self.validation],
                "assumptions":{
                    "ambient_magnetic_field_uniform_and_stationary":True,
                    "rigid_hall_imu_mount":True,
                    "absolute_imu_yaw_required":False,
                    "calibrates_additive_hall_bias":False,
                    "hall_imu_same_frame_static_hold":True,
                }}
            _atomic_json(path,profile,overwrite=False)
            try:self._apply_profile(profile)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            self.stage="COMMITTED";self.message=f"已保存并启用霍尔外参版本：{name}"
            return {"name":name,"path":str(path),"validation_rms_mT":self.validation_report["rms_mT"]}

    def _serialize_sample(self,s):
        return {
            "R_world_from_brush":s["R"].tolist(),
            "B_hall_mean_mT":s["B"].tolist(),
            **{k:v for k,v in s.items() if k not in ("R","B")}
        }

    def load(self,name):
        with self.lock:
            name=_profile_name(name)
            p=self.profile_dir/(name+".json")
            if not p.exists():raise FileNotFoundError(name)
            d=json.loads(p.read_text(encoding="utf-8"))
            is_baseline=d.get("schema")=="ebrush.hall_extrinsic_baseline.v1"
            if not is_baseline and (
                d.get("schema")!="ebrush.hall_extrinsic_profile.v1"
                or not d.get("validation",{}).get("passed")):
                raise ValueError("不是已验证的 Hall 外参 profile")
            if not is_baseline:
                self._check_profile_compatibility(d)
            if self.stage in ("COLLECT","VALIDATE","READY"):
                self._invalidate("加载旧版本中断了当前标定")
            self._apply_profile(d)
            return {
                "name":name,
                "status":"UNVERIFIED_BASELINE" if is_baseline else "VERIFIED_PROFILE",
                "validation_rms_mT":None if is_baseline else d["validation"]["rms_mT"],
                "R_brush_from_hall":d["R_brush_from_hall"],
                "warning":"原始映射仅供回退，不代表通过外参校准" if is_baseline else None,
            }

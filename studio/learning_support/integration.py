"""E-Brush teaching adapter: navigation and read-only state observation only."""
from pathlib import Path
from . import anchor

VIEW_HELP = {'采集质量': 'eb.view.quality', '彩色图像': 'eb.view.rgb', '深度图': 'eb.view.depth', 'SAM3 掩膜叠加': 'eb.view.mask', 'IMU ↔ 视觉验证': 'eb.view.validation', '三维场景': 'eb.view.scene', '霍尔磁通': 'eb.view.hall', '姿态映射校准': 'eb.view.pose', '霍尔外参标定': 'eb.view.hallcal', 'Agent 操作审计': 'eb.view.audit', 'RC Studio 数据交接': 'eb.view.handoff', 'Agent 工作流': 'eb.view.agent'}
VIEW_SECTIONS = {
    "quality": "sensors", "rgb": "camera", "depth": "camera", "mask": "camera",
    "validation": "diagnostics", "scene": "sensors", "hall": "sensors",
    "pose": "calibration", "hallcal": "calibration", "audit": "diagnostics",
    "handoff": "handoff", "agent": "workflow",
}

def evaluate(window, name, session):
    """Observe existing state. No IO, parameter changes, service calls or hardware commands."""
    if name.startswith("device."):
        data = getattr(window, "_last_quality", {}) or {}
        if name == "device.streaming":
            ok = bool(data.get("connected") and data.get("streaming"))
            return ok, "实时连接：" + str(data.get("summary", "尚无数据"))
        if name == "device.imu_fresh":
            ok = bool(data.get("streaming") and data.get("imu") == "有效 / 新鲜")
            return ok, "IMU 状态：" + str(data.get("imu", "尚无数据"))
    if name.startswith("pose."):
        state = getattr(window, "_learning_pose_state", {}) or {}
        stage = state.get("stage", "IDLE")
        results = state.get("results") or {}
        checks = {
            "pose.started": stage in ("ON_STAND", "PICK_UP", "UP_R0", "X_TEST", "Y_TEST", "ROLL_TEST", "REVIEW", "COMMITTED"),
            "pose.up0": bool(results.get("up0_confirmation") and results.get("up_r0_locked")),
            "pose.ready": bool(state.get("can_commit") or stage == "COMMITTED"),
            "pose.saved": stage == "COMMITTED",
        }
        return bool(checks.get(name)), f"业务阶段：{stage}。{state.get('message', '')}"
    if name.startswith("hall."):
        state = getattr(window, "_learning_hall_state", {}) or {}
        stage = state.get("stage", "IDLE")
        checks = {
            "hall.started": stage in ("COLLECT", "VALIDATE", "READY", "COMMITTED"),
            "hall.training": int(state.get("train_count", 0)) >= int(state.get("train_target", 8)),
            "hall.candidate": bool(state.get("candidate")),
            "hall.ready": bool(state.get("can_commit") or stage == "COMMITTED"),
            "hall.saved": stage == "COMMITTED",
        }
        return bool(checks.get(name)), (
            f"业务阶段：{stage}；训练姿态 {state.get('train_count', 0)}；"
            f"独立验证姿态 {state.get('validation_count', 0)}。{state.get('message', '')}")
    if name == "handoff.saved":
        filename = getattr(window, "_learning_last_handoff", "")
        return bool(filename), ("本次已成功写入移交清单：" + filename if filename else
                               "等待你选择源文件并成功保存清单；取消不会完成此步骤。")
    if name == "rgbd.recording":
        running = (getattr(window, "recorder", None) is not None
                   and not getattr(window, "ending", False)
                   and not window.stop_done.is_set())
        if running:
            session.progress.context["rgbd_seen_recording"] = True
        return running, ("已观察到采集会话运行。" if running else "请自行完成采集前检查并启动；教学不代为启动相机。")
    if name == "rgbd.stopped":
        # The original recorder object is retained after close(); stop_done is the actual completion signal.
        done = bool(session.progress.context.get("rgbd_seen_recording") and
                    window.stop_done.is_set() and not window.ending and not window.stop_error
                    and not getattr(window, "_learning_fatal_capture", False))
        return done, ("已完成收尾，未报告归档错误；精度和标注仍需独立验收。" if done else
                      "等待本轮采集完成归档；采集完整性或收尾错误不能标记为通过。")
    return False, "当前入口没有该状态。可以暂停，或明确跳过而不标记完成。"

def _bind_form_labels(window):
    from PySide6 import QtWidgets
    for form in window.findChildren(QtWidgets.QFormLayout):
        for row in range(form.rowCount()):
            field = form.itemAt(row, QtWidgets.QFormLayout.ItemRole.FieldRole)
            label = form.itemAt(row, QtWidgets.QFormLayout.ItemRole.LabelRole)
            if field and label and field.widget() and label.widget():
                key = field.widget().property("learningHelpId")
                if key:
                    anchor(label.widget(), key)

def install(window):
    from .qt import LearningManager
    for name, widget in window._views:
        anchor(widget, VIEW_HELP[name])
        widget.setProperty("learningTargetPriority", 10)
    for key in ("rate", "loss", "imu", "hall"):
        widget = getattr(window.quality_panel, key)
        anchor(widget, "eb.quality." + key)
        anchor(widget.value, "eb.quality." + key)
    anchor(window.quality_panel.details, "eb.quality.details")
    anchor(window.quality_panel.summary, "eb.quality.details")
    anchor(window.handoff_panel.service, "eb.handoff.probe")
    for key, label in window.hallui.labels.items():
        anchor(label, "eb.hall." + key)
    anchor(window.hallui.preview, "eb.view.hall")
    for attr in ("brush_status_lbl", "brush_port_lbl", "brush_hall_lbl", "brush_imu_lbl"):
        if hasattr(window, attr):
            anchor(getattr(window, attr), "eb.serial")
    anchor(window.colormap_panel, "eb.view.depth")
    for attr, suffix in (("sld_mix", "mix"), ("sld_bright", "brightness"),
                         ("sld_contrast", "contrast"), ("sld_dmin", "dmin"), ("sld_dmax", "dmax")):
        for widget in getattr(window.colormap_panel, attr):
            anchor(widget, "eb.depth." + suffix)
    _bind_form_labels(window)

    def reveal(key):
        # Only show existing panels/docks; never call click(), start(), confirm(), commit() or setValue().
        suffix = None
        if key.startswith("eb.pose."): suffix = "pose"
        elif key.startswith("eb.hallcal."): suffix = "hallcal"
        elif key.startswith("eb.hall."): suffix = "hall"
        elif key.startswith("eb.handoff."): suffix = "handoff"
        elif key.startswith("eb.quality."): suffix = "quality"
        elif key.startswith("eb.view."): suffix = key.split(".")[-1]
        if suffix in VIEW_SECTIONS:
            window.navigation.select(VIEW_SECTIONS[suffix])
            names = [label for label, topic in VIEW_HELP.items() if topic == "eb.view." + suffix]
            if names:
                window.workspace.select_named_views(names)
        if key.startswith(("eb.map.", "eb.mapping")):
            window._mapper_dock.show()
        if key.startswith(("eb.depth.", "eb.sam.", "eb.runtime", "eb.serial", "eb.reconnect", "eb.auto_reconnect")):
            window._runtime_dock.show()

    manager = LearningManager(
        window, Path(__file__).parent, app_id="EBrushStudio", app_name="E-Brush Studio",
        mode=window._theme_mode, reveal_hook=reveal,
        predicate_hook=lambda name, session: evaluate(window, name, session))
    # The standalone RGB-D tutorial belongs to its own window, not to the main sensor/calibration UI.
    manager.catalog.courses.pop("eb_rgbd", None)
    return manager

def install_rgbd(window):
    from .qt import LearningManager
    anchor(window, "eb.rgbd.status")
    anchor(window.color_view, "eb.rgbd.status")
    anchor(window.depth_view, "eb.rgbd.status")
    anchor(window.notice, "eb.rgbd.status")
    manager = LearningManager(
        window, Path(__file__).parent, app_id="EBrushRGBDStudio", app_name="E-Brush RGB-D Studio",
        predicate_hook=lambda name, session: evaluate(window, name, session))
    manager.catalog.courses = {k:v for k,v in manager.catalog.courses.items() if k == "eb_rgbd"}
    return manager

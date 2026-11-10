"""Application-state gates tested against cached, hardware-free observations."""
import importlib
import threading
from types import SimpleNamespace
from test_learning_model import NAME, PACKAGE, LessonSession, catalog

adapter = importlib.import_module(NAME + ".integration")


def session_for(course_id):
    return LessonSession(catalog.courses[course_id])


class Box:
    def __init__(self, data): self.data = data
    def currentData(self): return self.data


def test_application_specific_observations():
    if PACKAGE.name == "studio":
        window = SimpleNamespace(_last_quality={})
        session = session_for("eb_quality")
        assert not adapter.evaluate(window, "device.streaming", session)[0]
        window._last_quality = {"connected": True, "streaming": True, "imu": "无效或不新鲜"}
        assert adapter.evaluate(window, "device.streaming", session)[0]
        assert not adapter.evaluate(window, "device.imu_fresh", session)[0]
        window._last_quality["imu"] = "有效 / 新鲜"
        assert adapter.evaluate(window, "device.imu_fresh", session)[0]
    else:
        window = SimpleNamespace(project_box=Box("generic_esn"), dataset_box=Box({"kind": "sine"}))
        session = session_for("rc_first")
        assert adapter.evaluate(window, "rc.generic", session)[0]
        assert adapter.evaluate(window, "rc.sine", session)[0]
        assert not adapter.evaluate(window, "rc.ebrush", session)[0]
        window.project_box.data = "ebrush_motion_v1"
        assert not adapter.evaluate(window, "rc.sine", session)[0]


def test_calibration_or_selection_gate_never_accepts_false_success():
    if PACKAGE.name == "studio":
        state = {"stage": "REVIEW", "can_commit": False, "results": {"up_r0_locked": True}}
        window = SimpleNamespace(_learning_pose_state=state)
        session = session_for("eb_pose")
        assert not adapter.evaluate(window, "pose.up0", session)[0]
        assert not adapter.evaluate(window, "pose.ready", session)[0]
        assert not adapter.evaluate(window, "pose.saved", session)[0]
        state["results"]["up0_confirmation"] = {"manual": True}
        assert adapter.evaluate(window, "pose.up0", session)[0]
        state["can_commit"] = True
        assert adapter.evaluate(window, "pose.ready", session)[0]
        state["stage"] = "COMMITTED"
        assert adapter.evaluate(window, "pose.saved", session)[0]
    else:
        entries = [{"id":str(i), "recording_id":"same_record", "synthetic":False} for i in range(3)]
        window = SimpleNamespace(project_box=Box("ebrush_motion_v1"), latest_sessions=entries,
                                 _selected_sessions=lambda:["0","1","2"])
        session=session_for("rc_handoff")
        assert not adapter.evaluate(window, "rc.real_selected", session)[0]
        for i,e in enumerate(entries): e["recording_id"]=str(i)
        assert adapter.evaluate(window, "rc.real_selected", session)[0]
        entries[0]["synthetic"]=True
        assert not adapter.evaluate(window, "rc.real_selected", session)[0]
        assert not adapter.evaluate(window, "rc.demo_selected", session)[0]
        for e in entries:e["synthetic"]=True
        assert adapter.evaluate(window, "rc.demo_selected", session)[0]


def test_hall_or_training_requires_true_completion():
    if PACKAGE.name == "studio":
        state={"stage":"COLLECT","train_count":7,"train_target":8,"candidate":None,"can_commit":False}
        window=SimpleNamespace(_learning_hall_state=state)
        session=session_for("eb_hall")
        assert not adapter.evaluate(window,"hall.training",session)[0]
        state["train_count"]=8
        assert adapter.evaluate(window,"hall.training",session)[0]
        assert not adapter.evaluate(window,"hall.candidate",session)[0]
        state.update(stage="VALIDATE",candidate={"matrix":[1,2,3]})
        assert adapter.evaluate(window,"hall.candidate",session)[0]
        assert not adapter.evaluate(window,"hall.ready",session)[0]
        state.update(stage="READY",can_commit=True)
        assert adapter.evaluate(window,"hall.ready",session)[0]
        assert not adapter.evaluate(window,"hall.saved",session)[0]
    else:
        window=SimpleNamespace(project_box=Box("generic_esn"),
                               _learning_last_run={"id":"old","status":"finished","config":{"dataset":{"kind":"sine"}}})
        session=session_for("rc_first")
        assert not adapter.evaluate(window,"rc.course_finished",session)[0]
        session.progress.context["submitted_run"]="new"
        assert not adapter.evaluate(window,"rc.course_finished",session)[0]
        window._learning_last_run["id"]="new"
        for status in ["failed","interrupted","cancelled","queued","running"]:
            window._learning_last_run["status"]=status
            assert not adapter.evaluate(window,"rc.course_finished",session)[0]
        window._learning_last_run["status"]="finished"
        assert adapter.evaluate(window,"rc.course_finished",session)[0]
        window._learning_last_run["config"]["dataset"]["kind"]="csv"
        assert not adapter.evaluate(window,"rc.course_finished",session)[0]


def test_archive_or_ebrush_course_outcome_has_correct_provenance():
    if PACKAGE.name == "studio":
        done=threading.Event()
        window=SimpleNamespace(recorder=object(),ending=False,stop_done=done,stop_error=None)
        session=session_for("eb_rgbd")
        assert not adapter.evaluate(window,"rgbd.stopped",session)[0]
        assert adapter.evaluate(window,"rgbd.recording",session)[0]
        done.set()
        # The application intentionally retains recorder; stop_done, not recorder=None, proves close returned.
        assert adapter.evaluate(window,"rgbd.stopped",session)[0]
        window._learning_fatal_capture=True
        assert not adapter.evaluate(window,"rgbd.stopped",session)[0]
        window._learning_fatal_capture=False
        window.stop_error="writer failed"
        assert not adapter.evaluate(window,"rgbd.stopped",session)[0]
    else:
        session=session_for("rc_ebrush_demo")
        session.progress.context.update(submitted_run="abc",submitted_selection_kind="real",submitted_recordings=3)
        window=SimpleNamespace(project_box=Box("ebrush_motion_v1"),_learning_last_run={
            "id":"abc","status":"finished","config":{"project":"ebrush_motion_v1"}})
        assert not adapter.evaluate(window,"rc.course_finished",session)[0]
        session.progress.context["submitted_selection_kind"]="demo"
        assert adapter.evaluate(window,"rc.course_finished",session)[0]
        session.progress.context["submitted_recordings"]=1
        assert not adapter.evaluate(window,"rc.course_finished",session)[0]

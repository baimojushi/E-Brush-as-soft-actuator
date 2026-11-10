"""Hardware-free regression tests for the local learning system."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / ("studio" if (ROOT / "studio" / "learning_support").is_dir() else "rcstudio")
LEARNING = PACKAGE / "learning_support"
NAME = "_learning_under_test_" + PACKAGE.name
spec = importlib.util.spec_from_file_location(NAME, LEARNING / "__init__.py",
                                            submodule_search_locations=[str(LEARNING)])
pkg = importlib.util.module_from_spec(spec)
sys.modules[NAME] = pkg
spec.loader.exec_module(pkg)
model = __import__(NAME + ".model", fromlist=["Catalog"])
Catalog, LessonSession, LessonProgress = model.Catalog, model.LessonSession, model.LessonProgress
catalog = Catalog.load(LEARNING)


@pytest.mark.parametrize("key", sorted(catalog.entries))
def test_every_entry_has_full_accessible_explanation(key):
    e = catalog.entries[key]
    assert len(e["summary"]) >= 10
    assert len(e["explanation"]) >= 20
    assert len(e["example"]) >= 12
    assert len(e["pitfalls"]) >= 12
    assert isinstance(e["steps"], list) and e["steps"]
    full = catalog.html(key)
    for heading in ("怎样使用", "为什么这样设计", "一个具体例子", "常见误区与排查"):
        assert heading in full
    assert "<script" not in full and "<iframe" not in full and "<img" not in full
    assert key in catalog.search(key)
    assert e["title"] in catalog.html(key, detail=False)


def test_html_escapes_user_supplied_dynamic_text():
    key = next(iter(catalog.entries))
    html = catalog.html(key, current='<script>alert("x")</script>&')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "&amp;" in html


def test_search_is_casefolded_and_combines_terms():
    key = "eb.pose.confirm" if PACKAGE.name == "studio" else "rc.model.horizon"
    assert key in catalog.search(key.upper())
    assert catalog.search("string-that-cannot-be-a-help-topic") == []
    category = catalog.entries[key]["category"]
    assert all(catalog.entries[k]["category"] == category for k in catalog.search("", category))


@pytest.mark.parametrize("course_id", sorted(catalog.courses))
def test_every_course_can_be_completed_only_by_its_gates(course_id):
    course = catalog.courses[course_id]
    session = LessonSession(course)
    for n, step in enumerate(course["steps"]):
        assert session.progress.step_index == n
        assert not session.ready
        assert not session.advance()
        if step["gate"] == "read":
            session.mark_read()
        elif step["gate"] == "quiz":
            wrong = (step["correct"] + 1) % len(step["answers"])
            assert not session.answer(wrong)
            assert not session.advance()
            assert session.answer(step["correct"])
        elif step["gate"] == "event":
            session.notify_event("unrelated.click")
            assert not session.ready
            session.notify_event(step["event"])
        else:
            session.notify_predicate(step["predicate"], False)
            assert not session.ready
            session.notify_predicate(step["predicate"], True, "Observed outcome")
        assert session.ready
        assert session.advance()
    assert session.complete
    assert not session.progress.skipped


@pytest.mark.parametrize("course_id", sorted(catalog.courses))
def test_skip_is_never_success_and_resume_replays_no_action(course_id):
    course = catalog.courses[course_id]
    session = LessonSession(course)
    for _step in course["steps"]:
        assert session.advance(skip=True)
    assert not session.complete and not session.progress.completed
    assert len(session.progress.skipped) == len(course["steps"])
    session.pause()
    restored = LessonSession(course, json.loads(json.dumps(session.progress.to_dict())))
    assert restored.progress.paused
    restored.resume()
    assert not restored.ready
    assert restored.progress.skipped == session.progress.skipped


def test_paused_sessions_ignore_live_events():
    course = next(c for c in catalog.courses.values() if any(s["gate"] == "event" for s in c["steps"]))
    session = LessonSession(course)
    session.progress.step_index = next(i for i,s in enumerate(course["steps"]) if s["gate"] == "event")
    session.pause()
    session.notify_event(session.step["event"])
    assert not session.ready
    session.resume()
    session.notify_event(session.step["event"])
    assert session.ready


@pytest.mark.parametrize("bad", [None, "", [], {"version": 22}, {"course_id": "unknown", "version": 1}])
def test_invalid_progress_resets_to_clean_state(bad):
    course = next(iter(catalog.courses.values()))
    state = LessonProgress.from_dict(bad, course)
    assert state.course_id == course["id"] and state.step_index == 0
    assert not state.completed and not state.skipped


def test_progress_sanitizes_ids_and_context():
    course = next(iter(catalog.courses.values()))
    valid = course["steps"][0]["id"]
    saved = {"version": 1, "course_id": course["id"], "step_index": 9999,
             "completed": [valid, "not-a-step"], "skipped": [valid, "not-a-step"],
             "context": {"run": "abc", "bad": {"code": "x"}, "huge": "x" * 501}}
    state = LessonProgress.from_dict(saved, course)
    assert state.step_index == len(course["steps"]) - 1
    assert state.completed == {valid} and not state.skipped
    assert state.context == {"run": "abc"}


def test_catalog_rejects_invalid_gates_and_missing_fields():
    entries = copy.deepcopy(catalog.entries)
    key = next(iter(entries))
    entries[key].pop("explanation")
    with pytest.raises(model.CatalogError):
        Catalog(entries, list(catalog.courses.values()))
    courses = copy.deepcopy(list(catalog.courses.values()))
    courses[0]["steps"][0]["gate"] = "auto_hardware_action"
    with pytest.raises(model.CatalogError):
        Catalog(catalog.entries, courses)


def test_all_business_widget_constructor_sites_are_bound():
    types = {"QPushButton", "QToolButton", "QCheckBox", "QRadioButton", "QComboBox",
             "QSpinBox", "QDoubleSpinBox", "QSlider", "QLineEdit", "QPlainTextEdit",
             "QTextEdit", "QTextBrowser", "QTableWidget", "QListWidget", "QTabWidget",
             "QGroupBox", "QDockWidget", "QProgressBar", "PlotWidget", "ImageView"}
    missing = []
    counted = 0
    for path in PACKAGE.rglob("*.py"):
        if "learning_support" in path.parts or "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            kind = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if kind not in types:
                continue
            counted += 1
            p = parents.get(n)
            if not (isinstance(p, ast.Call) and isinstance(p.func, ast.Name) and p.func.id == "_help_anchor"):
                missing.append((str(path.relative_to(ROOT)), n.lineno, kind))
            elif isinstance(p.args[1], ast.Constant):
                assert p.args[1].value in catalog.entries
    assert counted > 20
    assert not missing, missing


def test_teaching_framework_has_no_network_device_or_training_imports():
    forbidden = {"requests", "urllib", "http", "socket", "serial", "cv2", "subprocess",
                 "esn", "client", "brush_serial", "hp60c_direct"}
    for path in LEARNING.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                assert not any(a.name.split(".")[0] in forbidden for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                assert n.module.split(".")[0] not in forbidden


def test_adapters_do_not_invoke_hazardous_business_commands():
    tree = ast.parse((LEARNING / "integration.py").read_text(encoding="utf-8"))
    forbidden_methods = {"click", "start_capture", "stop_capture", "confirm_up0", "commit",
                         "submit", "submit_ebrush", "reconnect", "setValue", "_set_theme",
                         "open", "connect", "send", "post", "get"}
    # dict.get is safe; all other listed methods would violate the navigation-only contract.
    forbidden_methods.discard("get")
    used = {n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
    assert not used.intersection(forbidden_methods)


def test_both_theme_token_sets_and_help_shortcuts_are_present():
    text = (LEARNING / "qt.py").read_text(encoding="utf-8")
    assert '"Ctrl+Shift+H"' in text and '"F1"' in text
    assert "WA_TransparentForMouseEvents" in text
    assert "WA_ShowWithoutActivating" in text
    assert "detail=True" in text
    assert "accessible" in text.lower()
    assert "#000000" not in text


def test_observed_event_survives_pause_without_forcing_a_repeated_business_action():
    course = next(c for c in catalog.courses.values() if any(s["gate"] == "event" for s in c["steps"]))
    session = LessonSession(course)
    session.progress.step_index = next(i for i,s in enumerate(course["steps"]) if s["gate"] == "event")
    event = session.step["event"]
    session.notify_event(event)
    assert session.ready and not session.progress.completed
    session.pause()
    restored = LessonSession(course, session.progress.to_dict())
    restored.resume()
    assert restored.ready
    assert restored.advance()

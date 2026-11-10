"""Native offscreen acceptance tests. Skipped when PySide6 is not installed.

Run on the target Windows environment as well as the manual acceptance checklist.
No device, camera, training service or GPU widget is instantiated.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import pytest
pytest.importorskip("PySide6", reason="Native Qt GUI not available; run target-machine acceptance")
from PySide6 import QtCore, QtWidgets, QtTest
import importlib
from test_learning_model import NAME, LEARNING, catalog, PACKAGE
qt=importlib.import_module(NAME+".qt")
anchor=importlib.import_module(NAME).anchor


@pytest.fixture
def ui(tmp_path):
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)
    QtCore.QSettings.setPath(QtCore.QSettings.Format.IniFormat,QtCore.QSettings.Scope.UserScope,str(tmp_path))
    w=QtWidgets.QMainWindow()
    body=QtWidgets.QWidget()
    box=QtWidgets.QVBoxLayout(body)
    key="eb.depth.near" if PACKAGE.name=="studio" else "rc.model.units"
    field=anchor(QtWidgets.QSpinBox(),key)
    field.setRange(0,999)
    field.setValue(123)
    box.addWidget(field)
    button=anchor(QtWidgets.QPushButton("Theme"),"ui.theme")
    box.addWidget(button)
    w.setCentralWidget(body)
    manager=qt.LearningManager(w,LEARNING,app_id="LearningTest",app_name="Acceptance")
    w.resize(640,480);w.show();app.processEvents()
    yield app,w,manager,field,button
    manager.shutdown();w.close();w.deleteLater();app.processEvents()


def test_explicit_registration_accessibility_dynamic_and_disabled(ui):
    app,w,m,field,button=ui
    assert field.accessibleDescription()
    assert field.property("learningHelpId") in m.catalog.entries
    field.setEnabled(False)
    assert m._resolve(field)[0] is field
    assert "不可操作" in m.describe(field)
    extra=anchor(QtWidgets.QPushButton("Later"),"ui.help")
    w.centralWidget().layout().addWidget(extra)
    app.processEvents();m.scan()
    assert extra in m.bound
    assert all(rec["accessible"] for rec in m.coverage())


def test_popup_content_theme_escape_and_current_value(ui):
    app,w,m,field,button=ui
    m.popup.display(field.property("learningHelpId"),field)
    app.processEvents()
    assert "123" in m.popup.body.toPlainText()
    assert "为什么这样设计" in m.popup.body.toPlainText()
    m.set_theme("dark")
    assert m.mode=="dark"
    QtTest.QTest.keyClick(w,QtCore.Qt.Key.Key_Escape)
    app.processEvents()
    assert not m.popup.isVisible()
    m.open_center(field.property("learningHelpId"))
    assert m.center.isVisible()
    assert "123" in m.center.reader.toPlainText()


def test_event_course_requires_user_action_and_skip_remains_incomplete(ui):
    app,w,m,field,button=ui
    cid="eb_intro" if PACKAGE.name=="studio" else "rc_read_results"
    m.start_course(cid)
    assert m.session is not None
    initial=m.session.step["id"]
    m.lesson_next(skip=True)
    assert initial in m.session.progress.skipped
    assert initial not in m.session.progress.completed
    m.pause_course()
    assert m.session.progress.paused and not m.coach.isVisible()
    m.start_course(cid,resume=True)
    assert initial in m.session.progress.skipped

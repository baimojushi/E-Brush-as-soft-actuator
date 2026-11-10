"""Qt implementation of local, contextual help and opt-in interactive lessons.

Design references: AndroidX Material3 Tooltip.kt and Material Web color roles.
This is an original PySide6 adaptation, not an embedded web/Android component.
No network requests, training submissions, hardware commands or telemetry here.
"""
from __future__ import annotations

from html import escape
import json
from pathlib import Path
import weakref

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from .model import Catalog, LessonSession

Qt = QtCore.Qt

COLORS = {
    "light": dict(surface="#F8FAF9", container="#EDF4F1", ink="#182D29",
                  muted="#4E635D", primary="#006B59", on_primary="#FFFFFF",
                  outline="#BECFC7", warning="#8A3D23"),
    "dark": dict(surface="#14221F", container="#21332D", ink="#E0EDE6",
                 muted="#B3C8BE", primary="#86D8BA", on_primary="#00382B",
                 outline="#496259", warning="#FFB59A"),
}


def _alive(widget):
    return widget is not None and isValid(widget)


def _tip(widget, text):
    """Native contextual help for the learning UI itself (no recursive popovers)."""
    widget.setToolTip(text)
    widget.setAccessibleDescription(text)
    return widget


def _text_style(mode="light", size=14):
    c = COLORS[mode]
    return f"""
    QWidget {{color:{c['ink']}; background:{c['surface']};
        font-family:'Microsoft YaHei UI','Noto Sans CJK SC','Segoe UI'; font-size:{size}px;}}
    QLabel {{background:transparent;}}
    QLabel#LearningTitle {{font-size:{size+5}px; font-weight:700;}}
    QLabel#LearningCaption {{color:{c['muted']};}}
    QLabel#LearningWarning {{color:{c['warning']};}}
    QFrame#LearningCard {{background:{c['container']}; border:1px solid {c['outline']};
        border-radius:16px;}}
    QTextBrowser {{background:{c['surface']}; border:none; padding:8px;
        selection-background-color:{c['primary']};}}
    QPushButton,QToolButton {{background:{c['container']}; border:1px solid {c['outline']};
        border-radius:16px; padding:8px 13px; min-height:20px;}}
    QPushButton:hover,QToolButton:hover {{border-color:{c['primary']};}}
    QPushButton:focus,QToolButton:focus,QLineEdit:focus,QSpinBox:focus,QComboBox:focus {{
        border:2px solid {c['primary']};}}
    QPushButton:disabled {{color:{c['muted']}; border:1px dashed {c['outline']};}}
    QPushButton#LearningPrimary {{background:{c['primary']}; color:{c['on_primary']};
        border-color:{c['primary']};}}
    QLineEdit,QSpinBox,QComboBox {{padding:7px; border:1px solid {c['outline']};
        border-radius:8px; background:{c['container']};}}
    QListWidget {{border:1px solid {c['outline']}; border-radius:12px; padding:6px;}}
    QListWidget::item {{padding:8px; border-radius:7px;}}
    QListWidget::item:selected {{background:{c['container']}; color:{c['primary']};}}
    QTabWidget::pane {{border:1px solid {c['outline']}; border-radius:10px;}}
    QTabBar::tab {{padding:10px 16px; background:{c['container']};}}
    QTabBar::tab:selected {{color:{c['primary']}; background:{c['surface']};}}
    QProgressBar {{border:none; border-radius:4px; min-height:7px; max-height:7px;
        background:{c['container']};}}
    QProgressBar::chunk {{background:{c['primary']}; border-radius:4px;}}
    QCheckBox,QRadioButton {{spacing:8px; padding:5px; background:transparent;}}
    """


def _document_style(mode, size):
    c = COLORS[mode]
    return (f"body {{font-size:{size}px; color:{c['ink']};}} "
            f"h2 {{font-size:{size+5}px; color:{c['ink']};}} "
            f"h3 {{font-size:{size}px; color:{c['primary']}; margin-top:14px;}} "
            "p, li {line-height:145%;} li {margin-bottom:6px;}")


def place_popup(popup, anchor_rect, screen):
    """Clamp to the anchor's own screen, including negative-origin monitors."""
    bounds = screen.availableGeometry().adjusted(10, 10, -10, -10)
    popup.resize(min(popup.width(), bounds.width()), min(popup.height(), bounds.height()))
    candidates = [
        QtCore.QPoint(anchor_rect.right()+10, anchor_rect.top()),
        QtCore.QPoint(anchor_rect.left()-popup.width()-10, anchor_rect.top()),
        QtCore.QPoint(anchor_rect.left(), anchor_rect.bottom()+10),
        QtCore.QPoint(anchor_rect.left(), anchor_rect.top()-popup.height()-10),
    ]
    for point in candidates:
        if bounds.contains(QtCore.QRect(point, popup.size())):
            popup.move(point)
            return
    point = candidates[0]
    popup.move(max(bounds.left(), min(point.x(), bounds.right()-popup.width()+1)),
               max(bounds.top(), min(point.y(), bounds.bottom()-popup.height()+1)))


class RichHelpCard(QtWidgets.QFrame):
    def __init__(self, manager):
        super().__init__(manager.window, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.manager = manager
        self.key = ""
        self.setObjectName("LearningCard")
        self.setProperty("learningUi", True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle("控件说明")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        row = QtWidgets.QHBoxLayout()
        caption = QtWidgets.QLabel("可滚动阅读  ·  F1 固定说明")
        caption.setObjectName("LearningCaption")
        row.addWidget(caption, 1)
        close = _tip(QtWidgets.QToolButton(), "收起当前说明，不执行控件原有操作。也可按 Esc。")
        close.setText("×")
        close.setAccessibleName("关闭悬停说明")
        close.clicked.connect(manager.dismiss_popup)
        row.addWidget(close)
        layout.addLayout(row)
        self.body = QtWidgets.QTextBrowser()
        self.body.setOpenExternalLinks(False)
        self.body.setOpenLinks(False)
        self.body.setAccessibleName("控件的操作说明")
        self.body.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.body, 1)
        more = _tip(QtWidgets.QPushButton("固定并深入了解"),
                    "打开可搜索、可选择复制文字的完整说明；不会启动该控件的功能。")
        more.setObjectName("LearningPrimary")
        more.clicked.connect(lambda: manager.open_center(self.key))
        layout.addWidget(more)
        self.resize(420, 340)

    def display(self, key, widget):
        self.key = key
        self.setStyleSheet(_text_style(self.manager.mode, self.manager.font_size))
        self.body.document().setDefaultStyleSheet(_document_style(self.manager.mode, self.manager.font_size))
        self.body.setHtml(self.manager.catalog.html(key, detail=True, current=self.manager.describe(widget)))
        self.resize(420, 360)
        rect = QtCore.QRect(widget.mapToGlobal(QtCore.QPoint(0, 0)), widget.size())
        screen = QtGui.QGuiApplication.screenAt(rect.center()) or widget.screen()
        if screen:
            place_popup(self, rect, screen)
        self.show()
        self.raise_()

    def enterEvent(self, event):
        self.manager.leave_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.manager.leave_timer.start()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.manager.dismiss_popup()
        else:
            super().keyPressEvent(event)


class TargetHalo(QtWidgets.QWidget):
    """A transparent, non-intercepting highlight. Never replaces the real control."""
    def __init__(self, parent, mode):
        super().__init__(parent)
        self.mode = mode
        self.setProperty("learningUi", True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor(COLORS[self.mode]["primary"]), 3)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(3, 3, -3, -3), 9, 9)
        painter.end()


class HelpCenter(QtWidgets.QDialog):
    def __init__(self, manager):
        super().__init__(manager.window)
        self.manager = manager
        self.current_key = None
        self.setProperty("learningUi", True)
        self.setWindowTitle(f"{manager.app_name} · 学习中心")
        self.setModal(False)
        self.resize(900, 670)
        self.setMinimumSize(590, 430)
        outer = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("理解每个控件，从第一步开始")
        title.setObjectName("LearningTitle")
        outer.addWidget(title)
        caption = QtWidgets.QLabel("本地离线帮助  ·  不发送使用记录  ·  课程由你主动开始")
        caption.setObjectName("LearningCaption")
        outer.addWidget(caption)
        self.tabs = QtWidgets.QTabWidget()
        _tip(self.tabs, "说明库用于检索控件；新手课程引导实际操作；阅读设置只改变帮助系统。")
        outer.addWidget(self.tabs, 1)

        library = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(library)
        tools = QtWidgets.QHBoxLayout()
        self.search_box = _tip(QtWidgets.QLineEdit(), "输入中文名称、英文缩写或参数名；多个词按同时包含匹配。")
        self.search_box.setPlaceholderText("搜索：谱半径、UP0、CSV、丢帧、horizon…")
        self.category = _tip(QtWidgets.QComboBox(), "按功能区域缩小说明范围；选择全部可恢复整个目录。")
        self.category.addItem("全部分类", "")
        for category in sorted({e["category"] for e in manager.catalog.entries.values()}):
            self.category.addItem(category, category)
        tools.addWidget(self.search_box, 1)
        tools.addWidget(self.category)
        ll.addLayout(tools)
        splitter = QtWidgets.QSplitter()
        self.results = _tip(QtWidgets.QListWidget(), "点击一个条目阅读说明；上下方向键也可选择。")
        self.results.setMinimumWidth(175)
        self.reader = _tip(QtWidgets.QTextBrowser(), "完整说明可以滚动、选择并复制；它不会执行命令或修改参数。")
        self.reader.setOpenExternalLinks(False)
        self.reader.setOpenLinks(False)
        self.reader.setAccessibleName("完整控件说明")
        splitter.addWidget(self.results)
        splitter.addWidget(self.reader)
        splitter.setSizes([255, 590])
        ll.addWidget(splitter, 1)
        self.locate_button = _tip(QtWidgets.QPushButton("在软件中定位这个控件"),
                                  "切换到相应页面并短暂高亮。只改变界面位置，不点击按钮、不改参数。")
        self.locate_button.clicked.connect(self.locate)
        ll.addWidget(self.locate_button)
        self.tabs.addTab(library, "控件说明")
        self.search_box.textChanged.connect(self.filter_entries)
        self.category.currentIndexChanged.connect(self.filter_entries)
        self.results.currentItemChanged.connect(self.select_item)

        courses = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(courses)
        intro = QtWidgets.QLabel("先走无硬件入门，再按需做真实实验。暂停教学不会停止采集或训练。")
        intro.setWordWrap(True)
        cl.addWidget(intro)
        self.course_list = _tip(QtWidgets.QListWidget(), "选择课程查看目标和前置条件；不会自动开始。")
        for course in manager.catalog.courses.values():
            item = QtWidgets.QListWidgetItem(f"{course['title']}  ·  {len(course['steps'])} 步")
            item.setData(Qt.ItemDataRole.UserRole, course["id"])
            self.course_list.addItem(item)
        cl.addWidget(self.course_list, 1)
        self.course_info = QtWidgets.QLabel()
        self.course_info.setWordWrap(True)
        cl.addWidget(self.course_info)
        row = QtWidgets.QHBoxLayout()
        self.new_course = _tip(QtWidgets.QPushButton("从头开始"),
                              "从课程第一步开始。只重置这门课程的学习记录，不清除数据、模型或标定。")
        self.new_course.setObjectName("LearningPrimary")
        self.resume_course = _tip(QtWidgets.QPushButton("继续上次进度"),
                                 "恢复所选课程的步骤和已完成检查，不重放硬件或训练操作。")
        self.new_course.clicked.connect(lambda: self.start_selected(False))
        self.resume_course.clicked.connect(lambda: self.start_selected(True))
        row.addWidget(self.new_course)
        row.addWidget(self.resume_course)
        cl.addLayout(row)
        self.course_list.currentItemChanged.connect(self.course_changed)
        self.tabs.addTab(courses, "新手课程")

        preferences = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(preferences)
        self.hover = _tip(QtWidgets.QCheckBox("鼠标停留时显示介绍"),
                         "关闭后不再自动弹出说明，但 F1 和学习中心仍可用。")
        self.hover.setChecked(manager.hover_enabled)
        self.focus = _tip(QtWidgets.QCheckBox("键盘聚焦时也显示介绍"),
                         "使用 Tab 导航时显示说明。输入文本时收起，不妨碍编辑。")
        self.focus.setChecked(manager.focus_enabled)
        pl.addWidget(self.hover)
        pl.addWidget(self.focus)
        form = QtWidgets.QFormLayout()
        self.delay = _tip(QtWidgets.QSpinBox(), "停留超过此时间才显示，较长可减少划过控件时的打扰。单位是毫秒。")
        self.delay.setRange(300, 2000)
        self.delay.setSingleStep(100)
        self.delay.setSuffix(" ms")
        self.delay.setValue(manager.hover_delay)
        form.addRow("悬停等待", self.delay)
        self.font = _tip(QtWidgets.QSpinBox(), "只调整帮助浮窗和学习页字号，不更改原软件的图表和参数区。")
        self.font.setRange(12, 20)
        self.font.setValue(manager.font_size)
        form.addRow("帮助文字大小", self.font)
        pl.addLayout(form)
        note = QtWidgets.QLabel("帮助采用静态高亮，无闪烁、无强制动画。Esc 关闭浮窗；F1 打开当前控件详情。")
        note.setWordWrap(True)
        pl.addWidget(note)
        reset = _tip(QtWidgets.QPushButton("清除本软件的学习进度…"),
                     "仅删除本软件学习中心的课程记录；清除前会确认。不会清理采集数据、模型或主题设置。")
        reset.clicked.connect(self.reset_progress)
        pl.addWidget(reset)
        pl.addStretch()
        self.tabs.addTab(preferences, "阅读设置")
        self.hover.toggled.connect(lambda v: manager.set_preference("hover", v))
        self.focus.toggled.connect(lambda v: manager.set_preference("focus", v))
        self.delay.valueChanged.connect(lambda v: manager.set_preference("delay", v))
        self.font.valueChanged.connect(lambda v: manager.set_preference("font", v))
        close = _tip(QtWidgets.QPushButton("返回工作台"), "关闭学习中心；正在运行的采集、训练和教学不会被取消。")
        close.clicked.connect(self.close)
        outer.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        self.filter_entries()
        if self.course_list.count():
            self.course_list.setCurrentRow(0)
        self.restyle()

    def restyle(self):
        self.setStyleSheet(_text_style(self.manager.mode, self.manager.font_size))
        self.reader.document().setDefaultStyleSheet(_document_style(self.manager.mode, self.manager.font_size))
        if self.current_key:
            self.show_topic(self.current_key)

    def filter_entries(self, *_args):
        old = self.current_key
        self.results.clear()
        keys = self.manager.catalog.search(self.search_box.text(), self.category.currentData() or "")
        for key in keys:
            entry = self.manager.catalog.entries[key]
            item = QtWidgets.QListWidgetItem(f"{entry['title']}\n{entry['category']}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.results.addItem(item)
            if old == key:
                self.results.setCurrentItem(item)
        if self.results.currentRow() < 0 and self.results.count():
            self.results.setCurrentRow(0)
        if not keys:
            self.current_key = None
            self.reader.setPlainText("没有匹配的说明。请尝试更短的关键词，或切换到“全部分类”。")
            self.locate_button.setEnabled(False)

    def select_item(self, item, _previous=None):
        if item:
            self.show_topic(item.data(Qt.ItemDataRole.UserRole))

    def show_topic(self, key):
        if key not in self.manager.catalog.entries:
            return
        self.current_key = key
        widget = self.manager.find_target(key)
        self.reader.setHtml(self.manager.catalog.html(key, current=self.manager.describe(widget)))
        # Some glossary entries describe a concept, not a concrete widget.
        self.locate_button.setEnabled(widget is not None)

    def open_topic(self, key):
        self.tabs.setCurrentIndex(0)
        self.search_box.clear()
        self.category.setCurrentIndex(0)
        for i in range(self.results.count()):
            if self.results.item(i).data(Qt.ItemDataRole.UserRole) == key:
                self.results.setCurrentRow(i)
                break
        self.show_topic(key)

    def locate(self):
        if self.current_key:
            if self.manager.reveal(self.current_key):
                self.hide()
                self.manager.highlight(self.current_key, transient=True)

    def course_changed(self, item, _previous=None):
        if item is None:
            return
        course = self.manager.catalog.courses[item.data(Qt.ItemDataRole.UserRole)]
        saved = self.manager.saved_progress(course["id"])
        progress = LessonSession(course, saved).progress
        done = len(progress.completed)
        skipped = len(progress.skipped)
        self.course_info.setText(
            f"{course['description']}\n\n前置条件：{course['prerequisites']}\n"
            f"学习记录：已完成 {done} 步，跳过 {skipped} 步。跳过不计为完成。")
        self.resume_course.setEnabled(bool(saved))

    def start_selected(self, resume):
        item = self.course_list.currentItem()
        if not item:
            return
        course_id = item.data(Qt.ItemDataRole.UserRole)
        if not resume and self.manager.saved_progress(course_id):
            answer = QtWidgets.QMessageBox.question(
                self, "重新学习", "只重置这门课程的学习记录，数据和模型不变。继续吗？")
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self.manager.start_course(course_id, resume=resume)
        self.hide()

    def reset_progress(self):
        answer = QtWidgets.QMessageBox.question(
            self, "清除学习进度", "只清除本软件的课程进度，不能撤销。\n数据、模型、标定均保留。继续吗？")
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.manager.pause_course()
        self.manager.settings.remove("progress")
        self.manager.settings.remove("last_course")
        self.course_changed(self.course_list.currentItem())


class LessonCoach(QtWidgets.QDialog):
    def __init__(self, manager):
        super().__init__(manager.window, Qt.WindowType.Tool)
        self.manager = manager
        self.setProperty("learningUi", True)
        self.setWindowTitle("新手交互式教学")
        self.setModal(False)
        self.resize(405, 530)
        self.setMinimumSize(300, 280)
        outer_layout = QtWidgets.QVBoxLayout(self)
        body_scroll = QtWidgets.QScrollArea()
        body_scroll.setWidgetResizable(True)
        body_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 2, 2)
        body_scroll.setWidget(content)
        outer_layout.addWidget(body_scroll, 1)
        self.progress_label = QtWidgets.QLabel()
        self.progress_label.setObjectName("LearningCaption")
        layout.addWidget(self.progress_label)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        self.title = QtWidgets.QLabel()
        self.title.setWordWrap(True)
        self.title.setObjectName("LearningTitle")
        layout.addWidget(self.title)
        self.instructions = QtWidgets.QTextBrowser()
        self.instructions.setOpenExternalLinks(False)
        self.instructions.setOpenLinks(False)
        self.instructions.setMinimumHeight(100)
        layout.addWidget(self.instructions, 1)
        self.gate_widget = QtWidgets.QWidget()
        self.gate_layout = QtWidgets.QVBoxLayout(self.gate_widget)
        self.gate_layout.setContentsMargins(0, 0, 0, 0)
        self.gate_scroll = QtWidgets.QScrollArea()
        self.gate_scroll.setWidgetResizable(True)
        self.gate_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.gate_scroll.setMinimumHeight(36)
        self.gate_scroll.setMaximumHeight(190)
        self.gate_scroll.setWidget(self.gate_widget)
        layout.addWidget(self.gate_scroll)
        self.feedback = QtWidgets.QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setObjectName("LearningCaption")
        self.feedback.setAccessibleName("本步骤完成条件与反馈")
        layout.addWidget(self.feedback)
        actions = QtWidgets.QHBoxLayout()
        locate = _tip(QtWidgets.QPushButton("定位控件"), "重新滚动并高亮目标，不代替你点击或改值。")
        locate.clicked.connect(self.locate)
        details = _tip(QtWidgets.QPushButton("深入了解"), "查看当前步骤对应的完整解释，教学进度保留。")
        details.clicked.connect(lambda: manager.open_center(manager.session.step["help"]) if manager.session else None)
        actions.addWidget(locate)
        actions.addWidget(details)
        layout.addLayout(actions)
        row = QtWidgets.QHBoxLayout()
        self.back_button = _tip(QtWidgets.QPushButton("上一步"), "返回上一个教学步骤，不撤销你已执行的业务操作。")
        self.next_button = _tip(QtWidgets.QPushButton("下一步"), "完成检查后进入下一步；不会代替点击采集、保存或训练。")
        self.next_button.setObjectName("LearningPrimary")
        self.back_button.clicked.connect(manager.lesson_back)
        self.next_button.clicked.connect(manager.lesson_next)
        row.addWidget(self.back_button)
        row.addWidget(self.next_button)
        outer_layout.addLayout(row)
        bottom = QtWidgets.QHBoxLayout()
        skip = _tip(QtWidgets.QPushButton("跳过（记为未完成）"),
                    "设备或数据不足时跳过；记录保留为未完成，不表示通过标定或实验。")
        skip.clicked.connect(lambda: manager.lesson_next(skip=True))
        pause = _tip(QtWidgets.QPushButton("暂停教学"), "保存学习位置。注意：不会暂停正在采集的数据或训练任务。")
        pause.clicked.connect(manager.pause_course)
        bottom.addWidget(skip)
        bottom.addWidget(pause)
        outer_layout.addLayout(bottom)

    def display_step(self):
        session = self.manager.session
        if not session:
            return
        self.setStyleSheet(_text_style(self.manager.mode, self.manager.font_size))
        self.instructions.document().setDefaultStyleSheet(_document_style(self.manager.mode, self.manager.font_size))
        step = session.step
        index = session.progress.step_index
        total = len(session.course["steps"])
        self.progress_label.setText(f"{session.course['title']}\n第 {index+1} / {total} 步 · 已完成 {len(session.progress.completed)} · 跳过 {len(session.progress.skipped)}")
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(len(session.progress.completed))
        self.title.setText(step["title"])
        text = f"<p>{escape(step['instruction']).replace(chr(10), '<br>')}</p>"
        text += f"<p><b>本步检查：</b>{escape(step.get('check', '阅读后勾选确认。'))}</p>"
        if step.get("warning"):
            text += f"<p><b>请注意：</b>{escape(step['warning'])}</p>"
        self.instructions.setHtml(text)
        while self.gate_layout.count():
            child = self.gate_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        if getattr(self, "answer_group", None):
            self.answer_group.deleteLater()
        self.answer_group = None
        if step["gate"] == "read":
            confirm = _tip(QtWidgets.QCheckBox("我已阅读并理解本步说明"),
                           "这只确认阅读，不表示硬件标定或模型测试已通过。")
            confirm.setChecked(session.ready)
            confirm.toggled.connect(self.read_checked)
            self.gate_layout.addWidget(confirm)
        elif step["gate"] == "quiz":
            self.answer_group = QtWidgets.QButtonGroup(self)
            for i, label in enumerate(step["answers"]):
                row = QtWidgets.QWidget()
                row_box = QtWidgets.QHBoxLayout(row)
                row_box.setContentsMargins(0, 2, 0, 2)
                button = QtWidgets.QRadioButton()
                button.setAccessibleName(label)
                button.setToolTip("选择答案获得即时反馈；答错可以重选，不影响设备或模型。")
                text = QtWidgets.QLabel(label)
                text.setWordWrap(True)
                text.setBuddy(button)
                text.setToolTip(label)
                # The radio retains keyboard access; clicking its text selects only this quiz answer.
                def choose(_event, choice=button, index=i):
                    choice.setChecked(True)
                    self.answer(index)
                text.mousePressEvent = choose
                self.answer_group.addButton(button, i)
                row_box.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
                row_box.addWidget(text, 1)
                self.gate_layout.addWidget(row)
            self.answer_group.idClicked.connect(self.answer)
        self.gate_scroll.setVisible(step["gate"] in ("read", "quiz"))
        self.back_button.setEnabled(index > 0)
        self.next_button.setText("完成本轮学习" if index == total-1 else "下一步")
        self.refresh_gate()
        self.show()
        self.raise_()

    def read_checked(self, checked):
        if not self.manager.session:
            return
        if checked:
            self.manager.session.mark_read()
        else:
            self.manager.session.ready = False
        self.refresh_gate()

    def answer(self, answer):
        self.manager.session.answer(answer)
        self.refresh_gate()

    def refresh_gate(self):
        session = self.manager.session
        if not session:
            return
        self.next_button.setEnabled(session.ready)
        self.feedback.setText(session.feedback or session.step.get("check", "完成检查后可继续。"))

    def locate(self):
        if self.manager.session:
            key = self.manager.session.step.get("target") or self.manager.session.step["help"]
            self.manager.reveal(key)
            self.manager.highlight(key)

    def closeEvent(self, event):
        self.manager.pause_course()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.manager.pause_course()
            event.accept()
        else:
            super().keyPressEvent(event)


class LearningManager(QtCore.QObject):
    """One manager per application window. Only the adapter may navigate views."""
    def __init__(self, window, folder, *, app_id, app_name, mode="light",
                 reveal_hook=None, predicate_hook=None, start_hook=None):
        super().__init__(window)
        self.window = window
        self.app_name = app_name
        self.catalog = Catalog.load(folder)
        self.settings = QtCore.QSettings(app_id, "Learning-v1")
        self.mode = mode if mode in COLORS else "light"
        def preference(name, default, kind):
            try:
                return self.settings.value(name, default, type=kind)
            except (TypeError, ValueError):
                return default
        self.hover_enabled = preference("hover", True, bool)
        self.focus_enabled = preference("focus", True, bool)
        self.hover_delay = max(300, min(2000, preference("delay", 650, int)))
        self.font_size = max(12, min(20, preference("font", 14, int)))
        self.reveal_hook = reveal_hook
        self.predicate_hook = predicate_hook
        self.start_hook = start_hook
        self.bound = weakref.WeakKeyDictionary()
        self.targets = {}
        self.pending = None
        self.last_anchor = None
        self.suppressed = None
        self._scan_pending = False
        self._navigating = False
        self._closing = False
        self._transient = False
        self.session = None
        self.center = None
        self.coach = None
        self.popup = RichHelpCard(self)
        self.halo = TargetHalo(window, self.mode)
        self.hover_timer = QtCore.QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.timeout.connect(self._show_pending)
        self.leave_timer = QtCore.QTimer(self)
        self.leave_timer.setSingleShot(True)
        self.leave_timer.setInterval(350)
        self.leave_timer.timeout.connect(self._hide_if_left)
        self.lesson_timer = QtCore.QTimer(self)
        self.lesson_timer.setInterval(500)
        self.lesson_timer.timeout.connect(self._lesson_tick)
        self.flash_timer = QtCore.QTimer(self)
        self.flash_timer.setSingleShot(True)
        self.flash_timer.timeout.connect(self._end_flash)
        self.app = QtWidgets.QApplication.instance()
        self.app.installEventFilter(self)
        self._add_menu()
        self.scan()
        # Weak callable avoids keeping a closed manager alive through a global signal.
        self.window.destroyed.connect(self._dispose)

    def _dispose(self, *_args):
        self._closing = True
        if self.app:
            self.app.removeEventFilter(self)

    def _add_menu(self):
        menu = self.window.menuBar().addMenu("帮助与教学(&H)")
        menu.setToolTipsVisible(True)
        center = menu.addAction("打开学习中心")
        center.setShortcut(QtGui.QKeySequence("Ctrl+Shift+H"))
        center.setToolTip("搜索全部控件说明、开始或恢复课程、设置悬停帮助。")
        center.triggered.connect(lambda _checked=False: self.open_center())
        contextual = menu.addAction("解释当前控件")
        contextual.setShortcut(QtGui.QKeySequence("F1"))
        contextual.setToolTip("显示鼠标指向或键盘聚焦控件的完整解释。")
        contextual.triggered.connect(lambda _checked=False: self.context_help())
        resume = menu.addAction("继续上次教学")
        resume.setToolTip("从本软件保存的教学步骤恢复，不重放业务操作。")
        resume.triggered.connect(lambda _checked=False: self.resume_last())
        menu.addSeparator()
        self.hover_action = menu.addAction("悬停显示介绍")
        self.hover_action.setCheckable(True)
        self.hover_action.setChecked(self.hover_enabled)
        self.hover_action.setToolTip("开启/关闭自动浮窗；F1 始终可用。")
        self.hover_action.toggled.connect(lambda v: self.set_preference("hover", v))

    def _belongs(self, widget):
        if not isinstance(widget, QtWidgets.QWidget) or not _alive(widget):
            return False
        current = widget
        while current is not None:
            if current.property("learningUi"):
                return False
            if current is self.window:
                return True
            current = current.parentWidget()
        return False

    def _resolve(self, widget):
        if not self._belongs(widget):
            return None
        current = widget
        while current is not None:
            # Empty application background is not a control: avoid generic popovers everywhere.
            if current is self.window and current.property("learningHelpId") == "ui.fallback":
                break
            key = current.property("learningHelpId")
            if key in self.catalog.entries:
                return current, str(key)
            if current is self.window:
                break
            current = current.parentWidget()
        return None

    def _queue_scan(self):
        if self._scan_pending or self._closing:
            return
        self._scan_pending = True
        QtCore.QTimer.singleShot(0, self._scan_later)

    def _scan_later(self):
        self._scan_pending = False
        if not self._closing and _alive(self.window):
            self.scan()

    def scan(self):
        """Explicit constructor IDs plus inherited help for native child widgets."""
        for widget in [self.window] + self.window.findChildren(QtWidgets.QWidget):
            if widget in self.bound or not self._belongs(widget):
                continue
            key = widget.property("learningHelpId")
            if key not in self.catalog.entries:
                continue
            key = str(key)
            entry = self.catalog.entries[key]
            self.bound[widget] = key
            self.targets.setdefault(key, []).append(weakref.ref(widget))
            widget.setMouseTracking(True)
            widget.setToolTip(entry["summary"] + "\nF1：完整解释；Esc：关闭说明。")
            if not widget.accessibleName():
                widget.setAccessibleName(entry["title"])
            widget.setAccessibleDescription(
                entry["summary"] + " " + " ".join(entry["steps"]) + " " + entry.get("risk", ""))
            self._bind_events(widget, key)

    def _bind_events(self, widget, key):
        if isinstance(widget, QtWidgets.QAbstractButton):
            widget.clicked.connect(lambda _v=False, k=key: self.notify(k + ".clicked"))
        elif isinstance(widget, QtWidgets.QComboBox):
            widget.activated.connect(lambda _v, k=key: self.notify(k + ".changed"))
        elif isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox, QtWidgets.QSlider)):
            ref = weakref.ref(widget)
            widget.valueChanged.connect(lambda _v, r=ref, k=key: self._numeric_event(r, k))
        elif isinstance(widget, QtWidgets.QLineEdit):
            widget.textEdited.connect(lambda _v, k=key: self.notify(k + ".changed"))
        elif isinstance(widget, QtWidgets.QTabWidget):
            widget.tabBarClicked.connect(lambda _v, k=key: self.notify(k + ".clicked"))
        elif isinstance(widget, QtWidgets.QTableWidget):
            widget.cellClicked.connect(lambda _r, _c, k=key: self.notify(k + ".clicked"))

    def _numeric_event(self, ref, key):
        widget = ref()
        focus = self.app.focusWidget()
        if _alive(widget) and (widget.hasFocus() or (focus and widget.isAncestorOf(focus))):
            self.notify(key + ".changed")

    def notify(self, event_id):
        if self._navigating:
            return
        if self.session and not self.session.progress.paused:
            self.session.notify_event(event_id)
            if self.session.ready and self.session.step["gate"] == "event":
                self._save()
            if self.coach:
                self.coach.refresh_gate()

    def find_target(self, key):
        refs = self.targets.get(key, [])
        widgets = [ref() for ref in refs if _alive(ref())]
        widgets.sort(key=lambda w: int(w.property("learningTargetPriority") or 0), reverse=True)
        visible = [w for w in widgets if w.isVisible()]
        return next(iter(visible or widgets), None)

    def describe(self, widget):
        if not _alive(widget):
            return "此条是概念说明，或控件尚未在当前工作区创建。"
        values = []
        if not widget.isEnabled():
            values.append("当前不可操作。请先满足说明中的前置条件；帮助不会绕过禁用状态。")
        if isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            values += [f"当前值：{widget.value()}{widget.suffix()}",
                       f"界面允许范围：{widget.minimum()}～{widget.maximum()}；每次步进：{widget.singleStep()}。"]
        elif isinstance(widget, QtWidgets.QSlider):
            values += [f"当前滑块值：{widget.value()}；范围：{widget.minimum()}～{widget.maximum()}。"]
        elif isinstance(widget, QtWidgets.QComboBox):
            values.append(f"当前选项：{widget.currentText() or '尚未选择'}")
        elif isinstance(widget, QtWidgets.QLineEdit):
            if widget.echoMode() != QtWidgets.QLineEdit.EchoMode.Normal:
                values.append("输入内容已隐藏。")
            else:
                values.append("当前输入：" + (widget.text()[:180] or "空"))
        elif isinstance(widget, QtWidgets.QCheckBox):
            values.append("当前状态：" + ("已勾选" if widget.isChecked() else "未勾选"))
        elif isinstance(widget, QtWidgets.QProgressBar):
            values.append(f"当前显示：{widget.value()} / {widget.maximum()}；进度表示执行阶段；验证结果另行查看。")
        return "\n".join(values)

    def _schedule(self, widget, key, source="hover"):
        if self.suppressed is widget or (self.popup.isVisible() and self.popup.key == key):
            return
        self.pending = (weakref.ref(widget), key, source)
        self.last_anchor = weakref.ref(widget)
        self.hover_timer.start(self.hover_delay)

    def _show_pending(self):
        if not self.pending or self._closing:
            return
        ref, key, source = self.pending
        widget = ref()
        if not _alive(widget) or not widget.isVisible():
            return
        if self.app.mouseButtons() != Qt.MouseButton.NoButton:
            return
        if self.app.activePopupWidget() is not None:
            return  # Do not cover a combo dropdown, native menu or context menu.
        modal = self.app.activeModalWidget()
        if modal and modal is not widget.window():
            return
        # Pending requests may outlive a leave event or a focus transition.
        under = QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos())
        focused = self.app.focusWidget()
        match = self._resolve(focused) if source == "focus" else self._resolve(under)
        if not match or match[0] is not widget:
            return
        self.popup.display(key, widget)

    def _inside_card(self, widget):
        return bool(widget and (widget is self.popup or self.popup.isAncestorOf(widget)))

    def _hide_if_left(self):
        at = self.app.widgetAt(QtGui.QCursor.pos())
        if self._inside_card(at):
            return
        resolved = self._resolve(at)
        if resolved and resolved[1] == self.popup.key:
            return
        self.popup.hide()

    def dismiss_popup(self, *_args):
        self.hover_timer.stop()
        self.leave_timer.stop()
        if self.pending:
            self.suppressed = self.pending[0]()
        self.pending = None
        self.popup.hide()

    def eventFilter(self, obj, event):
        if self._closing:
            return False
        try:
            kind = event.type()
            if kind in (QtCore.QEvent.Type.ChildAdded, QtCore.QEvent.Type.Show):
                if isinstance(obj, QtWidgets.QWidget) and self._belongs(obj):
                    self._queue_scan()
            if kind == QtCore.QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape:
                    if self.popup.isVisible():
                        self.dismiss_popup()
                        return True
                    if self.session and not self.session.progress.paused and self._belongs(obj):
                        self.pause_course()
                        return True
                elif self.popup.isVisible() and event.key() not in (
                    Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_F1
                ):
                    self.dismiss_popup()
            if kind == QtCore.QEvent.Type.MouseButtonPress:
                if not self._inside_card(obj if isinstance(obj, QtWidgets.QWidget) else None):
                    self.dismiss_popup()
            resolved = self._resolve(obj)
            if resolved:
                widget, key = resolved
                if kind == QtCore.QEvent.Type.Enter and self.hover_enabled:
                    self.leave_timer.stop()
                    self.suppressed = None
                    self._schedule(widget, key)
                elif kind == QtCore.QEvent.Type.FocusIn and self.focus_enabled:
                    self.suppressed = None
                    self._schedule(widget, key, source="focus")
                elif kind == QtCore.QEvent.Type.ToolTip:
                    # The custom event timer owns dwell time; suppress duplicate native tips.
                    if self.hover_enabled and self.suppressed is not widget:
                        self._schedule(widget, key)
                    return True
                elif kind in (QtCore.QEvent.Type.Leave, QtCore.QEvent.Type.FocusOut):
                    self.hover_timer.stop()
                    self.leave_timer.start()
                    self.suppressed = None
                elif kind in (QtCore.QEvent.Type.Hide, QtCore.QEvent.Type.Close):
                    if self.last_anchor and self.last_anchor() is widget:
                        self.dismiss_popup()
            if kind == QtCore.QEvent.Type.WindowDeactivate and obj is self.window:
                active = self.app.activeWindow()
                if active not in (self.popup, self.center, self.coach):
                    self.dismiss_popup()
        except RuntimeError:
            # A QObject can be deleted between an event and its timer callback.
            pass
        return False

    def open_center(self, key=None, *, courses=False):
        self.dismiss_popup()
        if self.center is None:
            self.center = HelpCenter(self)
        if key in self.catalog.entries:
            self.center.open_topic(key)
        if courses:
            self.center.tabs.setCurrentIndex(1)
            self.center.course_changed(self.center.course_list.currentItem())
        self.center.show()
        self.center.raise_()
        self.center.activateWindow()

    def context_help(self):
        resolved = self._resolve(self.app.widgetAt(QtGui.QCursor.pos()))
        if not resolved:
            resolved = self._resolve(self.app.focusWidget())
        if not resolved and self.last_anchor:
            resolved = self._resolve(self.last_anchor())
        self.open_center(resolved[1] if resolved else None)

    def set_preference(self, name, value):
        self.settings.setValue(name, value)
        if name == "hover":
            self.hover_enabled = bool(value)
            if self.hover_action.isChecked() != self.hover_enabled:
                self.hover_action.setChecked(self.hover_enabled)
            if not value:
                self.dismiss_popup()
        elif name == "focus":
            self.focus_enabled = bool(value)
            if not value:
                self.dismiss_popup()
        elif name == "delay":
            self.hover_delay = int(value)
        elif name == "font":
            self.font_size = int(value)
            self.set_theme(self.mode)

    def set_theme(self, mode):
        self.mode = mode if mode in COLORS else "light"
        self.halo.mode = self.mode
        self.halo.update()
        self.popup.setStyleSheet(_text_style(self.mode, self.font_size))
        if self.center:
            self.center.restyle()
        if self.coach and self.session and not self.session.progress.paused:
            self.coach.setStyleSheet(_text_style(self.mode, self.font_size))
            self.coach.instructions.document().setDefaultStyleSheet(_document_style(self.mode, self.font_size))

    def reveal(self, key):
        """Adapters may only navigate. Never invoke an anchor's click() or change values."""
        self._navigating = True
        try:
            if self.reveal_hook:
                self.reveal_hook(key)
            self.scan()
            widget = self.find_target(key)
            if not _alive(widget):
                return False
            child = widget
            while child is not None and child is not self.window:
                parent = child.parentWidget()
                if isinstance(parent, QtWidgets.QStackedWidget):
                    tabs = parent.parentWidget()
                    if isinstance(tabs, QtWidgets.QTabWidget):
                        tabs.setCurrentWidget(child)
                if isinstance(parent, QtWidgets.QScrollArea):
                    parent.ensureWidgetVisible(widget, 24, 24)
                child = parent
            # Scroll area viewport parents can be one extra level above the widget.
            parent = widget.parentWidget()
            while parent is not None:
                if isinstance(parent, QtWidgets.QScrollArea):
                    parent.ensureWidgetVisible(widget, 24, 24)
                if parent is self.window:
                    break
                parent = parent.parentWidget()
            return widget.isVisible()
        finally:
            self._navigating = False

    def highlight(self, key, *, transient=False):
        widget = self.find_target(key)
        if not _alive(widget) or not widget.isVisible():
            self.halo.hide()
            return
        owner = widget.window()
        if owner is not self.halo.parentWidget():
            self.halo.setParent(owner)
        pos = widget.mapTo(owner, QtCore.QPoint(0, 0))
        # Clip against ancestors so an off-screen scrolled widget never gets a misleading halo.
        rect = QtCore.QRect(pos, widget.size())
        ancestor = widget.parentWidget()
        while ancestor is not None:
            ar = QtCore.QRect(ancestor.mapTo(owner, QtCore.QPoint(0, 0)), ancestor.size())
            rect = rect.intersected(ar)
            if ancestor is owner:
                break
            ancestor = ancestor.parentWidget()
        if rect.isEmpty():
            self.halo.hide()
            return
        self.halo.setGeometry(rect.adjusted(-3, -3, 3, 3))
        self.halo.show()
        self.halo.raise_()
        self.halo.update()
        if transient:
            self._transient = True
            self.flash_timer.start(2800)
        if self.coach and self.coach.isVisible():
            target_global = QtCore.QRect(widget.mapToGlobal(QtCore.QPoint()), widget.size())
            screen = widget.screen()
            # Relocate only when target and coach overlap, not on every timer tick.
            if screen and self.coach.frameGeometry().intersects(target_global.adjusted(-8, -8, 8, 8)):
                place_popup(self.coach, target_global, screen)

    def _end_flash(self):
        self._transient = False
        if not self.session or self.session.progress.paused:
            self.halo.hide()

    def saved_progress(self, course_id):
        raw = self.settings.value("progress/" + course_id, "")
        try:
            data = json.loads(raw) if raw else None
            return data if isinstance(data, dict) else None
        except (TypeError, ValueError):
            return None

    def _save(self):
        if self.session:
            self.settings.setValue("progress/" + self.session.course["id"],
                                   json.dumps(self.session.progress.to_dict(), ensure_ascii=False))
            self.settings.setValue("last_course", self.session.course["id"])

    def start_course(self, course_id, *, resume=False):
        if course_id not in self.catalog.courses:
            return
        self.pause_course()
        course = self.catalog.courses[course_id]
        self.session = LessonSession(course, self.saved_progress(course_id) if resume else None)
        self.session.resume()
        if self.start_hook:
            self.start_hook(self.session)
        if self.coach is None:
            self.coach = LessonCoach(self)
        self._show_step()
        self.lesson_timer.start()
        self._save()

    def record_context(self, name, value):
        """Record actual application outcomes, not merely button presses."""
        if self.session and not self.session.progress.paused:
            if isinstance(value, (str, int, float, bool, type(None))) and len(str(value)) < 500:
                self.session.progress.context[str(name)] = value
                self._save()

    def resume_last(self):
        course_id = self.settings.value("last_course", "")
        if course_id in self.catalog.courses:
            self.start_course(course_id, resume=True)
        else:
            self.open_center(courses=True)

    def _show_step(self):
        step = self.session.step
        self.dismiss_popup()
        key = step.get("target") or step["help"]
        self.reveal(key)
        self.coach.display_step()
        self.highlight(key)
        self._lesson_tick()

    def _lesson_tick(self):
        if not self.session or self.session.progress.paused:
            return
        step = self.session.step
        if step["gate"] == "predicate":
            try:
                if self.predicate_hook:
                    result = self.predicate_hook(step["predicate"], self.session)
                    ok, detail = result if isinstance(result, tuple) else (bool(result), "")
                else:
                    ok, detail = False, "此环境没有该状态检查，请按说明操作，或明确跳过。"
            except Exception as exc:
                ok, detail = False, f"状态尚不可用：{type(exc).__name__}。可暂停教学后排查。"
            self.session.notify_predicate(step["predicate"], bool(ok), str(detail))
            self.coach.refresh_gate()
        self.highlight(step.get("target") or step["help"])

    def lesson_back(self):
        if not self.session:
            return
        self.session.back()
        self._show_step()
        self._save()

    def lesson_next(self, _checked=False, *, skip=False):
        if not self.session:
            return
        last = self.session.progress.step_index == len(self.session.course["steps"]) - 1
        if not self.session.advance(skip=skip):
            return
        self._save()
        if last:
            done = len(self.session.progress.completed)
            skipped = len(self.session.progress.skipped)
            total = len(self.session.course["steps"])
            self.pause_course()
            conclusion = ("本课程检查全部完成。" if done == total else "路径已浏览，仍有未完成步骤。")
            QtWidgets.QMessageBox.information(
                self.window, "本轮学习记录",
                f"已完成 {done}/{total} 步，跳过 {skipped} 步。\n"
                + conclusion + "\n硬件精度和模型泛化仍需独立验收。")
        else:
            self._show_step()

    def pause_course(self):
        self.lesson_timer.stop()
        if self.session:
            self.session.pause()
            self._save()
        self.halo.hide()
        if self.coach:
            self.coach.hide()

    def shutdown(self):
        """Call from window closeEvent to persist progress and remove the global filter."""
        self.pause_course()
        self.dismiss_popup()
        if self.center:
            self.center.hide()
        self._closing = True
        self.app.removeEventFilter(self)

    def coverage(self):
        """Runtime audit for explicit business-widget bindings; used by GUI acceptance tests."""
        return [{"help_id": key, "class": type(widget).__name__, "visible": widget.isVisible(),
                 "enabled": widget.isEnabled(), "accessible": bool(widget.accessibleDescription())}
                for widget, key in self.bound.items() if _alive(widget)]

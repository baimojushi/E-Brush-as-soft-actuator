"""Responsive, persistent-view workspace for E-Brush Studio.

Unlike the old fixed sqrt(N) grid, this component reacts to the **actual**
available central viewport width after both dock panels take their space.  It
never destroys/recreates live camera/3D/calibration view widgets when a view is
hidden, a tab is selected or the window is resized.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from layout_utils import column_count, forced_minimum_width


class ResponsiveWorkspace(QtWidgets.QWidget):
    sidebarToggleRequested = QtCore.Signal()
    resetLayoutRequested = QtCore.Signal()

    MODES = (
        ("auto", "自动分栏"),
        ("two", "双列对照"),
        ("one", "单列宽屏"),
        ("tabs", "标签聚焦"),
    )

    def __init__(self, views, parent=None):
        super().__init__(parent)
        self.views = list(views)
        self.visible_flags = [True] * len(self.views)
        self.mode = "auto"
        self.column_count = -1
        self._tiles = []
        self._toggle_buttons = []

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(5)

        # Scroll the control row as well; otherwise its buttons alone can
        # impose a large minimum width on the whole QMainWindow.
        controls_scroll = QtWidgets.QScrollArea()
        controls_scroll.setObjectName("layout_control_scroll")
        controls_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        controls_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        controls_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        controls_scroll.setWidgetResizable(False)
        controls_scroll.setFixedHeight(48)
        controls_widget = QtWidgets.QWidget()
        controls = QtWidgets.QHBoxLayout(controls_widget)
        controls.setSizeConstraint(QtWidgets.QLayout.SetMinAndMaxSize)
        controls.setContentsMargins(4, 0, 4, 0)
        controls.setSpacing(8)
        controls.addWidget(QtWidgets.QLabel("视图布局"))
        self.mode_combo = QtWidgets.QComboBox()
        for key, label in self.MODES:
            self.mode_combo.addItem(label, userData=key)
        self.mode_combo.setToolTip("自动分栏按可用宽度布局；双列过窄时可横向滚动；标签聚焦只显示当前面板")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        controls.addWidget(self.mode_combo)

        self.all_button = QtWidgets.QPushButton("全部")
        self.all_button.clicked.connect(lambda: self.select_views(range(len(self.views))))
        controls.addWidget(self.all_button)
        self.sensor_button = QtWidgets.QPushButton("传感器")
        self.sensor_button.clicked.connect(lambda: self.select_views([3, 4, 5]))
        controls.addWidget(self.sensor_button)
        self.calibration_button = QtWidgets.QPushButton("标定")
        self.calibration_button.clicked.connect(lambda: self.select_views([4, 5, 6, 7]))
        controls.addWidget(self.calibration_button)
        controls.addStretch(1)
        self.layout_info = QtWidgets.QLabel("")
        self.layout_info.setStyleSheet("color:#aab4be")
        controls.addWidget(self.layout_info)
        self.sidebar_button = QtWidgets.QPushButton("隐藏侧栏")
        self.sidebar_button.setToolTip("临时收起左右工具栏，扩大中央视图；再次点击恢复")
        self.sidebar_button.clicked.connect(self.sidebarToggleRequested.emit)
        controls.addWidget(self.sidebar_button)
        self.reset_button = QtWidgets.QPushButton("重置布局")
        self.reset_button.setToolTip("恢复自动分栏、默认窗口位置和左右工具栏")
        self.reset_button.clicked.connect(self.resetLayoutRequested.emit)
        controls.addWidget(self.reset_button)
        controls_widget.adjustSize()
        controls_scroll.setWidget(controls_widget)
        root.addWidget(controls_scroll)

        # View selection always scrolls horizontally instead of making the
        # main window grow wider than a laptop screen.
        self.view_strip = QtWidgets.QScrollArea()
        self.view_strip.setObjectName("view_selector_scroll")
        self.view_strip.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.view_strip.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.view_strip.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.view_strip.setWidgetResizable(False)
        self.view_strip.setFixedHeight(53)
        strip = QtWidgets.QWidget()
        strip_layout = QtWidgets.QHBoxLayout(strip)
        strip_layout.setSizeConstraint(QtWidgets.QLayout.SetMinAndMaxSize)
        strip_layout.setContentsMargins(2, 1, 2, 1)
        strip_layout.setSpacing(6)
        for index, (name, _) in enumerate(self.views):
            button = QtWidgets.QToolButton()
            button.setText(name)
            button.setCheckable(True)
            button.setChecked(True)
            button.setMinimumHeight(31)
            button.setMinimumWidth(99)
            button.setStyleSheet("""
                QToolButton { background:#333; color:#eee; border:1px solid #555;
                              border-radius:4px; padding:4px 8px; font-weight:600; }
                QToolButton:checked { background:#116aaf; border-color:#359be7; }
                QToolButton:hover:!checked { background:#454545; }
            """)
            button.toggled.connect(lambda value, i=index: self.set_view_visible(i, value))
            strip_layout.addWidget(button)
            self._toggle_buttons.append(button)
        strip.adjustSize()
        self.view_strip.setWidget(strip)
        root.addWidget(self.view_strip)

        self.grid_scroll = QtWidgets.QScrollArea()
        self.grid_scroll.setObjectName("view_grid_scroll")
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.grid_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.grid_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.grid_content = QtWidgets.QWidget()
        self.grid_layout = QtWidgets.QGridLayout(self.grid_content)
        self.grid_layout.setContentsMargins(4, 4, 4, 4)
        self.grid_layout.setHorizontalSpacing(6)
        self.grid_layout.setVerticalSpacing(8)
        self.grid_layout.setAlignment(QtCore.Qt.AlignTop)
        self.grid_scroll.setWidget(self.grid_content)
        root.addWidget(self.grid_scroll, 1)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("view_focus_tabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setTabPosition(QtWidgets.QTabWidget.North)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setVisible(False)
        root.addWidget(self.tabs, 1)

        self.empty_label = QtWidgets.QLabel("没有选中的视图。请从上方选择需要显示的面板。")
        self.empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet("color:#abb1b8; padding:16px")
        self.empty_label.hide()

        for index, (name, widget) in enumerate(self.views):
            tile = QtWidgets.QGroupBox(name)
            tile.setObjectName(f"view_tile_{index}")
            tile.setStyleSheet("""
                QGroupBox { font-weight:600; font-size:12px; border:1px solid #535b64;
                            border-radius:5px; margin-top:8px; padding-top:4px; }
                QGroupBox::title { subcontrol-origin:margin; left:8px;
                                   padding:0 4px; color:#d3dce3; }
            """)
            tile.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
            inner = QtWidgets.QVBoxLayout(tile)
            inner.setContentsMargins(5, 13, 5, 5)
            inner.setSpacing(0)
            inner.addWidget(widget)
            if index in (0, 1, 2, 3, 4, 5):
                tile.setMinimumHeight(300)
            else:
                tile.setMinimumHeight(345)
            self._tiles.append(tile)

        self._reflow_timer = QtCore.QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(75)
        self._reflow_timer.timeout.connect(self._maybe_reflow)
        self.grid_scroll.viewport().installEventFilter(self)
        self._rebuild()

    def eventFilter(self, obj, event):
        if obj is self.grid_scroll.viewport() and event.type() == QtCore.QEvent.Resize:
            self._reflow_timer.start()
        return super().eventFilter(obj, event)

    def set_sidebar_visible(self, visible):
        self.sidebar_button.setText("隐藏侧栏" if visible else "显示侧栏")

    def set_layout_mode(self, mode):
        idx = self.mode_combo.findData(mode)
        if idx < 0:
            raise ValueError(f"unknown layout mode {mode}")
        if idx != self.mode_combo.currentIndex():
            self.mode_combo.setCurrentIndex(idx)
        elif self.mode != mode:
            self._mode_changed(idx)

    def _mode_changed(self, _index):
        self.mode = self.mode_combo.currentData()
        self._rebuild()

    def set_view_visible(self, index, visible):
        self.visible_flags[index] = bool(visible)
        button = self._toggle_buttons[index]
        if button.isChecked() != bool(visible):
            button.blockSignals(True)
            button.setChecked(bool(visible))
            button.blockSignals(False)
        self._rebuild()

    def select_views(self, indices):
        chosen = set(indices)
        for index, button in enumerate(self._toggle_buttons):
            value = index in chosen
            self.visible_flags[index] = value
            button.blockSignals(True)
            button.setChecked(value)
            button.blockSignals(False)
        self._rebuild()

    def _maybe_reflow(self):
        if self.mode != "auto":
            return
        new_count = column_count(self.grid_scroll.viewport().width(),
                                 sum(self.visible_flags), self.mode)
        if new_count != self.column_count:
            self._rebuild()

    def _rebuild(self):
        # Save the focused view before moving the same persistent QWidget
        # between a grid and a QTabWidget. Never delete a view on reflow.
        focus_tile = self.tabs.currentWidget()
        for tile in self._tiles:
            tab_index = self.tabs.indexOf(tile)
            if tab_index != -1:
                self.tabs.removeTab(tab_index)
                tile.hide()
                # A tab page remains owned by its old QStackedWidget after
                # removeTab; detach it before adding it to the grid.
                tile.setParent(None)
            else:
                # Reflowing a grid must NOT unparent its OpenGL/camera view:
                # doing so unnecessarily recreates native window resources.
                self.grid_layout.removeWidget(tile)
                tile.hide()

        self.grid_layout.removeWidget(self.empty_label)
        self.empty_label.hide()
        self.empty_label.setParent(None)

        visible = [(index, self.views[index][0], self._tiles[index])
                   for index, enabled in enumerate(self.visible_flags) if enabled]
        count = len(visible)
        columns = column_count(self.grid_scroll.viewport().width(), count, self.mode)
        self.column_count = columns

        if self.mode == "tabs":
            self.grid_scroll.hide()
            self.tabs.show()
            for _, title, tile in visible:
                self.tabs.addTab(tile, title)
            if focus_tile is not None:
                focus_index = self.tabs.indexOf(focus_tile)
                if focus_index >= 0:
                    self.tabs.setCurrentIndex(focus_index)
            self.grid_content.setMinimumWidth(0)
            self.layout_info.setText(f"{count} 个视图 · 标签")
            return

        self.tabs.hide()
        self.grid_scroll.show()
        self.grid_content.setMinimumWidth(forced_minimum_width(columns, self.mode))
        for col in range(3):
            self.grid_layout.setColumnStretch(col, 1 if col < columns else 0)
        for position, (_, _title, tile) in enumerate(visible):
            self.grid_layout.addWidget(tile, position // columns, position % columns)
            tile.show()
        if not count:
            self.grid_layout.addWidget(self.empty_label, 0, 0)
            self.empty_label.show()
        self.layout_info.setText(f"{count} 个视图 · {columns} 列" if count else "无视图")
        # Qt adjusts to new tile size hints, without cramming existing panels
        # into a sqrt(N)-determined fixed column count.
        self.grid_content.updateGeometry()

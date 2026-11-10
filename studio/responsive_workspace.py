"""Material 3-inspired responsive workspace for E-Brush Studio.

Live camera, OpenGL and calibration widgets remain persistent while the user
switches between responsive grids and focused tabs.
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from layout_utils import column_count, forced_minimum_width


class ResponsiveWorkspace(QtWidgets.QWidget):
    sidebarToggleRequested = QtCore.Signal()
    resetLayoutRequested = QtCore.Signal()

    MODES = (
        ("auto", "\u81ea\u52a8\u5206\u680f"),
        ("two", "\u53cc\u5217\u5bf9\u7167"),
        ("one", "\u5355\u5217\u5bbd\u5c4f"),
        ("tabs", "\u6807\u7b7e\u805a\u7126"),
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
        root.setSpacing(9)

        controls_scroll = QtWidgets.QScrollArea()
        controls_scroll.setObjectName("layout_control_scroll")
        controls_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        controls_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        controls_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        controls_scroll.setWidgetResizable(False)
        controls_scroll.setFixedHeight(58)

        controls_widget = QtWidgets.QFrame()
        controls_widget.setObjectName("workspaceToolbar")
        controls = QtWidgets.QHBoxLayout(controls_widget)
        controls.setSizeConstraint(QtWidgets.QLayout.SetMinAndMaxSize)
        controls.setContentsMargins(12, 7, 12, 7)
        controls.setSpacing(8)

        heading = QtWidgets.QLabel("\u5de5\u4f5c\u533a\u89c6\u56fe")
        heading.setObjectName("sectionHeading")
        controls.addWidget(heading)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.setMinimumWidth(112)
        for key, label in self.MODES:
            self.mode_combo.addItem(label, userData=key)
        self.mode_combo.setToolTip("\u81ea\u52a8\u6a21\u5f0f\u4f1a\u6839\u636e\u53ef\u7528\u5bbd\u5ea6\u8c03\u6574\u5217\u6570")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        controls.addWidget(self.mode_combo)

        self.all_button = self._make_action("\u5168\u90e8", lambda: self.select_views(range(len(self.views))))
        self.sensor_button = self._make_action("\u4f20\u611f\u5668", lambda: self.select_views([3, 4, 5]))
        self.calibration_button = self._make_action("\u6807\u5b9a", lambda: self.select_views([4, 5, 6, 7]))
        controls.addWidget(self.all_button)
        controls.addWidget(self.sensor_button)
        controls.addWidget(self.calibration_button)
        controls.addStretch(1)

        self.layout_info = QtWidgets.QLabel("")
        self.layout_info.setObjectName("mutedLabel")
        controls.addWidget(self.layout_info)

        self.sidebar_button = self._make_action("\u9690\u85cf\u4fa7\u680f", self.sidebarToggleRequested.emit)
        self.sidebar_button.setToolTip("\u6536\u8d77\u5de6\u53f3\u5de5\u5177\u680f\uff0c\u4e3a\u4e2d\u592e\u89c6\u56fe\u7559\u51fa\u66f4\u591a\u7a7a\u95f4")
        self.reset_button = self._make_action("\u91cd\u7f6e\u5e03\u5c40", self.resetLayoutRequested.emit)
        self.reset_button.setToolTip("\u6062\u590d\u9ed8\u8ba4\u7a97\u53e3\u5e03\u5c40")
        controls.addWidget(self.sidebar_button)
        controls.addWidget(self.reset_button)
        controls_widget.adjustSize()
        controls_scroll.setWidget(controls_widget)
        root.addWidget(controls_scroll)

        self.view_strip = QtWidgets.QScrollArea()
        self.view_strip.setObjectName("view_selector_scroll")
        self.view_strip.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.view_strip.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.view_strip.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.view_strip.setWidgetResizable(False)
        self.view_strip.setFixedHeight(49)

        strip = QtWidgets.QFrame()
        strip.setObjectName("viewSelector")
        strip_layout = QtWidgets.QHBoxLayout(strip)
        strip_layout.setSizeConstraint(QtWidgets.QLayout.SetMinAndMaxSize)
        strip_layout.setContentsMargins(2, 1, 2, 1)
        strip_layout.setSpacing(7)
        for index, (name, _) in enumerate(self.views):
            button = QtWidgets.QToolButton()
            button.setText(name)
            button.setObjectName(f"viewFilter_{index}")
            button.setProperty("role", "filterChip")
            button.setCheckable(True)
            button.setChecked(True)
            button.setMinimumHeight(34)
            button.setMinimumWidth(96)
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
        self.grid_layout.setContentsMargins(5, 5, 5, 8)
        self.grid_layout.setHorizontalSpacing(12)
        self.grid_layout.setVerticalSpacing(12)
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

        self.empty_label = QtWidgets.QLabel("\u6ca1\u6709\u9009\u4e2d\u7684\u89c6\u56fe\u3002\u8bf7\u4ece\u4e0a\u65b9\u9009\u62e9\u9700\u8981\u663e\u793a\u7684\u9762\u677f\u3002")
        self.empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setObjectName("mutedLabel")
        self.empty_label.hide()

        for index, (name, widget) in enumerate(self.views):
            tile = QtWidgets.QFrame()
            tile.setObjectName("viewTile")
            tile.setProperty("viewIndex", index)
            tile.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)

            tile_layout = QtWidgets.QVBoxLayout(tile)
            tile_layout.setContentsMargins(11, 8, 11, 10)
            tile_layout.setSpacing(7)

            header = QtWidgets.QFrame()
            header.setObjectName("viewTileHeader")
            header_layout = QtWidgets.QHBoxLayout(header)
            header_layout.setContentsMargins(2, 1, 2, 1)
            header_layout.setSpacing(8)

            title = QtWidgets.QLabel(name)
            title.setObjectName("viewTileTitle")
            header_layout.addWidget(title)
            header_layout.addStretch(1)

            meta = QtWidgets.QLabel("\u5b9e\u65f6\u9762\u677f" if index < 6 else "\u6807\u5b9a\u5de5\u5177")
            meta.setObjectName("viewTileMeta")
            header_layout.addWidget(meta)
            tile_layout.addWidget(header)
            tile_layout.addWidget(widget, 1)

            tile.setMinimumHeight(315 if index < 6 else 350)
            self._tiles.append(tile)

        self._reflow_timer = QtCore.QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(75)
        self._reflow_timer.timeout.connect(self._maybe_reflow)
        self.grid_scroll.viewport().installEventFilter(self)
        self._rebuild()

    @staticmethod
    def _make_action(text, callback):
        button = QtWidgets.QPushButton(text)
        button.setProperty("role", "tonalAction")
        button.clicked.connect(callback)
        return button

    def eventFilter(self, obj, event):
        if obj is self.grid_scroll.viewport() and event.type() == QtCore.QEvent.Resize:
            self._reflow_timer.start()
        return super().eventFilter(obj, event)

    def set_sidebar_visible(self, visible):
        self.sidebar_button.setText("\u9690\u85cf\u4fa7\u680f" if visible else "\u663e\u793a\u4fa7\u680f")

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
        # Preserve the same live widget instances when changing grid/tab modes.
        focus_tile = self.tabs.currentWidget()
        for tile in self._tiles:
            tab_index = self.tabs.indexOf(tile)
            if tab_index != -1:
                self.tabs.removeTab(tab_index)
                tile.hide()
                tile.setParent(None)
            else:
                self.grid_layout.removeWidget(tile)
                tile.hide()

        self.grid_layout.removeWidget(self.empty_label)
        self.empty_label.hide()
        self.empty_label.setParent(None)

        visible = [(index, self.views[index][0], self._tiles[index])
                   for index, enabled in enumerate(self.visible_flags) if enabled]
        count = len(visible)
        columns = max(1, column_count(self.grid_scroll.viewport().width(), count, self.mode))
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
            self.layout_info.setText(f"{count} \u4e2a\u89c6\u56fe \u00b7 \u6807\u7b7e")
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
        self.layout_info.setText(f"{count} \u4e2a\u89c6\u56fe \u00b7 {columns} \u5217" if count else "\u65e0\u89c6\u56fe")
        self.grid_content.updateGeometry()

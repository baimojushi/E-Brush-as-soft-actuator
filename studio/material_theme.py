"""Material 3-inspired theme tokens for the E-Brush Studio Qt workspace.

The token roles follow the Material Web theming model (reference/system/component
roles), adapted to Qt Widgets. No browser runtime or external UI dependency is
required.
"""
from __future__ import annotations

from PySide6 import QtGui, QtWidgets


LIGHT = {
    "primary": "#006B5F",
    "on_primary": "#FFFFFF",
    "primary_container": "#9DF2E2",
    "on_primary_container": "#00201A",
    "secondary": "#4A635D",
    "on_secondary": "#FFFFFF",
    "secondary_container": "#CDE8DF",
    "on_secondary_container": "#06201B",
    "tertiary": "#416278",
    "tertiary_container": "#C4E7FF",
    "on_tertiary_container": "#001E2C",
    "surface": "#F7FAF8",
    "surface_dim": "#D9E3DE",
    "surface_container_lowest": "#FFFFFF",
    "surface_container_low": "#F1F4F2",
    "surface_container": "#EBF0ED",
    "surface_container_high": "#E5EAE7",
    "surface_container_highest": "#DFE5E1",
    "on_surface": "#191C1A",
    "on_surface_variant": "#404945",
    "outline": "#707A75",
    "outline_variant": "#C0C9C3",
    "error": "#BA1A1A",
    "success_container": "#D6F5E5",
    "on_success_container": "#063B27",
    "warning_container": "#F8E8C7",
    "on_warning_container": "#4A2C00",
    "plot_background": "#FFFFFF",
    "plot_grid": "#DCE4DF",
}

DARK = {
    "primary": "#7BDCC9",
    "on_primary": "#00382F",
    "primary_container": "#005044",
    "on_primary_container": "#9DF2E2",
    "secondary": "#B1CCC3",
    "on_secondary": "#1D352F",
    "secondary_container": "#334B44",
    "on_secondary_container": "#CDE8DF",
    "tertiary": "#A9CCE4",
    "tertiary_container": "#29495A",
    "on_tertiary_container": "#C4E7FF",
    "surface": "#101411",
    "surface_dim": "#101411",
    "surface_container_lowest": "#0B0F0D",
    "surface_container_low": "#171C19",
    "surface_container": "#1B211E",
    "surface_container_high": "#252B28",
    "surface_container_highest": "#303733",
    "on_surface": "#E1E4E1",
    "on_surface_variant": "#BFC9C2",
    "outline": "#89938D",
    "outline_variant": "#404944",
    "error": "#FFB4AB",
    "success_container": "#123D2C",
    "on_success_container": "#B7F2D1",
    "warning_container": "#4A3515",
    "on_warning_container": "#F8E8C7",
    "plot_background": "#171C19",
    "plot_grid": "#35403A",
}


def tokens(mode: str = "light") -> dict[str, str]:
    return dict(DARK if str(mode).lower() == "dark" else LIGHT)


def _stylesheet(c: dict[str, str]) -> str:
    return f"""
    * {{ font-family: 'Segoe UI Variable', 'Segoe UI', 'Microsoft YaHei UI', sans-serif; }}
    QWidget {{ color: {c['on_surface']}; font-size: 9pt; }}
    QMainWindow, QWidget#appCentral {{ background: {c['surface']}; }}
    QToolTip {{ background: {c['surface_container_high']}; color: {c['on_surface']};
               border: 1px solid {c['outline_variant']}; border-radius: 8px; padding: 6px 9px; }}

    QFrame#appBar {{ background: {c['surface_container_lowest']};
        border: 1px solid {c['outline_variant']}; border-radius: 20px; }}
    QLabel#brandMark {{ background: {c['primary_container']}; color: {c['on_primary_container']};
        border-radius: 16px; min-width: 42px; max-width: 42px; min-height: 42px; max-height: 42px;
        font-size: 16pt; font-weight: 800; qproperty-alignment: AlignCenter; }}
    QLabel#appTitle {{ font-size: 15pt; font-weight: 700; color: {c['on_surface']}; }}
    QLabel#appSubtitle {{ font-size: 8pt; color: {c['on_surface_variant']}; }}
    QLabel[role="statusChip"] {{ background: {c['surface_container']}; color: {c['on_surface_variant']};
        border: 1px solid {c['outline_variant']}; border-radius: 14px; padding: 5px 10px;
        font-size: 8pt; font-weight: 600; }}
    QLabel[role="statusChip"][state="success"] {{ background: {c['success_container']};
        color: {c['on_success_container']}; border-color: transparent; }}
    QLabel[role="statusChip"][state="warning"] {{ background: {c['warning_container']};
        color: {c['on_warning_container']}; border-color: transparent; }}
    QLabel[role="statusChip"][state="error"] {{ background: {c['surface_container_high']};
        color: {c['error']}; border-color: {c['error']}; }}
    QLabel[role="statusChip"][state="info"] {{ background: {c['primary_container']};
        color: {c['on_primary_container']}; border-color: transparent; }}

    QPushButton {{ background: {c['surface_container_high']}; color: {c['on_surface']};
        border: 1px solid {c['outline_variant']}; border-radius: 18px;
        padding: 7px 14px; min-height: 22px; font-weight: 600; }}
    QPushButton:hover {{ background: {c['surface_container_highest']}; border-color: {c['outline']}; }}
    QPushButton:pressed {{ background: {c['secondary_container']}; }}
    QPushButton:disabled {{ color: {c['outline']}; background: {c['surface_container']}; }}
    QPushButton[primary="true"] {{ background: {c['primary']}; color: {c['on_primary']};
        border: 1px solid {c['primary']}; }}
    QPushButton[primary="true"]:hover {{ background: {c['secondary']}; }}
    QPushButton[role="iconButton"] {{ border-radius: 18px; padding: 7px 12px;
        background: {c['surface_container']}; }}

    QToolButton {{ background: transparent; color: {c['on_surface_variant']};
        border: 1px solid transparent; border-radius: 16px; padding: 6px 11px; font-weight: 600; }}
    QToolButton:hover {{ background: {c['surface_container_high']}; }}
    QToolButton[role="filterChip"] {{ background: {c['surface_container']};
        border: 1px solid {c['outline_variant']}; border-radius: 17px; padding: 7px 13px; }}
    QToolButton[role="filterChip"]:checked {{ background: {c['primary_container']};
        color: {c['on_primary_container']}; border-color: {c['primary']}; }}
    QToolButton[role="filterChip"]:hover:!checked {{ background: {c['surface_container_highest']}; }}

    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {c['surface_container_highest']}; color: {c['on_surface']};
        border: 1px solid {c['outline_variant']}; border-radius: 10px; padding: 7px 9px;
        selection-background-color: {c['primary']}; selection-color: {c['on_primary']}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QComboBox:focus {{ border: 2px solid {c['primary']}; padding: 6px 8px; }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {c['outline']}; }}
    QComboBox::drop-down {{ border: 0; width: 24px; }}
    QComboBox QAbstractItemView {{ background: {c['surface_container_high']}; color: {c['on_surface']};
        border: 1px solid {c['outline_variant']}; selection-background-color: {c['primary_container']};
        selection-color: {c['on_primary_container']}; outline: 0; padding: 4px; }}

    QCheckBox {{ spacing: 8px; color: {c['on_surface']}; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px;
        border: 1px solid {c['outline']}; background: {c['surface_container_lowest']}; }}
    QCheckBox::indicator:checked {{ background: {c['primary']}; border-color: {c['primary']}; }}
    QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {c['outline_variant']}; }}
    QSlider::sub-page:horizontal {{ background: {c['primary']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ width: 18px; height: 18px; margin: -7px 0;
        border-radius: 9px; background: {c['primary']}; border: 2px solid {c['surface_container_lowest']}; }}

    QGroupBox {{ background: {c['surface_container_lowest']}; color: {c['on_surface']};
        border: 1px solid {c['outline_variant']}; border-radius: 14px; margin-top: 14px;
        padding: 12px 10px 10px; font-weight: 650; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px;
        color: {c['on_surface_variant']}; }}
    QDockWidget {{ color: {c['on_surface']}; font-weight: 700; }}
    QDockWidget::title {{ background: {c['surface_container']}; padding: 10px 12px;
        border-bottom: 1px solid {c['outline_variant']}; text-align: left; }}
    QDockWidget::close-button, QDockWidget::float-button {{ border: 0; padding: 3px; }}

    QFrame#workspaceToolbar {{ background: {c['surface_container_lowest']};
        border: 1px solid {c['outline_variant']}; border-radius: 16px; }}
    QFrame#viewSelector {{ background: transparent; border: 0; }}
    QFrame#viewTile {{ background: {c['surface_container_lowest']};
        border: 1px solid {c['outline_variant']}; border-radius: 16px; }}
    QFrame#viewTileHeader {{ background: transparent; border: 0; }}
    QLabel#viewTileTitle {{ font-size: 10pt; font-weight: 700; color: {c['on_surface']}; }}
    QLabel#viewTileMeta {{ font-size: 7pt; font-weight: 700; letter-spacing: 1px;
        color: {c['on_surface_variant']}; background: {c['surface_container']};
        border-radius: 9px; padding: 3px 7px; }}
    QLabel#sectionHeading {{ font-size: 10pt; font-weight: 700; }}
    QLabel#mutedLabel {{ color: {c['on_surface_variant']}; }}
    QLabel#panelHeadline {{ font-size: 15pt; font-weight: 750; color: {c['primary']}; }}
    QLabel#definitionCard {{ background: {c['surface_container']}; color: {c['on_surface']};
        border: 1px solid {c['outline_variant']}; border-radius: 12px; padding: 10px; }}
    QLabel#statusText[state="success"] {{ color: {c['primary']}; font-weight: 700; }}
    QLabel#statusText[state="warning"] {{ color: {c['on_warning_container']}; font-weight: 700; }}
    QLabel#statusText[state="error"] {{ color: {c['error']}; font-weight: 700; }}

    QTabWidget::pane {{ background: {c['surface_container_lowest']};
        border: 1px solid {c['outline_variant']}; border-radius: 14px; top: -1px; }}
    QTabBar::tab {{ background: {c['surface_container']}; color: {c['on_surface_variant']};
        border: 0; border-radius: 12px; padding: 8px 14px; margin: 3px; }}
    QTabBar::tab:selected {{ background: {c['primary_container']};
        color: {c['on_primary_container']}; }}
    QScrollArea {{ background: transparent; border: 0; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c['outline_variant']}; min-height: 28px; border-radius: 5px; }}
    QScrollBar::handle:vertical:hover {{ background: {c['outline']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {c['outline_variant']}; min-width: 28px; border-radius: 5px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QStatusBar {{ background: {c['surface_container']}; color: {c['on_surface_variant']};
        border-top: 1px solid {c['outline_variant']}; }}
    """


def apply_material_theme(app: QtWidgets.QApplication, mode: str = "light") -> dict[str, str]:
    """Apply a Material 3 tonal palette to Qt Widgets and return its tokens."""
    c = tokens(mode)
    palette = QtGui.QPalette()
    role = QtGui.QPalette
    palette.setColor(role.Window, QtGui.QColor(c["surface"]))
    palette.setColor(role.WindowText, QtGui.QColor(c["on_surface"]))
    palette.setColor(role.Base, QtGui.QColor(c["surface_container_lowest"]))
    palette.setColor(role.AlternateBase, QtGui.QColor(c["surface_container_low"]))
    palette.setColor(role.ToolTipBase, QtGui.QColor(c["surface_container_high"]))
    palette.setColor(role.ToolTipText, QtGui.QColor(c["on_surface"]))
    palette.setColor(role.Text, QtGui.QColor(c["on_surface"]))
    palette.setColor(role.Button, QtGui.QColor(c["surface_container_high"]))
    palette.setColor(role.ButtonText, QtGui.QColor(c["on_surface"]))
    palette.setColor(role.BrightText, QtGui.QColor(c["error"]))
    palette.setColor(role.Highlight, QtGui.QColor(c["primary"]))
    palette.setColor(role.HighlightedText, QtGui.QColor(c["on_primary"]))
    palette.setColor(role.Link, QtGui.QColor(c["primary"]))
    palette.setColor(role.PlaceholderText, QtGui.QColor(c["outline"]))
    disabled = QtGui.QColor(c["outline"])
    palette.setColor(role.Disabled, role.Text, disabled)
    palette.setColor(role.Disabled, role.ButtonText, disabled)
    app.setPalette(palette)
    app.setStyleSheet(_stylesheet(c))
    return c

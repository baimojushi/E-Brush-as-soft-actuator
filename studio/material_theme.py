"""Material Design 3 inspired semantic tokens adapted natively to Qt/QSS.

Not a dependency on Material Web. Desktop controls remain native PySide6.
"""
from __future__ import annotations

PALETTES = {
    "light": {
        "primary": "#126560", "on_primary": "#FFFFFF", "primary_container": "#B5EDE3",
        "on_primary_container": "#003733", "surface": "#FBFDFB", "surface_low": "#F3F8F5",
        "surface_high": "#E7EFEB", "text": "#192C28", "muted": "#566862", "border": "#BAC9C3",
        "outline": "#7D938C", "error": "#AB353A", "success": "#16734E",
        "warning": "#986400", "on_surface": "#192C28",
    },
    "dark": {
        "primary": "#8BDBD0", "on_primary": "#003732", "primary_container": "#15554F",
        "on_primary_container": "#B5EDE3", "surface": "#0F1716", "surface_low": "#1A2522",
        "surface_high": "#283632", "text": "#DDE7E1", "muted": "#A9BAB3", "border": "#3D5049",
        "outline": "#879C93", "error": "#FFB4AB", "success": "#9FE3B9",
        "warning": "#F1C879", "on_surface": "#DDE7E1",
    },
}


def style_sheet(mode="light"):
    c = PALETTES[mode]
    return f"""
    QMainWindow, QWidget {{ background-color: {c['surface']}; color: {c['text']}; }}
    QLabel {{ background-color: transparent; }}
    QLabel#studio_brand {{ font-size: 18px; font-weight: 800; color: {c['primary']}; }}
    QLabel#studio_subtitle, QLabel#studio_caption, QLabel#caption {{ color: {c['muted']}; font-size: 11px; }}
    QLabel#section_heading {{ font-size: 17px; font-weight: 700; }}
    QLabel#metric_value {{ font-size: 24px; font-weight: 700; color: {c['primary']}; }}
    QLabel#quality_good {{ color: {c['success']}; font-weight: 700; }}
    QLabel#quality_warning {{ color: {c['warning']}; font-weight: 700; }}
    QLabel#quality_waiting {{ color: {c['muted']}; font-weight: 700; }}
    QWidget#studio_header, QWidget#studio_navigation, QWidget#metric_card {{
        background: {c['surface_low']}; border: 1px solid {c['border']}; border-radius: 14px;
    }}
    QWidget#metric_card QLabel {{ background: transparent; }}
    QGroupBox {{ background: {c['surface_low']}; border: 1px solid {c['border']};
                 border-radius: 14px; margin-top: 12px; padding-top: 8px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 2px 6px; color: {c['muted']}; }}
    QFrame {{ border: 0px; }}
    QPushButton, QToolButton {{ background: {c['surface_high']}; color: {c['text']};
        padding: 7px 12px; border: 1px solid {c['border']}; border-radius: 10px; font-weight: 600; }}
    QPushButton:hover, QToolButton:hover {{ border-color: {c['primary']}; background: {c['primary_container']}; color: {c['on_primary_container']}; }}
    QPushButton:pressed, QToolButton:pressed, QToolButton:checked {{
        background: {c['primary_container']}; color: {c['on_primary_container']}; border-color: {c['primary']}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; border-color: {c['border']}; }}
    QPushButton#primary_action {{ background: {c['primary']}; color: {c['on_primary']}; border: none; }}
    QPushButton#primary_action:hover {{ background: {c['primary_container']}; color: {c['on_primary_container']}; }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
        background: {c['surface_low']}; color: {c['text']}; border: 1px solid {c['border']};
        border-radius: 9px; padding: 5px 8px; selection-background-color: {c['primary']};
        selection-color: {c['on_primary']}; }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
    QPlainTextEdit:focus {{ border-color: {c['primary']}; }}
    QComboBox QAbstractItemView {{ background: {c['surface_low']}; color: {c['text']}; }}
    QDockWidget {{ color: {c['text']}; font-weight: 600; }}
    QDockWidget::title {{ background: {c['surface_high']}; padding: 8px; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ border: none; background: transparent; }}
    QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 12px; }}
    QTabBar::tab {{ padding: 7px 13px; background: {c['surface_high']}; border: 0;
                     color: {c['muted']}; margin-right: 3px; border-radius: 8px; }}
    QTabBar::tab:selected {{ background: {c['primary_container']}; color: {c['on_primary_container']}; }}
    QProgressBar {{ background: {c['surface_high']}; color: {c['text']}; border: none;
                    border-radius: 6px; height: 12px; text-align: center; }}
    QProgressBar::chunk {{ background: {c['primary']}; border-radius: 6px; }}
    QScrollBar:vertical {{ width: 11px; background: transparent; margin: 1px; }}
    QScrollBar::handle:vertical {{ background: {c['outline']}; border-radius: 5px; min-height: 20px; }}
    QScrollBar:horizontal {{ height: 11px; background: transparent; margin: 1px; }}
    QScrollBar::handle:horizontal {{ background: {c['outline']}; border-radius: 5px; min-width: 20px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
    QSlider::groove:horizontal {{ height: 5px; border-radius: 2px; background: {c['border']}; }}
    QSlider::handle:horizontal {{ background: {c['primary']}; border: 0px; border-radius: 8px;
                                   width: 15px; margin: -5px 0px; }}
    QStatusBar {{ background: {c['surface_low']}; color: {c['muted']}; }}
    """


def apply_theme(app, mode):
    from PySide6 import QtGui
    if mode not in PALETTES:
        raise ValueError(mode)
    c = PALETTES[mode]
    pal = QtGui.QPalette()
    roles = (
        (QtGui.QPalette.Window, "surface"), (QtGui.QPalette.WindowText, "text"),
        (QtGui.QPalette.Base, "surface_low"), (QtGui.QPalette.AlternateBase, "surface_high"),
        (QtGui.QPalette.Text, "text"), (QtGui.QPalette.Button, "surface_high"),
        (QtGui.QPalette.ButtonText, "text"), (QtGui.QPalette.Highlight, "primary"),
        (QtGui.QPalette.HighlightedText, "on_primary"),
        (QtGui.QPalette.ToolTipBase, "surface_high"), (QtGui.QPalette.ToolTipText, "text"),
    )
    for role, key in roles:
        pal.setColor(role, QtGui.QColor(c[key]))
    app.setPalette(pal)
    app.setStyleSheet(style_sheet(mode))

"""편집 작업을 위한 고대비 다크 테마."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

BG = "#101318"
PANEL = "#171c24"
PANEL2 = "#222a36"
BORDER = "#303a4a"
TEXT = "#edf1f7"
MUTED = "#9aa7bb"
ACCENT = "#a2b6ff"
ACCENT2 = "#edba8c"
OK = "#4cd08b"
WARN = "#f2b84b"
ERR = "#ff5d6c"

C = {
    k: QColor(v)
    for k, v in dict(
        BG=BG,
        PANEL=PANEL,
        PANEL2=PANEL2,
        BORDER=BORDER,
        TEXT=TEXT,
        MUTED=MUTED,
        ACCENT=ACCENT,
        ACCENT2=ACCENT2,
        OK=OK,
        WARN=WARN,
        ERR=ERR,
    ).items()
}

STYLE = f"""
* {{ font-family: 'Malgun Gothic'; font-size: 12px; color: {TEXT}; }}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QWidget {{ background: transparent; }}
QDialog, QMessageBox {{ background: {BG}; }}
QFrame#panel, QGroupBox {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}
QGroupBox {{ margin-top: 14px; padding: 10px 8px 8px 8px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {MUTED}; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#h1 {{ font-size: 16px; font-weight: 700; }}
QLabel#warn {{ color: {WARN}; }}
QLabel#err {{ color: {ERR}; }}
QLabel#ok {{ color: {OK}; }}
QPushButton {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 5px; padding: 5px 12px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: #5b6171; border-color: #2a2e39; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: #08121f; font-weight: 700; }}
QPushButton#primary:hover {{ background: #6cb0ff; }}
QPushButton#primary:disabled {{ background: #2b4a73; color: #7f93ad; }}
QPushButton#danger {{ border-color: {ERR}; color: {ERR}; }}
QPushButton:checked {{ background: {ACCENT}; color: #08121f; border-color: {ACCENT}; }}
QToolButton {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 5px; padding: 4px 8px; }}
QToolButton:hover {{ border-color: {ACCENT}; }}
QToolButton:checked {{ background: {ACCENT}; color: #08121f; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
  background: #12141a; border: 1px solid {BORDER}; border-radius: 4px; padding: 4px 6px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; selection-background-color: {ACCENT}; }}
QListWidget, QTreeWidget, QTableWidget, QTableView {{ background: #12141a; border: 1px solid {BORDER}; border-radius: 4px;
  alternate-background-color: #171a21; gridline-color: {BORDER}; }}
QListWidget::item:selected, QTableWidget::item:selected, QTreeWidget::item:selected {{ background: #264a7a; }}
QHeaderView::section {{ background: {PANEL2}; border: none; border-right: 1px solid {BORDER}; padding: 4px 6px; color: {MUTED}; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; background: {ACCENT}; border-radius: 6px; }}
QProgressBar {{ background: #12141a; border: 1px solid {BORDER}; border-radius: 4px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QScrollBar:horizontal {{ background: {PANEL}; height: 12px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 24px; }}
QScrollBar:vertical {{ background: {PANEL}; width: 12px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSplitter::handle {{ background: {BG}; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background: #264a7a; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; }}
QTabBar::tab {{ background: {PANEL2}; padding: 6px 14px; border: 1px solid {BORDER}; }}
QTabBar::tab:selected {{ background: {ACCENT}; color: #08121f; }}
QPushButton#nav {{ text-align: left; padding: 12px 14px; border-radius: 6px; font-weight: 700; font-size: 13px; background: {PANEL}; }}
QPushButton#nav:checked {{ background: {ACCENT}; color: #08121f; }}
"""

STYLE += f"""
QFrame#topbar {{ background: #161b24; border-bottom: 1px solid {BORDER}; }}
QFrame#sidebar {{ background: #141922; border-right: 1px solid {BORDER}; }}
QFrame#workspaceCard {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#timelineToolbar {{ background: {PANEL}; border: none; border-bottom: 1px solid {BORDER}; }}
QWidget#transport {{ background: {PANEL}; border-top: 1px solid {BORDER}; }}
QWidget#inspector {{ background: {PANEL}; }}
QLabel#h1 {{ font-size: 18px; font-weight: 700; }}
QLabel#eyebrow {{ color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
QLabel#hero {{ font-size: 29px; font-weight: 700; }}
QLabel#badge {{ background: #26314b; color: #c6d1ff; border: 1px solid #3c4d71; border-radius: 5px; padding: 4px 8px; }}
QLabel#sourceMeta {{ color: {MUTED}; font-size: 11px; }}
QPushButton {{ padding: 6px 10px; min-height: 20px; border-radius: 7px; background: {PANEL2}; }}
QPushButton:hover {{ background: #2c3748; border-color: #6c7d9a; }}
QPushButton:focus {{ border: 1px solid {ACCENT}; }}
QPushButton:checked {{ background: #344565; color: #e2e8ff; border-color: #839be8; }}
QPushButton#primary {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #b7c5ff,stop:1 #8b9df1); color: #121a32; padding: 8px 18px; border: 1px solid #b5c5ff; }}
QPushButton#primary:hover {{ background: #c5d0ff; }}
QPushButton#play {{ background: #a2b6ff; border-color: #b5c5ff; padding: 4px; }}
QPushButton#primary:disabled {{ background: #34415e; border-color: #3d4c6e; color: #91a0c2; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {MUTED}; }}
QPushButton#ghost:hover {{ background: {PANEL2}; border-color: {BORDER}; color: {TEXT}; }}
QPushButton#compact {{ padding: 3px 6px; min-height: 20px; }}
QPushButton#nav {{ background: transparent; border: 1px solid transparent; border-radius: 9px; padding: 0; }}
QPushButton#nav:hover {{ background: #202938; border-color: {BORDER}; }}
QPushButton#nav:checked {{ background: #27344c; border-color: #6e86c9; }}
QToolButton {{ min-height: 24px; padding: 5px 9px; border-radius: 7px; }}
QGroupBox {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; margin-top: 18px; padding: 12px 10px 10px; }}
QGroupBox::title {{ left: 12px; padding: 0 5px; color: #b8c4d7; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{ background: #10151e; border-radius: 6px; padding: 6px 8px; }}
QLineEdit {{ min-height: 19px; }}
QLineEdit#timecode {{ font-family: 'Consolas'; color: #dce5ff; padding: 5px 4px; }}
QRadioButton, QCheckBox {{ spacing: 6px; }}
QListWidget, QTableWidget {{ background: #111720; border-radius: 9px; alternate-background-color: #19212d; }}
QTableWidget::item {{ padding: 7px; }}
QHeaderView::section {{ background: #1d2634; padding: 9px 8px; color: #b3bfd1; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ width: 8px; background: transparent; }}
QScrollBar:horizontal {{ height: 9px; background: transparent; }}
QScrollBar::handle {{ background: #45536c; border-radius: 4px; }}
QSplitter::handle {{ background: {BG}; width: 7px; height: 7px; }}
QSplitter::handle:hover {{ background: #445677; }}
QMenu {{ padding: 6px; border-radius: 8px; }}
QMenu::item {{ padding: 8px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: #344565; }}
"""


def apply(app) -> None:
    """기본 팝업과 비활성 컨트롤까지 같은 다크 팔레트를 사용한다."""
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, BG),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, "#10151e"),
        (QPalette.ColorRole.AlternateBase, PANEL),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.Button, PANEL2),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.Highlight, "#344565"),
        (QPalette.ColorRole.HighlightedText, TEXT),
        (QPalette.ColorRole.ToolTipBase, PANEL2),
        (QPalette.ColorRole.ToolTipText, TEXT),
    ):
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(MUTED))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(MUTED))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)

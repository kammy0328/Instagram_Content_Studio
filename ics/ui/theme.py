"""다크 테마 (HiDPI 대응은 app.py 에서 설정)."""

from PySide6.QtGui import QColor

BG = "#15171c"
PANEL = "#1d2026"
PANEL2 = "#252932"
BORDER = "#323744"
TEXT = "#e6e8ee"
MUTED = "#8b92a3"
ACCENT = "#4c9dff"
ACCENT2 = "#ff7a59"
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
* {{ font-family: 'Malgun Gothic','Segoe UI','Noto Sans CJK KR',sans-serif; font-size: 12px; color: {TEXT}; }}
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

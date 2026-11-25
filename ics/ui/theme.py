"""편집 작업을 위한 블랙 다크 테마.

원칙
- 글꼴: Pretendard 하나 (번들). 크기는 11 / 12 / 13 / 15px 네 단계만 쓴다.
- 회색은 단계를 줄여 TEXT(본문) / MUTED(보조) / DIM(비활성·3차) 세 가지.
- 색에는 뜻이 있다: STORY 살구색, REELS 초록, 재생 헤드 빨강, 선택은 흰색. 그 밖의 장식 색은 쓰지 않는다.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtGui import QColor, QPalette

# ---- 바탕 (어두운 순)
BG = "#09090a"
PANEL = "#111113"
PANEL2 = "#18181b"
HOVER = "#202024"
BORDER = "#26262a"
LINE = "#1c1c1f"  # 아주 옅은 구분선
# ---- 글자
TEXT = "#ececee"
MUTED = "#8e8e95"
DIM = "#55555b"
# ---- 뜻이 있는 색
ACCENT = "#f2f2f3"  # 선택 / 주 버튼
ACCENT2 = "#edba8c"  # STORY
OK = "#4cd08b"  # REELS / 정상
WARN = "#f2b84b"
ERR = "#ff5d6c"
PLAYHEAD = "#ff4d5e"

TAB_COLORS = {"story": ACCENT2, "reels": OK, "feed": "#dcdce0", "credits": "#b9b9c0"}

FONT = "Pretendard"
FONT_FILES = ("Pretendard-Regular.ttf", "Pretendard-Medium.ttf", "Pretendard-SemiBold.ttf", "Pretendard-Bold.ttf")
# 글자 크기 (px)
FS_SMALL, FS_BODY, FS_EM, FS_H = 11, 12, 13, 15

C = {
    k: QColor(v)
    for k, v in dict(
        BG=BG,
        PANEL=PANEL,
        PANEL2=PANEL2,
        HOVER=HOVER,
        BORDER=BORDER,
        LINE=LINE,
        TEXT=TEXT,
        MUTED=MUTED,
        DIM=DIM,
        ACCENT=ACCENT,
        ACCENT2=ACCENT2,
        OK=OK,
        WARN=WARN,
        ERR=ERR,
        PLAYHEAD=PLAYHEAD,
    ).items()
}

STYLE = f"""
* {{ font-family: '{FONT}'; font-size: {FS_BODY}px; color: {TEXT}; }}
QMainWindow, QDialog, QWidget#root, QWidget#dialogRoot {{ background: {BG}; }}
QWidget {{ background: transparent; }}
QDialog, QMessageBox {{ background: {BG}; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px 8px; border-radius: 6px; }}

/* ---- 글자 */
QLabel#muted, QLabel#status, QLabel#sourceMeta {{ color: {MUTED}; }}
QLabel#status, QLabel#sourceMeta {{ font-size: {FS_SMALL}px; }}
QLabel#hint {{ color: {MUTED}; font-size: {FS_SMALL}px; }}
QLabel#h1 {{ font-size: {FS_H}px; font-weight: 700; }}
QLabel#cardTitle {{ font-size: {FS_EM}px; font-weight: 600; padding-bottom: 2px; }}
QLabel#title {{ font-size: {FS_EM}px; font-weight: 600; }}
QLabel#hero {{ font-size: 26px; font-weight: 700; }}
QLabel#eyebrow {{ color: {MUTED}; font-size: {FS_SMALL}px; font-weight: 600; letter-spacing: 1px; }}
QLabel#subhead {{ color: {TEXT}; font-weight: 600; padding-top: 6px; }}
QLabel#fieldLabel {{ color: {MUTED}; min-width: 56px; }}
QLabel#warn {{ color: {WARN}; }}
QLabel#err {{ color: {ERR}; }}
QLabel#ok {{ color: {OK}; }}
QLabel#badge {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 5px; padding: 3px 7px;
    font-size: {FS_SMALL}px; }}
QLabel#help {{ padding: 0 2px; }}

/* ---- 버튼: 보조(기본) / 주 / 고스트 / 위험 */
QPushButton {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 7px; padding: 6px 12px; min-height: 18px; }}
QPushButton:hover {{ background: {HOVER}; border-color: #36363c; }}
QPushButton:pressed {{ background: #27272c; }}
QPushButton:focus {{ border-color: #4a4a52; }}
QPushButton:checked {{ background: #2a2a2f; color: #ffffff; border-color: #55555c; }}
QPushButton:disabled {{ color: {DIM}; background: {PANEL}; border-color: {LINE}; }}
QPushButton#primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: #0a0a0a; font-weight: 600; padding: 6px 16px; }}
QPushButton#primary:hover {{ background: #ffffff; border-color: #ffffff; }}
QPushButton#primary:pressed {{ background: #d6d6d8; }}
QPushButton#primary:disabled {{ background: #2c2c30; border-color: #2c2c30; color: {MUTED}; }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {MUTED}; }}
QPushButton#ghost:hover {{ background: {HOVER}; color: {TEXT}; }}
QPushButton#danger {{ color: {ERR}; border-color: #4a2329; }}
QPushButton#danger:hover {{ background: #2a1417; border-color: {ERR}; }}
QPushButton#compact {{ padding: 4px 9px; min-height: 18px; }}
QPushButton#compact:checked {{ background: #2a2a2f; color: #ffffff; border-color: #55555c; }}
QPushButton#icon {{ background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 0; min-height: 0; }}
QPushButton#icon:hover {{ background: {HOVER}; }}
QPushButton#icon:pressed {{ background: #27272c; }}
QPushButton#icon:checked {{ background: #232327; border-color: transparent; }}
QPushButton#icon:disabled {{ background: transparent; }}
QPushButton#play {{ background: {ACCENT}; border: 1px solid {ACCENT}; border-radius: 17px; padding: 0; min-height: 0; }}
QPushButton#play:hover {{ background: #ffffff; }}
QPushButton#play:pressed {{ background: #d6d6d8; }}
QPushButton#link {{ background: transparent; border: none; color: {MUTED}; padding: 2px 4px; min-height: 0; }}
QPushButton#link:hover {{ color: {TEXT}; }}
QPushButton#link:disabled {{ color: {DIM}; }}
QPushButton#iconToggle {{ background: transparent; border: 1px solid transparent; padding: 3px; }}
QPushButton#iconToggle:hover {{ background: {HOVER}; }}
QPushButton#iconToggle:checked {{ background: transparent; border-color: transparent; }}
QPushButton#chip {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 0; min-height: 18px;
    font-size: {FS_SMALL}px; }}
QPushButton#chip:hover {{ border-color: #3c3c42; background: {HOVER}; }}
QPushButton#chip:checked {{ background: {ACCENT}; color: #0a0a0a; border-color: {ACCENT}; font-weight: 600; }}
QPushButton#chip:disabled {{ color: {DIM}; border-color: {LINE}; background: transparent; }}
QToolButton {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 7px; padding: 5px 10px; min-height: 18px; }}
QToolButton:hover {{ background: {HOVER}; border-color: #36363c; }}
QToolButton:checked {{ background: #2a2a2f; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#ghostTool {{ background: transparent; border: 1px solid transparent; color: {MUTED}; padding: 4px; }}
QToolButton#ghostTool:hover {{ background: {HOVER}; color: {TEXT}; }}

/* ---- 세그먼트 (Fit/Fill 등) */
QFrame#segbarSmall {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}
QPushButton#segSmall {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px 0;
    min-height: 16px; color: {MUTED}; font-weight: 600; }}
QPushButton#segSmall:hover {{ color: {TEXT}; }}
QPushButton#segSmall:checked {{ background: #28282d; color: #ffffff; border-color: #38383e; }}

/* ---- 입력 */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
  background: #0d0d0f; border: 1px solid {BORDER}; border-radius: 7px; padding: 5px 8px;
  selection-background-color: #3a3a40; }}
QLineEdit {{ min-height: 18px; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: #34343a; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus {{ border-color: #6a6a72; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {PANEL2}; border: 1px solid {BORDER}; selection-background-color: #2c2c31;
    outline: none; padding: 4px; }}
QLineEdit#timecode {{ background: transparent; border: 1px solid transparent; font-family: 'Consolas';
    font-size: {FS_H}px; font-weight: 600; color: #f4f4f5; padding: 2px 2px; }}
QLineEdit#tcSmall {{ font-family: 'Consolas'; }}
QLineEdit#timecode:hover {{ background: {PANEL2}; border-color: {BORDER}; }}
QLineEdit#timecode:focus {{ background: #0d0d0f; border-color: #6a6a72; }}

/* ---- 목록 / 표 */
QListWidget, QTreeWidget, QTableWidget, QTableView {{ background: #0d0d0f; border: 1px solid {BORDER}; border-radius: 9px;
  alternate-background-color: #121214; gridline-color: {LINE}; outline: none; }}
QListWidget#cutStrip {{ background: transparent; border: none; border-radius: 0; }}
QListWidget::item:selected, QTableWidget::item:selected, QTreeWidget::item:selected {{ background: #26262b; color: #ffffff; }}
QListWidget::item:hover, QTableWidget::item:hover {{ background: #18181b; }}
QTableWidget::item {{ padding: 7px; }}
QHeaderView::section {{ background: {PANEL}; border: none; border-bottom: 1px solid {BORDER}; padding: 8px 8px;
    color: {MUTED}; font-size: {FS_SMALL}px; font-weight: 600; }}

/* ---- 묶음 */
QFrame#panel, QGroupBox {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QGroupBox {{ margin-top: 18px; padding: 12px 10px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; color: {MUTED}; }}
QGroupBox#flat {{ background: transparent; border: none; margin: 0; padding: 0; }}
QGroupBox#flat::title {{ height: 0; padding: 0; margin: 0; }}

/* ---- 슬라이더 / 진행 막대 / 체크 */
QSlider::groove:horizontal {{ height: 3px; background: #2a2a2f; border-radius: 1px; }}
QSlider::sub-page:horizontal {{ background: #8a8a92; border-radius: 1px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; }}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
QSlider::handle:horizontal:disabled {{ background: {DIM}; }}
QSlider::sub-page:horizontal:disabled {{ background: #333338; }}
QProgressBar {{ background: #0d0d0f; border: 1px solid {BORDER}; border-radius: 4px; text-align: center; height: 8px;
    font-size: {FS_SMALL}px; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QRadioButton, QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid #55555c; border-radius: 4px; background: transparent; }}
QCheckBox::indicator:hover {{ border-color: #9a9aa2; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox::indicator:disabled {{ border-color: #333338; }}
QCheckBox::indicator:checked:disabled {{ background: {DIM}; border-color: {DIM}; }}

/* ---- 스크롤바: 가늘게, 손잡이만 */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ width: 8px; background: transparent; margin: 2px; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; margin: 2px; }}
QScrollBar::handle {{ background: #333338; border-radius: 2px; min-width: 28px; min-height: 28px; }}
QScrollBar::handle:hover {{ background: #4a4a50; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: {BG}; width: 6px; height: 6px; }}
QSplitter::handle:hover {{ background: #2e2e33; }}

/* ---- 메뉴 */
QMenu {{ background: {PANEL2}; border: 1px solid {BORDER}; padding: 5px; border-radius: 9px; }}
QMenu::item {{ padding: 7px 22px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: #28282d; }}
QMenu::item:disabled {{ color: {DIM}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; }}
QTabBar::tab {{ background: transparent; padding: 7px 14px; border: none; color: {MUTED}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}

/* ---- 편집 창 구역 */
QFrame#topbar {{ background: {BG}; border-bottom: 1px solid {LINE}; }}
QFrame#workspaceCard {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame#bottomCard {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame#timelineToolbar {{ background: transparent; border: none; border-bottom: 1px solid {LINE}; }}
QWidget#transport {{ background: transparent; border-top: 1px solid {LINE}; }}
QWidget#inspector {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 12px; }}
QWidget#inspectorBody {{ background: transparent; }}
QFrame#sectionLine {{ background: {LINE}; max-height: 1px; min-height: 1px; border: none; }}
QLabel#toast {{ background: #1e1e22; border: 1px solid #34343a; border-radius: 10px; padding: 9px 16px; color: {TEXT}; }}

/* ---- 창 단추 (제목 표시줄 대신) */
QPushButton#winBtn, QPushButton#winClose {{ background: transparent; border: none; border-radius: 0; padding: 0; }}
QPushButton#winBtn:hover {{ background: {HOVER}; }}
QPushButton#winClose:hover {{ background: #c42b1c; }}
QFrame#dialogTitle {{ background: transparent; border-bottom: 1px solid {LINE}; }}
QLabel#dialogTitleText {{ font-size: {FS_EM}px; font-weight: 600; }}
QWidget#dialogFrame {{ background: {BG}; border: 1px solid #2a2a2f; border-radius: 0; }}
"""


def ensure_fonts() -> None:
    """번들 Pretendard 를 Qt 에 등록한다 (QGuiApplication 이 있어야 함)."""
    global _fonts_done
    if _fonts_done:
        return
    try:
        from PySide6.QtGui import QFontDatabase

        from .. import paths

        for name in FONT_FILES:
            f = paths.resource_path("assets/fonts") / name
            if f.is_file():
                QFontDatabase.addApplicationFont(str(f))
        _fonts_done = True
    except Exception:  # noqa: BLE001
        pass


_fonts_done = False


def stylesheet() -> str:
    ensure_fonts()
    return STYLE + _check_icon_rule()


def apply(app) -> None:
    """기본 팝업과 비활성 컨트롤까지 같은 다크 팔레트를 사용한다."""
    from PySide6.QtGui import QFont

    app.setStyle("Fusion")
    ensure_fonts()
    f = QFont(FONT)
    f.setPixelSize(FS_BODY)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(f)
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, BG),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, "#0d0d0f"),
        (QPalette.ColorRole.AlternateBase, PANEL),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.Button, PANEL2),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.Highlight, "#33333a"),
        (QPalette.ColorRole.HighlightedText, TEXT),
        (QPalette.ColorRole.ToolTipBase, PANEL2),
        (QPalette.ColorRole.ToolTipText, TEXT),
        (QPalette.ColorRole.PlaceholderText, DIM),
    ):
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(DIM))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(DIM))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(DIM))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet())


def _check_icon_rule() -> str:
    """체크박스의 체크 표시 이미지(QSS 는 파일 경로만 받으므로 임시 폴더에 그려 둔다). 실패하면 채운 상자만 쓴다."""
    try:
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QImage, QPainter, QPen

        out = Path(tempfile.gettempdir()) / "ics_ui" / "check_v2.png"
        if not out.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            img = QImage(28, 28, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor("#0a0a0a"), 3.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline([QPointF(7.5, 14.5), QPointF(12, 19), QPointF(20.5, 9.5)])
            p.end()
            img.save(str(out))
        return f"\nQCheckBox::indicator:checked {{ image: url({out.as_posix()}); }}\n"
    except Exception:  # noqa: BLE001
        return ""

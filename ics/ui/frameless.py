"""Windows 기본 제목 표시줄 없이 앱 상단바를 제목 표시줄로 쓰기.

- 상단바 빈 곳을 끌면 창 이동 (운영체제 이동이라 화면 가장자리 끌어 붙이기(스냅)도 그대로 동작), 더블클릭하면 최대화/복원
- 창 가장자리(최대화가 아닐 때 몇 픽셀)를 끌면 크기 조절 (운영체제 크기 조절)
- 오른쪽 끝에 최소화 / 최대화·복원 / 닫기 버튼
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QWidget,
)

from . import icons, motion, theme

EDGE = 6  # 크기 조절로 쓰는 가장자리 폭 (px)


def _WinButton(w: int, h: int):  # noqa: N802
    from .widgets import FixedButton

    return FixedButton(w, h)


def window_buttons(win) -> QWidget:
    """최소화 / 최대화·복원 / 닫기."""
    box = QWidget()
    box.setObjectName("winButtons")
    h = QHBoxLayout(box)
    h.setContentsMargins(6, 0, 0, 0)
    h.setSpacing(0)
    b_min, b_max, b_close = _WinButton(44, 34), _WinButton(44, 34), _WinButton(44, 34)
    for b, tip, name in ((b_min, "최소화", "minus"), (b_max, "최대화", "square"), (b_close, "닫기", "x")):
        b.setObjectName("winBtn" if b is not b_close else "winClose")
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        _set_icon(b, name, close=b is b_close)
        h.addWidget(b)
    b_min.clicked.connect(win.showMinimized)
    b_max.clicked.connect(lambda: toggle_max(win))
    b_close.clicked.connect(win.close)
    win._win_max_btn = b_max
    return box


def _set_icon(b: QPushButton, name: str, close: bool = False) -> None:
    size = 12 if name in ("square", "copy") else 14
    b.setIcon(icons.icon(name, theme.MUTED, size, active="#ffffff" if close else theme.TEXT))
    b.setIconSize(QSize(size, size))


def toggle_max(win) -> None:
    if win.isMaximized():
        win.showNormal()
    else:
        win.showMaximized()


def sync_max_button(win) -> None:
    b = getattr(win, "_win_max_btn", None)
    if b is not None:
        _set_icon(b, "copy" if win.isMaximized() else "square")
        b.setToolTip("이전 크기로" if win.isMaximized() else "최대화")
        b.setAccessibleName(b.toolTip())


class FramelessHelper(QObject):
    """win: 최상위 창, root: 가장자리 여백을 받는 중앙 위젯, bar: 끌어서 옮기는 상단바."""

    def __init__(self, win, root: QWidget, bar: QWidget):
        super().__init__(win)
        self.win, self.root, self.bar = win, root, bar
        win.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
        )
        root.setMouseTracking(True)
        root.installEventFilter(self)
        bar.installEventFilter(self)
        for w in bar.findChildren(QWidget):  # 상단바 안의 글자/빈 칸도 끌기 영역 (버튼/입력칸/메뉴 제외)
            if not isinstance(w, (QAbstractButton, QLineEdit, QMenu)) and not w.isWindow():
                w.installEventFilter(self)
        win.installEventFilter(self)
        self._apply_margins()

    # ---- 가장자리
    def _apply_margins(self) -> None:
        m = 0 if (self.win.isMaximized() or self.win.isFullScreen()) else EDGE
        lay = self.root.layout()
        if lay is not None:
            lay.setContentsMargins(m, m, m, m)
        sync_max_button(self.win)

    def edges_at(self, pos: QPoint) -> Qt.Edge:
        if self.win.isMaximized() or self.win.isFullScreen():
            return Qt.Edge(0)
        r = self.root.rect()
        e = Qt.Edge(0)
        if pos.x() <= EDGE:
            e |= Qt.Edge.LeftEdge
        elif pos.x() >= r.width() - EDGE:
            e |= Qt.Edge.RightEdge
        if pos.y() <= EDGE:
            e |= Qt.Edge.TopEdge
        elif pos.y() >= r.height() - EDGE:
            e |= Qt.Edge.BottomEdge
        return e

    @staticmethod
    def _cursor(e: Qt.Edge):
        L, R, T, B = Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge
        if e in (L | T, R | B):
            return Qt.CursorShape.SizeFDiagCursor
        if e in (R | T, L | B):
            return Qt.CursorShape.SizeBDiagCursor
        if e & (L | R):
            return Qt.CursorShape.SizeHorCursor
        if e & (T | B):
            return Qt.CursorShape.SizeVerCursor
        return None

    def eventFilter(self, obj, ev):  # noqa: N802
        try:
            return self._filter(obj, ev)
        except (AttributeError, RuntimeError):  # 창 정리 중 (속성이 이미 사라짐)
            return False

    def _filter(self, obj, ev):
        t = ev.type()
        if "win" not in self.__dict__:
            return False
        if obj is self.win:
            if t == QEvent.Type.WindowStateChange:
                self._apply_margins()
            return False
        # 상단바 안 단추에 딸린 메뉴(··· 메뉴 등)는 따로 뜨는 창이다. 여기서 누름을 '창 끌기' 로 가로채면
        # 메뉴 항목을 눌러도 실행되지 않고 메뉴만 남는다 (예전: [업데이트…] 를 눌러도 반응 없음)
        if isinstance(obj, QWidget) and obj.window() is not self.win:
            return False
        if obj is self.root:
            if t == QEvent.Type.MouseMove and not ev.buttons():
                c = self._cursor(self.edges_at(ev.position().toPoint()))
                if c is None:
                    self.root.unsetCursor()
                else:
                    self.root.setCursor(c)
            elif t == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                e = self.edges_at(ev.position().toPoint())
                if e and self.win.windowHandle() is not None:
                    self.win.windowHandle().startSystemResize(e)
                    return True
            elif t == QEvent.Type.Leave:
                self.root.unsetCursor()
            return False
        # 상단바: 끌어서 이동, 더블클릭 최대화
        if t == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
            if self.win.windowHandle() is not None:
                self.win.windowHandle().startSystemMove()
                return True
        elif t == QEvent.Type.MouseButtonDblClick and ev.button() == Qt.MouseButton.LeftButton:
            toggle_max(self.win)
            return True
        return False


# ====================================================================== 대화상자
TITLE_H = 42


class _DialogChrome(QObject):
    """대화상자에 운영체제 제목 표시줄 대신 앱 톤의 제목 줄(제목 + 닫기)을 붙인다.

    - 제목 줄을 끌면 이동, 크기를 바꿀 수 있는 창이면 가장자리를 끌어 크기 조절
    - 얇은 테두리, 나타날 때 살짝 페이드
    기존 레이아웃은 그대로 두고 위쪽 여백만 제목 줄 높이만큼 늘린다."""

    def __init__(self, dlg: QDialog):
        super().__init__(dlg)
        self.dlg = dlg
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.bar = QFrame(dlg)
        self.bar.setObjectName("dialogTitle")
        h = QHBoxLayout(self.bar)
        h.setContentsMargins(18, 0, 0, 0)
        h.setSpacing(0)
        self.title = QLabel(dlg.windowTitle())
        self.title.setObjectName("dialogTitleText")
        h.addWidget(self.title, 1)
        if getattr(dlg, "chrome_minimize", False):  # 시작 창(프로젝트 목록)은 최소화도
            dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint)
            mn = _WinButton(46, TITLE_H - 1)
            mn.setObjectName("winBtn")
            mn.setToolTip("최소화")
            mn.setAccessibleName("최소화")
            mn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            _set_icon(mn, "minus")
            mn.clicked.connect(dlg.showMinimized)
            h.addWidget(mn, 0, Qt.AlignmentFlag.AlignTop)
        self.close_btn = _WinButton(46, TITLE_H - 1)
        self.close_btn.setObjectName("winClose")
        self.close_btn.setToolTip("닫기")
        self.close_btn.setAccessibleName("닫기")
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        _set_icon(self.close_btn, "x", close=True)
        self.close_btn.clicked.connect(dlg.reject)
        h.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.border = QFrame(dlg)
        self.border.setObjectName("dialogBorder")
        self.border.setStyleSheet("QFrame#dialogBorder { background: transparent; border: 1px solid #2c2c31; }")
        self.border.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay = dlg.layout()
        if lay is not None:
            m = lay.contentsMargins()
            lay.setContentsMargins(m.left(), m.top() + TITLE_H, m.right(), m.bottom())
        self.bar.installEventFilter(self)
        self.title.installEventFilter(self)
        dlg.installEventFilter(self)
        dlg.setMouseTracking(True)
        self._place()

    def _resizable(self) -> bool:
        d = self.dlg
        return d.minimumSize() != d.maximumSize()

    def _place(self) -> None:
        d = self.dlg
        self.bar.setGeometry(0, 0, d.width(), TITLE_H)
        self.border.setGeometry(0, 0, d.width(), d.height())
        self.bar.raise_()
        self.border.raise_()

    def _edges(self, pos: QPoint) -> Qt.Edge:
        if not self._resizable():
            return Qt.Edge(0)
        r = self.dlg.rect()
        e = Qt.Edge(0)
        if pos.x() <= EDGE:
            e |= Qt.Edge.LeftEdge
        elif pos.x() >= r.width() - EDGE:
            e |= Qt.Edge.RightEdge
        if pos.y() <= EDGE:
            e |= Qt.Edge.TopEdge
        elif pos.y() >= r.height() - EDGE:
            e |= Qt.Edge.BottomEdge
        return e

    def eventFilter(self, obj, ev):  # noqa: N802
        # 창이 정리(가비지 수집)되는 도중에도 이벤트가 올 수 있다: 이때는 속성이 이미 사라졌으므로 조용히 넘긴다
        # (예전: "'_DialogChrome' object has no attribute 'dlg'" 오류 창)
        try:
            return self._filter(obj, ev)
        except (AttributeError, RuntimeError):
            return False

    def _filter(self, obj, ev):
        t = ev.type()
        d = self.__dict__.get("dlg")
        if d is None:
            return False
        if obj is d:
            if t in (QEvent.Type.Resize, QEvent.Type.Show):
                self._place()
            elif t == QEvent.Type.WindowTitleChange:
                self.title.setText(d.windowTitle())
            elif t == QEvent.Type.MouseMove and not ev.buttons():
                c = FramelessHelper._cursor(self._edges(ev.position().toPoint()))
                d.unsetCursor() if c is None else d.setCursor(c)
            elif t == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                e = self._edges(ev.position().toPoint())
                if e and d.windowHandle() is not None:
                    d.windowHandle().startSystemResize(e)
                    return True
            elif t == QEvent.Type.Leave:
                d.unsetCursor()
            return False
        if t == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
            if d.windowHandle() is not None:
                d.windowHandle().startSystemMove()
                return True
        return False


class ChromeDialog(QDialog):
    """앱의 모든 대화상자 기본형: 처음 보일 때 제목 줄을 붙이고 살짝 페이드한다."""

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        first = visible and getattr(self, "_chrome", None) is None
        if first:
            self._chrome = _DialogChrome(self)
        super().setVisible(visible)
        if first:
            motion.window_fade_in(self)

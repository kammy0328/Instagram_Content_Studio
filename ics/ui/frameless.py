"""Windows 기본 제목 표시줄 없이 앱 상단바를 제목 표시줄로 쓰기.

- 상단바 빈 곳을 끌면 창 이동 (운영체제 이동이라 화면 가장자리 끌어 붙이기(스냅)도 그대로 동작), 더블클릭하면 최대화/복원
- 창 가장자리(최대화가 아닐 때 몇 픽셀)를 끌면 크기 조절 (운영체제 크기 조절)
- 오른쪽 끝에 최소화 / 최대화·복원 / 닫기 버튼
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLineEdit, QPushButton, QWidget

EDGE = 6  # 크기 조절로 쓰는 가장자리 폭 (px)


def window_buttons(win) -> QWidget:
    """최소화 / 최대화·복원 / 닫기."""
    box = QWidget()
    box.setObjectName("winButtons")
    h = QHBoxLayout(box)
    h.setContentsMargins(6, 0, 0, 0)
    h.setSpacing(0)
    b_min = QPushButton("—")
    b_max = QPushButton("☐")
    b_close = QPushButton("✕")
    for b, tip in ((b_min, "최소화"), (b_max, "최대화"), (b_close, "닫기")):
        b.setObjectName("winBtn" if b is not b_close else "winClose")
        b.setFixedSize(42, 30)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        h.addWidget(b)
    b_min.clicked.connect(win.showMinimized)
    b_max.clicked.connect(lambda: toggle_max(win))
    b_close.clicked.connect(win.close)
    win._win_max_btn = b_max
    return box


def toggle_max(win) -> None:
    if win.isMaximized():
        win.showNormal()
    else:
        win.showMaximized()


def sync_max_button(win) -> None:
    b = getattr(win, "_win_max_btn", None)
    if b is not None:
        b.setText("❐" if win.isMaximized() else "☐")
        b.setToolTip("이전 크기로" if win.isMaximized() else "최대화")


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
        for w in bar.findChildren(QWidget):  # 상단바 안의 글자/빈 칸도 끌기 영역 (버튼/입력칸 제외)
            if not isinstance(w, (QAbstractButton, QLineEdit)):
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
        t = ev.type()
        if obj is self.win:
            if t == QEvent.Type.WindowStateChange:
                self._apply_margins()
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

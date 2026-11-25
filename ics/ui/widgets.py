"""작업 공간의 공통 표시 요소. 데이터와 편집 동작에는 관여하지 않는다."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from . import icons, motion, theme

QWIDGETSIZE_MAX = 16777215


class ElidedLabel(QLabel):
    """긴 이름이 창의 최소 너비를 늘리지 않도록 말줄임표로 표시한다."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), self.alignment(), text)

    def setText(self, text: str) -> None:
        super().setText(text)
        self.setToolTip(text)


class FixedButton(QPushButton):
    """고정 크기 단추: 스타일시트의 min-height 가 고정 크기를 덮어쓰지 않게 크기 힌트도 고정한다."""

    def __init__(self, w: int, h: int | None = None, text: str = ""):
        super().__init__(text)
        self._sz = QSize(w, h if h is not None else w)
        self.setFixedSize(self._sz)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self._sz

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self._sz


class FixedToolButton(QToolButton):
    """고정 크기 메뉴 단추 (FixedButton 과 같은 이유)."""

    def __init__(self, w: int, h: int | None = None):
        super().__init__()
        self._sz = QSize(w, h if h is not None else w)
        self.setFixedSize(self._sz)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self._sz

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self._sz


def menu_button(icon_name: str, tip: str, size: int = 30) -> QToolButton:
    """··· 같은 아이콘 메뉴 단추 (누르면 바로 메뉴)."""
    b = FixedToolButton(size)
    b.setObjectName("ghostTool")
    b.setIcon(icons.icon(icon_name, theme.TEXT, 16))
    b.setIconSize(QSize(16, 16))
    b.setToolTip(tip)
    b.setAccessibleName(tip.split(" (")[0])
    b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    return b


def icon_button(
    name: str,
    tip: str,
    fn=None,
    size: int = 30,
    icon_size: int = 16,
    checkable: bool = False,
    color: str = theme.TEXT,
    on: str | None = None,
) -> QPushButton:
    """글자 없는 아이콘 단추 (툴팁 = 접근성 이름)."""
    b = FixedButton(size)
    b.setObjectName("icon")
    b.setIcon(icons.icon(name, color, icon_size, on=on))
    b.setIconSize(QSize(icon_size, icon_size))
    b.setToolTip(tip)
    b.setAccessibleName(tip.split("\n")[0].split(" (")[0])
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if fn is not None:
        b.clicked.connect(fn)
    return b


# ====================================================================== 상단 작업 탭
class NavTab(QPushButton):
    """상단 작업 탭 하나: 아이콘 + 이름 + (오른쪽) 상태 점 또는 개수.

    선택 표시(밝은 바탕)는 탭 묶음(NavTabs)이 뒤에서 미끄러지듯 그린다. 체크(✓) 글자는 쓰지 않는다."""

    def __init__(self, key: str, title: str, icon_name: str):
        super().__init__(title)
        self.key = key
        self.icon_name = icon_name
        self.setObjectName("navTab")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.done = False  # 구간이 지정되어 내보낼 수 있음
        self.count = ""  # 예: '3' (피드 항목 수). 비어 있으면 점 표시만
        self._hover = False
        f = QFont(theme.FONT)
        f.setPixelSize(theme.FS_BODY)
        f.setWeight(QFont.Weight.DemiBold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        self._font = f
        self._fcount = QFont(theme.FONT)
        self._fcount.setPixelSize(10)
        self._fcount.setWeight(QFont.Weight.Bold)

    def set_state(self, done: bool, count: str = "") -> None:
        if (self.done, self.count) != (bool(done), count):
            self.done, self.count = bool(done), count
            self.updateGeometry()
            self.update()

    def _count_w(self) -> int:
        from PySide6.QtGui import QFontMetrics

        return QFontMetrics(self._fcount).horizontalAdvance(self.count) + 10 if self.count else 0

    def sizeHint(self) -> QSize:
        from PySide6.QtGui import QFontMetrics

        tw = QFontMetrics(self._font).horizontalAdvance(self.text())
        extra = (self._count_w() + 6) if self.count else 10
        return QSize(14 + 14 + 7 + tw + extra + 12, 30)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def event(self, e):  # noqa: N802
        if e.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self._hover = e.type() == QEvent.Type.HoverEnter
            self.update()
        return super().event(e)

    def paintEvent(self, _e):  # noqa: N802
        from PySide6.QtGui import QFontMetrics

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        col = theme.TAB_COLORS.get(self.key, theme.TEXT)
        if self._hover and not on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 10))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 7, 7)
        fg = "#ffffff" if on else (theme.TEXT if self._hover else theme.MUTED)
        x = 14
        cy = self.height() / 2
        pm = icons.pixmap(self.icon_name, col if on else fg, 14)
        p.drawPixmap(QPointF(x, cy - 7), pm)
        x += 14 + 7
        p.setFont(self._font)
        p.setPen(QColor(fg))
        tw = QFontMetrics(self._font).horizontalAdvance(self.text())
        p.drawText(
            QRectF(x, 0, tw + 4, self.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text()
        )
        x += tw + 7
        if self.count:
            w = self._count_w()
            r = QRectF(x, cy - 8, w, 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(col) if (self.done and on) else QColor(255, 255, 255, 24 if self.done else 12))
            p.drawRoundedRect(r, 8, 8)
            p.setFont(self._fcount)
            p.setPen(QColor("#0a0a0a") if (self.done and on) else QColor(fg if self.done else theme.DIM))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.count)
        elif self.done:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(col))
            p.drawEllipse(QRectF(x - 1, cy - 3, 6, 6))
        if self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus:
            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), 7, 7)
        p.end()


SegButton = NavTab  # 이전 이름


class NavTabs(QFrame):
    """작업 탭 묶음. 고른 탭 뒤의 밝은 바탕과 그 아래 색 막대가 다음 탭으로 미끄러져 간다."""

    tabClicked = Signal(str)

    def __init__(self, specs, parent=None):
        """specs: (key, 제목, 아이콘 이름, 툴팁) 목록."""
        super().__init__(parent)
        self.setObjectName("navTabs")
        self.buttons: dict[str, NavTab] = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        h = QHBoxLayout(self)
        h.setContentsMargins(3, 3, 3, 3)
        h.setSpacing(2)
        for key, title, icon_name, tip in specs:
            b = NavTab(key, title, icon_name)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self.tabClicked.emit(k))
            b.toggled.connect(lambda on, k=key: on and self._move_to(k))
            self.group.addButton(b)
            h.addWidget(b)
            self.buttons[key] = b
        self._cur: str | None = None
        self._ind = QRectF()
        self._ind_col = QColor(theme.TEXT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def current(self) -> str | None:
        return self._cur

    def _target(self, key: str) -> QRectF:
        return QRectF(self.buttons[key].geometry())

    def _move_to(self, key: str) -> None:
        prev = self._cur
        self._cur = key
        end = self._target(key)
        end_col = QColor(theme.TAB_COLORS.get(key, theme.TEXT))
        if prev is None or self._ind.isNull() or not self.isVisible():
            self._ind, self._ind_col = end, end_col
            self.update()
            return
        start, start_col = QRectF(self._ind), QColor(self._ind_col)

        def step(t):
            t = float(t)
            self._ind = motion.rect_lerp(start, end, t)
            self._ind_col = QColor(
                int(start_col.red() + (end_col.red() - start_col.red()) * t),
                int(start_col.green() + (end_col.green() - start_col.green()) * t),
                int(start_col.blue() + (end_col.blue() - start_col.blue()) * t),
            )
            self.update()

        motion.animate_value(self, "_ind_anim", 0.0, 1.0, step, ms=motion.SLOW)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self._cur:
            self._ind = self._target(self._cur)

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        if self._cur:
            self._ind = self._target(self._cur)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(theme.LINE), 1))
        p.setBrush(QColor(theme.PANEL))
        p.drawRoundedRect(r, 10, 10)
        if self._cur and not self._ind.isNull():
            ind = self._ind
            p.setPen(QPen(QColor("#303036"), 1))
            p.setBrush(QColor("#232327"))
            p.drawRoundedRect(ind.adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
            # 아래 색 막대 (탭의 뜻 색)
            bar = QRectF(ind.center().x() - 9, ind.bottom() - 3.5, 18, 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._ind_col)
            p.drawRoundedRect(bar, 1, 1)
        p.end()


# ====================================================================== 접는 묶음
class _SectionHead(QAbstractButton):
    """묶음 머리: 제목 (+ 도움말 ?) ... 펼침 화살표. 화살표는 돌아가며 바뀐다."""

    def __init__(self, title: str, help_text: str = ""):
        super().__init__()
        self.setText(title)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.help_text = help_text
        self._angle = 0.0  # 0: 펼침(아래), -90: 접힘(오른쪽)
        self._hover = False
        f = QFont(theme.FONT)
        f.setPixelSize(theme.FS_BODY)
        f.setWeight(QFont.Weight.DemiBold)
        self._font = f
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:
        return QSize(120, 34)

    def _help_rect(self) -> QRectF:
        from PySide6.QtGui import QFontMetrics

        tw = QFontMetrics(self._font).horizontalAdvance(self.text())
        return QRectF(tw + 6, (self.height() - 13) / 2, 13, 13)

    def set_angle(self, a: float) -> None:
        self._angle = float(a)
        self.update()

    def event(self, e):  # noqa: N802
        t = e.type()
        if t in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self._hover = t == QEvent.Type.HoverEnter
            self.update()
        elif t == QEvent.Type.ToolTip:
            if self.help_text and self._help_rect().adjusted(-4, -4, 4, 4).contains(e.position()):
                QToolTip.showText(e.globalPos(), self.help_text, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self._font)
        p.setPen(QColor("#ffffff" if self._hover else theme.TEXT))
        p.drawText(
            QRectF(0, 0, self.width() - 24, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )
        if self.help_text:
            p.drawPixmap(self._help_rect().topLeft(), icons.pixmap("circle-help", theme.DIM, 13))
        pm = icons.pixmap("chevron-down", theme.TEXT if self._hover else theme.MUTED, 14)
        p.translate(self.width() - 9, self.height() / 2)
        p.rotate(self._angle)
        p.drawPixmap(QPointF(-7, -7), pm)
        p.end()


class Section(QWidget):
    """접고 펼 수 있는 묶음 (머리를 누르면 접힘/펼침이 부드럽게). 접힘 상태는 설정(key)에 남긴다.

    상태(is_open, 설정 값)는 누르는 즉시 바뀌고, 높이 변화만 애니메이션된다."""

    toggledOpen = Signal(bool)

    def __init__(
        self,
        title: str,
        key: str | None = None,
        settings=None,
        open_default: bool = True,
        help_text: str = "",
        line: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self._settings, self._key = settings, key
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        if line:
            ln = QFrame()
            ln.setObjectName("sectionLine")
            ln.setFixedHeight(1)
            outer.addWidget(ln)
        self.head = _SectionHead(title, help_text)
        outer.addWidget(self.head)
        self.body = QWidget()
        self.body.setObjectName("sectionBody")
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 2, 0, 14)
        self.lay.setSpacing(8)
        outer.addWidget(self.body)
        is_open = open_default
        if settings is not None and key:
            is_open = bool(settings.get(key, open_default))
        self._open = is_open
        self.head.setChecked(is_open)
        self.head.set_angle(0 if is_open else -90)
        self.body.setVisible(is_open)
        self.head.toggled.connect(self._toggled)

    def is_open(self) -> bool:
        return self._open

    def set_open(self, on: bool) -> None:
        if self.head.isChecked() != on:
            self.head.setChecked(on)

    def _toggled(self, on: bool) -> None:
        self._open = bool(on)
        if self._settings is not None and self._key:
            self._settings.set(self._key, bool(on))
        self.toggledOpen.emit(bool(on))
        a0 = self.head._angle
        motion.animate_value(self, "_arrow_anim", a0, 0.0 if on else -90.0, self.head.set_angle, ms=motion.NORMAL)
        body = self.body
        if on:
            body.setMaximumHeight(0)
            body.setVisible(True)
            h = max(1, body.sizeHint().height())

            def done():
                body.setMaximumHeight(QWIDGETSIZE_MAX)

            motion.animate_value(
                self, "_body_anim", 0, h, lambda v: body.setMaximumHeight(int(v)), ms=motion.NORMAL, done=done
            )
        else:
            h = body.height() if body.isVisible() else 0

            def done2():
                if not self._open:
                    body.setVisible(False)
                body.setMaximumHeight(QWIDGETSIZE_MAX)

            motion.animate_value(
                self, "_body_anim", h, 0, lambda v: body.setMaximumHeight(int(v)), ms=motion.FAST, done=done2
            )


def studio_icon(size: int = 64) -> QIcon:
    """별도 이미지 파일 없이 그리는 앱 아이콘."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0, QColor("#ffffff"))
    gradient.setColorAt(1, QColor("#cfcfcf"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size * 0.23, size * 0.23)
    painter.setPen(QPen(QColor("#0a0a0a"), size * 0.065))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(size * 0.23, size * 0.22, size * 0.54, size * 0.57), size * 0.08, size * 0.08)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#0a0a0a"))
    painter.drawEllipse(QRectF(size * 0.4, size * 0.4, size * 0.2, size * 0.2))
    painter.drawEllipse(QRectF(size * 0.65, size * 0.29, size * 0.055, size * 0.055))
    painter.end()
    return QIcon(pixmap)


def control_icon(widget, standard, color: str = theme.TEXT) -> QIcon:
    """표준 아이콘을 다크 배경에서도 읽을 수 있는 색으로 맞춘다 (선 아이콘이 없는 곳의 예비)."""
    pixmap = widget.style().standardIcon(standard).pixmap(16, 16)
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(color))
    painter.end()
    return QIcon(pixmap)

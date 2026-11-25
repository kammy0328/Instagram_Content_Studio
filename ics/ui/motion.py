"""화면 전환과 작은 동작 애니메이션.

원칙: 상태(어느 탭인지, 무엇이 보이는지)는 언제나 즉시 바뀐다. 애니메이션은 그 위에 덧그리는 '보이는 효과' 일 뿐이라
빠르게 연달아 눌러도 꼬이지 않고, 애니메이션이 끝나기를 기다리는 코드가 없다.
- Transition: 바뀌기 전/후 화면을 사진처럼 찍어 위에 덮고 밀어내며 겹쳐 보인 뒤 사라진다 (마우스는 통과).
- fade_in / slide_fade_in: 새로 나타나는 위젯/창.
- smooth_scroll: 스크롤 막대를 목표 값까지 부드럽게.
환경 변수 ICS_REDUCED_MOTION=1 또는 set_enabled(False) 로 모두 끌 수 있다 (즉시 바뀜).
"""

from __future__ import annotations

import os

from PySide6.QtCore import (
    QAbstractAnimation,
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from . import theme

_enabled = os.environ.get("ICS_REDUCED_MOTION", "") not in ("1", "true", "yes")

# 길이 (ms): 짧게. 편집 도구는 빨라야 한다.
FAST, NORMAL, SLOW = 140, 220, 320
EASE = QEasingCurve.Type.OutCubic


def enabled() -> bool:
    return _enabled


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = bool(on)


def _keep(owner: QObject, anim: QAbstractAnimation, name: str) -> QAbstractAnimation:
    """같은 대상의 이전 애니메이션을 멈추고 새 것을 붙잡아 둔다 (가비지 수집 방지)."""
    old = getattr(owner, name, None)
    if old is not None:
        try:
            old.stop()
        except RuntimeError:
            pass
    setattr(owner, name, anim)
    return anim


# ------------------------------------------------------------------ 전환 (스냅샷)
class _Cover(QWidget):
    """host 위에 덮는 그림판: 이전 화면은 밀려나며 흐려지고, 새 화면은 들어오며 또렷해진다."""

    def __init__(self, host: QWidget, old: QPixmap):
        super().__init__(host)
        self.setObjectName("motionCover")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.old, self.new = old, None
        self.t = 0.0
        self.dx = 0
        self.dy = 0
        self.bg = QColor(theme.BG)
        self.radius = 0
        self.setGeometry(host.rect())

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        if self.radius:
            from PySide6.QtGui import QPainterPath

            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), self.radius, self.radius)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setClipPath(path)
        t = self.t
        if self.new is None:
            p.drawPixmap(0, 0, self.old)
            return
        # 제자리 전환: 이전 화면 위에 새 화면을 점점 진하게 겹친다 (바뀌지 않은 부분이 깜빡이지 않음).
        # 밀어 바꿀 때(dx)는 이전 화면이 앞쪽 절반에서 빠르게 빠져 두 화면이 겹쳐 보이는 시간을 줄인다.
        p.fillRect(self.rect(), self.bg)
        moving = bool(self.dx or self.dy)
        p.setOpacity(max(0.0, 1.0 - 2.0 * t) if moving else 1.0)
        p.drawPixmap(QPoint(int(-self.dx * 0.4 * t), int(-self.dy * 0.4 * t)), self.old)
        p.setOpacity(min(1.0, t * 1.3) if moving else t)
        p.drawPixmap(QPoint(int(self.dx * (1 - t)), int(self.dy * (1 - t))), self.new)


class Transition:
    """사용법:
    tr = Transition(widget)   # 바뀌기 전에 찍는다
    ... 상태 변경 ...
    tr.play(dx=24)            # 바뀐 뒤를 찍어 겹쳐 보인다 (dx>0: 오른쪽에서 들어옴)
    """

    def __init__(self, host: QWidget, bg: str | None = None, radius: int = 0):
        self.host = host
        self.cover: _Cover | None = None
        if not _enabled or not host.isVisible() or host.width() < 2 or host.height() < 2:
            return
        finish(host)
        try:
            old = host.grab()
        except Exception:  # noqa: BLE001
            return
        self.cover = _Cover(host, old)
        if bg:
            self.cover.bg = QColor(bg)
        self.cover.radius = radius
        self.cover.show()
        self.cover.raise_()

    def play(self, dx: int = 0, dy: int = 0, ms: int = NORMAL) -> None:
        cov = self.cover
        if cov is None:
            return
        host = self.host
        cov.hide()  # 새 화면을 찍을 때 덮개가 같이 찍히지 않게
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        try:
            cov.new = host.grab()
        except Exception:  # noqa: BLE001
            cov.deleteLater()
            return
        cov.dx, cov.dy = dx, dy
        cov.setGeometry(host.rect())
        cov.show()
        cov.raise_()
        anim = QVariantAnimation(cov)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(ms)
        anim.setEasingCurve(EASE)

        def step(v):
            cov.t = float(v)
            cov.update()

        anim.valueChanged.connect(step)
        anim.finished.connect(cov.deleteLater)
        host._motion_cover = cov
        anim.start()


def finish(host: QWidget) -> None:
    """진행 중인 전환 덮개를 바로 걷어낸다."""
    cov = getattr(host, "_motion_cover", None)
    if cov is not None:
        try:
            cov.hide()
            cov.deleteLater()
        except RuntimeError:
            pass
        host._motion_cover = None
    for c in host.findChildren(_Cover):
        if c.parent() is host:
            c.hide()
            c.deleteLater()


# ------------------------------------------------------------------ 나타나기
def fade_in(w: QWidget, ms: int = FAST, done=None) -> None:
    """위젯을 투명에서 또렷하게. 끝나면 효과를 떼어 낸다 (그리기 비용/글자 흐림 방지)."""
    if not _enabled:
        if done:
            done()
        return
    eff = QGraphicsOpacityEffect(w)
    eff.setOpacity(0.0)
    w.setGraphicsEffect(eff)
    a = QPropertyAnimation(eff, b"opacity", w)
    a.setStartValue(0.0)
    a.setEndValue(1.0)
    a.setDuration(ms)
    a.setEasingCurve(EASE)

    def end():
        if w.graphicsEffect() is eff:
            w.setGraphicsEffect(None)
        if done:
            done()

    a.finished.connect(end)
    _keep(w, a, "_motion_fade")
    a.start()


def fade_out(w: QWidget, ms: int = FAST, done=None) -> None:
    if not _enabled or not w.isVisible():
        w.hide()
        if done:
            done()
        return
    eff = QGraphicsOpacityEffect(w)
    eff.setOpacity(1.0)
    w.setGraphicsEffect(eff)
    a = QPropertyAnimation(eff, b"opacity", w)
    a.setStartValue(1.0)
    a.setEndValue(0.0)
    a.setDuration(ms)
    a.setEasingCurve(QEasingCurve.Type.InCubic)

    def end():
        w.hide()
        if w.graphicsEffect() is eff:
            w.setGraphicsEffect(None)
        if done:
            done()

    a.finished.connect(end)
    _keep(w, a, "_motion_fade")
    a.start()


def slide_fade_in(w: QWidget, dy: int = 8, ms: int = NORMAL) -> None:
    """살짝 아래에서 올라오며 나타난다 (토스트 등 자식 위젯)."""
    if not _enabled:
        return
    end = w.pos()
    w.move(end + QPoint(0, dy))
    a = QPropertyAnimation(w, b"pos", w)
    a.setStartValue(end + QPoint(0, dy))
    a.setEndValue(end)
    a.setDuration(ms)
    a.setEasingCurve(EASE)
    _keep(w, a, "_motion_slide")
    a.start()
    fade_in(w, ms)


def window_fade_in(win: QWidget, ms: int = FAST) -> None:
    """창 전체 투명도 (운영체제가 지원할 때만, 아니면 그냥 보인다)."""
    if not _enabled:
        return
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.platformName() in ("offscreen", "minimal"):
        return
    win.setWindowOpacity(0.0)
    a = QPropertyAnimation(win, b"windowOpacity", win)
    a.setStartValue(0.0)
    a.setEndValue(1.0)
    a.setDuration(ms)
    a.setEasingCurve(EASE)
    _keep(win, a, "_motion_win")
    a.start()


# ------------------------------------------------------------------ 스크롤
def smooth_scroll(bar, target: int, ms: int = NORMAL) -> None:
    """스크롤 막대를 target 까지 부드럽게. 꺼져 있으면 바로 간다."""
    target = max(bar.minimum(), min(bar.maximum(), int(target)))
    old = getattr(bar, "_motion_scroll", None)
    if old is not None and old.state() == QAbstractAnimation.State.Running:
        old.stop()
    if not _enabled or abs(target - bar.value()) < 2:
        bar.setValue(target)
        return
    a = QVariantAnimation(bar)
    a.setStartValue(bar.value())
    a.setEndValue(target)
    a.setDuration(ms)
    a.setEasingCurve(EASE)
    a.valueChanged.connect(lambda v: bar.setValue(int(v)))
    bar._motion_scroll = a
    a.start()


def stop_scroll(bar) -> None:
    a = getattr(bar, "_motion_scroll", None)
    if a is not None and a.state() == QAbstractAnimation.State.Running:
        a.stop()


def scroll_target(bar) -> int:
    """진행 중인 부드러운 스크롤이 있으면 그 목표 값, 없으면 현재 값."""
    a = getattr(bar, "_motion_scroll", None)
    if a is not None and a.state() == QAbstractAnimation.State.Running:
        return int(a.endValue())
    return bar.value()


# ------------------------------------------------------------------ 값 하나 움직이기
def animate_value(owner: QObject, name: str, start, end, setter, ms: int = NORMAL, done=None, curve=EASE):
    """임의의 값을 start -> end 로 (setter 호출). owner 의 name 슬롯에 붙잡아 둔다."""
    if not _enabled:
        setter(end)
        if done:
            done()
        return None
    a = QVariantAnimation(owner)
    a.setStartValue(start)
    a.setEndValue(end)
    a.setDuration(ms)
    a.setEasingCurve(curve)
    a.valueChanged.connect(setter)
    if done:
        a.finished.connect(done)
    _keep(owner, a, name)
    a.start()
    return a


def rect_lerp(a: QRectF, b: QRectF, t: float) -> QRectF:
    return QRectF(
        a.x() + (b.x() - a.x()) * t,
        a.y() + (b.y() - a.y()) * t,
        a.width() + (b.width() - a.width()) * t,
        a.height() + (b.height() - a.height()) * t,
    )

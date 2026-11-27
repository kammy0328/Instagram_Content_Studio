"""프로젝트 여는 동안 보여 주는 로딩 화면. 무채색, 얇은 회전 호와 단계 문구만 둔다."""

from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QEventLoop, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QDialog


class LoadingDialog(QDialog):
    """프레임 없는 어두운 카드. 메인 스레드가 바쁜 구간에서도 set_step 이 repaint 를 강제하므로 멈춘 것처럼 보이지 않는다."""

    ACCENT = QColor("#ececec")

    def __init__(self, title: str = "프로젝트 여는 중", parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.resize(360, 170)
        self._title, self._step, self._t0 = title, "", time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)

    def showEvent(self, e):  # noqa: N802
        self._t0 = time.monotonic()
        self._timer.start()
        scr = self.screen().availableGeometry() if self.screen() else None
        if scr:
            self.move(scr.center() - self.rect().center())
        super().showEvent(e)

    def hideEvent(self, e):  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def set_step(self, text: str) -> None:
        self._step = text
        self.repaint()
        QApplication.processEvents()

    def set_step_later(self, text: str) -> None:
        """다른 스레드에서 불러도 되는 단계 문구 바꾸기 (16ms 마다 다시 그리는 타이머가 반영한다)."""
        self._step = text

    def pulse(self) -> None:
        """무거운 UI 작업 사이에 불러 애니메이션을 한 번 더 그린다 (사용자 입력은 처리하지 않음)."""
        from PySide6.QtCore import QEventLoop

        self.repaint()
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def paintEvent(self, _e):  # noqa: N802
        t = time.monotonic() - self._t0
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        card = QPainterPath()
        card.addRoundedRect(QRectF(6, 6, w - 12, h - 12), 12, 12)
        p.fillPath(card, QColor(18, 18, 18, 250))
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.drawPath(card)
        # 얇은 회전 호 (길이가 천천히 늘었다 줄어든다)
        cx, cy, r = w / 2, 58, 15
        p.setPen(QPen(QColor(255, 255, 255, 18), 2))
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
        span = 70 + 110 * (0.5 + 0.5 * math.sin(t * 2.0))
        start = -(t * 260) % 360
        pen = QPen(self.ACCENT, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), int(start * 16), int(-span * 16))
        f = QFont(self.font())
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor("#ececec"))
        p.drawText(QRectF(0, 92, w, 22), Qt.AlignmentFlag.AlignCenter, self._title)
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 120))
        p.drawText(QRectF(20, 116, w - 40, 20), Qt.AlignmentFlag.AlignCenter, self._step)


def run_in_thread(fn, *args):
    """무거운 작업을 스레드에서 돌리고 끝날 때까지 이벤트 루프를 돌려(애니메이션 유지) 결과를 돌려준다. 예외는 그대로 전달."""
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn, *args)
    loop = QEventLoop()
    tick = QTimer()
    tick.setInterval(15)
    tick.timeout.connect(lambda: loop.quit() if fut.done() else None)
    tick.start()
    loop.exec()
    tick.stop()
    ex.shutdown(wait=False)
    return fut.result()

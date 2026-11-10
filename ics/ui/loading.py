"""프로젝트 여는 동안 보여 주는 로딩 화면 (회전 호 + 빛 흐르는 진행 막대 + 단계 문구)."""

from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QEventLoop, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QDialog


class LoadingDialog(QDialog):
    """프레임 없는 어두운 카드. 메인 스레드가 바쁜 구간에서도 set_step 이 repaint 를 강제하므로 멈춘 것처럼 보이지 않는다."""

    ACCENT = QColor("#ff6b9a")

    def __init__(self, title: str = "프로젝트 여는 중", parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.resize(420, 220)
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

    def paintEvent(self, _e):  # noqa: N802
        t = time.monotonic() - self._t0
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        card = QPainterPath()
        card.addRoundedRect(QRectF(8, 8, w - 16, h - 16), 18, 18)
        p.fillPath(card, QColor(24, 24, 30, 245))
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.drawPath(card)
        # 회전 호 (가속/감속하며 길이가 변한다)
        cx, cy, r = w / 2, 74, 24
        p.setPen(QPen(QColor(255, 255, 255, 25), 4))
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
        span = 90 + 150 * (0.5 + 0.5 * math.sin(t * 2.4))
        start = -(t * 300) % 360
        pen = QPen(self.ACCENT, 4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), int(start * 16), int(-span * 16))
        # 제목 / 단계
        f = QFont(self.font())
        f.setPointSizeF(13)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor("#f2f2f5"))
        p.drawText(QRectF(0, 116, w, 28), Qt.AlignmentFlag.AlignCenter, self._title + "." * (int(t * 2.5) % 4))
        f.setPointSizeF(9.5)
        f.setWeight(QFont.Weight.Normal)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 140))
        p.drawText(QRectF(24, 146, w - 48, 22), Qt.AlignmentFlag.AlignCenter, self._step)
        # 진행 막대: 빛이 흘러간다
        bx, by, bw, bh = 48, 184, w - 96, 4
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 25))
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 2, 2)
        pos = (t * 0.55) % 1.4 - 0.2
        g = QLinearGradient(bx + bw * (pos - 0.2), 0, bx + bw * (pos + 0.2), 0)
        g.setColorAt(0, QColor(255, 107, 154, 0))
        g.setColorAt(0.5, self.ACCENT)
        g.setColorAt(1, QColor(255, 107, 154, 0))
        p.setClipRect(QRectF(bx, by, bw, bh))
        p.setBrush(g)
        p.drawRect(QRectF(bx, by, bw, bh))


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

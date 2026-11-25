"""작업 공간의 공통 표시 요소. 데이터와 편집 동작에는 관여하지 않는다."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy

from . import theme


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


class SegButton(QPushButton):
    """상단 작업 탭. 글자 옆 체크(✓) 대신 오른쪽 위에 작은 점(구간 지정됨)이나 숫자(피드 개수)를 그린다."""

    def __init__(self, title: str):
        super().__init__(title)
        self.setObjectName("seg")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.done = False  # 구간이 지정되어 내보낼 수 있음
        self.count = ""  # 예: '3' (피드 항목 수). 비어 있으면 점 표시만

    def set_state(self, done: bool, count: str = "") -> None:
        if (self.done, self.count) != (bool(done), count):
            self.done, self.count = bool(done), count
            self.update()

    def sizeHint(self) -> QSize:
        sz = super().sizeHint()
        extra = (self.fontMetrics().horizontalAdvance(self.count) + 10) if self.count else 8
        return QSize(sz.width() + extra, sz.height())

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tw = self.fontMetrics().horizontalAdvance(self.text())
        x = (self.width() + tw) / 2 + 5
        cy = self.height() / 2
        on = QColor("#ffffff") if self.isChecked() else QColor(theme.TEXT)
        if self.count:
            f = self.font()
            f.setPixelSize(10)
            f.setBold(True)
            p.setFont(f)
            p.setPen(on if self.done else QColor(theme.MUTED))
            p.drawText(
                QRectF(x - 2, 0, 40, self.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.count,
            )
        elif self.done:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.OK))
            p.drawEllipse(QRectF(x - 1, cy - 2.5, 5, 5))
        p.end()


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
    """표준 아이콘을 다크 배경에서도 읽을 수 있는 색으로 맞춘다."""
    pixmap = widget.style().standardIcon(standard).pixmap(16, 16)
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(color))
    painter.end()
    return QIcon(pixmap)

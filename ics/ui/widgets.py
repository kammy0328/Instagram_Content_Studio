"""작업 공간의 공통 표시 요소. 데이터와 편집 동작에는 관여하지 않는다."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QStyle, QStyleOptionButton, QStylePainter

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


class WorkspaceButton(QPushButton):
    """단계 번호, 이름, 규격을 함께 보여 주는 접근 가능한 탐색 버튼."""

    def __init__(self, step: str, title: str, subtitle: str):
        super().__init__(title)
        self.step, self.subtitle = step, subtitle
        self.setObjectName("nav")
        self.setCheckable(True)
        self.setMinimumHeight(76)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(152, 76)

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(theme.ACCENT if self.isChecked() else theme.MUTED))
        font = self.font()
        font.setPixelSize(10)
        painter.setFont(font)
        painter.drawText(14, 21, self.step)
        font.setPixelSize(14)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(14, 42, self.text())
        font.setPixelSize(11)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(theme.MUTED))
        painter.drawText(14, 61, self.subtitle)


def studio_icon(size: int = 64) -> QIcon:
    """별도 이미지 파일 없이 그리는 앱 아이콘."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0, QColor("#a8b9ff"))
    gradient.setColorAt(1, QColor("#6b7ee8"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size * 0.23, size * 0.23)
    painter.setPen(QPen(QColor("#151b32"), size * 0.065))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(size * 0.23, size * 0.22, size * 0.54, size * 0.57), size * 0.08, size * 0.08)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#151b32"))
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

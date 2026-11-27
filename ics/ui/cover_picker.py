"""커버 추천 결과에서 한 장 고르기."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout

from .frameless import ChromeDialog


def rgb_to_qimage(rgb: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class CoverPickerDialog(ChromeDialog):
    """추천 프레임 그림을 보여 주고 하나를 고르게 한다. chosen = 고른 프레임 번호."""

    def __init__(self, items, tc_of, parent=None):
        super().__init__(parent)
        self.setWindowTitle("커버 추천")
        self.chosen: int | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)
        hint = QLabel("얼굴이 정면에 가깝고 초점이 맞은 프레임입니다. 누르면 커버로 지정하고 그 위치로 이동합니다.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        grid = QGridLayout()
        grid.setSpacing(10)
        self.buttons: list[QToolButton] = []
        for i, (cand, rgb) in enumerate(items):
            b = QToolButton()
            b.setObjectName("coverChoice")
            img = rgb_to_qimage(rgb)
            icon_w = 232
            icon_h = max(1, int(round(icon_w * img.height() / max(1, img.width()))))
            b.setIcon(QIcon(QPixmap.fromImage(img)))
            b.setIconSize(QSize(icon_w, icon_h))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setText(f"{tc_of(cand.frame)}\n{cand.reason()}")
            b.setToolTip(f"f{cand.frame}")
            b.clicked.connect(lambda _=False, f=cand.frame: self._pick(f))
            grid.addWidget(b, i // 3, i % 3)
            self.buttons.append(b)
        v.addLayout(grid)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("닫기")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        v.addLayout(row)

    def _pick(self, frame: int) -> None:
        self.chosen = frame
        self.accept()

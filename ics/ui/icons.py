"""선 아이콘 (Lucide, ISC 라이선스: assets/icons/LUCIDE_LICENSE.txt).

SVG 의 currentColor 를 원하는 색으로 바꿔 그린다. 같은 (이름, 색, 크기) 는 한 번만 그린다.
아이콘 파일이 없으면 빈 아이콘을 돌려준다 (화면이 깨지지 않게).
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap, QTransform

from .. import paths
from . import theme

STROKE = "1.75"  # Lucide 기본 2 보다 살짝 가늘게 (작은 크기에서 덜 무겁게)


@lru_cache(maxsize=None)
def _svg(name: str) -> bytes:
    f = paths.resource_path("assets/icons") / f"{name}.svg"
    try:
        return f.read_bytes()
    except OSError:
        return b""


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = theme.TEXT, size: int = 16, stroke: str = STROKE) -> QPixmap:
    """size 는 논리 픽셀. 화면 배율 2 까지 선명하게 2배로 그린 뒤 devicePixelRatio 를 맞춘다."""
    from PySide6.QtSvg import QSvgRenderer

    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.GlobalColor.transparent)
    data = _svg(name)
    if data:
        data = data.replace(b"currentColor", color.encode()).replace(
            b'stroke-width="2"', f'stroke-width="{stroke}"'.encode()
        )
        r = QSvgRenderer(QByteArray(data))
        if r.isValid():
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r.render(p, QRectF(0, 0, size * scale, size * scale))
            p.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(
    name: str,
    color: str = theme.TEXT,
    size: int = 16,
    on: str | None = None,
    disabled: str = theme.DIM,
    active: str | None = None,
    mirror: bool = False,
) -> QIcon:
    """on: 체크(켜짐) 상태 색, active: 마우스를 올렸을 때 색."""
    ic = QIcon()

    def pm(c: str) -> QPixmap:
        p = pixmap(name, c, size)
        if mirror:
            p = p.transformed(QTransform().scale(-1, 1))
            p.setDevicePixelRatio(2)
        return p

    ic.addPixmap(pm(color), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pm(disabled), QIcon.Mode.Disabled, QIcon.State.Off)
    ic.addPixmap(pm(active or color), QIcon.Mode.Active, QIcon.State.Off)
    if on:
        ic.addPixmap(pm(on), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(pm(on), QIcon.Mode.Active, QIcon.State.On)
        ic.addPixmap(pm(disabled), QIcon.Mode.Disabled, QIcon.State.On)
    return ic

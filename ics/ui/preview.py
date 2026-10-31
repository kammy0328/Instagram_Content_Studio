"""중앙 미리보기: 소스 보기 / 출력(9:16·4:5) 보기 / 커버 그리드 오버레이 / 드래그로 위치 조정."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..core.layout import Geometry, LayoutParams, compute_geometry, profile_grid_window
from . import theme


class PreviewWidget(QWidget):
    layoutChanged = Signal(object)  # LayoutParams (드래그 중 연속 발생)
    dragStarted = Signal()
    dragFinished = Signal()
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 200)
        self.image: QImage | None = None
        self.frame_no = 0
        self.mode = "source"  # 'source' | 'output'
        self.kind = "916"  # 출력 캔버스 종류
        self.layout = LayoutParams()
        self.disp = (1920, 1080)  # 소스 표시 크기(SAR 반영)
        self.safe_zone = False
        self.cover_grid: tuple[str, float] | None = None  # (비율 '3:4', 세로 위치)
        self.label = ""
        self._drag: QPointF | None = None
        self._drag_start_layout: LayoutParams | None = None
        self.setMouseTracking(True)

    # ---------------------------------------------------------------- API
    def set_image(self, n: int, img: QImage | None) -> None:
        self.frame_no, self.image = n, img
        self.update()

    def set_source_size(self, w: int, h: int) -> None:
        self.disp = (w, h)

    def set_canvas_mode(self) -> None:
        """이미 출력 규격으로 합성된 이미지(스틸)를 그대로 표시."""
        self.mode = "canvas"
        self.update()

    def set_output(self, enabled: bool, kind: str = "916", layout: LayoutParams | None = None) -> None:
        self.mode = "output" if enabled else "source"
        self.kind = kind
        if layout is not None:
            self.layout = layout
        self.update()

    def geometry_now(self) -> Geometry:
        return compute_geometry(self.disp[0], self.disp[1], self.kind, self.layout)

    # ---------------------------------------------------------------- 그리기
    def _canvas_rect(self, geo_w: int, geo_h: int) -> QRectF:
        m = 10
        avail = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        s = min(avail.width() / geo_w, avail.height() / geo_h)
        w, h = geo_w * s, geo_h * s
        return QRectF(avail.center().x() - w / 2, avail.center().y() - h / 2, w, h)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.C["BG"])
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self.image is None or self.image.isNull():
            p.setPen(theme.C["MUTED"])
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "영상을 불러오는 중…")
            return
        if self.mode in ("source", "canvas"):
            iw, ih = self.image.width(), self.image.height()
            r = self._canvas_rect(iw, ih)
            p.drawImage(r, self.image)
            p.setPen(QPen(theme.C["BORDER"], 1))
            p.drawRect(r)
        else:
            self._paint_output(p)
        if self.label:
            p.setPen(theme.C["MUTED"])
            p.drawText(14, 22, self.label)

    def _paint_output(self, p: QPainter) -> None:
        g = self.geometry_now()
        cr = self._canvas_rect(g.canvas_w, g.canvas_h)
        p.fillRect(cr, QColor("#000000"))
        sc = cr.width() / g.canvas_w
        iw, ih = self.image.width(), self.image.height()
        if g.mode == "fit":
            tr = QRectF(cr.x() + g.pad_x * sc, cr.y() + g.pad_y * sc, g.content_w * sc, g.content_h * sc)
            if g.crop_w:  # Fit 에서 확대하면 소스의 일부만 보인다
                kx, ky = iw / self.disp[0], ih / self.disp[1]
                p.drawImage(tr, self.image, QRectF(g.crop_x * kx, g.crop_y * ky, g.crop_w * kx, g.crop_h * ky))
            else:
                p.drawImage(tr, self.image)
        else:
            k = iw / self.disp[0]
            src = QRectF(g.crop_x * k, g.crop_y * (ih / self.disp[1]), g.crop_w * k, g.crop_h * (ih / self.disp[1]))
            p.drawImage(cr, self.image, src)
        p.setPen(QPen(theme.C["BORDER"], 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(cr)
        if self.safe_zone and self.kind == "916":
            shade = QColor(255, 90, 90, 50)
            p.fillRect(QRectF(cr.x(), cr.y(), cr.width(), cr.height() * 250 / 1920), shade)
            p.fillRect(
                QRectF(cr.x(), cr.bottom() - cr.height() * 340 / 1920, cr.width(), cr.height() * 340 / 1920), shade
            )
        if self.cover_grid:
            asp, off = self.cover_grid
            gx, gy, gw, gh = profile_grid_window(cr.width(), cr.height(), asp, off)
            gx, gy = cr.x() + gx, cr.y() + gy
            dim = QColor(0, 0, 0, 150)
            p.fillRect(QRectF(cr.x(), cr.y(), cr.width(), gy - cr.y()), dim)
            p.fillRect(QRectF(cr.x(), gy + gh, cr.width(), cr.bottom() - gy - gh), dim)
            p.fillRect(QRectF(cr.x(), gy, gx - cr.x(), gh), dim)
            p.fillRect(QRectF(gx + gw, gy, cr.right() - gx - gw, gh), dim)
            p.setPen(QPen(theme.C["ACCENT"], 2))
            p.drawRect(QRectF(gx, gy, gw, gh))
            p.setPen(theme.C["ACCENT"])
            p.drawText(QPointF(gx + 6, gy + 16), f"프로필 그리드 {asp}")
        info = f"{g.canvas_w}×{g.canvas_h} · " + (
            "원본 비율" if self.kind == "orig" else ("Fit" if g.mode == "fit" else "Fill")
        )
        if g.upscaled:
            info += f" · 확대 {g.scale:.2f}x"
        p.setPen(theme.C["MUTED"])
        p.drawText(QPointF(cr.x() + 6, cr.bottom() - 8), info)

    # ---------------------------------------------------------------- 드래그로 위치 조정
    def mousePressEvent(self, e):
        self.clicked.emit()
        if self.mode == "output" and e.button() == Qt.MouseButton.LeftButton and self.image is not None:
            self._drag = e.position()
            g = self.geometry_now()
            self._drag_geo = g
            self._drag_start_layout = LayoutParams(
                self.layout.mode, self.layout.zoom, self.layout.pos_x, self.layout.pos_y
            )
            self.dragStarted.emit()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is None or self._drag_start_layout is None:
            return
        g = self._drag_geo
        cr = self._canvas_rect(g.canvas_w, g.canvas_h)
        sc = cr.width() / g.canvas_w
        dx = (e.position().x() - self._drag.x()) / sc
        dy = (e.position().y() - self._drag.y()) / sc
        L = self._drag_start_layout
        new = LayoutParams(L.mode, L.zoom, L.pos_x, L.pos_y)
        k = g.scale
        rx = max(1, self.disp[0] - g.crop_w)
        ry = max(1, self.disp[1] - g.crop_h)
        # 축마다: 소스가 잘리는 축은 크롭 창을 옮기고, 여백이 남는 축(Fit)은 내용을 옮긴다
        if self.disp[0] > g.crop_w:
            new.pos_x = min(1, max(0, (g.crop_x - dx / k) / rx))
        elif g.mode == "fit" and g.canvas_w > g.content_w:
            new.pos_x = min(1, max(0, (g.pad_x + dx) / (g.canvas_w - g.content_w)))
        if self.disp[1] > g.crop_h:
            new.pos_y = min(1, max(0, (g.crop_y - dy / k) / ry))
        elif g.mode == "fit" and g.canvas_h > g.content_h:
            new.pos_y = min(1, max(0, (g.pad_y + dy) / (g.canvas_h - g.content_h)))
        self.layout = new
        self.update()
        self.layoutChanged.emit(new)

    def mouseReleaseEvent(self, _e):
        if self._drag is not None:
            self._drag = None
            self.unsetCursor()
            self.dragFinished.emit()

"""중앙 미리보기: 소스 보기 / 출력(9:16·4:5) 보기 / 커버 그리드 오버레이."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..core.layout import Geometry, LayoutParams, compute_geometry, profile_grid_window, source_bounds
from . import theme


class PreviewWidget(QWidget):
    layoutChanged = Signal(object)  # LayoutParams (드래그 중 연속 발생)
    dragStarted = Signal()
    dragFinished = Signal()
    clicked = Signal()
    hDragged = Signal(float)  # Fill 9:16: 끌고 있는 동안의 가로 위치 (0..1)
    hDragFinished = Signal(float)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 160)
        self.image: QImage | None = None
        self.frame_no = 0
        self.mode = "source"  # 'source' | 'output'
        self.kind = "916"  # 출력 캔버스 종류
        self.layout = LayoutParams()
        self.disp = (1920, 1080)  # 소스 표시 크기(SAR 반영)
        self.debug_dets: tuple = (None, [])  # 원본 보기용 검출 결과: (표본 프레임, [(x1, y1, x2, y2, 얼굴?)]) 표시 좌표
        self.safe_zone = False
        self.cover_grid: tuple[str, float] | None = None  # (비율 '3:4', 세로 위치)
        self.label = ""
        self.overlay_img: QImage | None = None  # 출력 보기에서 캔버스 위에 겹쳐 그리는 오버레이 (STORY 로고/제목)
        self.bg_dim: int | None = None  # STORY: 여백을 흐린 영상으로 채울 때 어둡게 하는 정도(%), None 이면 검정 여백
        self.show_info = False  # 출력 크기 · Fit/Fill 글자 (표시 안 함)
        self.h_drag_enabled = False  # STORY/REELS Fill 출력 보기에서만: 좌우로 끌어 지금 컷의 가로 위치 조정
        self._drag = None  # (시작 마우스 x, 시작 crop_x, 이동 가능 폭(소스 px), 소스 px / 위젯 px)
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
        # 카드(둥근 모서리) 안의 무대: 위쪽 모서리만 둥글게 (아래는 재생 줄과 이어진다)
        p.fillRect(self.rect(), theme.C["PANEL"])
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.C["BG"])
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0, 0, 0, 20), 11, 11)
        p.setBrush(
            Qt.BrushStyle.NoBrush
        )  # 아래 테두리(drawRect)가 바탕색으로 영상을 덮지 않게 (원본 보기가 검게 나오던 문제)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self.image is None or self.image.isNull():
            p.setPen(theme.C["MUTED"])
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "영상을 불러오는 중…")
            return
        if self.mode in ("source", "canvas"):
            iw, ih = self.image.width(), self.image.height()
            r = self._canvas_rect(iw, ih)
            p.drawImage(r, self.image)
            if self.mode == "source":
                self._paint_source_crop(p, r)
            p.setPen(QPen(theme.C["BORDER"], 1))
            p.drawRect(r)
        else:
            self._paint_output(p)
        if self.label:  # 작은 알약 모양 안내 (예: 원본 보기, 릴스 커버 미리보기)
            f = p.font()
            f.setPixelSize(theme.FS_SMALL)
            p.setFont(f)
            fm = p.fontMetrics()
            r = QRectF(12, 12, fm.horizontalAdvance(self.label) + 18, fm.height() + 8)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(20, 20, 23, 220))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            p.setPen(theme.C["TEXT"])
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.label)

    def _paint_source_crop(self, p: QPainter, r: QRectF) -> None:
        """원본 보기에서 Fill 로 잘려 나갈 부분을 어둡게, 남는 창은 흰 선으로 보여 준다."""
        if self.layout is None or self.layout.mode != "fill" or not self.disp[0]:
            return
        try:
            g = self.geometry_now()
        except Exception:  # noqa: BLE001
            return
        if not g.crop_w:
            return
        k = r.width() / self.disp[0]
        win = QRectF(r.x() + g.crop_x * k, r.y() + g.crop_y * k, g.crop_w * k, g.crop_h * k)
        dim = QColor(0, 0, 0, 150)
        p.fillRect(QRectF(r.x(), r.y(), r.width(), win.y() - r.y()), dim)
        p.fillRect(QRectF(r.x(), win.bottom(), r.width(), r.bottom() - win.bottom()), dim)
        p.fillRect(QRectF(r.x(), win.y(), win.x() - r.x(), win.height()), dim)
        p.fillRect(QRectF(win.right(), win.y(), r.right() - win.right(), win.height()), dim)
        p.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
        p.drawRect(win)
        self._paint_dets(p, r, k)

    def _paint_dets(self, p: QPainter, r: QRectF, k: float) -> None:
        """자동 맞춤이 본 것: 얼굴은 초록 실선(가운데 점 = 맞춘 기준), 얼굴을 못 찾은 사람은 머리 자리 점선."""
        frame, dets = self.debug_dets if self.debug_dets else (None, [])
        if not dets:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        face_c, head_c = QColor("#4cd08b"), QColor(255, 255, 255, 150)
        for x1, y1, x2, y2, face, *rest in dets:
            yaw = float(rest[0]) if rest else 0.0
            box = QRectF(r.x() + x1 * k, r.y() + y1 * k, (x2 - x1) * k, (y2 - y1) * k)
            if face:
                p.setPen(QPen(face_c, 1.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(box)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(face_c)
                c = box.center()
                p.drawEllipse(QPointF(c.x(), c.y()), 2.2, 2.2)
                if abs(yaw) >= 0.25:  # 보는 방향 (여백을 두는 쪽)
                    tip = QPointF(c.x() + yaw * box.width() * 0.9, c.y())
                    p.setPen(QPen(face_c, 1.6))
                    p.drawLine(c, tip)
                    s_ = 4 if yaw > 0 else -4
                    p.drawLine(tip, QPointF(tip.x() - s_, tip.y() - 3))
                    p.drawLine(tip, QPointF(tip.x() - s_, tip.y() + 3))
            else:
                pen = QPen(head_c, 1.2, Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(box)
        f = p.font()
        f.setPixelSize(theme.FS_SMALL)
        p.setFont(f)
        text = (
            f"초록: 얼굴 (점 = 기준, 화살표 = 보는 방향) · 점선: 얼굴을 못 찾은 사람의 머리 자리 · 분석 표본 f{frame}"
        )
        fm = p.fontMetrics()
        tr = QRectF(r.x() + 8, r.bottom() - fm.height() - 12, fm.horizontalAdvance(text) + 16, fm.height() + 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(tr, 6, 6)
        p.setPen(QColor(theme.TEXT))
        p.drawText(tr, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()

    def _paint_blur_bg(self, p: QPainter, cr: QRectF) -> None:
        """영상을 캔버스에 가득 차게 잘라 아주 작게 줄였다 키워(=흐림) 그리고, 최종 렌더링처럼 어둡게 한다."""
        image = self.image
        crop = source_bounds(*self.disp, self.layout)
        if crop:
            x, y, w, h = crop
            kx, ky = image.width() / self.disp[0], image.height() / self.disp[1]
            image = image.copy(int(x * kx), int(y * ky), max(1, int(w * kx)), max(1, int(h * ky)))
        iw, ih = image.width(), image.height()
        ar = cr.width() / cr.height()
        if iw / ih > ar:
            sw, sh = ih * ar, ih
        else:
            sw, sh = iw, iw / ar
        src = image.copy(int((iw - sw) / 2), int((ih - sh) / 2), max(1, int(sw)), max(1, int(sh)))
        # 아주 작게 줄인 뒤 두 단계로 키워 마름모/블록 무늬 없이 부드럽게 퍼지게 한다
        sm = Qt.TransformationMode.SmoothTransformation
        im = src.scaled(32, max(2, int(32 / ar)), Qt.AspectRatioMode.IgnoreAspectRatio, sm)
        im = im.scaled(im.width() * 4, im.height() * 4, Qt.AspectRatioMode.IgnoreAspectRatio, sm)
        p.drawImage(cr, im)
        p.fillRect(cr, QColor(0, 0, 0, int(255 * max(0, min(90, self.bg_dim)) / 100)))

    def _paint_output(self, p: QPainter) -> None:
        g = self.geometry_now()
        cr = self._canvas_rect(g.canvas_w, g.canvas_h)
        p.fillRect(cr, QColor("#000000"))
        sc = cr.width() / g.canvas_w
        iw, ih = self.image.width(), self.image.height()
        if g.mode == "fit" and self.bg_dim is not None and self.kind == "916" and g.content_h < g.canvas_h:
            self._paint_blur_bg(p, cr)
        if g.mode == "fit":
            tr = QRectF(cr.x() + g.pad_x * sc, cr.y() + g.pad_y * sc, g.content_w * sc, g.content_h * sc)
            if self.bg_dim is not None:  # 배경 위에 올릴 때는 가장자리가 반투명으로 번지지 않게 화면 픽셀에 맞춘다
                tr = QRectF(tr.toAlignedRect())
            if g.crop_w:  # Fit 에서 확대하면 소스의 일부만 보인다
                kx, ky = iw / self.disp[0], ih / self.disp[1]
                p.drawImage(tr, self.image, QRectF(g.crop_x * kx, g.crop_y * ky, g.crop_w * kx, g.crop_h * ky))
            else:
                p.drawImage(tr, self.image)
        else:
            k = iw / self.disp[0]
            src = QRectF(g.crop_x * k, g.crop_y * (ih / self.disp[1]), g.crop_w * k, g.crop_h * (ih / self.disp[1]))
            p.drawImage(cr, self.image, src)
        if self.overlay_img is not None and not self.overlay_img.isNull() and self.kind == "916":
            p.drawImage(cr, self.overlay_img)
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
        if self.show_info:
            info = f"{g.canvas_w}×{g.canvas_h} · " + (
                "원본 비율" if self.kind == "orig" else ("Fit" if g.mode == "fit" else "Fill")
            )
            if g.upscaled:
                info += f" · 확대 {g.scale:.2f}x"
            p.setPen(theme.C["MUTED"])
            p.drawText(QPointF(cr.x() + 6, cr.bottom() - 8), info)

    # ---------------------------------------------------------------- 마우스
    def _can_h_drag(self) -> bool:
        if not (self.h_drag_enabled and self.mode == "output" and self.layout.mode == "fill"):
            return False
        g = self.geometry_now()
        bx, _by, bw, _bh = source_bounds(self.disp[0], self.disp[1], self.layout) or (0, 0, *self.disp)
        return bw - g.crop_w > 2

    def mousePressEvent(self, e):
        """Fill 9:16 출력 보기에서는 좌우로만 끌 수 있다 (세로 위치와 확대는 바뀌지 않음). 그 밖에는 위치가 바뀌지 않는다."""
        self.clicked.emit()
        if e.button() == Qt.MouseButton.LeftButton and self._can_h_drag():
            g = self.geometry_now()
            bx, _by, bw, _bh = source_bounds(self.disp[0], self.disp[1], self.layout) or (0, 0, *self.disp)
            cr = self._canvas_rect(g.canvas_w, g.canvas_h)
            self._drag = (e.position().x(), g.crop_x - bx, bw - g.crop_w, g.crop_w / max(1.0, cr.width()))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept()

    def _drag_pos(self, x: float) -> float:
        x0, c0, span, k = self._drag
        return min(1.0, max(0.0, (c0 - (x - x0) * k) / span))

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.hDragged.emit(self._drag_pos(e.position().x()))
            e.accept()
            return
        self.setCursor(Qt.CursorShape.OpenHandCursor if self._can_h_drag() else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            pos = self._drag_pos(e.position().x())
            moved = abs(e.position().x() - self._drag[0]) >= 2
            self._drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if moved:
                self.hDragFinished.emit(pos)
            e.accept()

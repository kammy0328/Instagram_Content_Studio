"""STORY 오버레이(로고, 제목 텍스트) 레이아웃과 투명 PNG 생성.

규칙: 영상 위에는 절대 그리지 않는다. 출력 캔버스에서 영상(Fit 의 content)이 차지하지 않는 검정 여백(위/아래)에만 놓는다.
여백이 없거나(Fill, 영상이 캔버스를 가득 채움) 너무 작으면 그 요소는 그리지 않고 이유를 돌려준다.
미리보기와 최종 렌더링이 같은 함수를 쓰므로 보이는 그대로 출력된다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter

from .. import paths
from ..core.layout import Geometry
from ..core.model import StoryOverlay

MIN_MARGIN_PX = 90  # 이보다 작은 여백에는 아무것도 놓지 않는다 (1080 폭 기준)
FONT_FILES = ("Pretendard-Regular.ttf", "Pretendard-Bold.ttf")
DEFAULT_FONT = "Pretendard"
_lock = threading.Lock()
_fonts_ready = False


def ensure_fonts() -> None:
    """번들 폰트(Pretendard, 한글+영문)를 Qt 에 등록한다. QGuiApplication 이 있어야 한다."""
    global _fonts_ready
    with _lock:
        if _fonts_ready:
            return
        for name in FONT_FILES:
            f = paths.resource_path("assets/fonts") / name
            if f.is_file():
                QFontDatabase.addApplicationFont(str(f))
        _fonts_ready = True


@dataclass
class Placed:
    kind: str  # 'logo' | 'text'
    rect: QRectF  # 캔버스 픽셀 좌표
    text: str = ""
    font_px: float = 0.0


@dataclass
class OverlayResult:
    items: list[Placed]
    warnings: list[str]

    @property
    def empty(self) -> bool:
        return not self.items


def margins(geo: Geometry) -> tuple[float, float]:
    """(위 여백 높이, 아래 여백 높이). 영상이 캔버스를 채우는 Fill 은 (0, 0)."""
    if geo.mode != "fit":
        return 0.0, 0.0
    top = float(geo.pad_y)
    bottom = float(geo.canvas_h - geo.pad_y - geo.content_h)
    return max(0.0, top), max(0.0, bottom)


def active(ov: StoryOverlay, title: str) -> bool:
    return (ov.logo_on and bool(ov.logo_path)) or (ov.text_on and bool((ov.text or title).strip()))


def _logo_size(path: str) -> tuple[int, int] | None:
    img = QImage(path)
    return None if img.isNull() else (img.width(), img.height())


def _fit_text(text: str, family: str, bold: bool, px: float, box_w: float, box_h: float):
    """상자에 들어가도록 글자 크기를 줄여 가며(최소 50%) 줄바꿈 후 크기를 정한다. 안 들어가면 None."""
    size = px
    while size >= px * 0.5:
        font = QFont(family)
        font.setPixelSize(max(6, int(round(size))))
        font.setBold(bold)
        fm = QFontMetricsF(font)
        r = fm.boundingRect(
            QRectF(0, 0, box_w, 10_000), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), text
        )
        if r.height() <= box_h:
            return size
        size *= 0.94
    return None


def layout(ov: StoryOverlay, geo: Geometry, title: str) -> OverlayResult:
    ensure_fonts()
    items: list[Placed] = []
    warns: list[str] = []
    scale = geo.canvas_w / 1080.0
    top_h, bottom_h = margins(geo)
    areas = {"top": (0.0, top_h), "bottom": (geo.canvas_h - bottom_h, bottom_h)}
    want = []
    if ov.logo_on and ov.logo_path:
        want.append(("logo", ov.logo_pos))
    text = (ov.text or title or "").strip()
    if ov.text_on and text:
        want.append(("text", ov.text_pos))
    for pos in ("top", "bottom"):
        group = [k for k, p in want if p == pos]
        y0, h = areas[pos]
        label = "위" if pos == "top" else "아래"
        if not group:
            continue
        if h < MIN_MARGIN_PX * scale:
            what = "·".join("로고" if k == "logo" else "제목" for k in group)
            warns.append(f"{what}: 영상 {label} 검정 여백이 부족해 표시하지 않습니다 (영상 위에는 올리지 않습니다).")
            continue
        # 같은 여백에 둘 다 있으면 로고 60% / 텍스트 40%
        if len(group) == 2:
            parts = {"logo": (y0, h * 0.6), "text": (y0 + h * 0.6, h * 0.4)}
        else:
            parts = {group[0]: (y0, h)}
        for kind, (py, ph) in parts.items():
            pad = ph * 0.12
            bw, bh = geo.canvas_w * 0.9, ph - 2 * pad
            if kind == "logo":
                sz = _logo_size(ov.logo_path)
                if sz is None:
                    warns.append("로고: 이미지 파일을 열 수 없습니다.")
                    continue
                w = min(bw, geo.canvas_w * ov.logo_size / 100.0)
                hh = w * sz[1] / sz[0]
                if hh > bh:
                    w, hh = w * bh / hh, bh
                items.append(Placed("logo", QRectF((geo.canvas_w - w) / 2, py + (ph - hh) / 2, w, hh)))
            else:
                s = _fit_text(text, ov.text_font, ov.text_bold, ov.text_size * scale, bw, bh)
                if s is None:
                    warns.append("제목: 글자가 여백에 들어가지 않아 표시하지 않습니다 (글자 크기를 줄이세요).")
                    continue
                items.append(Placed("text", QRectF((geo.canvas_w - bw) / 2, py + pad, bw, bh), text, s))
    return OverlayResult(items, warns)


def render(ov: StoryOverlay, geo: Geometry, title: str) -> tuple[QImage | None, OverlayResult]:
    """캔버스 크기의 투명 ARGB 이미지 (그릴 것이 없으면 None)."""
    res = layout(ov, geo, title)
    if res.empty:
        return None, res
    img = QImage(geo.canvas_w, geo.canvas_h, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHints(
        QPainter.RenderHint.Antialiasing
        | QPainter.RenderHint.SmoothPixmapTransform
        | QPainter.RenderHint.TextAntialiasing
    )
    for it in res.items:
        if it.kind == "logo":
            src = QImage(ov.logo_path)
            p.drawImage(it.rect, src)
        else:
            font = QFont(ov.text_font)
            font.setPixelSize(max(6, int(round(it.font_px))))
            font.setBold(ov.text_bold)
            p.setFont(font)
            p.setPen(QColor(ov.text_color))
            p.drawText(it.rect, int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), it.text)
    p.end()
    return img, res


def save_png(ov: StoryOverlay, geo: Geometry, title: str, out: Path) -> tuple[str | None, OverlayResult]:
    img, res = render(ov, geo, title)
    if img is None:
        return None, res
    out.parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(out), "PNG"):
        raise OSError(f"오버레이 이미지를 저장하지 못했습니다: {out}")
    return str(out), res


def trim_logo(src: str, dst: Path) -> Path:
    """로고의 투명 여백을 잘라 dst 로 저장한다 (불투명 영역이 없으면 원본 그대로 복사)."""
    img = QImage(src).convertToFormat(QImage.Format.Format_ARGB32)
    if img.isNull():
        raise ValueError("이미지 파일을 열 수 없습니다.")
    import numpy as np

    w, h = img.width(), img.height()
    alpha = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(h, img.bytesPerLine() // 4, 4)[:, :w, 3]
    ys, xs = np.nonzero(alpha > 8)
    x0, y0, x1, y1 = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else (0, 0, w - 1, h - 1)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if x1 >= x0 and (x0, y0, x1, y1) != (0, 0, w - 1, h - 1):
        img = img.copy(x0, y0, x1 - x0 + 1, y1 - y0 + 1)
    img.save(str(dst), "PNG")
    return dst

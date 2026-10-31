"""프레임 정확도 타임라인: 눈금자(클릭/드래그로 재생 헤드 이동)·STORY/REELS/FEED 레인.

컷 레인과 컷 경계 드래그는 두지 않는다. 컷 보기/선택/병합/나누기는 컷 스트립이 맡는다."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QScrollBar, QWidget

from ..core.timecode import frame_to_tc
from . import theme

RULER_H, SEL_H, FEED_H = 24, 18, 26
Y_RULER = 0
Y_STORY = Y_RULER + RULER_H + 4
Y_REELS = Y_STORY + SEL_H + 2
Y_FEED = Y_REELS + SEL_H + 2
TOTAL_H = Y_FEED + FEED_H + 14
HIT = 6

NICE_STEPS = [1, 2, 5, 10, 15, 24, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200, 18000, 36000, 72000, 180000]


class TimelineWidget(QWidget):
    playheadRequested = Signal(int)
    feedItemClicked = Signal(str)
    status = Signal(str)

    def __init__(self, project, thumbs):
        super().__init__()
        self.project, self.thumbs = project, thumbs
        self.setMinimumHeight(TOTAL_H + 14)
        self.setMouseTracking(True)
        self.ppf = 1.0
        self.offset = 0.0
        self.playhead = 0
        self.snap_on = True
        self.active = "story"
        self.sel_feed: str | None = None
        self.loop_range: tuple[int, int] | None = None
        self._drag: dict | None = None
        self.sb = QScrollBar(Qt.Orientation.Horizontal, self)
        self.sb.valueChanged.connect(self._on_scroll)
        self._fit_pending = True
        thumbs.thumbReady.connect(lambda _n: self.update())

    # ============================================================ 좌표/줌
    @property
    def count(self) -> int:
        return max(1, self.project.count)

    def x_of(self, f: float) -> float:
        return (f - self.offset) * self.ppf

    def f_of(self, x: float) -> float:
        return self.offset + x / self.ppf

    def view_frames(self) -> float:
        return self.width() / self.ppf

    def min_ppf(self) -> float:
        return max(0.002, (self.width() - 4) / self.count)

    def fit(self) -> None:
        self.ppf = self.min_ppf()
        self.offset = 0
        self._sync_scroll()
        self.update()

    def zoom(self, factor: float, anchor_x: float | None = None) -> None:
        ax = self.width() / 2 if anchor_x is None else anchor_x
        af = self.f_of(ax)
        self.ppf = min(80.0, max(self.min_ppf(), self.ppf * factor))
        self.offset = af - ax / self.ppf
        self._clamp()
        self._sync_scroll()
        self.update()

    def zoom_to_range(self, a: int, b: int) -> None:
        span = max(10, b - a)
        self.ppf = min(80.0, max(self.min_ppf(), (self.width() - 40) / span))
        self.offset = a - 20 / self.ppf
        self._clamp()
        self._sync_scroll()
        self.update()

    def _clamp(self) -> None:
        self.offset = max(0.0, min(self.offset, max(0.0, self.count - self.view_frames() + 20 / self.ppf)))

    def _sync_scroll(self) -> None:
        self.sb.blockSignals(True)
        self.sb.setRange(0, max(0, int((self.count - self.view_frames()) * 1000)))
        self.sb.setPageStep(int(self.view_frames() * 1000))
        self.sb.setValue(int(self.offset * 1000))
        self.sb.blockSignals(False)

    def _on_scroll(self, v: int) -> None:
        self.offset = v / 1000.0
        self.update()

    def ensure_visible(self, f: int, center: bool = False) -> None:
        if self.x_of(f) < 0 or self.x_of(f) > self.width() - 20:
            self.offset = f - (self.view_frames() / 2 if center else 20 / self.ppf)
            self._clamp()
            self._sync_scroll()

    def set_playhead(self, f: int, follow: bool = False) -> None:
        self.playhead = f
        if follow:
            self.ensure_visible(f)
        self.update()

    def resizeEvent(self, _e):
        self.sb.setGeometry(0, self.height() - 12, self.width(), 12)
        if self._fit_pending and self.project.count:
            self._fit_pending = False
            self.fit()
        else:
            self.ppf = max(self.ppf, self.min_ppf())
            self._clamp()
            self._sync_scroll()

    def showEvent(self, e):
        super().showEvent(e)
        if self._fit_pending and self.project.count:
            self._fit_pending = False
            self.fit()

    # ============================================================ 스냅
    def _snap_targets(self, exclude: set[tuple] | None = None) -> list[int]:
        st = self.project.state
        t = [0, self.count, self.playhead]
        for i, c in enumerate(st.cuts):
            if exclude and ("cut", i, "start") in exclude:
                pass
            else:
                t.append(c.start)
            if not (exclude and ("cut", i, "end") in exclude):
                t.append(c.end)
        for sel in (st.story, st.reels):
            if sel.enabled:
                t += [sel.start, sel.end]
        for it in st.feed:
            t += [it.start, it.end] if it.kind == "video" else [it.start]
        return t

    def snap(self, f: float, exclude=None, force: bool = False) -> int:
        fi = int(round(f))
        if not self.snap_on and not force:
            return fi
        best, bd = fi, HIT + 3
        for t in self._snap_targets(exclude):
            d = abs(self.x_of(t) - self.x_of(f))
            if d < bd:
                best, bd = t, d
        return best

    # ============================================================ 그리기
    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.C["PANEL"])
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_ruler(p)
        self._paint_ranges(p)
        self._paint_feed(p)
        self._paint_playhead(p)

    def _paint_ruler(self, p: QPainter) -> None:
        p.fillRect(0, Y_RULER, self.width(), RULER_H, theme.C["PANEL2"])
        fps = self.project.fps
        step = next((s for s in NICE_STEPS if s * self.ppf >= 74), NICE_STEPS[-1])
        sub = max(1, step // 5) if step >= 5 else 1
        f0 = int(max(0, self.f_of(0)) // step * step)
        f1 = int(self.f_of(self.width())) + step
        p.setFont(QFont(self.font().family(), 8))
        for f in range(f0, min(f1, self.count + step), step):
            x = self.x_of(f)
            p.setPen(theme.C["MUTED"])
            p.drawLine(QPointF(x, Y_RULER + 10), QPointF(x, Y_RULER + RULER_H))
            p.drawText(QPointF(x + 3, Y_RULER + 10), frame_to_tc(f, fps))
        if sub * self.ppf >= 6:
            p.setPen(QPen(theme.C["BORDER"], 1))
            for f in range(f0, min(f1, self.count + step), sub):
                x = self.x_of(f)
                p.drawLine(QPointF(x, Y_RULER + 17), QPointF(x, Y_RULER + RULER_H))

    def _paint_ranges(self, p: QPainter) -> None:
        st = self.project.state
        for sel, y, name, col in ((st.story, Y_STORY, "STORY", theme.ACCENT2), (st.reels, Y_REELS, "REELS", theme.OK)):
            p.fillRect(0, y, self.width(), SEL_H, QColor("#14161b"))
            if not sel.enabled:
                p.setPen(theme.C["MUTED"])
                p.setFont(QFont(self.font().family(), 8))
                p.drawText(QPointF(6, y + 13), f"{name} 미지정")
                continue
            x0, x1 = self.x_of(sel.start), self.x_of(sel.end)
            r = QRectF(x0, y + 1, max(3.0, x1 - x0), SEL_H - 2)
            c = QColor(col)
            c.setAlpha(190 if self.active == name.lower() else 110)
            p.fillRect(r, c)
            p.setPen(QPen(QColor(col), 2))
            p.drawRect(r)
            p.setPen(QColor("#0b0d11"))
            p.setFont(QFont(self.font().family(), 8, QFont.Weight.Bold))
            p.drawText(
                r.adjusted(5, 0, 0, 0),
                Qt.AlignmentFlag.AlignVCenter,
                f"{name} {self.project.seconds(sel.start, sel.end):.2f}s",
            )

    def _paint_feed(self, p: QPainter) -> None:
        p.fillRect(0, Y_FEED, self.width(), FEED_H, QColor("#14161b"))
        items = self.project.feed_ordered()
        order = {it.id: n + 1 for n, it in enumerate([i for i in items if i.included])}
        p.setFont(QFont(self.font().family(), 8, QFont.Weight.Bold))
        for it in items:
            col = QColor(theme.ACCENT if it.included else "#5b6171")
            selected = it.id == self.sel_feed
            if it.kind == "video":
                x0, x1 = self.x_of(it.start), self.x_of(it.end)
                r = QRectF(x0, Y_FEED + 2, max(4.0, x1 - x0), FEED_H - 4)
                c = QColor(col)
                c.setAlpha(150 if it.included else 60)
                p.fillRect(r, c)
                p.setPen(QPen(QColor(theme.WARN) if selected else col, 2 if selected else 1))
                p.drawRect(r)
                if it.id in order:
                    p.setPen(QColor("#0b0d11"))
                    p.drawText(r.adjusted(4, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, f"{order[it.id]}▶")
            else:
                x = self.x_of(it.start)
                cy = Y_FEED + FEED_H / 2
                poly = QPolygonF([QPointF(x, cy - 9), QPointF(x + 7, cy), QPointF(x, cy + 9), QPointF(x - 7, cy)])
                p.setBrush(QBrush(col) if it.included else Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(theme.WARN) if selected else col, 2))
                p.drawPolygon(poly)
                p.setBrush(Qt.BrushStyle.NoBrush)
                if it.id in order:
                    p.setPen(theme.C["TEXT"])
                    p.drawText(QPointF(x + 9, cy + 4), str(order[it.id]))

    def _paint_playhead(self, p: QPainter) -> None:
        x = self.x_of(self.playhead)
        if self.loop_range:
            r = QRectF(
                self.x_of(self.loop_range[0]),
                Y_RULER,
                self.x_of(self.loop_range[1]) - self.x_of(self.loop_range[0]),
                RULER_H,
            )
            c = QColor(theme.ACCENT)
            c.setAlpha(60)
            p.fillRect(r, c)
        col = QColor("#ff4d5e")
        p.setPen(QPen(col, 1.5))
        p.drawLine(QPointF(x, Y_RULER), QPointF(x, TOTAL_H))
        p.setBrush(QBrush(col))
        p.drawPolygon(QPolygonF([QPointF(x - 6, Y_RULER), QPointF(x + 6, Y_RULER), QPointF(x, Y_RULER + 9)]))
        p.setBrush(Qt.BrushStyle.NoBrush)

    # ============================================================ 마우스
    def _range_hit(self, sel, x: float) -> str | None:
        if not sel.enabled:
            return None
        x0, x1 = self.x_of(sel.start), self.x_of(sel.end)
        if abs(x - x0) <= HIT:
            return "start"
        if abs(x - x1) <= HIT:
            return "end"
        if x0 < x < x1:
            return "move"
        return None

    def _feed_hit(self, x: float):
        best = None
        for it in self.project.feed_ordered():
            if it.kind == "still":
                if abs(self.x_of(it.start) - x) <= 9:
                    return it, "move"
            else:
                x0, x1 = self.x_of(it.start), self.x_of(it.end)
                if abs(x - x0) <= HIT:
                    best = (it, "start")
                elif abs(x - x1) <= HIT:
                    best = (it, "end")
                elif x0 < x < x1 and best is None:
                    best = (it, "move")
        return best

    def mousePressEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if e.button() != Qt.MouseButton.LeftButton or self.project.count == 0:
            return
        if y < Y_STORY - 2:
            self._drag = {"kind": "scrub"}
            self._scrub(x)
            return
        st = self.project.state
        for sel, ylane, which in ((st.story, Y_STORY, "story"), (st.reels, Y_REELS, "reels")):
            if ylane <= y < ylane + SEL_H:
                h = self._range_hit(sel, x)
                if h:
                    self.project.begin_group(f"{which.upper()} 구간 조정")
                    self._drag = {
                        "kind": "range",
                        "which": which,
                        "handle": h,
                        "f0": self.f_of(x),
                        "s0": sel.start,
                        "e0": sel.end,
                    }
                    self.active = which
                return
        if Y_FEED <= y < Y_FEED + FEED_H:
            hit = self._feed_hit(x)
            if hit:
                it, handle = hit
                self.sel_feed = it.id
                self.feedItemClicked.emit(it.id)
                self.project.begin_group("피드 구간 조정")
                self._drag = {
                    "kind": "feed",
                    "id": it.id,
                    "handle": handle,
                    "f0": self.f_of(x),
                    "s0": it.start,
                    "e0": it.end,
                }
                self.update()
            return

    def _scrub(self, x: float) -> None:
        f = max(0, min(self.count - 1, int(round(self.f_of(x)))))
        if self.snap_on:
            f = max(0, min(self.count - 1, self.snap(f)))
        self.playhead = f
        self.playheadRequested.emit(f)
        self.update()

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        d = self._drag
        if d is None:
            self._update_hover(x, y)
            return
        k = d["kind"]
        if k == "scrub":
            self._scrub(x)
        elif k == "range":
            self._drag_range(d, x)
        elif k == "feed":
            self._drag_feed(d, x)

    def _drag_range(self, d, x):
        which = d["which"]
        n = self.project.max_len_frames(which)
        s0, e0 = d["s0"], d["e0"]
        f = self.snap(self.f_of(x))
        if d["handle"] == "start":
            s, e = min(f, e0 - 1), e0
            if e - s > n:
                s = e - n
        elif d["handle"] == "end":
            s, e = s0, max(f, s0 + 1)
            if e - s > n:
                e = s + n
        else:
            delta = int(round(self.f_of(x) - d["f0"]))
            s = max(0, min(self.count - (e0 - s0), s0 + delta))
            s = self.snap(s) if self.snap_on else s
            e = s + (e0 - s0)
            if e > self.count:
                e = self.count
                s = e - (e0 - s0)
        self.project.set_range(which, max(0, s), min(self.count, e))
        self.update()

    def _drag_feed(self, d, x):
        it = self.project.get_feed(d["id"])
        if it is None:
            return
        f = self.snap(self.f_of(x))
        s0, e0 = d["s0"], d["e0"]
        if it.kind == "still":
            s, e = f, f + 1
        elif d["handle"] == "start":
            s, e = min(f, e0 - 1), e0
        elif d["handle"] == "end":
            s, e = s0, max(f, s0 + 1)
        else:
            delta = int(round(self.f_of(x) - d["f0"]))
            s = max(0, min(self.count - (e0 - s0), s0 + delta))
            e = s + (e0 - s0)
        self.project.update_feed_range(it.id, max(0, s), min(self.count, e))
        self.update()

    def mouseReleaseEvent(self, e):
        d, self._drag = self._drag, None
        if d and d["kind"] in ("range", "feed"):
            self.project.end_group()
            self.status.emit("")
        self.update()

    def _update_hover(self, x: float, y: float) -> None:
        cursor = Qt.CursorShape.ArrowCursor
        st = self.project.state
        for sel, ylane in ((st.story, Y_STORY), (st.reels, Y_REELS)):
            if ylane <= y < ylane + SEL_H:
                h = self._range_hit(sel, x)
                cursor = (
                    Qt.CursorShape.SplitHCursor
                    if h in ("start", "end")
                    else (Qt.CursorShape.SizeAllCursor if h == "move" else cursor)
                )
        if Y_FEED <= y < Y_FEED + FEED_H:
            h = self._feed_hit(x)
            if h:
                cursor = Qt.CursorShape.SplitHCursor if h[1] in ("start", "end") else Qt.CursorShape.SizeAllCursor
        self.setCursor(cursor)

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.25 if dy > 0 else 0.8, e.position().x())
        else:
            self.offset -= (dy or e.angleDelta().x()) / 120.0 * 60 / self.ppf
            self._clamp()
            self._sync_scroll()
            self.update()

"""컷 스트립: 컷마다 같은 크기의 타일(썸네일 + 번호 + 길이).

타임라인의 컷 레인은 길이에 비례해서 1프레임 컷은 번호조차 보이지 않는다. 이 목록은 길이와 무관하게 모든 컷을 같은 크기로
보여 주고, 컷을 눈으로 알아볼 수 있게 썸네일을 붙인다. 선택(클릭, Shift 범위, Ctrl 토글)은 타임라인과 양방향으로 동기화된다.
컷 정보(XML)는 프로젝트 전체에서 하나이므로 STORY/REELS/FEED 어느 탭에서나 같은 목록이 보인다."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyledItemDelegate,
    QWidget,
)

from ..core import cuts as cuts_mod
from . import theme

TILE_W, TILE_H = 140, 104  # 모든 컷 타일의 고정 크기 (컷 길이와 무관)
PAD = 4
STRIP_H = TILE_H + 22  # 가로 스크롤바 포함 높이
DATA = Qt.ItemDataRole.UserRole


def representative_frame(start: int, end: int) -> int:
    """컷을 대표하는 프레임: 첫 프레임은 전환 효과가 섞일 수 있어 컷의 가운데 프레임을 쓴다 (1프레임 컷이면 그 프레임)."""
    return start + max(0, (end - start - 1) // 2)


class _TileDelegate(QStyledItemDelegate):
    def __init__(self, strip: "CutStrip"):
        super().__init__(strip)
        self.strip = strip
        self._f_badge = QFont()
        self._f_badge.setBold(True)
        self._f_badge.setPointSize(11)
        self._f_small = QFont()
        self._f_small.setPointSize(9)

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(TILE_W, TILE_H)

    def paint(self, p: QPainter, option, index) -> None:
        d = index.data(DATA)
        if not d:
            return
        selected = self.strip.isSelected(index)
        r = QRectF(option.rect).adjusted(PAD, PAD, -PAD, -PAD)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fill = QColor(theme.ACCENT if selected else theme.PANEL2)
        if selected:
            fill.setAlpha(50)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(r, 5, 5)
        # 썸네일 (가로세로 비율 유지)
        th = QRectF(r.x() + 2, r.y() + 2, r.width() - 4, r.height() - 4 - 22)
        p.fillRect(th, QColor("#0c0c0c"))
        img = self.strip.thumb(d["rep"])
        rf = self.strip.reframe_info(d["start"], d["end"]) if self.strip.reframe_info else None
        if img is not None and img.height() > 0:
            sc = min(th.width() / img.width(), th.height() / img.height())
            w, h = img.width() * sc, img.height() * sc
            ir = QRectF(th.x() + (th.width() - w) / 2, th.y() + (th.height() - h) / 2, w, h)
            p.drawImage(ir, img)
            if rf is not None:  # STORY/REELS Fill: 9:16 로 잘릴 창을 썸네일 위에 보여 준다 (창 밖은 어둡게)
                _src, xf, wf = rf
                win = QRectF(ir.x() + ir.width() * xf, ir.y(), ir.width() * wf, ir.height())
                shade = QColor(0, 0, 0, 150)
                p.fillRect(QRectF(ir.x(), ir.y(), win.x() - ir.x(), ir.height()), shade)
                p.fillRect(QRectF(win.right(), ir.y(), ir.right() - win.right(), ir.height()), shade)
                p.setPen(QPen(QColor(255, 255, 255, 200), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(win.adjusted(0.5, 0.5, -0.5, -0.5))
        # 번호 배지 (왼쪽 위)
        p.setFont(self._f_badge)
        label = f"#{d['num']}"
        fm = p.fontMetrics()
        bw = fm.horizontalAdvance(label) + 10
        badge = QRectF(th.x(), th.y(), bw, fm.height() + 2)
        p.fillRect(badge, QColor(8, 10, 14, 215))
        p.setPen(theme.C["TEXT"])
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
        # 가로 위치를 어떻게 정했는지 (오른쪽 위): 자동 / 수동 / 인물 없음
        src = rf[0] if rf is not None else None
        if src:
            p.setFont(self._f_small)
            txt = {"auto": "자동", "manual": "수동", "none": "인물 없음"}[src]
            fm2 = p.fontMetrics()
            tw = fm2.horizontalAdvance(txt) + 10
            pill = QRectF(th.right() - tw - 3, th.y() + 3, tw, fm2.height() + 2)
            p.setPen(Qt.PenStyle.NoPen)
            if src == "auto":
                p.setBrush(QColor("#ececec"))
                fg = QColor("#0a0a0a")
            elif src == "manual":
                p.setBrush(QColor(theme.ACCENT2))
                fg = QColor("#0a0a0a")
            else:
                p.setBrush(QColor(20, 20, 20, 220))
                fg = QColor(theme.MUTED)
            p.drawRoundedRect(pill, 4, 4)
            p.setPen(fg)
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, txt)
        # 하단: 길이 + 병합/수동 표시
        p.setFont(self._f_small)
        n = d["end"] - d["start"]
        sec = d["sec"]
        extra = (f" ⛓{d['parts']}" if d["merged"] else "") + (" ●" if d["manual"] else "")
        text = f"{n}f · {sec:.2f}s{extra}"
        p.setPen(theme.C["TEXT"] if selected else theme.C["MUTED"])
        p.drawText(
            QRectF(r.x() + 4, r.bottom() - 22, r.width() - 8, 20),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )
        # 테두리 / 현재 위치 표시
        p.setPen(QPen(theme.C["ACCENT"] if selected else theme.C["BORDER"], 2 if selected else 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0, 0, -1, -1), 5, 5)
        if d["id"] == self.strip.current_id:
            # 이 컷 안에서의 재생 헤드: 썸네일 위 세로선 + 아래 진행 막대
            n = max(1, d["end"] - d["start"])
            f = min(1.0, max(0.0, (self.strip.playhead - d["start"]) / n))
            p.fillRect(QRectF(r.x(), r.bottom() - 3, r.width(), 3), QColor(255, 255, 255, 40))
            p.fillRect(QRectF(r.x(), r.bottom() - 3, r.width() * f, 3), theme.C["ACCENT2"])
            hx = th.x() + th.width() * f
            p.setPen(QPen(theme.C["ACCENT2"], 2))
            p.drawLine(QPointF(hx, th.y()), QPointF(hx, th.bottom()))
        p.restore()


class CutStrip(QListWidget):
    selectionIdsChanged = Signal(list)  # 사용자가 선택을 바꿨을 때 (컷 id 목록, 화면 순서)
    cutActivated = Signal(int)  # 수정키 없이 컷을 눌렀을 때: 그 컷의 시작 프레임으로 이동
    contextRequested = Signal(QPoint)  # 컷 위에서 오른쪽 클릭: 화면 좌표 (선택은 이미 맞춰진 뒤)

    def __init__(self, project, thumbs):
        super().__init__()
        self.project, self.thumbs = project, thumbs
        self.current_id: int | None = None
        self.playhead = 0
        # (컷 시작, 컷 끝) -> (출처 'auto'|'manual'|'none'|None, 창 x 비율, 창 폭 비율) 또는 None (표시 안 함)
        self.reframe_info = None
        self._sig: tuple = ()
        self._block = False
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setGridSize(QSize(TILE_W, TILE_H))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)  # Shift 범위 / Ctrl 토글
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.setFixedHeight(STRIP_H)
        self.setItemDelegate(_TileDelegate(self))
        # 방향키는 항상 프레임 이동(편집 창 단축키)에 쓰도록 컷 목록은 키보드 포커스를 받지 않는다
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSpacing(0)
        self.setToolTip(
            "컷 목록 (길이와 무관하게 같은 크기). 클릭: 선택 / Shift+클릭: 범위 / Ctrl+클릭: 추가·해제 / 오른쪽 클릭: 메뉴 (병합 등)"
        )
        self.itemSelectionChanged.connect(self._on_selection)
        self.clicked.connect(self._on_clicked)
        self.horizontalScrollBar().valueChanged.connect(lambda _v: self._request_visible())
        thumbs.thumbReady.connect(lambda _n: self.viewport().update())

    # ------------------------------------------------------------ 데이터
    def thumb(self, frame: int):
        return self.thumbs.get(frame)

    def isSelected(self, index) -> bool:  # noqa: N802
        return self.selectionModel().isSelected(index)

    def refresh(self, selected_ids: list[int] | None = None) -> None:
        """컷 목록이 바뀐 경우에만 타일을 다시 만든다. 선택은 selected_ids 로 맞춘다."""
        cuts = self.project.state.cuts
        sig = tuple((c.id, c.start, c.end, c.merged, c.manual, len(c.parts)) for c in cuts)
        if sig != self._sig:
            self._sig = sig
            self._block = True
            try:
                self.clear()
                fps = float(self.project.fps) or 24.0
                for i, c in enumerate(cuts):
                    it = QListWidgetItem()
                    it.setData(
                        DATA,
                        {
                            "id": c.id,
                            "num": i + 1,
                            "start": c.start,
                            "end": c.end,
                            "rep": representative_frame(c.start, c.end),
                            "sec": (c.end - c.start) / fps,
                            "merged": c.merged,
                            "parts": len(c.parts),
                            "manual": c.manual,
                        },
                    )
                    it.setSizeHint(QSize(TILE_W, TILE_H))
                    it.setToolTip(
                        f"#{i + 1}  {self.project.tc(c.start)} ~ {self.project.tc(c.end)}  ({c.end - c.start}프레임)"
                    )
                    self.addItem(it)
            finally:
                self._block = False
        if selected_ids is not None:
            self.set_selected_ids(selected_ids, scroll=False)
        self._request_visible()
        self.viewport().update()

    def ids(self) -> list[int]:
        return [self.item(i).data(DATA)["id"] for i in range(self.count())]

    def selected_ids(self) -> list[int]:
        return [self.item(i).data(DATA)["id"] for i in range(self.count()) if self.item(i).isSelected()]

    def set_selected_ids(self, ids: list[int], scroll: bool = True) -> None:
        want = set(ids)
        self._block = True
        try:
            self.clearSelection()
            first = None
            for i in range(self.count()):
                it = self.item(i)
                if it.data(DATA)["id"] in want:
                    it.setSelected(True)
                    first = first or it
            if scroll and first is not None:
                self.scrollToItem(first, QAbstractItemView.ScrollHint.EnsureVisible)
        finally:
            self._block = False
        self._request_visible()
        self.viewport().update()

    def set_playhead(self, frame: int) -> None:
        """재생 헤드가 있는 컷을 표시하고 스트립 가운데로 스크롤한다 (재생 헤드 근방 썸네일이 보이도록)."""
        i = cuts_mod.cut_index_at(self.project.state.cuts, frame)
        cid = self.project.state.cuts[i].id if i is not None else None
        self.playhead = frame
        if cid == self.current_id:
            if i is not None and i < self.count():  # 같은 컷 안: 그 타일의 재생 헤드만 다시 그린다
                self.viewport().update(self.visualItemRect(self.item(i)))
            return
        self.current_id = cid
        if i is not None and i < self.count():
            self._center_on(i)
        self.viewport().update()

    def _center_on(self, i: int) -> None:
        """i 번째 타일을 스트립 가운데로 (scrollToItem 의 가운데 맞춤은 아이콘 보기에서 어긋날 때가 있어 직접 계산)."""
        sb = self.horizontalScrollBar()
        r = self.visualItemRect(self.item(i))
        if r.width() <= 0:
            self.scrollToItem(self.item(i), QAbstractItemView.ScrollHint.PositionAtCenter)
            return
        sb.setValue(sb.value() + r.center().x() - self.viewport().width() // 2)

    # ------------------------------------------------------------ 입력
    def _on_selection(self) -> None:
        if not self._block:
            self.selectionIdsChanged.emit(self.selected_ids())

    def _on_clicked(self, index) -> None:
        mods = QApplication.keyboardModifiers()
        if mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
            return  # 선택만 바꾸고 재생 헤드는 움직이지 않는다 (타임라인과 같은 동작)
        d = index.data(DATA)
        if d:
            self.cutActivated.emit(d["start"])

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        """오른쪽 클릭: 선택되지 않은 컷을 누르면 그 컷만 선택하고(Shift 로 고른 범위 안이면 선택 유지) 메뉴를 요청한다."""
        idx = self.indexAt(e.pos())
        if not idx.isValid():
            return
        if not self.isSelected(idx):
            cid = idx.data(DATA)["id"]
            self.set_selected_ids([cid], scroll=False)
            self.selectionIdsChanged.emit([cid])
        self.contextRequested.emit(e.globalPos())
        e.accept()

    # ------------------------------------------------------------ 썸네일 요청
    def _request_visible(self) -> None:
        n = self.count()
        if n == 0:
            return
        w = self.viewport().width()
        first = self.indexAt(QPoint(4, TILE_H // 2))
        last = self.indexAt(QPoint(max(4, w - 4), TILE_H // 2))
        a = first.row() if first.isValid() else 0
        b = last.row() if last.isValid() else min(n - 1, a + max(1, w // TILE_W))
        a, b = max(0, a - 6), min(n - 1, b + 6)
        mid = (a + b) / 2
        rows = sorted(range(a, b + 1), key=lambda r: abs(r - mid))
        self.thumbs.request_visible([self.item(r).data(DATA)["rep"] for r in rows])

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._request_visible()

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        if self.count() == 0:
            p = QPainter(self.viewport())
            p.setPen(theme.C["MUTED"])
            p.drawText(
                self.viewport().rect().adjusted(12, 0, -12, 0),
                Qt.AlignmentFlag.AlignCenter,
                "편집 XML · FCPXML · EDL 을 여기로 끌어다 놓으면 바로 컷이 잘립니다",
            )


class CutScrubber(QWidget):
    """선택한 컷만 확대해 보여 주는 작은 타임라인. 눈금(타임코드)과 빨간 재생 헤드가 있고, 좌우로 끌면
    그 컷의 처음부터 끝까지만 긁어 볼 수 있다 (끝까지 끌어도 다음 컷으로 넘어가지 않는다).
    컷을 여러 개 고르면 고른 컷들의 처음~끝."""

    scrubbed = Signal(int)  # 원본 프레임 번호
    RULER_H, TRACK_H = 20, 26
    LEFT, RIGHT = 74, 118
    H = RULER_H + TRACK_H + 6

    def __init__(self, thumbs, fps: float, parent=None):
        super().__init__(parent)
        self.thumbs, self.fps = thumbs, max(1e-6, float(fps))
        self.start = self.end = 0
        self.label = ""
        self.playhead = 0
        self._drag = False
        self.setFixedHeight(self.H)
        self.setMouseTracking(True)
        self.setToolTip("선택한 컷만 확대한 타임라인: 좌우로 끌면 그 컷 처음부터 끝까지만 훑어봅니다")
        thumbs.thumbReady.connect(lambda _n: self.update())

    def set_range(self, start: int, end: int, label: str) -> None:
        if (start, end, label) != (self.start, self.end, self.label):
            self.start, self.end, self.label = start, end, label
            self.update()

    def set_playhead(self, n: int) -> None:
        if n != self.playhead:
            self.playhead = n
            self.update()

    @property
    def active(self) -> bool:
        return self.end > self.start

    def _area(self) -> QRectF:
        return QRectF(self.LEFT, 2, max(10, self.width() - self.LEFT - self.RIGHT), self.height() - 4)

    def x_of(self, f: float) -> float:
        a = self._area()
        return a.x() + a.width() * (f - self.start) / max(1, self.end - self.start)

    def _frame_at(self, x: float) -> int:
        a = self._area()
        f = min(1.0, max(0.0, (x - a.x()) / a.width()))
        return self.start + min(self.end - 1 - self.start, int(f * (self.end - self.start)))

    def mousePressEvent(self, e):  # noqa: N802
        if self.active and e.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            self.scrubbed.emit(self._frame_at(e.position().x()))

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._drag:
            self.scrubbed.emit(self._frame_at(e.position().x()))

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._drag = False
        self.unsetCursor()

    def paintEvent(self, _e):  # noqa: N802
        from ..core.timecode import frame_to_tc

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.BG))
        a = self._area()
        f = QFont(self.font().family())
        f.setPixelSize(11)
        p.setFont(f)
        if not self.active:
            p.setPen(QColor(theme.MUTED))
            p.drawText(
                self.rect().adjusted(12, 0, -12, 0),
                Qt.AlignmentFlag.AlignVCenter,
                "컷을 고르면 그 컷만 확대한 타임라인이 여기 나옵니다",
            )
            return
        n = self.end - self.start
        ruler = QRectF(a.x(), a.y(), a.width(), self.RULER_H)
        track = QRectF(a.x(), ruler.bottom() + 2, a.width(), self.TRACK_H)
        # ---- 눈금 (메인 타임라인과 같은 모양)
        p.fillRect(ruler, theme.C["PANEL2"])
        ppf = a.width() / max(1, n)
        fi = max(1, int(round(self.fps)))
        # 눈금 간격은 초 단위에 맞춘다 (24fps 면 1, 2, 3, 6, 12, 24f, 2초 …) -> 타임코드 글자가 깔끔하게 떨어진다
        steps = sorted({d for d in range(1, fi + 1) if fi % d == 0} | {fi * m for m in (2, 5, 10, 30, 60)})
        step = next((st for st in steps if st * ppf >= 74), steps[-1])
        sub = next((st for st in reversed(steps) if st < step and step % st == 0 and st * ppf >= 6), 0)
        p.setFont(QFont(self.font().family(), 8))
        tick = sub or step
        f0 = -(-self.start // tick) * tick
        for fr in range(f0, self.end + 1, tick):
            x = self.x_of(fr)
            if fr % step == 0:
                p.setPen(theme.C["MUTED"])
                p.drawLine(QPointF(x, ruler.y() + 9), QPointF(x, ruler.bottom()))
                if x + 70 < a.right() + 4:
                    p.drawText(QPointF(x + 3, ruler.y() + 9), frame_to_tc(fr, self.fps))
            else:
                p.setPen(QPen(theme.C["BORDER"], 1))
                p.drawLine(QPointF(x, ruler.y() + 15), QPointF(x, ruler.bottom()))
        # ---- 필름스트립 (그 컷의 프레임을 고르게)
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(track, 3, 3)
        p.setClipPath(clip)
        p.fillRect(track, QColor("#151515"))
        tw = max(24.0, track.height() * 16 / 9)
        k = max(1, int(track.width() // tw) + 1)
        want = []
        for i in range(k):
            fr = self.start + int((i + 0.5) / k * max(1, n - 1))
            want.append(fr)
            img = self.thumbs.get(fr)
            if img is not None and not img.isNull():
                r = QRectF(track.x() + i * track.width() / k, track.y(), track.width() / k, track.height())
                src = img.rect()
                sw = min(src.width(), src.height() * r.width() / r.height())
                p.drawImage(r, img, QRectF((src.width() - sw) / 2, 0, sw, src.height()))
        self.thumbs.request_visible(want)
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(track, 3, 3)
        # ---- 재생 헤드 (메인 타임라인과 같은 빨간 선 + 삼각형)
        ph = min(max(self.playhead, self.start), self.end - 1)
        x = self.x_of(ph + 0.5) if n < a.width() / 2 else self.x_of(ph)
        col = QColor("#ff4d5e")
        p.setPen(QPen(col, 1.5))
        p.drawLine(QPointF(x, ruler.y()), QPointF(x, track.bottom()))
        p.setBrush(col)
        p.drawPolygon(QPolygonF([QPointF(x - 6, ruler.y()), QPointF(x + 6, ruler.y()), QPointF(x, ruler.y() + 9)]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        # ---- 글자: 왼쪽 컷 번호와 길이, 오른쪽 컷 안 위치
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(12, 0, self.LEFT - 14, self.height() / 2 + 4), Qt.AlignmentFlag.AlignBottom, self.label)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(12, self.height() / 2 + 4, self.LEFT - 14, self.height() / 2 - 4), Qt.AlignmentFlag.AlignTop,
                   f"{n}f")  # fmt: skip
        cur = (ph - self.start) / self.fps
        p.drawText(
            QRectF(a.right() + 10, 0, self.RIGHT - 12, self.height()),
            Qt.AlignmentFlag.AlignVCenter,
            f"{cur:5.2f}s / {n / self.fps:.2f}s",
        )

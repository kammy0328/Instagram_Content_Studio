"""컷 스트립: 컷마다 같은 크기의 타일(썸네일 + 번호 + 길이).

타임라인의 컷 레인은 길이에 비례해서 1프레임 컷은 번호조차 보이지 않는다. 이 목록은 길이와 무관하게 모든 컷을 같은 크기로
보여 주고, 컷을 눈으로 알아볼 수 있게 썸네일을 붙인다. 선택(클릭, Shift 범위, Ctrl 토글)은 타임라인과 양방향으로 동기화된다.
컷 정보(XML)는 프로젝트 전체에서 하나이므로 STORY/REELS/FEED 어느 탭에서나 같은 목록이 보인다."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyledItemDelegate,
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
        p.fillRect(th, QColor("#101216"))
        img = self.strip.thumb(d["rep"])
        if img is not None and img.height() > 0:
            sc = min(th.width() / img.width(), th.height() / img.height())
            w, h = img.width() * sc, img.height() * sc
            p.drawImage(QRectF(th.x() + (th.width() - w) / 2, th.y() + (th.height() - h) / 2, w, h), img)
        # 번호 배지 (왼쪽 위)
        p.setFont(self._f_badge)
        label = f"#{d['num']}"
        fm = p.fontMetrics()
        bw = fm.horizontalAdvance(label) + 10
        badge = QRectF(th.x(), th.y(), bw, fm.height() + 2)
        p.fillRect(badge, QColor(8, 10, 14, 215))
        p.setPen(theme.C["TEXT"])
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
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
            p.fillRect(QRectF(r.x(), r.bottom() - 3, r.width(), 3), theme.C["ACCENT2"])
        p.restore()


class CutStrip(QListWidget):
    selectionIdsChanged = Signal(list)  # 사용자가 선택을 바꿨을 때 (컷 id 목록, 화면 순서)
    cutActivated = Signal(int)  # 수정키 없이 컷을 눌렀을 때: 그 컷의 시작 프레임으로 이동
    contextRequested = Signal(QPoint)  # 컷 위에서 오른쪽 클릭: 화면 좌표 (선택은 이미 맞춰진 뒤)

    def __init__(self, project, thumbs):
        super().__init__()
        self.project, self.thumbs = project, thumbs
        self.current_id: int | None = None
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
        if cid == self.current_id:
            return
        self.current_id = cid
        if i is not None and i < self.count():
            self.scrollToItem(self.item(i), QAbstractItemView.ScrollHint.PositionAtCenter)
        self.viewport().update()

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
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                "컷 정보 없음. 'XML 불러오기' 로 한 번만 불러오면 STORY / REELS / FEED 모두에서 같은 컷이 "
                "같은 크기의 타일로 표시됩니다.",
            )

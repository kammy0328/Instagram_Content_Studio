"""프로젝트 관리 화면 (시작 창).

- 위: 앱 표시 + '프로젝트' 제목과 개수, 검색, 새 프로젝트
- 가운데: 프로젝트 줄 (썸네일, 이름, 아티스트 · 곡, 원본 파일, 마지막 수정) - 마우스를 올리면 폴더 열기 / 삭제
- 더블클릭 또는 Enter 로 열기. 처음 열릴 때 줄이 차례로 떠오른다.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from . import icons, motion, theme
from .frameless import ChromeDialog
from .widgets import studio_icon

ROW_H = 84
THUMB_H = 60
THUMB_W = int(THUMB_H * 16 / 9)


def relative_time(ts: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    d = max(0, now - ts)
    if d < 60:
        return "방금"
    if d < 3600:
        return f"{int(d // 60)}분 전"
    if d < 86400:
        return f"{int(d // 3600)}시간 전"
    if d < 7 * 86400:
        return f"{int(d // 86400)}일 전"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def project_thumb(pid: str) -> QPixmap | None:
    """편집할 때 만들어 둔 썸네일 캐시에서 한 장 (앞쪽 검은 화면을 피해 1/3 지점). 없으면 None."""
    try:
        base = paths.cache_dir(pid)
    except OSError:
        return None
    for sub in ("cutline_thumbs", "strip_thumbs", "thumbs"):
        d = base / sub
        if not d.is_dir():
            continue
        try:
            files = sorted(d.rglob("*.jpg"))
        except OSError:
            continue
        if files:
            pm = QPixmap(str(files[len(files) // 3]))
            if not pm.isNull():
                return pm
    return None


class _RowDelegate(QStyledItemDelegate):
    """프로젝트 한 줄 그리기. 오른쪽 폴더/삭제 아이콘은 마우스를 올린 줄과 선택한 줄에만 보인다."""

    def __init__(self, dlg: ProjectListDialog):
        super().__init__(dlg)
        self.dlg = dlg
        self.hover_row = -1
        self.t = 1.0  # 등장 애니메이션 진행 (0..1)
        self._thumbs: dict[str, QPixmap | None] = {}
        self.f_name = QFont(theme.FONT)
        self.f_name.setPixelSize(theme.FS_EM + 1)
        self.f_name.setWeight(QFont.Weight.DemiBold)
        self.f_sub = QFont(theme.FONT)
        self.f_sub.setPixelSize(theme.FS_BODY)
        self.f_meta = QFont(theme.FONT)
        self.f_meta.setPixelSize(theme.FS_SMALL)

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(option.rect.width(), ROW_H + 6)

    def thumb(self, pid: str) -> QPixmap | None:
        if pid not in self._thumbs:
            self._thumbs[pid] = project_thumb(pid)
        return self._thumbs[pid]

    @staticmethod
    def card(rect: QRect) -> QRectF:
        return QRectF(rect).adjusted(0, 3, 0, -3)

    def action_rects(self, rect: QRect) -> dict[str, QRectF]:
        c = self.card(rect)
        y = c.center().y() - 14
        return {"delete": QRectF(c.right() - 42, y, 28, 28), "folder": QRectF(c.right() - 76, y, 28, 28)}

    def paint(self, p: QPainter, option, index):
        rec = self.dlg.rec_at(index.row())
        if rec is None:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # 등장: 줄마다 조금씩 늦게, 살짝 아래에서
        k = max(0.0, min(1.0, (self.t - index.row() * 0.07) / 0.35))
        p.setOpacity(k)
        p.translate(0, (1 - k) * 10)
        c = self.card(option.rect)
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        hov = index.row() == self.hover_row
        if sel or hov:
            p.setPen(QPen(QColor("#3a3a41" if sel else "#26262b"), 1))
            p.setBrush(QColor("#1b1b1f" if sel else "#141417"))
            p.drawRoundedRect(c.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        # 썸네일
        tr = QRectF(c.x() + 12, c.center().y() - THUMB_H / 2, THUMB_W, THUMB_H)
        path = QPainterPath()
        path.addRoundedRect(tr, 6, 6)
        p.save()
        p.setClipPath(path)
        p.fillRect(tr, QColor("#0d0d0f"))
        pm = self.thumb(rec.id)
        if pm is not None:
            s = max(tr.width() / pm.width(), tr.height() / pm.height())
            w, h = pm.width() * s, pm.height() * s
            p.drawPixmap(QRectF(tr.center().x() - w / 2, tr.center().y() - h / 2, w, h), pm, QRectF(pm.rect()))
        else:
            p.drawPixmap(QPointF(tr.center().x() - 10, tr.center().y() - 10), icons.pixmap("film", theme.DIM, 20))
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(tr, 6, 6)
        # 글자
        x = tr.right() + 16
        right_pad = 96 if (sel or hov) else 16
        tw = c.right() - right_pad - x
        meta_w = 90
        ok = self.dlg.master_ok(rec)
        p.setFont(self.f_name)
        p.setPen(QColor("#ffffff" if sel else theme.TEXT))
        fm = QFontMetrics(self.f_name)
        p.drawText(
            QRectF(x, c.y() + 12, tw - meta_w, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm.elidedText(rec.name, Qt.TextElideMode.ElideRight, int(tw - meta_w)),
        )
        p.setFont(self.f_sub)
        p.setPen(QColor(theme.MUTED))
        sub = " · ".join(x for x in ((rec.artist or "").upper(), rec.title or "") if x) or "아티스트 / 곡 정보 없음"
        p.drawText(
            QRectF(x, c.y() + 33, tw, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(self.f_sub).elidedText(sub, Qt.TextElideMode.ElideRight, int(tw)),
        )
        p.setFont(self.f_meta)
        fmm = QFontMetrics(self.f_meta)
        base = os.path.basename(rec.master_path)
        if ok:
            p.setPen(QColor(theme.DIM))
            text = base
        else:
            p.setPen(QColor(theme.WARN))
            text = "⚠ 원본 파일 연결 끊김 · " + base
        p.drawText(
            QRectF(x, c.y() + 53, tw, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fmm.elidedText(text, Qt.TextElideMode.ElideMiddle, int(tw)),
        )
        # 마지막 수정 (오른쪽 위)
        p.setPen(QColor(theme.MUTED))
        if not (sel or hov):
            p.drawText(
                QRectF(c.right() - 16 - meta_w, c.y() + 12, meta_w, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                relative_time(rec.updated),
            )
        else:
            p.drawText(
                QRectF(c.right() - 96 - meta_w, c.y() + 12, meta_w, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                relative_time(rec.updated),
            )
            for name, r in self.action_rects(option.rect).items():
                over = r.contains(self.dlg.mouse_pos)
                if over:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor("#2a1417") if name == "delete" else QColor("#26262b"))
                    p.drawRoundedRect(r, 7, 7)
                col = theme.ERR if (name == "delete" and over) else (theme.TEXT if over else theme.MUTED)
                p.drawPixmap(
                    QPointF(r.center().x() - 8, r.center().y() - 8),
                    icons.pixmap("trash-2" if name == "delete" else "folder-open", col, 16),
                )
        p.restore()

    def editorEvent(self, event, model, option, index):  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            for name, r in self.action_rects(option.rect).items():
                if r.contains(event.position()):
                    self.dlg.row_action(index.row(), name)
                    return True
        return super().editorEvent(event, model, option, index)


class ProjectListDialog(ChromeDialog):
    chrome_minimize = True  # 시작 창이므로 최소화 단추도

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Instagram Content Studio")
        self.setWindowIcon(studio_icon())
        self.resize(980, 660)
        self.setMinimumSize(720, 480)
        self.chosen = None
        self.new_requested = False
        self.recs: list = []
        self.shown: list = []
        self.mouse_pos = QPointF(-1, -1)
        self._master_ok: dict[str, bool] = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 18, 28, 20)
        v.setSpacing(14)
        # ---- 머리
        head = QHBoxLayout()
        head.setSpacing(12)
        mark = QLabel()
        mark.setPixmap(studio_icon(40).pixmap(40, 40))
        head.addWidget(mark)
        tv = QVBoxLayout()
        tv.setSpacing(1)
        t = QLabel("프로젝트")
        t.setStyleSheet("font-size:22px; font-weight:700;")
        tv.addWidget(t)
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("hint")
        tv.addWidget(self.count_lbl)
        head.addLayout(tv)
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("이름, 아티스트, 곡 검색")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search", theme.DIM, 14), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setFixedSize(240, 34)
        self.search.textChanged.connect(self._filter)
        head.addWidget(self.search)
        nb = QPushButton("새 프로젝트")
        nb.setObjectName("primary")
        nb.setIcon(icons.icon("plus", "#0a0a0a", 15))
        nb.clicked.connect(self._new)
        head.addWidget(nb)
        v.addLayout(head)
        # ---- 목록
        self.list = QListWidget()
        self.list.setObjectName("projectList")
        self.list.setStyleSheet(
            "QListWidget#projectList { background: transparent; border: none; outline: none; }"
            "QListWidget#projectList::item { background: transparent; border: none; }"
            "QListWidget#projectList::item:selected { background: transparent; }"
        )
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setMouseTracking(True)
        self.list.viewport().installEventFilter(self)
        self.delegate = _RowDelegate(self)
        self.list.setItemDelegate(self.delegate)
        self.list.itemDoubleClicked.connect(lambda _it: self._open())
        self.list.itemActivated.connect(lambda _it: self._open())  # Enter
        v.addWidget(self.list, 1)
        # ---- 빈 화면
        self.empty = QWidget()
        ev = QVBoxLayout(self.empty)
        ev.addStretch(1)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("film", theme.DIM, 40))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(ic)
        self.empty_title = QLabel("아직 프로젝트가 없습니다")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title.setStyleSheet(f"font-size:{theme.FS_H}px; font-weight:600;")
        ev.addWidget(self.empty_title)
        self.empty_hint = QLabel(
            "‘새 프로젝트’ 로 마스터링 영상을 불러오면 STORY · REELS · FEED 를 한 번에 만들 수 있습니다."
        )
        self.empty_hint.setObjectName("hint")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(self.empty_hint)
        ev.addStretch(2)
        v.addWidget(self.empty, 1)
        # ---- 아래
        foot = QHBoxLayout()
        hint = QLabel("더블클릭 또는 Enter 로 열기 · 줄에 마우스를 올리면 출력 폴더 열기 / 삭제")
        hint.setObjectName("hint")
        foot.addWidget(hint)
        foot.addStretch(1)
        self.open_btn = QPushButton("열기")
        self.open_btn.clicked.connect(self._open)
        foot.addWidget(self.open_btn)
        c = QPushButton("닫기")
        c.clicked.connect(self.reject)
        foot.addWidget(c)
        v.addLayout(foot)
        self.refresh()
        self._appear()

    # ---------------------------------------------------------------- 데이터
    def master_ok(self, rec) -> bool:
        if rec.id not in self._master_ok:
            self._master_ok[rec.id] = os.path.isfile(rec.master_path)
        return self._master_ok[rec.id]

    def rec_at(self, row: int):
        return self.shown[row] if 0 <= row < len(self.shown) else None

    def refresh(self) -> None:
        self.recs = self.library.list()
        self._master_ok.clear()
        self._filter(self.search.text())

    def _filter(self, text: str = "") -> None:
        q = (text or "").strip().lower()
        self.shown = [r for r in self.recs if not q or any(q in (x or "").lower() for x in (r.name, r.artist, r.title))]
        self.list.clear()
        for _r in self.shown:
            it = QListWidgetItem()
            it.setSizeHint(QSize(10, ROW_H + 6))
            self.list.addItem(it)
        n, m = len(self.recs), len(self.shown)
        self.count_lbl.setText(f"{n}개" if not q else f"{m} / {n}개")
        has = bool(self.shown)
        self.list.setVisible(has)
        self.empty.setVisible(not has)
        self.open_btn.setEnabled(has)
        if not self.recs:
            self.empty_title.setText("아직 프로젝트가 없습니다")
            self.empty_hint.setText(
                "‘새 프로젝트’ 로 마스터링 영상을 불러오면 STORY · REELS · FEED 를 한 번에 만들 수 있습니다."
            )
        elif not has:
            self.empty_title.setText("검색 결과가 없습니다")
            self.empty_hint.setText("다른 이름이나 아티스트로 찾아보세요.")
        if has:
            self.list.setCurrentRow(0)

    def _appear(self) -> None:
        d = self.delegate
        d.t = 0.0 if motion.enabled() else 1.0
        motion.animate_value(
            self, "_appear_anim", 0.0, 1.0 + len(self.shown[:12]) * 0.07,
            lambda t: (setattr(d, "t", float(t)), self.list.viewport().update()),
            ms=motion.SLOW + 60 * min(12, len(self.shown)),
        )  # fmt: skip

    def eventFilter(self, obj, ev):  # noqa: N802
        try:
            vp = self.list.viewport()
        except (AttributeError, RuntimeError):
            return False
        if obj is vp:
            t = ev.type()
            if t == QEvent.Type.MouseMove:
                self.mouse_pos = ev.position()
                idx = self.list.indexAt(ev.position().toPoint())
                self.delegate.hover_row = idx.row() if idx.isValid() else -1
                over = idx.isValid() and any(
                    r.contains(ev.position()) for r in self.delegate.action_rects(self.list.visualRect(idx)).values()
                )
                vp.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)
                vp.update()
            elif t == QEvent.Type.Leave:
                self.delegate.hover_row = -1
                self.mouse_pos = QPointF(-1, -1)
                vp.update()
        return False

    # ---------------------------------------------------------------- 동작
    def _cur(self):
        return self.rec_at(self.list.currentRow())

    def row_action(self, row: int, name: str) -> None:
        self.list.setCurrentRow(row)
        if name == "folder":
            self._open_output()
        elif name == "delete":
            self._delete()

    def _new(self):
        self.new_requested = True
        self.accept()

    def _open(self):
        rec = self._cur()
        if rec:
            self.chosen = rec
            self.accept()

    def _open_output(self):
        from .dialogs import open_path

        rec = self._cur()
        if rec:
            root = Path(rec.output_dir) / paths.safe_filename(rec.name)
            open_path(str(root if root.exists() else rec.output_dir))

    def _delete(self):
        rec = self._cur()
        if not rec:
            return
        m = QMessageBox.question(
            self,
            "프로젝트 삭제",
            f"'{rec.name}' 프로젝트 데이터(편집 상태·캐시)를 삭제합니다.\n"
            "마스터링 원본과 출력 폴더의 결과물은 삭제되지 않습니다.\n\n계속할까요?",
        )
        if m == QMessageBox.StandardButton.Yes:
            self.library.delete(rec.id)
            self.refresh()

"""다이얼로그 모음."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..core import media as media_mod
from ..core import xml_import as xmlmod
from ..core.model import FEED_MAX_FINAL
from ..core.timecode import frame_to_tc, tc_to_frame
from ..render.planner import RenderJob, RenderPlan
from .settings import DEFAULT_SHORTCUTS
from .widgets import studio_icon


def open_path(path: str) -> None:
    """파일/폴더를 OS 기본 프로그램으로 연다."""
    if not os.path.exists(path):
        QMessageBox.warning(None, "열기", f"경로를 찾을 수 없습니다:\n{path}")
        return
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def fmt_bytes(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return ""


# ============================================================================ 미디어 분석 리포트
class MediaReportDialog(QDialog):
    def __init__(self, info: media_mod.MediaInfo, parent=None):
        super().__init__(parent)
        self.setWindowTitle("영상 분석 결과")
        self.resize(560, 460)
        v = QVBoxLayout(self)
        t = QLabel(os.path.basename(info.path))
        t.setObjectName("h1")
        v.addWidget(t)
        box = QPlainTextEdit("\n".join(info.summary_lines()))
        box.setReadOnly(True)
        box.setMaximumHeight(210)
        v.addWidget(box)
        for iss in info.issues:
            col = {"error": "#ff5d6c", "warn": "#f2b84b", "info": "#8b92a3"}[iss.level]
            icon = {"error": "⛔", "warn": "⚠", "info": "ℹ"}[iss.level]
            lb = QLabel(f"{icon} {iss.message}")
            lb.setWordWrap(True)
            lb.setStyleSheet(f"color:{col};")
            v.addWidget(lb)
        v.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self.accept)
        v.addWidget(bb)


# ============================================================================ 새 프로젝트
class NewProjectDialog(QDialog):
    def __init__(self, parent=None, default_output: str = ""):
        super().__init__(parent)
        self.setWindowTitle("새 프로젝트")
        self.setWindowIcon(studio_icon())
        self.setAcceptDrops(True)
        self.resize(640, 450)
        self.info: media_mod.MediaInfo | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(14)
        heading = QLabel("새 프로젝트 만들기")
        heading.setObjectName("h1")
        v.addWidget(heading)
        note = QLabel("마스터링 원본과 결과물을 저장할 폴더를 선택하세요.")
        note.setObjectName("muted")
        v.addWidget(note)
        f = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("예: 아티스트_뮤직비디오")
        self.artist = QLineEdit()
        self.title = QLineEdit()
        self.master = QLineEdit()
        self.master.setPlaceholderText("MP4/MOV 마스터링 영상을 끌어다 놓거나 선택")
        self.output = QLineEdit(default_output)
        bm = QPushButton("선택…")
        bm.clicked.connect(self._pick_master)
        bo = QPushButton("선택…")
        bo.clicked.connect(self._pick_output)
        rm, ro = QHBoxLayout(), QHBoxLayout()
        rm.addWidget(self.master)
        rm.addWidget(bm)
        ro.addWidget(self.output)
        ro.addWidget(bo)
        f.addRow("프로젝트명", self.name)
        f.addRow("아티스트", self.artist)
        f.addRow("영상 제목", self.title)
        f.addRow("마스터링 경로", rm)
        f.addRow("출력 경로", ro)
        v.addLayout(f)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        v.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("프로젝트 만들기")
        bb.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            self._set_master(u.toLocalFile())
            break

    def _pick_master(self):
        p, _ = QFileDialog.getOpenFileName(self, "마스터링 영상", "", "영상 (*.mp4 *.mov *.m4v);;모든 파일 (*)")
        if p:
            self._set_master(p)

    def _set_master(self, p: str):
        self.master.setText(p)
        if not self.name.text():
            self.name.setText(Path(p).stem)
        if not self.title.text():
            self.title.setText(Path(p).stem)

    def _pick_output(self):
        p = QFileDialog.getExistingDirectory(self, "출력 경로", self.output.text())
        if p:
            self.output.setText(p)

    def _ok(self):
        if not self.name.text().strip():
            self.msg.setText("프로젝트명을 입력하세요.")
            return
        if not self.output.text().strip():
            self.msg.setText("출력 경로를 지정하세요.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            info = media_mod.analyze(self.master.text().strip())
        finally:
            QApplication.restoreOverrideCursor()
        if info.errors:
            self.msg.setText("\n".join("⛔ " + i.message for i in info.errors))
            self.msg.setStyleSheet("color:#ff5d6c;")
            return
        self.info = info
        self.accept()

    def values(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "artist": self.artist.text().strip(),
            "title": self.title.text().strip(),
            "master_path": self.master.text().strip(),
            "output_dir": self.output.text().strip(),
        }


# ============================================================================ 프로젝트 목록
class ProjectListDialog(QDialog):
    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Instagram Content Studio - 프로젝트")
        self.setWindowIcon(studio_icon())
        self.resize(1000, 660)
        self.chosen = None
        v = QVBoxLayout(self)
        v.setContentsMargins(30, 28, 30, 24)
        v.setSpacing(16)
        brand = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(studio_icon().pixmap(36, 36))
        brand.addWidget(mark)
        name = QLabel("INSTAGRAM CONTENT STUDIO")
        name.setObjectName("eyebrow")
        brand.addWidget(name)
        brand.addStretch(1)
        v.addLayout(brand)
        heading = QLabel("마스터링의 다음 장면을 만들어 보세요.")
        heading.setObjectName("hero")
        v.addWidget(heading)
        description = QLabel("하나의 원본에서 STORY, REELS, FEED까지. 프레임 단위로 선택하고 완성하세요.")
        description.setObjectName("muted")
        v.addWidget(description)
        specs = QHBoxLayout()
        for title, detail in (
            ("STORY", "9:16 · 최대 60초"),
            ("REELS", "세로 영상과 커버 이미지"),
            ("FEED", "이미지와 영상을 캐러셀로"),
        ):
            card = QGroupBox(title)
            layout = QVBoxLayout(card)
            label = QLabel(detail)
            label.setObjectName("muted")
            layout.addWidget(label)
            specs.addWidget(card)
        v.addLayout(specs)
        recent = QLabel("최근 프로젝트")
        recent.setObjectName("eyebrow")
        v.addWidget(recent)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["프로젝트명", "아티스트", "영상 제목", "마지막 수정", "마스터링 파일"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.doubleClicked.connect(self._open)
        v.addWidget(self.table, 1)
        self.empty = QLabel("아직 프로젝트가 없습니다.\n아래 ‘새 프로젝트’를 눌러 마스터링 영상을 불러오세요.")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setMinimumHeight(90)
        v.addWidget(self.empty, 1)
        r = QHBoxLayout()
        for text, fn, name in (
            ("새 프로젝트", self._new, "primary"),
            ("열기", self._open, ""),
            ("출력 폴더 열기", self._open_output, ""),
            ("삭제", self._delete, "danger"),
        ):
            b = QPushButton(text)
            if name:
                b.setObjectName(name)
            b.clicked.connect(fn)
            r.addWidget(b)
        r.addStretch(1)
        c = QPushButton("닫기")
        c.clicked.connect(self.reject)
        r.addWidget(c)
        v.addLayout(r)
        self.new_requested = False
        self.refresh()

    def refresh(self):
        self.recs = self.library.list()
        self.empty.setVisible(not self.recs)
        self.table.setVisible(bool(self.recs))
        self.table.setRowCount(len(self.recs))
        for i, r in enumerate(self.recs):
            ok = os.path.isfile(r.master_path)
            vals = [
                r.name,
                r.artist,
                r.title,
                time.strftime("%Y-%m-%d %H:%M", time.localtime(r.updated)),
                os.path.basename(r.master_path) + ("" if ok else "  ⚠ 연결 끊김"),
            ]
            for c, t in enumerate(vals):
                it = QTableWidgetItem(t)
                if c == 4 and not ok:
                    it.setForeground(Qt.GlobalColor.yellow)
                self.table.setItem(i, c, it)
        if self.recs:
            self.table.selectRow(0)

    def _cur(self):
        r = self.table.currentRow()
        return self.recs[r] if 0 <= r < len(self.recs) else None

    def _new(self):
        self.new_requested = True
        self.accept()

    def _open(self):
        rec = self._cur()
        if rec:
            self.chosen = rec
            self.accept()

    def _open_output(self):
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


# ============================================================================ XML 불러오기
class XmlImportDialog(QDialog):
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("XML 컷 정보 불러오기")
        self.resize(760, 640)
        self.doc: xmlmod.XmlDoc | None = None
        self.report: xmlmod.MappingReport | None = None
        v = QVBoxLayout(self)
        note = QLabel(
            "XML 은 참고 데이터입니다. 원본 영상과 XML 파일은 수정되지 않으며, 컷 시작/종료 프레임만 타임라인에 표시됩니다."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        v.addWidget(note)
        r = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        b = QPushButton("파일 선택…")
        b.clicked.connect(self._pick)
        r.addWidget(self.path)
        r.addWidget(b)
        v.addLayout(r)
        f = QFormLayout()
        self.seq = QComboBox()
        self.seq.currentIndexChanged.connect(self._seq_changed)
        self.track = QComboBox()
        self.track.currentIndexChanged.connect(self._remap)
        self.offset = QSpinBox()
        self.offset.setRange(-10_000_000, 10_000_000)
        self.offset.valueChanged.connect(self._remap)
        self.off_tc = QLabel("")
        ob = QPushButton("자동 제안")
        ob.clicked.connect(self._suggest)
        orow = QHBoxLayout()
        orow.addWidget(self.offset)
        orow.addWidget(self.off_tc)
        orow.addWidget(ob)
        f.addRow("기준 시퀀스", self.seq)
        f.addRow("영상 트랙", self.track)
        f.addRow("오프셋(프레임)", orow)
        v.addLayout(f)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color:#f2b84b;")
        v.addWidget(self.warn)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "시작", "종료(미포함)", "이름"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        v.addWidget(self.table, 1)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setText("컷 적용")
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.bb.accepted.connect(self._apply)
        self.bb.rejected.connect(self.reject)
        v.addWidget(self.bb)

    def _pick(self):
        p, _ = QFileDialog.getOpenFileName(self, "편집 XML", "", "편집 정보 (*.xml *.fcpxml *.edl);;모든 파일 (*)")
        if p:
            self.load(p)

    def load(self, p: str):
        try:
            self.doc = xmlmod.parse_file(p, self.project.fps)
        except xmlmod.XmlError as e:
            QMessageBox.warning(self, "XML 불러오기", str(e))
            return
        self.path.setText(p)
        self.seq.blockSignals(True)
        self.seq.clear()
        for i, s in enumerate(self.doc.sequences):
            n = sum(len(t.clips) for t in s.tracks)
            self.seq.addItem(f"{s.name}  ({float(s.fps):.3f}fps · {n}클립)", i)
        self.seq.blockSignals(False)
        self._seq_changed()

    def _cur_seq(self):
        return self.doc.sequences[self.seq.currentData()] if self.doc and self.seq.count() else None

    def _seq_changed(self):
        s = self._cur_seq()
        self.track.blockSignals(True)
        self.track.clear()
        if s:
            best = s.best_track()
            for t in s.tracks:
                self.track.addItem(f"{t.label}  ({len(t.clips)}컷)", t.index)
            if best:
                self.track.setCurrentIndex(s.tracks.index(best))
        self.track.blockSignals(False)
        self._suggest(silent=True)
        self._remap()

    def _cur_track(self):
        s = self._cur_seq()
        if not s or not self.track.count():
            return None
        return next((t for t in s.tracks if t.index == self.track.currentData()), None)

    def _suggest(self, silent=False):
        s, t = self._cur_seq(), self._cur_track()
        if s and t:
            self.offset.blockSignals(True)
            self.offset.setValue(xmlmod.suggest_offset(s, t, self.project.count))
            self.offset.blockSignals(False)
            if not silent:
                self._remap()

    def _remap(self, *_):
        s, t = self._cur_seq(), self._cur_track()
        self.table.setRowCount(0)
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        if not (s and t):
            return
        tc0 = None
        m = self.project.media
        if m and m.tc_start:
            tc0 = tc_to_frame(m.tc_start, self.project.fps)
        self.report = xmlmod.map_to_master(s, t, self.project.fps, self.project.count, self.offset.value(), tc0)
        self.off_tc.setText(frame_to_tc(abs(self.offset.value()), self.project.fps))
        self.warn.setText("\n".join("⚠ " + w for w in (self.doc.warnings + self.report.warnings)))
        self.table.setRowCount(len(self.report.cuts))
        for i, (a, b, n, _m) in enumerate(self.report.cuts):
            for c, text in enumerate(
                [
                    str(i + 1),
                    f"{frame_to_tc(a, self.project.fps)}  (f{a})",
                    f"{frame_to_tc(b, self.project.fps)}  (f{b})",
                    n,
                ]
            ):
                self.table.setItem(i, c, QTableWidgetItem(text))
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.report.cuts))

    def _apply(self):
        if not self.report:
            return
        if self.project.has_manual_cut_edits():
            m = QMessageBox.question(
                self,
                "기존 수정사항 덮어쓰기",
                "이미 수동으로 보정하거나 병합한 컷이 있습니다.\nXML 을 다시 불러오면 이 수정사항이 사라집니다 "
                "(Undo 로 되돌릴 수 있습니다).\n\n덮어쓸까요?",
            )
            if m != QMessageBox.StandardButton.Yes:
                return
        s, t = self._cur_seq(), self._cur_track()
        meta = {
            "file": self.path.text(),
            "format": self.doc.format,
            "sequence": s.name,
            "track": t.label,
            "offset": self.offset.value(),
            "xml_fps": str(s.fps),
            "warnings": self.report.warnings,
        }
        self.project.import_cuts(self.report.cuts, meta)
        self.accept()


# ============================================================================ 피드 관리
class FeedManagerDialog(QDialog):
    def __init__(self, project, thumbs, parent=None):
        super().__init__(parent)
        self.project, self.thumbs = project, thumbs
        self._thumb_frames: set[int] = set()
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(250)
        self._thumb_timer.timeout.connect(self.refresh)
        thumbs.thumbReady.connect(self._on_thumb)
        self.finished.connect(lambda _r: thumbs.thumbReady.disconnect(self._on_thumb))  # 닫힌 뒤에는 갱신하지 않는다
        self.setWindowTitle("FEED 관리 - 최종 출력 항목 선택")
        self.resize(760, 560)
        v = QVBoxLayout(self)
        self.head = QLabel("")
        self.head.setWordWrap(True)
        v.addWidget(self.head)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["포함", "순서", "썸네일", "종류", "원본 타임코드", "길이"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setDefaultSectionSize(64)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemChanged.connect(self._changed)
        v.addWidget(self.table, 1)
        r = QHBoxLayout()
        for text, fn in (("모두 포함", lambda: self._all(True)), ("모두 제외", lambda: self._all(False))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            r.addWidget(b)
        r.addStretch(1)
        ok = QPushButton("확인")
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        r.addWidget(ok)
        v.addLayout(r)
        self._block = False
        self.refresh()

    def _on_thumb(self, n: int) -> None:
        if n in self._thumb_frames:
            self._thumb_timer.start()

    def refresh(self):
        p = self.project
        self._block = True
        items = p.feed_ordered()
        self._thumb_frames = {it.start for it in items}
        self.thumbs.ensure(sorted(self._thumb_frames))
        self.table.setRowCount(len(items))
        n = 0
        for i, it in enumerate(items):
            if it.included:
                n += 1
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked if it.included else Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, it.id)
            self.table.setItem(i, 0, chk)
            self.table.setItem(i, 1, QTableWidgetItem(str(n) if it.included else "-"))
            th = QTableWidgetItem()
            img = self.thumbs.get(it.start)
            if img is not None:
                th.setIcon(QIcon(QPixmap.fromImage(img)))
            self.table.setItem(i, 2, th)
            self.table.setItem(i, 3, QTableWidgetItem("스틸" if it.kind == "still" else "영상"))
            tc = frame_to_tc(it.start, p.fps) + (f" ~ {frame_to_tc(it.end, p.fps)}" if it.kind == "video" else "")
            self.table.setItem(i, 4, QTableWidgetItem(tc))
            self.table.setItem(
                i, 5, QTableWidgetItem("-" if it.kind == "still" else f"{p.seconds(it.start, it.end):.2f}s")
            )
        self.table.setIconSize(QSize(80, 56))
        self._block = False
        over = n > FEED_MAX_FINAL
        self.head.setText(
            f"등록 {len(items)}개 · 포함 <b>{n}</b> / 최종 {FEED_MAX_FINAL}개 "
            + (
                "<span style='color:#ff5d6c'>- 20개를 넘어 렌더링할 수 없습니다. 항목을 제외하세요 (삭제되지 않고 보존됩니다).</span>"
                if over
                else "<span style='color:#4cd08b'>- 렌더링 가능</span>"
            )
        )

    def _changed(self, item: QTableWidgetItem):
        if self._block or item.column() != 0:
            return
        self.project.set_feed_included(
            [item.data(Qt.ItemDataRole.UserRole)], item.checkState() == Qt.CheckState.Checked
        )
        self.refresh()

    def _all(self, state: bool):
        self.project.set_feed_included([f.id for f in self.project.state.feed], state)
        self.refresh()


# ============================================================================ 렌더링
class RenderDialog(QDialog):
    startRequested = Signal(bool)  # overwrite
    cancelRequested = Signal()
    retryRequested = Signal()
    jobUpdated = Signal(object)  # 스레드 -> UI

    def __init__(self, plan: RenderPlan, parent=None, running=False):
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle("내보내기")
        self.resize(900, 640)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 22)
        v.setSpacing(12)
        heading = QLabel("내보내기 준비")
        heading.setObjectName("h1")
        v.addWidget(heading)
        n_files = len([j for j in plan.jobs])
        self.head = QLabel(
            f"<b>출력 폴더</b> {plan.root}<br>파일 {n_files}개 · 예상 최대 용량 {fmt_bytes(plan.total_est_bytes)} "
            "(실제 용량은 영상 복잡도에 따라 다름)"
        )
        self.head.setWordWrap(True)
        self.head.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.head)
        for iss in plan.issues:
            col = {"error": "#ff5d6c", "warn": "#f2b84b", "info": "#8b92a3"}[iss.level]
            icon = {"error": "⛔", "warn": "⚠", "info": "ℹ"}[iss.level]
            lb = QLabel(f"{icon} {iss.message}")
            lb.setStyleSheet(f"color:{col};")
            lb.setWordWrap(True)
            v.addWidget(lb)
        self.table = QTableWidget(len(plan.jobs), 5)
        self.table.setHorizontalHeaderLabels(["파일", "상태", "진행", "남은 시간", "비고"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 160)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.bars: dict[str, QProgressBar] = {}
        self.rows: dict[str, int] = {}
        for i, j in enumerate(plan.jobs):
            rel = os.path.relpath(j.out_path, plan.root) if plan.root else j.out_path
            self.table.setItem(i, 0, QTableWidgetItem(rel))
            self.table.setItem(i, 1, QTableWidgetItem("대기"))
            bar = QProgressBar()
            bar.setRange(0, 100)
            self.table.setCellWidget(i, 2, bar)
            self.bars[j.id], self.rows[j.id] = bar, i
            self.table.setItem(i, 3, QTableWidgetItem(""))
            self.table.setItem(i, 4, QTableWidgetItem("이미 존재함" if j.conflict else ""))
        v.addWidget(self.table, 1)
        self.overwrite = QCheckBox("이미 존재하는 파일을 덮어씁니다 (체크 해제 시 기존 파일 유지)")
        self.overwrite.setChecked(False)
        self.overwrite.setVisible(bool(plan.conflicts))
        v.addWidget(self.overwrite)
        self.overall = QProgressBar()
        self.overall.setRange(0, 1000)
        v.addWidget(self.overall)
        r = QHBoxLayout()
        self.b_start = QPushButton("렌더링 시작")
        self.b_start.setObjectName("primary")
        self.b_start.setEnabled(not plan.blocked)
        self.b_start.clicked.connect(self._start)
        self.b_cancel = QPushButton("취소")
        self.b_cancel.setEnabled(False)
        self.b_cancel.clicked.connect(self.cancelRequested.emit)
        self.b_retry = QPushButton("실패 항목만 재시도")
        self.b_retry.setEnabled(False)
        self.b_retry.clicked.connect(self._retry)
        self.b_open = QPushButton("출력 폴더 열기")
        self.b_open.clicked.connect(lambda: open_path(plan.root))
        self.b_close = QPushButton("닫기 (백그라운드 계속)")
        self.b_close.clicked.connect(self.accept)
        for b in (self.b_start, self.b_cancel, self.b_retry, self.b_open):
            r.addWidget(b)
        r.addStretch(1)
        r.addWidget(self.b_close)
        v.addLayout(r)
        self.jobUpdated.connect(self._on_job)

    def _start(self):
        if self.plan.conflicts and not self.overwrite.isChecked():
            m = QMessageBox.question(
                self,
                "기존 파일",
                f"이미 존재하는 파일 {len(self.plan.conflicts)}개는 덮어쓰지 않고 건너뜁니다.\n계속할까요?",
            )
            if m != QMessageBox.StandardButton.Yes:
                return
        elif self.plan.conflicts:
            m = QMessageBox.warning(
                self,
                "덮어쓰기 확인",
                f"기존 파일 {len(self.plan.conflicts)}개를 덮어씁니다.\n계속할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if m != QMessageBox.StandardButton.Yes:
                return
        self.b_start.setEnabled(False)
        self.b_cancel.setEnabled(True)
        self.b_retry.setEnabled(False)
        self.startRequested.emit(self.overwrite.isChecked())

    def _retry(self):
        self.b_retry.setEnabled(False)
        self.b_cancel.setEnabled(True)
        self.retryRequested.emit()

    def _on_job(self, j: RenderJob):
        i = self.rows.get(j.id)
        if i is None:
            return
        txt = {
            "pending": "대기",
            "running": "진행 중",
            "done": "완료",
            "error": "오류",
            "cancelled": "취소됨",
            "skipped": "건너뜀",
        }[j.status]
        self.table.item(i, 1).setText(txt)
        self.bars[j.id].setValue(int(j.progress * 100))
        self.table.item(i, 3).setText("" if not j.eta or j.status != "running" else f"약 {int(j.eta)}초")
        note = j.error or (("약 " + fmt_bytes(j.result.get("size_bytes", 0))) if j.status == "done" else "")
        if j.status == "done" and j.result.get("audio_check", {}).get("clipping_risk"):
            note += "  ⚠ 오디오 피크가 0dBFS 근처 (클리핑 주의, 볼륨은 변경하지 않음)"
        if j.status == "skipped":
            note = "기존 파일 유지"
        self.table.item(i, 4).setText(note)
        total = sum(
            x.progress if x.status in ("running",) else (1.0 if x.status in ("done", "skipped") else 0.0)
            for x in self.plan.jobs
        )
        self.overall.setValue(int(1000 * total / max(1, len(self.plan.jobs))))

    def finished_state(self):
        self.b_cancel.setEnabled(False)
        failed = [j for j in self.plan.jobs if j.status in ("error", "cancelled")]
        self.b_retry.setEnabled(bool(failed))
        self.b_start.setEnabled(False)
        done = len([j for j in self.plan.jobs if j.status == "done"])
        self.setWindowTitle(f"렌더링 - 완료 {done}개" + (f" · 실패/취소 {len(failed)}개" if failed else ""))
        self.overall.setValue(1000 if not failed else self.overall.value())


# ============================================================================ 업로드 준비
class ReadyDialog(QDialog):
    def __init__(self, project, parent=None, toast=None):
        super().__init__(parent)
        self.project = project
        self.toast = toast
        self.setWindowTitle("업로드 준비")
        self.resize(980, 720)
        root = project.output_root()
        v = QVBoxLayout(self)
        top = QLabel(
            f"<b>{project.rec.name}</b> · 출력 폴더: {root}<br>"
            "<span style='color:#8b92a3'>자동 게시는 하지 않습니다. 파일을 열어 직접 업로드한 뒤 '업로드 완료'를 체크하세요.</span>"
        )
        top.setWordWrap(True)
        v.addWidget(top)
        hb = QPushButton("출력 폴더 열기")
        hb.clicked.connect(lambda: open_path(str(root)))
        v.addWidget(hb, 0, Qt.AlignmentFlag.AlignLeft)
        area = QScrollArea()
        area.setWidgetResizable(True)
        host = QWidget()
        self.lay = QVBoxLayout(host)
        area.setWidget(host)
        v.addWidget(area, 1)
        name = project.project_folder_name()
        cap = project.caption_result()
        # STORY
        self._section(
            "STORY",
            [("STORY 영상", root / "STORY" / f"{name}_STORY_01.mp4")],
            "story",
            project.state.story.enabled
            and f"{project.seconds(project.state.story.start, project.state.story.end):.1f}초",
        )
        # REELS
        self._section(
            "REELS",
            [
                ("REELS 영상", root / "REELS" / f"{name}_REELS_01.mp4"),
                ("커버 JPG", root / "REELS" / f"{name}_REELS_COVER.jpg"),
            ],
            "reels",
            project.state.reels.enabled
            and f"{project.seconds(project.state.reels.start, project.state.reels.end):.1f}초",
            copy_caption=True,
            cap=cap,
        )
        # FEED
        items = [
            (
                f"{n:02d} {'VIDEO' if it.kind == 'video' else 'STILL'}",
                root / "FEED" / f"{n:02d}_{'VIDEO.mp4' if it.kind == 'video' else 'STILL.jpg'}",
            )
            for n, it in enumerate(project.feed_included(), start=1)
        ]
        self._section("FEED (순서대로 업로드)", items, "feed", f"{len(items)}개", copy_caption=True, cap=cap)
        self.lay.addStretch(1)
        c = QPushButton("닫기")
        c.clicked.connect(self.accept)
        v.addWidget(c, 0, Qt.AlignmentFlag.AlignRight)

    def _section(self, title, files, key, meta, copy_caption=False, cap=None):
        box = QGroupBox(title + (f"  ·  {meta}" if meta else ""))
        bl = QVBoxLayout(box)
        exist_any = False
        grid = QHBoxLayout()
        for label, path in files:
            ok = Path(path).exists()
            exist_any |= ok
            col = QVBoxLayout()
            th = QLabel()
            th.setFixedSize(110, 130 if "FEED" in title or "COVER" in label.upper() or "커버" in label else 100)
            th.setStyleSheet("background:#12141a;border:1px solid #323744;")
            th.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if ok and str(path).lower().endswith(".jpg"):
                pm = QPixmap(str(path))
                th.setPixmap(
                    pm.scaled(th.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
            else:
                th.setText("🎬" if ok else "없음")
            col.addWidget(th)
            lab = QLabel(label + ("" if ok else "\n(렌더링 필요)"))
            lab.setWordWrap(True)
            lab.setMaximumWidth(120)
            col.addWidget(lab)
            ob = QPushButton("열기")
            ob.setEnabled(ok)
            ob.clicked.connect(lambda _=False, p=path: open_path(str(p)))
            fb = QPushButton("폴더")
            fb.clicked.connect(lambda _=False, p=path: open_path(str(Path(p).parent)))
            col.addWidget(ob)
            col.addWidget(fb)
            w = QWidget()
            w.setLayout(col)
            grid.addWidget(w)
        grid.addStretch(1)
        bl.addLayout(grid)
        if copy_caption and cap is not None:
            r = QHBoxLayout()
            b = QPushButton("캡션 복사")
            b.clicked.connect(lambda: self._copy(cap.caption))
            r.addWidget(b)
            for i, c in enumerate(cap.comments, start=1):
                bb = QPushButton(f"댓글 {i} 복사")
                bb.clicked.connect(lambda _=False, t=c: self._copy(t))
                r.addWidget(bb)
            r.addStretch(1)
            bl.addLayout(r)
        chk = QCheckBox("업로드 완료")
        chk.setChecked(bool(self.project.state.upload_checks.get(key)))
        chk.toggled.connect(lambda v, k=key: self.project.set_upload_check(k, v))
        bl.addWidget(chk)
        if not exist_any:
            warn = QLabel("출력 파일이 아직 없습니다. 먼저 렌더링을 실행하세요.")
            warn.setObjectName("warn")
            bl.addWidget(warn)
        self.lay.addWidget(box)

    def _copy(self, text):
        QApplication.clipboard().setText(text)
        if self.toast:
            self.toast("복사했습니다 (줄바꿈 유지)")


# ============================================================================ 단축키
class ShortcutsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("단축키 사용자 지정")
        self.resize(480, 640)
        v = QVBoxLayout(self)
        self.table = QTableWidget(len(DEFAULT_SHORTCUTS), 2)
        self.table.setHorizontalHeaderLabels(["동작", "단축키"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.edits: dict[str, QKeySequenceEdit] = {}
        for i, (k, (label, _d)) in enumerate(DEFAULT_SHORTCUTS.items()):
            self.table.setItem(i, 0, QTableWidgetItem(label))
            e = QKeySequenceEdit(QKeySequence(settings.shortcut(k)))
            self.table.setCellWidget(i, 1, e)
            self.edits[k] = e
        v.addWidget(self.table)
        r = QHBoxLayout()
        rb = QPushButton("기본값으로")
        rb.clicked.connect(self._reset)
        r.addWidget(rb)
        r.addStretch(1)
        ok = QPushButton("저장")
        ok.setObjectName("primary")
        ok.clicked.connect(self._save)
        cc = QPushButton("취소")
        cc.clicked.connect(self.reject)
        r.addWidget(cc)
        r.addWidget(ok)
        v.addLayout(r)

    def _reset(self):
        for k, e in self.edits.items():
            e.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS[k][1]))

    def _save(self):
        seqs = {k: e.keySequence().toString() for k, e in self.edits.items()}
        used: dict[str, str] = {}
        for k, s in seqs.items():
            if s and s in used:
                QMessageBox.warning(
                    self,
                    "단축키 중복",
                    f"'{s}' 가 '{DEFAULT_SHORTCUTS[used[s]][0]}' 와(과) "
                    f"'{DEFAULT_SHORTCUTS[k][0]}' 에 중복 지정되어 있습니다.",
                )
                return
            used[s] = k
        for k, s in seqs.items():
            self.settings.set_shortcut(k, s)
        self.accept()


# ============================================================================ ID 사전
class IdDictionaryDialog(QDialog):
    def __init__(self, iddict, parent=None):
        super().__init__(parent)
        self.iddict = iddict
        self.setWindowTitle("인스타그램 ID 사전")
        self.resize(720, 520)
        v = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("이름 또는 @아이디 검색")
        self.search.textChanged.connect(self.refresh)
        v.addWidget(self.search)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["이름", "@아이디", "종류", "메모"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._edited)
        v.addWidget(self.table, 1)
        f = QHBoxLayout()
        self.n_name, self.n_handle = QLineEdit(), QLineEdit()
        self.n_name.setPlaceholderText("이름")
        self.n_handle.setPlaceholderText("@아이디")
        self.n_kind = QComboBox()
        self.n_kind.addItems(["person", "company"])
        ab = QPushButton("추가")
        ab.clicked.connect(self._add)
        for w in (self.n_name, self.n_handle, self.n_kind, ab):
            f.addWidget(w)
        v.addLayout(f)
        r = QHBoxLayout()
        db = QPushButton("선택 삭제")
        db.setObjectName("danger")
        db.clicked.connect(self._delete)
        self.info = QLabel("동명이인은 자동 확정되지 않고 후보로만 제안됩니다.")
        self.info.setObjectName("muted")
        r.addWidget(db)
        r.addWidget(self.info, 1)
        cl = QPushButton("닫기")
        cl.clicked.connect(self.accept)
        r.addWidget(cl)
        v.addLayout(r)
        self._block = False
        self.refresh()

    def refresh(self):
        self._block = True
        self.entries = self.iddict.search(self.search.text())
        self.table.setRowCount(len(self.entries))
        for i, e in enumerate(self.entries):
            for c, t in enumerate([e.name, "@" + e.handle, e.kind, e.note]):
                it = QTableWidgetItem(t)
                it.setData(Qt.ItemDataRole.UserRole, e.id)
                self.table.setItem(i, c, it)
        self._block = False

    def _add(self):
        n, h = self.n_name.text().strip(), self.n_handle.text().strip()
        if not n or not h:
            return
        self.iddict.add(n, h, self.n_kind.currentText())
        self.n_name.clear()
        self.n_handle.clear()
        self.refresh()

    def _edited(self, item):
        if self._block:
            return
        r = item.row()
        e = self.entries[r]
        vals = [self.table.item(r, c).text() for c in range(4)]
        self.iddict.update(e.id, vals[0], vals[1], vals[2], vals[3])

    def _delete(self):
        r = self.table.currentRow()
        if 0 <= r < len(self.entries):
            self.iddict.delete(self.entries[r].id)
            self.refresh()

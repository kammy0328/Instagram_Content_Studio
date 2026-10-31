"""편집 창: 상단(프로젝트·자동저장·렌더링) / 좌측(STORY·REELS·FEED·CREDITS) / 중앙(미리보기) / 하단(타임라인) / 우측(선택 구간 설정)."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.layout import LayoutParams, compute_geometry
from ..core.model import FEED_MAX_FINAL
from ..render import engine
from ..render.planner import plan_render
from ..render.queue import RenderQueue
from . import theme
from .credits_page import CreditsPage
from .cutstrip import CutStrip
from .dialogs import (
    FeedManagerDialog,
    IdDictionaryDialog,
    MediaReportDialog,
    ReadyDialog,
    RenderDialog,
    ShortcutsDialog,
    XmlImportDialog,
)
from .panels import FeedPanel, SelectionPanel, TransportBar
from .preview import PreviewWidget
from .services import Decoder, FrameService, PlaybackEngine, ProxyBuilder, ThumbService, np_to_qimage
from .settings import DEFAULT_SHORTCUTS, Settings
from .timeline import TimelineWidget


class StillLoader(QObject):
    loaded = Signal(int, object)

    def load(self, token: int, info, index, frame: int, geo) -> None:
        def work():
            try:
                arr = engine.decode_still_rgb(info, index, frame, geo)
                self.loaded.emit(token, np_to_qimage(arr))
            except Exception:  # noqa: BLE001
                self.loaded.emit(token, None)

        threading.Thread(target=work, daemon=True).start()


class StillZoomDialog(QDialog):
    def __init__(self, img: QImage, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"스틸 확대 (100%) - {img.width()}×{img.height()}")
        self.resize(min(1100, img.width() + 40), min(860, img.height() + 40))
        v = QVBoxLayout(self)
        sc = QScrollArea()
        lb = QLabel()
        lb.setPixmap(QPixmap.fromImage(img))
        sc.setWidget(lb)
        v.addWidget(sc)


class CarouselView(QWidget):
    """인스타그램 캐러셀 형태 좌우 넘김 미리보기 (영상 재생 / 스틸 확대 / 순번·포함 상태)."""

    requestPlay = Signal(int, int)
    requestStop = Signal()

    def __init__(self, project):
        super().__init__()
        self.project = project
        self.items = []
        self.idx = 0
        self.token = 0
        self.canvas_img: QImage | None = None
        self.loader = StillLoader()
        self.loader.loaded.connect(self._loaded)
        v = QVBoxLayout(self)
        self.pw = PreviewWidget()
        v.addWidget(self.pw, 1)
        r = QHBoxLayout()
        self.b_prev = QPushButton("◀ 이전")
        self.b_next = QPushButton("다음 ▶")
        self.lbl = QLabel("")
        self.lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.b_play = QPushButton("▶ 영상 재생")
        self.b_zoom = QPushButton("🔍 스틸 확대")
        self.b_prev.clicked.connect(lambda: self.show_index(self.idx - 1))
        self.b_next.clicked.connect(lambda: self.show_index(self.idx + 1))
        self.b_play.clicked.connect(self._play)
        self.b_zoom.clicked.connect(self._zoom)
        for w in (self.b_prev, self.lbl, self.b_next, self.b_play, self.b_zoom):
            r.addWidget(w, 1 if w is self.lbl else 0)
        v.addLayout(r)
        self.dots = QLabel("")
        self.dots.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.dots)

    def refresh(self) -> None:
        cur = self.items[self.idx].id if self.items and self.idx < len(self.items) else None
        self.items = self.project.feed_included()
        self.idx = next(
            (i for i, it in enumerate(self.items) if it.id == cur), min(self.idx, max(0, len(self.items) - 1))
        )
        self.show_index(self.idx, keep=True)

    def show_index(self, i: int, keep: bool = False) -> None:
        if not self.items:
            self.lbl.setText("포함된 피드 항목이 없습니다")
            self.pw.set_image(0, None)
            self.dots.setText("")
            return
        self.idx = max(0, min(len(self.items) - 1, i))
        it = self.items[self.idx]
        self.lbl.setText(f"{self.idx + 1} / {len(self.items)}  ·  {'스틸' if it.kind == 'still' else '영상'}")
        self.dots.setText(" ".join("●" if k == self.idx else "○" for k in range(len(self.items))))
        self.b_prev.setEnabled(self.idx > 0)
        self.b_next.setEnabled(self.idx < len(self.items) - 1)
        self.b_play.setEnabled(it.kind == "video")
        self.b_zoom.setEnabled(it.kind == "still")
        self.requestStop.emit()
        m = self.project.media
        if it.kind == "still":
            geo = compute_geometry(m.display_w, m.display_h, "45", it.layout)
            self.token += 1
            self.pw.set_canvas_mode()
            self.loader.load(self.token, m, self.project.index, it.start, geo)
        else:
            self.pw.set_source_size(m.display_w, m.display_h)
            self.pw.set_output(True, "45", it.layout)
            self.canvas_img = None
            self.requestPlay.emit(it.start, -1)  # 첫 프레임만 표시

    def _loaded(self, token: int, img) -> None:
        if token != self.token or img is None:
            return
        self.canvas_img = img
        self.pw.set_canvas_mode()
        self.pw.set_image(0, img)

    def _play(self) -> None:
        if self.items and self.items[self.idx].kind == "video":
            it = self.items[self.idx]
            self.requestPlay.emit(it.start, it.end)

    def _zoom(self) -> None:
        if self.canvas_img is not None:
            StillZoomDialog(self.canvas_img, self).exec()


class EditorWindow(QMainWindow):
    saveStatus = Signal(str, str)
    requestProjects = Signal()
    jobSignal = Signal(object)
    queueFinished = Signal()
    closed = Signal()  # 창이 실제로 닫혔을 때 (렌더 중 취소한 경우는 발생하지 않음)

    def __init__(self, project, settings: Settings, iddict, cache_dir: Path):
        super().__init__()
        self.project, self.settings, self.iddict = project, settings, iddict
        info, index = project.media, project.index
        self.setWindowTitle(f"{project.rec.name} - Instagram Content Studio")
        self.resize(1560, 940)
        self.setStyleSheet(theme.STYLE)
        # ---- 서비스
        self.decoder = Decoder(info, index)
        self.frames = FrameService(self.decoder)
        self.thumbs = ThumbService(self.decoder, cache_dir, info)
        # 컷 스트립용: 타임라인 썸네일보다 크고 거친 격자는 만들지 않는다 (보이는 컷만 생성)
        self.cut_thumbs = ThumbService(self.decoder, cache_dir, info, height=72, subdir="cut_thumbs", coarse=False)
        self.playback = PlaybackEngine(self.decoder, self.frames)
        self.proxy = ProxyBuilder(info, cache_dir)
        self.render_queue: RenderQueue | None = None
        self.render_dialog: RenderDialog | None = None
        self.tab = "story"
        self.playhead = max(0, min(project.state.playhead, max(0, index.count - 1)))
        self.cover_mode = False
        self._range_source = "manual"  # FEED 영상 추가 구간의 출처: 'cuts'(선택한 컷) | 'manual'(In/Out 직접 지정)
        self.rate_level = 0
        self._refresh_pending = False
        self._layout_group = False
        self._closing = False
        self._over_notified = False
        self._build_ui()
        self._build_actions()
        # ---- 연결
        project.add_listener(self._on_project_change)
        project.status_listeners.append(lambda s, m: self.saveStatus.emit(s, m))
        self.saveStatus.connect(self._on_save_status)
        self.frames.frameReady.connect(self._on_frame_ready)
        self.playback.imageReady.connect(self._on_play_image)
        self.playback.stateChanged.connect(self.transport.set_playing)
        self.playback.positionChanged.connect(self._on_play_pos)
        self.jobSignal.connect(self._on_job)
        self.queueFinished.connect(self._on_queue_finished)
        self.proxy.progress.connect(lambda p: self.proxy_lbl.setText(f"프록시 생성 {int(p * 100)}%"))
        self.proxy.finished.connect(self._on_proxy_done)
        self.preview.set_source_size(info.display_w, info.display_h)
        self.carousel.pw.set_source_size(info.display_w, info.display_h)
        self.proxy.start() if not self.proxy.ready() else self._on_proxy_done(True, "")
        self._apply_ui_state()
        self.goto(self.playhead)
        self._refresh()
        QTimer.singleShot(150, self.timeline.fit)

    # ================================================================ UI 구성
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._topbar())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._nav())
        self.pages = QStackedWidget()
        self.pages.addWidget(self._editor_page())
        self.credits = CreditsPage(self.project, self.iddict)
        self.credits.toast.connect(self.toast)
        self.credits.openDictionary.connect(lambda: IdDictionaryDialog(self.iddict, self).exec())
        self.pages.addWidget(self.credits)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)
        self.toast_lbl = QLabel("", root)
        self.toast_lbl.setStyleSheet("background:#264a7a;border:1px solid #4c9dff;border-radius:6px;padding:8px 14px;")
        self.toast_lbl.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_lbl.hide)

    def _topbar(self) -> QWidget:
        f = QFrame()
        f.setObjectName("panel")
        f.setFixedHeight(46)
        h = QHBoxLayout(f)
        h.setContentsMargins(10, 4, 10, 4)
        b = QPushButton("← 프로젝트")
        b.clicked.connect(self._to_projects)
        h.addWidget(b)
        self.title_lbl = QLabel("")
        self.title_lbl.setObjectName("h1")
        h.addWidget(self.title_lbl)
        self.save_lbl = QLabel("● 저장됨")
        h.addWidget(self.save_lbl)
        self.proxy_lbl = QLabel("")
        self.proxy_lbl.setObjectName("muted")
        h.addWidget(self.proxy_lbl)
        h.addStretch(1)
        self.render_lbl = QLabel("")
        h.addWidget(self.render_lbl)
        self.b_undo = QPushButton("↶")
        self.b_redo = QPushButton("↷")
        self.b_undo.clicked.connect(self.do_undo)
        self.b_redo.clicked.connect(self.do_redo)
        for x in (self.b_undo, self.b_redo):
            x.setFixedWidth(36)
            h.addWidget(x)
        bm = QPushButton("영상 정보")
        bm.clicked.connect(lambda: MediaReportDialog(self.project.media, self).exec())
        bs = QPushButton("단축키")
        bs.clicked.connect(self.open_shortcuts)
        br = QPushButton("렌더링…")
        br.setObjectName("primary")
        br.clicked.connect(self.open_render)
        bu = QPushButton("업로드 준비…")
        bu.clicked.connect(self.open_ready)
        for x in (bm, bs, bu, br):
            h.addWidget(x)
        return f

    def _nav(self) -> QWidget:
        f = QFrame()
        f.setObjectName("panel")
        f.setFixedWidth(150)
        v = QVBoxLayout(f)
        v.setContentsMargins(8, 10, 8, 10)
        self.nav_group = QButtonGroup(self)
        self.nav_btns: dict[str, QPushButton] = {}
        for key, label in (("story", "STORY"), ("reels", "REELS"), ("feed", "FEED"), ("credits", "CREDITS")):
            b = QPushButton(label)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.set_tab(k))
            self.nav_group.addButton(b)
            v.addWidget(b)
            self.nav_btns[key] = b
        self.nav_btns["story"].setChecked(True)
        v.addStretch(1)
        self.nav_info = QLabel("")
        self.nav_info.setObjectName("muted")
        self.nav_info.setWordWrap(True)
        v.addWidget(self.nav_info)
        return f

    def _editor_page(self) -> QWidget:
        sp = QSplitter(Qt.Orientation.Vertical)
        top = QSplitter(Qt.Orientation.Horizontal)
        # --- 중앙
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        self.center_stack = QStackedWidget()
        self.preview = PreviewWidget()
        self.carousel = CarouselView(self.project)
        self.carousel.requestPlay.connect(self._carousel_play)
        self.carousel.requestStop.connect(self.playback.pause)
        self.center_stack.addWidget(self.preview)
        self.center_stack.addWidget(self.carousel)
        cv.addWidget(self.center_stack, 1)
        self.transport = TransportBar(self.project)
        self.transport.playClicked.connect(self.toggle_play)
        self.transport.stepClicked.connect(lambda d: self.step(d))
        self.transport.gotoClicked.connect(lambda w: self.goto(0 if w == "start" else self.project.count - 1))
        self.transport.frameEntered.connect(self.goto)
        self.transport.viewToggled.connect(lambda _v: self._update_preview_layout())
        self.transport.safe.toggled.connect(self._safe_toggled)
        cv.addWidget(self.transport)
        top.addWidget(center)
        # --- 우측 인스펙터
        self.inspector = QStackedWidget()
        self.story_panel = SelectionPanel("story", self.project)
        self.reels_panel = SelectionPanel("reels", self.project)
        self.feed_panel = FeedPanel(self.project, self.thumbs)
        for w in (self.story_panel, self.reels_panel, self.feed_panel):
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setWidget(w)
            sc.setFrameShape(QFrame.Shape.NoFrame)
            self.inspector.addWidget(sc)
        self.inspector.setMinimumWidth(350)
        top.addWidget(self.inspector)
        top.setSizes([900, 380])
        top.setStretchFactor(0, 1)
        sp.addWidget(top)
        # --- 하단 타임라인
        bottom = QWidget()
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(0)
        bv.addWidget(self._cut_toolbar())
        self.cut_strip = CutStrip(self.project, self.cut_thumbs)
        self.cut_strip.selectionIdsChanged.connect(self._strip_selected)
        self.cut_strip.cutActivated.connect(self.goto)
        bv.addWidget(self.cut_strip)
        self.timeline = TimelineWidget(self.project, self.thumbs)
        self.timeline.playheadRequested.connect(self.goto)
        self.timeline.cutSelectionChanged.connect(self._timeline_selected)
        self.timeline.feedItemClicked.connect(self._feed_clicked)
        bv.addWidget(self.timeline)
        sp.addWidget(bottom)
        sp.setSizes([520, 420])
        sp.setStretchFactor(0, 1)
        # --- 패널 연결
        for which, panel in (("story", self.story_panel), ("reels", self.reels_panel)):
            panel.presetClicked.connect(lambda p, w=which: self.apply_preset(w, p))
            panel.inOutEntered.connect(lambda a, b, w=which: self._warn(self.project.set_range(w, a, b)))
            panel.setIn.connect(self.set_in)
            panel.setOut.connect(self.set_out)
            panel.playRange.connect(self.play_range)
            panel.clearClicked.connect(lambda w=which: self.project.clear_selection(w))
            panel.layoutLive.connect(lambda lp, w=which: self._layout_live(w, lp))
            panel.layoutCommit.connect(lambda _lp: self._layout_commit())
        self.reels_panel.coverSet.connect(lambda: self.project.set_cover_frame(self.playhead))
        self.reels_panel.coverGoto.connect(self._goto_cover)
        self.reels_panel.coverChanged.connect(lambda a, o: self.project.set_cover_grid(a, o))
        self.reels_panel.coverPreview.connect(self._cover_preview)
        self.reels_panel.coverLayoutLive.connect(lambda lp: self._layout_live("cover", lp))
        self.reels_panel.coverLayoutCommit.connect(lambda _lp: self._layout_commit())
        fp = self.feed_panel
        fp.addStill.connect(self.add_still)
        fp.addVideo.connect(self.add_video)
        fp.itemSelected.connect(self._feed_selected)
        fp.includeToggled.connect(lambda i, v: self._feed_include(i, v))
        fp.deleteClicked.connect(self._feed_delete)
        fp.manageClicked.connect(self.open_feed_manager)
        fp.carouselToggled.connect(self._carousel_toggled)
        fp.layoutLive.connect(lambda lp: self._layout_live("feed", lp))
        fp.layoutCommit.connect(lambda _lp: self._layout_commit())
        fp.setIn.connect(self.set_in)
        fp.setOut.connect(self.set_out)
        fp.inOutEntered.connect(self._feed_inout_entered)
        fp.playItem.connect(self._feed_play)
        # --- 미리보기 드래그
        self.preview.dragStarted.connect(lambda: self.project.begin_group("화면 위치 조정"))
        self.preview.dragFinished.connect(self.project.end_group)
        self.preview.layoutChanged.connect(self._preview_layout_dragged)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._layout_commit)
        return sp

    def _cut_toolbar(self) -> QWidget:
        f = QFrame()
        f.setObjectName("panel")
        h = QHBoxLayout(f)
        h.setContentsMargins(8, 3, 8, 3)

        def btn(text, fn, tip="", checkable=False):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            b.setCheckable(checkable)
            h.addWidget(b)
            return b

        btn("XML 불러오기", self.import_xml, "Premiere/FCP7 XML · FCPXML · EDL 컷 정보 (선택 사항)")
        btn("병합 (M)", self.merge_selected, "Shift+클릭으로 선택한 연속 컷을 하나의 논리 컷으로 병합")
        btn("병합 해제", self.unmerge_selected)
        btn("원래 컷 복원", self.restore_cuts)
        btn("선택 컷 → In/Out", self.cuts_to_inout, "선택한 (병합) 컷 범위를 현재 탭의 In/Out 으로 지정")
        self.mode_btn = btn("편집: 롤", self.toggle_mode, "롤: 맞닿은 두 경계를 함께 이동 / 트림: 한쪽 경계만 이동 (R)")
        self.snap_btn = btn("스냅 ✓", self.toggle_snap, "스냅 켜기/끄기 (N)")
        h.addStretch(1)
        self.cut_info = QLabel("")
        self.cut_info.setObjectName("muted")
        h.addWidget(self.cut_info)
        for t, fn in (
            ("－", lambda: self.timeline.zoom(0.8)),
            ("＋", lambda: self.timeline.zoom(1.25)),
            ("전체", lambda: self.timeline.fit()),
        ):
            b = QPushButton(t)
            b.setFixedWidth(46 if t == "전체" else 32)
            b.clicked.connect(fn)
            h.addWidget(b)
        return f

    # ================================================================ 액션/단축키
    def _build_actions(self) -> None:
        self.actions: dict[str, QAction] = {}
        fn = {
            "play_pause": self.toggle_play,
            "shuttle_back": lambda: self.shuttle(-1),
            "shuttle_stop": self.playback.pause,
            "shuttle_fwd": lambda: self.shuttle(1),
            "step_back": lambda: self.step(-1),
            "step_fwd": lambda: self.step(1),
            "step_back_10": lambda: self.step(-10),
            "step_fwd_10": lambda: self.step(10),
            "goto_start": lambda: self.goto(0),
            "goto_end": lambda: self.goto(self.project.count - 1),
            "prev_cut": lambda: self.jump_cut(-1),
            "next_cut": lambda: self.jump_cut(1),
            "set_in": self.set_in,
            "set_out": self.set_out,
            "loop_play": self.play_range,
            "undo": self.do_undo,
            "redo": self.do_redo,
            "zoom_in": lambda: self.timeline.zoom(1.25),
            "zoom_out": lambda: self.timeline.zoom(0.8),
            "zoom_fit": self.timeline.fit,
            "add_still": self.add_still,
            "add_video": self.add_video,
            "merge_cuts": self.merge_selected,
            "toggle_view": lambda: self.transport.view_btn.toggle(),
            "toggle_mode": self.toggle_mode,
            "toggle_snap": self.toggle_snap,
            "tab_story": lambda: self.set_tab("story"),
            "tab_reels": lambda: self.set_tab("reels"),
            "tab_feed": lambda: self.set_tab("feed"),
            "tab_credits": lambda: self.set_tab("credits"),
        }
        for name, (label, _d) in DEFAULT_SHORTCUTS.items():
            a = QAction(label, self)
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            a.triggered.connect(lambda _=False, f=fn[name]: f())
            self.addAction(a)
            self.actions[name] = a
        self.apply_shortcuts()
        extra = QAction(self)
        extra.setShortcut(QKeySequence("Ctrl+Y"))
        extra.triggered.connect(self.do_redo)
        self.addAction(extra)
        extra2 = QAction(self)
        extra2.setShortcut(QKeySequence("+"))
        extra2.triggered.connect(lambda: self.timeline.zoom(1.25))
        self.addAction(extra2)

    def apply_shortcuts(self) -> None:
        for name, a in self.actions.items():
            a.setShortcut(QKeySequence(self.settings.shortcut(name)))

    def open_shortcuts(self) -> None:
        if ShortcutsDialog(self.settings, self).exec():
            self.apply_shortcuts()
            self.toast("단축키를 저장했습니다.")

    # ================================================================ 탭
    def set_tab(self, key: str) -> None:
        self.tab = key
        self.nav_btns[key].setChecked(True)
        self.playback.pause()
        if key == "credits":
            self.pages.setCurrentIndex(1)
            self.credits.refresh()
        else:
            self.pages.setCurrentIndex(0)
            self.inspector.setCurrentIndex({"story": 0, "reels": 1, "feed": 2}[key])
            self.timeline.active = key
            if key != "reels":
                self.cover_mode = False
                self.reels_panel.cover_prev.blockSignals(True)
                self.reels_panel.cover_prev.setChecked(False)
                self.reels_panel.cover_prev.blockSignals(False)
            if key != "feed":
                self.feed_panel.car_btn.setChecked(False)
            self._update_preview_layout()
            self.timeline.update()
        self.project.set_ui("tab", key)

    # ================================================================ 이동/재생
    def goto(self, n: int, request: bool = True) -> None:
        n = self.project.index.clamp(n)
        if self.playback.playing and request:
            self.playback.pause()
        self.playhead = n
        self.project.set_playhead(n)
        self.timeline.set_playhead(n, follow=True)
        self.cut_strip.set_playhead(n)
        self.transport.set_frame(n)
        if request:
            self._request_display_frame()

    def _display_frame(self) -> int:
        if self.cover_mode and self.project.state.cover.frame is not None:
            return self.project.state.cover.frame
        return self.playhead

    def _request_display_frame(self) -> None:
        self.frames.request(self._display_frame())

    def _on_frame_ready(self, n: int, img: QImage) -> None:
        if self.playback.playing and self.playback.rate > 0:
            return
        if n == self._display_frame():
            self.preview.set_image(n, img)
        it = (
            self.carousel.items[self.carousel.idx]
            if self.carousel.items and self.carousel.idx < len(self.carousel.items)
            else None
        )
        if (
            it is not None
            and it.kind == "video"
            and n == it.start
            and self.center_stack.currentIndex() == 1
            and not self.playback.playing
        ):
            self.carousel.pw.set_image(n, img)

    def _on_play_image(self, n: int, img: QImage) -> None:
        self.preview.set_image(n, img)
        if self.center_stack.currentIndex() == 1:
            self.carousel.pw.set_image(n, img)

    def _on_play_pos(self, n: int) -> None:
        self.playhead = n
        self.project.set_playhead(n)
        self.timeline.set_playhead(n, follow=True)
        self.cut_strip.set_playhead(n)
        self.transport.set_frame(n)

    def _active_range(self) -> tuple[int, int] | None:
        st = self.project.state
        if self.tab in ("story", "reels"):
            sel = st.story if self.tab == "story" else st.reels
            return (sel.start, sel.end) if sel.enabled else None
        if self.tab == "feed":
            it = self.project.get_feed(self.feed_panel.current_id()) if self.feed_panel.current_id() else None
            if it is not None and it.kind == "video":
                return (it.start, it.end)
            a, b = st.ui.get("feed_in"), st.ui.get("feed_out")
            if a is not None and b is not None and b > a:
                return (a, b)
        return None

    def toggle_play(self) -> None:
        if self.playback.playing:
            self.playback.pause()
            self.rate_level = 0
            self.transport.rate_lbl.setText("")
            return
        loop = self._active_range() if self.transport.loop.isChecked() else None
        start = self.playhead
        if loop and not (loop[0] <= start < loop[1]):
            start = loop[0]
        self.rate_level = 1
        self.playback.play(start, 1.0, loop)

    def play_range(self) -> None:
        r = self._active_range()
        if not r:
            self.toast("재생할 구간이 지정되지 않았습니다.")
            return
        self.transport.loop.setChecked(True)
        self.playback.play(r[0], 1.0, r)

    def shuttle(self, direction: int) -> None:
        cur = self.playback.rate if self.playback.playing else 0
        if direction > 0:
            rate = 1.0 if cur <= 0 else min(8.0, cur * 2)
        else:
            rate = -1.0 if cur >= 0 else max(-8.0, cur * 2)
        self.transport.rate_lbl.setText(f"{rate:g}×")
        loop = self._active_range() if self.transport.loop.isChecked() else None
        self.playback.play(self.playhead, rate, loop)

    def step(self, d: int) -> None:
        self.playback.pause()
        self.goto(self.playhead + d)

    def jump_cut(self, d: int) -> None:
        cs = self.project.state.cuts
        if not cs:
            return
        starts = sorted({c.start for c in cs} | {cs[-1].end - 1})
        if d > 0:
            nxt = [s for s in starts if s > self.playhead]
            self.goto(nxt[0] if nxt else starts[-1])
        else:
            prv = [s for s in starts if s < self.playhead]
            self.goto(prv[-1] if prv else 0)

    def _carousel_play(self, start: int, end: int) -> None:
        if end < 0:
            self.frames.request(start)
            return
        self.playback.play(start, 1.0, (start, end))

    # ================================================================ 구간/In-Out
    def apply_preset(self, which: str, preset: str) -> None:
        self._warn(self.project.apply_preset(which, self.playhead, preset))

    def set_in(self) -> None:
        p = self.project
        if self.tab in ("story", "reels"):
            sel = p.state.story if self.tab == "story" else p.state.reels
            end = (
                sel.end
                if sel.enabled and sel.end > self.playhead
                else min(p.count, self.playhead + p.max_len_frames(self.tab))
            )
            self._warn(p.set_range(self.tab, self.playhead, end))
        elif self.tab == "feed":
            self._adopt_cut_range()
            p.set_ui("feed_in", self.playhead)
            if p.state.ui.get("feed_out", 0) <= self.playhead:
                p.set_ui("feed_out", min(p.count, self.playhead + 1))
            self._update_feed_pending()
            self.timeline.update()

    def set_out(self) -> None:
        p = self.project
        end = self.playhead + 1  # Out 은 현재 프레임을 포함
        if self.tab in ("story", "reels"):
            sel = p.state.story if self.tab == "story" else p.state.reels
            start = sel.start if sel.enabled and sel.start < end else max(0, end - 1)
            self._warn(p.set_range(self.tab, start, end))
        elif self.tab == "feed":
            self._adopt_cut_range()
            p.set_ui("feed_out", end)
            if p.state.ui.get("feed_in", 0) >= end:
                p.set_ui("feed_in", max(0, end - 1))
            self._update_feed_pending()

    def _feed_pending_range(self) -> tuple[int, int] | None:
        """FEED 영상 추가가 사용할 구간. 컷을 가장 최근에 선택했다면 선택한 컷들의 시작~끝(Shift 다중 선택이면 그 범위 전체),
        In/Out 을 직접 지정했다면 그 값이다. 예전에 남은 In/Out 이 새로 선택한 컷보다 우선하지 않도록 출처를 구분한다."""
        p = self.project
        if self._range_source == "cuts" and self.timeline.sel_ids:
            want = set(self.timeline.sel_ids)
            cs = [c for c in p.state.cuts if c.id in want]
            if cs:
                return min(c.start for c in cs), max(c.end for c in cs)
        ui = p.state.ui
        a, b = ui.get("feed_in"), ui.get("feed_out")
        if a is not None and b is not None and b > a:
            return int(a), int(b)
        return None

    def _adopt_cut_range(self) -> None:
        """선택한 컷 범위를 In/Out 값으로 확정한다 (이후 I/O 로 한쪽만 고쳐도 나머지는 컷 범위를 유지)."""
        if self._range_source == "cuts":
            r = self._feed_pending_range()
            if r:
                self.project.set_ui("feed_in", r[0])
                self.project.set_ui("feed_out", r[1])
            self._range_source = "manual"

    def _update_feed_pending(self) -> None:
        r = self._feed_pending_range()
        if r:
            self.feed_panel.set_pending(*r)
            return
        ui = self.project.state.ui
        self.feed_panel.set_pending(ui.get("feed_in", self.playhead), ui.get("feed_out", self.playhead + 1))

    def _feed_inout_entered(self, a: int, b: int) -> None:
        self._range_source = "manual"
        self.project.set_ui("feed_in", a)
        self.project.set_ui("feed_out", max(a + 1, b))
        self._update_feed_pending()

    # ================================================================ 피드
    def add_still(self) -> None:
        was = len(self.project.feed_included())
        it = self.project.add_feed_still(self.playhead)
        self.tab != "feed" and self.set_tab("feed")
        self.feed_panel.select(it.id)
        self.toast(f"스틸 추가: f{self.playhead} ({self.project.tc(self.playhead)})")
        self._check_over(was)

    def add_video(self) -> None:
        p = self.project
        r = self._feed_pending_range()
        if r is None:
            self.toast("In/Out 을 지정하세요 (I / O) 또는 컷을 선택하세요.")
            return
        a, b = r
        was = len(p.feed_included())
        it, msg = p.add_feed_video(a, b)
        self._warn(msg)
        self.tab != "feed" and self.set_tab("feed")
        self.feed_panel.select(it.id)
        self.toast(f"영상 추가: {p.seconds(it.start, it.end):.2f}초")
        self._check_over(was)

    def _check_over(self, was: int) -> None:
        now = len(self.project.feed_included())
        if now > FEED_MAX_FINAL >= was:
            QTimer.singleShot(50, self.open_feed_manager)

    def _feed_selected(self, item_id: str) -> None:
        it = self.project.get_feed(item_id)
        self.timeline.sel_feed = item_id
        if it:
            self.goto(it.start)
        self.feed_panel.refresh()
        self._update_preview_layout()
        self.timeline.update()

    def _feed_clicked(self, item_id: str) -> None:
        self.tab != "feed" and self.set_tab("feed")
        self.feed_panel.select(item_id)

    def _feed_include(self, item_id: str, v: bool) -> None:
        was = len(self.project.feed_included())
        self.project.set_feed_included([item_id], v)
        self._check_over(was)

    def _feed_delete(self, item_id: str) -> None:
        if (
            QMessageBox.question(self, "삭제", "이 피드 항목을 완전히 삭제할까요?\n(제외만 하려면 체크를 해제하세요)")
            == QMessageBox.StandardButton.Yes
        ):
            self.project.remove_feed(item_id)

    def _feed_play(self, item_id: str) -> None:
        it = self.project.get_feed(item_id)
        if it is None:
            return
        self.goto(it.start)
        if it.kind == "video":
            self.playback.play(it.start, 1.0, (it.start, it.end))

    def open_feed_manager(self) -> None:
        FeedManagerDialog(self.project, self.thumbs, self).exec()
        self._refresh()

    def _carousel_toggled(self, on: bool) -> None:
        self.center_stack.setCurrentIndex(1 if on else 0)
        if on:
            self.carousel.refresh()

    # ================================================================ 컷
    def import_xml(self) -> None:
        dlg = XmlImportDialog(self.project, self)
        p, _ = QFileDialog.getOpenFileName(self, "편집 XML", "", "편집 정보 (*.xml *.fcpxml *.edl);;모든 파일 (*)")
        if not p:
            return
        dlg.load(p)
        if dlg.doc is None:
            return
        if dlg.exec():
            self.toast(f"컷 {len(self.project.state.cuts)}개를 불러왔습니다.")
            self.timeline.update()

    def _set_cut_selection(self, ids: list[int]) -> None:
        """컷 선택을 타임라인과 컷 스트립에 함께 반영한다."""
        self.timeline.sel_ids = list(ids)
        self.timeline.anchor_id = ids[0] if ids else None
        self.cut_strip.refresh()  # 병합 직후처럼 컷 목록이 방금 바뀐 경우를 위해 타일을 먼저 맞춘다
        self.cut_strip.set_selected_ids(ids)
        self.timeline.update()
        self._on_cut_selection()

    def _strip_selected(self, ids: list) -> None:
        self.timeline.sel_ids = list(ids)
        self.timeline.anchor_id = ids[0] if ids else None
        self.timeline.update()
        self._on_cut_selection()

    def _timeline_selected(self, ids: list) -> None:
        self.cut_strip.set_selected_ids(ids)
        self._on_cut_selection()

    def _on_cut_selection(self) -> None:
        if self.timeline.sel_ids:
            self._range_source = "cuts"
        self._update_cut_info()
        self._update_feed_pending()

    def merge_selected(self) -> None:
        ids = self.timeline.sel_ids
        try:
            self.project.merge_cuts(ids)
        except ValueError as e:
            self.toast(str(e))
            return
        new = self.project.state.cuts
        merged = [c for c in new if c.merged]
        self._set_cut_selection([merged[-1].id] if merged else [])
        self.toast("컷을 병합했습니다 (원본/XML 은 변경되지 않음)")

    def unmerge_selected(self) -> None:
        cs = [c for c in self.project.state.cuts if c.id in self.timeline.sel_ids and c.merged]
        if not cs:
            self.toast("병합된 컷을 선택하세요.")
            return
        self.project.begin_group("병합 해제")
        try:
            for c in cs:
                self.project.unmerge_cut(c.id)
        finally:
            self.project.end_group()
        self._set_cut_selection([])

    def restore_cuts(self) -> None:
        if not self.project.state.orig_cuts:
            self.toast("복원할 원본 컷 목록이 없습니다 (XML 을 먼저 불러오세요).")
            return
        if (
            QMessageBox.question(
                self,
                "원래 컷 목록 복원",
                "병합과 경계 보정을 모두 되돌리고 XML 원본 컷 목록으로 복원합니다.\n"
                "(Undo 로 다시 되돌릴 수 있습니다) 계속할까요?",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.project.restore_original_cuts()
            self._set_cut_selection([])

    def cuts_to_inout(self) -> None:
        cs = [c for c in self.project.state.cuts if c.id in self.timeline.sel_ids]
        if not cs:
            self.toast("컷을 선택하세요.")
            return
        a, b = min(c.start for c in cs), max(c.end for c in cs)
        if self.tab in ("story", "reels"):
            self._warn(self.project.set_range(self.tab, a, b))
        elif self.tab == "feed":
            self._range_source = "manual"
            self.project.set_ui("feed_in", a)
            self.project.set_ui("feed_out", b)
            self._update_feed_pending()
        self.goto(a)

    def toggle_mode(self) -> None:
        self.timeline.edit_mode = "trim" if self.timeline.edit_mode == "roll" else "roll"
        self.mode_btn.setText("편집: 트림" if self.timeline.edit_mode == "trim" else "편집: 롤")
        self.toast(
            "트림: 한쪽 경계만 이동 (겹침/빈 구간 발생 가능)"
            if self.timeline.edit_mode == "trim"
            else "롤: 맞닿은 두 경계를 함께 이동"
        )
        self.timeline.update()

    def toggle_snap(self) -> None:
        self.timeline.snap_on = not self.timeline.snap_on
        self.snap_btn.setText("스냅 ✓" if self.timeline.snap_on else "스냅 ✗")

    def _update_cut_info(self) -> None:
        cs = self.project.state.cuts
        if not cs:
            self.cut_info.setText("컷 정보 없음 (XML 없이도 프레임 단위 편집 가능)")
            return
        sel = [c for c in cs if c.id in self.timeline.sel_ids]
        iss = self.project.cut_issues()
        xml_name = Path(str(self.project.state.xml_meta.get("file", ""))).name
        txt = f"컷 {len(cs)}개" + (f" · XML {xml_name}" if xml_name else "") + " (STORY/REELS/FEED 공용)"
        if sel:
            nums = [cs.index(c) + 1 for c in sel]
            txt += f" · 선택 #{min(nums)}~#{max(nums)} ({len(sel)}개, {self.project.seconds(min(c.start for c in sel), max(c.end for c in sel)):.2f}s)"
        if iss:
            txt += f" · ⚠ 겹침/빈 구간 {len(iss)}곳"
        self.cut_info.setText(txt)

    # ================================================================ 레이아웃 편집
    def _layout_live(self, target: str, lp: LayoutParams) -> None:
        if not self._layout_group:
            self.project.begin_group("화면 배치")
            self._layout_group = True
        p = self.project
        if target in ("story", "reels"):
            p.set_selection_layout(target, lp)
        elif target == "cover":
            p.set_cover_layout(lp)
        elif target == "feed":
            cid = self.feed_panel.current_id()
            if cid:
                p.set_feed_layout(cid, lp)
        self.preview.layout = lp
        self.preview.update()
        self._layout_timer.start(600)

    def _layout_commit(self) -> None:
        self._layout_timer.stop()
        if self._layout_group:
            self._layout_group = False
            self.project.end_group()

    def _preview_layout_dragged(self, lp: LayoutParams) -> None:
        p = self.project
        if self.cover_mode:
            p.set_cover_layout(lp)
        elif self.tab in ("story", "reels"):
            p.set_selection_layout(self.tab, lp)
        elif self.tab == "feed" and self.feed_panel.current_id():
            p.set_feed_layout(self.feed_panel.current_id(), lp)

    def _cover_preview(self, on: bool) -> None:
        self.cover_mode = on
        self._update_preview_layout()
        self._request_display_frame()

    def _goto_cover(self) -> None:
        f = self.project.state.cover.frame
        if f is not None:
            self.goto(f)

    def _safe_toggled(self, on: bool) -> None:
        self.preview.safe_zone = on
        self.preview.update()

    def _update_preview_layout(self) -> None:
        st = self.project.state
        view_out = self.transport.view_btn.isChecked()
        if self.cover_mode:
            kind, lp = "916", st.cover.layout
            self.preview.cover_grid = (st.cover.grid_aspect, st.cover.grid_offset)
            self.preview.label = "릴스 커버 미리보기"
        else:
            self.preview.cover_grid = None
            self.preview.label = ""
            if self.tab == "feed":
                kind = "45"
                cid = self.feed_panel.current_id()
                it = self.project.get_feed(cid) if cid else None
                lp = it.layout if it else LayoutParams()
            else:
                kind = "916"
                lp = (st.story if self.tab == "story" else st.reels).layout
        self.preview.set_output(view_out or self.cover_mode, kind, lp)

    # ================================================================ 렌더링 / 업로드 준비
    def open_render(self) -> None:
        if self.render_queue and self.render_queue.running and self.render_dialog:
            self.render_dialog.show()
            self.render_dialog.raise_()
            return
        if self.project.feed_over_limit():
            self.open_feed_manager()
            if self.project.feed_over_limit():
                return
        self.playback.pause()
        plan = plan_render(self.project)
        dlg = RenderDialog(plan, self)
        self.render_dialog = dlg
        dlg.startRequested.connect(lambda ow, d=dlg, pl=plan: self._start_render(pl, ow))
        dlg.cancelRequested.connect(lambda: self.render_queue and self.render_queue.cancel_all())
        dlg.retryRequested.connect(lambda: self.render_queue and self.render_queue.retry_failed())
        dlg.show()

    def _start_render(self, plan, overwrite: bool) -> None:
        self.project.autosaver.flush()
        self.render_queue = RenderQueue(
            self.project.media,
            self.project.index,
            plan.root,
            self.project.project_data_files(),
            on_update=lambda j: self.jobSignal.emit(j),
            on_finished=lambda: self.queueFinished.emit(),
        )
        self.render_queue.start(plan.jobs, overwrite)
        self.render_lbl.setText("렌더링 시작…")

    def _on_job(self, j) -> None:
        if self.render_dialog:
            self.render_dialog.jobUpdated.emit(j)
        if self.render_queue:
            jobs = self.render_queue.jobs
            done = len([x for x in jobs if x.status in ("done", "skipped", "error", "cancelled")])
            self.render_lbl.setText(
                f"렌더링 {done}/{len(jobs)} · {j.label} {int(j.progress * 100)}%"
                if j.status == "running"
                else f"렌더링 {done}/{len(jobs)}"
            )

    def _on_queue_finished(self) -> None:
        jobs = self.render_queue.jobs if self.render_queue else []
        fail = [j for j in jobs if j.status in ("error", "cancelled")]
        self.render_lbl.setText(
            f"렌더링 완료 ({len(jobs) - len(fail)}/{len(jobs)})" + (f" · 실패 {len(fail)}" if fail else "")
        )
        if self.render_dialog:
            self.render_dialog.finished_state()
        self.toast("렌더링이 끝났습니다." if not fail else f"렌더링 완료 - 실패/취소 {len(fail)}개 (재시도 가능)")

    def open_ready(self) -> None:
        ReadyDialog(self.project, self, self.toast).exec()

    # ================================================================ Undo/Redo
    def do_undo(self) -> None:
        self._layout_commit()
        lab = self.project.undo()
        self.toast(f"실행 취소: {lab}" if lab else "되돌릴 작업이 없습니다.")

    def do_redo(self) -> None:
        lab = self.project.redo()
        self.toast(f"다시 실행: {lab}" if lab else "다시 실행할 작업이 없습니다.")

    # ================================================================ 갱신
    def _on_project_change(self, kind: str) -> None:
        if kind == "info":
            self._update_title()
        if not self._refresh_pending:
            self._refresh_pending = True
            QTimer.singleShot(0, self._refresh)

    def _refresh(self) -> None:
        self._refresh_pending = False
        if self._closing:
            return
        self._update_title()
        self.story_panel.refresh()
        self.reels_panel.refresh()
        self.feed_panel.refresh()
        self._update_feed_pending()
        self._update_preview_layout()
        self.timeline.update()
        self.cut_strip.refresh(self.timeline.sel_ids)
        self._update_cut_info()
        self.b_undo.setEnabled(self.project.can_undo())
        self.b_redo.setEnabled(self.project.can_redo())
        self.b_undo.setToolTip(f"실행 취소: {self.project.undo_label()}" if self.project.can_undo() else "")
        self.b_redo.setToolTip(f"다시 실행: {self.project.redo_label()}" if self.project.can_redo() else "")
        st = self.project.state
        n = len(self.project.feed_included())
        self.nav_btns["story"].setText("STORY" + ("  ✓" if st.story.enabled else ""))
        self.nav_btns["reels"].setText("REELS" + ("  ✓" if st.reels.enabled else ""))
        self.nav_btns["feed"].setText(f"FEED  {n}/{FEED_MAX_FINAL}")
        self.nav_info.setText(
            f"{self.project.media.display_w}×{self.project.media.display_h}\n"
            f"{float(self.project.fps):.3f} fps · {self.project.count} 프레임"
        )
        if self.pages.currentIndex() == 1:
            self.credits.refresh()
        if self.center_stack.currentIndex() == 1:
            self.carousel.refresh()
        self._request_display_frame()

    def _update_title(self) -> None:
        r = self.project.rec
        self.title_lbl.setText(f"{r.name}" + (f" · {r.artist}" if r.artist else ""))
        self.setWindowTitle(f"{r.name} - Instagram Content Studio")

    def _apply_ui_state(self) -> None:
        ui = self.project.state.ui
        self.set_tab(ui.get("tab", "story"))

    def _on_save_status(self, status: str, msg: str) -> None:
        text, color = {
            "saved": ("● 저장됨", "#4cd08b"),
            "saving": ("저장 중…", "#f2b84b"),
            "dirty": ("● 변경됨", "#8b92a3"),
            "error": ("⚠ 저장 오류", "#ff5d6c"),
        }[status]
        self.save_lbl.setText(text)
        self.save_lbl.setStyleSheet(f"color:{color};font-weight:700;")
        self.save_lbl.setToolTip(msg)
        if status == "error":
            self.toast(msg or "저장에 실패했습니다. 디스크 공간/권한을 확인하세요.", 6000)

    def _on_proxy_done(self, ok: bool, err: str) -> None:
        if ok and self.proxy.index is not None:
            self.decoder.set_proxy(str(self.proxy.video_path), self.proxy.index)
            self.frames.clear_cache()
            if self.proxy.audio_path.is_file():
                self.playback.set_audio(str(self.proxy.audio_path))
            self.proxy_lbl.setText("프록시 준비됨 (재생 전용 · 최종 렌더링은 항상 원본)")
            self._request_display_frame()
        elif ok:
            self.proxy_lbl.setText("프록시 준비됨")
        else:
            self.proxy_lbl.setText("프록시 생성 실패 - 원본으로 재생합니다")
            self.proxy_lbl.setToolTip(err)

    # ================================================================ 기타
    def toast(self, text: str, ms: int = 2600) -> None:
        self.toast_lbl.setText(text)
        self.toast_lbl.adjustSize()
        w = self.centralWidget()
        self.toast_lbl.move((w.width() - self.toast_lbl.width()) // 2, w.height() - self.toast_lbl.height() - 60)
        self.toast_lbl.show()
        self.toast_lbl.raise_()
        self._toast_timer.start(ms)

    def _warn(self, msg: str) -> None:
        if msg:
            self.toast(msg, 4000)

    def _to_projects(self) -> None:
        self.requestProjects.emit()

    def closeEvent(self, e) -> None:
        if self.render_queue and self.render_queue.running:
            m = QMessageBox.question(self, "렌더링 진행 중", "렌더링이 진행 중입니다. 취소하고 종료할까요?")
            if m != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self.render_queue.cancel_all()
            self.render_queue.wait(10)
        self._closing = True
        self._layout_commit()
        self.playback.shutdown()
        self.proxy.cancel()
        self.frames.stop()
        self.thumbs.stop()
        self.cut_thumbs.stop()
        self.project.close()
        super().closeEvent(e)
        self.closed.emit()

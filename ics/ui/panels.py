"""우측 인스펙터(STORY/REELS/FEED), 재생 컨트롤 바, 타임코드 입력."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap, QTransform
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from ..core.layout import LayoutParams, compute_geometry
from ..core.model import FEED_MAX_FINAL
from ..core.timecode import frame_to_tc, tc_to_frame
from .widgets import ElidedLabel, control_icon


class TCEdit(QLineEdit):
    """프레임 정확도 타임코드 입력. 'HH:MM:SS:FF', '1234f', 숫자만(Premiere 방식) 허용."""

    frameEntered = Signal(int)

    def __init__(self, project):
        super().__init__()
        self.project = project
        self.setObjectName("timecode")
        self.setFixedWidth(104)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip("타임코드 입력 (예: 00:01:02:12 · 1234f · 10212)")
        self.editingFinished.connect(self._done)
        self._frame = 0

    def set_frame(self, f: int) -> None:
        self._frame = f
        if not self.hasFocus():
            self.setText(frame_to_tc(f, self.project.fps))
        self.setStyleSheet("")

    def _done(self) -> None:
        f = tc_to_frame(self.text(), self.project.fps)
        if f is None or f < 0 or f >= max(1, self.project.count):
            self.setStyleSheet("border-color: #ff5d6c;")
            self.setText(frame_to_tc(self._frame, self.project.fps))
            return
        self.setStyleSheet("")
        self.frameEntered.emit(f)


class TransportBar(QWidget):
    playClicked = Signal()
    reverseClicked = Signal()
    stepClicked = Signal(int)
    gotoClicked = Signal(str)
    loopToggled = Signal(bool)
    viewToggled = Signal(bool)
    frameEntered = Signal(int)
    muteToggled = Signal(bool)  # True = 소리 끔

    def __init__(self, project):
        super().__init__()
        self.setObjectName("transport")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(9)
        lay = QHBoxLayout()
        lay.setSpacing(6)
        self.b_start = self._btn("⏮", "처음으로 (Home)", lambda: self.gotoClicked.emit("start"))
        self.b_prev = self._btn("◀|", "1프레임 뒤로 (←)", lambda: self.stepClicked.emit(-1))
        self.b_rev = self._btn("◀", "역재생 (J) - 소리 없이 거꾸로 재생, 다시 누르면 정지", self.reverseClicked.emit)
        self.b_play = self._btn("▶", "재생/정지 (Space)", self.playClicked.emit)
        self.b_next = self._btn("|▶", "1프레임 앞으로 (→)", lambda: self.stepClicked.emit(1))
        self.b_end = self._btn("⏭", "끝으로 (End)", lambda: self.gotoClicked.emit("end"))
        self.b_play.setObjectName("play")
        self.b_play.setFixedWidth(40)
        for b in (self.b_start, self.b_prev, self.b_rev, self.b_play, self.b_next, self.b_end):
            lay.addWidget(b)
        self.tc = TCEdit(project)
        self.tc.frameEntered.connect(self.frameEntered.emit)
        lay.addWidget(self.tc)
        self.frame_lbl = QLabel("f0")
        self.frame_lbl.setObjectName("muted")
        self.frame_lbl.setMinimumWidth(76)
        lay.addWidget(self.frame_lbl)
        self.rate_lbl = QLabel("")
        self.rate_lbl.setObjectName("muted")
        lay.addWidget(self.rate_lbl)
        self.mute_btn = QPushButton()
        self.mute_btn.setCheckable(True)
        self.mute_btn.toggled.connect(self.muteToggled.emit)
        lay.addWidget(self.mute_btn)
        self.audio_lbl = ElidedLabel()
        self.audio_lbl.setObjectName("muted")
        lay.addWidget(self.audio_lbl, 1)
        self._has_audio = True
        self.set_muted(False)
        v.addLayout(lay)
        lay = QHBoxLayout()
        lay.setSpacing(10)
        self.loop = QCheckBox("구간 반복")
        self.loop.toggled.connect(self.loopToggled.emit)
        lay.addWidget(self.loop)
        self.view_btn = QPushButton("출력 미리보기")
        self.view_btn.setCheckable(True)
        self.view_btn.setChecked(True)
        self.view_btn.setToolTip("소스 / 출력(9:16·4:5 + Fit/Fill) 전환 (Tab)")
        self.view_btn.toggled.connect(self.viewToggled.emit)
        lay.addWidget(self.view_btn)
        self.safe = QCheckBox("UI 안전영역")
        lay.addWidget(self.safe)
        lay.addStretch(1)
        v.addLayout(lay)
        self.project = project

    def _btn(self, text, tip, fn):
        b = QPushButton(text)
        icons = {
            "⏮": QStyle.StandardPixmap.SP_MediaSkipBackward,
            "◀|": QStyle.StandardPixmap.SP_MediaSeekBackward,
            "▶": QStyle.StandardPixmap.SP_MediaPlay,
            "|▶": QStyle.StandardPixmap.SP_MediaSeekForward,
            "⏭": QStyle.StandardPixmap.SP_MediaSkipForward,
            "◀": QStyle.StandardPixmap.SP_MediaPlay,
        }
        ic = control_icon(self, icons[text], "#0a0a0a" if text == "▶" else "#ececec")
        if text == "◀":  # 역재생: 재생 아이콘을 좌우로 뒤집는다
            ic = QIcon(ic.pixmap(16, 16).transformed(QTransform().scale(-1, 1)))
        b.setIcon(ic)
        b.setText("")
        b.setAccessibleName(tip)
        b.setToolTip(tip)
        b.setObjectName("compact")
        b.setFixedWidth(32)
        b.clicked.connect(fn)
        return b

    def set_frame(self, f: int) -> None:
        self.tc.set_frame(f)
        self.frame_lbl.setText(f"f{f} / {max(0, self.project.count - 1)}")

    def set_playing(self, playing: bool) -> None:
        icon = QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
        self.b_play.setIcon(control_icon(self, icon, "#0a0a0a"))
        self.b_play.setAccessibleName("일시 정지" if playing else "재생")

    def set_muted(self, muted: bool) -> None:
        """버튼 표시만 맞춘다 (muteToggled 는 내보내지 않는다)."""
        self.mute_btn.blockSignals(True)
        self.mute_btn.setChecked(muted)
        self.mute_btn.blockSignals(False)
        self._refresh_mute_btn()

    def set_audio_available(self, has_audio: bool) -> None:
        self._has_audio = has_audio
        self._refresh_mute_btn()

    def set_audio_status(self, text: str, tip: str = "") -> None:
        """소리 준비 중 / 사용 불가 사유를 보여 준다 (정상일 때는 빈 문자열)."""
        self.audio_lbl.setText(text)
        self.audio_lbl.setToolTip(tip)

    def _refresh_mute_btn(self) -> None:
        muted = self.mute_btn.isChecked()
        self.mute_btn.setEnabled(self._has_audio)
        if not self._has_audio:
            self.mute_btn.setText("🔇 소리 없음")
            self.mute_btn.setToolTip("이 영상에는 오디오 트랙이 없습니다.")
            return
        self.mute_btn.setText("🔇 소리 꺼짐" if muted else "🔊 소리 켜짐")
        self.mute_btn.setToolTip(
            "소리 켜기/끄기 (Shift+M)\n미리보기 재생 소리만 끕니다. 렌더링 결과물의 소리에는 영향이 없습니다."
        )


def slider(minv, maxv, val) -> QSlider:
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(minv, maxv)
    s.setValue(val)
    return s


class LayoutEditor(QGroupBox):
    """Fit / Fill(크롭) + 확대 + 위치 조정. 확대는 Fit 에서도 된다 (넘치는 부분은 잘림). 원본 비율 출력에서는 Fit/Fill 구분이 없다."""

    changed = Signal(object)  # LayoutParams
    committed = Signal(object)
    cropRequested = Signal(bool)  # True: 다시 감지, False: 기존 원본 감지 결과 재사용 가능

    def __init__(self, title="화면 배치"):
        super().__init__(title)
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.r_fit = QRadioButton("Fit (원본 전체 + 검정 여백)")
        self.r_fill = QRadioButton("Fill / Crop")
        self.r_fit.setChecked(True)
        row.addWidget(self.r_fit)
        row.addWidget(self.r_fill)
        v.addLayout(row)
        self._source_crop = None
        self.auto_crop = QCheckBox("검정 띠 자동 크롭")
        self.auto_crop.setToolTip(
            "원본 전체의 최대 15개 프레임에서 공통 검정 띠를 감지합니다.\n"
            "켜면 Fit/Fill 전에 띠를 제거하고, 끄면 원본 영역으로 돌아갑니다.\n"
            "장면마다 띠나 화면 비율이 달라지면 놓칠 수 있으므로 출력 미리보기를 확인하세요."
        )
        self.detect_crop = QPushButton("다시 감지")
        self.detect_crop.setEnabled(False)
        self.detect_crop.clicked.connect(lambda: self.cropRequested.emit(True))
        crop_row = QHBoxLayout()
        crop_row.addWidget(self.auto_crop, 1)
        crop_row.addWidget(self.detect_crop)
        v.addLayout(crop_row)
        self.crop_info = QLabel("원본의 위·아래 / 좌·우 검정 띠를 감지합니다.")
        self.crop_info.setObjectName("muted")
        self.crop_info.setWordWrap(True)
        v.addWidget(self.crop_info)
        f = QFormLayout()
        self.zoom = slider(100, 400, 100)
        self.px = slider(0, 100, 50)
        self.py = slider(0, 100, 50)
        self.zoom_lbl = QLabel("100%")
        zr = QHBoxLayout()
        zr.addWidget(self.zoom)
        zr.addWidget(self.zoom_lbl)
        f.addRow("확대", zr)
        f.addRow("가로 위치", self.px)
        f.addRow("세로 위치", self.py)
        v.addLayout(f)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        v.addWidget(self.info)
        btn = QPushButton("위치 초기화")
        btn.clicked.connect(self.reset)
        v.addWidget(btn)
        self._block = False
        self._crop_busy = False
        self.auto_crop.toggled.connect(self._crop_toggled)
        for w in (self.r_fit, self.r_fill):
            w.toggled.connect(self._emit)
        for s in (self.zoom, self.px, self.py):
            s.valueChanged.connect(self._emit)
            s.sliderReleased.connect(lambda: self.committed.emit(self.value()))
        self.r_fit.toggled.connect(self._sync_enabled)
        self._sync_enabled()

    def _sync_enabled(self):
        self.zoom.setEnabled(True)
        self.detect_crop.setEnabled(self.auto_crop.isChecked() and not self._crop_busy)

    def _crop_toggled(self, on: bool) -> None:
        self._sync_enabled()
        if self._block:
            return
        self.changed.emit(self.value())
        self.committed.emit(self.value())
        if on and self._source_crop is None:
            self.cropRequested.emit(False)

    def set_crop_busy(self, busy: bool) -> None:
        self._crop_busy = busy
        self.detect_crop.setText("감지 중…" if busy else "다시 감지")
        self._sync_enabled()

    def set_original_ratio(self, on: bool) -> None:
        """원본 비율 출력이면 여백/크롭 선택이 의미 없으므로 Fit/Fill 선택을 숨긴다."""
        self.r_fit.setVisible(not on)
        self.r_fill.setVisible(not on)

    def value(self) -> LayoutParams:
        return LayoutParams(
            "fill" if self.r_fill.isChecked() else "fit",
            self.zoom.value() / 100.0,
            self.px.value() / 100.0,
            self.py.value() / 100.0,
            auto_crop=self.auto_crop.isChecked(),
            source_crop=self._source_crop,
        )

    def set_value(self, lp: LayoutParams) -> None:
        self._block = True
        self._source_crop = lp.source_crop
        self.auto_crop.setChecked(lp.auto_crop)
        self.crop_info.setText(
            (
                f"감지 영역 {lp.source_crop[2]}×{lp.source_crop[3]} · 크롭 켜짐"
                if lp.source_crop
                else "검정 띠 감지 대기 중…"
            )
            if lp.auto_crop
            else "자동 크롭 꺼짐 · 원본 영역 유지"
        )
        (self.r_fill if lp.mode == "fill" else self.r_fit).setChecked(True)
        self.zoom.setValue(int(lp.zoom * 100))
        self.px.setValue(int(round(lp.pos_x * 100)))
        self.py.setValue(int(round(lp.pos_y * 100)))
        self.zoom_lbl.setText(f"{self.zoom.value()}%")
        self._sync_enabled()
        self._block = False

    def reset(self):
        self.set_value(
            LayoutParams(self.value().mode, auto_crop=self.auto_crop.isChecked(), source_crop=self._source_crop)
        )
        self.changed.emit(self.value())
        self.committed.emit(self.value())

    def _emit(self, *_):
        self.zoom_lbl.setText(f"{self.zoom.value()}%")
        if not self._block:
            self.changed.emit(self.value())

    def set_info(self, text: str) -> None:
        self.info.setText(text)


class SelectionPanel(QWidget):
    """STORY / REELS 인스펙터."""

    presetClicked = Signal(str)
    inOutEntered = Signal(int, int)
    setIn = Signal()
    setOut = Signal()
    playRange = Signal()
    layoutLive = Signal(object)
    layoutCommit = Signal(object)
    clearClicked = Signal()
    coverSet = Signal()
    coverGoto = Signal()
    coverChanged = Signal(str, float)
    coverPreview = Signal(bool)
    coverRatioChanged = Signal(str)  # 'orig' | '916'
    coverLayoutLive = Signal(object)
    coverLayoutCommit = Signal(object)
    overlayChanged = Signal(object)  # StoryOverlay (STORY 전용)

    def __init__(self, which: str, project, settings=None):
        super().__init__()
        self.which, self.project = which, project
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 14)
        spec = "최대 60초 · 9:16 · 1080×1920 · MP4" if which == "story" else "최대 180초 · 9:16 · 1080×1920 · MP4"
        t = QLabel(f"{which.upper()} 설정")
        t.setObjectName("h1")
        v.addWidget(t)
        s = QLabel(spec)
        s.setObjectName("muted")
        v.addWidget(s)
        g = QGroupBox("구간")
        gl = QVBoxLayout(g)
        pr = QHBoxLayout()
        presets = ["15", "30", "60"] + (["180"] if which == "reels" else [])
        self.preset_btns: dict[str, QPushButton] = {}
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for p in presets + ["max"]:
            b = QPushButton(f"{p}초" if p != "max" else "최대")
            b.setCheckable(True)
            b.setToolTip("현재 재생 위치를 시작 지점으로 해당 길이만큼 선택")
            b.clicked.connect(lambda _=False, pp=p: self.presetClicked.emit(pp))
            grp.addButton(b)
            pr.addWidget(b)
            self.preset_btns[p] = b
        gl.addLayout(pr)
        self.start_note = QLabel("재생헤드를 시작 지점에 놓고 길이 버튼을 누르세요.")
        self.start_note.setObjectName("muted")
        self.start_note.setWordWrap(True)
        gl.addWidget(self.start_note)
        io = QFormLayout()
        self.in_edit, self.out_edit = TCEdit(project), TCEdit(project)
        self.in_edit.frameEntered.connect(lambda f: self.inOutEntered.emit(f, self._cur_out()))
        self.out_edit.frameEntered.connect(lambda f: self.inOutEntered.emit(self._cur_in(), f + 1))
        ri = QHBoxLayout()
        ri.addWidget(self.in_edit)
        bi = QPushButton("현재=In (I)")
        bi.clicked.connect(self.setIn.emit)
        ri.addWidget(bi)
        ro = QHBoxLayout()
        ro.addWidget(self.out_edit)
        bo = QPushButton("현재=Out (O)")
        bo.clicked.connect(self.setOut.emit)
        ro.addWidget(bo)
        io.addRow("In", ri)
        io.addRow("Out(포함)", ro)
        gl.addLayout(io)
        self.len_lbl = QLabel("")
        gl.addWidget(self.len_lbl)
        row = QHBoxLayout()
        pb = QPushButton("구간 재생")
        pb.clicked.connect(self.playRange.emit)
        cb = QPushButton("선택 해제")
        cb.clicked.connect(self.clearClicked.emit)
        row.addWidget(pb)
        row.addWidget(cb)
        gl.addLayout(row)
        v.addWidget(g)
        self.layout_editor = LayoutEditor()
        self.layout_editor.changed.connect(self.layoutLive.emit)
        self.layout_editor.committed.connect(self.layoutCommit.emit)
        v.addWidget(self.layout_editor)
        self.overlay_editor = None
        if which == "story" and settings is not None:
            from .overlay_editor import OverlayEditor

            self.overlay_editor = OverlayEditor(settings)
            self.overlay_editor.changed.connect(self.overlayChanged.emit)
            v.addWidget(self.overlay_editor)
        if which == "reels":
            self._build_cover(v)
        v.addStretch(1)
        self._in = self._out = 0

    def _cur_in(self):
        return self._in

    def _cur_out(self):
        return self._out

    def _build_cover(self, v: QVBoxLayout) -> None:
        g = QGroupBox("릴스 커버 (JPG)")
        gl = QVBoxLayout(g)
        r = QHBoxLayout()
        b = QPushButton("현재 프레임을 커버로")
        b.clicked.connect(self.coverSet.emit)
        r.addWidget(b)
        b2 = QPushButton("커버 위치로 이동")
        b2.clicked.connect(self.coverGoto.emit)
        r.addWidget(b2)
        gl.addLayout(r)
        self.cover_lbl = QLabel("커버 프레임: 미지정 (릴스 구간과 무관하게 아무 프레임)")
        self.cover_lbl.setObjectName("muted")
        self.cover_lbl.setWordWrap(True)
        gl.addWidget(self.cover_lbl)
        self.cover_ratio_combo = QComboBox()
        self.cover_ratio_combo.addItem("원본 비율 (기본)", "orig")
        self.cover_ratio_combo.addItem("9:16 (1080×1920)", "916")
        self.cover_ratio_combo.currentIndexChanged.connect(
            lambda _i: self.coverRatioChanged.emit(self.cover_ratio_combo.currentData())
        )
        rr = QHBoxLayout()
        rr.addWidget(QLabel("커버 출력 비율"))
        rr.addWidget(self.cover_ratio_combo, 1)
        gl.addLayout(rr)
        self.cover_prev = QCheckBox("커버 미리보기 + 프로필 그리드 크롭")
        self.cover_prev.toggled.connect(self.coverPreview.emit)
        gl.addWidget(self.cover_prev)
        f = QFormLayout()
        self.grid_combo = QComboBox()
        self.grid_combo.addItems(["4:5", "3:4", "1:1"])
        self.grid_combo.currentTextChanged.connect(lambda t: self.coverChanged.emit(t, self.grid_off.value() / 100))
        self.grid_off = slider(0, 100, 50)
        self.grid_off.valueChanged.connect(lambda v_: self.coverChanged.emit(self.grid_combo.currentText(), v_ / 100))
        f.addRow("그리드 비율", self.grid_combo)
        f.addRow("그리드 세로 위치", self.grid_off)
        gl.addLayout(f)
        self.cover_layout = LayoutEditor("커버 화면 배치 / 위치 조정")
        self.cover_layout.changed.connect(self.coverLayoutLive.emit)
        self.cover_layout.committed.connect(self.coverLayoutCommit.emit)
        gl.addWidget(self.cover_layout)
        v.addWidget(g)

    def refresh(self) -> None:
        p = self.project
        sel = p.state.story if self.which == "story" else p.state.reels
        self._in, self._out = sel.start, sel.end
        if sel.enabled:
            self.in_edit.set_frame(sel.start)
            self.out_edit.set_frame(max(0, sel.end - 1))
            dur = p.seconds(sel.start, sel.end)
            lim = 60.0 if self.which == "story" else 180.0
            ok = dur <= lim + 1e-6
            self.len_lbl.setText(f"길이 {dur:.3f}초 ({sel.length}프레임)" + ("" if ok else f"  ⚠ {int(lim)}초 초과"))
            self.len_lbl.setObjectName("" if ok else "err")
            self.len_lbl.setStyleSheet("" if ok else "color:#ff5d6c;")
        else:
            self.len_lbl.setText("미지정 - 재생헤드를 시작 지점에 놓고 길이 버튼을 누르세요.")
            self.len_lbl.setStyleSheet("")
        for k, b in self.preset_btns.items():
            b.blockSignals(True)
            b.setChecked(sel.enabled and sel.preset == k)
            b.blockSignals(False)
        self.layout_editor.set_value(sel.layout)
        if p.media:
            g = compute_geometry(p.media.display_w, p.media.display_h, "916", sel.layout)
            self.layout_editor.set_info(
                f"출력 {g.canvas_w}×{g.canvas_h}" + (f" · {g.tier_note}" if g.tier_note else "")
            )
            if self.overlay_editor is not None:
                from ..render import overlay as ov_mod

                ov = p.state.story_overlay
                warns = ov_mod.layout(ov, g, p.rec.name).warnings if ov_mod.active(ov, p.rec.name) else []
                self.overlay_editor.set_value(ov, warns)
        if self.which == "reels":
            c = p.state.cover
            if c.frame is None:
                self.cover_lbl.setText("커버 프레임: 미지정 (릴스 구간과 무관하게 아무 프레임)")
            else:
                self.cover_lbl.setText(f"커버 프레임: f{c.frame} ({frame_to_tc(c.frame, p.fps)})")
            self.grid_combo.blockSignals(True)
            self.grid_combo.setCurrentText(c.grid_aspect)
            self.grid_combo.blockSignals(False)
            self.grid_off.blockSignals(True)
            self.grid_off.setValue(int(c.grid_offset * 100))
            self.grid_off.blockSignals(False)
            self.cover_layout.set_value(c.layout)
            self.cover_ratio_combo.blockSignals(True)
            self.cover_ratio_combo.setCurrentIndex(0 if p.cover_kind() == "orig" else 1)
            self.cover_ratio_combo.blockSignals(False)
            self.cover_layout.set_original_ratio(p.cover_kind() == "orig")


class FeedPanel(QWidget):
    addStill = Signal()
    addVideo = Signal()
    itemSelected = Signal(str)
    includeToggled = Signal(str, bool)
    deleteClicked = Signal(str)
    orderChanged = Signal(list)  # 드래그로 바꾼 항목 id 순서
    layoutLive = Signal(object)
    layoutCommit = Signal(object)
    setIn = Signal()
    setOut = Signal()
    inOutEntered = Signal(int, int)
    playItem = Signal(str)
    ratioChanged = Signal(str)  # 'orig' | '45'

    def __init__(self, project, thumbs):
        super().__init__()
        self.project, self.thumbs = project, thumbs
        self._block = False
        self._want: str | None = None
        self._thumb_frames: set[int] = set()
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(250)
        self._thumb_timer.timeout.connect(self.refresh)
        thumbs.thumbReady.connect(lambda n: self._thumb_timer.start() if n in self._thumb_frames else None)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        t = QLabel("<b>FEED</b> (캐러셀)")
        t.setObjectName("h1")
        v.addWidget(t)
        s = QLabel("스틸 JPG / 영상 MP4(최대 60초) · 최종 20개 이하")
        s.setObjectName("muted")
        s.setWordWrap(True)
        v.addWidget(s)
        rr = QHBoxLayout()
        rr.addWidget(QLabel("출력 비율"))
        self.ratio_combo = QComboBox()
        self.ratio_combo.addItem("원본 비율 (기본)", "orig")
        self.ratio_combo.addItem("4:5 (1080×1350)", "45")
        self.ratio_combo.setToolTip(
            "원본 비율: 여백/크롭 없이 원본과 같은 비율로 출력 / 4:5: 여백(Fit) 또는 크롭(Fill)"
        )
        self.ratio_combo.currentIndexChanged.connect(
            lambda _i: None if self._block else self.ratioChanged.emit(self.ratio_combo.currentData())
        )
        rr.addWidget(self.ratio_combo, 1)
        v.addLayout(rr)
        g = QGroupBox("추가")
        gl = QVBoxLayout(g)
        row = QHBoxLayout()
        b1 = QPushButton("스틸 추가 (S)\n현재 프레임")
        b1.clicked.connect(self.addStill.emit)
        b2 = QPushButton("영상 추가 (V)\nIn~Out 구간")
        b2.clicked.connect(self.addVideo.emit)
        row.addWidget(b1)
        row.addWidget(b2)
        gl.addLayout(row)
        io = QFormLayout()
        self.in_edit, self.out_edit = TCEdit(project), TCEdit(project)
        self.in_edit.frameEntered.connect(lambda f: self.inOutEntered.emit(f, self._out))
        self.out_edit.frameEntered.connect(lambda f: self.inOutEntered.emit(self._in, f + 1))
        ri, ro = QHBoxLayout(), QHBoxLayout()
        ri.addWidget(self.in_edit)
        bi = QPushButton("현재=In (I)")
        bi.clicked.connect(self.setIn.emit)
        ri.addWidget(bi)
        ro.addWidget(self.out_edit)
        bo = QPushButton("현재=Out (O)")
        bo.clicked.connect(self.setOut.emit)
        ro.addWidget(bo)
        io.addRow("In", ri)
        io.addRow("Out(포함)", ro)
        gl.addLayout(io)
        self.dur_lbl = QLabel("")
        self.dur_lbl.setObjectName("muted")
        gl.addWidget(self.dur_lbl)
        v.addWidget(g)
        self.count_lbl = QLabel("")
        v.addWidget(self.count_lbl)
        hint = QLabel("항목을 끌어서 순서를 바꿀 수 있습니다. 체크를 해제하면 최종 출력에서 제외됩니다.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setDragEnabled(True)
        self.list.model().rowsMoved.connect(self._rows_moved)
        self.list.setIconSize(QSize(48, 60))
        self.list.setMinimumHeight(180)
        self.list.currentItemChanged.connect(self._cur_changed)
        self.list.itemChanged.connect(self._item_changed)
        v.addWidget(self.list, 1)
        rb = QHBoxLayout()
        pb = QPushButton("선택 항목 재생/이동")
        pb.clicked.connect(lambda: self.current_id() and self.playItem.emit(self.current_id()))
        db = QPushButton("삭제")
        db.setObjectName("danger")
        db.clicked.connect(lambda: self.current_id() and self.deleteClicked.emit(self.current_id()))
        rb.addWidget(pb)
        rb.addWidget(db)
        v.addLayout(rb)
        self.warn_lbl = QLabel("")
        self.warn_lbl.setObjectName("warn")
        self.warn_lbl.setWordWrap(True)
        v.addWidget(self.warn_lbl)
        self.layout_editor = LayoutEditor("선택 항목 화면 배치")
        self.layout_editor.changed.connect(self.layoutLive.emit)
        self.layout_editor.committed.connect(self.layoutCommit.emit)
        v.addWidget(self.layout_editor)
        self._block = False
        self._in = self._out = 0

    def _rows_moved(self, *_a) -> None:
        if self._block:
            return
        ids = [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        QTimer.singleShot(0, lambda: self.orderChanged.emit(ids))  # 드롭 처리가 끝난 뒤 목록을 다시 그린다

    def current_id(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def select(self, item_id: str) -> None:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == item_id:
                self.list.setCurrentRow(i)
                return
        self._want = item_id  # 목록이 아직 갱신 전이면 다음 refresh 에서 선택한다

    def _cur_changed(self, cur, _prev):
        if self._block or cur is None:
            return
        self.itemSelected.emit(cur.data(Qt.ItemDataRole.UserRole))

    def _item_changed(self, it):
        if self._block:
            return
        self.includeToggled.emit(it.data(Qt.ItemDataRole.UserRole), it.checkState() == Qt.CheckState.Checked)

    def set_pending(self, a: int, b: int) -> None:
        self._in, self._out = a, b
        self.in_edit.set_frame(a)
        self.out_edit.set_frame(max(0, b - 1))
        d = self.project.seconds(a, b) if b > a else 0
        self.dur_lbl.setText(f"영상 구간 길이 {d:.2f}초" + ("  ⚠ 60초 초과 시 자동 조정" if d > 60 else ""))

    def refresh(self) -> None:
        p = self.project
        cur = self._want or self.current_id()
        self._want = None
        self._block = True
        self.list.clear()
        items = p.feed_ordered()
        self._thumb_frames = {it.start for it in items}
        self.thumbs.ensure(sorted(self._thumb_frames))  # 타임라인 전체 썸네일 줄이 없으므로 필요한 프레임을 직접 요청
        n = 0
        for it in items:
            if it.included:
                n += 1
            tag = f"#{n}" if it.included else "제외"
            tc = frame_to_tc(it.start, p.fps)
            if it.kind == "still":
                text = f"{tag}  스틸  {tc}"
            else:
                text = f"{tag}  영상  {tc} · {p.seconds(it.start, it.end):.1f}s"
            li = QListWidgetItem(text)
            li.setData(Qt.ItemDataRole.UserRole, it.id)
            li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            li.setCheckState(Qt.CheckState.Checked if it.included else Qt.CheckState.Unchecked)
            img = self.thumbs.get(it.start)
            if img is not None:
                li.setIcon(
                    QIcon(
                        QPixmap.fromImage(img).scaled(
                            48, 60, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                        )
                    )
                )
            self.list.addItem(li)
            if it.id == cur:
                self.list.setCurrentItem(li)
        self._block = False
        total = len([i for i in items if i.included])
        over = total > FEED_MAX_FINAL
        self.count_lbl.setText(
            f"포함 {total} / 최종 {FEED_MAX_FINAL}  (등록 전체 {len(items)}개)"
            + ("  ⚠ 20개 초과 - 체크 해제 또는 삭제로 줄여주세요" if over else "")
        )
        self.count_lbl.setStyleSheet("color:#ff5d6c;font-weight:700;" if over else "")
        w = p.feed_warnings()
        self.warn_lbl.setText("\n".join(w[:6]) + (f"\n… 외 {len(w) - 6}건" if len(w) > 6 else ""))
        it = p.get_feed(self.current_id()) if self.current_id() else None
        self.layout_editor.setEnabled(it is not None)
        self.layout_editor.set_original_ratio(p.feed_kind() == "orig")
        self.ratio_combo.blockSignals(True)
        self.ratio_combo.setCurrentIndex(0 if p.feed_kind() == "orig" else 1)
        self.ratio_combo.blockSignals(False)
        if it is not None:
            self.layout_editor.set_value(it.layout)
            if p.media:
                g = compute_geometry(p.media.display_w, p.media.display_h, p.feed_kind(), it.layout)
                self.layout_editor.set_info(
                    f"출력 {g.canvas_w}×{g.canvas_h}" + (f" · {g.tier_note}" if g.tier_note else "")
                )

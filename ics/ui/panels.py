"""우측 인스펙터(STORY/REELS/FEED), 재생 컨트롤 바, 타임코드 입력."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.layout import LayoutParams, compute_geometry
from ..core.model import FEED_MAX_FINAL
from ..core.timecode import frame_to_tc, tc_to_frame
from . import icons, theme
from .widgets import ElidedLabel, FixedButton, Section


class TCEdit(QLineEdit):
    """프레임 정확도 타임코드 입력. 'HH:MM:SS:FF', '1234f', 숫자만(Premiere 방식) 허용."""

    frameEntered = Signal(int)

    def __init__(self, project, big: bool = True):
        super().__init__()
        self.project = project
        self.setObjectName("timecode" if big else "tcSmall")
        self.setFixedWidth(124 if big else 108)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
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
    """재생 줄: 왼쪽 타임코드(크게) + 프레임 번호(작게) | 가운데 재생 단추 | 오른쪽 보기 토글 · 소리."""

    playClicked = Signal()
    reverseClicked = Signal()
    stepClicked = Signal(int)
    gotoClicked = Signal(str)
    loopToggled = Signal(bool)
    viewToggled = Signal(bool)
    frameEntered = Signal(int)
    muteToggled = Signal(bool)  # True = 소리 끔
    volumeChanged = Signal(int)  # 0~100 (미리보기 음량)

    def __init__(self, project):
        super().__init__()
        self.project = project
        self.setObjectName("transport")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(0)
        # ---- 왼쪽: 타임코드
        left = QHBoxLayout()
        left.setSpacing(6)
        self.tc = TCEdit(project)
        self.tc.frameEntered.connect(self.frameEntered.emit)
        left.addWidget(self.tc)
        self.frame_lbl = QLabel("f0")
        self.frame_lbl.setObjectName("hint")
        self.frame_lbl.setToolTip("현재 프레임 번호 / 마지막 프레임")
        left.addWidget(self.frame_lbl)
        self.rate_lbl = QLabel("")
        self.rate_lbl.setObjectName("badge")
        self.rate_lbl.setVisible(False)
        left.addWidget(self.rate_lbl)
        self.audio_lbl = ElidedLabel()
        self.audio_lbl.setObjectName("hint")
        left.addWidget(self.audio_lbl, 1)
        lw = QWidget()
        lw.setLayout(left)
        lay.addWidget(lw, 1)
        # ---- 가운데: 재생 단추
        mid = QHBoxLayout()
        mid.setSpacing(2)
        self.b_start = self._btn("skip-back", "처음으로 (Home)", lambda: self.gotoClicked.emit("start"))
        self.b_prev = self._btn("step-back", "1프레임 뒤로 (←)", lambda: self.stepClicked.emit(-1))
        self.b_rev = self._btn(
            "play", "역재생 (J) - 소리 없이 거꾸로 재생, 다시 누르면 정지", self.reverseClicked.emit, mirror=True
        )
        self.b_play = FixedButton(34)
        self.b_play.setObjectName("play")
        self.b_play.setIconSize(QSize(16, 16))
        self.b_play.setToolTip("재생/정지 (Space)")
        self.b_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_play.clicked.connect(self.playClicked.emit)
        self.b_next = self._btn("step-forward", "1프레임 앞으로 (→)", lambda: self.stepClicked.emit(1))
        self.b_end = self._btn("skip-forward", "끝으로 (End)", lambda: self.gotoClicked.emit("end"))
        for w in (self.b_start, self.b_prev, self.b_rev):
            mid.addWidget(w)
        mid.addSpacing(6)
        mid.addWidget(self.b_play)
        mid.addSpacing(6)
        for w in (self.b_next, self.b_end):
            mid.addWidget(w)
        lay.addLayout(mid)
        # ---- 오른쪽: 보기 토글 + 소리
        right = QHBoxLayout()
        right.setSpacing(2)
        right.addStretch(1)
        self.loop = self._toggle("repeat", "구간 반복 재생\n재생이 선택 구간 끝에 닿으면 처음으로 돌아갑니다")
        self.loop.toggled.connect(self.loopToggled.emit)
        right.addWidget(self.loop)
        self.safe = self._toggle(
            "scan", "안전 영역 표시\n인스타그램 화면(프로필 줄, 메시지 바)이 가리는 곳을 보여 줍니다"
        )
        right.addWidget(self.safe)
        self.view_btn = self._toggle("crop", "출력 화면으로 보기 (Tab)\n끄면 원본 전체를 봅니다")
        self.view_btn.setChecked(True)
        self.view_btn.toggled.connect(self.viewToggled.emit)
        right.addWidget(self.view_btn)
        sep = QFrame()
        sep.setFixedSize(1, 16)
        sep.setStyleSheet(f"background:{theme.BORDER};")
        right.addSpacing(6)
        right.addWidget(sep)
        right.addSpacing(6)
        self.mute_btn = FixedButton(30)
        self.mute_btn.setObjectName("icon")
        self.mute_btn.setCheckable(True)
        self.mute_btn.setIconSize(QSize(16, 16))
        self.mute_btn.toggled.connect(self.muteToggled.emit)
        right.addWidget(self.mute_btn)
        self.volume = ResetSlider(0, 100, 100, default=100)
        self.volume.setObjectName("volume")
        self.volume.setFixedWidth(78)
        self.volume.setToolTip("미리보기 음량 (더블클릭: 100%) · 렌더링 결과와 무관")
        self.volume.valueChanged.connect(self.volumeChanged.emit)
        right.addWidget(self.volume)
        rw = QWidget()
        rw.setLayout(right)
        lay.addWidget(rw, 1)
        self._has_audio = True
        self._playing = False
        self.set_playing(False)
        self.set_muted(False)

    def _btn(self, name, tip, fn, mirror: bool = False):
        b = FixedButton(30)
        b.setObjectName("icon")
        b.setIcon(icons.icon(name, theme.TEXT, 16, mirror=mirror))
        b.setIconSize(QSize(16, 16))
        b.setAccessibleName(tip)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    def _toggle(self, name, tip):
        """켜면 흰색, 끄면 흐린 회색 아이콘 (글자 체크박스 대신)."""
        b = FixedButton(30)
        b.setObjectName("icon")
        b.setCheckable(True)
        b.setIcon(icons.icon(name, theme.DIM, 16, on="#ffffff", active=theme.MUTED))
        b.setIconSize(QSize(16, 16))
        b.setToolTip(tip)
        b.setAccessibleName(tip.split("\n")[0])
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def set_frame(self, f: int) -> None:
        self.tc.set_frame(f)
        self.frame_lbl.setText(f"f{f} / {max(0, self.project.count - 1)}")

    def set_rate(self, text: str) -> None:
        self.rate_lbl.setText(text)
        self.rate_lbl.setVisible(bool(text))

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self.b_play.setIcon(icons.icon("pause" if playing else "play", "#0a0a0a", 16))
        self.b_play.setAccessibleName("일시 정지" if playing else "재생")

    def set_muted(self, muted: bool) -> None:
        """버튼 표시만 맞춘다 (muteToggled 는 내보내지 않는다)."""
        self.mute_btn.blockSignals(True)
        self.mute_btn.setChecked(muted)
        self.mute_btn.blockSignals(False)
        self._refresh_mute_btn()

    def set_volume(self, value: int) -> None:
        """표시만 맞춘다 (volumeChanged 는 내보내지 않는다)."""
        self.volume.blockSignals(True)
        self.volume.setValue(int(value))
        self.volume.blockSignals(False)

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
        self.volume.setEnabled(self._has_audio and not muted)
        # 재생 단추와 같은 선 아이콘 (켜짐: 흰색 스피커, 꺼짐: 흐린 X 스피커)
        off = muted or not self._has_audio
        self.mute_btn.setIcon(icons.icon("volume-x" if off else "volume-2", theme.MUTED if off else theme.TEXT, 16))
        self.mute_btn.setAccessibleName("소리 꺼짐" if off else "소리 켜짐")
        if not self._has_audio:
            self.mute_btn.setToolTip("이 영상에는 오디오 트랙이 없습니다.")
            return
        self.mute_btn.setToolTip(
            "소리 켜기/끄기 (Shift+M)\n미리보기 재생 소리만 끕니다. 렌더링 결과물의 소리에는 영향이 없습니다."
        )


class ResetSlider(QSlider):
    """마우스 휠로는 값이 바뀌지 않고(인스펙터 스크롤 중 오조작 방지), 더블클릭하면 기본값으로 돌아간다."""

    def __init__(self, minv=0, maxv=100, val=0, default=None):
        super().__init__(Qt.Orientation.Horizontal)
        self.setRange(minv, maxv)
        self.setValue(val)
        self.default = val if default is None else default
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("더블클릭: 기본값으로 초기화")

    def wheelEvent(self, e):  # noqa: N802
        e.ignore()  # 부모(스크롤 영역)가 처리하도록 넘긴다

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        self.setValue(self.default)
        self.sliderReleased.emit()
        e.accept()


def slider(minv, maxv, val, default=None) -> QSlider:
    return ResetSlider(minv, maxv, val, default)


class LayoutEditor(QGroupBox):
    """Fit / Fill(크롭) + 세로 위치. 원본 비율 출력에서는 Fit/Fill 구분이 없다."""

    changed = Signal(object)  # LayoutParams
    committed = Signal(object)
    cropRequested = Signal(bool)  # True: 다시 감지, False: 기존 원본 감지 결과 재사용 가능
    reframeRequested = Signal()  # 인물 기준 자동 맞춤: 구간 안 모든 컷 (컷마다 따로 위치)
    reframeSomeRequested = Signal()  # 선택한 컷(없으면 지금 컷)만 자동 맞춤
    reframeReset = Signal()
    reframeCancel = Signal()
    panToggled = Signal(bool)  # 컷 안에서 인물 따라가기 (전체/개별 맞추기 모두에 적용)

    def __init__(self, title="화면 배치", reframe: bool = False):
        super().__init__("")
        self.setObjectName("flat")
        self.title_text = title
        self._has_reframe = reframe
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        # Fit / Fill 은 두 칸짜리 세그먼트 버튼
        self.mode_bar = QFrame()
        self.mode_bar.setObjectName("segbarSmall")
        row = QHBoxLayout(self.mode_bar)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)
        self.r_fit = QPushButton("Fit")
        self.r_fill = QPushButton("Fill")
        self.r_fit.setToolTip("원본 전체가 보이게 맞추고 남는 곳은 여백")
        self.r_fill.setToolTip("화면을 꽉 채우고 넘치는 부분은 잘라냄")
        self._mode_grp = QButtonGroup(self)
        self._mode_grp.setExclusive(True)
        for b in (self.r_fit, self.r_fill):
            b.setObjectName("segSmall")
            b.setCheckable(True)
            self._mode_grp.addButton(b)
            row.addWidget(b)
        self.r_fit.setChecked(True)
        v.addWidget(self.mode_bar)
        self._source_crop = None
        self.auto_crop = QCheckBox("검정 띠 자동 크롭")
        self.auto_crop.setToolTip(
            "원본 전체의 최대 15개 프레임에서 공통 검정 띠를 감지합니다.\n"
            "켜면 Fit/Fill 전에 띠를 제거하고, 끄면 원본 영역으로 돌아갑니다.\n"
            "장면마다 띠나 화면 비율이 달라지면 놓칠 수 있으므로 출력 미리보기를 확인하세요."
        )
        self.detect_crop = QPushButton("다시 감지")
        self.detect_crop.setObjectName("link")
        self.detect_crop.setEnabled(False)
        self.detect_crop.clicked.connect(lambda: self.cropRequested.emit(True))
        crop_row = QHBoxLayout()
        crop_row.addWidget(self.auto_crop, 1)
        crop_row.addWidget(self.detect_crop)
        v.addLayout(crop_row)
        self.crop_info = QLabel("")
        self.crop_info.setObjectName("hint")
        v.addWidget(self.crop_info)
        pr = QHBoxLayout()
        pr.setSpacing(8)
        self.py_lbl = QLabel("세로 위치")
        self.py_lbl.setObjectName("fieldLabel")
        pr.addWidget(self.py_lbl)
        self.py = slider(0, 100, 50)
        pr.addWidget(self.py, 1)
        v.addLayout(pr)
        self.info = QLabel("")
        self.info.setObjectName("hint")
        self.info.setWordWrap(True)
        v.addWidget(self.info)
        # ---- Fill 9:16: 컷별 가로 위치 (인물 자동 맞춤 + 미리보기 좌우 드래그)
        self.reframe_box = QWidget()
        rv = QVBoxLayout(self.reframe_box)
        rv.setContentsMargins(0, 4, 0, 0)
        rv.setSpacing(4)
        rr = QHBoxLayout()
        rr.setSpacing(6)
        self.reframe_btn = QPushButton("전체 컷 자동 맞춤")
        self.reframe_btn.setIcon(icons.icon("scan-face", theme.TEXT, 15))
        self.reframe_btn.setToolTip(
            "영상 전체의 모든 컷을 한 번에 분석해(구간 밖 포함), 컷마다 인물이 가장 잘 보이는 가로 위치로 맞춥니다.\n"
            "인물이 컷 안에서 움직이면 움직인 경로의 평균 위치를 가운데로 둡니다. 여러 명이면 여러 명이 모인 쪽.\n"
            "'컷 안에서 인물 따라가기' 를 켜면, 인물이 크게 움직이는 컷은 카메라처럼 부드럽게 따라갑니다.\n"
            "세로 위치와 확대는 그대로입니다. 인물이 없는 컷은 바꾸지 않습니다."
        )
        self.reframe_btn.clicked.connect(self.reframeRequested.emit)
        rr.addWidget(self.reframe_btn, 1)
        self.reframe_some_btn = QPushButton("지금 컷만")
        self.reframe_some_btn.setToolTip("재생 헤드가 있는 컷만 (컷을 선택했으면 선택한 컷만) 자동으로 맞춥니다")
        self.reframe_some_btn.clicked.connect(self.reframeSomeRequested.emit)
        rr.addWidget(self.reframe_some_btn)
        self.reframe_reset = QPushButton("초기화")
        self.reframe_reset.setObjectName("link")
        self.reframe_reset.setToolTip("모든 컷의 가로 위치를 가운데로 되돌립니다")
        self.reframe_reset.clicked.connect(self.reframeReset.emit)
        rr.addWidget(self.reframe_reset)
        self.reframe_cancel = QPushButton("취소")
        self.reframe_cancel.setObjectName("link")
        self.reframe_cancel.setToolTip("인물 분석을 멈춥니다 (그때까지 찾은 결과는 다음에 다시 씁니다)")
        self.reframe_cancel.clicked.connect(self.reframeCancel.emit)
        self.reframe_cancel.setVisible(False)
        rr.addWidget(self.reframe_cancel)
        rv.addLayout(rr)
        self.pan_check = QCheckBox("컷 안에서 인물 따라가기 (부드러운 패닝)")
        self.pan_check.setToolTip(
            "켜면 전체 컷 / 지금 컷 자동 맞춤에서, 인물이 컷 안에서 크게 움직이는 컷만 카메라처럼 부드럽게 따라갑니다\n"
            "(천천히 출발하고 천천히 멈춤, 작은 흔들림은 따라가지 않음). 끄면 컷마다 고정 위치 하나.\n"
            "이미 맞춘 컷에는 다시 맞출 때 적용됩니다. 미리보기를 좌우로 끌어 옮긴 컷은 고정 위치가 됩니다."
        )
        self.pan_check.toggled.connect(self.panToggled.emit)
        rv.addWidget(self.pan_check)
        self.reframe_info = QLabel("")
        self.reframe_info.setObjectName("hint")
        self.reframe_info.setWordWrap(True)
        self.reframe_info.setVisible(False)
        rv.addWidget(self.reframe_info)
        v.addWidget(self.reframe_box)
        self.reframe_box.setVisible(False)
        self._block = False
        self._crop_busy = False
        self.auto_crop.toggled.connect(self._crop_toggled)
        for w in (self.r_fit, self.r_fill):
            w.toggled.connect(self._emit)
        self.py.valueChanged.connect(self._emit)
        self.py.sliderReleased.connect(lambda: self.committed.emit(self.value()))
        self.r_fit.toggled.connect(self._sync_enabled)
        self.r_fill.toggled.connect(lambda on: self.reframe_box.setVisible(self._has_reframe and on))
        self._sync_enabled()

    def _sync_enabled(self):
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
        self.mode_bar.setVisible(not on)

    def value(self) -> LayoutParams:
        return LayoutParams(
            "fill" if self.r_fill.isChecked() else "fit",
            1.0,
            0.5,
            self.py.value() / 100.0,
            auto_crop=self.auto_crop.isChecked(),
            source_crop=self._source_crop,
        )

    def set_value(self, lp: LayoutParams) -> None:
        self._block = True
        self._source_crop = lp.source_crop
        self.auto_crop.setChecked(lp.auto_crop)
        txt = (
            (f"감지 영역 {lp.source_crop[2]}×{lp.source_crop[3]}" if lp.source_crop else "검정 띠 감지 중…")
            if lp.auto_crop
            else ""
        )
        self.crop_info.setText(txt)
        self.crop_info.setVisible(bool(txt))
        (self.r_fill if lp.mode == "fill" else self.r_fit).setChecked(True)
        self.reframe_box.setVisible(self._has_reframe and lp.mode == "fill")
        self.py.setValue(int(round(lp.pos_y * 100)))
        self._sync_enabled()
        self._block = False

    def reset(self):
        self.set_value(
            LayoutParams(self.value().mode, auto_crop=self.auto_crop.isChecked(), source_crop=self._source_crop)
        )
        self.changed.emit(self.value())
        self.committed.emit(self.value())

    def _emit(self, *_):
        if not self._block:
            self.changed.emit(self.value())

    def set_reframe_status(self, text: str, busy: bool = False) -> None:
        self.reframe_info.setText(text)
        self.reframe_info.setVisible(bool(text))
        self.reframe_btn.setEnabled(not busy)
        self.reframe_some_btn.setEnabled(not busy)
        self.reframe_btn.setText("분석 중…" if busy else "전체 컷 자동 맞춤")
        self.reframe_cancel.setVisible(busy)
        self.reframe_reset.setVisible(not busy)
        self._busy = busy

    def set_reframe_target(self, n_selected: int) -> None:
        self.reframe_some_btn.setText(
            f"선택 컷 {n_selected}" if n_selected > 1 else ("선택 컷" if n_selected else "지금 컷만")
        )

    def set_info(self, text: str, tip: str = "") -> None:
        self.info.setText(text)
        self.info.setToolTip(tip)


def panel_header(title: str, spec: str, color: str) -> QWidget:
    """인스펙터 맨 위: 색 점 + 작업 이름 + 규격 (미리보기 머리글 대신 여기서 알려 준다)."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 10)
    h.setSpacing(8)
    dot = QLabel()
    dot.setFixedSize(8, 8)
    dot.setStyleSheet(f"background:{color};border-radius:4px;")
    h.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
    t = QLabel(title)
    t.setStyleSheet(f"font-size:{theme.FS_EM}px;font-weight:700;letter-spacing:1px;")
    h.addWidget(t)
    h.addStretch(1)
    sp = QLabel(spec)
    sp.setObjectName("hint")
    h.addWidget(sp)
    return w


class SelectionPanel(QWidget):
    """STORY / REELS 인스펙터."""

    presetClicked = Signal(str)
    layoutLive = Signal(object)
    layoutCommit = Signal(object)
    coverSet = Signal()
    coverSuggest = Signal()  # 커버 추천 (정면 · 초점 맞은 얼굴)
    coverGoto = Signal()
    coverChanged = Signal(str, float)
    coverPreview = Signal(bool)
    coverRatioChanged = Signal(str)  # 'orig' | '916'
    coverLayoutLive = Signal(object)
    coverLayoutCommit = Signal(object)
    overlayChanged = Signal(object)  # StoryOverlay (STORY 전용)
    fromCutsClicked = Signal()  # 선택한 컷으로 구간 지정

    def __init__(self, which: str, project, settings=None):
        super().__init__()
        self.which, self.project = which, project
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 18)
        v.setSpacing(0)
        spec = "9:16 · 1080×1920 · 최대 60초" if which == "story" else "9:16 · 1080×1920 · 최대 180초"
        v.addWidget(panel_header(which.upper(), spec, theme.TAB_COLORS[which]))
        sec = Section(
            "구간",
            f"inspector_{which}_range",
            settings,
            help_text="재생 헤드를 시작 지점에 두고 길이를 고릅니다.\n"
            "'선택 컷' 은 컷 목록에서 고른 컷 범위를 그대로 구간으로 씁니다 (Shift+클릭으로 여러 개).",
        )
        gl = sec.lay
        pr = QHBoxLayout()
        pr.setSpacing(4)
        presets = ["15", "30", "60"] + (["180"] if which == "reels" else [])
        self.preset_btns: dict[str, QPushButton] = {}
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for p in presets + ["max"]:
            b = QPushButton(f"{p}초" if p != "max" else "최대")
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setToolTip("재생 헤드 위치부터 이 길이만큼 선택")
            b.clicked.connect(lambda _=False, pp=p: self.presetClicked.emit(pp))
            grp.addButton(b)
            pr.addWidget(b)
            self.preset_btns[p] = b
        self.cuts_btn = QPushButton("선택 컷")
        self.cuts_btn.setObjectName("chip")
        self.cuts_btn.setToolTip("타임라인에서 고른 컷 범위를 그대로 구간으로 (컷을 Shift+클릭으로 여러 개 선택)")
        self.cuts_btn.setEnabled(False)
        self.cuts_btn.clicked.connect(self.fromCutsClicked.emit)
        pr.addWidget(self.cuts_btn)
        gl.addLayout(pr)
        self.len_lbl = QLabel("")
        self.len_lbl.setObjectName("hint")
        gl.addWidget(self.len_lbl)
        v.addWidget(sec)
        self.layout_editor = LayoutEditor(reframe=True)
        self.layout_editor.changed.connect(self.layoutLive.emit)
        self.layout_editor.committed.connect(self.layoutCommit.emit)
        sec = Section(
            "화면 배치",
            f"inspector_{which}_layout",
            settings,
            help_text="Fit: 원본 전체가 보이고 남는 곳은 여백 / Fill: 화면을 꽉 채우고 넘치는 부분은 잘라냅니다.\n"
            "Fill 에서는 컷마다 가로 위치를 정할 수 있습니다. '전체 컷 자동 맞춤' 은 얼굴이 가장 오래 보이는 곳으로,\n"
            "미리보기를 좌우로 끌면 지금 컷만 옮겨집니다. 세로 위치와 확대는 모든 컷이 같습니다.",
        )
        sec.lay.addWidget(self.layout_editor)
        v.addWidget(sec)
        self.overlay_editor = None
        if which == "story" and settings is not None:
            from .overlay_editor import OverlayEditor

            self.overlay_editor = OverlayEditor(settings)
            self.overlay_editor.changed.connect(self.overlayChanged.emit)
            v.addWidget(self.overlay_editor)
        if which == "reels":
            self._build_cover(v, settings)
        v.addStretch(1)
        self._in = self._out = 0

    def set_cuts_selected(self, n: int) -> None:
        self.layout_editor.set_reframe_target(n)
        self.cuts_btn.setEnabled(n > 0)
        self.cuts_btn.setText(f"선택 컷 {n}" if n > 1 else "선택 컷")

    def _build_cover(self, v: QVBoxLayout, settings=None) -> None:
        sec = Section(
            "릴스 커버",
            "inspector_reels_cover",
            settings,
            help_text="릴스 구간과 상관없이 영상의 아무 프레임이나 커버(JPG)로 쓸 수 있습니다.\n"
            "커버 미리보기를 켜면 프로필 그리드에서 잘려 보이는 영역을 표시합니다.",
        )
        gl = sec.lay
        r = QHBoxLayout()
        r.setSpacing(6)
        b = QPushButton("현재 프레임을 커버로")
        b.clicked.connect(self.coverSet.emit)
        r.addWidget(b, 1)
        b2 = QPushButton("커버로 이동")
        b2.setToolTip("커버로 지정한 프레임으로 재생 헤드를 옮깁니다")
        b2.clicked.connect(self.coverGoto.emit)
        r.addWidget(b2)
        gl.addLayout(r)
        self.cover_suggest_btn = QPushButton("커버 추천")
        self.cover_suggest_btn.setIcon(icons.icon("sparkles", theme.TEXT, 15))
        self.cover_suggest_btn.setToolTip(
            "영상 전체에서 얼굴이 정면에 가깝고 초점이 맞은 프레임을 골라 보여 줍니다.\n"
            "초점이 나갔거나 움직여 번진 얼굴, 너무 어둡거나 작은 얼굴은 빼고, 컷이 겹치지 않게 최대 6장."
        )
        self.cover_suggest_btn.clicked.connect(self.coverSuggest.emit)
        gl.addWidget(self.cover_suggest_btn)
        self.cover_lbl = QLabel("커버 프레임 미지정")
        self.cover_lbl.setObjectName("hint")
        self.cover_lbl.setWordWrap(True)
        gl.addWidget(self.cover_lbl)
        self.cover_ratio_combo = QComboBox()
        self.cover_ratio_combo.addItem("원본 비율 (기본)", "orig")
        self.cover_ratio_combo.addItem("9:16 (1080×1920)", "916")
        self.cover_ratio_combo.currentIndexChanged.connect(
            lambda _i: self.coverRatioChanged.emit(self.cover_ratio_combo.currentData())
        )
        rr = QHBoxLayout()
        _cl = QLabel("출력 비율")
        _cl.setObjectName("fieldLabel")
        rr.addWidget(_cl)
        rr.addWidget(self.cover_ratio_combo, 1)
        gl.addLayout(rr)
        self.grid_combo = QComboBox()
        self.grid_combo.addItems(["4:5", "3:4", "1:1"])
        self.grid_combo.currentTextChanged.connect(
            lambda t: self.coverChanged.emit(t, self.project.state.cover.grid_offset)
        )
        rg = QHBoxLayout()
        _gl = QLabel("그리드")
        _gl.setObjectName("fieldLabel")
        _gl.setToolTip("프로필 그리드 비율")
        rg.addWidget(_gl)
        rg.addWidget(self.grid_combo, 1)
        gl.addLayout(rg)
        self.cover_prev = QCheckBox("커버 미리보기 (그리드 크롭 표시)")
        self.cover_prev.toggled.connect(self.coverPreview.emit)
        gl.addWidget(self.cover_prev)
        sub = QLabel("커버 화면 배치")
        sub.setObjectName("subhead")
        gl.addWidget(sub)
        self.cover_layout = LayoutEditor("커버 화면 배치")
        self.cover_layout.changed.connect(self.coverLayoutLive.emit)
        self.cover_layout.committed.connect(self.coverLayoutCommit.emit)
        gl.addWidget(self.cover_layout)
        v.addWidget(sec)

    def refresh(self) -> None:
        p = self.project
        sel = p.state.story if self.which == "story" else p.state.reels
        self._in, self._out = sel.start, sel.end
        if sel.enabled:
            dur = p.seconds(sel.start, sel.end)
            lim = 60.0 if self.which == "story" else 180.0
            ok = dur <= lim + 1e-6
            self.len_lbl.setText(
                f"{dur:.3f}초 · {sel.length}f · {frame_to_tc(sel.start, p.fps)} 부터"
                + ("" if ok else f"  ⚠ {int(lim)}초 초과")
            )
            self.len_lbl.setStyleSheet("" if ok else "color:#ff5d6c;")
        else:
            self.len_lbl.setText("구간 미지정 · 길이나 '선택 컷' 을 고르세요")
            self.len_lbl.setStyleSheet("")
        for k, b in self.preset_btns.items():
            b.blockSignals(True)
            b.setChecked(sel.enabled and sel.preset == k)
            b.blockSignals(False)
        self.layout_editor.set_value(sel.layout)
        if self.layout_editor.reframe_btn.isEnabled():  # 분석 중이 아니면 현재 상태를 보여 준다
            src = list(sel.cut_x_src.values())
            parts = [
                f"{name} {src.count(k)}"
                for k, name in (("auto", "자동"), ("manual", "수동"), ("none", "인물 없음"))
                if src.count(k)
            ]
            if sel.cut_path:
                parts.append(f"따라가기 {len(sel.cut_path)}")
            self.layout_editor.set_reframe_status(("컷 " + " · ".join(parts)) if parts else "")
        if p.media:
            g = compute_geometry(p.media.display_w, p.media.display_h, "916", sel.layout)
            self.layout_editor.set_info(f"출력 {g.canvas_w}×{g.canvas_h}", g.tier_note)
            if self.overlay_editor is not None:
                from ..render import overlay as ov_mod

                ov = p.state.story_overlay
                title = p.rec.title or p.rec.name
                warns = (
                    ov_mod.layout(ov, g, title, p.rec.artist).warnings if ov_mod.active(ov, title, p.rec.artist) else []
                )
                self.overlay_editor.set_fill_mode(g.mode == "fill")
                self.overlay_editor.set_value(ov, warns)
        if self.which == "reels":
            c = p.state.cover
            if c.frame is None:
                self.cover_lbl.setText("커버 프레임 미지정")
            else:
                self.cover_lbl.setText(f"커버 프레임: f{c.frame} ({frame_to_tc(c.frame, p.fps)})")
            self.grid_combo.blockSignals(True)
            self.grid_combo.setCurrentText(c.grid_aspect)
            self.grid_combo.blockSignals(False)
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

    def __init__(self, project, thumbs, settings=None):
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
        v.setContentsMargins(18, 14, 18, 18)
        v.setSpacing(0)
        v.addWidget(panel_header("FEED", "캐러셀 · 스틸 JPG / 영상 MP4 · 최종 20개", theme.TAB_COLORS["feed"]))
        # ---- 추가
        sec = Section(
            "추가",
            "inspector_feed_add",
            settings,
            help_text="스틸: 재생 헤드의 프레임을 JPG 로 (S)\n영상: In~Out 구간을 MP4 로, 최대 60초 (V)\n"
            "컷을 고르면 그 컷 범위가 In/Out 이 됩니다.",
        )
        gl = sec.lay
        row = QHBoxLayout()
        row.setSpacing(6)
        b1 = QPushButton("스틸 추가")
        b1.setIcon(icons.icon("image-plus", theme.TEXT, 15))
        b1.setToolTip("현재 프레임을 스틸로 추가 (S)")
        b1.clicked.connect(self.addStill.emit)
        b2 = QPushButton("영상 추가")
        b2.setIcon(icons.icon("film", theme.TEXT, 15))
        b2.setToolTip("In~Out 구간을 영상으로 추가 (V)")
        b2.clicked.connect(self.addVideo.emit)
        row.addWidget(b1, 1)
        row.addWidget(b2, 1)
        gl.addLayout(row)
        self.in_edit, self.out_edit = TCEdit(project, big=False), TCEdit(project, big=False)
        self.in_edit.frameEntered.connect(lambda f: self.inOutEntered.emit(f, self._out))
        self.out_edit.frameEntered.connect(lambda f: self.inOutEntered.emit(self._in, f + 1))
        for lab, ed, sig, tip in (
            ("In", self.in_edit, self.setIn, "현재 프레임을 In 으로 (I)"),
            ("Out", self.out_edit, self.setOut, "현재 프레임을 Out 으로, 이 프레임까지 포함 (O)"),
        ):
            r = QHBoxLayout()
            r.setSpacing(6)
            lb = QLabel(lab)
            lb.setObjectName("fieldLabel")
            lb.setMinimumWidth(30)
            r.addWidget(lb)
            r.addWidget(ed)
            r.addStretch(1)
            bt = QPushButton("현재 위치")
            bt.setObjectName("compact")
            bt.setToolTip(tip)
            bt.clicked.connect(sig.emit)
            r.addWidget(bt)
            gl.addLayout(r)
        self.dur_lbl = QLabel("")
        self.dur_lbl.setObjectName("hint")
        gl.addWidget(self.dur_lbl)
        v.addWidget(sec)
        # ---- 목록
        sec = Section(
            "목록",
            "inspector_feed_list",
            settings,
            help_text="항목을 끌어서 순서를 바꿀 수 있습니다.\n체크를 해제하면 최종 출력에서 제외됩니다 (삭제되지 않음).",
        )
        gl = sec.lay
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("hint")
        gl.addWidget(self.count_lbl)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setDragEnabled(True)
        self.list.model().rowsMoved.connect(self._rows_moved)
        self.list.setIconSize(QSize(48, 60))
        self.list.setMinimumHeight(200)
        self.list.currentItemChanged.connect(self._cur_changed)
        self.list.itemChanged.connect(self._item_changed)
        gl.addWidget(self.list, 1)
        rb = QHBoxLayout()
        rb.setSpacing(6)
        pb = QPushButton("재생/이동")
        pb.setIcon(icons.icon("play", theme.TEXT, 14))
        pb.setToolTip("선택한 항목 위치로 이동해 재생합니다")
        pb.clicked.connect(lambda: self.current_id() and self.playItem.emit(self.current_id()))
        db = QPushButton("삭제")
        db.setObjectName("danger")
        db.setIcon(icons.icon("trash-2", theme.ERR, 14))
        db.clicked.connect(lambda: self.current_id() and self.deleteClicked.emit(self.current_id()))
        rb.addWidget(pb, 1)
        rb.addWidget(db)
        gl.addLayout(rb)
        self.warn_lbl = QLabel("")
        self.warn_lbl.setObjectName("warn")
        self.warn_lbl.setWordWrap(True)
        gl.addWidget(self.warn_lbl)
        v.addWidget(sec, 1)
        # ---- 화면 배치 (출력 비율 + 선택 항목)
        sec = Section(
            "화면 배치",
            "inspector_feed_layout",
            settings,
            help_text="원본 비율: 여백/크롭 없이 원본과 같은 비율로 출력\n4:5: 여백(Fit) 또는 크롭(Fill). 배치는 선택한 항목에 적용됩니다.",
        )
        gl = sec.lay
        rr = QHBoxLayout()
        _rl = QLabel("출력 비율")
        _rl.setObjectName("fieldLabel")
        rr.addWidget(_rl)
        self.ratio_combo = QComboBox()
        self.ratio_combo.addItem("원본 비율 (기본)", "orig")
        self.ratio_combo.addItem("4:5 (1080×1350)", "45")
        self.ratio_combo.currentIndexChanged.connect(
            lambda _i: None if self._block else self.ratioChanged.emit(self.ratio_combo.currentData())
        )
        rr.addWidget(self.ratio_combo, 1)
        gl.addLayout(rr)
        self.layout_editor = LayoutEditor("선택 항목 화면 배치")
        self.layout_editor.changed.connect(self.layoutLive.emit)
        self.layout_editor.committed.connect(self.layoutCommit.emit)
        gl.addWidget(self.layout_editor)
        v.addWidget(sec)
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
        self.dur_lbl.setText(f"영상 구간 {d:.2f}초" + ("  ⚠ 60초 초과 시 자동 조정" if d > 60 else ""))

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
            f"포함 {total} / {FEED_MAX_FINAL} · 등록 {len(items)}개"
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
                self.layout_editor.set_info(f"출력 {g.canvas_w}×{g.canvas_h}", g.tier_note)

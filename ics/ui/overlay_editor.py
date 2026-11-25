"""STORY 인스펙터의 오버레이 편집기. Fill(9:16 꽉 참): 아티스트/제목 + 로고 + 문구를 영상 위에, Fit: 검정 여백 안에."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFontComboBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.model import StoryOverlay
from ..overlay_presets import LogoPresets
from ..render import overlay as ov_mod
from .panels import ResetSlider


def _shrinkable(c: QComboBox) -> None:
    """긴 항목 이름 때문에 인스펙터가 가로로 넓어지지 않게 한다."""
    c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(8)
    c.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)


FONT_STYLES = (
    ("Black Han Sans · 임팩트 (한글/영문)", "Black Han Sans", False),
    ("Pretendard Bold · 깔끔한 산세리프", "Pretendard", True),
    ("Montserrat · 모던 영문", "Montserrat", False),
    ("Playfair Display · 세리프 영문", "Playfair Display", False),
)


class Section(QWidget):
    """접고 펼 수 있는 작은 묶음 (머리 글자를 누르면 접힘). 접힘 상태는 설정에 남긴다."""

    def __init__(self, title: str, key: str, settings=None, open_default: bool = True, parent=None):
        super().__init__(parent)
        self._settings, self._key = settings, f"overlay_section_{key}"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.head = QToolButton()
        self.head.setObjectName("sectionHead")
        self.head.setText(title)
        self.head.setCheckable(True)
        self.head.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.head.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(self.head)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 0, 4)
        self.lay.setSpacing(6)
        lay.addWidget(self.body)
        is_open = open_default
        if settings is not None:
            is_open = bool(settings.get(self._key, open_default))
        self.head.setChecked(is_open)
        self._apply(is_open)
        self.head.toggled.connect(self._toggled)

    def _apply(self, on: bool) -> None:
        self.body.setVisible(on)
        self.head.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)

    def _toggled(self, on: bool) -> None:
        self._apply(on)
        if self._settings is not None:
            self._settings.set(self._key, bool(on))


class OverlayEditor(QGroupBox):
    changed = Signal(object)  # StoryOverlay (확정된 변경)

    def __init__(self, settings, parent=None):
        super().__init__("로고 · 제목 · 문구", parent)
        ov_mod.ensure_fonts()
        self.presets = LogoPresets(settings)
        self._ov = StoryOverlay()
        self._block = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(450)
        self._timer.timeout.connect(self._commit)
        v = QVBoxLayout(self)

        # ---- 배경 (Fit 전용)
        self.legacy_bg = QWidget()
        lv = QVBoxLayout(self.legacy_bg)
        lv.setContentsMargins(0, 0, 0, 0)
        self.bg_on = QCheckBox("여백을 흐린 영상으로 채우기")
        self.bg_on.toggled.connect(self._edited)
        lv.addWidget(self.bg_on)
        br = QHBoxLayout()
        _bl = QLabel("배경 어둡기")
        _bl.setObjectName("fieldLabel")
        br.addWidget(_bl)
        self.bg_dim = ResetSlider()
        self.bg_dim.setRange(0, 90)
        self.bg_dim.default = 45
        self.bg_dim.valueChanged.connect(self._edited_slider)
        br.addWidget(self.bg_dim, 1)
        self.bg_dim_lbl = QLabel("")
        self.bg_dim_lbl.setObjectName("hint")
        br.addWidget(self.bg_dim_lbl)
        lv.addLayout(br)

        v.addWidget(self.legacy_bg)

        def vrow(label, widget, value_lbl=None, into=None):
            r = QHBoxLayout()
            r.setSpacing(8)
            lb = QLabel(label)
            lb.setObjectName("fieldLabel")
            r.addWidget(lb)
            r.addWidget(widget, 1)
            if value_lbl is not None:
                value_lbl.setObjectName("hint")
                value_lbl.setMinimumWidth(34)
                value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                r.addWidget(value_lbl)
            (into or v).addLayout(r)
            return lb

        outer = v
        # ---- 제목
        self.sec_title = Section("제목", "title", settings)
        outer.addWidget(self.sec_title)
        v = self.sec_title.lay
        self.text_on = QCheckBox("아티스트 · 제목 표시")
        self.text_on.toggled.connect(self._edited)
        v.addWidget(self.text_on)
        self.f_artist = QLineEdit()
        self.f_artist.setPlaceholderText("아티스트 (비우면 프로젝트 아티스트)")
        self.f_artist.textEdited.connect(self._edited_slider)
        v.addWidget(self.f_artist)
        self.text = QLineEdit()
        self.text.setPlaceholderText("제목 (비우면 프로젝트 곡 제목)")
        self.text.textEdited.connect(self._edited_slider)
        v.addWidget(self.text)
        self.upper = QCheckBox("항상 대문자")
        self.upper.toggled.connect(self._edited)
        v.addWidget(self.upper)

        # ---- 로고
        self.sec_logo = Section("로고", "logo", settings)
        outer.addWidget(self.sec_logo)
        v = self.sec_logo.lay
        self.logo_on = QCheckBox("로고 표시")
        self.logo_on.toggled.connect(self._logo_toggled)
        v.addWidget(self.logo_on)
        pr = QHBoxLayout()
        pr.setSpacing(4)
        self.preset_combo = QComboBox()
        self.preset_combo.setToolTip("로고 프리셋 (고르면 바로 적용, 모든 프로젝트 공통)")
        _shrinkable(self.preset_combo)
        self.preset_combo.activated.connect(lambda _i: self._apply_preset())
        pr.addWidget(self.preset_combo, 1)
        more = QToolButton()
        more.setObjectName("ghostTool")
        more.setText("···")
        more.setToolTip("로고 파일 / 프리셋 관리")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(more)
        m.addAction("로고 파일 선택…", self._pick_logo)
        m.addAction("프리셋 다시 적용", self._apply_preset)
        m.addSeparator()
        m.addAction("지금 로고를 프리셋으로 저장…", self._save_preset)
        m.addAction("선택한 프리셋 삭제", self._delete_preset)
        more.setMenu(m)
        pr.addWidget(more)
        v.addLayout(pr)
        self.logo_lbl = QLabel("")
        self.logo_lbl.setObjectName("hint")
        self.logo_lbl.setWordWrap(True)
        v.addWidget(self.logo_lbl)
        self.logo_pos = QComboBox()
        self.logo_pos.addItem("아래 여백", "bottom")
        self.logo_pos.addItem("위 여백", "top")
        self.logo_pos.currentIndexChanged.connect(self._edited)
        self.logo_pos_lbl = vrow("위치", self.logo_pos)
        self.logo_size = ResetSlider()
        self.logo_size.setRange(5, 100)
        self.logo_size.default = 13
        self.logo_size.valueChanged.connect(self._edited_slider)
        self.logo_size_lbl = QLabel("")
        vrow("크기", self.logo_size, self.logo_size_lbl)

        # ---- Fill 전용: 문구, 불투명도, 그라데이션, 위치
        self.fill_box = QWidget()
        fv = QVBoxLayout(self.fill_box)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(6)
        self.logo_y = ResetSlider()
        self.logo_y.setRange(600, 950)
        self.logo_y.default = 838
        self.logo_y_lbl = QLabel("")
        vrow("높이", self.logo_y, self.logo_y_lbl, fv)
        self.sec_cap = Section("로고 위 문구", "caption", settings)
        fv.addWidget(self.sec_cap)
        cap_l = self.sec_cap.lay
        wr = QHBoxLayout()
        wr.setSpacing(4)
        self.cap_on = QCheckBox("표시")
        self.cap_on.toggled.connect(self._edited)
        wr.addWidget(self.cap_on)
        wr.addSpacing(6)
        self.work_beauty = QPushButton("BEAUTY")
        self.work_cleanup = QPushButton("CLEANUP")
        for c in (self.work_beauty, self.work_cleanup):
            c.setObjectName("chip")
            c.setCheckable(True)
            c.setMinimumWidth(78)
            c.toggled.connect(self._edited)
            wr.addWidget(c)
        wr.addStretch(1)
        cap_l.addLayout(wr)
        self.cap_preview = QLabel("")
        self.cap_preview.setObjectName("hint")
        cap_l.addWidget(self.cap_preview)
        self.sec_look = Section("표시 (불투명도 · 어둡게 · 위치)", "look", settings, open_default=False)
        fv.addWidget(self.sec_look)
        look_l = self.sec_look.lay
        self.opacity = ResetSlider()
        self.opacity.setRange(10, 100)
        self.opacity.default = 85
        self.opacity_lbl = QLabel("")
        self.scrim = ResetSlider()
        self.scrim.setRange(0, 150)
        self.scrim.default = 100
        self.scrim_lbl = QLabel("")
        self.title_y = ResetSlider()
        self.title_y.setRange(60, 400)
        self.title_y.default = 131
        self.title_y_lbl = QLabel("")
        vrow("불투명도", self.opacity, self.opacity_lbl, look_l)
        vrow("어둡게", self.scrim, self.scrim_lbl, look_l)
        vrow("제목 높이", self.title_y, self.title_y_lbl, look_l)
        tip = "기본 위치는 인스타 스토리 UI(위 프로필 줄, 아래 메시지 바)에 가려지지 않게 잡았습니다. 더블클릭: 기본값"
        for sl in (self.opacity, self.scrim, self.title_y, self.logo_y):
            sl.valueChanged.connect(self._edited_slider)
            sl.setToolTip(tip)
        self.scrim.setToolTip("글자가 잘 보이도록 위/아래를 어둡게 하는 정도. " + tip)
        v = outer
        v.addWidget(self.fill_box)

        # ---- Fit(검정 여백) 전용 글꼴 설정
        self.legacy_text = QWidget()
        lt = QVBoxLayout(self.legacy_text)
        lt.setContentsMargins(0, 0, 0, 0)
        sr = QHBoxLayout()
        sr.addWidget(QLabel("스타일"))
        self.style = QComboBox()
        for label, fam, bold in FONT_STYLES:
            self.style.addItem(label, (fam, bold))
        self.style.setToolTip("추천 글꼴 모음 (아래에서 다른 글꼴을 직접 고를 수도 있습니다)")
        _shrinkable(self.style)
        self.style.activated.connect(self._style_picked)
        sr.addWidget(self.style, 1)
        sr.addWidget(QLabel("자간"))
        self.spacing = ResetSlider()
        self.spacing.setRange(-10, 40)
        self.spacing.default = 4
        self.spacing.valueChanged.connect(self._edited_slider)
        sr.addWidget(self.spacing, 1)
        lt.addLayout(sr)
        tr = QHBoxLayout()
        self.font = QFontComboBox()
        self.font.setEditable(False)
        _shrinkable(self.font)
        self.font.currentFontChanged.connect(self._edited)
        tr.addWidget(self.font, 1)
        self.size = QSpinBox()
        self.size.setRange(12, 200)
        self.size.setSuffix(" px")
        self.size.setToolTip("1080 폭 기준 글자 크기 (여백에 안 들어가면 자동으로 줄입니다)")
        self.size.valueChanged.connect(self._edited_slider)
        tr.addWidget(self.size)
        self.bold = QCheckBox("굵게")
        self.bold.toggled.connect(self._edited)
        tr.addWidget(self.bold)
        lt.addLayout(tr)
        tr2 = QHBoxLayout()
        tr2.addWidget(QLabel("위치"))
        self.text_pos = QComboBox()
        self.text_pos.addItem("위 여백", "top")
        self.text_pos.addItem("아래 여백", "bottom")
        self.text_pos.currentIndexChanged.connect(self._edited)
        tr2.addWidget(self.text_pos)
        self.color_btn = QPushButton("글자색")
        self.color_btn.clicked.connect(self._pick_color)
        tr2.addWidget(self.color_btn)
        tr2.addStretch(1)
        lt.addLayout(tr2)
        v.addWidget(self.legacy_text)
        self.warn = QLabel("")
        self.warn.setObjectName("warn")
        self.warn.setWordWrap(True)
        v.addWidget(self.warn)
        self._reload_presets()

    # ------------------------------------------------------------------ 값 <-> UI
    def set_value(self, ov: StoryOverlay, warnings: list[str] | None = None) -> None:
        self._ov = StoryOverlay.from_dict(ov.to_dict())
        self._block = True
        self.bg_on.setChecked(ov.bg_on)
        self.bg_dim.setValue(ov.bg_dim)
        self.bg_dim_lbl.setText(f"{ov.bg_dim}%")
        self.logo_on.setChecked(ov.logo_on)
        self.logo_pos.setCurrentIndex(0 if ov.logo_pos == "bottom" else 1)
        self.logo_size.setValue(ov.logo_size)
        self.logo_size_lbl.setText(f"{ov.logo_size}%")
        self.logo_lbl.setText(
            ("로고: " + ov.logo_path.replace("\\", "/").split("/")[-1]) if ov.logo_path else "로고 파일 없음"
        )
        self.text_on.setChecked(ov.text_on)
        if not self.text.hasFocus():
            self.text.setText(ov.text)
        if not self.f_artist.hasFocus():
            self.f_artist.setText(ov.artist)
        self.upper.setChecked(ov.upper)
        self.cap_on.setChecked(ov.cap_on)
        self.work_beauty.setChecked(ov.work_beauty)
        self.work_cleanup.setChecked(ov.work_cleanup)
        self.opacity.setValue(ov.opacity)
        self.scrim.setValue(ov.scrim)
        self.title_y.setValue(ov.title_y)
        self.logo_y.setValue(ov.logo_y)
        self._update_labels()
        self.font.setCurrentFont(QFont(ov.text_font))
        self.spacing.setValue(ov.text_spacing)
        for i in range(self.style.count()):
            if self.style.itemData(i) == (ov.text_font, ov.text_bold):
                self.style.setCurrentIndex(i)
                break
        self.size.setValue(ov.text_size)
        self.bold.setChecked(ov.text_bold)
        self.text_pos.setCurrentIndex(0 if ov.text_pos == "top" else 1)
        self._paint_color(ov.text_color)
        self.warn.setText("\n".join("⚠ " + w for w in (warnings or [])))
        self._block = False

    def _paint_color(self, c: str) -> None:
        self.color_btn.setStyleSheet(f"border-left: 14px solid {c};")

    def _gather(self) -> StoryOverlay:
        o = StoryOverlay.from_dict(self._ov.to_dict())
        o.bg_on = self.bg_on.isChecked()
        o.bg_dim = self.bg_dim.value()
        o.logo_on = self.logo_on.isChecked()
        o.logo_pos = self.logo_pos.currentData()
        o.logo_size = self.logo_size.value()
        o.text_on = self.text_on.isChecked()
        o.text = self.text.text()
        o.artist = self.f_artist.text()
        o.upper = self.upper.isChecked()
        o.cap_on = self.cap_on.isChecked()
        o.work_beauty = self.work_beauty.isChecked()
        o.work_cleanup = self.work_cleanup.isChecked()
        o.opacity = self.opacity.value()
        o.scrim = self.scrim.value()
        o.title_y = self.title_y.value()
        o.logo_y = self.logo_y.value()
        o.text_font = self.font.currentFont().family()
        o.text_size = self.size.value()
        o.text_bold = self.bold.isChecked()
        o.text_spacing = self.spacing.value()
        o.text_pos = self.text_pos.currentData()
        return o

    def _style_picked(self, i: int) -> None:
        fam, bold = self.style.itemData(i)
        self._block = True
        self.font.setCurrentFont(QFont(fam))
        self.bold.setChecked(bold)
        self._block = False
        self._commit()

    def _logo_toggled(self, on: bool) -> None:
        """체크만 해도 바로 적용: 로고 파일이 아직 없으면 선택된 프리셋(없으면 첫 프리셋)을 자동으로 넣는다."""
        if self._block:
            return
        if on and not self._ov.logo_path:
            p = self.presets.get(self.preset_combo.currentText())
            if p is not None:
                o = self._gather()
                o.logo_on, o.logo_path, o.logo_size, o.logo_pos = True, self.presets.materialize(p), p.size, p.pos
                self._ov = o
                self.changed.emit(o)
                return
            self.logo_lbl.setText("로고 파일 없음: '로고 파일…' 로 선택하거나 프리셋을 저장하세요.")
        self._commit()

    def _edited(self, *_a) -> None:
        if not self._block:
            self._commit()

    def _edited_slider(self, *_a) -> None:  # 연속 입력은 잠시 기다렸다가 한 번에 확정
        if not self._block:
            self._update_labels()
            self._timer.start()

    def _update_labels(self) -> None:
        self.logo_size_lbl.setText(f"{self.logo_size.value()}%")
        self.bg_dim_lbl.setText(f"{self.bg_dim.value()}%")
        self.opacity_lbl.setText(f"{self.opacity.value()}%")
        self.scrim_lbl.setText(f"{self.scrim.value()}%")
        self.title_y_lbl.setText(f"{self.title_y.value() / 10:.1f}%")
        self.logo_y_lbl.setText(f"{self.logo_y.value() / 10:.1f}%")
        self.cap_preview.setText(
            ov_mod.caption_text(self._gather_caption()) or "문구 없음 (작업 종류를 하나 이상 선택)"
        )

    def _gather_caption(self) -> StoryOverlay:
        o = StoryOverlay()
        o.work_beauty, o.work_cleanup = self.work_beauty.isChecked(), self.work_cleanup.isChecked()
        return o

    def set_fill_mode(self, fill: bool) -> None:
        """Fill 이면 영상 위 오버레이 설정을, Fit 이면 검정 여백 설정(배경, 글꼴, 위치)을 보인다."""
        self.fill_box.setVisible(fill)
        self.f_artist.setVisible(fill)
        self.legacy_bg.setVisible(not fill)
        self.legacy_text.setVisible(not fill)
        self.logo_pos.setVisible(not fill)
        self.logo_pos_lbl.setVisible(not fill)

    def _commit(self) -> None:
        self._timer.stop()
        o = self._gather()
        self._ov = o
        self.changed.emit(o)

    # ------------------------------------------------------------------ 로고/프리셋
    def _reload_presets(self) -> None:
        cur = self.preset_combo.currentText()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for p in self.presets.all():
            self.preset_combo.addItem(p.name)
        i = self.preset_combo.findText(cur)
        self.preset_combo.setCurrentIndex(max(0, i))
        self.preset_combo.blockSignals(False)

    def _apply_preset(self) -> None:
        p = self.presets.get(self.preset_combo.currentText())
        if p is None:
            QMessageBox.information(self, "프리셋", "적용할 프리셋이 없습니다.")
            return
        o = self._gather()
        o.logo_on, o.logo_path, o.logo_size, o.logo_pos = True, self.presets.materialize(p), p.size, p.pos
        self._ov = o
        self.changed.emit(o)

    def _pick_logo(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "로고 이미지", "", "이미지 (*.png *.jpg *.jpeg *.webp);;모든 파일 (*)")
        if not f:
            return
        o = self._gather()
        o.logo_path, o.logo_on = f, True
        self._ov = o
        self.changed.emit(o)

    def _save_preset(self) -> None:
        o = self._gather()
        if not o.logo_path:
            QMessageBox.information(self, "프리셋", "먼저 로고 파일을 선택하세요.")
            return
        name, ok = QInputDialog.getText(self, "로고 프리셋 저장", "프리셋 이름 (같은 이름이면 덮어씁니다)")
        if not ok or not name.strip():
            return
        try:
            self.presets.save(name, o.logo_path, o.logo_size, o.logo_pos)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "프리셋", str(e))
            return
        self._reload_presets()
        self.preset_combo.setCurrentText(name.strip())

    def _delete_preset(self) -> None:
        p = self.presets.get(self.preset_combo.currentText())
        if p is None or p.builtin:
            QMessageBox.information(self, "프리셋", "기본 제공 프리셋은 삭제할 수 없습니다.")
            return
        if QMessageBox.question(
            self, "프리셋 삭제", f"'{p.name}' 프리셋을 삭제할까요? (이미 프로젝트에 넣은 로고는 유지됩니다)"
        ) != (QMessageBox.StandardButton.Yes):
            return
        self.presets.delete(p.name)
        self._reload_presets()

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(QColor(self._ov.text_color), self, "글자색")
        if c.isValid():
            o = self._gather()
            o.text_color = c.name()
            self._ov = o
            self.changed.emit(o)

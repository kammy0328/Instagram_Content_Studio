"""STORY 인스펙터의 오버레이 편집기: 로고(프리셋) + 제목 텍스트. 항상 검정 여백에만 표시된다."""

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
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from ..core.model import StoryOverlay
from ..overlay_presets import LogoPresets
from ..render import overlay as ov_mod


def _shrinkable(c: QComboBox) -> None:
    """긴 항목 이름 때문에 인스펙터가 가로로 넓어지지 않게 한다."""
    c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(8)
    c.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)


class OverlayEditor(QGroupBox):
    changed = Signal(object)  # StoryOverlay (확정된 변경)

    def __init__(self, settings, parent=None):
        super().__init__("오버레이 (영상 위가 아닌 검정 여백에만 표시)", parent)
        ov_mod.ensure_fonts()
        self.presets = LogoPresets(settings)
        self._ov = StoryOverlay()
        self._block = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(450)
        self._timer.timeout.connect(self._commit)
        v = QVBoxLayout(self)

        # ---- 로고
        self.logo_on = QCheckBox("로고 표시")
        self.logo_on.toggled.connect(self._edited)
        v.addWidget(self.logo_on)
        pr = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.setToolTip("저장해 둔 로고 프리셋 (모든 프로젝트에서 공통)")
        _shrinkable(self.preset_combo)
        pr.addWidget(self.preset_combo, 1)
        b_apply = QPushButton("프리셋 적용")
        b_apply.setToolTip("선택한 프리셋의 로고를 이 프로젝트 STORY 에 넣고 켭니다")
        b_apply.clicked.connect(self._apply_preset)
        pr.addWidget(b_apply)
        v.addLayout(pr)
        fr = QHBoxLayout()
        b_file = QPushButton("로고 파일…")
        b_file.clicked.connect(self._pick_logo)
        b_save = QPushButton("프리셋 저장")
        b_save.setToolTip("지금 로고(크기/위치 포함)를 프리셋으로 저장합니다. 이름이 같으면 덮어씁니다")
        b_save.clicked.connect(self._save_preset)
        b_del = QPushButton("프리셋 삭제")
        b_del.clicked.connect(self._delete_preset)
        for b in (b_file, b_save, b_del):
            fr.addWidget(b)
        v.addLayout(fr)
        self.logo_lbl = QLabel("")
        self.logo_lbl.setObjectName("muted")
        self.logo_lbl.setWordWrap(True)
        v.addWidget(self.logo_lbl)
        lr = QHBoxLayout()
        lr.addWidget(QLabel("위치"))
        self.logo_pos = QComboBox()
        self.logo_pos.addItem("위 여백", "top")
        self.logo_pos.addItem("아래 여백", "bottom")
        self.logo_pos.currentIndexChanged.connect(self._edited)
        lr.addWidget(self.logo_pos)
        lr.addWidget(QLabel("크기"))
        self.logo_size = QSlider(Qt.Orientation.Horizontal)
        self.logo_size.setRange(10, 100)
        self.logo_size.valueChanged.connect(self._edited_slider)
        lr.addWidget(self.logo_size, 1)
        self.logo_size_lbl = QLabel("")
        lr.addWidget(self.logo_size_lbl)
        v.addLayout(lr)

        # ---- 제목 텍스트
        self.text_on = QCheckBox("제목 텍스트 표시")
        self.text_on.toggled.connect(self._edited)
        v.addWidget(self.text_on)
        self.text = QLineEdit()
        self.text.setPlaceholderText("비워 두면 프로젝트명")
        self.text.textEdited.connect(self._edited_slider)
        v.addWidget(self.text)
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
        v.addLayout(tr)
        tr2 = QHBoxLayout()
        tr2.addWidget(QLabel("위치"))
        self.text_pos = QComboBox()
        self.text_pos.addItem("아래 여백", "bottom")
        self.text_pos.addItem("위 여백", "top")
        self.text_pos.currentIndexChanged.connect(self._edited)
        tr2.addWidget(self.text_pos)
        self.color_btn = QPushButton("글자색")
        self.color_btn.clicked.connect(self._pick_color)
        tr2.addWidget(self.color_btn)
        tr2.addStretch(1)
        v.addLayout(tr2)
        self.warn = QLabel("")
        self.warn.setObjectName("warn")
        self.warn.setWordWrap(True)
        v.addWidget(self.warn)
        self._reload_presets()

    # ------------------------------------------------------------------ 값 <-> UI
    def set_value(self, ov: StoryOverlay, warnings: list[str] | None = None) -> None:
        self._ov = StoryOverlay.from_dict(ov.to_dict())
        self._block = True
        self.logo_on.setChecked(ov.logo_on)
        self.logo_pos.setCurrentIndex(0 if ov.logo_pos == "top" else 1)
        self.logo_size.setValue(ov.logo_size)
        self.logo_size_lbl.setText(f"{ov.logo_size}%")
        self.logo_lbl.setText(
            ("로고: " + ov.logo_path.replace("\\", "/").split("/")[-1]) if ov.logo_path else "로고 파일 없음"
        )
        self.text_on.setChecked(ov.text_on)
        if not self.text.hasFocus():
            self.text.setText(ov.text)
        self.font.setCurrentFont(QFont(ov.text_font))
        self.size.setValue(ov.text_size)
        self.bold.setChecked(ov.text_bold)
        self.text_pos.setCurrentIndex(0 if ov.text_pos == "bottom" else 1)
        self._paint_color(ov.text_color)
        self.warn.setText("\n".join("⚠ " + w for w in (warnings or [])))
        self._block = False

    def _paint_color(self, c: str) -> None:
        self.color_btn.setStyleSheet(f"border-left: 14px solid {c};")

    def _gather(self) -> StoryOverlay:
        o = StoryOverlay.from_dict(self._ov.to_dict())
        o.logo_on = self.logo_on.isChecked()
        o.logo_pos = self.logo_pos.currentData()
        o.logo_size = self.logo_size.value()
        o.text_on = self.text_on.isChecked()
        o.text = self.text.text()
        o.text_font = self.font.currentFont().family()
        o.text_size = self.size.value()
        o.text_bold = self.bold.isChecked()
        o.text_pos = self.text_pos.currentData()
        return o

    def _edited(self, *_a) -> None:
        if not self._block:
            self._commit()

    def _edited_slider(self, *_a) -> None:  # 연속 입력은 잠시 기다렸다가 한 번에 확정
        if not self._block:
            self.logo_size_lbl.setText(f"{self.logo_size.value()}%")
            self._timer.start()

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

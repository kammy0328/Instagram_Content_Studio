"""CREDITS 탭: 자유 텍스트 -> 표준 형식 정규화, 검토 표, 캡션/댓글 분리·복사."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import credits as cr
from ..core.caption import count_all, count_chars


def _copy(text: str, toast=None) -> None:
    QApplication.clipboard().setText(text)
    if toast:
        toast("복사했습니다 (줄바꿈 유지)")


class CreditsPage(QWidget):
    openDictionary = Signal()
    toast = Signal(str)

    def __init__(self, project, iddict):
        super().__init__()
        self.project, self.iddict = project, iddict
        self._block = False
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        sp = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(sp)
        sp.addWidget(self._left())
        sp.addWidget(self._middle())
        sp.addWidget(self._right())
        sp.setSizes([320, 520, 420])
        self._raw_timer = QTimer(self)
        self._raw_timer.setSingleShot(True)
        self._raw_timer.timeout.connect(lambda: self.project.set_credits_raw(self.raw.toPlainText()))
        self._body_timer = QTimer(self)
        self._body_timer.setSingleShot(True)
        self._body_timer.timeout.connect(lambda: self.project.set_caption_body(self.body.toPlainText()))
        self._cmt_timer = QTimer(self)
        self._cmt_timer.setSingleShot(True)
        self._cmt_timer.timeout.connect(self._commit_comments)

    # ------------------------------------------------------------------ 왼쪽: 원문
    def _left(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        h = QLabel("크레딧 원문 (자유 텍스트)")
        h.setObjectName("h1")
        v.addWidget(h)
        hint = QLabel(
            "콜론·하이픈·슬래시·파이프, @아이디, instagram.com 링크 등 어떤 표기든 붙여넣으세요.\n"
            "표준 형식: ROLE | NAME (@instagram_id)"
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.raw = QPlainTextEdit()
        self.raw.setPlaceholderText(
            "Director: Mingi Kang @aarch.film\nAssistant Director - Yeyoung Kim (instagram.com/mimyo_yeye), Seohyun Son"
        )
        self.raw.textChanged.connect(lambda: None if self._block else self._raw_timer.start(600))
        v.addWidget(self.raw, 1)
        self.auto_unique = QCheckBox("사전의 유일한 ID 자동 적용")
        self.auto_unique.setToolTip("동명이인은 자동으로 확정하지 않고 후보로 남깁니다.")
        self.auto_unique.setChecked(True)
        v.addWidget(self.auto_unique)
        r = QHBoxLayout()
        b = QPushButton("정규화 실행")
        b.setObjectName("primary")
        b.clicked.connect(self.run_parse)
        d = QPushButton("ID 사전…")
        d.clicked.connect(self.openDictionary.emit)
        r.addWidget(b)
        r.addWidget(d)
        v.addLayout(r)
        return w

    # ------------------------------------------------------------------ 가운데: 결과 표
    def _middle(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        h = QLabel("정규화 결과 (직접 수정 가능)")
        h.setObjectName("h1")
        v.addWidget(h)
        self.summary = QLabel("")
        v.addWidget(self.summary)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["상태", "원문", "변환 결과 (수정 가능)", "비고"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(True)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._cell_edited)
        v.addWidget(self.table, 1)
        r = QGridLayout()
        for i, (text, fn) in enumerate(
            (
                ("제안 변환 수락", self.accept_selected),
                ("원문 그대로 확정", self.keep_raw),
                ("동명이인 후보 선택…", self.pick_candidate),
                ("ID를 사전에 저장", self.save_to_dict),
            )
        ):
            b = QPushButton(text)
            b.clicked.connect(fn)
            r.addWidget(b, i // 2, i % 2)
        v.addLayout(r)
        return w

    # ------------------------------------------------------------------ 오른쪽: 캡션/댓글
    def _right(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        h = QLabel("캡션 / 댓글")
        h.setObjectName("h1")
        lay.addWidget(h)
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText("캡션 본문 (해시태그·이모지 포함)")
        self.body.setMaximumHeight(130)
        self.body.textChanged.connect(lambda: None if self._block else self._body_timer.start(500))
        lay.addWidget(self.body)
        f = QFormLayout()
        self.limit = QSpinBox()
        self.limit.setRange(100, 10000)
        self.limit.setValue(2200)
        self.limit.valueChanged.connect(lambda v: None if self._block else self.project.set_caption_options(limit=v))
        self.mode = QComboBox()
        self.mode.addItem("UTF-16 코드유닛 (보수적·권장)", "utf16")
        self.mode.addItem("유니코드 코드포인트", "codepoint")
        self.mode.addItem("글자 단위(이모지 조합 1자)", "grapheme")
        self.mode.currentIndexChanged.connect(
            lambda _i: None if self._block else self.project.set_caption_options(count_mode=self.mode.currentData())
        )
        self.pos = QComboBox()
        self.pos.addItem("본문 뒤에 크레딧", "after")
        self.pos.addItem("크레딧을 본문 앞에", "before")
        self.pos.currentIndexChanged.connect(
            lambda _i: (
                None if self._block else self.project.set_caption_options(credits_position=self.pos.currentData())
            )
        )
        f.addRow("글자 수 한도", self.limit)
        f.addRow("계산 기준", self.mode)
        f.addRow("배치", self.pos)
        lay.addLayout(f)
        self.counts = QLabel("")
        self.counts.setObjectName("muted")
        self.counts.setWordWrap(True)
        lay.addWidget(self.counts)
        cap_box = QGroupBox("CAPTION")
        cl = QVBoxLayout(cap_box)
        self.cap_view = QPlainTextEdit()
        self.cap_view.setReadOnly(True)
        self.cap_view.setMinimumHeight(110)
        cl.addWidget(self.cap_view)
        r = QHBoxLayout()
        self.cap_lbl = QLabel("")
        b = QPushButton("캡션 복사")
        b.clicked.connect(lambda: _copy(self.cap_view.toPlainText(), self.toast.emit))
        b2 = QPushButton("전체 크레딧 복사")
        b2.clicked.connect(lambda: _copy("\n".join(cr.rows_to_lines(self.project.credit_rows())), self.toast.emit))
        r.addWidget(self.cap_lbl, 1)
        r.addWidget(b)
        r.addWidget(b2)
        cl.addLayout(r)
        lay.addWidget(cap_box)
        self.warn = QLabel("")
        self.warn.setObjectName("warn")
        self.warn.setWordWrap(True)
        lay.addWidget(self.warn)
        self.cm_area = QScrollArea()
        self.cm_area.setWidgetResizable(True)
        self.cm_host = QWidget()
        self.cm_lay = QVBoxLayout(self.cm_host)
        self.cm_lay.setContentsMargins(0, 0, 0, 0)
        self.cm_area.setWidget(self.cm_host)
        lay.addWidget(self.cm_area, 1)
        rb = QPushButton("댓글 자동 다시 계산")
        rb.clicked.connect(lambda: self.project.set_comments_override(None))
        lay.addWidget(rb)
        self.cm_edits: list[QPlainTextEdit] = []
        return w

    # ------------------------------------------------------------------ 동작
    def flush_pending(self) -> None:
        """렌더/종료 전에 디바운스 중인 입력을 프로젝트 상태에 반영한다."""
        for timer in (self._raw_timer, self._body_timer, self._cmt_timer):
            if timer.isActive():
                timer.stop()
                timer.timeout.emit()

    def run_parse(self) -> None:
        self._raw_timer.stop()
        self.project.set_credits_raw(self.raw.toPlainText())
        rows = self.project.parse_credits(self.iddict.lookup, self.auto_unique.isChecked())
        n = cr.count_review(rows)
        self.toast.emit(f"{len(rows)}행 정규화 완료" + (f" · 검토 필요 {n}행" if n else ""))

    def _selected_row(self) -> int | None:
        r = self.table.currentRow()
        return r if 0 <= r < len(self.project.state.credits_rows) else None

    def accept_selected(self) -> None:
        i = self._selected_row()
        if i is None:
            return
        rows = self.project.credit_rows()
        if not rows[i].candidate_text:
            self.toast.emit("이 행에는 제안 변환이 없습니다. 변환 결과 칸을 직접 수정하세요.")
            return
        cr.accept_candidate(rows[i])
        self.project.update_credit_row(i, rows[i])

    def keep_raw(self) -> None:
        i = self._selected_row()
        if i is None:
            return
        rows = self.project.credit_rows()
        rows[i].status = "edited"
        self.project.update_credit_row(i, rows[i])

    def pick_candidate(self) -> None:
        i = self._selected_row()
        if i is None:
            return
        rows = self.project.credit_rows()
        row = rows[i]
        for p in row.people:
            if p.candidates:
                item, ok = QInputDialog.getItem(
                    self,
                    "동명이인 후보",
                    f"'{p.name}' 의 인스타그램 ID 선택",
                    [f"@{h}" for h in p.candidates],
                    0,
                    False,
                )
                if ok:
                    p.handle, p.status, p.candidates, p.note = item.lstrip("@"), "ok", [], "사용자가 선택"
                    cr.finalize_row(row)
                    self.project.update_credit_row(i, row)
                return
        self.toast.emit("선택할 후보가 있는 인물이 없습니다.")

    def save_to_dict(self) -> None:
        n = 0
        company_roles = {"production", "제작사", "label", "agency", "제작"}
        for row in self.project.credit_rows():
            if row.status == "review":
                continue
            for p in row.people:
                if p.handle and p.name and p.status != "dict":
                    kind = "company" if row.role.casefold() in company_roles else "person"
                    self.iddict.add(p.name, p.handle, kind)
                    n += 1
        self.toast.emit(f"ID 사전에 {n}건 저장했습니다.")

    def _cell_edited(self, item: QTableWidgetItem) -> None:
        if self._block or item.column() != 2:
            return
        i = item.row()
        rows = self.project.credit_rows()
        if 0 <= i < len(rows) and rows[i].text != item.text():
            cr.set_manual_text(rows[i], item.text())
            self.project.update_credit_row(i, rows[i])

    def _commit_comments(self) -> None:
        self.project.set_comments_override([e.toPlainText() for e in self.cm_edits])

    # ------------------------------------------------------------------ 갱신
    def refresh(self) -> None:
        st = self.project.state
        self._block = True
        if not self.raw.hasFocus() and self.raw.toPlainText() != st.credits_raw:
            self.raw.setPlainText(st.credits_raw)
        if not self.body.hasFocus() and self.body.toPlainText() != st.caption_body:
            self.body.setPlainText(st.caption_body)
        o = st.caption_options
        self.limit.setValue(int(o.get("limit", 2200)))
        self.mode.setCurrentIndex(max(0, self.mode.findData(o.get("count_mode", "utf16"))))
        self.pos.setCurrentIndex(max(0, self.pos.findData(o.get("credits_position", "after"))))
        rows = self.project.credit_rows()
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            status = {"ok": "✔", "review": "⚠ 검토", "edited": "✎ 수정"}.get(r.status, r.status)
            if any(p.candidates for p in r.people):
                status += " 👥"
            if any(p.status == "dict" for p in r.people):
                status += " 📖"
            cells = [
                status,
                "\n".join(x.strip() for x in r.raw_lines),
                r.text,
                ("제안: " + r.candidate_text + "\n" if r.candidate_text and r.status == "review" else "")
                + "; ".join(r.reasons + [p.note for p in r.people if p.note and p.status != "review"]),
            ]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if c != 2:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if r.status == "review":
                    it.setBackground(QBrush(QColor(90, 60, 20)))
                self.table.setItem(i, c, it)
        self.table.resizeRowsToContents()
        self.table.blockSignals(False)
        nrev = cr.count_review(rows)
        self.summary.setText(
            f"{len(rows)}행 · "
            + (
                f"<span style='color:#f2b84b'>검토 필요 {nrev}행</span> (원문 그대로 유지됨)"
                if nrev
                else "<span style='color:#4cd08b'>모두 정상</span>"
            )
            if rows
            else ""
        )
        self._refresh_caption()
        self._block = False

    def _refresh_caption(self) -> None:
        res = self.project.caption_result()
        o = self.project.state.caption_options
        mode, limit = o.get("count_mode", "utf16"), int(o.get("limit", 2200))
        if self.cap_view.toPlainText() != res.caption:
            self.cap_view.setPlainText(res.caption)
        over = res.caption_count > limit
        self.cap_lbl.setText(f"{res.caption_count} / {limit}자")
        self.cap_lbl.setStyleSheet("color:#ff5d6c;font-weight:700;" if over else "")
        body_all = count_all(self.project.state.caption_body)
        self.counts.setText(
            "본문 글자 수 - UTF-16: {utf16} · 코드포인트: {codepoint} · 글자단위: {grapheme}  "
            "(이모지/복합문자는 기준에 따라 다릅니다)".format(**body_all)
        )
        self.warn.setText("\n".join(res.warnings))
        while self.cm_lay.count():
            w = self.cm_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.cm_edits = []
        for i, c in enumerate(res.comments, start=1):
            box = QGroupBox(f"COMMENT_{i:02d}")
            bl = QVBoxLayout(box)
            e = QPlainTextEdit(c)
            e.setMinimumHeight(70)
            e.setMaximumHeight(120)
            e.textChanged.connect(lambda: self._cmt_timer.start(600) if not self._block else None)
            bl.addWidget(e)
            rr = QHBoxLayout()
            lbl = QLabel(f"{count_chars(c, mode)} / {limit}자")
            e.textChanged.connect(lambda e=e, lbl=lbl: lbl.setText(f"{count_chars(e.toPlainText(), mode)} / {limit}자"))
            bt = QPushButton(f"댓글 {i} 복사")
            bt.clicked.connect(lambda _=False, e=e: _copy(e.toPlainText(), self.toast.emit))
            rr.addWidget(lbl, 1)
            rr.addWidget(bt)
            bl.addLayout(rr)
            self.cm_lay.addWidget(box)
            self.cm_edits.append(e)
        if not res.comments:
            lbl = QLabel("크레딧이 캡션에 모두 들어가 댓글이 필요하지 않습니다.")
            lbl.setObjectName("muted")
            self.cm_lay.addWidget(lbl)
        self.cm_lay.addStretch(1)

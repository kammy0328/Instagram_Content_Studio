"""게시(Publisher) 창. 기존 편집 화면은 그대로 두고, '게시…' 버튼으로 여는 별도 창이다.

세 탭: 게시(유형 선택 -> 검토 -> 승인) / 내역(상태, 취소, 결과 확인) / 연결(중계, 계정, 기능 확인).
자동 게시는 사용자가 검토 화면에서 명시적으로 승인한 뒤에만 시작한다."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..publisher.capability import FEATURES
from ..publisher.models import ALL_KINDS, Kind, State
from ..publisher.relay import RelayError
from ..publisher.service import DEFAULT_API_VERSION, PublisherService, ReviewRow, SubmitResult
from .dialogs import open_path

KIND_LABEL = {Kind.STORY: "STORY", Kind.REELS: "REELS", Kind.FEED: "FEED"}
STATE_LABEL = {
    State.AWAITING_APPROVAL: "승인 대기",
    State.MANUAL_REQUIRED: "수동 업로드 필요",
    State.SCHEDULED: "예약됨(미전송)",
    State.CLOUD_SCHEDULED: "클라우드 예약됨",
    State.PUBLISHED: "게시 완료",
    State.FAILED: "실패",
    State.CANCELLED: "취소됨",
    State.MISSED: "놓친 예약(확인 필요)",
    State.AUTH_REQUIRED: "재연결 필요",
    State.RECONCILIATION_REQUIRED: "결과 확인 필요",
    State.RETRY_WAIT: "재시도 대기",
}
FEATURE_LABEL = {
    "reels": "릴스",
    "story": "스토리",
    "feed_image": "피드 이미지 1장",
    "feed_video": "피드 영상 1개",
    "carousel": "피드 여러 장(캐러셀)",
    "comments": "첫 댓글",
    "scheduling": "예약(자체 구현)",
    "pkce": "PKCE 로그인",
}


def _fmt(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""


class PublisherDialog(QDialog):
    def __init__(self, project, service: PublisherService, parent=None, toast=None):
        super().__init__(parent)
        self.project, self.svc, self.toast = project, service, toast or (lambda m: None)
        self.report = None
        self.rows: list[ReviewRow] = []
        self.confirm = self._confirm_box  # 테스트에서 바꿀 수 있다
        self.setWindowTitle("게시 (Instagram Publisher)")
        self.resize(1000, 720)
        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs)
        self.tabs.addTab(self._build_publish(), "게시")
        self.tabs.addTab(self._build_history(), "내역")
        self.tabs.addTab(self._build_connect(), "연결")
        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.refresh_history)
        self.timer.start()
        self._refresh_outputs()
        self.refresh_history()
        if self.svc.missing_setup():
            self.tabs.setCurrentIndex(2)

    # ------------------------------------------------------------------ 탭 1: 게시
    def _build_publish(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.root = Path(self.project.output_root())
        self.out_lbl = QLabel()
        self.out_lbl.setWordWrap(True)
        v.addWidget(self.out_lbl)
        row = QHBoxLayout()
        self.chk: dict[Kind, QCheckBox] = {}
        for k in ALL_KINDS:
            c = QCheckBox(KIND_LABEL[k])
            self.chk[k] = c
            row.addWidget(c)
        self.chk_comment = QCheckBox("첫 댓글 사용")
        row.addWidget(self.chk_comment)
        row.addStretch(1)
        v.addLayout(row)
        sch = QHBoxLayout()
        self.r_now, self.r_sched = QRadioButton("지금 게시"), QRadioButton("예약")
        self.r_now.setChecked(True)
        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.when.setEnabled(False)
        self.r_sched.toggled.connect(self.when.setEnabled)
        self.policy = QComboBox()
        self.policy.addItem("한 건 실패 시 나머지 중단 (기본)", "stop")
        self.policy.addItem("실패해도 나머지 계속", "continue")
        for x in (self.r_now, self.r_sched, self.when, QLabel("  일괄 정책:"), self.policy):
            sch.addWidget(x)
        sch.addStretch(1)
        v.addLayout(sch)
        b = QHBoxLayout()
        self.btn_review = QPushButton("검토…")
        self.btn_review.clicked.connect(self.do_review)
        self.btn_export = QPushButton("수동 내보내기…")
        self.btn_export.clicked.connect(self.do_export)
        self.btn_open = QPushButton("출력 폴더 열기")
        self.btn_open.clicked.connect(lambda: open_path(str(self.root)))
        for x in (self.btn_review, self.btn_export, self.btn_open):
            b.addWidget(x)
        b.addStretch(1)
        v.addLayout(b)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["유형", "상태", "파일", "비고"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self._show_detail)
        v.addWidget(self.table, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("행을 선택하면 게시될 캡션/첫 댓글을 그대로 보여줍니다.")
        v.addWidget(self.detail, 1)
        self.btn_go = QPushButton("승인하고 게시/예약")
        self.btn_go.setObjectName("primary")
        self.btn_go.setEnabled(False)
        self.btn_go.clicked.connect(self.do_submit)
        v.addWidget(self.btn_go, 0, Qt.AlignmentFlag.AlignRight)
        return w

    def _refresh_outputs(self) -> None:
        b = self.svc.load_output(self.root)
        av = b.available()
        miss = [KIND_LABEL[k] for k in ALL_KINDS if not av[k]]
        self.out_lbl.setText(
            f"<b>{self.project.rec.name}</b> · 출력 폴더: {self.root}"
            + (
                f"<br><span style='color:#e0a000'>출력물이 없는 유형: {', '.join(miss)} (먼저 렌더링하세요)</span>"
                if miss
                else ""
            )
        )
        for k in ALL_KINDS:
            self.chk[k].setEnabled(av[k])
            if not av[k]:
                self.chk[k].setChecked(False)

    def selection(self) -> set[Kind]:
        return {k for k, c in self.chk.items() if c.isChecked()}

    def schedule_at(self) -> float | None:
        return self.when.dateTime().toSecsSinceEpoch() if self.r_sched.isChecked() else None

    def do_review(self) -> None:
        sel = self.selection()
        if not sel:
            QMessageBox.information(self, "게시", "게시할 유형을 하나 이상 선택하세요.")
            return
        try:
            self.report, self.rows = self.svc.prepare(
                self.root,
                str(getattr(self.project.rec, "id", "")),
                sel,
                self.chk_comment.isChecked(),
                self.policy.currentData(),
            )
        except (RuntimeError, ValueError) as e:
            QMessageBox.warning(self, "게시", str(e))
            return
        self._fill_table()

    def _fill_table(self) -> None:
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            for c, t in enumerate(
                (KIND_LABEL[r.kind], STATE_LABEL.get(r.state, r.state.value), ", ".join(r.files), r.blocked)
            ):
                it = QTableWidgetItem(t)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, c, it)
        self.btn_go.setEnabled(any(r.state is State.AWAITING_APPROVAL for r in self.rows))
        if self.rows:
            self.table.selectRow(0)

    def _show_detail(self) -> None:
        i = self.table.currentRow()
        if 0 <= i < len(self.rows):
            r = self.rows[i]
            if r.kind is Kind.STORY:
                self.detail.setPlainText("(스토리에는 캡션/댓글을 적용하지 않습니다.)")
            else:
                self.detail.setPlainText(f"[캡션]\n{r.caption}\n\n[첫 댓글]\n{r.first_comment or '(없음)'}")

    def _confirm_box(self, text: str) -> bool:
        return QMessageBox.question(self, "게시 승인", text) == QMessageBox.StandardButton.Yes

    def do_submit(self) -> None:
        ready = [r for r in self.rows if r.state is State.AWAITING_APPROVAL]
        if not ready or self.report is None:
            return
        cfg = self.svc.config()
        when = self.schedule_at()
        text = (
            f"계정: @{cfg['username'] or cfg['ig_id']}\n"
            f"게시 유형: {', '.join(KIND_LABEL[r.kind] for r in ready)}\n"
            f"시각: {'지금(약 1~2분 안에 게시)' if when is None else datetime.fromtimestamp(when).strftime('%Y-%m-%d %H:%M')}\n\n"
            "표시된 캡션/파일 그대로 게시됩니다. 이후 내용을 바꾸면 승인이 취소됩니다. 진행할까요?"
        )
        if not self.confirm(text):
            return
        try:
            res = self.svc.approve_and_submit(self.report, when)
        except (RuntimeError, RelayError) as e:
            QMessageBox.warning(self, "게시", str(getattr(e, "message", e)))
            return
        self._after_submit(res)

    def _after_submit(self, res: SubmitResult) -> None:
        self.rows = self.svc.review(self.report)
        self._fill_table()
        self.refresh_history()
        msg = []
        if res.errors:
            msg.append("실패: " + " / ".join(res.errors.values()))
        if res.not_started:
            msg.append(f"정책에 따라 시작하지 않은 항목 {len(res.not_started)}건")
        done = sum(1 for s in res.states.values() if s is State.CLOUD_SCHEDULED)
        self.toast(f"클라우드에 {done}건 맡겼습니다." + (" " + " ".join(msg) if msg else ""))
        if msg:
            QMessageBox.warning(self, "게시", "\n".join(msg))

    def do_export(self) -> None:
        dest = QFileDialog.getExistingDirectory(self, "내보낼 폴더 선택")
        if not dest:
            return
        sel = self.selection() or None
        name = f"{self.project.project_folder_name()}_{time.strftime('%Y%m%d_%H%M%S')}"
        files = self.svc.export_manual(self.root, Path(dest) / name, sel)
        self.toast(f"수동 업로드용 {len(files)}개 파일을 만들었습니다.")
        open_path(str(Path(dest) / name))

    # ------------------------------------------------------------------ 탭 2: 내역
    def _build_history(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("캡션/계정 검색")
        self.search.textChanged.connect(self.refresh_history)
        self.f_state = QComboBox()
        self.f_state.addItem("모든 상태", None)
        for s, t in STATE_LABEL.items():
            self.f_state.addItem(t, s)
        self.f_state.currentIndexChanged.connect(self.refresh_history)
        b_sync = QPushButton("상태 새로고침")
        b_sync.clicked.connect(lambda: self.refresh_history(sync=True))
        for x in (self.search, self.f_state, b_sync):
            top.addWidget(x)
        v.addLayout(top)
        self.hist = QTableWidget(0, 6)
        self.hist.setHorizontalHeaderLabels(["#", "유형", "상태", "예약", "게시물 ID", "메모"])
        self.hist.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.hist.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        v.addWidget(self.hist, 1)
        row = QHBoxLayout()
        self.b_cancel = QPushButton("예약 취소")
        self.b_cancel.clicked.connect(self.do_cancel)
        self.b_resolve = QPushButton("결과 확인…")
        self.b_resolve.clicked.connect(self.do_resolve)
        self.b_missed = QPushButton("놓친 예약 지금 게시")
        self.b_missed.clicked.connect(self.do_missed)
        self.b_copy = QPushButton("초안으로 다시 준비")
        self.b_copy.clicked.connect(self.do_redraft)
        for x in (self.b_cancel, self.b_resolve, self.b_missed, self.b_copy):
            row.addWidget(x)
        row.addStretch(1)
        v.addLayout(row)
        self.hist_ids: list[int] = []
        return w

    def refresh_history(self, *_a, sync: bool = False) -> None:
        if sync or any(self.svc.store.jobs(states={State.CLOUD_SCHEDULED})):
            try:
                if not self.svc.missing_setup():
                    self.svc.sync()
            except (RelayError, RuntimeError) as e:
                self.toast(f"상태를 가져오지 못했습니다: {getattr(e, 'message', e)}")
        jobs = self.svc.history(self.search.text().strip(), None, self.f_state.currentData())
        self.hist_ids = [j.id for j in jobs]
        self.hist.setRowCount(len(jobs))
        for i, j in enumerate(jobs):
            vals = (
                str(j.id),
                j.kind.upper(),
                STATE_LABEL.get(j.state, j.state.value),
                _fmt(j.schedule_at),
                j.media_id or "",
                j.error or "",
            )
            for c, t in enumerate(vals):
                it = QTableWidgetItem(t)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.hist.setItem(i, c, it)

    def _selected_job(self) -> int | None:
        i = self.hist.currentRow()
        return self.hist_ids[i] if 0 <= i < len(self.hist_ids) else None

    def do_cancel(self) -> None:
        jid = self._selected_job()
        if jid is None:
            return
        try:
            note = self.svc.cancel(jid)
        except (RelayError, RuntimeError, Exception) as e:  # 상태 전이 불가 등
            note = str(getattr(e, "message", e))
        if note:
            QMessageBox.warning(self, "취소", note)
        self.refresh_history()

    def do_resolve(self) -> None:
        jid = self._selected_job()
        if jid is None or self.svc.store.get(jid).state is not State.RECONCILIATION_REQUIRED:
            QMessageBox.information(self, "결과 확인", "'결과 확인 필요' 상태의 항목을 선택하세요.")
            return
        text, ok = QInputDialog.getText(
            self,
            "결과 확인",
            "인스타그램에서 직접 확인한 뒤, 게시되어 있으면 게시물 ID 를 입력하세요.\n게시되지 않았다면 비워 두세요(실패로 기록, 다시 게시하려면 새 승인 필요).",
        )
        if not ok:
            return
        try:
            self.svc.cloud.resolve(jid, text.strip() or None)
        except RelayError as e:
            QMessageBox.warning(self, "결과 확인", e.message)
        self.refresh_history()

    def do_missed(self) -> None:
        jid = self._selected_job()
        if jid is None or self.svc.store.get(jid).state is not State.MISSED:
            QMessageBox.information(self, "놓친 예약", "'놓친 예약' 상태의 항목을 선택하세요.")
            return
        if not self.confirm(
            "예약 시각이 지났습니다. 지금 게시할까요? (승인 이후 내용이 바뀌었다면 다시 승인해야 합니다.)"
        ):
            return
        try:
            st = self.svc.cloud.run_missed_now(jid)
            self.toast(
                "지금 게시를 요청했습니다." if st is State.CLOUD_SCHEDULED else "내용이 바뀌어 다시 승인이 필요합니다."
            )
        except (RelayError, ValueError) as e:
            QMessageBox.warning(self, "놓친 예약", str(getattr(e, "message", e)))
        self.refresh_history()

    def do_redraft(self) -> None:
        """이전 작업과 같은 유형을 선택한 채 게시 탭으로 돌아간다(캡션은 현재 출력 폴더 기준으로 다시 읽는다)."""
        jid = self._selected_job()
        if jid is None:
            return
        j = self.svc.store.get(jid)
        for k, c in self.chk.items():
            c.setChecked(c.isEnabled() and k.value == j.kind)
        self.tabs.setCurrentIndex(0)

    # ------------------------------------------------------------------ 탭 3: 연결
    def _build_connect(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        info = QLabel(
            "앱은 인스타그램을 직접 호출하지 않고, 사용자가 만든 Cloudflare 중계를 통해 게시합니다. "
            "비밀값(API 키, 토큰)은 이 PC 의 Windows 보호 저장소에 암호화되어 저장되며 설정 파일에는 남지 않습니다."
        )
        info.setWordWrap(True)
        v.addWidget(info)
        f = QFormLayout()
        c = self.svc.config()
        self.e_url, self.e_ig, self.e_user, self.e_ver = (
            QLineEdit(c["relay_url"]),
            QLineEdit(c["ig_id"]),
            QLineEdit(c["username"]),
            QLineEdit(c["api_version"] or DEFAULT_API_VERSION),
        )
        self.e_key, self.e_tok = QLineEdit(), QLineEdit()
        for e in (self.e_key, self.e_tok):
            e.setEchoMode(QLineEdit.EchoMode.Password)
        self.e_key.setPlaceholderText("(저장됨)" if self.svc.secrets.get("relay_api_key") else "중계 RELAY_API_KEY")
        self.e_tok.setPlaceholderText("(저장됨)" if self.svc.secrets.get("ig_token") else "인스타그램 계정 토큰")
        for lab, e in (
            ("중계 주소(https)", self.e_url),
            ("중계 API 키", self.e_key),
            ("인스타그램 계정 ID", self.e_ig),
            ("계정 이름", self.e_user),
            ("Graph API 버전", self.e_ver),
            ("계정 토큰", self.e_tok),
        ):
            f.addRow(lab, e)
        v.addLayout(f)
        self.persist_lbl = QLabel(
            "" if self.svc.secrets.persistent else "이 환경에서는 비밀값을 저장하지 않습니다(앱을 닫으면 다시 입력)."
        )
        v.addWidget(self.persist_lbl)
        bs = QPushButton("저장")
        bs.clicked.connect(self.save_connect)
        v.addWidget(bs, 0, Qt.AlignmentFlag.AlignLeft)
        v.addWidget(
            QLabel(
                "<b>기능 확인 상태</b> (실제 테스트 계정에서 성공을 확인한 기능만 켭니다. 계정/버전이 바뀌면 다시 꺼집니다)"
            )
        )
        self.feat = QTableWidget(len(FEATURES), 3)
        self.feat.setHorizontalHeaderLabels(["기능", "상태", ""])
        self.feat.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.feat, 1)
        self._fill_features()
        return w

    def _fill_features(self) -> None:
        p = self.svc.profile()
        for i, f in enumerate(FEATURES):
            self.feat.setItem(i, 0, QTableWidgetItem(FEATURE_LABEL.get(f, f)))
            self.feat.setItem(
                i,
                1,
                QTableWidgetItem(
                    p.state(f).value + (f" (한도 {p.carousel_limit})" if f == "carousel" and p.carousel_limit else "")
                ),
            )
            b = QPushButton("확인함으로 표시…")
            b.clicked.connect(lambda _=False, name=f: self.mark_feature(name))
            self.feat.setCellWidget(i, 2, b)

    def mark_feature(self, name: str) -> None:
        note, ok = QInputDialog.getText(
            self, "기능 확인", f"'{FEATURE_LABEL.get(name, name)}' 을(를) 어떤 테스트로 확인했나요? (근거 메모 필수)"
        )
        if not ok or not note.strip():
            return
        limit = None
        if name == "carousel":
            n, ok2 = QInputDialog.getInt(self, "캐러셀 한도", "실제로 확인한 최대 개수", 10, 2, 20)
            if not ok2:
                return
            limit = n
        try:
            self.svc.mark_feature(name, True, note, carousel_limit=limit)
        except ValueError as e:
            QMessageBox.warning(self, "기능 확인", str(e))
        self._fill_features()

    def save_connect(self) -> None:
        url = self.e_url.text().strip()
        if url and not (
            url.startswith("https://") or url.startswith("http://127.0.0.1") or url.startswith("http://localhost")
        ):
            QMessageBox.warning(self, "연결", "중계 주소는 https 여야 합니다.")
            return
        self.svc.save_config(url, self.e_ig.text(), self.e_user.text(), self.e_ver.text() or DEFAULT_API_VERSION)
        if self.e_key.text():
            self.svc.secrets.set("relay_api_key", self.e_key.text().strip())
            self.e_key.clear()
            self.e_key.setPlaceholderText("(저장됨)")
        if self.e_tok.text():
            self.svc.secrets.set("ig_token", self.e_tok.text().strip())
            self.e_tok.clear()
            self.e_tok.setPlaceholderText("(저장됨)")
        self._fill_features()
        miss = self.svc.missing_setup()
        self.toast("저장했습니다." + (f" 아직 필요: {', '.join(miss)}" if miss else ""))

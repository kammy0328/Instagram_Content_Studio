"""게시 창 스모크: 조합 선택 -> 검토 -> 승인 -> 내역, 연결 탭 저장(비밀값 분리). 오프스크린 Qt, 가짜 중계."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from ics.publisher.models import Kind, State  # noqa: E402
from ics.ui.publisher_dialog import PublisherDialog  # noqa: E402
from tests.test_publisher import out as _out_fixture  # noqa: E402
from tests.test_publisher_service import make  # noqa: E402

out = _out_fixture


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def fake_project(out):
    return SimpleNamespace(
        rec=SimpleNamespace(name="테스트 프로젝트", id="p1"),
        output_root=lambda: out,
        project_folder_name=lambda: "PROJ",
    )


def test_review_and_submit_selected_kinds_only(qapp, tmp_path, out):
    svc, relay, clock, *_ = make(tmp_path, out)
    d = PublisherDialog(fake_project(out), svc)
    d.confirm = lambda text: True
    assert all(d.chk[k].isEnabled() for k in Kind)
    d.chk[Kind.REELS].setChecked(True)
    d.chk[Kind.STORY].setChecked(True)
    d.do_review()
    assert [r.kind for r in d.rows] == [Kind.STORY, Kind.REELS] and d.btn_go.isEnabled()
    d.table.selectRow(1)
    assert "본문 #tag" in d.detail.toPlainText()
    d.table.selectRow(0)
    assert "스토리에는 캡션" in d.detail.toPlainText()
    d.r_sched.setChecked(True)
    d.do_submit()
    assert {j["payload"]["kind"] for j in relay.jobs.values()} == {"story", "reels"}
    assert d.hist.rowCount() == 2
    assert all("클라우드 예약" in d.hist.item(i, 2).text() for i in range(2))


def test_declined_confirmation_makes_no_external_call(qapp, tmp_path, out):
    svc, relay, *_ = make(tmp_path, out)
    d = PublisherDialog(fake_project(out), svc)
    d.confirm = lambda text: False
    d.chk[Kind.REELS].setChecked(True)
    d.do_review()
    d.do_submit()
    assert not relay.jobs and not relay.files
    assert all(r.state is State.AWAITING_APPROVAL for r in d.rows)


def test_missing_output_disables_kind_and_empty_selection_is_refused(qapp, tmp_path, out):
    for p in (out / "STORY").glob("*.mp4"):
        p.unlink()
    svc, *_ = make(tmp_path, out)
    d = PublisherDialog(fake_project(out), svc)
    assert not d.chk[Kind.STORY].isEnabled() and d.chk[Kind.REELS].isEnabled()
    assert d.selection() == set()


def test_connect_tab_saves_secrets_outside_settings(qapp, tmp_path, out):
    svc, relay, clock, sec, st = make(tmp_path, out)
    d = PublisherDialog(fake_project(out), svc)
    d.e_url.setText("https://relay.example.workers.dev")
    d.e_key.setText("NEWKEY123")
    d.e_tok.setText("IGQVJ_NEW_TOKEN_0123456789")
    d.save_connect()
    assert sec.get("relay_api_key") == "NEWKEY123" and sec.get("ig_token") == "IGQVJ_NEW_TOKEN_0123456789"
    text = (tmp_path / "s.json").read_text("utf-8")
    assert "NEWKEY123" not in text and "IGQVJ_NEW" not in text
    assert d.e_key.text() == "" and d.e_key.placeholderText() == "(저장됨)"


def test_insecure_relay_url_is_rejected(qapp, tmp_path, out, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    svc, *_ = make(tmp_path, out)
    d = PublisherDialog(fake_project(out), svc)
    d.e_url.setText("http://evil.example.com")
    d.save_connect()
    assert shown and svc.config()["relay_url"] == "https://relay.test"


def test_setup_missing_opens_connect_tab(qapp, tmp_path, out):
    from ics.publisher.secrets import MemorySecretStore
    from ics.publisher.service import PublisherService
    from ics.ui.settings import Settings

    svc = PublisherService(tmp_path / "p", Settings(tmp_path / "s.json"), MemorySecretStore())
    d = PublisherDialog(fake_project(out), svc)
    assert d.tabs.currentIndex() == 2


def test_cancel_from_history(qapp, tmp_path, out):
    svc, relay, clock, *_ = make(tmp_path, out)
    d = PublisherDialog(fake_project(out), svc)
    d.confirm = lambda t: True
    d.chk[Kind.REELS].setChecked(True)
    d.r_sched.setChecked(True)
    d.do_review()
    d.do_submit()
    d.hist.selectRow(0)
    d.do_cancel()
    assert svc.store.get(d.hist_ids[0]).state is State.CANCELLED

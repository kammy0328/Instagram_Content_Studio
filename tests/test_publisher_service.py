"""PublisherService: 설정 부족, 임의 조합, 즉시/예약, 정책 stop, 수동 내보내기, 비밀값 보관."""

from __future__ import annotations

import json

import pytest

from ics.publisher.models import Kind, State
from ics.publisher.secrets import DpapiSecretStore, MemorySecretStore
from ics.publisher.service import IMMEDIATE_LEAD, PublisherService
from ics.ui.settings import Settings
from tests.test_publisher import out as _out_fixture
from tests.test_relay_cloud import FakeRelay

out = _out_fixture


def make(tmp_path, out, verified=("reels", "story", "feed_image", "comments"), relay=None):
    st = Settings(tmp_path / "s.json")
    sec = MemorySecretStore()
    clock = {"t": 10_000.0}
    relay = relay or FakeRelay()
    svc = PublisherService(
        tmp_path / "pub", st, sec, relay_factory=lambda u, k: relay, clock=lambda: clock["t"], probe_media=False
    )
    svc.save_config("https://relay.test", "1789", "tester", "v99.0")
    sec.set("relay_api_key", "KEY")
    sec.set("ig_token", "IGQVJ_TOKEN_0123456789ABCDEF")
    for f in verified:
        svc.mark_feature(f, True, "테스트 계정에서 게시 성공 확인")
    return svc, relay, clock, sec, st


def test_missing_setup_blocks_use(tmp_path, out):
    svc = PublisherService(tmp_path / "p", Settings(tmp_path / "s.json"), MemorySecretStore())
    assert set(svc.missing_setup()) == {"중계 주소", "중계 API 키", "인스타그램 계정 ID", "계정 토큰"}
    with pytest.raises(RuntimeError):
        svc.prepare(out, "p", {Kind.REELS}, False)


def test_unverified_features_are_blocked_not_published(tmp_path, out):
    svc, relay, clock, *_ = make(tmp_path, out, verified=())
    report, rows = svc.prepare(out, "p", {Kind.REELS}, False)
    assert rows[0].state is State.MANUAL_REQUIRED and "UNKNOWN" in rows[0].blocked
    res = svc.approve_and_submit(report, None)
    assert res.states == {} and not relay.jobs, "막힌 항목은 승인/업로드하지 않는다"


def test_feature_verification_requires_note(tmp_path, out):
    svc, *_ = make(tmp_path, out, verified=())
    with pytest.raises(ValueError):
        svc.mark_feature("reels", True, "  ")
    with pytest.raises(ValueError):
        svc.mark_feature("nope", True, "x")


@pytest.mark.parametrize("sel", [{Kind.REELS}, {Kind.STORY}, {Kind.REELS, Kind.FEED}, {Kind.STORY, Kind.REELS}])
def test_arbitrary_selection_schedules_only_selected(tmp_path, out, sel):
    svc, relay, clock, *_ = make(tmp_path, out)
    report, rows = svc.prepare(out, "p", sel, True)
    assert {r.kind for r in rows} == sel
    # 피드는 2개 항목(캐러셀)이라 검증되지 않아 막힘 -> 막히지 않은 것만 예약된다
    res = svc.approve_and_submit(report, clock["t"] + 3600)
    submitted = {j["payload"]["kind"] for j in relay.jobs.values()}
    assert submitted <= {"story", "reels"} and len(submitted) == len(relay.jobs)
    assert all(s is State.CLOUD_SCHEDULED for s in res.states.values())


def test_immediate_uses_short_lead_and_scheduled_needs_two_minutes(tmp_path, out):
    svc, relay, clock, *_ = make(tmp_path, out)
    report, _ = svc.prepare(out, "p", {Kind.REELS}, False)
    res = svc.approve_and_submit(report, None)
    (jid,) = res.states
    assert svc.store.get(jid).schedule_at == clock["t"] + IMMEDIATE_LEAD
    assert next(iter(relay.jobs.values()))["run_at"] == int((clock["t"] + IMMEDIATE_LEAD) * 1000)

    svc2, relay2, clock2, *_ = make(tmp_path / "b", out)
    report2, _ = svc2.prepare(out, "p", {Kind.REELS}, False)
    res2 = svc2.approve_and_submit(report2, clock2["t"] + 30)
    assert res2.errors and not relay2.jobs, "예약은 2분 이상 뒤여야 한다"


def test_policy_stop_halts_remaining_when_one_fails(tmp_path, out):
    relay = FakeRelay()
    svc, relay, clock, *_ = make(tmp_path, out, relay=relay)
    report, _ = svc.prepare(out, "p", {Kind.STORY, Kind.REELS}, False, policy="stop")
    relay.down = True  # 첫 건(STORY)부터 실패
    res = svc.approve_and_submit(report, clock["t"] + 3600)
    assert len(res.errors) == 1 and len(res.not_started) == 1 and not relay.jobs
    # 연결 실패라 승인은 되었지만 클라우드로 표시되지 않음
    assert all(svc.store.get(j).state is not State.CLOUD_SCHEDULED for j in res.states)


def test_cancel_reports_when_not_guaranteed(tmp_path, out):
    svc, relay, clock, *_ = make(tmp_path, out)
    report, _ = svc.prepare(out, "p", {Kind.REELS}, False)
    res = svc.approve_and_submit(report, clock["t"] + 3600)
    (jid,) = res.states
    relay.cancel_status = 409
    assert "보장할 수 없" in svc.cancel(jid)
    assert svc.store.get(jid).state is State.CLOUD_SCHEDULED
    relay.cancel_status = 200
    assert svc.cancel(jid) == "" and svc.store.get(jid).state is State.CANCELLED


def test_direct_calls_are_impossible(tmp_path, out):
    svc, *_ = make(tmp_path, out)
    eng = svc._build()[0]  # noqa: SLF001
    with pytest.raises(RuntimeError):
        eng.t.create_container("1", {})
    with pytest.raises(RuntimeError):
        eng.host.upload(None)


def test_config_change_resets_verified_features(tmp_path, out):
    svc, *_ = make(tmp_path, out)
    assert svc.profile().enabled("reels")
    svc.save_config("https://relay.test", "OTHER", "x", "v99.0")
    assert not svc.profile().enabled("reels"), "계정이 바뀌면 다시 검증해야 한다"


def test_manual_export_orders_files_and_leaves_source_untouched(tmp_path, out):
    svc, *_ = make(tmp_path, out)
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    files = svc.export_manual(out, tmp_path / "manual")
    names = sorted(p.name for p in files)
    assert "FEED_01_VIDEO.mp4" in names and "FEED_02_STILL.jpg" in names and "STORY_01.mp4" in names
    assert (tmp_path / "manual" / "CAPTION.txt").read_text("utf-8") == "본문 #tag"
    assert "UPLOAD_GUIDE.txt" in names
    assert before == {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    only = svc.export_manual(out, tmp_path / "m2", {Kind.STORY})
    assert {p.name for p in only} == {"STORY_01.mp4", "UPLOAD_GUIDE.txt"}


def test_dpapi_store_never_writes_plaintext(tmp_path):
    prot = lambda b: bytes(x ^ 0x5A for x in b)  # noqa: E731  (테스트용 가짜 암호화)
    s = DpapiSecretStore(tmp_path / "sec.json", protect=prot, unprotect=prot)
    s.set("ig_token", "IGQVJ_SECRET_PLAIN_0123456789")
    raw = (tmp_path / "sec.json").read_text("utf-8")
    assert "SECRET_PLAIN" not in raw and json.loads(raw)
    assert s.get("ig_token") == "IGQVJ_SECRET_PLAIN_0123456789"
    s.delete("ig_token")
    assert s.get("ig_token") == ""
    (tmp_path / "sec.json").write_text('{"x": "!!!not-base64"}', "utf-8")
    assert DpapiSecretStore(tmp_path / "sec.json", protect=prot, unprotect=prot).get("x") == ""


def test_secrets_not_in_settings_file(tmp_path, out):
    svc, relay, clock, sec, st = make(tmp_path, out)
    text = (tmp_path / "s.json").read_text("utf-8")
    assert "KEY" not in text.replace("relay_api_key", "") and "IGQVJ" not in text


def test_restart_keeps_file_paths_so_submit_can_resume(tmp_path, out):
    svc, relay, clock, sec, st = make(tmp_path, out)
    report, _ = svc.prepare(out, "p", {Kind.REELS}, False)
    (jid,) = report.job_ids.values()
    relay.down = True
    res = svc.approve_and_submit(report, clock["t"] + 3600)
    assert jid in res.errors and svc.store.get(jid).state is State.SCHEDULED
    # 앱 재시작: 새 서비스 인스턴스, 같은 데이터 폴더
    svc2 = PublisherService(
        tmp_path / "pub", st, sec, relay_factory=lambda u, k: relay, clock=lambda: clock["t"], probe_media=False
    )
    relay.down = False
    assert svc2.cloud.submit(jid) is State.CLOUD_SCHEDULED
    assert len(relay.jobs) == 1


def test_integer_schedule_time_does_not_invalidate_approval(tmp_path, out):
    svc, relay, clock, *_ = make(tmp_path, out)
    report, _ = svc.prepare(out, "p", {Kind.REELS}, False)
    res = svc.approve_and_submit(report, int(clock["t"]) + 3600)  # UI 는 정수 초를 넘긴다
    assert list(res.states.values()) == [State.CLOUD_SCHEDULED] and not res.errors

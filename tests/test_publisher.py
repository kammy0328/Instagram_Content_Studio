"""Publisher 코어: 조합 선택, 승인/무효화, 멱등, 결과 불명, 재시도 정책, 댓글 분리, 예약, 기능 프로필. 모의 전송만 사용한다."""

from __future__ import annotations

import pytest

from ics.publisher import adapter
from ics.publisher.capability import CapabilityProfile, Support
from ics.publisher.engine import Account, MockHost, PublishEngine, mask
from ics.publisher.models import (
    IllegalTransition,
    JobSpec,
    Kind,
    MediaAsset,
    State,
    check_transition,
    diff_specs,
)
from ics.publisher.scheduler import LocalScheduler
from ics.publisher.store import PublisherStore
from ics.publisher.transport import (
    ApiError,
    GraphTransport,
    LiveNotApproved,
    MockTransport,
    TransportNetworkError,
    TransportTimeout,
)

ACC = Account("1789", "tester")


def jpeg(path, tag=b"a"):
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"\0" * 20 + tag + b"\xff\xd9")
    return path


def mp4(path, tag=b"v"):
    path.write_bytes(b"\0\0\0\x18ftypmp42" + b"\0" * 16 + tag)
    return path


@pytest.fixture()
def out(tmp_path):
    root = tmp_path / "PROJ"
    for d in ("STORY", "REELS", "FEED", "CAPTION"):
        (root / d).mkdir(parents=True)
    mp4(root / "STORY" / "p_STORY.mp4", b"s")
    mp4(root / "REELS" / "p_REELS.mp4", b"r")
    jpeg(root / "REELS" / "p_REELS_COVER.jpg", b"c")
    mp4(root / "FEED" / "01_VIDEO.mp4", b"f1")
    jpeg(root / "FEED" / "02_STILL.jpg", b"f2")
    (root / "CAPTION" / "CAPTION.txt").write_text("본문 #tag", "utf-8")
    (root / "CAPTION" / "COMMENT_01.txt").write_text("첫 댓글", "utf-8")
    (root / "CAPTION" / "CREDITS_ORIGINAL.txt").write_text("Director | A", "utf-8")
    return root


def profile(**on):
    p = CapabilityProfile(account_id=ACC.id, api_version="v99.0", permissions=("x",))
    for f in on.get("features", ("reels", "story", "feed_image", "comments", "scheduling")):
        p.mark_verified(f, "test")
    if "carousel" in on:
        p.mark_verified("carousel", "test", carousel_limit=on["carousel"])
    return p


def make(out, prof=None, transport=None, now=None, **kw):
    clock = {"t": 1000.0} if now is None else now
    t = transport or MockTransport()
    host = MockHost()
    eng = PublishEngine(
        PublisherStore(),
        t,
        host,
        prof or profile(),
        ACC,
        clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        probe_media=False,
        **kw,
    )
    b = adapter.read_output(out)
    return eng, t, host, adapter.build_specs(b, "p1", first_comment=True), clock


# ------------------------------------------------------------------ 읽기 전용 어댑터
def test_adapter_reads_without_touching_files(out):
    before = {p: p.stat().st_mtime_ns for p in out.rglob("*") if p.is_file()}
    b = adapter.read_output(out)
    assert b.story and b.reels and b.reels_cover and [a.role for a in b.feed] == ["video", "image"]
    assert b.caption == "본문 #tag" and b.comments == ["첫 댓글"] and b.credits == "Director | A"
    assert before == {p: p.stat().st_mtime_ns for p in out.rglob("*") if p.is_file()}
    assert sorted(p.name for p in out.rglob("*") if p.is_file()).count("p_STORY.mp4") == 1


def test_story_never_gets_caption_or_comment(out):
    specs = adapter.build_specs(adapter.read_output(out), "p", first_comment=True)
    assert specs[Kind.STORY].caption == "" and specs[Kind.STORY].first_comment == ""


# ------------------------------------------------------------------ 임의 조합 선택
@pytest.mark.parametrize(
    "sel",
    [
        {Kind.REELS},
        {Kind.STORY},
        {Kind.FEED},
        {Kind.REELS, Kind.FEED},
        {Kind.STORY, Kind.REELS},
        {Kind.STORY, Kind.REELS, Kind.FEED},
    ],
)
def test_any_combination_creates_jobs_only_for_selected_kinds(out, sel):
    eng, t, host, specs, _ = make(out, profile(features=("reels", "story", "feed_image", "comments"), carousel=10))
    rep = eng.prepare_batch(specs, sel)
    assert set(rep.job_ids) == sel and not rep.blocked
    for jid in rep.job_ids.values():
        eng.approve(jid)
    res = eng.run_batch(rep.batch_id)
    assert all(s is State.PUBLISHED for s in res.values()) and len(res) == len(sel)
    created = [c for c in t.calls if c[0] == "create_container"]
    # 피드(2개)는 자식 2 + 부모 1 = 컨테이너 3개. 선택하지 않은 유형은 호출조차 하지 않는다
    assert len(created) == len(sel - {Kind.FEED}) + (3 if Kind.FEED in sel else 0)
    assert sum(1 for c in created if c[2].get("media_type") == "STORIES") == (1 if Kind.STORY in sel else 0)
    assert sum(1 for c in created if c[2].get("media_type") == "REELS") == (1 if Kind.REELS in sel else 0)


def test_empty_selection_and_missing_output_are_rejected(out):
    eng, *_ = make(out)
    specs = adapter.build_specs(adapter.read_output(out), "p")
    with pytest.raises(ValueError):
        eng.prepare_batch(specs, set())
    del specs[Kind.STORY]
    with pytest.raises(ValueError, match="출력물이 없는"):
        eng.prepare_batch(specs, {Kind.STORY})


def test_params_per_kind_and_story_has_no_caption(out):
    eng, t, host, specs, _ = make(out, profile(features=("reels", "story", "feed_image", "comments")))
    rep = eng.prepare_batch(specs, {Kind.STORY, Kind.REELS})
    for j in rep.job_ids.values():
        eng.approve(j)
    eng.run_batch(rep.batch_id)
    params = {c[2].get("media_type"): c[2] for c in t.calls if c[0] == "create_container"}
    assert params["STORIES"].keys() == {"media_type", "video_url"}
    assert params["REELS"]["caption"] == "본문 #tag" and params["REELS"]["video_url"].startswith("https://")


# ------------------------------------------------------------------ 승인
def test_cannot_run_without_approval(out):
    eng2, t2, host2, specs2, _c = make(out)
    rep = eng2.prepare_batch(specs2, {Kind.REELS})
    jid = rep.job_ids[Kind.REELS]
    assert eng2.run_job(jid) is State.AWAITING_APPROVAL
    assert not [c for c in t2.calls if c[0] == "create_container"]


def test_file_change_after_approval_invalidates_and_blocks(out):
    eng, t, host, specs, _ = make(out)
    rep = eng.prepare_batch(specs, {Kind.REELS})
    jid = rep.job_ids[Kind.REELS]
    eng.approve(jid)
    mp4(out / "REELS" / "p_REELS.mp4", b"CHANGED")
    assert eng.run_job(jid) is State.AWAITING_APPROVAL
    assert "바뀌었습니다" in eng.store.get(jid).error
    assert eng.store.active_approval(jid) is None
    assert not host.uploads  # 업로드 직전 해시 재검사로 막힘


def test_text_change_invalidates_approval_with_reasons(out):
    eng, t, host, specs, _ = make(out)
    rep = eng.prepare_batch(specs, {Kind.REELS})
    jid = rep.job_ids[Kind.REELS]
    eng.approve(jid)
    s = specs[Kind.REELS]
    new = JobSpec(s.kind, s.assets, "다른 캡션", s.first_comment, s.credits, s.settings, s.project_id, s.output_version)
    assert eng.update_spec(jid, new) == ["캡션이 바뀌었습니다."]
    assert eng.store.get(jid).state is State.AWAITING_APPROVAL


def test_diff_specs_distinguishes_order_content_text():
    a = {
        "kind": "feed",
        "assets": [{"order": 0, "sha256": "x"}, {"order": 1, "sha256": "y"}],
        "caption": "c",
        "first_comment": "",
        "credits": "",
        "settings": {},
        "output_version": "1",
    }
    swapped = {**a, "assets": [{"order": 0, "sha256": "y"}, {"order": 1, "sha256": "x"}]}
    assert diff_specs(a, swapped) == ["미디어 순서가 바뀌었습니다."]
    assert "미디어 파일 내용이 바뀌었습니다." in diff_specs(
        a, {**a, "assets": [{"order": 0, "sha256": "z"}, {"order": 1, "sha256": "y"}]}
    )
    assert diff_specs(a, {**a, "caption": "d", "credits": "e"}) == ["캡션이 바뀌었습니다.", "크레딧이 바뀌었습니다."]


def test_account_change_blocks(out):
    eng, t, host, specs, _ = make(out)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    eng.account = Account("OTHER", "x")
    assert eng.run_job(jid) is State.AWAITING_APPROVAL


# ------------------------------------------------------------------ 중복 방지/멱등
def test_same_content_cannot_be_created_twice(out):
    eng, t, host, specs, _ = make(out)
    r1 = eng.prepare_batch(specs, {Kind.REELS})
    r2 = eng.prepare_batch(specs, {Kind.REELS})
    assert Kind.REELS in r1.job_ids and Kind.REELS not in r2.job_ids and "이미" in r2.blocked[Kind.REELS]


def test_double_run_does_not_publish_twice(out):
    eng, t, host, specs, _ = make(out)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    assert eng.run_job(jid) is State.PUBLISHED
    assert eng.run_job(jid) is State.PUBLISHED
    assert len([c for c in t.calls if c[0] == "publish"]) == 1


# ------------------------------------------------------------------ 결과 불명
def test_lost_publish_response_goes_to_reconciliation_and_is_never_republished(out):
    eng, t, host, specs, _ = make(out, transport=MockTransport(lose_publish_response=True))
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    assert eng.run_job(jid) is State.RECONCILIATION_REQUIRED
    for _ in range(3):
        assert eng.run_job(jid) is State.RECONCILIATION_REQUIRED
    assert len([c for c in t.calls if c[0] == "publish"]) == 1  # 재게시 금지
    assert len([c for c in t.calls if c[0] == "create_container"]) == 1
    r = eng.reconcile(jid)  # 컨테이너가 PUBLISHED 이므로 확정
    assert r["status"] == "PUBLISHED" and eng.store.get(jid).state is State.PUBLISHED
    assert eng.store.get(jid).media_id == r["media_id"]


def test_reconciliation_unconfirmed_needs_user_and_never_matches_by_caption(out):
    t = MockTransport(lose_publish_response=True)
    eng, _t, host, specs, _ = make(out, transport=t)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    eng.run_job(jid)
    t.containers[eng.store.get(jid).container_id]["published"] = False  # 컨테이너로는 확정 불가인 상황
    t.containers[eng.store.get(jid).container_id]["forced"] = "FINISHED"
    r = eng.reconcile(jid)
    assert r["needs_user"] and r["candidates"] and eng.store.get(jid).state is State.RECONCILIATION_REQUIRED
    assert eng.resolve(jid, None) is State.FAILED  # 게시되지 않음 확인: 실패 처리, 재시도는 새 승인


def test_crash_during_publishing_is_recovered_as_reconciliation(out):
    eng, t, host, specs, _ = make(out)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    for s in (State.UPLOADING, State.CREATING_CONTAINER, State.PROCESSING, State.READY_TO_PUBLISH, State.PUBLISHING):
        eng.store.set_state(jid, s)
    assert eng.recover_after_restart() == [jid]
    assert eng.store.get(jid).state is State.RECONCILIATION_REQUIRED


# ------------------------------------------------------------------ 오류 정책
def test_transient_error_retries_then_succeeds(out):
    t = MockTransport(faults={"create_container": [TransportNetworkError("x"), None]})
    eng, _t, host, specs, clock = make(out, transport=t)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    assert eng.run_job(jid) is State.RETRY_WAIT
    assert eng.run_job(jid) is State.RETRY_WAIT  # 아직 시간 전
    clock["t"] += 10
    assert eng.run_job(jid) is State.PUBLISHED


def test_retry_limit_then_failed(out):
    t = MockTransport(faults={"create_container": [TransportNetworkError("x")] * 10})
    eng, _t, host, specs, clock = make(out, transport=t)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    st = eng.run_job(jid)
    for _ in range(6):
        clock["t"] += 1000
        st = eng.run_job(jid)
    assert st is State.FAILED and "재시도 한도" in eng.store.get(jid).error


def test_auth_error_stops_and_requires_reauth(out):
    t = MockTransport(faults={"create_container": [ApiError(401, 190, 0, "token", "auth")]})
    eng, _t, host, specs, _ = make(out, transport=t)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    assert eng.run_job(jid) is State.AUTH_REQUIRED


def test_param_error_is_not_retried(out):
    t = MockTransport(faults={"create_container": [ApiError(400, 100, 0, "bad param", "param")]})
    eng, _t, host, specs, _ = make(out, transport=t)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    assert eng.run_job(jid) is State.FAILED
    assert len([c for c in t.calls if c[0] == "create_container"]) == 1


def test_container_error_status_fails_and_expired_recreates(out):
    t = MockTransport()
    eng, _t, host, specs, _ = make(out, transport=t)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    orig = t.create_container

    def flaky(ig, p):
        cid = orig(ig, p)
        if len([c for c in t.calls if c[0] == "create_container"]) == 1:
            t.containers[cid]["forced"] = "EXPIRED"
        return cid

    t.create_container = flaky
    assert eng.run_job(jid) is State.PUBLISHED
    assert len([c for c in t.calls if c[0] == "create_container"]) == 2  # 만료 -> 새 컨테이너
    t2 = MockTransport()
    eng2, _x, h2, sp2, _c = make(out, transport=t2)
    jid2 = eng2.prepare_batch(sp2, {Kind.STORY}).job_ids[Kind.STORY]
    eng2.approve(jid2)
    o2 = t2.create_container
    t2.create_container = lambda ig, p: (lambda c: (t2.containers[c].__setitem__("forced", "ERROR"), c)[1])(o2(ig, p))
    assert eng2.run_job(jid2) is State.FAILED


def test_processing_timeout_is_recoverable_wait(out):
    t = MockTransport(process_polls=10**6)
    eng, _t, host, specs, clock = make(out, transport=t)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    assert eng.run_job(jid) is State.RETRY_WAIT
    assert eng.store.get(jid).container_id  # ID 는 보존되어 이어서 조회한다
    t.process_polls = 0
    clock["t"] += 1000
    assert eng.run_job(jid) is State.PUBLISHED
    assert len([c for c in t.calls if c[0] == "create_container"]) == 1


def test_publishing_limit_waits_without_guessing(out):
    t = MockTransport(quota_total=0)
    eng, _t, host, specs, clock = make(out, transport=t)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    assert eng.run_job(jid) is State.RETRY_WAIT and "한도" in eng.store.get(jid).error
    assert not [c for c in t.calls if c[0] == "publish"]


# ------------------------------------------------------------------ 일괄 정책
def test_batch_policy_stop_leaves_the_rest_not_started_and_continue_goes_on(out):
    for policy, expect_story in (("stop", State.APPROVED), ("continue", State.PUBLISHED)):
        # 실행 순서는 스토리 -> 릴스 -> 피드. 첫 작업(스토리)이 규격 오류로 실패한다
        t = MockTransport(faults={"create_container": [ApiError(400, 100, 0, "bad", "param")]})
        eng, _t, host, specs, _ = make(out, profile(), t)
        rep = eng.prepare_batch(specs, {Kind.REELS, Kind.STORY}, policy=policy)
        for j in rep.job_ids.values():
            eng.approve(j)
        res = eng.run_batch(rep.batch_id)
        assert set(res) == set(rep.job_ids.values())
        assert eng.store.get(rep.job_ids[Kind.STORY]).state is State.FAILED
        assert eng.store.get(rep.job_ids[Kind.REELS]).state is expect_story


def test_success_is_never_auto_deleted_when_a_later_job_fails(out):
    t = MockTransport()
    eng, _t, host, specs, _ = make(out, transport=t)
    rep = eng.prepare_batch(specs, {Kind.STORY, Kind.REELS}, policy="continue")
    for j in rep.job_ids.values():
        eng.approve(j)
    mp4(out / "REELS" / "p_REELS.mp4", b"CHANGED")  # 릴스만 승인 후 변경
    res = eng.run_batch(rep.batch_id)
    assert res[rep.job_ids[Kind.STORY]] is State.PUBLISHED
    assert res[rep.job_ids[Kind.REELS]] is State.AWAITING_APPROVAL
    assert t.media  # 삭제 호출이 없다


# ------------------------------------------------------------------ 댓글
def test_comment_failure_keeps_publish_success_and_retries_only_the_comment(out):
    t = MockTransport(faults={"create_comment": [ApiError(400, 100, 0, "no perm", "param")]})
    eng, _t, host, specs, _ = make(out, transport=t)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    assert eng.run_job(jid) is State.PUBLISHED
    assert eng.comment_problems(jid)
    eng.retry_comments(jid)
    assert not eng.comment_problems(jid) and len([c for c in t.calls if c[0] == "publish"]) == 1


def test_comment_response_loss_does_not_duplicate(out):
    t = MockTransport(faults={"create_comment": [TransportTimeout("lost")]})
    eng, _t, host, specs, _ = make(out, transport=t)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    eng.run_job(jid)
    mid = eng.store.get(jid).media_id
    t.comments[mid] = [{"id": "K1", "text": "첫 댓글"}]  # 응답은 잃었지만 실제로는 등록된 상황
    eng.retry_comments(jid)
    assert not eng.comment_problems(jid)
    assert len([c for c in t.calls if c[0] == "create_comment"]) == 1


def test_comment_check_failure_stops_auto_posting(out):
    t = MockTransport(faults={"list_comments": [TransportNetworkError("x")]})
    eng, _t, host, specs, _ = make(out, transport=t)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    eng.run_job(jid)
    assert not [c for c in t.calls if c[0] == "create_comment"]
    assert eng.comment_problems(jid)


# ------------------------------------------------------------------ 기능 프로필/조건부 기능
def test_unknown_features_are_disabled_and_become_manual(out):
    eng, t, host, specs, _ = make(out, prof=CapabilityProfile(account_id=ACC.id))
    rep = eng.prepare_batch(specs, {Kind.REELS, Kind.STORY, Kind.FEED})
    assert set(rep.blocked) == {Kind.REELS, Kind.STORY, Kind.FEED}
    assert all(eng.store.get(j).state is State.MANUAL_REQUIRED for j in rep.job_ids.values())
    assert "UNKNOWN" in rep.blocked[Kind.REELS]


def test_carousel_disabled_without_verified_limit_and_limit_is_never_guessed(out):
    p = profile(features=("reels", "feed_image", "feed_video"))
    assert p.state("carousel") is Support.UNSUPPORTED and not p.enabled("carousel") and p.carousel_limit is None
    eng, t, host, specs, _ = make(out, prof=p)
    rep = eng.prepare_batch(specs, {Kind.FEED})  # 피드 2개 -> 캐러셀
    assert "carousel" in rep.blocked[Kind.FEED]


def test_carousel_enabled_only_within_verified_limit(out):
    p = profile(features=("reels",), carousel=1)
    eng, *_ = make(out, prof=p)
    specs = adapter.build_specs(adapter.read_output(out), "p")
    assert "한도(1)" in eng.prepare_batch(specs, {Kind.FEED}).blocked[Kind.FEED]
    p2 = profile(features=("reels", "comments"), carousel=10)
    eng2, t2, host2, sp2, _ = make(out, prof=p2)
    rep = eng2.prepare_batch(sp2, {Kind.FEED})
    assert not rep.blocked
    eng2.approve(rep.job_ids[Kind.FEED])
    assert eng2.run_job(rep.job_ids[Kind.FEED]) is State.PUBLISHED
    kinds = [c[2] for c in t2.calls if c[0] == "create_container"]
    assert [k.get("is_carousel_item") for k in kinds[:2]] == ["true", "true"] and kinds[2]["media_type"] == "CAROUSEL"
    assert kinds[2]["caption"] == "본문 #tag" and "children" in kinds[2] and "caption" not in kinds[0]


def test_profile_reverifies_when_account_version_or_permissions_change():
    p = profile()
    assert p.reverify_if_changed(ACC.id, "v99.0", ("x",)) == []
    assert p.enabled("reels")
    assert set(p.reverify_if_changed("NEW", "v100.0", ("x", "y"))) == {"account", "api_version", "permissions"}
    assert not p.enabled("reels") and p.state("reels") is Support.UNKNOWN


def test_profile_operator_can_disable_a_feature_and_roundtrips(tmp_path):
    p = profile()
    p.disabled_by_operator.add("reels")
    assert not p.enabled("reels") and "운영자" in p.reason_disabled("reels")
    f = tmp_path / "cap.json"
    p.save(f)
    q = CapabilityProfile.load(f)
    assert q.disabled_by_operator == {"reels"} and q.state("story") is Support.VERIFIED and q.evidence["reels"]


# ------------------------------------------------------------------ 예약
def test_scheduled_job_runs_at_the_time_and_not_before(out):
    eng, t, host, specs, clock = make(out)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid, schedule_at=clock["t"] + 3600)
    sch = LocalScheduler(eng)
    assert sch.tick() == {} and eng.store.get(jid).state is State.SCHEDULED
    clock["t"] += 3601
    assert sch.tick()[jid] is State.PUBLISHED


def test_missed_schedule_is_not_auto_published(out):
    eng, t, host, specs, clock = make(out)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid, schedule_at=clock["t"] + 60)
    sch = LocalScheduler(eng, grace=600)
    clock["t"] += 86400  # 앱이 꺼져 있었다
    assert sch.tick()[jid] is State.MISSED and not t.media
    assert sch.publish_missed_now(jid) is State.PUBLISHED


def test_missed_schedule_with_changed_file_requires_reapproval(out):
    eng, t, host, specs, clock = make(out)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid, schedule_at=clock["t"] + 60)
    sch = LocalScheduler(eng)
    clock["t"] += 86400
    sch.tick()
    mp4(out / "REELS" / "p_REELS.mp4", b"CHANGED")
    assert sch.publish_missed_now(jid) is State.AWAITING_APPROVAL and not t.media


def test_schedule_time_is_part_of_the_approval_snapshot(out):
    eng, t, host, specs, clock = make(out)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    h1 = eng.approve(jid, schedule_at=5000.0)
    eng.store.set_state(jid, State.AWAITING_APPROVAL)
    assert eng.approve(jid, schedule_at=6000.0) != h1


def test_schedule_with_changed_file_is_blocked_at_run_time(out):
    eng, t, host, specs, clock = make(out)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid, schedule_at=clock["t"] + 10)
    mp4(out / "REELS" / "p_REELS.mp4", b"CHANGED")
    clock["t"] += 11
    assert LocalScheduler(eng).tick()[jid] is State.AWAITING_APPROVAL and not t.media


# ------------------------------------------------------------------ 상태기계 / 보안 / 정리 / 이력
def test_state_machine_rejects_illegal_jumps():
    with pytest.raises(IllegalTransition):
        check_transition(State.DRAFT, State.PUBLISHED)
    with pytest.raises(IllegalTransition):
        check_transition(State.PUBLISHED, State.UPLOADING)
    with pytest.raises(IllegalTransition):
        check_transition(State.RECONCILIATION_REQUIRED, State.UPLOADING)  # 재게시 경로가 없다
    check_transition(State.RECONCILIATION_REQUIRED, State.PUBLISHED)


def test_live_transport_requires_explicit_approval():
    with pytest.raises(LiveNotApproved):
        GraphTransport("tok", "v99.0", live_approved=False)
    with pytest.raises(ValueError):
        GraphTransport("tok", "", live_approved=True)  # API 버전 추측 금지


def test_tokens_are_masked_in_logs(out):
    assert "SECRETTOKEN" not in mask("fail access_token=SECRETTOKEN&x=1 Bearer SECRETTOKEN")
    t = MockTransport(faults={"create_container": [ApiError(400, 1, 0, "bad access_token=SECRETTOKEN", "param")]})
    eng, _t, host, specs, _ = make(out, transport=t)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    eng.run_job(jid)
    dump = str(eng.store.attempts(jid)) + eng.store.get(jid).error
    assert "SECRETTOKEN" not in dump


def test_temp_uploads_are_cleaned_only_after_publish_and_original_is_kept(out):
    eng, t, host, specs, _ = make(out)
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    assert not host.deleted
    eng.run_job(jid)
    assert len(host.deleted) == 1 and (out / "REELS" / "p_REELS.mp4").exists()
    t2 = MockTransport(lose_publish_response=True)
    eng2, _t, h2, sp2, _c = make(out, transport=t2)
    j2 = eng2.prepare_batch(sp2, {Kind.STORY}).job_ids[Kind.STORY]
    eng2.approve(j2)
    t2.lose_publish_response = True
    eng2.run_job(j2)
    assert not h2.deleted  # 결과 불명 중에는 즉시 삭제하지 않는다


def test_history_search_and_copy_as_new_draft_needs_new_approval(out):
    eng, t, host, specs, _ = make(out)
    rep = eng.prepare_batch(specs, {Kind.REELS, Kind.STORY})
    for j in rep.job_ids.values():
        eng.approve(j)
    eng.run_batch(rep.batch_id)
    assert [j.kind for j in eng.store.search(kind="reels")] == ["reels"]
    assert len(eng.store.search(text="본문")) == 1
    assert len(eng.store.search(state=State.PUBLISHED)) == 2
    assert eng.store.search(project_id="nope") == []
    # 초안 복사: 같은 내용을 의도적 재게시로 새 작업으로 만들되 승인 전에는 게시되지 않는다
    old = eng.store.get(rep.job_ids[Kind.REELS])
    new_id = eng.store.create_job(
        None, "reels", ACC.id, ACC.username, "p1", old.spec, old.idempotency_key, True, repost_of=old.id
    )
    eng.store.set_state(new_id, State.VALIDATING)
    eng.store.set_state(new_id, State.AWAITING_APPROVAL)
    eng._specs[new_id] = specs[Kind.REELS]
    assert eng.run_job(new_id) is State.AWAITING_APPROVAL
    assert len([c for c in t.calls if c[0] == "publish"]) == 2


def test_cancel_does_not_claim_to_undo_sent_requests(out):
    eng, t, host, specs, _ = make(out, transport=MockTransport(lose_publish_response=True))
    jid = eng.prepare_batch(specs, {Kind.REELS}).job_ids[Kind.REELS]
    eng.approve(jid)
    eng.run_job(jid)
    assert eng.cancel(jid) is State.RECONCILIATION_REQUIRED
    j2 = eng.prepare_batch(specs, {Kind.STORY}).job_ids[Kind.STORY]
    assert eng.cancel(j2) is State.CANCELLED


def test_validation_blocks_bad_inputs(tmp_path):
    from ics.publisher.validate import validate_spec

    png = tmp_path / "x.jpg"
    png.write_bytes(b"\x89PNG....")
    spec = JobSpec(Kind.FEED, [MediaAsset.from_file(png, "image")], caption="x" * 2300)
    rep = validate_spec(spec, probe=False)
    msgs = " ".join(f.message for f in rep.findings if f.level == "error")
    assert "JPEG" in msgs and "2300자" in msgs and not rep.ok
    story = JobSpec(Kind.STORY, [MediaAsset.from_file(png, "image")], caption="안 됨")
    assert any("스토리" in f.message for f in validate_spec(story, probe=False).findings)

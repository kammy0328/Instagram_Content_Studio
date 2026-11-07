"""클라우드 예약: RelayClient, CloudScheduler(가짜 중계), 그리고 실제 Worker 코드와의 계약 테스트(node 가 있을 때)."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from ics.publisher.cloud import CloudCancelRefused, CloudScheduler
from ics.publisher.models import JobSpec, Kind, State
from ics.publisher.relay import PART_SIZE, RelayClient, RelayError
from tests.test_publisher import ACC, make, mp4
from tests.test_publisher import out as _out_fixture

out = _out_fixture  # 같은 출력 폴더 fixture 재사용
WORKER = Path(__file__).resolve().parents[1] / "worker"


class FakeRelay:
    def __init__(self):
        self.files, self.tokens, self.jobs, self.calls = {}, {}, {}, []
        self.cancel_status = 200
        self.down = False

    def _chk(self):
        if self.down:
            raise RelayError(0, "연결 실패")

    def upload_file(self, key, path):
        self._chk()
        self.files[key] = Path(path).read_bytes()
        return len(self.files[key])

    def put_token(self, ig_id, token, username="", expires_at=None):
        self._chk()
        self.tokens[ig_id] = token

    def create_job(self, job_id, ig_id, approval_hash, run_at_ms, payload):
        self._chk()
        self.calls.append(("create", job_id))
        self.jobs.setdefault(job_id, {"id": job_id, "state": "SCHEDULED", "payload": payload, "run_at": run_at_ms})
        return {"id": job_id}

    def get_job(self, job_id):
        self._chk()
        if job_id not in self.jobs:
            raise RelayError(404, "not found")
        return self.jobs[job_id]

    def cancel(self, job_id):
        self._chk()
        if self.cancel_status != 200:
            raise RelayError(self.cancel_status, "no")
        self.jobs[job_id]["state"] = "CANCELLED"
        return self.jobs[job_id]

    def resolve(self, job_id, media_id):
        j = self.jobs[job_id]
        j.update(state="PUBLISHED", media_id=media_id) if media_id else j.update(state="FAILED", error="확인됨")
        return j

    def run_now(self, job_id):
        self.jobs[job_id]["state"] = "SCHEDULED"
        return self.jobs[job_id]


def sched(out, relay=None, now=None, kinds=(Kind.REELS,), at=None):
    eng, t, host, specs, clock = make(out, now=now)
    relay = relay or FakeRelay()
    cs = CloudScheduler(eng, relay, "v99.0", lambda: "IGQVJ_TOKEN_0123456789ABCDEF")
    batch = eng.prepare_batch(specs, set(kinds))
    ids = list(batch.job_ids.values())
    for jid in ids:
        eng.approve(jid, schedule_at=at or clock["t"] + 3600)
    return eng, cs, relay, ids, specs, clock


# ------------------------------------------------------------------ RelayClient
def test_client_requires_https_and_key():
    with pytest.raises(ValueError):
        RelayClient("http://example.com", "k")
    with pytest.raises(ValueError):
        RelayClient("https://example.com", "")
    RelayClient("http://127.0.0.1:8787", "k")  # 로컬 테스트 예외


def test_client_multipart_splits_and_errors_are_masked(tmp_path):
    calls = []

    def http(method, url, headers, body, timeout):
        calls.append((method, url.split("test")[-1], len(body or b"")))
        assert headers["Authorization"] == "Bearer SECRETKEY"
        if url.endswith("/v1/media"):
            return 200, b'{"key":"k","upload_id":"u1"}'
        if "/parts/" in url:
            n = int(url.split("/parts/")[1].split("?")[0])
            return 200, json.dumps({"part_number": n, "etag": f"e{n}"}).encode()
        return 200, b"{}"

    c = RelayClient("https://relay.test", "SECRETKEY", http=http)
    big = tmp_path / "v.mp4"
    big.write_bytes(b"x" * (PART_SIZE * 2 + 5))
    assert c.upload_file("key.mp4", str(big)) == PART_SIZE * 2 + 5
    assert [x[2] for x in calls if "/parts/" in x[1]] == [PART_SIZE, PART_SIZE, 5]
    assert calls[-1][1].endswith("/complete")
    small = tmp_path / "s.jpg"
    small.write_bytes(b"abc")
    calls.clear()
    c.upload_file("s.jpg", str(small))
    assert [x[0] for x in calls] == ["PUT"]

    bad = RelayClient("https://relay.test", "SECRETKEY", http=lambda *a: (401, b'{"error":"unauthorized"}'))
    with pytest.raises(RelayError) as ei:
        bad.get_job("x")
    assert ei.value.status == 401 and "SECRETKEY" not in str(ei.value)


# ------------------------------------------------------------------ CloudScheduler (가짜 중계)
def test_submit_uploads_registers_and_marks_cloud(out):
    eng, cs, relay, ids, specs, clock = sched(out)
    assert eng.store.get(ids[0]).state is State.SCHEDULED
    assert cs.submit(ids[0]) is State.CLOUD_SCHEDULED
    assert len(relay.files) == 1 and relay.tokens == {ACC.id: "IGQVJ_TOKEN_0123456789ABCDEF"}
    ((rid, job),) = relay.jobs.items()
    assert job["payload"]["kind"] == "reels" and job["payload"]["api_version"] == "v99.0"
    assert job["payload"]["media"][0]["key"] in relay.files
    assert cs.is_cloud(ids[0])
    # 로컬 예약 실행기는 클라우드 작업을 건드리지 않는다
    from ics.publisher.scheduler import LocalScheduler

    clock["t"] += 7200
    assert LocalScheduler(eng).tick() == {}


def test_story_payload_has_no_caption_and_all_kinds_selectable(out):
    eng, cs, relay, ids, specs, clock = sched(out, kinds=(Kind.STORY, Kind.REELS))
    for i in ids:
        cs.submit(i)
    kinds = {j["payload"]["kind"]: j["payload"] for j in relay.jobs.values()}
    assert set(kinds) == {"story", "reels"}
    assert "caption" not in kinds["story"] and "first_comment" not in kinds["story"]


def test_submit_rejects_too_soon_and_changed_files(out):
    eng, cs, relay, ids, specs, clock = sched(out, at=1000.0 + 30)
    with pytest.raises(ValueError):
        cs.submit(ids[0])
    eng2, cs2, relay2, ids2, specs2, clock2 = sched(out)
    mp4(Path(out) / "REELS" / "p_REELS.mp4", b"CHANGED")  # 승인 후 파일 변경
    assert cs2.submit(ids2[0]) is State.AWAITING_APPROVAL
    assert not relay2.jobs and not relay2.files, "변경된 파일은 업로드하지 않는다"


def test_submit_is_idempotent_on_retry_after_failure(out):
    relay = FakeRelay()
    eng, cs, relay, ids, specs, clock = sched(out, relay=relay)
    relay.down = True
    with pytest.raises(RelayError):
        cs.submit(ids[0])
    assert eng.store.get(ids[0]).state is State.SCHEDULED, "실패하면 예약 상태 유지, 클라우드로 표시하지 않음"
    relay.down = False
    cs.submit(ids[0])
    assert len(relay.jobs) == 1


def test_sync_maps_remote_state_and_keeps_state_when_unreachable(out):
    eng, cs, relay, ids, specs, clock = sched(out)
    cs.submit(ids[0])
    rid = next(iter(relay.jobs))
    relay.jobs[rid]["state"] = "PROCESSING"
    assert cs.sync() == {ids[0]: State.CLOUD_SCHEDULED}
    assert "PROCESSING" in eng.store.get(ids[0]).error
    relay.down = True
    assert cs.sync() == {ids[0]: State.CLOUD_SCHEDULED}, "연결 실패 시 추측하지 않는다"
    relay.down = False
    relay.jobs[rid].update(state="PUBLISHED", media_id="M7")
    assert cs.sync() == {ids[0]: State.PUBLISHED}
    assert eng.store.get(ids[0]).media_id == "M7"


@pytest.mark.parametrize(
    "remote,local",
    [
        ("MISSED", State.MISSED),
        ("AUTH_REQUIRED", State.AUTH_REQUIRED),
        ("RECONCILIATION_REQUIRED", State.RECONCILIATION_REQUIRED),
        ("FAILED", State.FAILED),
    ],
)
def test_sync_terminal_and_attention_states(out, remote, local):
    eng, cs, relay, ids, specs, clock = sched(out)
    cs.submit(ids[0])
    relay.jobs[next(iter(relay.jobs))]["state"] = remote
    assert cs.sync()[ids[0]] is local


def test_edit_after_submit_cancels_remote_first(out):
    eng, cs, relay, ids, specs, clock = sched(out)
    cs.submit(ids[0])
    spec = specs[Kind.REELS]
    new = JobSpec(
        spec.kind,
        spec.assets,
        "바뀐 캡션",
        spec.first_comment,
        spec.credits,
        spec.settings,
        spec.project_id,
        spec.output_version,
    )
    reasons = eng.update_spec(ids[0], new)
    assert reasons and eng.store.get(ids[0]).state is State.AWAITING_APPROVAL
    assert next(iter(relay.jobs.values()))["state"] == "CANCELLED", "옛 내용이 게시되지 않도록 원격 취소"


def test_edit_blocked_when_remote_cannot_be_cancelled(out):
    eng, cs, relay, ids, specs, clock = sched(out)
    cs.submit(ids[0])
    relay.cancel_status = 409
    spec = specs[Kind.REELS]
    new = JobSpec(
        spec.kind,
        spec.assets,
        "바뀐 캡션",
        spec.first_comment,
        spec.credits,
        spec.settings,
        spec.project_id,
        spec.output_version,
    )
    with pytest.raises(CloudCancelRefused):
        eng.update_spec(ids[0], new)
    assert eng.store.get(ids[0]).state is State.CLOUD_SCHEDULED
    assert eng.store.active_approval(ids[0]) is not None, "수정이 거부되면 승인도 그대로"


def test_cancel_and_missed_run_now_and_resolve(out):
    eng, cs, relay, ids, specs, clock = sched(out)
    cs.submit(ids[0])
    rid = next(iter(relay.jobs))
    relay.jobs[rid]["state"] = "MISSED"
    cs.sync()
    assert cs.run_missed_now(ids[0]) is State.CLOUD_SCHEDULED and relay.jobs[rid]["state"] == "SCHEDULED"
    relay.jobs[rid]["state"] = "RECONCILIATION_REQUIRED"
    cs.sync()
    assert eng.store.get(ids[0]).state is State.RECONCILIATION_REQUIRED
    assert cs.resolve(ids[0], "M9") is State.PUBLISHED
    # 취소
    eng2, cs2, relay2, ids2, *_ = sched(out)
    cs2.submit(ids2[0])
    assert cs2.cancel(ids2[0]) is State.CANCELLED
    eng3, cs3, relay3, ids3, *_ = sched(out)
    cs3.submit(ids3[0])
    relay3.cancel_status = 409
    with pytest.raises(CloudCancelRefused):
        cs3.cancel(ids3[0])
    assert eng3.store.get(ids3[0]).state is State.CLOUD_SCHEDULED


# ------------------------------------------------------------------ 실제 Worker 코드와의 계약 테스트
def _node_has_sqlite() -> bool:
    if shutil.which("node") is None:
        return False
    r = subprocess.run(["node", "--no-warnings", "-e", "import('node:sqlite')"], capture_output=True, timeout=30)
    return r.returncode == 0


@pytest.mark.skipif(not _node_has_sqlite(), reason="node 22.5+ (node:sqlite) 없음")
def test_contract_against_real_worker_code(out):
    proc = subprocess.Popen(
        ["node", "--no-warnings", str(WORKER / "test" / "serve.js")], stdout=subprocess.PIPE, text=True, cwd=str(WORKER)
    )
    try:
        port = int(proc.stdout.readline().split()[1])
        base = f"http://127.0.0.1:{port}"

        def ctl(path, body):
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST")
            return json.loads(urllib.request.urlopen(req, timeout=20).read())

        relay = RelayClient(base, "relay-secret-key")
        eng, cs, _fake, ids, specs, clock = sched(
            out, relay=relay, now={"t": time.time()}, kinds=(Kind.STORY, Kind.REELS)
        )
        for i in ids:
            assert cs.submit(i) is State.CLOUD_SCHEDULED
        assert {j["state"] for j in relay.list_jobs()} == {"SCHEDULED"}
        due = int((time.time() + 3601) * 1000)
        for step in range(8):
            ctl("/__tick", {"now": due + step * 70000})
        assert set(cs.sync().values()) == {State.PUBLISHED}
        graph = json.loads(urllib.request.urlopen(base + "/__calls", timeout=20).read())
        bodies = " ".join(c["body"] for c in graph if "/media_publish" in c["url"])
        assert bodies.count("creation_id") == 2, "선택한 2건만 정확히 한 번씩 게시"
        # 잘못된 API 키는 거부
        with pytest.raises(RelayError) as ei:
            RelayClient(base, "wrong").list_jobs()
        assert ei.value.status == 401
    finally:
        proc.terminate()
        proc.wait(timeout=10)

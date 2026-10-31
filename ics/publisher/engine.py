"""게시 엔진: 임의 조합(스토리/릴스/피드 중 1~3개) 선택, 승인 스냅샷, 업로드-컨테이너-폴링-게시, 결과 불명 처리, 댓글 복구.

원칙: 결과가 불명한 게시는 절대 자동 재게시하지 않는다. 성공한 게시를 자동 삭제/롤백하지 않는다.
모든 외부 요청 전에 의도를 저장하고, 응답 후 결과를 저장한다. 시계/sleep 은 주입 가능하다(테스트)."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .capability import CapabilityProfile
from .models import (
    ALL_KINDS,
    JobSpec,
    Kind,
    MediaAsset,
    State,
    diff_specs,
    idempotency_key,
    snapshot_hash,
)
from .store import DuplicateJob, JobRow, PublisherStore
from .transport import ApiError, Transport, TransportNetworkError, TransportTimeout
from .validate import validate_spec

RETRY_DELAYS = (5, 30, 120)  # 초. 일시 오류만, 안전한 단계에서만
POLL_INTERVAL = 60  # 공식 문서 권장: 분당 1회
POLL_MAX_SECONDS = 300  # 최대 5분 폴링 후 복구 가능한 대기로 저장
MISSED_GRACE = 600  # 예약 시각에서 10분 넘게 지나면 자동 게시하지 않고 사용자 확인(MISSED)


@dataclass
class Account:
    id: str
    username: str


class MediaHost(Protocol):
    def upload(self, asset: MediaAsset) -> str: ...  # 공개 HTTPS URL
    def delete(self, url: str) -> None: ...


@dataclass
class MockHost:
    """모의 임시 HTTPS 저장소. 실제 업로드 없이 URL 발급/만료/삭제를 재현한다."""

    uploads: list = field(default_factory=list)
    deleted: list = field(default_factory=list)
    fail_next: Exception | None = None

    def upload(self, asset):
        if self.fail_next:
            e, self.fail_next = self.fail_next, None
            raise e
        url = f"https://mock.invalid/{asset.sha256[:12]}/{len(self.uploads)}"
        self.uploads.append((url, asset.path))
        return url

    def delete(self, url):
        self.deleted.append(url)


_TOKEN_RE = re.compile(r"(IG[A-Za-z0-9_-]{20,}|access_token=[^&\s]+|Bearer\s+[A-Za-z0-9._-]+)")


def mask(text: str) -> str:
    return _TOKEN_RE.sub("***", str(text))


@dataclass
class BatchReport:
    batch_id: int
    job_ids: dict[Kind, int]
    blocked: dict[Kind, str]  # 사전 점검에서 막힌 항목과 사유 (수동 전환 대상)


class PublishEngine:
    def __init__(
        self,
        store: PublisherStore,
        transport: Transport,
        host: MediaHost,
        profile: CapabilityProfile,
        account: Account,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        probe_media: bool = True,
    ):
        self.store, self.t, self.host, self.profile, self.account = store, transport, host, profile, account
        self.clock, self.sleep, self.probe = clock, sleep, probe_media
        self.simulation = bool(getattr(transport, "is_simulation", False))
        self.cloud = None  # CloudScheduler 가 연결되면 설정된다 (수정 시 원격 취소 훅)
        self._specs: dict[int, JobSpec] = {}
        self._urls: dict[int, list[str]] = {}

    # ====================================================================== 준비/검토
    def _feature_for(self, spec: JobSpec) -> str:
        if spec.kind is Kind.REELS:
            return "reels"
        if spec.kind is Kind.STORY:
            return "story"
        n = len([a for a in spec.assets if a.role != "cover"])
        if n > 1:
            return "carousel"
        return "feed_video" if spec.assets[0].role == "video" else "feed_image"

    def prepare_batch(self, specs: dict[Kind, JobSpec], selection: set[Kind], policy: str = "stop") -> BatchReport:
        """selection 은 1~3개의 임의 조합. 선택하지 않은 유형은 작업을 만들지 않는다."""
        sel = [k for k in ALL_KINDS if k in selection]
        if not sel:
            raise ValueError("게시할 유형을 하나 이상 선택하세요.")
        if policy not in ("stop", "continue"):
            raise ValueError(policy)
        missing = [k.value for k in sel if k not in specs]
        if missing:
            raise ValueError(f"출력물이 없는 유형을 선택했습니다: {', '.join(missing)}")
        bid = self.store.create_batch(self.account.id, [k.value for k in sel], policy)
        ids, blocked = {}, {}
        for k in sel:
            spec = specs[k]
            key = idempotency_key(self.account.id, spec)
            try:
                jid = self.store.create_job(
                    bid,
                    k.value,
                    self.account.id,
                    self.account.username,
                    spec.project_id,
                    self._persisted(spec),
                    key,
                    self.simulation,
                )
            except DuplicateJob as d:
                blocked[k] = (
                    f"같은 내용이 이미 게시/예약되어 있습니다 (작업 #{d.existing_id}). 의도적 재게시만 별도 승인으로 가능합니다."
                )
                continue
            ids[k] = jid
            self._specs[jid] = spec
            self.store.set_state(jid, State.VALIDATING)
            reason = self._precheck(spec)
            if reason:
                blocked[k] = reason
                self.store.set_state(jid, State.MANUAL_REQUIRED, error=reason)
            else:
                self.store.set_state(jid, State.AWAITING_APPROVAL)
        return BatchReport(bid, ids, blocked)

    def _precheck(self, spec: JobSpec) -> str:
        rep = validate_spec(spec, probe=self.probe)
        errs = [f.message for f in rep.findings if f.level == "error"]
        if errs:
            return " / ".join(errs)
        feature = self._feature_for(spec)
        if not self.profile.enabled(feature):
            return f"{feature}: {self.profile.reason_disabled(feature)}"
        if feature == "carousel":
            n = len([a for a in spec.assets if a.role != "cover"])
            if n > (self.profile.carousel_limit or 0):
                return f"선택한 {n}개가 검증된 캐러셀 한도({self.profile.carousel_limit})를 넘습니다. 자동으로 나누지 않습니다."
        if spec.first_comment and not self.profile.enabled("comments"):
            return "첫 댓글: " + self.profile.reason_disabled("comments")
        return ""

    def exclude_job(self, job_id: int) -> None:
        """사전 점검에서 막힌 항목을 승인 전에 제외(취소)."""
        self.store.set_state(job_id, State.CANCELLED, error="사용자가 제외함")

    # ====================================================================== 승인
    def approve(self, job_id: int, schedule_at: float | None = None) -> str:
        job = self.store.get(job_id)
        if job.state is not State.AWAITING_APPROVAL:
            raise ValueError(f"승인할 수 없는 상태입니다: {job.state.value}")
        spec = self._spec(job)
        h = snapshot_hash(job.account_id, job.username, spec, schedule_at)
        self.store.save_approval(job_id, h, {**spec.to_dict(), "schedule_at": schedule_at})
        self.store.set_state(job_id, State.APPROVED, approval_hash=h, approved_at=self.clock(), schedule_at=schedule_at)
        if schedule_at is not None:
            self.store.set_state(job_id, State.SCHEDULED)
        return h

    def update_spec(self, job_id: int, spec: JobSpec) -> list[str]:
        """승인 뒤 내용이 바뀌면 승인을 무효화하고 변경 사유를 돌려준다. 재승인 전에는 게시하지 않는다."""
        job = self.store.get(job_id)
        ap = self.store.active_approval(job_id)
        reasons = diff_specs(ap[1], spec.to_dict()) if ap else []
        if ap and reasons and job.state is State.CLOUD_SCHEDULED and self.cloud is not None:
            self.cloud.cancel_remote(
                job_id
            )  # 취소를 보장할 수 없으면 예외로 수정 자체를 막는다 (옛 내용이 게시되는 사고 방지)
        self._specs[job_id] = spec
        if ap and reasons:
            self.store.invalidate_approval(job_id, " / ".join(reasons))
            if job.state in (State.APPROVED, State.SCHEDULED, State.CLOUD_SCHEDULED):
                self.store.set_state(
                    job_id, State.AWAITING_APPROVAL, spec_json=json.dumps(self._persisted(spec), ensure_ascii=False)
                )
        return reasons

    @staticmethod
    def _persisted(spec: JobSpec) -> dict:
        """DB 에 저장하는 형태: 승인 스냅샷(to_dict)에는 넣지 않는 파일 경로를 따로 보관해, 앱을 다시 켜도 이어서 진행할 수 있게 한다."""
        return {**spec.to_dict(), "_paths": [a.path for a in spec.assets]}

    def _spec(self, job: JobRow) -> JobSpec:
        if job.id in self._specs:
            return self._specs[job.id]
        d = job.spec
        paths = d.get("_paths", [])
        spec = JobSpec(
            Kind(d["kind"]),
            [
                MediaAsset(
                    a.get("path") or (paths[i] if i < len(paths) else ""), a["role"], a["sha256"], a["size"], a["order"]
                )
                for i, a in enumerate(d["assets"])
            ],
            d["caption"],
            d["first_comment"],
            d["credits"],
            d["settings"],
            d["project_id"],
            d["output_version"],
        )
        return spec

    def verify_before_upload(self, job: JobRow) -> list[str]:
        """업로드 직전 해시/계정/승인 재검사. 사유 목록이 비어 있어야 진행한다."""
        reasons = []
        ap = self.store.active_approval(job.id)
        if ap is None:
            return ["유효한 승인이 없습니다."]
        spec = self._spec(job)
        for a in spec.assets:
            try:
                now = MediaAsset.from_file(a.path, a.role).sha256
            except OSError:
                reasons.append(f"파일을 읽을 수 없습니다: {a.path}")
                continue
            if now != a.sha256:
                reasons.append(f"승인 후 파일이 바뀌었습니다: {a.path}")
        if job.account_id != self.account.id:
            reasons.append("승인한 계정과 현재 연결된 계정이 다릅니다.")
        want = snapshot_hash(job.account_id, job.username, spec, job.schedule_at)
        if want != ap[0] and not reasons:
            reasons += diff_specs(ap[1], spec.to_dict()) or ["승인 스냅샷과 현재 내용이 다릅니다."]
        return reasons

    # ====================================================================== 실행
    def run_batch(self, batch_id: int) -> dict[int, State]:
        """정책: stop(기본) - 한 건이 실패/불명이면 나머지를 시작하지 않는다. continue - 계속 진행. 성공분은 삭제하지 않는다."""
        with self.store._lock:  # noqa: SLF001
            r = self.store._c.execute("SELECT policy FROM batches WHERE id=?", (batch_id,)).fetchone()  # noqa: SLF001
            policy = r[0] if r else "stop"
        out = {}
        halted = False
        for job in self.store.jobs(batch_id):
            if job.state not in (State.APPROVED, State.SCHEDULED):
                out[job.id] = job.state
                continue
            if halted:
                out[job.id] = job.state  # 시작하지 않음 (승인 상태 유지)
                continue
            st = self.run_job(job.id)
            out[job.id] = st
            if st not in (State.PUBLISHED,) and policy == "stop":
                halted = True
        return out

    def run_job(self, job_id: int) -> State:
        job = self.store.get(job_id)
        if job.state in (State.APPROVED, State.SCHEDULED):
            reasons = self.verify_before_upload(job)
            if reasons:
                self.store.invalidate_approval(job_id, " / ".join(reasons))
                self.store.set_state(job_id, State.AWAITING_APPROVAL, error=" / ".join(reasons))
                return State.AWAITING_APPROVAL
            feature = self._feature_for(self._spec(job))
            if not self.profile.enabled(feature):
                msg = f"{feature}: {self.profile.reason_disabled(feature)}"
                self.store.set_state(job_id, State.AWAITING_APPROVAL, error=msg)
                return State.AWAITING_APPROVAL
            self.store.set_state(job_id, State.UPLOADING, error=None)
        elif job.state is State.RETRY_WAIT:
            if job.next_try_at and self.clock() < job.next_try_at:
                return State.RETRY_WAIT
            self.store.set_state(job_id, self._resume_state(job))
        elif job.state in (State.PUBLISHING,):
            # 앱이 게시 요청 도중 종료된 경우: 게시 여부 불명 -> 자동 재게시 금지
            return self._to_reconciliation(job_id, "게시 요청 도중 중단되어 결과를 알 수 없습니다.")
        elif job.state in (State.UPLOADING, State.CREATING_CONTAINER, State.PROCESSING, State.READY_TO_PUBLISH):
            pass
        else:
            return job.state
        try:
            return self._advance(job_id)
        except ApiError as e:
            return self._on_api_error(job_id, e)
        except TransportTimeout:
            return self._to_retry(job_id, "응답 시간 초과")
        except TransportNetworkError:
            return self._to_retry(job_id, "네트워크 연결 실패")

    def _resume_state(self, job: JobRow) -> State:
        if job.container_id:
            return State.PROCESSING
        return State.UPLOADING

    def _advance(self, job_id: int) -> State:
        while True:
            job = self.store.get(job_id)
            spec = self._spec(job)
            s = job.state
            if s is State.UPLOADING:
                self._urls[job_id] = [self.host.upload(a) for a in spec.assets if a.role != "cover"]
                self.store.attempt(job_id, "upload", "ok", detail=f"{len(self._urls[job_id])}개")
                self.store.set_state(job_id, State.CREATING_CONTAINER)
            elif s is State.CREATING_CONTAINER:
                self.store.attempt(job_id, "create_container", "intent")
                try:
                    cid = self._create_container(job, spec)
                except TransportTimeout:
                    # 컨테이너는 게시 전까지 노출되지 않으므로 재생성이 안전하다. 알 수 없는 생성 결과는 이력에 남긴다.
                    self.store.attempt(
                        job_id, "create_container", "unknown", detail="응답 유실: 컨테이너 생성 여부 불명"
                    )
                    raise
                self.store.attempt(job_id, "create_container", "ok", detail=cid)
                self.store.set_state(job_id, State.PROCESSING, container_id=cid)
            elif s is State.PROCESSING:
                r = self._poll(job)
                if r == "EXPIRED":
                    continue  # 만료된 컨테이너: 상태는 이미 CREATING_CONTAINER 로 옮겨 재생성한다
                if r == "PUBLISHED":
                    return self._to_reconciliation(job_id, "컨테이너가 이미 게시된 상태입니다. 게시물을 확인하세요.")
                if r != "FINISHED":
                    return self.store.get(job_id).state
                self.store.set_state(job_id, State.READY_TO_PUBLISH)
            elif s is State.READY_TO_PUBLISH:
                wait = self._quota_wait()
                if wait:
                    self.store.attempt(job_id, "quota", "wait", detail=wait)
                    return self._to_retry(job_id, wait, delay=3600, from_state=State.READY_TO_PUBLISH)
                # 의도 저장 -> 게시 호출 -> 결과 저장
                self.store.set_state(job_id, State.PUBLISHING, intent=f"publish:{job.container_id}")
                self.store.attempt(job_id, "publish", "intent", detail=job.container_id or "")
                try:
                    mid = self.t.publish(self.account.id, job.container_id)
                except (TransportTimeout, TransportNetworkError) as e:
                    self.store.attempt(job_id, "publish", "unknown", detail=mask(e))
                    return self._to_reconciliation(
                        job_id, "게시 요청의 결과를 알 수 없습니다. 자동 재게시하지 않습니다."
                    )
                self.store.set_state(job_id, State.PUBLISHED, media_id=mid)  # 게시물 ID 를 먼저 영속 저장
                self.store.attempt(job_id, "publish", "ok", detail=mid)
                self._cleanup(job_id)
                self._post_publish_comments(job_id)
                return State.PUBLISHED
            else:
                return s

    def _create_container(self, job: JobRow, spec: JobSpec) -> str:
        urls = self._urls.get(job.id)
        if urls is None:
            urls = self._urls[job.id] = [self.host.upload(a) for a in spec.assets if a.role != "cover"]
        media = [a for a in spec.assets if a.role != "cover"]
        if spec.kind is Kind.REELS:
            p = {"media_type": "REELS", "video_url": urls[0], "caption": spec.caption}
            return self.t.create_container(self.account.id, p)
        if spec.kind is Kind.STORY:
            key = "video_url" if media[0].role == "video" else "image_url"
            return self.t.create_container(self.account.id, {"media_type": "STORIES", key: urls[0]})
        if len(media) == 1:
            if media[0].role == "image":
                return self.t.create_container(self.account.id, {"image_url": urls[0], "caption": spec.caption})
            vt = spec.settings.get("video_media_type")
            if not vt:
                raise ApiError(0, message="단일 피드 영상의 게시 유형이 확인되지 않았습니다.", kind="param")
            return self.t.create_container(
                self.account.id, {"media_type": vt, "video_url": urls[0], "caption": spec.caption}
            )
        children = []
        for a, u in zip(media, urls):
            key = "video_url" if a.role == "video" else "image_url"
            p = {key: u, "is_carousel_item": "true"}
            if a.role == "video":
                p["media_type"] = "VIDEO"
            children.append(self.t.create_container(self.account.id, p))
        self.store.attempt(job.id, "carousel_children", "ok", detail=",".join(children))
        return self.t.create_container(
            self.account.id, {"media_type": "CAROUSEL", "children": ",".join(children), "caption": spec.caption}
        )

    def _poll(self, job: JobRow) -> str:
        waited = 0
        while True:
            st = self.t.container_status(job.container_id).get("status_code", "")
            if st == "FINISHED":
                return st
            if st == "ERROR":
                self.store.set_state(job.id, State.FAILED, error="Meta 가 컨테이너 처리에 실패했습니다 (ERROR).")
                return st
            if st == "EXPIRED":
                self.store.attempt(job.id, "container", "expired")
                self.store.set_state(job.id, State.CREATING_CONTAINER, container_id=None)  # 만료: 재생성
                self._urls.pop(job.id, None)
                return st
            if st == "PUBLISHED":
                return st
            if waited >= POLL_MAX_SECONDS:
                self.store.attempt(job.id, "poll", "timeout")
                self._to_retry(job.id, "처리 시간 초과: 복구 가능한 대기로 저장", delay=POLL_INTERVAL * 2)
                return "TIMEOUT"
            self.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL

    def _quota_wait(self) -> str:
        try:
            q = self.t.publishing_limit(self.account.id)
        except (ApiError, TransportTimeout, TransportNetworkError):
            return ""  # 조회 불가: 막지 않는다 (한도를 추측하지 않는다)
        used, total = q.get("quota_usage"), q.get("quota_total")
        if used is not None and total is not None and used >= total:
            return f"게시 한도({used}/{total})에 도달했습니다."
        return ""

    # ====================================================================== 오류 처리
    def _to_retry(self, job_id: int, why: str, delay: int | None = None, from_state: State | None = None) -> State:
        job = self.store.get(job_id)
        tries = job.tries + 1
        if delay is None:
            if tries > len(RETRY_DELAYS):
                self.store.set_state(job_id, State.FAILED, error=f"재시도 한도 초과: {why}", tries=tries)
                return State.FAILED
            delay = RETRY_DELAYS[tries - 1]
        self.store.set_state(job_id, State.RETRY_WAIT, error=why, tries=tries, next_try_at=self.clock() + delay)
        self.store.attempt(job_id, "retry", "wait", detail=f"{why} ({delay}s)")
        return State.RETRY_WAIT

    def _to_reconciliation(self, job_id: int, why: str) -> State:
        job = self.store.get(job_id)
        if job.state is not State.RECONCILIATION_REQUIRED:
            self.store.set_state(job_id, State.RECONCILIATION_REQUIRED, error=why)
        return State.RECONCILIATION_REQUIRED

    def _on_api_error(self, job_id: int, e: ApiError) -> State:
        self.store.attempt(job_id, "api_error", e.kind, detail=mask(e))
        if e.kind == "auth":
            self.store.set_state(job_id, State.AUTH_REQUIRED, error="인증/권한 오류: 다시 연결이 필요합니다.")
            return State.AUTH_REQUIRED
        if e.kind in ("rate", "server"):
            return self._to_retry(
                job_id,
                f"{'호출 제한' if e.kind == 'rate' else '서버 오류'}: {mask(e.message)}",
                delay=None if e.kind == "server" else 600,
            )
        if e.kind == "expired":
            self.store.set_state(job_id, State.CREATING_CONTAINER, container_id=None)
            return State.CREATING_CONTAINER
        self.store.set_state(
            job_id, State.FAILED, error=f"규격/파라미터 오류: 자동 재시도하지 않습니다. {mask(e.message)}"
        )
        return State.FAILED

    # ====================================================================== 결과 불명 확인
    def reconcile(self, job_id: int) -> dict:
        """컨테이너 상태와 최근 미디어 후보를 조회한다. 컨테이너가 PUBLISHED 면 게시 확정, 아니면 후보만 제시한다
        (캡션/시각만으로 일치를 확정하지 않는다)."""
        job = self.store.get(job_id)
        if job.state is not State.RECONCILIATION_REQUIRED:
            raise ValueError("대조가 필요한 상태가 아닙니다.")
        status = ""
        if job.container_id:
            try:
                status = self.t.container_status(job.container_id).get("status_code", "")
            except (ApiError, TransportTimeout, TransportNetworkError):
                status = "UNKNOWN"
        cands = []
        try:
            cands = self.t.recent_media(self.account.id)
        except (ApiError, TransportTimeout, TransportNetworkError):
            pass
        if status == "PUBLISHED":
            mid = next((m["id"] for m in cands if m.get("container") == job.container_id), None)
            if mid:
                self.resolve(job_id, mid)
                return {"status": "PUBLISHED", "media_id": mid, "candidates": cands}
            return {"status": "PUBLISHED", "media_id": None, "candidates": cands, "needs_user": True}
        return {"status": status or "UNKNOWN", "media_id": None, "candidates": cands, "needs_user": True}

    def resolve(self, job_id: int, media_id: str | None) -> State:
        """사용자 확인 결과. media_id 가 있으면 게시된 것으로 확정, None 이면 게시되지 않았음을 확인(실패 처리 후 새 승인으로 재시도)."""
        if media_id:
            self.store.set_state(job_id, State.PUBLISHED, media_id=media_id, error=None)
            self._cleanup(job_id)
            self._post_publish_comments(job_id)
            return State.PUBLISHED
        self.store.set_state(
            job_id, State.FAILED, error="사용자가 게시되지 않았음을 확인했습니다. 재시도하려면 새 승인이 필요합니다."
        )
        return State.FAILED

    def recover_after_restart(self) -> list[int]:
        """앱 시작 시: 게시 요청 도중이던 작업은 대조 필요로 옮긴다 (저장된 ID/상태부터 이어간다)."""
        moved = []
        for j in self.store.jobs(states={State.PUBLISHING}):
            self._to_reconciliation(j.id, "앱이 게시 요청 도중 종료되어 결과를 알 수 없습니다.")
            moved.append(j.id)
        return moved

    # ====================================================================== 댓글 (게시 성공과 분리)
    def _post_publish_comments(self, job_id: int) -> None:
        job = self.store.get(job_id)
        spec = self._spec(job)
        if not spec.first_comment:
            return
        h = hashlib.sha256(spec.first_comment.encode("utf-8")).hexdigest()
        if not any(c["body_hash"] == h for c in self.store.comment_jobs(job_id)):
            self.store.add_comment_job(job_id, h, spec.first_comment)
        self.retry_comments(job_id)

    def retry_comments(self, job_id: int) -> list[dict]:
        """댓글만 재시도. 응답 유실 가능성을 고려해 기존 댓글을 먼저 확인하고, 확인 못 하면 자동 등록을 중단한다."""
        job = self.store.get(job_id)
        if not job.media_id:
            raise ValueError("게시물 ID 가 없습니다.")
        for cj in self.store.comment_jobs(job_id):
            if cj["state"] == "DONE":
                continue
            try:
                existing = self.t.list_comments(job.media_id)
            except (ApiError, TransportTimeout, TransportNetworkError) as e:
                self.store.update_comment_job(
                    cj["id"], "BLOCKED", error=f"기존 댓글 확인 실패: {mask(e)}", tries=cj["tries"] + 1
                )
                continue
            dup = next((c for c in existing if c.get("text") == cj["body"]), None)
            if dup:
                self.store.update_comment_job(cj["id"], "DONE", comment_id=dup["id"])
                continue
            try:
                kid = self.t.create_comment(job.media_id, cj["body"])
                self.store.update_comment_job(cj["id"], "DONE", comment_id=kid, tries=cj["tries"] + 1)
            except ApiError as e:
                self.store.update_comment_job(cj["id"], "FAILED", error=mask(e), tries=cj["tries"] + 1)
            except (TransportTimeout, TransportNetworkError) as e:
                # 응답 유실: 실제로는 등록됐을 수 있으므로 다음 재시도 때 기존 댓글을 먼저 확인한다
                self.store.update_comment_job(cj["id"], "UNKNOWN", error=mask(e), tries=cj["tries"] + 1)
        return self.store.comment_jobs(job_id)

    def comment_problems(self, job_id: int) -> list[str]:
        return [
            f"댓글 미완료({c['state']}): {c['error'] or ''}"
            for c in self.store.comment_jobs(job_id)
            if c["state"] != "DONE"
        ]

    # ====================================================================== 정리/취소
    def _cleanup(self, job_id: int) -> None:
        """게시 확인 후에만 임시 업로드를 정리한다. 실패는 별도로 남긴다 (PC 원본은 건드리지 않는다)."""
        for u in self._urls.pop(job_id, []):
            try:
                self.host.delete(u)
            except Exception as e:  # noqa: BLE001
                self.store.attempt(job_id, "cleanup", "failed", detail=mask(e))

    def cancel(self, job_id: int) -> State:
        """취소는 후속 작업 중지이며 이미 전송된 게시 요청의 취소를 보장하지 않는다."""
        job = self.store.get(job_id)
        if job.state in (State.PUBLISHED, State.CANCELLED):
            return job.state
        if job.state in (State.PUBLISHING, State.RECONCILIATION_REQUIRED):
            self.store.attempt(job_id, "cancel", "ignored", detail="이미 전송된 게시 요청은 취소를 보장하지 않습니다")
            return job.state
        self.store.set_state(job_id, State.CANCELLED)
        return State.CANCELLED

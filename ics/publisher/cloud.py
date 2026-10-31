"""클라우드 예약: 승인된 작업을 중계(Cloudflare Worker)에 맡기고, 상태를 동기화한다.

원칙: 중계에 맡기는 순간의 내용은 승인 스냅샷과 같아야 하고(재검사), 맡긴 뒤 내용이 바뀌면 원격 작업을 먼저 취소한다.
이미 게시 요청이 나간 뒤에는 취소를 보장하지 않으므로 수정/취소를 막고 이유를 알린다."""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from .engine import PublishEngine, mask
from .models import Kind, State
from .relay import RelayError

MIN_LEAD = 120  # 예약 시각은 지금부터 최소 2분 뒤(업로드/등록 시간 확보)


class CloudCancelRefused(Exception):
    """원격 작업이 이미 진행 중이라 취소를 보장할 수 없음."""


class CloudScheduler:
    def __init__(self, engine: PublishEngine, relay, api_version: str, token_provider: Callable[[], str]):
        if not api_version:
            raise ValueError("고정된 API 버전이 필요합니다.")
        self.engine, self.relay, self.api_version, self._token = engine, relay, api_version, token_provider
        engine.cloud = self  # 수정 시 원격 취소 훅

    # ------------------------------------------------------------ 식별
    @staticmethod
    def remote_id(job) -> str:
        return f"ics{job.id}-{(job.approval_hash or '')[:12]}"

    def is_cloud(self, job_id: int) -> bool:
        return any(a[0] == "cloud_submit" and a[1] == "ok" for a in self.engine.store.attempts(job_id))

    # ------------------------------------------------------------ 맡기기
    def _payload(self, job, spec, keys: list[dict]) -> dict:
        feature = self.engine._feature_for(spec)  # noqa: SLF001
        kind = {"reels": "reels", "story": "story", "carousel": "carousel", "feed_image": "feed_image"}.get(
            feature, "feed_video"
        )
        p = {"kind": kind, "api_version": self.api_version, "media": keys}
        if spec.kind is not Kind.STORY:  # 스토리에는 캡션/댓글을 보내지 않는다
            p["caption"] = spec.caption
            if spec.first_comment:
                p["first_comment"] = spec.first_comment
        if spec.settings.get("video_media_type"):
            p["video_media_type"] = spec.settings["video_media_type"]
        return p

    def submit(self, job_id: int, min_lead: float = MIN_LEAD) -> State:
        e = self.engine
        job = e.store.get(job_id)
        if job.state is not State.SCHEDULED or job.schedule_at is None:
            raise ValueError("예약 시각이 정해진 승인 작업만 클라우드에 맡길 수 있습니다.")
        if job.schedule_at - e.clock() < min_lead:
            raise ValueError(
                "예약 시각은 지금부터 2분 이상 뒤여야 합니다."
                if min_lead >= MIN_LEAD
                else "예약 시각이 너무 가깝습니다."
            )
        reasons = e.verify_before_upload(job)  # 승인 이후 파일/계정/내용 변경 재검사
        if reasons:
            e.store.invalidate_approval(job_id, " / ".join(reasons))
            e.store.set_state(job_id, State.AWAITING_APPROVAL, error=" / ".join(reasons))
            return State.AWAITING_APPROVAL
        spec = e._spec(job)  # noqa: SLF001
        why = e._precheck(spec)  # noqa: SLF001
        if why:
            e.store.set_state(job_id, State.AWAITING_APPROVAL, error=why)
            return State.AWAITING_APPROVAL
        rid = self.remote_id(job)
        try:
            keys = []
            for a in sorted((x for x in spec.assets if x.role != "cover"), key=lambda x: x.order):
                ext = os.path.splitext(a.path)[1].lower()
                key = f"{rid}-{a.order}-{a.sha256[:10]}{ext}"
                self.relay.upload_file(key, a.path)
                keys.append({"key": key, "role": a.role, "size": a.size})
            self.relay.put_token(e.account.id, self._token(), e.account.username)
            self.relay.create_job(
                rid, e.account.id, job.approval_hash or "", int(job.schedule_at * 1000), self._payload(job, spec, keys)
            )
        except RelayError as ex:
            e.store.attempt(job_id, "cloud_submit", "failed", detail=mask(ex.message))
            raise
        e.store.attempt(job_id, "cloud_submit", "ok", detail=rid)
        e.store.set_state(job_id, State.CLOUD_SCHEDULED, error=None)
        return State.CLOUD_SCHEDULED

    # ------------------------------------------------------------ 동기화
    _MAP = {
        "PUBLISHED": State.PUBLISHED,
        "FAILED": State.FAILED,
        "MISSED": State.MISSED,
        "AUTH_REQUIRED": State.AUTH_REQUIRED,
        "RECONCILIATION_REQUIRED": State.RECONCILIATION_REQUIRED,
        "CANCELLED": State.CANCELLED,
    }

    def sync(self) -> dict[int, State]:
        """중계의 상태를 가져와 로컬에 반영한다. 연결 실패 시 로컬 상태를 바꾸지 않는다(추측 금지)."""
        out: dict[int, State] = {}
        for job in self.engine.store.jobs(states={State.CLOUD_SCHEDULED}):
            try:
                r = self.relay.get_job(self.remote_id(job))
            except RelayError as ex:
                self.engine.store.attempt(job.id, "cloud_sync", "unreachable", detail=mask(ex.message))
                out[job.id] = job.state
                continue
            st, new = r.get("state", ""), self._MAP.get(r.get("state", ""))
            if new is State.PUBLISHED:
                self.engine.store.set_state(job.id, new, media_id=r.get("media_id"), error=None)
            elif new is not None:
                self.engine.store.set_state(job.id, new, error=r.get("error"))
            else:
                msg = f"중계 진행 중: {st}"
                if job.error != msg:
                    self.engine.store.set_state(job.id, State.CLOUD_SCHEDULED, error=msg)
            out[job.id] = self.engine.store.get(job.id).state
        return out

    # ------------------------------------------------------------ 취소/수정/해소
    def cancel_remote(self, job_id: int) -> None:
        job = self.engine.store.get(job_id)
        try:
            r = self.relay.cancel(self.remote_id(job))
        except RelayError as ex:
            if ex.status == 409:
                raise CloudCancelRefused(
                    "이미 게시 요청이 나갔거나 진행 중이라 취소를 보장할 수 없습니다. 인스타그램에서 직접 확인하세요."
                ) from None
            if ex.status == 404:
                return  # 원격에 없음
            raise
        self.engine.store.attempt(job_id, "cloud_cancel", "ok", detail=str(r.get("state", "")))

    def cancel(self, job_id: int) -> State:
        self.cancel_remote(job_id)
        self.engine.store.set_state(job_id, State.CANCELLED, error="사용자가 취소함")
        return State.CANCELLED

    def run_missed_now(self, job_id: int) -> State:
        """놓친 클라우드 예약을 사용자가 지금 게시하기로 한 경우. 승인이 유효해야 한다."""
        e = self.engine
        job = e.store.get(job_id)
        if job.state is not State.MISSED:
            raise ValueError("놓친 예약이 아닙니다.")
        reasons = e.verify_before_upload(job)
        if reasons:
            self.cancel_remote(job_id)
            e.store.invalidate_approval(job_id, " / ".join(reasons))
            e.store.set_state(job_id, State.AWAITING_APPROVAL, error=" / ".join(reasons))
            return State.AWAITING_APPROVAL
        self.relay.run_now(self.remote_id(job))
        e.store.set_state(job_id, State.CLOUD_SCHEDULED, error=None)
        return State.CLOUD_SCHEDULED

    def resolve(self, job_id: int, media_id: str | None) -> State:
        """결과 불명 해소: 사용자가 인스타그램에서 직접 확인한 결과만 반영."""
        job = self.engine.store.get(job_id)
        r = self.relay.resolve(self.remote_id(job), media_id)
        new = self._MAP.get(r.get("state", ""))
        if new is State.PUBLISHED:
            self.engine.store.set_state(job_id, new, media_id=r.get("media_id") or media_id, error=None)
        elif new is not None:
            self.engine.store.set_state(job_id, new, error=r.get("error"))
        return self.engine.store.get(job_id).state

    def time_left(self, job_id: int) -> float | None:
        j = self.engine.store.get(job_id)
        return None if j.schedule_at is None else j.schedule_at - time.time()

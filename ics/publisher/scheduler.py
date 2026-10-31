"""로컬 예약 실행기. 예약은 Instagram API 가 지원하지 않으므로 앱(또는 클라우드 중계)이 시간이 되면 게시 API 를 호출한다.

PC 앱이 꺼져 있다가 늦게 켜진 경우: 예약 시각에서 MISSED_GRACE 초가 넘었으면 자동 게시하지 않고 MISSED 로 두어 사용자가 확인한다."""

from __future__ import annotations

from .engine import MISSED_GRACE, PublishEngine
from .models import State


class LocalScheduler:
    def __init__(self, engine: PublishEngine, grace: int = MISSED_GRACE):
        self.engine, self.grace = engine, grace

    def tick(self) -> dict[int, State]:
        """한 번 호출: 시간이 된 예약 실행, 대기 중인 재시도 재개. 결과 상태를 돌려준다."""
        e, now = self.engine, self.engine.clock()
        out: dict[int, State] = {}
        for j in e.store.jobs(states={State.SCHEDULED}):
            if j.schedule_at is None or now < j.schedule_at:
                continue
            if now - j.schedule_at > self.grace:
                e.store.set_state(
                    j.id,
                    State.MISSED,
                    error="예약 시각이 지나서 자동 게시하지 않았습니다. 확인 후 지금 게시하거나 취소하세요.",
                )
                out[j.id] = State.MISSED
                continue
            out[j.id] = e.run_job(j.id)
        for j in e.store.jobs(states={State.RETRY_WAIT}):
            if j.next_try_at is None or now >= j.next_try_at:
                out[j.id] = e.run_job(j.id)
        return out

    def publish_missed_now(self, job_id: int) -> State:
        """사용자가 '지금 게시'를 명시적으로 선택한 경우에만. 승인이 유효해야 하며 아니면 재승인으로 돌아간다."""
        e = self.engine
        j = e.store.get(job_id)
        if j.state is not State.MISSED:
            raise ValueError("놓친 예약이 아닙니다.")
        reasons = e.verify_before_upload(j)
        if reasons:  # 승인 이후 바뀐 것이 있으면 재승인이 필요하다
            e.store.invalidate_approval(job_id, " / ".join(reasons))
            e.store.set_state(job_id, State.AWAITING_APPROVAL, error=" / ".join(reasons))
            return State.AWAITING_APPROVAL
        e.store.set_state(job_id, State.UPLOADING)
        return e.run_job(job_id)

    def upcoming(self) -> list[tuple[int, float, str]]:
        return [
            (j.id, j.schedule_at, j.kind) for j in self.engine.store.jobs(states={State.SCHEDULED}) if j.schedule_at
        ]

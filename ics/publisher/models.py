"""Publisher 데이터 모델과 작업 상태기계."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Kind(str, Enum):
    STORY = "story"
    REELS = "reels"
    FEED = "feed"


ALL_KINDS = (Kind.STORY, Kind.REELS, Kind.FEED)


class State(str, Enum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    SCHEDULED = "SCHEDULED"  # 승인 후 예약 시각을 기다리는 상태 (실행 전)
    UPLOADING = "UPLOADING"
    CREATING_CONTAINER = "CREATING_CONTAINER"
    PROCESSING = "PROCESSING"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    RETRY_WAIT = "RETRY_WAIT"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    MISSED = "MISSED"  # 예약 시각이 지났는데 실행되지 못함(앱/PC 꺼짐 등): 사용자 확인 전 게시하지 않는다
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {State.PUBLISHED, State.FAILED, State.CANCELLED}

_T = {
    State.DRAFT: {State.VALIDATING, State.CANCELLED},
    State.VALIDATING: {State.AWAITING_APPROVAL, State.FAILED, State.MANUAL_REQUIRED, State.CANCELLED},
    State.AWAITING_APPROVAL: {State.APPROVED, State.VALIDATING, State.CANCELLED},
    State.APPROVED: {State.SCHEDULED, State.UPLOADING, State.AWAITING_APPROVAL, State.CANCELLED},
    State.SCHEDULED: {State.UPLOADING, State.MISSED, State.AWAITING_APPROVAL, State.CANCELLED},
    State.UPLOADING: {State.CREATING_CONTAINER, State.RETRY_WAIT, State.AUTH_REQUIRED, State.FAILED, State.CANCELLED},
    State.CREATING_CONTAINER: {
        State.PROCESSING,
        State.RETRY_WAIT,
        State.AUTH_REQUIRED,
        State.FAILED,
        State.CANCELLED,
    },
    State.PROCESSING: {
        State.READY_TO_PUBLISH,
        State.RETRY_WAIT,
        State.CREATING_CONTAINER,  # 컨테이너 만료 시 재생성
        State.AUTH_REQUIRED,
        State.FAILED,
        State.CANCELLED,
    },
    State.READY_TO_PUBLISH: {State.PUBLISHING, State.CREATING_CONTAINER, State.RETRY_WAIT, State.CANCELLED},
    State.PUBLISHING: {
        State.PUBLISHED,
        State.RECONCILIATION_REQUIRED,
        State.AUTH_REQUIRED,
        State.FAILED,
        State.RETRY_WAIT,
    },
    State.RETRY_WAIT: {
        State.UPLOADING,
        State.CREATING_CONTAINER,
        State.PROCESSING,
        State.READY_TO_PUBLISH,
        State.CANCELLED,
    },
    State.AUTH_REQUIRED: {State.AWAITING_APPROVAL, State.CANCELLED},
    State.RECONCILIATION_REQUIRED: {State.PUBLISHED, State.FAILED, State.CANCELLED},
    State.MISSED: {State.AWAITING_APPROVAL, State.UPLOADING, State.CANCELLED},
    State.MANUAL_REQUIRED: {State.AWAITING_APPROVAL, State.CANCELLED},
    State.PUBLISHED: set(),
    State.FAILED: {State.AWAITING_APPROVAL, State.CANCELLED},
    State.CANCELLED: set(),
}


class IllegalTransition(Exception):
    pass


def check_transition(a: State, b: State) -> None:
    if b not in _T[a]:
        raise IllegalTransition(f"{a.value} -> {b.value} 는 허용되지 않습니다.")


@dataclass
class MediaAsset:
    path: str
    role: str  # 'video' | 'image' | 'cover'
    sha256: str = ""
    size: int = 0
    order: int = 0

    @staticmethod
    def from_file(path: str | Path, role: str, order: int = 0) -> "MediaAsset":
        p = Path(path)
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return MediaAsset(str(p), role, h.hexdigest(), p.stat().st_size, order)


@dataclass
class JobSpec:
    """게시 한 건(스토리 1, 릴스 1, 피드 1)의 내용. 승인 스냅샷의 입력."""

    kind: Kind
    assets: list[MediaAsset]
    caption: str = ""
    first_comment: str = ""  # 첫 댓글 (선택)
    credits: str = ""
    settings: dict = field(default_factory=dict)  # 커버/공유 설정 등 (지원이 확인된 필드만 사용)
    project_id: str = ""
    output_version: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "assets": [{"role": a.role, "sha256": a.sha256, "size": a.size, "order": a.order} for a in self.assets],
            "caption": self.caption,
            "first_comment": self.first_comment,
            "credits": self.credits,
            "settings": self.settings,
            "project_id": self.project_id,
            "output_version": self.output_version,
        }


def snapshot_hash(account_id: str, username: str, spec: JobSpec, schedule_at: float | None = None) -> str:
    """승인 스냅샷 해시. 계정/유형/순서 포함 파일 해시/최종 텍스트/설정/프로젝트 출력 버전/예약 시각이 바뀌면 달라진다."""
    doc = {"account": account_id, "username": username, "spec": spec.to_dict(), "schedule_at": schedule_at}
    return hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def idempotency_key(account_id: str, spec: JobSpec) -> str:
    """같은 계정에 같은 내용(유형/순서 포함 파일 해시/텍스트/설정)은 같은 키. 예약 시각은 포함하지 않는다."""
    return snapshot_hash(account_id, "", spec, None)


def diff_specs(old: dict, new: dict) -> list[str]:
    """승인 당시 스냅샷과 현재 내용의 변경 사유 (사람이 읽는 한국어)."""
    out = []
    if old.get("kind") != new.get("kind"):
        out.append("게시 유형이 바뀌었습니다.")
    oa = [(a["order"], a["sha256"]) for a in old.get("assets", [])]
    na = [(a["order"], a["sha256"]) for a in new.get("assets", [])]
    if sorted(a for _, a in oa) != sorted(a for _, a in na):
        out.append("미디어 파일 내용이 바뀌었습니다.")
    elif oa != na:
        out.append("미디어 순서가 바뀌었습니다.")
    for key, label in (("caption", "캡션이"), ("first_comment", "첫 댓글이"), ("credits", "크레딧이")):
        if old.get(key) != new.get(key):
            out.append(f"{label} 바뀌었습니다.")
    if old.get("settings") != new.get("settings"):
        out.append("설정이 바뀌었습니다.")
    if old.get("output_version") != new.get("output_version"):
        out.append("프로젝트 출력 버전이 바뀌었습니다.")
    return out

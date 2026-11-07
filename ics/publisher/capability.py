"""기능 프로필: 공식 문서와 실제 테스트로 확인된 기능만 켠다. 확인되지 않으면 UNKNOWN 이며 비활성."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Support(str, Enum):
    UNKNOWN = "UNKNOWN"
    VERIFIED = "VERIFIED"
    UNSUPPORTED = "UNSUPPORTED"


FEATURES = ("reels", "story", "feed_image", "feed_video", "carousel", "comments", "scheduling", "pkce")

# 1단계 검증 보고서 (docs/PUBLISHER_VERIFICATION.md) 의 초기값. 모든 게시 기능은 실제 성공 전까지 UNKNOWN.
INITIAL = {
    "reels": Support.UNKNOWN,
    "story": Support.UNKNOWN,
    "feed_image": Support.UNKNOWN,
    "feed_video": Support.UNKNOWN,
    "carousel": Support.UNSUPPORTED,  # 문서끼리 한도가 충돌(10 vs 20) -> 실제 확인 전 자동 게시 비활성
    "comments": Support.UNKNOWN,
    "scheduling": Support.UNKNOWN,  # API 자체 예약은 문서에 없음. 자체 구현(Worker)은 배포/실테스트 후
    "pkce": Support.UNKNOWN,
}


@dataclass
class Evidence:
    source: str  # 공식 URL 또는 'live-test'
    note: str = ""
    verified_at: float = field(default_factory=time.time)


@dataclass
class CapabilityProfile:
    account_id: str = ""
    api_version: str = ""
    permissions: tuple[str, ...] = ()
    states: dict[str, Support] = field(default_factory=lambda: dict(INITIAL))
    carousel_limit: int | None = None  # 검증된 값만. None 이면 캐러셀 자동 게시 불가
    evidence: dict[str, list[Evidence]] = field(default_factory=dict)
    disabled_by_operator: set[str] = field(default_factory=set)  # 운영 중 기능별 비활성화

    def state(self, feature: str) -> Support:
        return self.states.get(feature, Support.UNKNOWN)

    def enabled(self, feature: str) -> bool:
        if feature in self.disabled_by_operator:
            return False
        if feature == "carousel" and not self.carousel_limit:
            return False
        return self.state(feature) is Support.VERIFIED

    def reason_disabled(self, feature: str) -> str:
        if feature in self.disabled_by_operator:
            return "운영자가 비활성화했습니다."
        if feature == "carousel" and self.state(feature) is Support.VERIFIED and not self.carousel_limit:
            return "검증된 캐러셀 한도가 없습니다."
        s = self.state(feature)
        if s is Support.VERIFIED:
            return ""
        if s is Support.UNSUPPORTED:
            return "공식 지원이 확인되지 않아(또는 문서가 충돌해) 비활성입니다. 수동 내보내기를 사용하세요."
        return "아직 공식 지원/실제 테스트가 확인되지 않았습니다 (UNKNOWN)."

    def mark_verified(self, feature: str, source: str, note: str = "", carousel_limit: int | None = None) -> None:
        self.states[feature] = Support.VERIFIED
        if feature == "carousel":
            self.carousel_limit = carousel_limit
        self.evidence.setdefault(feature, []).append(Evidence(source, note))

    def mark_unsupported(self, feature: str, source: str, note: str = "") -> None:
        self.states[feature] = Support.UNSUPPORTED
        self.evidence.setdefault(feature, []).append(Evidence(source, note))

    def reverify_if_changed(self, account_id: str, api_version: str, permissions: tuple[str, ...]) -> list[str]:
        """계정/API 버전/권한이 달라지면 모든 VERIFIED 를 UNKNOWN 으로 되돌린다 (재검증 필요). 바뀐 항목 이름을 반환."""
        changed = []
        if account_id != self.account_id:
            changed.append("account")
        if api_version != self.api_version:
            changed.append("api_version")
        if set(permissions) != set(self.permissions):
            changed.append("permissions")
        if changed:
            for f, s in list(self.states.items()):
                if s is Support.VERIFIED:
                    self.states[f] = Support.UNKNOWN
            self.carousel_limit = None
            self.account_id, self.api_version, self.permissions = account_id, api_version, tuple(permissions)
        return changed

    # ---- 저장
    def to_json(self) -> str:
        return json.dumps(
            {
                "account_id": self.account_id,
                "api_version": self.api_version,
                "permissions": list(self.permissions),
                "states": {k: v.value for k, v in self.states.items()},
                "carousel_limit": self.carousel_limit,
                "evidence": {k: [e.__dict__ for e in v] for k, v in self.evidence.items()},
                "disabled": sorted(self.disabled_by_operator),
            },
            ensure_ascii=False,
            indent=1,
        )

    @classmethod
    def from_json(cls, text: str) -> "CapabilityProfile":
        d = json.loads(text)
        p = cls(account_id=d.get("account_id", ""), api_version=d.get("api_version", ""))
        p.permissions = tuple(d.get("permissions", []))
        for k, v in d.get("states", {}).items():
            if k in FEATURES:
                try:
                    p.states[k] = Support(v)
                except ValueError:
                    p.states[k] = Support.UNKNOWN
        p.carousel_limit = d.get("carousel_limit")
        p.evidence = {k: [Evidence(**e) for e in v] for k, v in d.get("evidence", {}).items()}
        p.disabled_by_operator = set(d.get("disabled", []))
        return p

    def save(self, path: Path) -> None:
        path.write_text(self.to_json(), "utf-8")

    @classmethod
    def load(cls, path: Path) -> "CapabilityProfile":
        try:
            return cls.from_json(path.read_text("utf-8"))
        except (OSError, ValueError):
            return cls()

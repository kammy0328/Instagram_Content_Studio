"""전송 계층. 기본은 모의(MockTransport). 실제 호출(GraphTransport)은 live 모드를 명시 승인해야만 만들 수 있다.

실제 엔드포인트/필드는 1단계 검증 보고서의 확인된 범위(컨테이너 생성, 상태 조회, media_publish, 한도 조회)만 쓴다."""

from __future__ import annotations

import itertools
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

GRAPH_HOST = "https://graph.instagram.com"


class TransportError(Exception):
    """분류 가능한 전송 오류의 부모."""


class TransportTimeout(TransportError):
    """요청은 나갔을 수 있으나 응답을 받지 못함 (결과 불명)."""


class TransportNetworkError(TransportError):
    """연결 자체가 실패 (요청이 서버에 닿지 않았다고 볼 수 있음)."""


@dataclass
class ApiError(TransportError):
    http: int
    code: int = 0
    subcode: int = 0
    message: str = ""
    kind: str = "param"  # param | auth | rate | server | expired

    def __str__(self) -> str:
        return f"HTTP {self.http} code={self.code}/{self.subcode} {self.message}"


class Transport(Protocol):
    def create_container(self, ig_id: str, params: dict) -> str: ...
    def container_status(self, container_id: str) -> dict: ...  # {'status_code': 'FINISHED', 'status': '...'}
    def publish(self, ig_id: str, container_id: str) -> str: ...  # media id
    def publishing_limit(self, ig_id: str) -> dict: ...  # {'quota_usage': n, 'quota_total': m}
    def recent_media(self, ig_id: str, limit: int = 10) -> list[dict]: ...
    def list_comments(self, media_id: str) -> list[dict]: ...
    def create_comment(self, media_id: str, text: str) -> str: ...


def classify_http(http: int, code: int, message: str) -> str:
    if http in (401, 403) or code in (190, 102, 10, 200):
        return "auth"
    if http == 429 or code in (4, 17, 32, 613):
        return "rate"
    if http >= 500:
        return "server"
    return "param"


# ============================================================ 모의 전송
@dataclass
class MockTransport:
    """실제 호출 없이 생성/처리/게시/댓글/타임아웃/만료/권한 오류/결과 불명을 재현한다."""

    process_polls: int = 1  # FINISHED 가 되기 전 IN_PROGRESS 로 응답하는 횟수
    faults: dict = field(default_factory=dict)  # 메서드명 -> 예외 리스트(앞에서부터 한 번씩 소비)
    lose_publish_response: bool = False  # 게시는 서버에 반영되지만 응답을 못 받는 상황
    calls: list = field(default_factory=list)
    media: dict = field(default_factory=dict)  # media_id -> {...}
    containers: dict = field(default_factory=dict)
    comments: dict = field(default_factory=dict)
    quota_total: int = 100
    is_simulation: bool = True

    def __post_init__(self):
        self._ids = itertools.count(1000)

    def _fault(self, name: str):
        q = self.faults.get(name)
        if q:
            exc = q.pop(0)
            if exc is not None:
                raise exc

    def create_container(self, ig_id, params):
        self.calls.append(("create_container", ig_id, dict(params)))
        self._fault("create_container")
        cid = f"C{next(self._ids)}"
        self.containers[cid] = {"params": dict(params), "polls": 0, "published": False, "forced": None}
        return cid

    def container_status(self, container_id):
        self.calls.append(("container_status", container_id))
        self._fault("container_status")
        c = self.containers[container_id]
        if c["forced"]:
            return {"status_code": c["forced"]}
        if c["published"]:
            return {"status_code": "PUBLISHED"}
        c["polls"] += 1
        return {"status_code": "IN_PROGRESS" if c["polls"] <= self.process_polls else "FINISHED"}

    def publish(self, ig_id, container_id):
        self.calls.append(("publish", ig_id, container_id))
        self._fault("publish")
        c = self.containers[container_id]
        mid = f"M{next(self._ids)}"
        c["published"] = True
        self.media[mid] = {"id": mid, "container": container_id, "caption": c["params"].get("caption", "")}
        if self.lose_publish_response:
            raise TransportTimeout("게시 응답 유실 (서버에는 반영됨)")
        return mid

    def publishing_limit(self, ig_id):
        self.calls.append(("publishing_limit", ig_id))
        self._fault("publishing_limit")
        return {"quota_usage": len(self.media), "quota_total": self.quota_total}

    def recent_media(self, ig_id, limit=10):
        self.calls.append(("recent_media", ig_id))
        return list(self.media.values())[-limit:]

    def list_comments(self, media_id):
        self.calls.append(("list_comments", media_id))
        self._fault("list_comments")
        return list(self.comments.get(media_id, []))

    def create_comment(self, media_id, text):
        self.calls.append(("create_comment", media_id))
        self._fault("create_comment")
        cid = f"K{next(self._ids)}"
        self.comments.setdefault(media_id, []).append({"id": cid, "text": text})
        return cid


# ============================================================ 실제 전송 (live 승인 필수)
class LiveNotApproved(Exception):
    pass


class GraphTransport:
    """graph.instagram.com 호출. 토큰은 생성자 인자로만 받고 로그/예외 문구에 넣지 않는다."""

    is_simulation = False

    def __init__(self, access_token: str, api_version: str, live_approved: bool, timeout: float = 30.0):
        if not live_approved:
            raise LiveNotApproved("실제 API 호출은 사용자가 명시적으로 승인한 테스트에서만 허용됩니다.")
        if not api_version:
            raise ValueError("API 버전이 고정되어야 합니다 (추측 금지).")
        self._token, self._ver, self._timeout = access_token, api_version, timeout

    def _call(self, method: str, path: str, params: dict | None = None) -> dict:
        url = f"{GRAPH_HOST}/{self._ver}/{path.lstrip('/')}"
        data = None
        headers = {"Authorization": f"Bearer {self._token}"}
        if method == "GET":
            if params:
                url += "?" + urllib.parse.urlencode(params)
        else:
            data = urllib.parse.urlencode(params or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode("utf-8")).get("error", {})
            except ValueError:
                err = {}
            code = int(err.get("code", 0) or 0)
            msg = str(err.get("message", ""))[:300]
            raise ApiError(
                e.code, code, int(err.get("error_subcode", 0) or 0), msg, classify_http(e.code, code, msg)
            ) from None
        except TimeoutError as e:
            raise TransportTimeout("응답 대기 시간 초과") from e
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), TimeoutError):
                raise TransportTimeout("응답 대기 시간 초과") from e
            raise TransportNetworkError("네트워크 연결 실패") from e

    def create_container(self, ig_id, params):
        return str(self._call("POST", f"{ig_id}/media", params)["id"])

    def container_status(self, container_id):
        return self._call("GET", container_id, {"fields": "status_code,status"})

    def publish(self, ig_id, container_id):
        return str(self._call("POST", f"{ig_id}/media_publish", {"creation_id": container_id})["id"])

    def publishing_limit(self, ig_id):
        d = self._call("GET", f"{ig_id}/content_publishing_limit", {"fields": "quota_usage,config"})
        row = (d.get("data") or [{}])[0]
        return {"quota_usage": row.get("quota_usage"), "quota_total": (row.get("config") or {}).get("quota_total")}

    def recent_media(self, ig_id, limit=10):
        return self._call("GET", f"{ig_id}/media", {"fields": "id,timestamp,media_product_type", "limit": limit}).get(
            "data", []
        )

    def list_comments(self, media_id):
        return self._call("GET", f"{media_id}/comments", {"fields": "id,text"}).get("data", [])

    def create_comment(self, media_id, text):
        return str(self._call("POST", f"{media_id}/comments", {"message": text})["id"])

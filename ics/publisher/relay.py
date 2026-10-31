"""클라우드 중계(Cloudflare Worker) 클라이언트. 표준 라이브러리(urllib)만 사용한다.

보안: 중계 주소는 https 만 허용한다(테스트용 localhost 예외). API 키/토큰은 로그와 예외 메시지에 넣지 않는다."""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

PART_SIZE = 10 * 1024 * 1024  # R2 멀티파트는 마지막 조각을 제외하고 5MiB 이상이어야 한다
SIMPLE_LIMIT = PART_SIZE


class RelayError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class Relay(Protocol):
    def upload_file(self, key: str, path: str) -> int: ...
    def put_token(self, ig_id: str, token: str, username: str = "", expires_at: int | None = None) -> None: ...
    def create_job(self, job_id: str, ig_id: str, approval_hash: str, run_at_ms: int, payload: dict) -> dict: ...
    def get_job(self, job_id: str) -> dict: ...
    def cancel(self, job_id: str) -> dict: ...
    def resolve(self, job_id: str, media_id: str | None) -> dict: ...
    def run_now(self, job_id: str) -> dict: ...


Http = Callable[[str, str, dict, "bytes | None", float], "tuple[int, bytes]"]


def _urllib_http(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (https 만 허용하도록 생성자에서 검사)
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RelayError(0, f"중계에 연결하지 못했습니다: {type(e).__name__}") from None


class RelayClient:
    def __init__(self, base_url: str, api_key: str, http: Http | None = None, timeout: float = 60.0):
        u = urllib.parse.urlparse(base_url)
        local = u.hostname in ("127.0.0.1", "localhost")
        if u.scheme != "https" and not (u.scheme == "http" and local):
            raise ValueError("중계 주소는 https 여야 합니다.")
        if not api_key:
            raise ValueError("중계 API 키가 필요합니다.")
        self.base, self._key, self._http, self.timeout = base_url.rstrip("/"), api_key, http or _urllib_http, timeout

    def _call(self, method: str, path: str, body: dict | bytes | None = None, ctype: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._key}"}
        data: bytes | None
        if isinstance(body, dict):
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        else:
            data = body
            if ctype:
                headers["Content-Type"] = ctype
        status, raw = self._http(method, self.base + path, headers, data, self.timeout)
        try:
            out = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            out = {}
        if status >= 400:
            raise RelayError(status, str(out.get("error") or out.get("note") or f"HTTP {status}")[:200])
        return out

    # ---- 업로드
    def upload_file(self, key: str, path: str) -> int:
        p = Path(path)
        size = p.stat().st_size
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        q = urllib.parse.quote(key, safe="")
        if size <= SIMPLE_LIMIT:
            self._call("PUT", f"/v1/media/{q}", p.read_bytes(), ctype)
            return size
        up = self._call("POST", "/v1/media", {"id": key, "content_type": ctype})
        parts = []
        with p.open("rb") as f:
            n = 0
            while chunk := f.read(PART_SIZE):
                n += 1
                r = self._call("PUT", f"/v1/media/{q}/parts/{n}?upload_id={urllib.parse.quote(up['upload_id'])}", chunk)
                parts.append({"part_number": r["part_number"], "etag": r["etag"]})
        self._call("POST", f"/v1/media/{q}/complete", {"upload_id": up["upload_id"], "parts": parts})
        return size

    # ---- 계정/작업
    def put_token(self, ig_id: str, token: str, username: str = "", expires_at: int | None = None) -> None:
        self._call(
            "PUT",
            f"/v1/accounts/{urllib.parse.quote(ig_id, safe='')}/token",
            {"token": token, "username": username, "expires_at": expires_at},
        )

    def delete_account(self, ig_id: str) -> None:
        self._call("DELETE", f"/v1/accounts/{urllib.parse.quote(ig_id, safe='')}/token")

    def create_job(self, job_id: str, ig_id: str, approval_hash: str, run_at_ms: int, payload: dict) -> dict:
        return self._call(
            "POST",
            "/v1/jobs",
            {"id": job_id, "ig_id": ig_id, "approval_hash": approval_hash, "run_at": run_at_ms, "payload": payload},
        )

    def get_job(self, job_id: str) -> dict:
        return self._call("GET", f"/v1/jobs/{urllib.parse.quote(job_id, safe='')}")

    def list_jobs(self) -> list[dict]:
        return self._call("GET", "/v1/jobs").get("jobs", [])

    def cancel(self, job_id: str) -> dict:
        return self._call("POST", f"/v1/jobs/{urllib.parse.quote(job_id, safe='')}/cancel", {})

    def resolve(self, job_id: str, media_id: str | None) -> dict:
        return self._call(
            "POST",
            f"/v1/jobs/{urllib.parse.quote(job_id, safe='')}/resolve",
            {"media_id": media_id} if media_id else {},
        )

    def run_now(self, job_id: str) -> dict:
        return self._call("POST", f"/v1/jobs/{urllib.parse.quote(job_id, safe='')}/run-now", {})

    def claim_oauth(self, state: str) -> str:
        return self._call("POST", "/v1/oauth/claim", {"state": state})["code"]

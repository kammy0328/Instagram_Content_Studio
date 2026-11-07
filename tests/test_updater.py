"""자동 업데이트: 비교/검증/내려받기/설치 명령 (네트워크는 가짜 opener 로 대체)."""

from __future__ import annotations

import hashlib
import io
import json

import pytest

from ics import updater


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def make_opener(meta: dict, payload: bytes, calls: list | None = None):
    rel = {
        "assets": [
            {"name": "update.json", "url": "https://api.github.com/a/1", "size": 10},
            {"name": "InstagramContentStudio_Setup.exe", "url": "https://api.github.com/a/2", "size": len(payload)},
        ]
    }

    def opener(url, token, accept, timeout=30.0):
        if calls is not None:
            calls.append((url, token))
        if url.endswith("/releases/tags/build-main"):
            return Resp(json.dumps(rel).encode())
        if url.endswith("/a/1"):
            return Resp(json.dumps(meta).encode())
        if url.endswith("/a/2"):
            return Resp(payload)
        raise updater.UpdateError("404")

    return opener


def meta_for(payload: bytes, build_id="abc1234") -> dict:
    return {
        "build_id": build_id,
        "version": "1.2.0",
        "installer": "InstagramContentStudio_Setup.exe",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "notes": "n",
    }


def test_check_finds_newer_build_and_ignores_same_or_dev():
    data = b"x" * 3000
    op = make_opener(meta_for(data), data)
    info = updater.check(current_build="old0000", opener=op)
    assert info and info.build_id == "abc1234" and info.size == 3000
    assert updater.check(current_build="abc1234", opener=op) is None
    assert updater.check(current_build="dev", opener=op) is None  # 개발 실행은 확인하지 않음


def test_token_is_passed_to_api_and_check_rejects_bad_meta():
    data = b"y"
    calls: list = []
    updater.check(token="tok", current_build="old", opener=make_opener(meta_for(data), data, calls))
    assert all(t == "tok" for _u, t in calls)
    bad = meta_for(data)
    bad["installer"] = "missing.exe"
    with pytest.raises(updater.UpdateError):
        updater.check(current_build="old", opener=make_opener(bad, data))


def test_download_verifies_sha256_and_cleans_part(tmp_path):
    data = b"z" * 5000
    info = updater.check(current_build="old", opener=make_opener(meta_for(data), data))
    seen: list[float] = []
    out = updater.download(info, "", tmp_path, seen.append, opener=make_opener(meta_for(data), data))
    assert out.read_bytes() == data and seen and seen[-1] == 1.0
    tampered = make_opener(meta_for(data), b"q" * 5000)
    with pytest.raises(updater.UpdateError):
        updater.download(info, "", tmp_path / "t2", opener=tampered)
    assert not list((tmp_path / "t2").glob("*"))  # 검증 실패 파일은 남기지 않는다


def test_installer_command_and_due(tmp_path):
    cmd = updater.installer_command(tmp_path / "s.exe", True)
    assert "/VERYSILENT" in cmd and "/LAUNCH=1" in cmd and "/CLOSEAPPLICATIONS" in cmd
    assert updater.installer_command(tmp_path / "s.exe", False) == [str(tmp_path / "s.exe")]
    assert updater.due(0, now=updater.CHECK_INTERVAL_S + 1)
    assert not updater.due(100, now=200)


def test_redirect_handler_drops_authorization():
    import urllib.request

    h = updater._DropAuthOnRedirect()
    req = urllib.request.Request("https://api.github.com/x", headers={"Authorization": "Bearer t"})
    new = h.redirect_request(req, None, 302, "Found", {}, "https://objects.example.com/y")
    assert new is not None and "Authorization" not in {k.title() for k in new.headers}

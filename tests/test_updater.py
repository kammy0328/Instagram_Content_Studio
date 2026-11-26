"""자동 업데이트: 비교/검증/내려받기/설치 명령 (네트워크는 가짜 opener 로 대체)."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

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
    assert "/VERYSILENT" in cmd and "/CLOSEAPPLICATIONS" in cmd
    assert "/LAUNCH=1" not in cmd  # 다시 실행은 실행기가 맡는다 (설치가 실패해도 열리게)
    assert updater.installer_command(tmp_path / "s.exe", False) == [
        str(tmp_path / "s.exe"),
        f"/LOG={updater.install_log_path()}",
    ]
    assert updater.due(0, now=updater.CHECK_INTERVAL_S + 1)
    assert not updater.due(100, now=200)


def test_redirect_handler_drops_authorization():
    import urllib.request

    h = updater._DropAuthOnRedirect()
    req = urllib.request.Request("https://api.github.com/x", headers={"Authorization": "Bearer t"})
    new = h.redirect_request(req, None, 302, "Found", {}, "https://objects.example.com/y")
    assert new is not None and "Authorization" not in {k.title() for k in new.headers}


def test_launcher_waits_installs_then_always_relaunches(tmp_path, monkeypatch):
    """업데이트 실행기: 앱 종료 대기 → 설치(그 프로세스만 대기) → 앱이 안 떠 있으면 다시 실행, 모든 단계 기록."""
    monkeypatch.setattr(updater, "update_dir", lambda: tmp_path / "u")
    setup = tmp_path / "it's setup.exe"  # 작은따옴표와 공백이 있어도 안전하게 전달
    app = tmp_path / "App Dir" / "InstagramContentStudio.exe"
    script = updater.launcher_script(setup, True, 4321, app, "abc1234")
    order = ["Wait-Process -Id 4321", "Start-Process -FilePath " + updater._ps_quote(setup), "WaitForExit()",
             "setup exit=", "Get-Process -Name 'InstagramContentStudio'", "Start-Process -FilePath " + updater._ps_quote(app)]  # fmt: skip
    pos = [script.index(x) for x in order]
    assert pos == sorted(pos)
    assert "-Wait " not in script  # Start-Process -Wait 는 자식(다시 연 앱)까지 기다려 멈출 수 있다
    assert "'/VERYSILENT'" in script and "/LOG=" in script and "/LAUNCH=1" not in script
    assert updater._ps_quote(updater.launcher_log_path()) in script
    cmd = updater.launcher_command(setup, True, 4321, app, "abc1234")
    assert cmd[0] == "powershell.exe" and cmd[-2] == "-File"
    f = tmp_path / "u" / "run_update.ps1"
    assert cmd[-1] == str(f) and f.read_bytes().startswith(b"\xef\xbb\xbf")  # PowerShell 5.1 용 BOM


def test_last_update_result_reports_once(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "update_dir", lambda: tmp_path)
    assert updater.last_update_result() is None
    updater.launcher_log_path().write_text("10:00 start\n10:01 setup exit=0\n10:01 relaunch ok\n", encoding="utf-8")
    assert updater.last_update_result()[0] == "ok"
    assert updater.last_update_result() is None  # 한 번만 알린다
    updater.launcher_log_path().write_text("10:00 start\n10:01 setup exit=5\n", encoding="utf-8")
    updater.install_log_path().write_text("line1\n설치 실패 이유\n", encoding="utf-8")
    state, detail = updater.last_update_result()
    assert state == "failed" and "setup exit=5" in detail and "설치 실패 이유" in detail
    updater.launcher_log_path().write_text("10:00 start\n", encoding="utf-8")
    assert updater.last_update_result() is None  # 아직 진행 중


def test_shutdown_for_update_closes_editor_windows_then_exits(monkeypatch):
    import os

    from PySide6.QtWidgets import QApplication, QMainWindow

    from ics.ui import update_ui

    app = QApplication.instance() or QApplication([])
    closed = []

    class W(QMainWindow):
        def closeEvent(self, e):  # noqa: N802
            closed.append(True)
            e.accept()

    w = W()
    w.show()
    exited = []
    monkeypatch.setattr(os, "_exit", lambda code: exited.append(code))
    update_ui.shutdown_for_update()
    assert closed == [True] and exited == [0]
    assert app is not None


def test_download_uses_a_fresh_name_even_if_an_old_setup_is_locked(tmp_path, monkeypatch):
    """예전 업데이트의 설치 파일이 아직 실행 중이라 같은 이름 파일이 잠겨 있어도 (WinError 32) 새로 받을 수 있다."""
    data = b"y" * 3000
    info = updater.check(current_build="old", opener=make_opener(meta_for(data), data))
    old = tmp_path / info.asset_name
    old.write_bytes(b"old")
    real_replace = os.replace

    def guarded(src, dst):
        if Path(dst) == old:
            raise PermissionError(32, "다른 프로세스가 파일을 사용 중")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", guarded)
    out = updater.download(info, "", tmp_path, opener=make_opener(meta_for(data), data))
    assert out != old and out.read_bytes() == data and out.suffix == old.suffix


def test_download_retries_while_antivirus_holds_the_new_file(tmp_path, monkeypatch):
    data = b"z" * 1000
    info = updater.check(current_build="old", opener=make_opener(meta_for(data), data))
    real_replace = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(32, "검사 중")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    out = updater.download(info, "", tmp_path, opener=make_opener(meta_for(data), data))
    assert out.read_bytes() == data and len(calls) == 3
    monkeypatch.setattr(os, "replace", lambda s, d: (_ for _ in ()).throw(PermissionError(32, "x")))
    with pytest.raises(updater.UpdateError, match="사용 중"):
        updater.download(info, "", tmp_path / "b", opener=make_opener(meta_for(data), data))


def test_spawn_never_uses_detached_process(monkeypatch):
    """powershell.exe 는 콘솔이 아예 없으면(DETACHED_PROCESS) 아무것도 못 하고 끝난다: 숨은 콘솔(CREATE_NO_WINDOW)로 실행."""
    import subprocess

    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 0x8, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    seen = []

    class P:
        pid = 77

        def __init__(self, cmd, creationflags=0, **kw):
            seen.append(creationflags)
            if creationflags & updater.CREATE_BREAKAWAY_FROM_JOB:
                raise PermissionError(5, "breakaway not allowed")

    monkeypatch.setattr(subprocess, "Popen", P)
    assert updater._spawn_detached(["powershell.exe"]) == 77
    assert len(seen) == 2 and all(f & 0x08000000 and not f & 0x8 for f in seen)

from __future__ import annotations

import sys
import time

import pytest

from ics.core import ffbin
from ics.core.project import Project
from ics.core.store import Library


@pytest.fixture()
def saved_project(tmp_path):
    lib = Library()
    rec = lib.create("저장 검증", "", "", "", str(tmp_path / "out"))
    p = Project(rec, lib)
    p.open()
    yield p
    p.close()


def test_corrupt_database_open_releases_file_handle(tmp_path):
    from ics.core.store import ProjectStore

    path = tmp_path / "corrupt.sqlite"
    path.write_bytes(b"not a database" * 300)
    store = ProjectStore(path)  # 손상된 DB 는 따로 보관하고 새 DB 로 시작한다 (백업이 없으면 빈 상태)
    assert store.db_recovered == "empty"
    store.close()
    for f in tmp_path.iterdir():  # 열었던 핸들이 모두 풀려 있어야 Windows 에서도 지울 수 있다
        f.unlink()


def test_project_close_failure_preserves_session_and_allows_retry(saved_project, monkeypatch):
    p = saved_project
    save = p.store.save

    def full_disk(payload):
        raise OSError(28, "디스크 공간 부족")

    monkeypatch.setattr(p.store, "save", full_disk)
    p.set_caption_body("아직 저장하지 못한 편집")
    with pytest.raises(RuntimeError, match="저장"):
        p.close()
    assert not p._closed
    with p.store._lock:
        assert p.store._conn.execute("SELECT value FROM meta WHERE key='session_open'").fetchone()[0] == "1"
    monkeypatch.setattr(p.store, "save", save)
    p.close()
    from ics.core.store import ProjectStore

    reopened = ProjectStore(p.store.path)
    try:
        assert reopened.load_latest()[0]["state"]["caption_body"] == "아직 저장하지 못한 편집"
    finally:
        reopened.close()


def test_project_store_rejects_concurrent_writer(tmp_path):
    from ics.core.store import ProjectStore

    path = tmp_path / "project.sqlite"
    first = ProjectStore(path)
    second = None
    try:
        first.mark_open()
        first.save({"owner": "first"})
        with pytest.raises(RuntimeError, match="사용 중"):
            second = ProjectStore(path)
        assert first.load_latest()[0] == {"owner": "first"}
    finally:
        if second:
            second.close()
        first.close()
    reopened = ProjectStore(path)
    try:
        assert reopened.mark_open()
    finally:
        reopened.close()


def test_process_lock_is_released_after_forced_termination(tmp_path):
    from ics.core.store import ProjectStore

    path, ready = tmp_path / "project.sqlite", tmp_path / "ready"
    child = ffbin.popen(
        [
            sys._base_executable,
            "-c",
            "from pathlib import Path; import sys,time; from ics.core.store import ProjectStore; "
            "s=ProjectStore(Path(sys.argv[1])); s.mark_open(); s.save({'saved': True}); "
            "Path(sys.argv[2]).touch(); time.sleep(30)",
            str(path),
            str(ready),
        ]
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        with pytest.raises(RuntimeError, match="사용 중"):
            ProjectStore(path)
    finally:
        child.kill()
        child.wait(timeout=5)
    store = ProjectStore(path)
    try:
        assert store.mark_open()
        assert store.load_latest()[0] == {"saved": True}
    finally:
        store.close()

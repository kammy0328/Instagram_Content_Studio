"""캡션/PROJECT_DATA 같은 여러 텍스트 파일의 일괄 교체: 중간 실패 시 이전 내용 그대로."""

from __future__ import annotations

import os

import pytest

from ics.render.queue import replace_files_atomically, write_caption_files


def read(d):
    return {p.name: p.read_text("utf-8") for p in sorted(d.iterdir())}


def test_write_caption_files_basic_and_removes_stale_comments(tmp_path):
    write_caption_files(tmp_path, "본문1", ["c1", "c2", "c3"], "credits1")
    assert read(tmp_path) == {
        "CAPTION.txt": "본문1",
        "COMMENT_01.txt": "c1",
        "COMMENT_02.txt": "c2",
        "COMMENT_03.txt": "c3",
        "CREDITS_ORIGINAL.txt": "credits1",
    }
    r = write_caption_files(tmp_path, "본문2", ["d1"], "credits2")
    assert read(tmp_path) == {"CAPTION.txt": "본문2", "COMMENT_01.txt": "d1", "CREDITS_ORIGINAL.txt": "credits2"}
    assert r["files"] == ["CAPTION.txt", "COMMENT_01.txt", "CREDITS_ORIGINAL.txt"]


def test_no_temp_files_left_and_other_files_untouched(tmp_path):
    (tmp_path / "MEMO.txt").write_text("내 메모", "utf-8")
    write_caption_files(tmp_path, "a", ["b"], "c")
    assert (tmp_path / "MEMO.txt").read_text("utf-8") == "내 메모"
    assert not list(tmp_path.glob("*.new"))


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_failure_midway_restores_previous_files_exactly(tmp_path, monkeypatch, fail_at):
    write_caption_files(tmp_path, "이전 본문", ["이전1", "이전2"], "이전 크레딧")
    before = read(tmp_path)
    real, calls = os.replace, {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == fail_at:
            raise OSError(28, "디스크 공간 부족")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError):
        write_caption_files(tmp_path, "새 본문", ["새1"], "새 크레딧")
    monkeypatch.undo()
    assert read(tmp_path) == before  # 이전/새 결과가 섞이지 않는다


def test_failure_while_staging_leaves_everything_untouched(tmp_path, monkeypatch):
    write_caption_files(tmp_path, "이전", ["x"], "이전c")
    before = read(tmp_path)
    monkeypatch.setattr(os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError(5, "I/O")))
    with pytest.raises(OSError):
        write_caption_files(tmp_path, "새", [], "새c")
    monkeypatch.undo()
    assert read(tmp_path) == before and not list(tmp_path.glob("*.new"))


def test_failed_first_write_leaves_no_partial_files(tmp_path, monkeypatch):
    real, calls = os.replace, {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(5, "I/O")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError):
        replace_files_atomically(tmp_path, {"A.txt": "a", "B.txt": "b"})
    monkeypatch.undo()
    assert read(tmp_path) == {}

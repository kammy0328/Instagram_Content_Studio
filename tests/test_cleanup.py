"""프로그램을 닫을 때 쓸모없어진 캐시/임시 파일 정리."""

from __future__ import annotations

import os
import time

from ics import cleanup, paths
from ics.core.store import Library


def _touch(p, size=10, age=0.0):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    if age:
        t = time.time() - age
        os.utime(p, (t, t))
    return p


def test_cache_cleanup_keeps_what_is_used_and_removes_leftovers(tmp_path):
    lib = Library()
    masters = {}
    recs = []
    for i in range(3):
        m = _touch(tmp_path / f"m{i}.mov", 100 + i)
        rec = lib.create(f"P{i}", "A", "T", str(m), str(tmp_path / "out"))
        rec.updated = time.time() - (100 - i)  # P2 이 가장 최근
        lib.upsert(rec)
        masters[rec.id] = m
        recs.append(rec)
    p0, p1, p2 = recs

    def key(path):
        st = os.stat(path)
        return cleanup.file_key(str(path), st.st_size, st.st_mtime)

    def make(rec, k):
        d = paths.cache_dir(rec.id)
        _touch(d / f"proxy_{k}_p2.mkv", 1000)
        _touch(d / f"proxy_{k}.wav", 500)
        _touch(d / f"frameindex_{k}.json")
        _touch(d / "thumbs" / k / "00000001.jpg")
        _touch(d / "reframe_dets.json")
        return d

    d0 = make(p0, key(masters[p0.id]))
    d1 = make(p1, key(masters[p1.id]))
    d2 = make(p2, key(masters[p2.id]))
    # P1: 마스터를 바꾸기 전 원본의 캐시와 끊긴 임시 파일
    old = "0123456789abcdef"
    _touch(d1 / f"proxy_{old}_p2.mkv", 2000)
    _touch(d1 / f"frameindex_{old}.json")
    _touch(d1 / "thumbs" / old / "1.jpg")
    _touch(d1 / f"proxy_{key(masters[p1.id])}_p2.mkv.part", 3000)
    # 지운 프로젝트의 캐시
    gone = _touch(paths.cache_root() / "deadbeef0000" / "proxy_x_p2.mkv", 4000).parent

    rep = cleanup.clean_all(lib, current=p0.id)  # 방금 연 것은 P0, 그다음 최근은 P2
    k0, k1, k2 = (key(masters[r.id]) for r in recs)
    assert not gone.exists()
    assert not (d1 / f"proxy_{old}_p2.mkv").exists() and not (d1 / f"frameindex_{old}.json").exists()
    assert not (d1 / "thumbs" / old).exists() and not list(d1.glob("*.part"))
    # 최근 2개(P0, P2)는 프록시를 남기고, P1 은 프록시만 지운다 (인덱스/썸네일/검출 결과는 남김)
    assert (d0 / f"proxy_{k0}_p2.mkv").exists() and (d2 / f"proxy_{k2}_p2.mkv").exists()
    assert not (d1 / f"proxy_{k1}_p2.mkv").exists() and not (d1 / f"proxy_{k1}.wav").exists()
    assert (d1 / f"frameindex_{k1}.json").exists() and (d1 / "thumbs" / k1).exists()
    assert all((d / "reframe_dets.json").exists() for d in (d0, d1, d2))
    assert rep.freed >= 1000 + 500 + 2000 + 3000 + 4000


def test_unreachable_master_is_not_judged_stale(tmp_path, monkeypatch):
    """원본이 NAS 에 있고 연결이 끊겼으면 '예전 원본' 으로 보고 지우지 않는다."""
    lib = Library()
    rec = lib.create("NAS", "A", "T", str(tmp_path / "offline.mov"), str(tmp_path / "out"))
    d = paths.cache_dir(rec.id)
    f = _touch(d / "frameindex_aaaaaaaaaaaaaaaa.json")
    t = _touch(d / "thumbs" / "aaaaaaaaaaaaaaaa" / "1.jpg")
    cleanup.clean_all(lib, current=rec.id)
    assert f.exists() and t.exists()


def test_temp_and_finished_installers_are_removed(tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    ov = _touch(tmp_path / "ics_overlay" / "x_story.png")
    old_setup = _touch(tmp_path / "ics_update" / "InstagramContentStudio_Setup_1.exe", 100, age=3600)
    fresh = _touch(tmp_path / "ics_update" / "InstagramContentStudio_Setup_2.exe", 100)
    log = _touch(tmp_path / "ics_update" / "launcher.seen.log")
    cleanup.clean_temp()
    assert not ov.exists() and not old_setup.exists()
    assert fresh.exists() and log.exists()  # 막 받은 설치 파일과 기록은 남긴다


def test_successful_update_removes_the_downloaded_installer(tmp_path, monkeypatch):
    from ics import updater

    monkeypatch.setattr(updater, "update_dir", lambda: tmp_path)
    (tmp_path / "launcher.log").write_text("start\nsetup exit=0\nrelaunch ok\n done\n", encoding="utf-8")
    setup = _touch(tmp_path / "InstagramContentStudio_Setup_abc.exe", 100)
    assert updater.last_update_result()[0] == "ok"
    assert not setup.exists()

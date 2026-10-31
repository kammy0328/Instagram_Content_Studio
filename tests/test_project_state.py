"""프로젝트 상태: Undo/Redo, 자동저장, 크래시 복구, 손상 스냅샷 복구, 피드/구간 제한."""

from __future__ import annotations

import sqlite3

import pytest

from ics import paths
from ics.core import frameindex, media
from ics.core.layout import LayoutParams
from ics.core.model import FEED_MAX_FINAL, max_frames_for
from ics.core.project import Project
from ics.core.store import Library


@pytest.fixture()
def env(prores_2398):
    lib = Library()
    rec = lib.create("My Project & Test", "ARTIST", "TITLE", str(prores_2398), str(paths.app_data_dir() / "out"))
    info = media.analyze(str(prores_2398))
    idx = frameindex.build_frame_index(info)

    def make() -> Project:
        p = Project(rec, lib)
        res = p.open()
        p.attach_media(info, idx)
        p.res = res
        return p

    return lib, rec, make


def test_undo_redo_and_group(env):
    _, _, make = env
    p = make()
    p.apply_preset("story", 10, "15")
    assert p.state.story.enabled
    p.begin_group("드래그")
    for e in range(100, 140, 5):
        p.set_range("story", 10, e)
    p.end_group()
    end = p.state.story.end
    assert p.undo() == "드래그" and p.state.story.end != end  # 그룹은 1회 Undo
    assert p.state.story.preset == "15"
    assert p.redo() == "드래그" and p.state.story.end == end
    assert p.undo() == "드래그" and p.undo() and not p.state.story.enabled
    assert p.undo() is None
    p.close()


def test_story_reels_length_limits(env):
    _, _, make = env
    p = make()
    p.set_range("story", 0, 10_000)
    assert p.state.story.end - p.state.story.start <= max_frames_for(60, p.fps)
    p.set_range("reels", 0, 10_000)
    assert p.state.reels.end <= p.count  # 180초 > 영상 길이: 영상 끝까지만
    msg = p.apply_preset("story", 200, "60")
    assert p.state.story.end == p.count and "초만 선택" in msg
    p.close()


def test_feed_limit_ordering_and_warnings(env):
    _, _, make = env
    p = make()
    v, _ = p.add_feed_video(100, 140)
    s = p.add_feed_still(100)  # 같은 시작 프레임: 스틸이 먼저
    assert [f.id for f in p.feed_ordered()][:2] == [s.id, v.id]
    assert any("포함" in w for w in p.feed_warnings())  # 스틸이 영상 구간 안
    assert len(p.state.feed) == 2  # 경고만, 자동 삭제 없음
    for i in range(19):
        p.add_feed_still(i)
    assert len(p.feed_included()) == 21 and p.feed_over_limit()
    p.set_feed_included([s.id, v.id], False)
    assert not p.feed_over_limit() and len(p.feed_included()) == FEED_MAX_FINAL - 1
    p.close()


def test_feed_video_max_60s(env):
    _, _, make = env
    p = make()
    it, msg = p.add_feed_video(0, p.count)  # 영상 10초 -> 제한 안 걸림
    assert it.end == p.count and msg == ""
    p.close()


def test_autosave_and_reopen_restores_everything(env):
    _, _, make = env
    p = make()
    p.apply_preset("story", 10, "15")
    p.set_cover_frame(77)
    p.add_feed_still(60)
    p.set_credits_raw("Director: A @a_id")
    p.set_caption_body("본문 #tag")
    p.import_cuts([(0, 100, "a", {}), (100, 240, "b", {})], {"format": "x"})
    p.merge_cuts([1, 2])
    p.set_playhead(55)
    p.close()
    q = make()
    assert q.res["restored"] and not q.res["crashed"]
    assert q.state.story.enabled and q.state.story.start == 10
    assert q.state.cover.frame == 77 and len(q.state.feed) == 1
    assert q.state.credits_raw.startswith("Director") and q.state.caption_body == "본문 #tag"
    assert len(q.state.cuts) == 1 and q.state.cuts[0].merged
    q.close()


def test_crash_is_detected_and_last_good_state_recovered(env):
    _, rec, make = env
    p = make()
    p.apply_preset("story", 10, "15")
    p.autosaver.flush()
    p.autosaver.close()
    p.store.close()  # mark_closed 없이 종료 = 비정상 종료
    q = make()
    assert q.res["crashed"] and q.res["restored"] and q.state.story.start == 10
    q.close()


def test_corrupt_latest_snapshot_falls_back_to_previous(env):
    _, rec, make = env
    p = make()
    p.apply_preset("story", 10, "15")
    p.autosaver.flush()
    p.set_cover_frame(120)
    p.autosaver.flush()
    p.close()
    con = sqlite3.connect(paths.project_db_path(rec.id))
    con.execute("UPDATE snapshots SET data = substr(data, 1, 20) WHERE id = (SELECT MAX(id) FROM snapshots)")
    con.commit()
    con.close()
    q = make()
    assert q.res["skipped"] >= 1 and q.res["restored"]
    assert q.state.story.start == 10  # 이전 정상 저장본
    q.close()


def test_snapshot_count_is_bounded(env):
    _, rec, make = env
    p = make()
    for i in range(45):
        p.set_playhead(i)
        p.set_cover_frame(i)
        p.autosaver.flush()
    assert p.store.snapshot_count() <= 30
    p.close()


def test_project_folder_name_has_no_spaces_or_ampersand(env):
    _, _, make = env
    p = make()
    name = p.project_folder_name()
    assert " " not in name and "&" not in name and name
    p.close()


def test_selection_layout_is_independent_per_item(env):
    _, _, make = env
    p = make()
    a = p.add_feed_still(10)
    b = p.add_feed_still(20)
    p.set_feed_layout(a.id, LayoutParams("fill", 1.5, 0.2, 0.3))
    assert p.get_feed(a.id).layout.mode == "fill" and p.get_feed(b.id).layout.mode == "fit"
    p.close()


def test_split_cut_at_playhead_is_undoable_and_persisted(env):
    _, _, make = env
    p = make()
    p.import_cuts([(0, 100, "a", {}), (100, 240, "b", {})], {"format": "x"})
    left, right = p.split_cut_at(150)
    assert [(c.start, c.end) for c in p.state.cuts] == [(0, 100), (100, 150), (150, 240)]
    assert p.state.cuts[1].id == left and p.state.cuts[2].id == right
    with pytest.raises(ValueError):
        p.split_cut_at(150)  # 이제 150 은 뒤쪽 컷의 첫 프레임
    assert len(p.state.cuts) == 3  # 실패한 시도는 상태를 바꾸지 않는다
    assert p.can_undo() and p.undo_label() == "컷 나누기"
    p.undo()
    assert [(c.start, c.end) for c in p.state.cuts] == [(0, 100), (100, 240)]
    p.redo()
    assert len(p.state.cuts) == 3
    p.close()
    q = make()
    assert [(c.start, c.end) for c in q.state.cuts] == [(0, 100), (100, 150), (150, 240)]
    q.close()


def test_split_cut_without_xml_starts_from_whole_video(env):
    _, _, make = env
    p = make()
    assert p.state.cuts == []
    left, right = p.split_cut_at(30)
    assert [(c.start, c.end) for c in p.state.cuts] == [(0, 30), (30, p.count)]
    p.undo()
    assert p.state.cuts == []
    with pytest.raises(ValueError):
        p.split_cut_at(0)  # 첫 프레임에서는 나눌 수 없다
    assert p.state.cuts == []
    p.close()

from __future__ import annotations

import threading
from fractions import Fraction

import pytest

from ics.core import cuts
from ics.core.frameindex import FrameIndex
from ics.core.layout import compute_geometry
from ics.core.project import Project
from ics.core.store import Library
from ics.core.timecode import tc_to_frame
from ics.render import engine
from ics.render.planner import plan_render
from tests.helpers import decode_code, make_video


@pytest.mark.parametrize("delta", [-1, 1])
def test_output_verification_rejects_one_frame_error(prores_2398, opener, tmp_path, delta):
    info, idx = opener(prores_2398)
    geo = compute_geometry(info.display_w, info.display_h, "45")
    out = tmp_path / "verify.mp4"
    engine.render_video(info, idx, engine.VideoSpec(0, 4, geo), str(out))
    with pytest.raises(engine.RenderError, match="프레임 수 불일치"):
        engine.verify_video(str(out), 4 + delta, float(Fraction(4, 1) / idx.fps), True, geo)


def test_cancel_during_verification_keeps_existing_output(prores_2398, opener, tmp_path, monkeypatch):
    info, idx = opener(prores_2398)
    geo = compute_geometry(info.display_w, info.display_h, "45")
    out = tmp_path / "cancel.mp4"
    out.write_bytes(b"existing output")
    event = threading.Event()
    verify = engine.verify_video

    def cancel_after_verify(*args):
        result = verify(*args)
        event.set()
        return result

    monkeypatch.setattr(engine, "verify_video", cancel_after_verify)
    with pytest.raises(engine.RenderCancelled):
        engine.render_video(info, idx, engine.VideoSpec(0, 4, geo), str(out), cancel=event)
    assert out.read_bytes() == b"existing output"
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("codec", ["h264", "h265"])
@pytest.mark.parametrize("frame", [0, 1, 19])
def test_nonzero_start_pts_still_is_exact(tmp_path, opener, codec, frame):
    src = make_video(tmp_path / "offset.mp4", frames=20, audio=False, vcodec=codec, extra=["-output_ts_offset", "5"])
    info, idx = opener(src)
    assert info.start_time >= 5
    geo = compute_geometry(info.display_w, info.display_h, "45")
    arr = engine.decode_still_rgb(info, idx, frame, geo)
    assert decode_code(arr, geo.pad_x, geo.pad_y, geo.content_w / 960, geo.content_h / 540) == frame


@pytest.fixture()
def vfr_project(tmp_path):
    lib = Library()
    src = tmp_path / "master.mp4"
    src.touch()
    rec = lib.create("VFR", "", "", str(src), str(tmp_path / "out"))
    p = Project(rec, lib)
    p.open()
    from ics.core.media import MediaInfo

    pts = [Fraction(n, 10) for n in range(2000)] + [Fraction(200) + Fraction(n, 30) for n in range(6000)]
    idx = FrameIndex(len(pts), Fraction(20), Fraction(0), pts, vfr=True)
    p.attach_media(
        MediaInfo(path=str(src), width=960, height=540, display_w=960, display_h=540, fps="20", is_vfr=True), idx
    )
    yield p
    p.close()


@pytest.mark.parametrize("which,limit", [("story", 60), ("reels", 180)])
def test_vfr_ranges_and_presets_use_pts_duration(vfr_project, which, limit):
    p = vfr_project
    p.set_range(which, 0, p.count)
    s = p._sel(which)
    assert p.seconds(s.start, s.end) <= limit
    p.apply_preset(which, 0, "max")
    assert p.seconds(s.start, s.end) <= limit


def test_vfr_feed_range_uses_pts_duration(vfr_project):
    p = vfr_project
    item, _ = p.add_feed_video(0, p.count)
    assert p.seconds(item.start, item.end) <= 60
    p.update_feed_range(item.id, 0, p.count)
    assert p.seconds(item.start, item.end) <= 60


@pytest.mark.parametrize("kind", ["story", "reels", "feed"])
def test_planner_blocks_oversized_vfr_saved_state(vfr_project, kind):
    p = vfr_project
    if kind == "feed":
        item, _ = p.add_feed_video(0, 1)
        item.end = 1000
    else:
        sel = p._sel(kind)
        sel.enabled, sel.start, sel.end = True, 0, 1900 if kind == "reels" else 1000
    assert plan_render(p).blocked


def test_unmerge_after_trimming_past_inner_boundary_keeps_valid_cuts():
    cs = cuts.cuts_from_ranges([(0, 40, "a", {}), (40, 90, "b", {}), (90, 150, "c", {})])
    merged = cuts.merge(cs, [1, 2, 3])
    trimmed = cuts.move_boundary(merged, 0, "start", 100, "trim", 150)
    result = cuts.unmerge(trimmed, trimmed[0].id)
    assert [(c.start, c.end) for c in result] == [(100, 150)]
    assert all(c.length > 0 for c in result)


@pytest.mark.parametrize("fps,drop", [(Fraction(30000, 1001), 2), (Fraction(60000, 1001), 4)])
def test_skipped_drop_frame_labels_are_invalid(fps, drop):
    for frame in range(drop):
        assert tc_to_frame(f"00:01:00;{frame:02d}", fps) is None
    assert tc_to_frame("00:10:00;00", fps) is not None

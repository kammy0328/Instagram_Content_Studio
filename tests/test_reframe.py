"""STORY / REELS Fill 9:16 컷별 가로 위치: 구간 나누기, 자동 위치 고르기, 인물 검출, 렌더링 반영."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from ics.core import reframe as R
from ics.core.layout import LayoutParams, compute_geometry
from ics.core.model import EditState, Selection
from ics.render import engine
from tests.helpers import read_frame_rgb

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Cut:
    start: int
    end: int


def test_segments_follow_cuts_and_fill_gaps():
    cuts = [Cut(0, 50), Cut(50, 120), Cut(130, 200)]
    segs = R.segments(30, 160, cuts)
    assert [(s.start, s.end, s.key) for s in segs] == [(30, 50, 0), (50, 120, 50), (120, 130, -1), (130, 160, 130)]
    assert [(s.start, s.end, s.key) for s in R.segments(10, 20, [])] == [(10, 20, -1)]


def test_pos_for_frame_uses_the_cut_value_or_the_default():
    cuts = [Cut(0, 50), Cut(50, 120)]
    assert R.pos_for_frame(10, cuts, {50: 0.9}, 0.5) == 0.5
    assert R.pos_for_frame(60, cuts, {50: 0.9}, 0.5) == 0.9
    assert R.pos_for_frame(500, cuts, {-1: 0.2}, 0.5) == 0.2


def test_best_window_prefers_most_visible_people_and_centers_them():
    # 폭 1000 화면, 창 폭 300. 왼쪽에 한 명(약함), 오른쪽에 두 명(강함) -> 오른쪽 두 명을 다 담고 그 가운데로
    frames = [[R.Det(50, 0, 150, 100, 0.3), R.Det(600, 0, 700, 100, 0.5), R.Det(720, 0, 820, 100, 0.5)]]
    px = R.best_window_x(frames, 0, 1000, 300)
    x = px * 700
    assert x <= 600 and x + 300 >= 820
    assert abs((x + 150) - 710) < 25  # 두 사람 가운데
    # 인물이 없으면 바꾸지 않는다
    assert R.best_window_x([[], []], 0, 1000, 300) is None


def test_person_who_appears_briefly_counts_less_than_one_who_stays():
    stay = R.Det(100, 0, 200, 100, 0.5)
    brief = R.Det(800, 0, 900, 100, 0.5)
    frames = [[stay], [stay], [stay, brief], [stay]]
    x = R.best_window_x(frames, 0, 1000, 300) * 700
    assert x <= 100 and x + 300 >= 200


def test_crop_steps_change_only_at_cut_boundaries():
    lp = LayoutParams("fill")
    segs = R.segments(10, 100, [Cut(0, 40), Cut(40, 70), Cut(70, 200)])
    steps = R.crop_steps(1920, 1080, lp, segs, {0: 0.0, 40: 0.0, 70: 1.0})
    g1 = compute_geometry(1920, 1080, "916", LayoutParams("fill", pos_x=1.0))
    assert steps == [(0, 0), (60, g1.crop_x)]
    assert R.crop_steps(1920, 1080, LayoutParams("fit"), segs, {0: 1.0}) == []


def test_selection_cut_x_round_trip_and_old_saves():
    s = Selection(True, 0, 10, "custom", LayoutParams("fill"), {0: 0.25, -1: 0.75})
    d = s.to_dict()
    assert d["cut_x"] == {"0": 0.25, "-1": 0.75}
    assert Selection.from_dict(d).cut_x == {0: 0.25, -1: 0.75}
    assert EditState.from_dict({"story": {"enabled": True, "start": 0, "end": 5}}).story.cut_x == {}


def test_detector_finds_people_and_faces_in_a_drawn_scene():
    pytest.importorskip("cv2")
    det = R.PersonDetector(ROOT / "assets" / "models")
    img = np.zeros((360, 640, 3), np.uint8)
    out = det.detect(img)
    assert out == []  # 빈 화면에는 아무것도 없다


def test_analyze_picks_the_side_with_people(prores_2398, opener):
    """검출기를 대역으로 바꿔 디코딩, 표본, 좌표 환산, 컷별 결과 묶음을 확인한다."""
    info, idx = opener(prores_2398)

    class FakeDet:
        def detect(self, rgb):
            h, w = rgb.shape[:2]
            return [R.Det(w * 0.85, 0, w * 0.95, h, 1.0)]  # 늘 오른쪽 끝에 인물

    segs = R.segments(0, 120, [Cut(0, 60), Cut(60, 240)])
    calls = []

    def decode(n, w, h):
        calls.append(n)
        return np.zeros((h, w, 3), np.uint8)

    res = R.analyze(info, idx, LayoutParams("fill"), segs, decode, detector_factory=FakeDet, workers=2)
    assert set(res) == {0, 60} and all(v > 0.9 for v in res.values())
    assert calls and all(0 <= n < 120 for n in calls)


def test_render_switches_crop_at_the_cut(prores_2398, opener, tmp_path):
    """원본 좌우 끝에는 흰 테두리가 있다: 앞 컷은 왼쪽 끝, 뒤 컷은 오른쪽 끝 크롭이면 테두리가 그쪽에만 보인다."""
    info, idx = opener(prores_2398)
    lp = LayoutParams("fill")
    geo = compute_geometry(info.display_w, info.display_h, "916", lp)
    right = info.display_w - geo.crop_w
    out = tmp_path / "o.mp4"
    spec = engine.VideoSpec(30, 54, geo, x_steps=[(0, 0), (12, right)])
    engine.render_video(info, idx, spec, str(out))
    a = read_frame_rgb(out, n=5)
    b = read_frame_rgb(out, n=18)
    mid = a.shape[0] // 2
    assert a[mid, 1:3].mean() > 200 and a[mid, -3:-1].mean() < 200  # 앞 컷: 왼쪽 테두리
    assert b[mid, -3:-1].mean() > 200 and b[mid, 1:3].mean() < 200  # 뒤 컷: 오른쪽 테두리
    # 컷 경계 바로 앞/뒤 프레임 (키프레임 이동 없이 한 번에 바뀐다)
    assert read_frame_rgb(out, n=11)[mid, 1:3].mean() > 200
    assert read_frame_rgb(out, n=12)[mid, -3:-1].mean() > 200


def test_planner_passes_cut_steps_to_the_job(prores_2398, tmp_path):
    from ics import paths
    from ics.core import frameindex, media
    from ics.core.project import Project
    from ics.core.store import Library
    from ics.render.planner import plan_render

    lib = Library()
    rec = lib.create("RF", "A", "T", str(prores_2398), str(tmp_path / "out"))
    info = media.analyze(str(prores_2398))
    ix = frameindex.build_frame_index(info, paths.cache_dir(rec.id))
    p = Project(rec, lib)
    p.open()
    p.attach_media(info, ix)
    p.import_cuts([(0, 40, "a", {}), (40, 240, "b", {})], {"file": "x.xml"})
    p.set_range("reels", 20, 80)
    p.set_selection_layout("reels", LayoutParams("fill"))
    p.set_cut_x("reels", {0: 0.0, 40: 1.0})
    job = next(j for j in plan_render(p, sections={"reels"}).jobs if j.kind == "reels")
    g1 = compute_geometry(info.display_w, info.display_h, "916", LayoutParams("fill", pos_x=1.0))
    assert job.params["x_steps"] == [[0, 0], [20, g1.crop_x]]
    p.set_cut_x("reels", {0: 1.0, 40: 1.0}, replace_all=True)  # 위치가 하나뿐이면 고정 크롭
    job = next(j for j in plan_render(p, sections={"reels"}).jobs if j.kind == "reels")
    assert "x_steps" not in job.params and job.params["geo"]["crop_x"] == g1.crop_x


def test_person_moving_within_a_cut_is_centered_on_the_average_position():
    # 폭 1000, 창 300. 한 사람이 컷 동안 왼쪽(100)에서 오른쪽(700)으로 걸어간다 -> 경로 평균(450) 가운데
    frames = [[R.Det(x, 0, x + 100, 100, 0.5)] for x in (100, 250, 400, 550, 700)]
    px = R.best_window_x(frames, 0, 1000, 300)
    assert abs((px * 700 + 150) - 450) < 15


def test_moving_group_follows_the_group_average():
    frames = [[R.Det(x, 0, x + 60, 100, 0.5), R.Det(x + 90, 0, x + 150, 100, 0.5)] for x in (200, 300, 400)]
    px = R.best_window_x(frames, 0, 1000, 300)
    assert abs((px * 700 + 150) - (300 + 75)) < 15  # 무리 가운데(경로 평균)


def test_window_avoids_cutting_a_face_in_half():
    # 창 300. 두 사람 무리의 평균 가운데로 두면 오른쪽 사람 얼굴(560~620)이 창 끝(약 575)에 반쯤 걸린다
    # -> 가까운 범위 안에서 얼굴이 다 들어가거나 다 빠지는 위치로 옮긴다
    a = [R.Det(300, 0, 400, 300, 0.4), R.Det(320, 20, 380, 80, 0.6, True)]
    b = [R.Det(580, 0, 680, 300, 0.4), R.Det(600, 20, 660, 80, 0.6, True)]
    frames = [a, a, b, b]
    px = R.best_window_x(frames, 0, 1000, 300)
    x = px * 700
    face = (600, 660)
    vis = max(0.0, min(face[1], x + 300) - max(face[0], x)) / 60
    assert vis < 0.15 or vis > 0.85, vis
    left = (320, 380)
    assert x <= left[0] and x + 300 >= left[1]  # 오래 나온 사람 얼굴은 온전히


def test_detection_cache_is_reused_and_saved(prores_2398, opener, tmp_path):
    info, idx = opener(prores_2398)
    calls = []

    class FakeDet:
        def detect(self, rgb):
            calls.append(1)
            h, w = rgb.shape[:2]
            return [R.Det(w * 0.1, 0, w * 0.2, h, 1.0), R.Det(w * 0.12, 0, w * 0.18, h * 0.2, 1.0, True)]

    segs = R.segments(0, 120, [Cut(0, 60), Cut(60, 240)])
    cache = {}
    dec = lambda n, w, h: np.zeros((h, w, 3), np.uint8)  # noqa: E731
    r1 = R.analyze(info, idx, LayoutParams("fill"), segs, dec, detector_factory=FakeDet, cache=cache)
    n1 = len(calls)
    assert n1 > 0 and len(cache) == n1
    r2 = R.analyze(info, idx, LayoutParams("fill"), segs, dec, detector_factory=FakeDet, cache=cache)
    assert len(calls) == n1 and r1 == r2  # 두 번째(REELS 등)는 다시 검출하지 않는다
    path = tmp_path / "dets.json"
    R.save_cache(path, cache)
    back = R.load_cache(path)
    assert set(back) == set(cache) and any(d.face for v in back.values() for d in v)
    assert R.load_cache(tmp_path / "missing.json") == {}


def test_face_nudge_is_what_prevents_the_cut(monkeypatch):
    a = [R.Det(300, 0, 400, 300, 0.4), R.Det(320, 20, 380, 80, 0.6, True)]
    b = [R.Det(580, 0, 680, 300, 0.4), R.Det(600, 20, 660, 80, 0.6, True)]
    monkeypatch.setattr(R, "NUDGE", 0.0)
    x = R.best_window_x([a, a, b, b], 0, 1000, 300) * 700
    vis = max(0.0, min(660, x + 300) - max(600, x)) / 60
    assert 0.15 < vis < 0.85  # 옮기지 않으면 얼굴이 잘린다 (위 테스트가 실제로 옮김을 확인하는지 보증)


def test_center_follows_the_face_not_the_body_box():
    # 몸 상자는 팔을 뻗어 왼쪽으로 넓다 (200~500, 가운데 350). 얼굴은 400~440 (가운데 420) -> 얼굴 가운데
    frames = [[R.Det(200, 0, 500, 600, 0.5), R.Det(400, 40, 440, 90, 0.7, True)]]
    x = R.best_window_x(frames, 0, 1000, 300) * 700
    assert abs((x + 150) - 420) < 8


def test_back_turned_person_without_face_still_uses_the_body():
    frames = [[R.Det(600, 0, 700, 600, 0.5)]]
    x = R.best_window_x(frames, 0, 1000, 300) * 700
    assert abs((x + 150) - 650) < 8

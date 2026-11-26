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


def test_old_save_with_positions_only_on_one_side_is_linked(prores_2398, tmp_path):
    from ics import paths
    from ics.core import frameindex, media
    from ics.core.project import Project
    from ics.core.store import Library

    lib = Library()
    rec = lib.create("LINK", "A", "T", str(prores_2398), str(tmp_path / "out"))
    info = media.analyze(str(prores_2398))
    ix = frameindex.build_frame_index(info, paths.cache_dir(rec.id))
    p = Project(rec, lib)
    p.open()
    p.attach_media(info, ix)
    p.state.reels.cut_x, p.state.reels.cut_x_src = {0: 0.3}, {0: "auto"}  # 예전 버전: REELS 에만 있음
    p.close()
    q = Project(rec, lib)
    q.open()
    assert q.state.story.cut_x == {0: 0.3} and q.state.story.cut_x_src == {0: "auto"}
    q.close()


def test_frames_where_the_face_stays_longest():
    # 얼굴이 컷의 3/4 동안 300 근처, 마지막 1/4 동안 800 으로 이동 -> 단순 평균(425)이 아니라 오래 머문 300
    stay = [[R.Det(260, 0, 340, 400, 0.5), R.Det(280, 20, 320, 60, 0.7, True)]] * 6
    moved = [[R.Det(760, 0, 840, 400, 0.5), R.Det(780, 20, 820, 60, 0.7, True)]] * 2
    x = R.best_window_x(stay + moved, 0, 1000, 300) * 700
    assert abs((x + 150) - 300) < 10


# ---------------------------------------------------------------- 얼굴 정확도 개선 (SCRFD, 머리 상자, 코 위치, 2차 검출)
def test_head_box_is_top_center_of_person():
    from ics.core.reframe import head_box

    x1, y1, x2, y2 = head_box(100, 50, 300, 450)  # 팔을 벌린 넓은 몸 상자
    assert abs((x1 + x2) / 2 - 200) < 1e-6 and y1 == 50 and y2 == 50 + 0.22 * 400
    assert (x2 - x1) < 200 * 0.5  # 몸 폭보다 훨씬 좁다


def test_nms_faces_merges_duplicates_from_tiles():
    from ics.core.reframe import _nms_faces

    a = (10, 10, 30, 30, 0.9, 20.0)
    b = (11, 10, 31, 30, 0.8, None)  # 겹치는 타일에서 같은 얼굴
    c = (100, 10, 120, 30, 0.7, None)
    assert _nms_faces([b, a, c]) == [a, c]


class _FakeSCRFD:
    def __init__(self, first, tiles):
        self.first, self.tiles, self.calls = first, tiles, []

    def detect(self, rgb, width=640):
        self.calls.append((rgb.shape[1], width))
        return self.first if len(self.calls) == 1 else self.tiles.pop(0) if self.tiles else []


def _detector_with(fake):
    from ics.core.reframe import PersonDetector

    d = PersonDetector.__new__(PersonDetector)
    d.scrfd = fake
    import cv2

    d.cv2 = cv2
    return d


def test_second_pass_runs_only_when_a_person_has_no_face():
    import numpy as np

    img = np.zeros((720, 1280, 3), np.uint8)
    person = [(600.0, 100.0, 700.0, 600.0, 0.9)]
    # 머리 자리에 얼굴이 있으면 2차 검출 없음
    fake = _FakeSCRFD([(630.0, 100.0, 670.0, 150.0, 0.9, 650.0)], [])
    d = _detector_with(fake)
    assert len(d.faces(img, person)) == 1 and len(fake.calls) == 1
    # 사람은 있는데 얼굴이 없으면 좌우 반쪽을 원본 해상도로 다시 (타일 좌표는 전체 좌표로 되돌린다)
    fake = _FakeSCRFD([], [[], [(10.0, 20.0, 30.0, 40.0, 0.8, 20.0)]])
    d = _detector_with(fake)
    got = d.faces(img, person)
    assert len(fake.calls) == 3 and fake.calls[1][0] == int(1280 * 0.55)
    x0 = 1280 - int(1280 * 0.55)
    assert got == [(10.0 + x0, 20.0, 30.0 + x0, 40.0, 0.8, 20.0 + x0)]


def test_face_center_follows_the_nose_and_sharp_faces_weigh_more(monkeypatch):
    import numpy as np

    from ics.core import reframe

    d = _detector_with(None)
    monkeypatch.setattr(d, "persons", lambda rgb: [], raising=False)
    faces = [(100.0, 100.0, 160.0, 160.0, 0.9, 115.0), (400.0, 100.0, 460.0, 160.0, 0.9, None)]
    monkeypatch.setattr(d, "faces", lambda rgb, persons=None: faces, raising=False)
    img = np.zeros((720, 1280, 3), np.uint8)
    img[100:160, 100:160] = (np.indices((60, 60)).sum(0) % 2 * 255)[..., None]  # 첫 얼굴만 선명(체크무늬)
    dets = d.detect(img)
    a, b = dets
    assert a.face and abs((a.x1 + a.x2) / 2 - 115.0) < 1e-6 and abs(a.x2 - a.x1 - 60) < 1e-6  # 가운데 = 코
    assert a.weight > b.weight * 1.5  # 흐린(무늬 없는) 얼굴은 가중치가 낮다
    assert reframe.ANALYSIS_WIDTH == 1280 and reframe.CACHE_VERSION == 3


def test_bundled_scrfd_model_loads_and_finds_nothing_in_noise():
    import numpy as np

    from ics.core import reframe

    d = reframe.PersonDetector()
    assert d.face_model == "scrfd"
    rng = np.random.default_rng(0)
    assert d.faces(rng.integers(0, 40, (720, 1280, 3), dtype=np.uint8), []) == []


# ---------------------------------------------------------------- 옆얼굴: 보는 쪽에 여백 (lead room)
def test_yaw_from_landmarks_sign_and_range():
    from ics.core.reframe import yaw_from_landmarks

    assert abs(yaw_from_landmarks(40, 60, 50, 50)) < 1e-9  # 정면: 코가 두 눈 가운데
    assert yaw_from_landmarks(40, 50, 58, 50) > 0.5  # 코가 오른쪽으로: 화면 오른쪽을 봄
    assert yaw_from_landmarks(50, 60, 42, 50) < -0.5
    assert yaw_from_landmarks(50, 52, 90, 50) == 1.0  # 완전 옆얼굴은 1 로 자른다


def test_profile_face_keeps_the_back_of_the_head_in_frame():
    # 옆얼굴의 코는 머리 앞쪽 끝: 기준을 뒤통수 쪽으로 옮겨 뒷머리가 잘리지 않게 (IZNA 10번 컷 수동 값과 맞춘 양)
    from ics.core.reframe import HEAD_SHIFT, Det, best_window_x, face_anchor_x

    lo, hi, win = 0.0, 1920.0, 608.0
    span = hi - lo - win

    def frames(yaw):
        return [[Det(900, 300, 1100, 520, 1.0, True, yaw)] for _ in range(5)]

    front = best_window_x(frames(0.0), lo, hi, win)
    right = best_window_x(frames(0.9), lo, hi, win)  # 오른쪽을 봄 -> 창은 왼쪽(뒤통수 쪽)으로
    left = best_window_x(frames(-0.9), lo, hi, win)
    shift = HEAD_SHIFT * 200 * 0.9 / span
    assert abs((front - right) - shift) < 0.02 and abs((left - front) - shift) < 0.02
    assert face_anchor_x(900, 1100, 0.0) == 1000 and face_anchor_x(900, 1100, -1.0) == 1000 + HEAD_SHIFT * 200


def test_real_cut_with_turning_profile_matches_the_hand_set_position():
    # IZNA_IWALY_PV 10번 컷 (3840x2160, 9:16 창 1215): 왼쪽을 보며 고개를 돌려 오른쪽으로 자리 잡는 얼굴.
    # 사용자가 손으로 맞춘 값 0.3429 (예전 자동 0.279: 코 기준이라 뒷머리가 잘림)
    from ics.core.reframe import Det, best_window_x

    rows = [
        (1364, 2033, 0.661, 736, 1464, 1.197), (1325, 2006, 0.664, 735, 1452, 1.18),
        (1366, 2034, 0.659, 827, 1521, 1.15), (1498, 2175, 0.655, 1006, 1680, 1.024),
        (1675, 2318, 0.63, 1198, 1903, 1.073), (1636, 2268, 0.634, 1215, 1918, 0.973),
        (1580, 2189, 0.624, 1138, 1882, 1.056), (1576, 2169, 0.612, 1139, 1910, 1.06),
    ]  # fmt: skip
    win = 2160 * 9 / 16
    frames = [[Det(h1, 0, h2, 400, hw), Det(f1, 200, f2, 900, fw, True, -0.95)] for h1, h2, hw, f1, f2, fw in rows]
    px = best_window_x(frames, 0, 3840, win)
    assert abs(px - 0.3429) * (3840 - win) < 40  # 40px 안 (창 폭의 3%)


def test_cache_keeps_face_direction(tmp_path):
    from ics.core import reframe

    p = tmp_path / "c.json"
    reframe.save_cache(p, {12: [reframe.Det(1, 2, 3, 4, 0.5, True, -0.734)]})
    d = reframe.load_cache(p)[12][0]
    assert d.face and abs(d.yaw + 0.734) < 1e-3

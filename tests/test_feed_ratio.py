"""FEED 원본 비율(기본) / Fit 확대 / 기존 프로젝트 호환."""

from __future__ import annotations

import pytest

from ics.core import ffbin
from ics.core.layout import LayoutParams, compute_geometry
from ics.core.model import EditState
from tests.helpers import all_frame_codes, read_frame_rgb
from tests.test_render_accuracy import render


# ------------------------------------------------------------------ 지오메트리
@pytest.mark.parametrize("w,h", [(1920, 1080), (960, 540), (1080, 1920), (1000, 1000), (3840, 1608), (1440, 1080)])
def test_orig_keeps_the_source_aspect_without_padding_or_crop(w, h):
    g = compute_geometry(w, h, "orig", LayoutParams("fit"))
    assert g.canvas_w % 2 == 0 and g.canvas_h % 2 == 0 and g.canvas_w <= 1080
    assert abs(g.canvas_w / g.canvas_h - w / h) < 0.01  # 짝수 반올림 오차만 허용
    assert (g.crop_x, g.crop_y, g.crop_w, g.crop_h) == (0, 0, w, h)  # 크롭 없음
    assert not g.upscaled


def test_orig_never_upscales_small_sources():
    g = compute_geometry(640, 360, "orig", LayoutParams())
    assert (g.canvas_w, g.canvas_h) == (640, 360)


def test_orig_zoom_crops_the_source_instead_of_adding_bars():
    g = compute_geometry(1920, 1080, "orig", LayoutParams("fit", zoom=2.0, pos_x=0.0, pos_y=1.0))
    assert (g.canvas_w, g.canvas_h) == (1080, 608) or (g.canvas_w, g.canvas_h) == (1080, 606)
    assert g.crop_w == 960 and g.crop_h == 540 and g.crop_x == 0 and g.crop_y == 540


def test_fit_zoom_enlarges_and_crops_only_the_overflowing_axis():
    base = compute_geometry(1920, 1080, "916", LayoutParams("fit"))
    assert base.crop_w == 1920 and base.crop_h == 1080 and base.pad_y > 0
    g = compute_geometry(1920, 1080, "916", LayoutParams("fit", zoom=2.0))
    assert g.scale > base.scale and not g.upscaled  # 확대되지만 의미 없는 업스케일은 티어를 낮춰 피한다
    assert g.crop_w < 1920  # 가로는 넘쳐서 잘린다
    assert g.content_w == g.canvas_w
    assert g.pad_x + g.content_w <= g.canvas_w and g.pad_y + g.content_h <= g.canvas_h


def test_fit_zoom_one_is_identical_to_the_old_fit():
    g = compute_geometry(1920, 1080, "916", LayoutParams("fit", zoom=1.0))
    assert (g.content_w, g.pad_x) == (1080, 0) and g.content_h in (606, 608)


@pytest.mark.parametrize("zoom", [1.0, 1.3, 2.0, 3.0])
@pytest.mark.parametrize("kind", ["916", "45", "orig"])
def test_fit_zoom_stays_inside_the_source_and_canvas(zoom, kind):
    for px, py in ((0, 0), (0.5, 0.5), (1, 1)):
        g = compute_geometry(1920, 1080, kind, LayoutParams("fit", zoom=zoom, pos_x=px, pos_y=py))
        assert 0 <= g.crop_x and g.crop_x + g.crop_w <= 1920
        assert 0 <= g.crop_y and g.crop_y + g.crop_h <= 1080
        assert g.pad_x + g.content_w <= g.canvas_w and g.pad_y + g.content_h <= g.canvas_h


# ------------------------------------------------------------------ 옛 저장본 호환
def test_old_fit_layout_with_leftover_zoom_is_not_changed():
    old = {"mode": "fit", "zoom": 2.5, "pos_x": 0.5, "pos_y": 0.5}  # zv 없음: 예전에는 fit 에서 zoom 이 무시됐다
    assert LayoutParams.from_dict(old).zoom == 1.0
    assert LayoutParams.from_dict({**old, "mode": "fill"}).zoom == 2.5
    new = LayoutParams.from_dict(LayoutParams("fit", zoom=2.5).to_dict())
    assert new.zoom == 2.5 and new.mode == "fit"


def _state_with_feed(**extra):
    st = EditState()
    d = st.to_dict()
    d.pop("feed_ratio", None)
    d.pop("cover_ratio", None)
    d.update(extra)
    return d


def test_project_without_feed_ratio_defaults_to_original_when_no_feed_items():
    assert EditState.from_dict(_state_with_feed()).feed_ratio == "orig"
    assert EditState().feed_ratio == "orig"


def test_existing_project_with_feed_items_keeps_4x5_so_outputs_do_not_change():
    item = {"id": "x", "kind": "still", "start": 3, "end": 4, "included": True, "layout": {"mode": "fit"}}
    st = EditState.from_dict(_state_with_feed(feed=[item]))
    assert st.feed and st.feed_ratio == "45"


@pytest.mark.parametrize("bad", ["", None, "169", 5])
def test_unknown_ratio_value_falls_back_to_original(bad):
    assert EditState.from_dict(_state_with_feed(feed_ratio=bad)).feed_ratio == "orig"


def test_feed_ratio_round_trips():
    st = EditState()
    st.feed_ratio = "45"
    assert EditState.from_dict(st.to_dict()).feed_ratio == "45"


# ------------------------------------------------------------------ 렌더 (프레임 정확도 포함)
def test_original_ratio_feed_video_renders_exact_frames_at_source_aspect(prores_2398, opener, tmp_path):
    info, idx = opener(prores_2398)
    out = tmp_path / "o.mp4"
    geo, res = render(info, idx, 10, 58, out, kind="orig", layout=LayoutParams("fit"))
    assert (res["width"], res["height"]) == (geo.canvas_w, geo.canvas_h) == (960, 540)
    assert all_frame_codes(out, geo.canvas_w and 960, 540) == list(range(10, 58))


def test_fit_zoom_video_renders_exact_frames_and_fills_the_canvas(prores_2398, opener, tmp_path):
    info, idx = opener(prores_2398)
    out = tmp_path / "o.mp4"
    geo, res = render(info, idx, 20, 44, out, kind="916", layout=LayoutParams("fit", zoom=2.0))
    assert (res["width"], res["height"]) == (geo.canvas_w, geo.canvas_h)
    pj = ffbin.probe_json(
        str(out), "-count_packets", "-select_streams", "v:0", "-show_entries", "stream=nb_read_packets"
    )
    assert int(pj["streams"][0]["nb_read_packets"]) == 24
    fr = read_frame_rgb(out, t=0.2)
    # 가로가 넘쳐 잘리므로 원본 좌우 흰 테두리는 보이지 않고, 위아래에는 여백이 남는다
    assert fr[fr.shape[0] // 2, 1:3].mean() < 200
    assert geo.crop_w < info.display_w


# ------------------------------------------------------------------ REELS 커버 비율
def test_cover_ratio_defaults_to_original_for_new_projects():
    assert EditState().cover_ratio == "orig"
    assert EditState.from_dict(_state_with_feed()).cover_ratio == "orig"


def test_existing_project_with_a_cover_keeps_916_so_the_output_does_not_change():
    st = EditState.from_dict(_state_with_feed(cover={"frame": 12}))
    assert st.cover.frame == 12 and st.cover_ratio == "916"


@pytest.mark.parametrize("bad", ["", None, "45", 3])
def test_unknown_cover_ratio_falls_back_to_original(bad):
    assert EditState.from_dict(_state_with_feed(cover_ratio=bad)).cover_ratio == "orig"


def test_cover_ratio_round_trips():
    st = EditState()
    st.cover_ratio = "916"
    assert EditState.from_dict(st.to_dict()).cover_ratio == "916"

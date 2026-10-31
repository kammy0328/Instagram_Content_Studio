import pytest

from ics.core.layout import LayoutParams, compute_geometry


def test_fit_16x9_into_story_keeps_everything():
    g = compute_geometry(1920, 1080, "916", LayoutParams("fit"))
    assert (g.canvas_w, g.canvas_h) == (1080, 1920)
    assert g.content_w == 1080 and g.content_h in (606, 608)  # 짝수 반올림
    assert g.content_w <= g.canvas_w and g.content_h <= g.canvas_h
    assert abs(g.content_w / g.content_h - 1920 / 1080) < 0.01  # 비율 유지(크롭/왜곡 없음)
    assert g.pad_x == 0 and g.pad_y == (1920 - g.content_h) // 2 - ((1920 - g.content_h) // 2) % 2
    assert not g.upscaled


def test_fit_16x9_into_feed_45():
    g = compute_geometry(1920, 1080, "45", LayoutParams("fit"))
    assert (g.canvas_w, g.canvas_h) == (1080, 1350)
    assert g.content_w == 1080 and g.pad_x == 0


@pytest.mark.parametrize("w,h,kind", [(1920, 1080, "916"), (1280, 720, "916"), (3840, 2160, "45"), (1000, 1000, "45")])
def test_even_dimensions_and_bounds(w, h, kind):
    for mode in ("fit", "fill"):
        g = compute_geometry(w, h, kind, LayoutParams(mode, zoom=1.7, pos_x=0.3, pos_y=0.8))
        assert g.canvas_w % 2 == 0 and g.canvas_h % 2 == 0
        if mode == "fit":
            assert g.content_w % 2 == 0 and g.content_h % 2 == 0
            assert g.pad_x + g.content_w <= g.canvas_w and g.pad_y + g.content_h <= g.canvas_h
        else:
            assert 0 <= g.crop_x and g.crop_x + g.crop_w <= w
            assert 0 <= g.crop_y and g.crop_y + g.crop_h <= h


def test_low_resolution_source_avoids_pointless_upscale():
    g = compute_geometry(960, 540, "916", LayoutParams("fit"))
    assert g.canvas_w < 1080  # 1080 폭으로 키우지 않고 낮은 티어 선택
    assert not g.upscaled or g.canvas_w == 720
    assert "업스케일" in g.tier_note or g.tier_note


def test_fit_position_within_padding():
    top = compute_geometry(1920, 1080, "916", LayoutParams("fit", pos_y=0.0))
    bot = compute_geometry(1920, 1080, "916", LayoutParams("fit", pos_y=1.0))
    assert top.pad_y == 0
    assert bot.pad_y + bot.content_h == bot.canvas_h


def test_fill_crop_window_has_target_aspect():
    g = compute_geometry(1920, 1080, "916", LayoutParams("fill", zoom=1.0))
    assert abs(g.crop_w / g.crop_h - 9 / 16) < 0.01
    assert g.crop_h == 1080


def test_profile_grid_default_is_4x5_and_window_is_correct():
    from ics.core.model import Cover

    assert Cover().grid_aspect == "4:5" and Cover.from_dict({}).grid_aspect == "4:5"
    assert Cover.from_dict({"grid_aspect": "3:4"}).grid_aspect == "3:4"  # 저장된 값은 그대로 유지
    from ics.core.layout import profile_grid_window

    x, y, w, h = profile_grid_window(1080, 1920, "4:5", 0.5)
    assert (x, w, h) == (0, 1080, 1350) and y == (1920 - 1350) / 2  # 폭 전체, 1080x1350 가운데
    assert profile_grid_window(1080, 1920, "4:5", 0.0)[1] == 0
    assert profile_grid_window(1080, 1920, "4:5", 1.0)[1] == 1920 - 1350
    x, y, w, h = profile_grid_window(1080, 1920, "3:4", 0.5)
    assert (w, h) == (1080, 1440)
    x, y, w, h = profile_grid_window(1080, 1920, "1:1", 0.5)
    assert (w, h) == (1080, 1080)

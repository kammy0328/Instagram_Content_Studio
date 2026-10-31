"""스틸(JPG/원본 TIFF·PNG) 색 정확도 / 비트 심도 / 크롭 없음 검증."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from ics.core import ffbin
from ics.core.layout import LayoutParams, compute_geometry
from ics.render import engine
from tests.helpers import decode_code, make_frame

N = 17
FIXTURES = ["prores_2398", "untagged_hd", "fullrange_h264", "prores4444_12bit"]


def read_any(path, w, h):
    if str(path).endswith(".tiff"):
        p = ffbin.run([ffbin.ffmpeg(), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"])
        return np.frombuffer(p.stdout, "<u2").reshape(h, w, 3).astype(float) / 257.0
    return np.asarray(Image.open(path).convert("RGB")).astype(float)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_original_still_is_source_resolution_with_correct_color(request, opener, tmp_path, fixture):
    info, idx = opener(request.getfixturevalue(fixture))
    ext, _ = engine.original_still_format(info)
    out = tmp_path / f"orig.{ext}"
    r = engine.render_original_still(info, idx, N, str(out))
    w, h = info.width, info.height
    assert (r["width"], r["height"]) == (w, h)  # 원본 해상도 유지(스케일 없음)
    assert ext == ("tiff" if info.bit_depth > 8 else "png")
    assert r["bit_depth"] == (16 if info.bit_depth > 8 else 8)
    arr = read_any(out, w, h)
    ref = make_frame(N, w, h).astype(float)
    err = np.abs(arr - ref)[10:-10, 10:-10]  # 가장자리 크로마 번짐 제외
    assert err.mean() < 2.0, f"평균 색 오차 {err.mean():.2f}"
    assert decode_code(arr.astype(np.uint8), src_w=w, src_h=h) == N  # 프레임 정확


@pytest.mark.parametrize("fixture", FIXTURES)
def test_feed_jpg_45_fit(request, opener, tmp_path, fixture):
    info, idx = opener(request.getfixturevalue(fixture))
    geo = compute_geometry(info.display_w, info.display_h, "45", LayoutParams("fit"))
    out = tmp_path / "f.jpg"
    engine.render_jpeg_still(info, idx, N, geo, str(out))
    im = Image.open(out)
    assert im.size == (geo.canvas_w, geo.canvas_h) and im.size[0] * 5 == im.size[1] * 4
    assert "icc_profile" in im.info  # sRGB 프로파일 포함
    a = np.asarray(im.convert("RGB"))
    assert (
        decode_code(
            a, geo.pad_x, geo.pad_y, geo.content_w / info.width, geo.content_h / info.height, info.width, info.height
        )
        == N
    )
    assert a[: geo.pad_y - 2].max() <= 6  # 여백은 검정
    mid = geo.pad_y + geo.content_h // 2
    assert a[mid, geo.pad_x + 1 : geo.pad_x + 3].mean() > 200  # 크롭 없음(흰 테두리 유지)


def test_untagged_color_assumption_follows_sd_hd_rule(untagged_hd, untagged_h264, opener):
    hd, _ = opener(untagged_hd)
    sd, _ = opener(untagged_h264)  # 960x540: height<=576 and width<1280
    assert hd.color_assumed and hd.eff_matrix == "bt709" and hd.eff_range == "tv"
    assert sd.color_assumed and sd.eff_matrix == "smpte170m"  # mpv/VLC 와 같은 SD 가정
    assert any(i.code == "color_assumed" for i in hd.issues)  # 사용자에게 가정 사실을 알린다


def test_jpg_color_matches_reference(prores_2398, opener, tmp_path):
    info, idx = opener(prores_2398)
    geo = compute_geometry(info.display_w, info.display_h, "45", LayoutParams("fit"))
    out = tmp_path / "f.jpg"
    engine.render_jpeg_still(info, idx, N, geo, str(out))
    a = np.asarray(Image.open(out).convert("RGB")).astype(float)
    ref = make_frame(N, 960, 540)
    # 콘텐츠 영역을 참조 크기로 줄여서 비교
    crop = Image.fromarray(a.astype(np.uint8)).crop(
        (geo.pad_x, geo.pad_y, geo.pad_x + geo.content_w, geo.pad_y + geo.content_h)
    )
    crop = np.asarray(crop.resize((960, 540), Image.LANCZOS)).astype(float)
    assert np.abs(crop - ref.astype(float))[20:-20, 20:-20].mean() < 3.0

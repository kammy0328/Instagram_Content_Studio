from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from ics.core.layout import LayoutParams, compute_geometry


def image(top=24, bottom=24, left=0, right=0):
    a = np.zeros((180, 320, 3), dtype=np.uint8)
    a[top : 180 - bottom, left : 320 - right] = (80, 130, 180)
    return a


def detect(frames):
    from ics.core.letterbox import detect_bounds

    return detect_bounds(frames)


def test_detect_letterbox_pillarbox_and_windowbox():
    assert detect([image()] * 3) == (0, 24, 320, 132)
    assert detect([image(0, 0, 32, 32)] * 3) == (32, 0, 256, 180)
    assert detect([image(20, 20, 30, 30)] * 3) == (30, 20, 260, 140)


def test_dark_frames_are_ignored_but_full_frame_content_vetoes_crop():
    black = np.zeros((180, 320, 3), np.uint8)
    assert detect([black, image(), image()]) == (0, 24, 320, 132)
    assert detect([black, black]) is None
    assert detect([image(), image(0, 0)]) is None
    assert detect([np.full_like(black, 10)]) is None


def test_variable_bars_keep_all_sampled_content():
    assert detect([image(30, 30), image(20, 20)]) == (0, 20, 320, 140)
    # 한쪽만 어두운 구도는 레터박스로 자동 확정하지 않는다.
    assert detect([image(30, 0)]) is None


@pytest.mark.parametrize("kind", ["916", "45", "orig"])
@pytest.mark.parametrize("mode", ["fit", "fill"])
def test_crop_then_layout_and_toggle_preserve_original(kind, mode):
    lp = LayoutParams(mode=mode, auto_crop=True, source_crop=(0, 120, 1920, 840))
    g = compute_geometry(1920, 1080, kind, lp)
    expected = compute_geometry(1920, 840, kind, LayoutParams(mode=mode))
    assert g.crop_y == expected.crop_y + 120
    assert g.crop_x == expected.crop_x
    assert (g.crop_w, g.crop_h) == (expected.crop_w, expected.crop_h)
    assert (g.canvas_w, g.canvas_h) == (expected.canvas_w, expected.canvas_h)
    assert compute_geometry(1920, 1080, kind, replace(lp, auto_crop=False)) == compute_geometry(
        1920, 1080, kind, LayoutParams(mode=mode)
    )
    assert LayoutParams.from_dict(lp.to_dict()) == lp
    assert not LayoutParams.from_dict({}).auto_crop


def test_invalid_stored_crop_is_ignored():
    for rect in [(0, 0, 9999, 840), (-2, 0, 100, 100), (0, 0, 0, 0)]:
        assert compute_geometry(1920, 1080, "916", LayoutParams(auto_crop=True, source_crop=rect)) == compute_geometry(
            1920, 1080, "916"
        )


@pytest.mark.parametrize("kind", ["916", "45", "orig"])
def test_real_detection_video_and_still_render(tmp_path, opener, kind):
    from ics.core import ffbin
    from ics.core.letterbox import detect_letterbox
    from ics.render import engine
    from tests.helpers import read_frame_rgb

    src = tmp_path / "letterbox.mp4"
    cmd = [
        ffbin.ffmpeg(),
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x80a0c0:s=320x132:r=24:d=1,pad=320:180:0:24:black",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(src),
    ]
    assert ffbin.run(cmd).returncode == 0
    info, idx = opener(src)
    rect = detect_letterbox(info, idx, 0, idx.count)
    assert rect == (0, 24, 320, 132)
    geo = compute_geometry(320, 180, kind, LayoutParams(auto_crop=True, source_crop=rect))
    out = tmp_path / "cropped.mp4"
    result = engine.render_video(info, idx, engine.VideoSpec(3, 9, geo), str(out))
    assert result["frames"] == 6
    arr = read_frame_rgb(out)
    if geo.mode == "fit":
        arr = arr[geo.pad_y : geo.pad_y + geo.content_h, geo.pad_x : geo.pad_x + geo.content_w]
    assert arr[5:-5, 5:-5].mean(axis=2).min() > 70
    still = engine.decode_still_rgb(info, idx, 5, geo)
    if geo.mode == "fit":
        still = still[geo.pad_y : geo.pad_y + geo.content_h, geo.pad_x : geo.pad_x + geo.content_w]
    assert still[5:-5, 5:-5].mean(axis=2).min() > 70


def test_letterbox_keeps_frame_codes_with_blurred_story_background(tmp_path, opener):
    from ics.core import ffbin
    from ics.core.letterbox import detect_letterbox
    from ics.render import engine
    from tests.helpers import all_frame_codes, make_video

    src = make_video(tmp_path / "content.mov", h=420, frames=48, audio=False)
    padded = tmp_path / "padded.mp4"
    assert (
        ffbin.run(
            [
                ffbin.ffmpeg(),
                "-v",
                "error",
                "-y",
                "-i",
                str(src),
                "-vf",
                "pad=960:540:0:60:black",
                "-c:v",
                "libx264",
                "-crf",
                "10",
                str(padded),
            ]
        ).returncode
        == 0
    )
    info, idx = opener(padded)
    rect = detect_letterbox(info, idx, 0, idx.count)
    assert rect == (0, 60, 960, 420)
    g = compute_geometry(960, 540, "916", LayoutParams(auto_crop=True, source_crop=rect))
    out = tmp_path / "story.mp4"
    engine.render_video(info, idx, engine.VideoSpec(7, 19, g, bg_dim=40), str(out))
    assert all_frame_codes(out, 960, 420, g.pad_x, g.pad_y, g.content_w / 960, g.content_h / 420) == list(range(7, 19))


@pytest.mark.parametrize("transform", ["sar", "rotate"])
def test_detect_display_coordinates_with_sar_and_rotation(tmp_path, opener, transform):
    from ics.core import ffbin
    from ics.core.letterbox import detect_letterbox
    from ics.render import engine

    src = tmp_path / "letterbox.mp4"
    vf = "color=c=0x80a0c0:s=320x132:r=24:d=1,pad=320:180:0:24:black"
    if transform == "sar":
        vf += ",setsar=4/3"
    assert (
        ffbin.run(
            [
                ffbin.ffmpeg(),
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                vf,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(src),
            ]
        ).returncode
        == 0
    )
    if transform == "rotate":
        rot = tmp_path / "rotated.mp4"
        assert (
            ffbin.run(
                [
                    ffbin.ffmpeg(),
                    "-v",
                    "error",
                    "-y",
                    "-display_rotation",
                    "90",
                    "-i",
                    str(src),
                    "-c",
                    "copy",
                    str(rot),
                ]
            ).returncode
            == 0
        )
        src = rot
    info, idx = opener(src)
    rect = detect_letterbox(info, idx, 0, idx.count)
    expected = (24, 0, 132, 320) if transform == "rotate" else (0, 24, info.display_w, 132)
    assert rect == expected
    g = compute_geometry(info.display_w, info.display_h, "orig", LayoutParams(auto_crop=True, source_crop=rect))
    arr = engine.decode_still_rgb(info, idx, 3, g)
    assert arr[4:-4, 4:-4].mean(axis=2).min() > 70


@pytest.mark.parametrize("crf", [23, 34])
@pytest.mark.parametrize(
    "bars", [("crop=1920:800:0:140,pad=1920:1080:0:140:black", (0, 140, 1920, 800)), ("crop=1500:700:210:190,pad=1920:1080:210:190:black", (210, 190, 1500, 700))]
)  # fmt: skip
def test_detected_edges_are_exact_even_with_compression_ringing(tmp_path, opener, crf, bars):
    """축소 샘플만으로는 압축 링잉 때문에 경계가 최대 수 픽셀 어긋나 검정 선이 남았다. 원본 해상도 보정 후에는 정확해야 한다."""
    from ics.core import ffbin
    from ics.core.letterbox import detect_letterbox

    vf, ideal = bars
    src = tmp_path / "bars.mp4"
    cmd = [ffbin.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=24,{vf}", "-t", "2"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf), str(src)]
    assert ffbin.run(cmd, timeout=120).returncode == 0
    info, idx = opener(src)
    got = detect_letterbox(info, idx, 0, idx.count)
    assert got is not None
    # 검정이 남지 않아야 하고(바깥쪽 오차 0), 영상이 깎이더라도 2px 이내
    inward = (
        got[0] - ideal[0],
        got[1] - ideal[1],
        ideal[0] + ideal[2] - got[0] - got[2],
        ideal[1] + ideal[3] - got[1] - got[3],
    )
    assert all(0 <= v <= 2 for v in inward), (got, ideal)

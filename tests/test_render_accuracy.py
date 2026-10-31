"""프레임 정확도 / 여백 / 크롭 없음 / 오디오 싱크 / 출력 규격 검증 (프레임 번호가 새겨진 테스트 영상 사용)."""

from __future__ import annotations

import numpy as np
import pytest

from ics.core import ffbin
from ics.core.layout import LayoutParams, compute_geometry
from ics.render import engine
from tests.helpers import all_frame_codes, read_frame_rgb


def render(info, idx, a, b, out, kind="916", layout=None):
    geo = compute_geometry(info.display_w, info.display_h, kind, layout or LayoutParams("fit"))
    res = engine.render_video(info, idx, engine.VideoSpec(a, b, geo), str(out))
    return geo, res


def out_codes(path, geo):
    return all_frame_codes(path, 960, 540, geo.pad_x, geo.pad_y, geo.content_w / 960, geo.content_h / 540)


def click_times(path, thresh=0.5):
    p = ffbin.run(
        [
            ffbin.ffmpeg(),
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-",
        ]
    )
    a = np.frombuffer(p.stdout, "<i2").astype(float) / 32768
    env = np.abs(a) > thresh
    idx = np.flatnonzero(env & ~np.concatenate([[False], env[:-1]]))
    starts: list[int] = []
    for i in idx:
        if not starts or i - starts[-1] > 2400:
            starts.append(i)
    return [s / 48000 for s in starts]


CASES = [("prores_2398", 100, 172), ("prores_2398", 0, 48), ("prores_2398", 200, 240), ("h264_2997", 37, 121)]


@pytest.mark.parametrize("fixture,a,b", CASES)
def test_frame_exact_cut_and_audio_sync(request, opener, tmp_path, fixture, a, b):
    src = request.getfixturevalue(fixture)
    info, idx = opener(src)
    out = tmp_path / "o.mp4"
    geo, res = render(info, idx, a, b, out)
    # 1) 프레임 정확: 시작/끝 포함 모든 프레임 번호가 일치하고 1프레임도 더하거나 빼지 않는다
    assert out_codes(out, geo) == list(range(a, b))
    # 2) 규격
    assert (res["width"], res["height"]) == (geo.canvas_w, geo.canvas_h)
    assert not list(tmp_path.glob("*.part"))
    # 3) 오디오 싱크 (원본은 정수 초마다 1kHz 클릭)
    fps = float(info.fps_frac)
    first = int(np.ceil(a / fps))
    expected = first - a / fps
    if expected < (b - a) / fps - 0.05:
        clicks = click_times(out)
        assert clicks and abs(clicks[0] - expected) < 0.005, (clicks[:2], expected)


def test_output_stream_spec(prores_2398, opener, tmp_path):
    info, idx = opener(prores_2398)
    out = tmp_path / "o.mp4"
    geo, _ = render(info, idx, 10, 70, out)
    pj = ffbin.probe_json(str(out), "-show_streams", "-show_format")
    v = next(s for s in pj["streams"] if s["codec_type"] == "video")
    a = next(s for s in pj["streams"] if s["codec_type"] == "audio")
    assert v["codec_name"] == "h264" and v["profile"] == "High" and v["pix_fmt"] == "yuv420p"
    assert v.get("color_space") == "bt709" and v.get("color_range") == "tv"
    assert a["codec_name"] == "aac" and int(a["sample_rate"]) == 48000
    assert pj["format"]["format_name"].startswith("mov,mp4")
    head = out.read_bytes()[:4096]
    assert head.find(b"moov") != -1 and head.find(b"moov") < (
        head.find(b"mdat") if b"mdat" in head else 10**9
    )  # faststart


def test_fit_has_black_bars_and_no_crop(prores_2398, opener, tmp_path):
    info, idx = opener(prores_2398)
    out = tmp_path / "o.mp4"
    geo, _ = render(info, idx, 20, 60, out)
    fr = read_frame_rgb(out, t=0.3)
    assert geo.pad_y > 0
    assert int(fr[: geo.pad_y - 2].max()) <= 4  # 위 여백: 순수 검정(압축 노이즈 허용)
    assert int(fr[geo.pad_y + geo.content_h + 2 :].max()) <= 4  # 아래 여백
    # 원본 4px 흰 테두리가 콘텐츠 가장자리에 남아 있어야 함 (크롭되지 않았다는 증거)
    mid = geo.pad_y + geo.content_h // 2
    assert fr[mid, geo.pad_x + 1 : geo.pad_x + 3].mean() > 200
    assert fr[mid, geo.pad_x + geo.content_w - 3 : geo.pad_x + geo.content_w - 1].mean() > 200
    assert fr[geo.pad_y + geo.content_h - 3 : geo.pad_y + geo.content_h - 1, geo.canvas_w // 2].mean() > 200


def test_feed_45_video(prores_2398, opener, tmp_path):
    info, idx = opener(prores_2398)
    out = tmp_path / "o.mp4"
    geo, res = render(info, idx, 5, 53, out, kind="45")
    assert geo.canvas_w * 5 == geo.canvas_h * 4
    assert out_codes(out, geo) == list(range(5, 53))


def test_fill_mode_renders_exact_frames(prores_2398, opener, tmp_path):
    info, idx = opener(prores_2398)
    out = tmp_path / "o.mp4"
    geo, res = render(info, idx, 30, 54, out, layout=LayoutParams("fill", zoom=1.0, pos_x=0.5))
    assert (res["width"], res["height"]) == (geo.canvas_w, geo.canvas_h)
    pj = ffbin.probe_json(
        str(out), "-count_packets", "-select_streams", "v:0", "-show_entries", "stream=nb_read_packets"
    )
    assert int(pj["streams"][0]["nb_read_packets"]) == 24  # 정확히 24프레임
    # Fill 은 크롭이므로 가운데 크롭 창 밖의 원본 좌우 흰 테두리는 보이지 않아야 한다
    fr = read_frame_rgb(out, t=0.2)
    assert fr[fr.shape[0] // 2, 1:3].mean() < 200


def test_vfr_source_is_rendered_cfr_with_correct_start(vfr_h264, opener, tmp_path):
    info, idx = opener(vfr_h264)
    assert idx.vfr
    src_codes = all_frame_codes(vfr_h264)
    a, b = 20, 60
    out = tmp_path / "o.mp4"
    geo, res = render(info, idx, a, b, out)
    got = out_codes(out, geo)
    assert got[0] == src_codes[a]  # 시작 프레임이 정확
    assert src_codes[b - 1] in got and src_codes[b] not in got  # 마지막 프레임 포함, 다음 프레임 미포함
    pj = ffbin.probe_json(str(out), "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate,avg_frame_rate")
    s = pj["streams"][0]
    assert s["r_frame_rate"] == s["avg_frame_rate"]  # CFR 출력


def test_proxy_can_never_be_used_for_final_output(prores_2398, opener, tmp_path):
    info, idx = opener(prores_2398)
    geo = compute_geometry(info.display_w, info.display_h, "916")
    fake_proxy = tmp_path / "proxy_abc.mkv"
    fake_proxy.write_bytes(b"x")
    with pytest.raises(engine.RenderError):
        engine.decode_still_rgb(info, idx, 3, geo, src=str(fake_proxy))


def test_render_cancel_leaves_no_partial_file(prores_2398, opener, tmp_path):
    import threading

    info, idx = opener(prores_2398)
    geo = compute_geometry(info.display_w, info.display_h, "916")
    ev = threading.Event()
    ev.set()
    out = tmp_path / "c.mp4"
    with pytest.raises(engine.RenderCancelled):
        engine.render_video(info, idx, engine.VideoSpec(0, 100, geo), str(out), cancel=ev)
    assert not out.exists() and not list(tmp_path.glob("*.part"))

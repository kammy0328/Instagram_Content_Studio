"""STORY 오버레이(로고/제목): 검정 여백에만 그려지는지, 프리셋, 렌더링 필터, 새 프로젝트 기본 구간."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ics.core.layout import LayoutParams, compute_geometry  # noqa: E402
from ics.core.model import EditState, StoryOverlay  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOGO = str(ROOT / "assets" / "logos" / "diap_light.png")


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def ov(**kw) -> StoryOverlay:
    return StoryOverlay(logo_on=True, logo_path=LOGO, text_on=True, text="뮤직비디오 Title", **kw)


def test_items_stay_inside_black_margins_only(qapp):
    from ics.render import overlay

    geo = compute_geometry(1920, 1080, "916", LayoutParams())  # 가로 영상: 위 656 / 아래 656 여백
    res = overlay.layout(ov(), geo, "x")
    assert {i.kind for i in res.items} == {"logo", "text"} and not res.warnings
    top, bottom = overlay.margins(geo)
    for it in res.items:
        r = it.rect
        in_top = r.top() >= 0 and r.bottom() <= top + 0.5
        in_bottom = r.top() >= geo.canvas_h - bottom - 0.5 and r.bottom() <= geo.canvas_h
        assert in_top or in_bottom, (it.kind, r)
        assert not (r.bottom() > geo.pad_y and r.top() < geo.pad_y + geo.content_h)  # 영상 영역과 겹치지 않음


def test_no_margin_means_nothing_is_drawn_with_a_reason(qapp):
    from ics.render import overlay

    vertical = compute_geometry(1080, 1920, "916", LayoutParams())  # 영상이 캔버스를 꽉 채움
    res = overlay.layout(ov(), vertical, "x")
    assert res.empty and len(res.warnings) == 2
    fill = compute_geometry(1920, 1080, "916", LayoutParams(mode="fill"))
    assert overlay.layout(ov(), fill, "x").empty
    assert overlay.render(ov(), fill, "x")[0] is None


def test_empty_text_uses_project_name_and_logo_pos_bottom(qapp):
    from ics.render import overlay

    geo = compute_geometry(1920, 1080, "916", LayoutParams())
    o = StoryOverlay(text_on=True, text="", text_pos="top", logo_on=True, logo_path=LOGO, logo_pos="bottom")
    res = overlay.layout(o, geo, "프로젝트명")
    texts = [i for i in res.items if i.kind == "text"]
    assert texts and texts[0].text == "프로젝트명" and texts[0].rect.bottom() <= geo.pad_y + 0.5
    logo = [i for i in res.items if i.kind == "logo"][0]
    assert logo.rect.top() >= geo.pad_y + geo.content_h - 0.5


def test_rendered_png_is_transparent_over_the_video_area(qapp):
    from ics.render import overlay

    geo = compute_geometry(1920, 1080, "916", LayoutParams())
    img, _ = overlay.render(ov(), geo, "x")
    assert img.width() == 1080 and img.height() == 1920
    for y in range(geo.pad_y, geo.pad_y + geo.content_h, 40):
        for x in range(0, 1080, 60):
            assert (img.pixel(x, y) >> 24) == 0  # 영상 자리는 완전 투명
    assert any(
        (img.pixel(x, y) >> 24) > 0 for x in range(0, 1080, 4) for y in range(1300, 1920, 8)
    )  # 아래 여백에는 로고
    assert any(
        (img.pixel(x, y) >> 24) > 0 for x in range(0, 1080, 4) for y in range(0, 640, 8)
    )  # 위 여백에는 제목이 그려짐 (기본: 제목 위, 로고 아래)


def test_long_text_shrinks_or_warns_instead_of_overflowing(qapp):
    from ics.render import overlay

    geo = compute_geometry(1920, 1080, "916", LayoutParams())
    o = StoryOverlay(text_on=True, text="아주 긴 제목 " * 30, text_size=80)
    res = overlay.layout(o, geo, "x")
    for it in res.items:
        assert it.rect.top() >= 0 and it.rect.bottom() <= geo.pad_y + 0.5  # 기본 위치: 제목은 위 여백


def test_overlay_roundtrips_in_project_state_and_old_projects_are_unchanged():
    st = EditState()
    st.story_overlay = ov(logo_size=55)
    d = st.to_dict()
    back = EditState.from_dict(d)
    assert back.story_overlay.logo_size == 55 and back.story_overlay.text == "뮤직비디오 Title"
    old = EditState.from_dict({"story": {"enabled": False}})  # 오버레이/기본값 필드가 없던 옛 저장본
    assert not old.story_overlay.logo_on and old.defaults_applied is True


def test_logo_presets_save_apply_delete_and_survive_across_projects(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("ICS_DATA_DIR", str(tmp_path / "data"))
    from ics.overlay_presets import LogoPresets
    from ics.ui.settings import Settings

    st = Settings(tmp_path / "s.json")
    pr = LogoPresets(st)
    names = [p.name for p in pr.all()]
    assert any("diap" in n for n in names)  # 기본 제공
    mine = pr.save("내 로고", LOGO, 33, "bottom")
    assert Path(mine.file).is_file()
    pr2 = LogoPresets(Settings(tmp_path / "s.json"))  # 다른 프로젝트/다음 실행에서도 보인다
    got = pr2.get("내 로고")
    assert got and got.size == 33 and got.pos == "bottom"
    builtin = [p for p in pr2.all() if p.builtin][0]
    assert Path(pr2.materialize(builtin)).is_file() and "logo_presets" in pr2.materialize(builtin)
    with pytest.raises(ValueError):
        pr2.save(builtin.name, LOGO, 40, "top")
    pr2.delete("내 로고")
    assert pr2.get("내 로고") is None and not Path(mine.file).exists()


def test_story_render_command_composites_png_and_keeps_audio_and_tags(qapp, prores_2398, tmp_path):
    from ics.core import frameindex, media
    from ics.render import engine, overlay

    info = media.analyze(str(prores_2398))
    idx = frameindex.build_frame_index(info, tmp_path)
    geo = compute_geometry(info.display_w, info.display_h, "916", LayoutParams())
    png, res = overlay.save_png(ov(), geo, "x", tmp_path / "ov.png")
    cmd, meta = engine.build_video_cmd(
        info, idx, engine.VideoSpec(0, min(idx.count, 24), geo, overlay_png=png), str(tmp_path / "o.mp4")
    )
    assert "-filter_complex" in cmd and meta["overlay"] and "[vout]" in cmd
    assert "-vf" not in cmd
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
    from PySide6.QtGui import QImage

    from ics.core import ffbin

    subprocess.run(
        [
            ffbin.ffmpeg(),
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(tmp_path / "o.mp4"),
            "-frames:v",
            "1",
            str(tmp_path / "f.png"),
        ],
        check=True,
    )
    f = QImage(str(tmp_path / "f.png"))
    assert (f.width(), f.height()) == (geo.canvas_w, geo.canvas_h)
    top, _ = overlay.margins(geo)
    if top >= 90:
        y = int(top / 2)
        assert any(QColor(f.pixel(x, y)).lightness() > 100 for x in range(0, f.width(), 3))  # 위 여백에 로고(밝은 픽셀)


def test_new_project_defaults_to_60s_for_story_and_reels_but_not_existing(qapp, prores_2398, tmp_path, monkeypatch):
    monkeypatch.setenv("ICS_DATA_DIR", str(tmp_path / "data"))
    from ics import paths
    from ics.core import frameindex, media
    from ics.core.project import Project
    from ics.core.store import Library

    lib = Library()
    rec = lib.create("P", "", "P", str(prores_2398), str(tmp_path / "out"))
    info = media.analyze(str(prores_2398))
    idx = frameindex.build_frame_index(info, paths.cache_dir(rec.id))
    p = Project(rec, lib)
    p.open()
    p.attach_media(info, idx)
    assert not p.state.story.enabled  # attach 자체는 상태를 바꾸지 않는다
    p.apply_default_ranges()
    for sel in (p.state.story, p.state.reels):
        assert sel.enabled and sel.start == 0 and sel.preset == "60"
        assert p.seconds(sel.start, sel.end) <= 60.0
    end = p.state.story.end
    p.clear_selection("story")
    p.apply_default_ranges()  # 한 번만 적용: 사용자가 지운 선택을 되살리지 않는다
    assert not p.state.story.enabled and end > 0


def test_defaults_title_on_top_small_logo_on_bottom():
    o = StoryOverlay()
    assert o.text_pos == "top" and o.logo_pos == "bottom" and o.logo_size <= 25


def test_story_blur_background_fills_margins_and_dims(qapp, prores_2398, tmp_path):
    from PySide6.QtGui import QImage

    from ics.core import ffbin, frameindex, media
    from ics.render import engine

    info = media.analyze(str(prores_2398))
    idx = frameindex.build_frame_index(info, tmp_path)
    geo = compute_geometry(info.display_w, info.display_h, "916", LayoutParams())
    top, _ = (geo.pad_y, 0)
    assert top > 0

    def frame(dim, name):
        spec = engine.VideoSpec(0, min(idx.count, 12), geo, bg_dim=dim)
        cmd, meta = engine.build_video_cmd(info, idx, spec, str(tmp_path / f"{name}.mp4"))
        assert ("-filter_complex" in cmd) == (dim is not None) and meta["bg_blur"] == (dim is not None)
        r = subprocess.run(cmd, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-600:]
        subprocess.run(
            [
                ffbin.ffmpeg(),
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(tmp_path / f"{name}.mp4"),
                "-frames:v",
                "1",
                str(tmp_path / f"{name}.png"),
            ],
            check=True,
        )
        return QImage(str(tmp_path / f"{name}.png"))

    def margin_light(img):
        ys = range(10, top - 10, 12)
        return sum(QColor(img.pixel(x, y)).lightness() for y in ys for x in range(0, img.width(), 20)) / (
            len(ys) * len(range(0, img.width(), 20))
        )

    plain, bright, dim = frame(None, "plain"), frame(0, "b0"), frame(60, "b60")
    assert (plain.width(), plain.height()) == (geo.canvas_w, geo.canvas_h)
    assert margin_light(plain) < 1  # 배경 없음: 순수 검정 여백
    assert margin_light(bright) > 5  # 흐린 영상 배경이 채움
    assert margin_light(dim) < margin_light(bright) * 0.7  # 어둡게 하면 더 어두움
    mid = geo.pad_y + geo.content_h // 2  # 영상 부분은 배경과 관계없이 그대로
    assert abs(QColor(plain.pixel(540, mid)).lightness() - QColor(dim.pixel(540, mid)).lightness()) < 6

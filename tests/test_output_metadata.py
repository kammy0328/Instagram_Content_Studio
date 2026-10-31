from __future__ import annotations

from ics.core import ffbin
from ics.core.layout import compute_geometry
from ics.render import engine


def test_rendered_video_has_complete_rec709_tags(untagged_hd, opener, tmp_path):
    info, idx = opener(untagged_hd)
    geo = compute_geometry(info.display_w, info.display_h, "45")
    out = tmp_path / "rec709.mp4"
    engine.render_video(info, idx, engine.VideoSpec(0, 4, geo), str(out))
    v = ffbin.probe_json(str(out), "-show_streams")["streams"][0]
    assert {key: v.get(key) for key in ("color_space", "color_primaries", "color_transfer", "color_range")} == {
        "color_space": "bt709",
        "color_primaries": "bt709",
        "color_transfer": "bt709",
        "color_range": "tv",
    }

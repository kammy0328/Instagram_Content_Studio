from __future__ import annotations

import pytest

from ics.core import ffbin
from tests.helpers import make_video


@pytest.mark.parametrize("tags", [True, False])
def test_generated_media_has_requested_color_tags(tmp_path, tags):
    src = make_video(tmp_path / "tags.mp4", frames=2, audio=False, vcodec="h264", tags=tags)
    v = ffbin.probe_json(str(src), "-show_streams")["streams"][0]
    for key in ("color_space", "color_primaries", "color_transfer"):
        assert v.get(key, "unknown") == ("bt709" if tags else "unknown")


def test_generated_hdr_has_pq_tag(tmp_path):
    src = make_video(tmp_path / "pq.mp4", frames=2, audio=False, vcodec="h264", extra=["-color_trc", "smpte2084"])
    assert ffbin.probe_json(str(src), "-show_streams")["streams"][0].get("color_transfer") == "smpte2084"

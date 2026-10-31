"""CI 진단: 설치된 FFmpeg 가 테스트 영상/렌더 결과에 색 태그를 어떻게 기록하는지 출력한다 (실패 원인 분석용)."""

from __future__ import annotations

import sys
import tempfile
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ics.core import ffbin  # noqa: E402
from tests import helpers  # noqa: E402

FIELDS = "stream=codec_name,pix_fmt,color_space,color_primaries,color_transfer,color_range"


def show(label: str, path: Path) -> None:
    pj = ffbin.probe_json(str(path), "-select_streams", "v:0", "-show_entries", FIELDS)
    s = pj["streams"][0]
    print(f"{label:28s}", {k: s.get(k) for k in FIELDS.split("=")[1].split(",")})


print(ffbin.versions())
d = Path(tempfile.mkdtemp(prefix="ics_diag_"))
show("prores tagged", helpers.make_video(d / "a.mov", frames=10, audio=False))
show("h264 tagged", helpers.make_video(d / "b.mp4", vcodec="h264", pix_fmt="yuv420p", frames=10, audio=False))
show(
    "h264 untagged",
    helpers.make_video(d / "c.mp4", vcodec="h264", pix_fmt="yuv420p", frames=10, audio=False, tags=False),
)
show(
    "h264 hdr",
    helpers.make_video(
        d / "d.mp4",
        vcodec="h264",
        pix_fmt="yuv420p",
        frames=10,
        audio=False,
        color_tags=("bt2020nc", "bt2020", "smpte2084"),
    ),
)
show(
    "h264 fps25 tagged",
    helpers.make_video(d / "e.mp4", vcodec="h264", pix_fmt="yuv420p", fps=Fraction(25), frames=10, audio=False),
)

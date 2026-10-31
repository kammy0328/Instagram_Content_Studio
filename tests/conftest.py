"""공용 픽스처. 앱 데이터/캐시는 테스트마다 임시 폴더로 격리한다."""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests import helpers  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("ICS_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("ICS_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(autouse=True)
def no_real_audio_system(monkeypatch):
    """테스트는 실제 오디오 시스템(PipeWire/WASAPI)에 닿지 않는다: 소리 출력 장치가 없는 것으로 본다.
    (장치가 없어도 되는 환경이 CI 이고, 일부 서버 환경은 장치 조회만으로 프로세스가 죽는다.)"""
    try:
        from ics.ui import services
    except ImportError:  # Qt 없이 코어만 시험하는 환경
        return
    monkeypatch.setattr(services, "default_audio_output", lambda: None)
    monkeypatch.setattr(services, "audio_output_names", lambda: [])


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def prores_2398(media_dir) -> Path:
    """ProRes 422 HQ 10bit, 23.976fps, 960x540, 오디오(1kHz 클릭이 매 정수 초)."""
    return helpers.make_video(media_dir / "prores_2398.mov", frames=240)


@pytest.fixture(scope="session")
def h264_2997(media_dir) -> Path:
    return helpers.make_video(
        media_dir / "h264_2997.mp4", fps=Fraction(30000, 1001), vcodec="h264", pix_fmt="yuv420p", frames=150
    )


@pytest.fixture(scope="session")
def vfr_h264(media_dir) -> Path:
    return helpers.make_video(
        media_dir / "vfr.mp4",
        fps=Fraction(25),
        vcodec="h264",
        pix_fmt="yuv420p",
        frames=100,
        vfr_drop=True,
        audio=False,
    )


@pytest.fixture(scope="session")
def untagged_h264(media_dir) -> Path:
    return helpers.make_video(
        media_dir / "untagged_h264.mp4",
        fps=Fraction(25),
        vcodec="h264",
        pix_fmt="yuv420p",
        frames=50,
        tags=False,
        audio=False,
    )


@pytest.fixture(scope="session")
def untagged_hd(media_dir) -> Path:
    """색 태그 없는 HD(1280x720) - 앱은 BT.709 Limited 로 가정해야 한다."""
    return helpers.make_video(
        media_dir / "untagged_hd.mp4",
        w=1280,
        h=720,
        fps=Fraction(25),
        vcodec="h264",
        pix_fmt="yuv420p",
        frames=50,
        tags=False,
        audio=False,
    )


@pytest.fixture(scope="session")
def fullrange_h264(media_dir) -> Path:
    return helpers.make_video(
        media_dir / "fullrange_h264.mp4",
        fps=Fraction(25),
        vcodec="h264",
        pix_fmt="yuv420p",
        frames=50,
        full_range=True,
        audio=False,
    )


@pytest.fixture(scope="session")
def prores4444_12bit(media_dir) -> Path:
    return helpers.make_video(
        media_dir / "prores4444_12bit.mov", vcodec="prores4444", pix_fmt="yuv444p12le", frames=30, audio=False
    )


@pytest.fixture(scope="session")
def hdr_pq(media_dir) -> Path:
    return helpers.make_video(
        media_dir / "hdr_pq.mp4",
        vcodec="h264",
        pix_fmt="yuv420p",
        frames=20,
        audio=False,
        color_tags=("bt2020nc", "bt2020", "smpte2084"),
    )


def open_media(path):
    from ics.core import frameindex, media

    info = media.analyze(str(path))
    return info, frameindex.build_frame_index(info)


@pytest.fixture()
def opener():
    return open_media

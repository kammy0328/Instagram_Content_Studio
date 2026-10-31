from fractions import Fraction

import pytest

from ics.core.timecode import frame_to_tc, parse_rational, snap_fps, tc_to_frame

F2997 = Fraction(30000, 1001)
F2398 = Fraction(24000, 1001)


def test_parse_rational():
    assert parse_rational("24000/1001") == F2398
    assert parse_rational("25") == 25
    assert parse_rational("0/0") is None
    assert parse_rational("N/A") is None
    assert parse_rational("1/0") is None


def test_snap_fps():
    assert snap_fps(Fraction(23976023, 1000000)) == F2398
    assert snap_fps(Fraction(29970030, 1000000)) == F2997
    assert snap_fps(Fraction(12345, 1000)) == Fraction(12345, 1000)


@pytest.mark.parametrize("fps", [F2398, Fraction(25), Fraction(30), Fraction(50), Fraction(60)])
def test_ndf_roundtrip(fps):
    for f in (0, 1, 23, 24, 1439, 86399, 123456):
        assert tc_to_frame(frame_to_tc(f, fps), fps) == f


def test_drop_frame_known_values():
    assert frame_to_tc(1799, F2997, drop=True) == "00:00:59;29"
    assert frame_to_tc(1800, F2997, drop=True) == "00:01:00;02"  # 매분 0,1 프레임 건너뜀
    assert frame_to_tc(17982, F2997, drop=True) == "00:10:00;00"  # 10분 배수는 건너뛰지 않음
    assert frame_to_tc(1800, F2997, drop=False) == "00:01:00:00"


def test_drop_frame_roundtrip():
    for f in range(0, 200000, 997):
        tc = frame_to_tc(f, F2997, drop=True)
        assert ";" in tc
        assert tc_to_frame(tc, F2997) == f


@pytest.mark.parametrize(
    "text,expect",
    [
        ("00:00:01:00", 24),
        ("01:00", 24),  # 앞쪽 생략 = 0 채움
        ("1:02:03:04", (3600 + 2 * 60 + 3) * 24 + 4),
        ("100f", 100),  # 프레임 번호 직접 입력
        ("10203", (1 * 60 + 2) * 24 + 3),  # 숫자만: 오른쪽 정렬 HHMMSSFF (Premiere 방식)
        ("0", 0),
    ],
)
def test_parse_variants(text, expect):
    assert tc_to_frame(text, Fraction(24)) == expect


@pytest.mark.parametrize("bad", ["", "abc", "00:00:60:00", "00:00:00:24", "1:2:3:4:5"])
def test_invalid_input_returns_none(bad):
    assert tc_to_frame(bad, Fraction(24)) is None

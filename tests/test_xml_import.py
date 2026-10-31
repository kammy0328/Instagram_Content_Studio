from fractions import Fraction
from pathlib import Path

import pytest

from ics.core import xml_import as X

DATA = Path(__file__).parent / "data"
F2398 = Fraction(24000, 1001)


def test_premiere_xml_parses_tracks_and_picks_main_track():
    doc = X.parse_file(str(DATA / "sample_premiere.xml"))
    assert doc.format == "xmeml"
    seq = doc.sequences[0]
    assert seq.fps == F2398 and seq.tc_start == 86400
    assert [(t.label, len(t.clips)) for t in seq.tracks] == [("V1", 4), ("V2", 1)]
    assert seq.best_track().label == "V1"  # 컷이 가장 많은/전체를 덮는 트랙


def test_map_to_master_exact_and_no_warnings():
    seq = X.parse_file(str(DATA / "sample_premiere.xml")).sequences[0]
    rep = X.map_to_master(seq, seq.best_track(), F2398, 240, 0, None)
    assert [(a, b) for a, b, _n, _m in rep.cuts] == [(0, 50), (50, 120), (120, 200), (200, 240)]
    assert rep.warnings == []


def test_offset_and_clipping_warning():
    seq = X.parse_file(str(DATA / "sample_premiere.xml")).sequences[0]
    rep = X.map_to_master(seq, seq.best_track(), F2398, 240, 10, None)
    assert rep.cuts[0][0] == 10 and rep.clipped >= 1 and rep.warnings


def test_length_mismatch_is_reported():
    seq = X.parse_file(str(DATA / "sample_premiere.xml")).sequences[0]
    rep = X.map_to_master(seq, seq.best_track(), F2398, 300, 0, None)
    assert any("프레임" in w for w in rep.warnings)


def test_timecode_start_mismatch_is_reported():
    seq = X.parse_file(str(DATA / "sample_premiere.xml")).sequences[0]
    rep = X.map_to_master(seq, seq.best_track(), F2398, 240, 0, 0)
    assert any("TC" in w for w in rep.warnings)


def test_fcpxml_parses_spine_and_warns_about_connected_clips():
    doc = X.parse_file(str(DATA / "sample_fcpxml.fcpxml"))
    assert doc.format == "fcpxml" and doc.sequences[0].fps == F2398
    assert any("spine" in w for w in doc.warnings)
    assert len(doc.sequences[0].best_track().clips) == 2


def test_edl_uses_master_fps(tmp_path):
    p = tmp_path / "a.edl"
    p.write_text(
        "TITLE: T\nFCM: NON-DROP FRAME\n\n"
        "001  AX       V     C        00:00:00:00 00:00:02:00 00:00:00:00 00:00:02:00\n"
        "002  AX       V     C        00:00:00:00 00:00:03:00 00:00:02:00 00:00:05:00\n"
    )
    doc = X.parse_file(str(p), Fraction(25))
    cuts = doc.sequences[0].best_track().clips
    assert [(c.start, c.end) for c in cuts] == [(0, 50), (50, 125)]


def test_broken_file_raises_friendly_error(tmp_path):
    p = tmp_path / "bad.xml"
    p.write_text("<xmeml><sequence>")
    with pytest.raises(X.XmlError):
        X.parse_file(str(p))


def test_suggest_offset():
    seq = X.parse_file(str(DATA / "sample_premiere.xml")).sequences[0]
    assert X.suggest_offset(seq, seq.best_track(), 240) == 0

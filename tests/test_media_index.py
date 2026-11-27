from fractions import Fraction

from ics.core import frameindex, media
from ics.core.media import analyze


def codes(info):
    return {i.code: i.level for i in info.issues}


def test_prores_analysis(prores_2398):
    info = analyze(str(prores_2398))
    assert not info.errors
    assert info.fps_frac == Fraction(24000, 1001)
    assert (info.width, info.height) == (960, 540) and info.bit_depth == 10
    assert info.has_audio and 9.9 < info.duration < 10.2


def test_hdr_is_rejected_with_error(hdr_pq):
    info = analyze(str(hdr_pq))
    assert codes(info).get("hdr") == "error"


def test_missing_file_is_error(tmp_path):
    info = analyze(str(tmp_path / "nope.mp4"))
    assert codes(info).get("missing") == "error"


def test_corrupt_file_is_error(tmp_path):
    p = tmp_path / "bad.mp4"
    p.write_bytes(b"not a video" * 100)
    assert any(i.level == "error" for i in analyze(str(p)).issues)


def test_untagged_color_is_flagged_as_assumed(untagged_h264):
    info = analyze(str(untagged_h264))
    assert codes(info).get("color_assumed") == "warn"
    assert not info.errors


def test_tagged_sources_do_not_warn_about_color(prores_2398, h264_2997):
    for p in (prores_2398, h264_2997):
        assert "color_assumed" not in codes(analyze(str(p)))


def test_vfr_detected_and_warned(vfr_h264):
    info = analyze(str(vfr_h264))
    assert codes(info).get("vfr") == "warn"
    idx = frameindex.build_frame_index(info)
    assert idx.vfr and idx.count == 80  # 100프레임 중 n%5==3 인 20프레임이 빠짐


def test_cfr_index_is_exact_rational(prores_2398):
    info = analyze(str(prores_2398))
    idx = frameindex.build_frame_index(info)
    assert idx.count == 240 and not idx.vfr
    for n in (0, 1, 100, 239):
        assert idx.time_of(n) == Fraction(n * 1001, 24000)
        assert idx.frame_at(idx.time_of(n)) == n
    assert idx.clamp(-5) == 0 and idx.clamp(10_000) == 239


def test_seek_time_is_before_target_frame(h264_2997):
    info = analyze(str(h264_2997))
    idx = frameindex.build_frame_index(info)
    for n in (1, 37, 120):
        assert idx.time_of(n - 1) < idx.seek_time(n) < idx.time_of(n)


def test_frame_index_cache_roundtrip(prores_2398, tmp_path):
    info = analyze(str(prores_2398))
    a = frameindex.build_frame_index(info, tmp_path)
    b = frameindex.build_frame_index(info, tmp_path)  # 캐시에서 로드
    assert a.count == b.count and a.time_of(77) == b.time_of(77)
    assert list(tmp_path.glob("frameindex_*.json"))


def test_media_info_dict_roundtrip(h264_2997):
    info = analyze(str(h264_2997))
    again = media.MediaInfo.from_dict(info.to_dict())
    assert again.fps_frac == info.fps_frac and [i.code for i in again.issues] == [i.code for i in info.issues]


def test_cfr_master_is_indexed_without_reading_the_whole_file(prores_2398, h264_2997, monkeypatch):
    """CFR 파일은 앞/끝 패킷만 읽어 인덱스를 만든다. NAS 의 15GB 4K ProRes 마스터에서 전체 패킷을 읽느라
    '프레임 인덱스 구성 중' 에서 몇 분씩 멈춘 것처럼 보이던 문제."""

    def no_full_scan(*_a, **_k):
        raise AssertionError("CFR 파일에서 전체 패킷을 읽음")

    expected = {}
    for p in (prores_2398, h264_2997):
        expected[p] = frameindex.build_frame_index(analyze(str(p)))  # 비교용 (이때는 어느 쪽이든)
    monkeypatch.setattr(frameindex, "_scan_ticks", no_full_scan)
    for p, ref in expected.items():
        idx = frameindex.build_frame_index(analyze(str(p)))
        assert idx.is_cfr and idx.count == ref.count
        assert all(idx.time_of(n) == ref.time_of(n) for n in (0, 1, ref.count // 2, ref.count - 1))


def test_vfr_still_reads_every_packet_and_reports_progress(vfr_h264, monkeypatch):
    info = analyze(str(vfr_h264))
    msgs = []
    idx = frameindex.build_frame_index(info, progress=msgs.append)
    assert idx.vfr and idx.count == 80
    assert msgs and msgs[0].startswith("프레임 인덱스 구성 중")


def test_fast_index_rejects_a_frame_count_that_does_not_match(prores_2398):
    info = analyze(str(prores_2398))
    info.nb_frames += 3  # 컨테이너 프레임 수와 끝 패킷 번호가 다르면 빠른 길을 쓰지 않는다
    tb = frameindex.parse_rational(info.time_base)
    assert frameindex._fast_cfr_index(info, tb, Fraction(0)) is None

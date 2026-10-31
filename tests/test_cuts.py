import pytest

from ics.core import cuts as C

RANGES = [(0, 40, "a", {}), (40, 90, "b", {}), (90, 150, "c", {}), (150, 200, "d", {})]


def base():
    return C.cuts_from_ranges(RANGES)


def spans(cs):
    return [(c.start, c.end) for c in cs]


def test_merge_contiguous_and_unmerge_roundtrip():
    cs = base()
    m = C.merge(cs, [2, 3])
    assert spans(m) == [(0, 40), (40, 150), (150, 200)]
    assert m[1].merged and len(m[1].parts) == 2
    u = C.unmerge(m, m[1].id)
    assert spans(u) == spans(cs)


def test_merge_rejects_non_contiguous_and_single():
    cs = base()
    with pytest.raises(ValueError):
        C.merge(cs, [1, 3])
    with pytest.raises(ValueError):
        C.merge(cs, [1])


def test_unmerge_keeps_outer_trim():
    cs = base()
    m = C.merge(cs, [2, 3])
    m = C.move_boundary(m, 1, "start", 45, "trim", 200)  # 병합 컷 바깥 경계 보정
    u = C.unmerge(m, m[1].id)
    assert u[0].end == 40 and u[1].start == 45 and u[2].end == 150


def test_roll_moves_both_sides_trim_moves_one():
    cs = base()
    r = C.move_boundary(cs, 1, "end", 100, "roll", 200)
    assert spans(r)[1:3] == [(40, 100), (100, 150)] and not C.find_issues(r)
    t = C.move_boundary(cs, 1, "end", 100, "trim", 200)
    assert spans(t)[1:3] == [(40, 100), (90, 150)]
    assert C.find_issues(t) == [{"index": 1, "kind": "overlap", "frames": 10}]
    g = C.move_boundary(cs, 1, "end", 80, "trim", 200)
    assert C.find_issues(g) == [{"index": 1, "kind": "gap", "frames": 10}]


def test_roll_keeps_min_one_frame():
    cs = base()
    r = C.move_boundary(cs, 1, "end", 10_000, "roll", 200)
    assert r[1].length >= 1 and r[2].length >= 1 and r[1].end == r[2].start


def test_functions_are_pure():
    cs = base()
    C.move_boundary(cs, 1, "end", 100, "roll", 200)
    assert spans(cs) == [(r[0], r[1]) for r in RANGES]


def test_restore_original():
    orig = [{"start": s, "end": e, "name": n, "meta": m} for s, e, n, m in RANGES]
    assert spans(C.restore_original(orig)) == [(r[0], r[1]) for r in RANGES]


def test_cut_index_at_is_half_open():
    cs = base()
    assert C.cut_index_at(cs, 39) == 0 and C.cut_index_at(cs, 40) == 1
    assert C.cut_index_at(cs, 200) is None


def test_serialize_roundtrip():
    cs = C.merge(base(), [1, 2])
    assert spans(C.deserialize(C.serialize(cs))) == spans(cs)


# ------------------------------------------------------------------ 컷 나누기 (재생 헤드 기준)
def test_split_makes_frame_the_first_frame_of_the_right_cut():
    cs = base()
    out = C.split(cs, 60)
    assert spans(out) == [(0, 40), (40, 60), (60, 90), (90, 150), (150, 200)]
    assert out[1].id == cs[1].id and out[2].id not in {c.id for c in cs}
    assert out[1].manual and out[2].manual and not out[0].manual
    assert [(p.start, p.end) for p in out[1].parts] == [(40, 60)]
    assert [(p.start, p.end) for p in out[2].parts] == [(60, 90)]
    assert spans(cs) == [(r[0], r[1]) for r in RANGES]  # 입력은 바뀌지 않는다


def test_split_rejects_first_frame_and_gaps():
    cs = base()
    for frame in (0, 40, 90):  # 컷의 첫 프레임
        with pytest.raises(ValueError):
            C.split(cs, frame)
    with pytest.raises(ValueError):
        C.split(cs, 200)  # 마지막 컷 끝 밖
    gap = [C.Cut(1, 0, 10, parts=[C.Part(1, 0, 10)]), C.Cut(2, 20, 30, parts=[C.Part(2, 20, 30)])]
    with pytest.raises(ValueError):
        C.split(gap, 15)  # 빈 구간
    assert C.split_error(cs, 41) is None and C.split_error(cs, 40) is not None


def test_split_one_frame_cut_halves_are_one_frame_each():
    cs = C.cuts_from_ranges([(0, 2, "a", {})])
    out = C.split(cs, 1)
    assert spans(out) == [(0, 1), (1, 2)]


def test_split_merged_cut_distributes_parts_and_roundtrips_via_unmerge():
    cs = C.merge(base(), [1, 2, 3])  # (0,150) = 파트 (0,40) (40,90) (90,150)
    out = C.split(cs, 90)  # 파트 경계에서 나눔
    assert spans(out) == [(0, 90), (90, 150), (150, 200)]
    assert out[0].merged and len(out[0].parts) == 2 and not out[1].merged
    out2 = C.split(cs, 60)  # 파트 안쪽에서 나눔: (40,90) 이 둘로 쪼개진다
    assert spans(out2) == [(0, 60), (60, 150), (150, 200)]
    assert [(p.start, p.end) for p in out2[0].parts] == [(0, 40), (40, 60)]
    assert [(p.start, p.end) for p in out2[1].parts] == [(60, 90), (90, 150)]
    part_ids = [p.id for c in out2 for p in c.parts]
    assert len(part_ids) == len(set(part_ids))
    un = C.unmerge(out2, out2[0].id)
    assert spans(un) == [(0, 40), (40, 60), (60, 150), (150, 200)]


def test_split_merged_cut_with_trimmed_outer_boundary_keeps_both_sides_valid():
    m = C.merge(base(), [1, 2])  # (0,90) 파트 (0,40) (40,90)
    m = C.move_boundary(m, 0, "end", 120, "trim", 200)  # 파트 범위 밖으로 바깥 경계를 늘림 -> (0,120)
    out = C.split(m, 100)
    assert spans(out)[:2] == [(0, 100), (100, 120)]
    assert all(c.parts for c in out)  # 파트가 비어 있는 컷이 없어야 한다
    assert (out[1].parts[0].start, out[1].parts[0].end) == (100, 120)


def test_split_ids_never_collide_after_repeated_splits_and_merges():
    cs = base()
    for f in (10, 20, 30, 50, 70):
        cs = C.split(cs, f)
    ids = [c.id for c in cs]
    assert len(ids) == len(set(ids))
    m = C.merge(cs, [c.id for c in cs[:4]])
    u = C.unmerge(m, m[0].id)
    assert spans(u) == spans(cs)
    assert len({c.id for c in u}) == len(u)


def test_merge_error_reports_same_reasons_as_merge():
    cs = base()
    assert C.merge_error(cs, [1]) and C.merge_error(cs, [1, 3]) and C.merge_error(cs, [2, 3]) is None

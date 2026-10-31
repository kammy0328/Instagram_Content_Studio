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

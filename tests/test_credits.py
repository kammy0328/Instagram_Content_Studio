from ics.core import credits as C


def parse(raw, **kw):
    return C.parse_credits(raw, **kw)


def test_standard_format_and_handle_extraction():
    r = parse("Director: Mingi Kang @aarch.film")[0]
    assert r.status == "ok"
    assert r.text == "Director | Mingi Kang (@aarch.film)"


def test_url_to_handle_and_multiple_people():
    r = parse("Assistant Director - Yeyoung Kim (instagram.com/mimyo_yeye), Seohyun Son")[0]
    assert r.text == "Assistant Director | Yeyoung Kim (@mimyo_yeye), Seohyun Son"


def test_invalid_handle_goes_to_review_and_keeps_raw():
    raw = "Editor | Foo @bad-id!"
    r = parse(raw)[0]
    assert r.status == "review" and r.text == raw


def test_ambiguous_ampersand_is_not_auto_split():
    raw = "Colorist: A & B"
    r = parse(raw)[0]
    assert r.status == "review" and r.text == raw  # 자동 확정 없이 원문 유지
    assert r.candidate_text == "Colorist | A, B"  # 제안만 제공


def test_line_without_delimiter_is_review():
    r = parse("그냥 이름만 있는 줄")[0]
    assert r.status == "review" and r.text == "그냥 이름만 있는 줄"


def test_same_role_lines_are_merged():
    rows = parse("Director: A (@a)\nDirector: B (@b)")
    assert len(rows) == 1 and rows[0].text == "Director | A (@a), B (@b)"


def test_dictionary_unique_match_applies_automatically():
    look = lambda name: [("Seohyun Son", "sh_son")] if name == "Seohyun Son" else []  # noqa: E731
    r = parse("Editor: Seohyun Son", lookup=look)[0]
    assert r.text == "Editor | Seohyun Son (@sh_son)"


def test_dictionary_homonyms_are_never_auto_confirmed():
    look = lambda name: [("Kim Min", "kim_a"), ("Kim Min", "kim_b")]  # noqa: E731
    r = parse("Editor: Kim Min", lookup=look)[0]
    p = r.people[0]
    assert p.handle is None and p.status == "candidates" and sorted(p.candidates) == ["kim_a", "kim_b"]
    assert "@" not in r.text


def test_dictionary_auto_apply_can_be_disabled():
    look = lambda name: [("A", "a_id")]  # noqa: E731
    r = parse("Editor: A", lookup=look, auto_apply_unique=False)[0]
    assert r.people[0].handle is None


def test_headers_and_separators_are_ignored():
    rows = parse("[CREDITS]\n-----\nDirector: A")
    assert len(rows) == 1


def test_rows_to_lines_and_review_count():
    rows = parse("Director: A\nbad line")
    assert C.count_review(rows) == 1
    assert C.rows_to_lines(rows)[0] == "Director | A"


def test_similar_domain_does_not_create_instagram_handle():
    row = parse("Director | A https://notinstagram.com/someone")[0]
    assert all(p.handle is None for p in row.people)


def test_ampersand_with_only_one_handle_remains_review():
    raw = "Production | A & B (@studio)"
    row = parse(raw)[0]
    assert row.status == "review" and row.text == raw

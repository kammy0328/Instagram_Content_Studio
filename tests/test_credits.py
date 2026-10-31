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


# ---- 실제 크레딧 붙여넣기에서 나온 표기 흔들림 (구분자 모양 문자, 그룹 제목, 소속 괄호) ----


def test_lookalike_l_and_I_are_treated_as_pipe_with_note():
    for raw, text in [
        ("GENERATOR l Foo Bar", "GENERATOR | Foo Bar"),
        ("PRODUCTION SOUND l Foo Bar, Baz Qux", "PRODUCTION SOUND | Foo Bar, Baz Qux"),
        ("MATCH MOVE I 75mm studio", "MATCH MOVE | 75mm studio"),
        ("ART-TEAM l Foo Bar", "ART-TEAM | Foo Bar"),  # 'ART' 로 잘못 쪼개지지 않는다
        ("3D VFX l STUDIO ABC", "3D VFX | STUDIO ABC"),
    ]:
        r = parse(raw)[0]
        assert r.status == "ok" and r.text == text, (raw, r.text, r.reasons)
        assert any("구분자" in x for x in r.reasons)  # 해석했다는 사실을 비고에 남긴다


def test_pipe_lookalike_characters_are_always_pipes():
    r = parse("LOCATION TEAMㅣGRAY LOCATION")[0]
    assert r.status == "ok" and r.text == "LOCATION TEAM | GRAY LOCATION"
    assert parse("PRODUCTION DESIGN｜Mu:E")[0].text == "PRODUCTION DESIGN | Mu:E"
    assert parse("PRODUCTION DESIGN l Mu:E")[0].text == "PRODUCTION DESIGN | Mu:E"  # 뒤의 ':' 보다 앞의 'l' 이 구분자


def test_lookalike_does_not_swallow_normal_text():
    r = parse("Thanks I love you all")[0]  # 대명사 I: 역할처럼 보이지 않으므로 그대로 검토 대상
    assert r.status == "review" and r.text == "Thanks I love you all"
    r = parse("Director | Foo I Bar")[0]  # 이미 '|' 가 있으면 건드리지 않는다
    assert r.text == "Director | Foo I Bar"


def test_group_header_is_not_an_error_and_groups_do_not_merge():
    raw = (
        "LIGHTING CREW (DAY 1)\n1st | A One\n2nd | B Two\n\n"
        "LIGHTING CREW (DAY 2,3)\n1st |  C Three\n2nd | B Two, D Four\n"
    )
    rows = parse(raw)
    assert [r.status for r in rows] == ["ok"] * 6 and not C.count_review(rows)
    assert [r.text for r in rows] == [
        "LIGHTING CREW (DAY 1)",
        "1st | A One",
        "2nd | B Two",
        "LIGHTING CREW (DAY 2,3)",
        "1st | C Three",
        "2nd | B Two, D Four",
    ]
    assert "그룹 제목" in rows[0].reasons[0]


def test_line_without_delimiter_before_non_ordinal_line_is_still_review():
    rows = parse("SOME TITLE\nDirector | A")
    assert rows[0].status == "review" and rows[1].status == "ok"


def test_same_role_is_merged_only_inside_a_block():
    rows = parse("VFX Supervisor | A\nVFX SUPERVISOR | B\n\nVFX Supervisor | C")
    assert [r.text for r in rows] == ["VFX Supervisor | A, B", "VFX Supervisor | C"]  # 빈 줄 너머는 소속이 다를 수 있다


def test_affiliation_parentheses_are_preserved():
    assert parse("EDIT | YVNG WING (IDIOTS), NOHP (IDIOTS)")[0].text == "EDIT | YVNG WING (IDIOTS), NOHP (IDIOTS)"
    assert parse("Director | Foo (Mu:E) @foo_id")[0].text == "Director | Foo (Mu:E) (@foo_id)"
    r = parse("ART DIRECTOR | Jinsil & Bona (Mu:E)")[0]
    assert r.status == "review" and r.candidate_text == "Art Director | Jinsil, Bona (Mu:E)"
    assert parse("Editor | Foo (@foo_id)")[0].text == "Editor | Foo (@foo_id)"  # 핸들 괄호는 기존처럼
    assert parse("Editor | Foo (broken")[0].text == "Editor | Foo broken"  # 짝이 안 맞는 괄호만 제거

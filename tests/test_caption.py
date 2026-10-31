from ics.core.caption import count_all, count_chars, count_hashtags, split_caption


def test_counting_modes_differ_for_emoji():
    t = "A😀👨‍👩‍👧B"
    c = count_all(t)
    assert c["grapheme"] == 4  # A, 😀, 가족(ZWJ), B
    assert c["codepoint"] == 1 + 1 + 5 + 1
    assert c["utf16"] == 1 + 2 + (2 + 1 + 2 + 1 + 2) + 1
    assert c["utf16"] >= c["codepoint"] >= c["grapheme"]  # 기본(utf16)이 가장 보수적


def test_newlines_count_as_one_char():
    assert count_chars("a\r\nb\rc\nd") == 7


def test_hashtag_count():
    assert count_hashtags("#음악 #film text#notatag #a_b") == 3


def test_credits_stay_in_caption_when_they_fit():
    r = split_caption("본문", ["Director | A", "Editor | B"])
    assert r.comments == [] and "Director | A" in r.caption and r.caption.startswith("본문")


def test_split_by_row_never_cuts_a_row():
    rows = [f"Role{i:02d} | " + "가" * 60 for i in range(30)]
    r = split_caption("본문", rows, limit=500)
    assert r.caption_count <= 500 and all(n <= 500 for n in r.comment_counts)
    joined = "\n".join([r.caption] + r.comments)
    for row in rows:
        assert row in joined  # 행 단위 보존(중간 절단 없음)
    assert len(r.comments) >= 1


def test_credit_order_is_preserved_across_caption_and_comments():
    rows = [f"R{i:02d} | " + "x" * 80 for i in range(20)]
    r = split_caption("body", rows, limit=400)
    out = [ln for blk in [r.caption] + r.comments for ln in blk.split("\n") if ln.startswith("R")]
    assert out == rows


def test_overlong_body_warns_but_is_not_auto_split():
    r = split_caption("x" * 300, ["A | B"], limit=200)
    assert any("본문" in w for w in r.warnings)
    assert r.caption.startswith("x" * 300)


def test_too_many_hashtags_warns():
    r = split_caption(" ".join(f"#t{i}" for i in range(31)), [])
    assert any("해시태그" in w for w in r.warnings)


def test_credits_before_body():
    r = split_caption("본문", ["Director | A"], credits_position="before")
    assert r.caption.index("Director") < r.caption.index("본문")

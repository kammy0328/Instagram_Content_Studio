"""캡션 글자 수 계산 및 본문/댓글 분리 (크레딧 행 단위, 역할/인물명 중간 절단 금지)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import regex

DEFAULT_LIMIT = 2200
HASHTAG_LIMIT = 30
_GRAPHEME = regex.compile(r"\X")
_HASHTAG = re.compile(r"(?<![\w#])#[\w가-힣]+", re.UNICODE)


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def count_chars(text: str, mode: str = "utf16") -> int:
    """mode:
      'utf16'     : UTF-16 코드 유닛 (이모지 = 2 이상). 가장 보수적 - 어떤 기준으로도 한도를 넘지 않음 [기본]
      'codepoint' : 유니코드 코드포인트 수
      'grapheme'  : 사용자 인지 글자(이모지 ZWJ 조합 = 1)
    줄바꿈은 1자로 센다."""
    t = normalize_newlines(text)
    if mode == "codepoint":
        return len(t)
    if mode == "grapheme":
        return len(_GRAPHEME.findall(t))
    return len(t.encode("utf-16-le")) // 2


def count_all(text: str) -> dict[str, int]:
    return {m: count_chars(text, m) for m in ("utf16", "codepoint", "grapheme")}


def count_hashtags(text: str) -> int:
    return len(_HASHTAG.findall(text))


@dataclass
class SplitResult:
    caption: str
    comments: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    caption_count: int = 0
    comment_counts: list[int] = field(default_factory=list)


def _split_long_row(row: str, limit: int, mode: str) -> list[str]:
    """한 줄이 한도를 넘는 극단적 경우: 인물 단위(', ')로 나눈다 (이름 중간 절단 금지)."""
    if " | " not in row:
        return [row]
    role, people = row.split(" | ", 1)
    parts = re.split(r",\s+(?![^()]*\))", people)
    out, cur = [], ""
    for p in parts:
        cand = (cur + ", " + p) if cur else p
        if count_chars(f"{role} | {cand}", mode) <= limit or not cur:
            cur = cand
        else:
            out.append(f"{role} | {cur}")
            cur = p
    if cur:
        out.append(f"{role} | {cur}")
    return out


def split_caption(
    body: str, credit_lines: list[str], limit: int = DEFAULT_LIMIT, mode: str = "utf16", credits_position: str = "after"
) -> SplitResult:
    body = normalize_newlines(body or "").strip("\n")
    warns: list[str] = []
    rows: list[str] = []
    for r in credit_lines:
        r = normalize_newlines(r).strip()
        if not r:
            continue
        if count_chars(r, mode) > limit:
            warns.append("한 줄 크레딧이 한도를 넘어 인물 단위로 나눴습니다.")
            rows.extend(_split_long_row(r, limit, mode))
        else:
            rows.append(r)
    if count_chars(body, mode) > limit:
        warns.append(
            f"본문만으로 {count_chars(body, mode)}자라 {limit}자를 넘습니다. 본문은 자동으로 나누지 않습니다 - 직접 줄여주세요."
        )

    caption_rows: list[str] = []
    rest = list(rows)
    if credits_position == "before":
        head = ""
        tail = body
    else:
        head = body
        tail = ""

    def build(cr: list[str]) -> str:
        block = "\n".join(cr)
        if credits_position == "before":
            parts = [p for p in (block, tail) if p]
        else:
            parts = [p for p in (head, block) if p]
        return "\n\n".join(parts)

    while rest:
        trial = caption_rows + [rest[0]]
        if count_chars(build(trial), mode) <= limit:
            caption_rows.append(rest.pop(0))
        else:
            break
    caption = build(caption_rows)

    comments: list[str] = []
    cur: list[str] = []
    for r in rest:
        if cur and count_chars("\n".join(cur + [r]), mode) > limit:
            comments.append("\n".join(cur))
            cur = []
        cur.append(r)
    if cur:
        comments.append("\n".join(cur))
    tags = count_hashtags(caption)
    if tags > HASHTAG_LIMIT:
        warns.append(f"해시태그가 {tags}개입니다 (인스타그램 한도 {HASHTAG_LIMIT}개).")
    return SplitResult(caption, comments, warns, count_chars(caption, mode), [count_chars(c, mode) for c in comments])

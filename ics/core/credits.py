"""크레딧 자유 텍스트 파싱/정규화.  표준 형식: ROLE | NAME (@instagram_id)  (ID 없으면 괄호 생략)

원칙: 파싱이 불확실한 줄은 추측으로 변환하지 않고 '검토 필요'로 표시하며 원문을 그대로 보존한다."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable

HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
AT_RE = re.compile(r"@([^\s,;()\[\]&|<>/]+)")
URL_RE = re.compile(r"(?<![\w.-])(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9._]+)/?(?:\?[^\s)]*)?", re.I)
RESERVED_IG_PATHS = {"p", "reel", "reels", "stories", "explore", "accounts", "tv", "direct", "about"}

# 역할 어휘(방향 판정용). 사전에 없는 역할도 허용되지만, 약한 구분자(-, /)에서는 검토 대상이 된다.
ROLE_CANON = {
    "production": "Production",
    "producer": "Producer",
    "executive producer": "Executive Producer",
    "line producer": "Line Producer",
    "production manager": "Production Manager",
    "pm": "PM",
    "director": "Director",
    "assistant director": "Assistant Director",
    "ad": "AD",
    "dop": "DOP",
    "dp": "DP",
    "director of photography": "Director of Photography",
    "cinematographer": "Cinematographer",
    "camera": "Camera",
    "camera operator": "Camera Operator",
    "focus puller": "Focus Puller",
    "gaffer": "Gaffer",
    "lighting": "Lighting",
    "lighting director": "Lighting Director",
    "grip": "Grip",
    "key grip": "Key Grip",
    "editor": "Editor",
    "editing": "Editing",
    "colorist": "Colorist",
    "color": "Color",
    "color grading": "Color Grading",
    "dit": "DIT",
    "vfx": "VFX",
    "cg": "CG",
    "post production": "Post Production",
    "post supervisor": "Post Supervisor",
    "retouch": "Retouch",
    "beauty retouch": "Beauty Retouch",
    "choreographer": "Choreographer",
    "choreography": "Choreography",
    "stylist": "Stylist",
    "styling": "Styling",
    "hair": "Hair",
    "makeup": "Makeup",
    "make up": "Makeup",
    "hair & makeup": "Hair & Makeup",
    "art director": "Art Director",
    "art": "Art",
    "set design": "Set Design",
    "production designer": "Production Designer",
    "costume": "Costume",
    "sound": "Sound",
    "mixing": "Mixing",
    "music": "Music",
    "photographer": "Photographer",
    "making": "Making",
    "making film": "Making Film",
    "special thanks": "Special Thanks",
    "label": "Label",
    "agency": "Agency",
    "location": "Location",
    "drone": "Drone",
    "data manager": "Data Manager",
    "script": "Script",
    "pa": "PA",
    # 한국어
    "감독": "감독",
    "연출": "연출",
    "조감독": "조감독",
    "촬영": "촬영",
    "촬영감독": "촬영감독",
    "조명": "조명",
    "조명감독": "조명감독",
    "편집": "편집",
    "색보정": "색보정",
    "컬러": "컬러",
    "후반": "후반",
    "후반작업": "후반작업",
    "프로듀서": "프로듀서",
    "제작": "제작",
    "제작사": "제작사",
    "기획": "기획",
    "안무": "안무",
    "스타일리스트": "스타일리스트",
    "스타일": "스타일",
    "헤어": "헤어",
    "메이크업": "메이크업",
    "헤메": "헤메",
    "미술": "미술",
    "의상": "의상",
    "음악": "음악",
    "녹음": "녹음",
    "믹싱": "믹싱",
    "보정": "보정",
    "그립": "그립",
    "스틸": "스틸",
    "메이킹": "메이킹",
}
_HEADERS = {"credit", "credits", "크레딧", "staff", "스태프", "스탭", "cast", "출연", "credit list", "크레딧 정리"}
_PEOPLE_SPLIT = re.compile(r"\s*(?:,|;|、|&|·|ㆍ|\+|/|\band\b|\s+및\s+)\s*", re.I)


@dataclass
class Person:
    name: str
    handle: str | None = None
    status: str = "ok"  # ok | dict(사전 자동 적용) | candidates(동명이인 후보) | review
    note: str = ""
    candidates: list[str] = field(default_factory=list)  # 동명이인 후보 핸들


@dataclass
class CreditRow:
    role: str
    people: list[Person] = field(default_factory=list)
    status: str = "ok"  # ok | review | edited
    raw_lines: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    text: str = ""  # 최종 출력 줄(수동 수정 가능)
    candidate_text: str = ""  # review 행의 제안(적용 전)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(
            role=d.get("role", ""),
            people=[Person(**p) for p in d.get("people", [])],
            status=d.get("status", "ok"),
            raw_lines=list(d.get("raw_lines", [])),
            reasons=list(d.get("reasons", [])),
            text=d.get("text", ""),
            candidate_text=d.get("candidate_text", ""),
        )


def format_person(p: Person) -> str:
    return f"{p.name} (@{p.handle})" if p.handle else p.name


def format_row(role: str, people: list[Person]) -> str:
    return f"{role} | " + ", ".join(format_person(p) for p in people)


def _norm_role_key(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower().replace("&amp;", "&"))


def canon_role(role: str) -> tuple[str, bool]:
    """(표준화된 역할, 사전에 있는 역할인지)"""
    r = re.sub(r"\s+", " ", role.strip().strip("[]()<>*-–—·•:").strip())
    k = _norm_role_key(r)
    if k in ROLE_CANON:
        return ROLE_CANON[k], True
    return r, False


def _normalize_line(line: str) -> str:
    t = (
        line.replace("：", ":")
        .replace("｜", "|")
        .replace("＠", "@")
        .replace(" ", " ")
        .replace("​", "")
        .replace("﻿", "")
    )
    t = re.sub(r"^\s*[-•·*▪●○■□▶▷>\d]+[.)]?\s+(?=\S)", "", t) if re.match(r"^\s*[•·*▪●○■□▶▷]", t) else t
    return t.strip()


def _split_role(line: str) -> tuple[str, str, str, bool] | None:
    """(left, right, delimiter, strong) 또는 None. URL 속 ':' '/' 는 구분자로 보지 않는다."""
    masked = URL_RE.sub(lambda m: "§" * len(m.group(0)), line)
    for d, strong in (("|", True), ("\t", True), (":", True)):
        i = masked.find(d)
        if i > 0:
            return line[:i].strip(), line[i + 1 :].strip(), d, strong
    m = re.search(r"\s[-–—]\s", masked)
    if m:
        return line[: m.start()].strip(), line[m.end() :].strip(), "-", False
    m = re.search(r"\s/\s", masked)
    if m:
        return line[: m.start()].strip(), line[m.end() :].strip(), "/", False
    m = re.search(r"\s{2,}", masked)
    if m:
        return line[: m.start()].strip(), line[m.end() :].strip(), "  ", False
    for d in ("-", "–", "—"):
        i = masked.find(d)
        if i > 0 and canon_role(line[:i])[1]:
            return line[:i].strip(), line[i + 1 :].strip(), d, False
    return None


def _urls_to_handles(s: str) -> str:
    """instagram.com 링크를 '@id' 표기로 바꿔 이름 분리 시 URL 내부 기호가 영향을 주지 않게 한다."""

    def rep(m):
        h = m.group(1)
        return f" @!{h} " if h.lower() in RESERVED_IG_PATHS else f" @{h.rstrip('.')} "

    return URL_RE.sub(rep, s)


_AMBIG_SEPS = {"&", "and", "+", "및", "·", "ㆍ"}


def _split_outside_parens(s: str, role_delim: str) -> list[tuple[str, str]]:
    """괄호 밖에서 이름을 분리. 반환: [(chunk, 앞쪽 구분자)]"""
    chunks: list[tuple[str, str]] = []
    buf: list[str] = []
    depth = 0
    i = 0
    prev_sep = ""
    while i < len(s):
        ch = s[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if depth == 0 and (ch in ",;、&·ㆍ+/ " or s[i : i + 3].lower() == "and" or s[i : i + 2] == "및"):
            m = _PEOPLE_SPLIT.match(s, i)
            if m and m.end() > m.start() and m.group(0).strip():
                chunks.append(("".join(buf), prev_sep))
                buf = []
                prev_sep = m.group(0).strip().lower()
                i = m.end()
                continue
        buf.append(ch)
        i += 1
    chunks.append(("".join(buf), prev_sep))
    return [(c.strip(), sep) for c, sep in chunks if c.strip()]


def _parse_chunk(chunk: str) -> Person | None:
    handles: list[str] = []
    problems: list[str] = []

    def take_at(m):
        h = m.group(1).rstrip(".,!?:;")
        if h.startswith("!"):
            problems.append(f"프로필이 아닌 인스타그램 링크(instagram.com/{h[1:]})")
        else:
            handles.append(h)
        return " "

    rest = AT_RE.sub(take_at, chunk)
    rest = re.sub(r"\(\s*\)|\[\s*\]", " ", rest)
    rest = re.sub(r"(?i)\b(?:ig|insta(?:gram)?)\s*[:：]?\s*(?=$|\s)", " ", rest)
    name = re.sub(r"[()\[\]]", " ", rest)
    name = re.sub(r"\s+", " ", name).strip(" ,;:-–—·|")
    if not name and not handles:
        return None
    note = "; ".join(problems)
    status = "ok"
    if len(handles) > 1:
        status, note = "review", (note + "; " if note else "") + "한 사람에 인스타그램 ID가 2개 이상"
    handle = handles[0] if handles else None
    if handle is not None and (not HANDLE_RE.match(handle) or set(handle) <= {"."}):
        status, note = "review", (note + "; " if note else "") + f"올바르지 않은 인스타그램 ID 형식({handle})"
    if not name and handle:
        status, note = "review", (note + "; " if note else "") + "이름 없이 ID만 있음"
        name = ""
    if problems and status == "ok":
        status = "review"
    return Person(name=name, handle=handle, status=status, note=note)


def parse_credits(
    raw: str, lookup: Callable[[str], list[tuple[str, str]]] | None = None, auto_apply_unique: bool = True
) -> list[CreditRow]:
    """자유 텍스트 -> CreditRow 목록.
    lookup(name) -> [(name, handle), ...] : 로컬 ID 사전 조회 (없으면 None)."""
    rows: list[CreditRow] = []
    by_role: dict[str, CreditRow] = {}
    for original in raw.splitlines():
        line = _normalize_line(original)
        if not line:
            continue
        if _norm_role_key(line.strip("[]():-=*# ")) in _HEADERS or re.fullmatch(r"[-=_*~#\s]{3,}", line):
            continue
        row = _parse_line(line, original)
        if row.status == "ok":
            key = row.role.casefold()
            if key in by_role and by_role[key].status == "ok":
                tgt = by_role[key]
                existing = {(p.name.casefold(), (p.handle or "").casefold()) for p in tgt.people}
                for p in row.people:
                    if (p.name.casefold(), (p.handle or "").casefold()) not in existing:
                        tgt.people.append(p)
                tgt.raw_lines.append(original)
                tgt.reasons.append("같은 역할의 여러 줄을 한 줄로 합쳤습니다")
                continue
            by_role[key] = row
        rows.append(row)
    for r in rows:
        if lookup is not None:
            _apply_dictionary(r, lookup, auto_apply_unique)
        finalize_row(r)
    return rows


def _parse_line(line: str, original: str) -> CreditRow:
    sp = _split_role(line)
    if sp is None:
        return CreditRow(
            role="",
            people=[],
            status="review",
            raw_lines=[original],
            text=original.strip(),
            reasons=["역할 구분자(| : - /)를 찾지 못했습니다"],
        )
    left, right, delim, strong = sp
    reasons: list[str] = []
    status = "ok"
    l_role, l_known = canon_role(left)
    r_role, r_known = canon_role(right)
    if not l_known and r_known and not re.search(r"[@/]", right):
        # 'NAME | ROLE' 순서로 보임 -> 추측하지 않고 검토 대상
        status = "review"
        reasons.append(f"'이름 {delim} 역할' 순서로 보입니다 (역할 '{r_role}'). 확인이 필요합니다")
        role, people_text = r_role, left
    else:
        role, people_text = l_role, right
        if not l_known and not strong:
            status = "review"
            reasons.append(f"역할 '{l_role}' 이 알려진 역할이 아니고 구분자('{delim}')가 모호합니다")
    if not role:
        status = "review"
        reasons.append("역할이 비어 있습니다")
    people: list[Person] = []
    ambiguous = False
    for chunk, sep in _split_outside_parens(_urls_to_handles(people_text), delim):
        p = _parse_chunk(chunk)
        if p is not None:
            people.append(p)
            if sep in _AMBIG_SEPS and (not p.handle or len(people) < 2 or not people[-2].handle):
                ambiguous = True
    if ambiguous:
        status = "review"
        reasons.append("'&/and' 등으로 이름을 나눴지만 상호명 일부인지 두 사람인지 불확실합니다")
    if not people:
        status = "review"
        reasons.append("이름을 찾지 못했습니다")
    for p in people:
        if p.status == "review":
            status = "review"
            reasons.append(f"{p.name or '(이름없음)'}: {p.note}")
    row = CreditRow(role=role, people=people, status=status, raw_lines=[original], reasons=reasons)
    if status == "review":
        row.text = original.strip()  # 원문 그대로 보존
        row.candidate_text = (
            format_row(role, people) if people and role and all(p.name and p.status != "review" for p in people) else ""
        )
    return row


def _apply_dictionary(row: CreditRow, lookup, auto_unique: bool) -> None:
    if row.status != "ok":
        return
    for p in row.people:
        if p.handle or not p.name:
            continue
        found = lookup(p.name) or []
        handles = sorted({h for _, h in found})
        if len(handles) == 1 and auto_unique:
            p.handle, p.status, p.note = handles[0], "dict", "ID 사전에서 자동 적용"
        elif len(handles) > 1:
            p.status, p.candidates = "candidates", handles
            p.note = "동명이인 후보가 있어 자동 확정하지 않았습니다"


def finalize_row(row: CreditRow) -> None:
    """상태에 맞게 최종 텍스트 생성. review/edited 행은 사용자가 확정/수정하기 전까지 원문 유지."""
    if row.status == "ok":
        row.text = format_row(row.role, row.people)


def accept_candidate(row: CreditRow) -> None:
    """검토 행에서 제안 변환을 사용자가 수락."""
    if row.candidate_text:
        row.text = row.candidate_text
        row.status = "edited"
        row.reasons.append("사용자가 제안 변환을 수락함")


def set_manual_text(row: CreditRow, text: str) -> None:
    row.text = text.strip()
    row.status = "edited"


def rows_to_lines(rows: list[CreditRow]) -> list[str]:
    return [r.text for r in rows if r.text.strip()]


def count_review(rows: list[CreditRow]) -> int:
    return sum(1 for r in rows if r.status == "review")

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
# 크레딧 맨 끝의 저작권 문구 (역할 구분자가 없어도 오류가 아니다)
_COPYRIGHT_RE = re.compile(r"^\s*(?:\(c\)|©|copyright\b)|all rights reserved", re.I)
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
    # 역할 안의 'and' 는 '&' 로 통일하고 '&' 앞뒤는 띄운다 (Beauty and Cleanup -> Beauty & Cleanup, Hair&Make -> Hair & Make).
    # 'R&D' 처럼 한 글자 약어는 건드리지 않는다.
    r = re.sub(r"(?i)(?<=\w\w)\s+and\s+(?=\w\w)", " & ", r)
    r = re.sub(r"(?<=\w\w)\s*&\s*(?=\w\w)", " & ", r)
    k = _norm_role_key(r)
    if k in ROLE_CANON:
        return ROLE_CANON[k], True
    return r, False


# '|' 와 모양이 같아 구분자 대신 쓰이는 문자들 (한글 자모 ㅣ, 전각/박스 세로줄 등). 글자 자체가 구분자로만 쓰이므로 무조건 '|' 로 본다.
_PIPE_LOOKALIKES = str.maketrans({"ㅣ": "|", "ǀ": "|", "│": "|", "┃": "|", "¦": "|", "∣": "|"})
# 소문자 l / 대문자 I 는 글자이기도 해서, 앞뒤가 공백인 단독 글자이고 앞부분이 역할처럼 보일 때만 구분자로 본다.
_LOOKALIKE_TOKEN = re.compile(r"(?<=\S)\s+([lI])\s+(?=\S)")
# 그룹 제목 아래에 오는 '1st | ...', '2nd | ...' 처럼 서수만 있는 역할
_ORDINAL_ONLY = re.compile(r"(?:\d{1,2}\s*(?:st|nd|rd|th)|first|second|third|fourth|\d{1,2}\s*(?:차|번째))", re.I)


def _normalize_line(line: str) -> str:
    t = (
        line.translate(_PIPE_LOOKALIKES)
        .replace("：", ":")
        .replace("｜", "|")
        .replace("＠", "@")
        .replace(" ", " ")
        .replace("​", "")
        .replace("﻿", "")
    )
    t = re.sub(r"^\s*[-•·*▪●○■□▶▷>\d]+[.)]?\s+(?=\S)", "", t) if re.match(r"^\s*[•·*▪●○■□▶▷]", t) else t
    return t.strip()


def _fix_lookalike_separator(line: str) -> tuple[str, str]:
    """'|' 대신 단독 'l' / 'I' 를 구분자로 쓴 줄을 '|' 로 바꾼다. 반환: (줄, 안내 문구 또는 '').
    더 앞에 '|' ':' 탭이 있으면 그쪽이 구분자이므로 건드리지 않는다. 대문자 I 는 영어 대명사일 수 있어
    앞부분이 알려진 역할이거나 소문자 없는 제목(ALL CAPS)일 때만 인정한다."""
    masked = URL_RE.sub(lambda m: "§" * len(m.group(0)), line)
    m = _LOOKALIKE_TOKEN.search(masked)
    if not m:
        return line, ""
    firsts = [i for i in (masked.find("|"), masked.find("\t"), masked.find(":")) if i >= 0]
    if firsts and min(firsts) < m.start():
        return line, ""
    left = line[: m.start()].strip()
    if not left or len(left) > 60:
        return line, ""
    tok = m.group(1)
    known = canon_role(left)[1]
    if tok == "I" and not known and re.search(r"[a-z]", left):
        return line, ""
    if tok == "l" and not known and len(left.split()) > 5:
        return line, ""
    return line[: m.start()] + " | " + line[m.end() :], f"구분자 '{tok}' 를 '|' 로 해석했습니다"


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


def _balanced_brackets(s: str) -> bool:
    stack: list[str] = []
    pairs = {")": "(", "]": "["}
    for ch in s:
        if ch in "([":
            stack.append(ch)
        elif ch in ")]":
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack


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
    name = rest if _balanced_brackets(rest) else re.sub(r"[()\[\]]", " ", rest)  # '(Mu:E)' 같은 소속 표기는 보존
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
    by_role: dict[tuple[int, str], CreditRow] = {}
    lines = _join_wrapped_lines(raw.splitlines())
    seen_role = False
    # 첫 역할 줄 앞의 제목/설명 줄은 역할 줄이 하나라도 있는 크레딧에서만 일반 줄로 인정한다
    has_role = any(
        _split_role(_fix_lookalike_separator(_normalize_line(ln))[0]) is not None for ln in lines if ln.strip()
    )
    # 같은 역할 줄은 같은 블록(빈 줄 / 그룹 제목으로 구분) 안에서만 합친다. 블록이 다르면 소속이 다른 사람일 수 있다
    # (예: 스튜디오별 VFX Supervisor, 촬영일별 LIGHTING CREW 의 1st/2nd/3rd).
    gid = 0
    for idx, original in enumerate(lines):
        line = _normalize_line(original)
        if not line:
            gid += 1
            continue
        if _norm_role_key(line.strip("[]():-=*# ")) in _HEADERS or re.fullmatch(r"[-=_*~#\s]{3,}", line):
            continue
        line, sep_note = _fix_lookalike_separator(line)
        if _split_role(line) is None and _next_line_is_ordinal(lines, idx):
            gid += 1
            rows.append(
                CreditRow(
                    role="",
                    status="ok",
                    raw_lines=[original],
                    text=original.strip(),
                    reasons=["그룹 제목으로 인식했습니다 (이어지는 1st/2nd/3rd 줄은 이 그룹에 속합니다)"],
                )
            )
            continue
        row = _parse_line(line, original)
        if row.status == "review" and not row.role and _split_role(line) is None:
            # 구분자가 없는 줄: 맨 끝 저작권 문구, 또는 첫 역할 줄 앞의 제목/연출 문구는 그대로 두는 일반 줄이다
            note = "저작권 문구로 인식했습니다" if _COPYRIGHT_RE.search(line) else None
            if note is None and has_role and not seen_role:
                note = "크레딧 앞의 제목/설명 줄로 인식했습니다 (원문 그대로 유지)"
            if note:
                rows.append(
                    CreditRow(role="", status="ok", raw_lines=[original], text=original.strip(), reasons=[note])
                )
                continue
        if row.status == "ok" and row.role:
            seen_role = True
        if sep_note:
            row.reasons.insert(0, sep_note)
        if row.status == "ok":
            key = (gid, row.role.casefold())
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


def _join_wrapped_lines(lines: list[str]) -> list[str]:
    """쉼표/세미콜론으로 끝난 줄 바로 다음 줄이 구분자 없는 이름 목록이면 한 줄로 합친다
    (예: 'Production Assistant | A, B,' 다음 줄 'C, D' 가 줄바꿈으로 넘어간 경우)."""
    out: list[str] = []
    for ln in lines:
        prev = out[-1] if out else ""
        cur = _normalize_line(ln)
        if (
            prev.strip()
            and prev.rstrip().endswith((",", ";"))
            and cur
            and _split_role(cur) is None
            and _split_role(_normalize_line(prev)) is not None
        ):
            out[-1] = prev.rstrip() + " " + ln.strip()
        else:
            out.append(ln)
    return out


def _next_line_is_ordinal(lines: list[str], idx: int) -> bool:
    """바로 다음 줄이 '1st | ...' 처럼 서수만 역할인 줄이면 현재 줄은 그룹 제목이다."""
    if idx + 1 >= len(lines):
        return False
    nxt, _ = _fix_lookalike_separator(_normalize_line(lines[idx + 1]))
    sp = _split_role(nxt) if nxt else None
    return bool(sp and _ORDINAL_ONLY.fullmatch(sp[0].strip()))


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
    if row.status == "ok" and (row.role or row.people):  # 그룹 제목 줄은 원문 그대로
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

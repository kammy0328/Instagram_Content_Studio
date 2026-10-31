"""프레임 <-> 타임코드 변환 (Non-drop / Drop-frame). 모든 FPS는 Fraction(정확한 유리수)로 다룬다."""

from __future__ import annotations

import re
from fractions import Fraction

STANDARD_FPS = {
    "23.976": Fraction(24000, 1001),
    "24": Fraction(24),
    "25": Fraction(25),
    "29.97": Fraction(30000, 1001),
    "30": Fraction(30),
    "50": Fraction(50),
    "59.94": Fraction(60000, 1001),
    "60": Fraction(60),
}


def parse_rational(text: str | None) -> Fraction | None:
    """'24000/1001', '30/1', '25' -> Fraction. 0/0, N/A -> None."""
    if not text or text in ("N/A", "0/0"):
        return None
    try:
        if "/" in text:
            a, b = text.split("/", 1)
            if int(b) == 0:
                return None
            return Fraction(int(a), int(b))
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


def snap_fps(fps: Fraction, tol: float = 0.005) -> Fraction:
    """측정된 FPS를 표준 FPS에 정확히 스냅 (예: 23.976023 -> 24000/1001)."""
    for std in STANDARD_FPS.values():
        if abs(float(fps) - float(std)) <= tol:
            return std
    return fps


def fps_label(fps: Fraction) -> str:
    for k, v in STANDARD_FPS.items():
        if fps == v:
            return k
    return f"{float(fps):.3f}".rstrip("0").rstrip(".")


def nominal(fps: Fraction) -> int:
    return max(1, round(float(fps)))


def is_drop_capable(fps: Fraction) -> bool:
    return fps in (Fraction(30000, 1001), Fraction(60000, 1001))


def _drop_n(fps: Fraction) -> int:
    return 2 if nominal(fps) == 30 else 4


def frame_to_tc(frame: int, fps: Fraction, drop: bool = False) -> str:
    neg = frame < 0
    frame = abs(int(frame))
    base = nominal(fps)
    sep = ":"
    if drop and is_drop_capable(fps):
        d = _drop_n(fps)
        per10 = base * 600 - d * 9
        per_min = base * 60 - d
        dd, mm = divmod(frame, per10)
        if mm > d:
            frame += d * 9 * dd + d * ((mm - d) // per_min)
        else:
            frame += d * 9 * dd
        sep = ";"
    ff = frame % base
    s = frame // base
    ss = s % 60
    m = s // 60
    mm_ = m % 60
    hh = m // 60
    return f"{'-' if neg else ''}{hh:02d}:{mm_:02d}:{ss:02d}{sep}{ff:02d}"


def tc_to_frame(text: str, fps: Fraction, drop: bool | None = None) -> int | None:
    """타임코드 문자열 -> 프레임 번호. 실패 시 None.

    허용 입력:
      - 01:02:03:04 / 01:02:03;04 (세미콜론 = drop-frame) / 02:03:04 / 03:04
      - 1234f  (프레임 번호 직접 입력)
      - 숫자만(예: 10203)  -> Premiere 방식으로 오른쪽 정렬 HHMMSSFF 해석
    """
    t = (text or "").strip()
    if not t:
        return None
    neg = t.startswith("-")
    if neg:
        t = t[1:].strip()
    m = re.fullmatch(r"(\d+)\s*[fF]", t)
    if m:
        v = int(m.group(1))
        return -v if neg else v
    if re.fullmatch(r"\d+", t):
        digits = t.zfill(8)[-8:]
        parts = [int(digits[i : i + 2]) for i in range(0, 8, 2)]
        sep_drop = False
    else:
        if not re.fullmatch(r"\d{1,3}([:;.,]\d{1,3}){1,3}", t):
            return None
        sep_drop = ";" in t  # 세미콜론 표기만 drop-frame 으로 해석
        parts = [int(x) for x in re.split(r"[:;.,]", t)]
        while len(parts) < 4:
            parts.insert(0, 0)
    hh, mm, ss, ff = parts
    base = nominal(fps)
    if ff >= base or ss >= 60 or mm >= 60:
        return None
    use_drop = sep_drop if drop is None else (drop or sep_drop)
    if use_drop and is_drop_capable(fps):
        d = _drop_n(fps)
        total_min = 60 * hh + mm
        frame = ((hh * 3600 + mm * 60 + ss) * base + ff) - d * (total_min - total_min // 10)
    else:
        frame = (hh * 3600 + mm * 60 + ss) * base + ff
    return -frame if neg else frame


def seconds_label(sec: float) -> str:
    sec = max(0.0, float(sec))
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:04.1f}"

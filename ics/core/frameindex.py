"""프레임 인덱스: 프레임 번호 <-> 표시 시각. VFR 은 패킷 PTS 표로 정확히 처리, CFR 은 유리수 연산."""

from __future__ import annotations

import bisect
import hashlib
import json
import os
from fractions import Fraction
from pathlib import Path
from typing import Callable

from . import ffbin
from .media import MediaInfo
from .timecode import parse_rational, snap_fps


class FrameIndex:
    """
    time_of(n)  : n 번째 프레임의 표시 시각(초, Fraction). 파일 시작(format start_time) 기준 -> -ss 에 그대로 사용 가능.
    frame_at(t) : 시각 t 에 표시되는(또는 가장 가까운) 프레임 번호.
    """

    def __init__(
        self, count: int, fps: Fraction, origin: Fraction, pts_list: list[Fraction] | None = None, vfr: bool = False
    ):
        self.count = int(count)
        self.fps = Fraction(fps)
        self.origin = Fraction(origin)  # 첫 프레임 시각(보통 0)
        self._pts = pts_list  # None 이면 CFR
        self.vfr = vfr

    # ------------------------------------------------------------------ queries
    @property
    def is_cfr(self) -> bool:
        return self._pts is None

    def time_of(self, n: int) -> Fraction:
        n = max(0, min(int(n), self.count))
        if self._pts is None:
            return self.origin + Fraction(n) / self.fps
        if n >= len(self._pts):
            return self._pts[-1] + self.frame_duration(len(self._pts) - 1)
        return self._pts[n]

    def frame_duration(self, n: int) -> Fraction:
        if self._pts is None:
            return 1 / self.fps
        n = max(0, min(n, len(self._pts) - 1))
        if n + 1 < len(self._pts):
            return self._pts[n + 1] - self._pts[n]
        if n > 0:
            return self._pts[n] - self._pts[n - 1]
        return 1 / self.fps

    def frame_at(self, t: Fraction | float) -> int:
        t = Fraction(t).limit_denominator(1_000_000) if not isinstance(t, Fraction) else t
        if self.count == 0:
            return 0
        if self._pts is None:
            n = round((t - self.origin) * self.fps)
        else:
            i = bisect.bisect_right(self._pts, t) - 1
            if i < 0:
                n = 0
            elif i + 1 < len(self._pts) and (self._pts[i + 1] - t) < (t - self._pts[i]):
                n = i + 1
            else:
                n = i
        return max(0, min(int(n), self.count - 1))

    def duration(self) -> Fraction:
        return self.time_of(self.count) - self.origin

    def seek_time(self, n: int) -> float:
        """입력 시킹용 시각: 목표 프레임 PTS 보다 1/4 프레임 앞 -> 반올림 오차로 다음 프레임이 선택되는 것을 방지."""
        n = max(0, min(int(n), max(0, self.count - 1)))
        d = self.frame_duration(n)
        if n > 0:
            d = min(d, self.frame_duration(n - 1))
        return float(self.time_of(n) - d / 4)

    def clamp(self, n: int) -> int:
        return max(0, min(int(n), max(0, self.count - 1)))

    # ------------------------------------------------------------------ cache
    def to_json(self) -> dict:
        return {
            "count": self.count,
            "fps": str(self.fps),
            "origin": str(self.origin),
            "vfr": self.vfr,
            "pts": None if self._pts is None else [str(p) for p in self._pts],
        }

    @classmethod
    def from_json(cls, d: dict) -> "FrameIndex":
        pts = None if d.get("pts") is None else [Fraction(p) for p in d["pts"]]
        return cls(d["count"], Fraction(d["fps"]), Fraction(d["origin"]), pts, d.get("vfr", False))


def _cache_key(info: MediaInfo) -> str:
    h = hashlib.sha1(f"{info.path}|{info.size_bytes}|{info.mtime}".encode()).hexdigest()[:16]
    return h


def build_frame_index(
    info: MediaInfo, cache_dir: Path | None = None, progress: Callable[[str], None] | None = None
) -> FrameIndex:
    """패킷 PTS 로 프레임 인덱스를 만든다 (디코딩 없음, 빠름)."""
    cache_file = None
    if cache_dir is not None:
        cache_file = Path(cache_dir) / f"frameindex_{_cache_key(info)}.json"
        if cache_file.is_file():
            try:
                return FrameIndex.from_json(json.loads(cache_file.read_text("utf-8")))
            except Exception:  # noqa: BLE001
                pass
    if progress:
        progress("프레임 인덱스 구성 중…")
    tb = parse_rational(info.time_base) or Fraction(1, 1000)
    p = ffbin.run(
        [
            ffbin.ffprobe(),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=pts,flags",
            "-of",
            "csv=p=0",
            info.path,
        ]
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip() or "프레임 인덱스 구성 실패")
    ticks: list[int] = []
    for ln in p.stdout.decode("utf-8", "replace").splitlines():
        parts = ln.strip().split(",")
        if not parts or not parts[0] or parts[0] == "N/A":
            continue
        if len(parts) > 1 and "D" in parts[1]:
            continue
        try:
            ticks.append(int(parts[0]))
        except ValueError:
            continue
    ticks.sort()
    if not ticks:
        # 디코딩 기반 대체 경로 (매우 드문 경우)
        n = info.nb_frames or int(info.duration * float(info.fps_frac))
        idx = FrameIndex(n, info.fps_frac, Fraction(0))
    else:
        times = [Fraction(t) * tb for t in ticks]
        fmt_start = Fraction(info.start_time).limit_denominator(1_000_000)
        # 파일 시작 기준(-ss 와 동일 기준)으로 변환
        times = [t - fmt_start for t in times]
        origin = times[0]
        fps = info.fps_frac
        if fps <= 0:
            span = times[-1] - times[0]
            fps = snap_fps(Fraction(len(times) - 1) / span) if span > 0 and len(times) > 1 else Fraction(24)
        # CFR 판정: 선형 모델에서의 최대 편차가 프레임 길이의 10% 이하
        tol = Fraction(1, 10) / fps
        dev = max((abs(t - (origin + Fraction(i) / fps)) for i, t in enumerate(times)), default=Fraction(0))
        if dev <= tol:
            idx = FrameIndex(len(times), fps, origin)
        else:
            span = times[-1] - times[0]
            avg = snap_fps(Fraction(len(times) - 1) / span) if span > 0 and len(times) > 1 else fps
            idx = FrameIndex(len(times), avg, origin, times, vfr=True)
    if cache_file is not None:
        try:
            tmp = cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(idx.to_json()), "utf-8")
            os.replace(tmp, cache_file)
        except OSError:
            pass
    return idx

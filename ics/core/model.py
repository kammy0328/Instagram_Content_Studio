"""편집 상태(Undo/Redo 와 자동저장의 단위) 데이터 모델."""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from .cuts import Cut, deserialize, serialize
from .layout import LayoutParams

STORY_MAX_SEC = 60.0
REELS_MAX_SEC = 180.0
FEED_VIDEO_MAX_SEC = 60.0
FEED_MAX_FINAL = 20


def new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class Selection:
    """STORY / REELS 구간 (프로젝트당 최대 1개)."""

    enabled: bool = False
    start: int = 0
    end: int = 0  # exclusive
    preset: str = "max"  # '15' | '30' | '60' | '180' | 'max' | 'custom'
    layout: LayoutParams = field(default_factory=LayoutParams)

    @property
    def length(self) -> int:
        return max(0, self.end - self.start)

    def to_dict(self):
        return {
            "enabled": self.enabled,
            "start": self.start,
            "end": self.end,
            "preset": self.preset,
            "layout": self.layout.to_dict(),
        }

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(
            d.get("enabled", False),
            d.get("start", 0),
            d.get("end", 0),
            d.get("preset", "max"),
            LayoutParams.from_dict(d.get("layout")),
        )


@dataclass
class Cover:
    frame: int | None = None
    layout: LayoutParams = field(default_factory=lambda: LayoutParams("fit"))
    grid_aspect: str = "4:5"  # 프로필 그리드 미리보기 비율 ('4:5' 기본 | '3:4' | '1:1')
    grid_offset: float = 0.5  # 9:16 안에서 그리드 크롭 창의 세로 위치(0..1)

    def to_dict(self):
        return {
            "frame": self.frame,
            "layout": self.layout.to_dict(),
            "grid_aspect": self.grid_aspect,
            "grid_offset": self.grid_offset,
        }

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(
            d.get("frame"),
            LayoutParams.from_dict(d.get("layout")),
            d.get("grid_aspect", "4:5"),
            float(d.get("grid_offset", 0.5)),
        )


@dataclass
class FeedItem:
    id: str
    kind: str  # 'still' | 'video'
    start: int  # still: 프레임 / video: In
    end: int = 0  # video: Out (exclusive). still 은 start+1
    included: bool = True
    layout: LayoutParams = field(default_factory=LayoutParams)
    seq: int = 0  # 생성 순서(동률 정렬용)
    cut_no: int | None = None  # 등록 시점의 컷 번호(스틸 파일명용 참고값)

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
            "included": self.included,
            "layout": self.layout.to_dict(),
            "seq": self.seq,
            "cut_no": self.cut_no,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            d["id"],
            d["kind"],
            d["start"],
            d.get("end", d["start"] + 1),
            d.get("included", True),
            LayoutParams.from_dict(d.get("layout")),
            d.get("seq", 0),
            d.get("cut_no"),
        )

    def sort_key(self):
        return (self.start, 0 if self.kind == "still" else 1, self.seq)


@dataclass
class EditState:
    cuts: list[Cut] = field(default_factory=list)
    orig_cuts: list[dict] = field(default_factory=list)  # XML 에서 읽은 원본 컷(복원용)
    xml_meta: dict = field(default_factory=dict)
    story: Selection = field(default_factory=lambda: Selection(preset="60"))
    reels: Selection = field(default_factory=lambda: Selection(preset="max"))
    cover: Cover = field(default_factory=Cover)
    feed: list[FeedItem] = field(default_factory=list)
    credits_raw: str = ""
    credits_rows: list[dict] = field(default_factory=list)
    caption_body: str = ""
    caption_options: dict = field(
        default_factory=lambda: {"limit": 2200, "count_mode": "utf16", "credits_position": "after"}
    )
    comments_override: list[str] | None = None
    upload_checks: dict = field(default_factory=lambda: {"story": False, "reels": False, "feed": False})
    feed_seq: int = 0
    feed_ratio: str = "orig"  # FEED 출력 비율: 'orig'(원본 비율, 기본) | '45'(4:5)
    cover_ratio: str = "orig"  # REELS 커버 출력 비율: 'orig'(원본 비율, 기본) | '916'
    playhead: int = 0
    ui: dict = field(default_factory=dict)  # 줌/스크롤/탭 등 복원용 (undo 대상 아님)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cuts": serialize(self.cuts),
            "orig_cuts": copy.deepcopy(self.orig_cuts),
            "xml_meta": copy.deepcopy(self.xml_meta),
            "story": self.story.to_dict(),
            "reels": self.reels.to_dict(),
            "cover": self.cover.to_dict(),
            "feed": [f.to_dict() for f in self.feed],
            "credits_raw": self.credits_raw,
            "credits_rows": copy.deepcopy(self.credits_rows),
            "caption_body": self.caption_body,
            "caption_options": dict(self.caption_options),
            "comments_override": None if self.comments_override is None else list(self.comments_override),
            "upload_checks": dict(self.upload_checks),
            "feed_seq": self.feed_seq,
            "feed_ratio": self.feed_ratio,
            "cover_ratio": self.cover_ratio,
            "playhead": self.playhead,
            "ui": copy.deepcopy(self.ui),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EditState":
        s = cls()
        s.cuts = deserialize(d.get("cuts", []))
        s.orig_cuts = copy.deepcopy(d.get("orig_cuts", []))
        s.xml_meta = copy.deepcopy(d.get("xml_meta", {}))
        s.story = Selection.from_dict(d.get("story")) if d.get("story") else Selection(preset="60")
        s.reels = Selection.from_dict(d.get("reels")) if d.get("reels") else Selection(preset="max")
        s.cover = Cover.from_dict(d.get("cover"))
        s.feed = [FeedItem.from_dict(x) for x in d.get("feed", [])]
        s.credits_raw = d.get("credits_raw", "")
        s.credits_rows = copy.deepcopy(d.get("credits_rows", []))
        s.caption_body = d.get("caption_body", "")
        s.caption_options.update(d.get("caption_options", {}))
        s.comments_override = d.get("comments_override")
        s.upload_checks.update(d.get("upload_checks", {}))
        s.feed_seq = d.get("feed_seq", 0)
        # 이 설정이 없던 옛 프로젝트: 이미 4:5 로 구성한 피드가 있으면 그대로 4:5 를 유지한다 (결과가 바뀌면 안 됨)
        s.feed_ratio = d.get("feed_ratio") or ("45" if d.get("feed") else "orig")
        if s.feed_ratio not in ("orig", "45"):
            s.feed_ratio = "orig"
        # 옛 프로젝트: 이미 9:16 으로 지정한 커버가 있으면 그대로 9:16 유지 (결과가 바뀌면 안 됨)
        s.cover_ratio = d.get("cover_ratio") or ("916" if (d.get("cover") or {}).get("frame") is not None else "orig")
        if s.cover_ratio not in ("orig", "916"):
            s.cover_ratio = "orig"
        s.playhead = d.get("playhead", 0)
        s.ui = copy.deepcopy(d.get("ui", {}))
        return s


# --------------------------------------------------------------------- 길이 규칙
def max_frames_for(seconds: float, fps: Fraction) -> int:
    """제한 시간(초) 이내의 최대 프레임 수. 예) 23.976fps, 60초 -> 1438프레임 (59.98초)."""
    return int((Fraction(seconds).limit_denominator(1000) * fps) // 1)


def sel_duration(index, start: int, end: int) -> float:
    return float(index.time_of(end) - index.time_of(start))

"""기존 출력 읽기 전용 어댑터. 출력 폴더(STORY/REELS/FEED/CAPTION)를 읽기만 하며 어떤 파일도 만들거나 바꾸거나 지우지 않는다."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import JobSpec, Kind, MediaAsset


@dataclass
class OutputBundle:
    root: Path
    story: MediaAsset | None = None
    reels: MediaAsset | None = None
    reels_cover: MediaAsset | None = None
    feed: list[MediaAsset] = field(default_factory=list)  # 01, 02 순서
    caption: str = ""
    comments: list[str] = field(default_factory=list)
    credits: str = ""
    output_version: str = ""
    problems: list[str] = field(default_factory=list)  # 읽지 못한 파일 (사용 중/권한 없음 등)

    def available(self) -> dict[Kind, bool]:
        return {Kind.STORY: self.story is not None, Kind.REELS: self.reels is not None, Kind.FEED: bool(self.feed)}


def _text(p: Path) -> str:
    try:
        return p.read_text("utf-8")
    except OSError:
        return ""


def read_output(root: str | Path) -> OutputBundle:
    root = Path(root)
    b = OutputBundle(root)

    def first(d: str, pattern: str) -> Path | None:
        try:
            files = sorted((root / d).glob(pattern)) if (root / d).is_dir() else []
        except OSError as e:
            b.problems.append(f"{d}: 폴더를 읽을 수 없음 ({e.strerror or e})")
            return None
        return files[0] if files else None

    def asset(p: Path | None, role: str, order: int = 0) -> MediaAsset | None:
        """파일을 못 읽으면(다른 프로그램이 사용 중, 권한 없음, 네트워크 끊김) 그 항목만 빼고 이유를 남긴다."""
        if p is None:
            return None
        try:
            return MediaAsset.from_file(p, role, order=order)
        except OSError as e:
            why = (
                "다른 프로그램이 사용 중이거나 접근 권한이 없습니다"
                if isinstance(e, PermissionError)
                else (e.strerror or str(e))
            )
            b.problems.append(f"{p.parent.name}/{p.name}: 읽을 수 없음 ({why})")
            return None

    b.story = asset(first("STORY", "*.mp4"), "video")
    b.reels = asset(first("REELS", "*REELS*.mp4") or first("REELS", "*.mp4"), "video")
    b.reels_cover = asset(first("REELS", "*_COVER.jpg"), "cover")
    items = []
    try:
        feed_files = sorted((root / "FEED").iterdir()) if (root / "FEED").is_dir() else []
    except OSError as e:
        feed_files = []
        b.problems.append(f"FEED: 폴더를 읽을 수 없음 ({e.strerror or e})")
    for p in feed_files:
        m = re.match(r"^(\d+)_(VIDEO|STILL)\.(mp4|jpg)$", p.name, re.I)
        if m:
            items.append((int(m.group(1)), p, "video" if m.group(2).upper() == "VIDEO" else "image"))
    feed = [asset(p, role, n) for n, (_num, p, role) in enumerate(sorted(items, key=lambda t: t[0]))]
    # 한 장이라도 못 읽으면 FEED 전체를 빼 둔다 (빠진 채로 캐러셀이 올라가지 않게)
    b.feed = [a for a in feed if a is not None] if all(a is not None for a in feed) else []
    cd = root / "CAPTION"
    b.caption = _text(cd / "CAPTION.txt")
    try:
        b.comments = [_text(p) for p in sorted(cd.glob("COMMENT_*.txt"))] if cd.is_dir() else []
    except OSError:
        b.comments = []
    b.credits = _text(cd / "CREDITS_ORIGINAL.txt")
    h = hashlib.sha256()
    for a in [b.story, b.reels, b.reels_cover, *b.feed]:
        if a:
            h.update(a.sha256.encode())
    h.update(b.caption.encode("utf-8"))
    b.output_version = h.hexdigest()[:16]
    return b


def build_specs(b: OutputBundle, project_id: str = "", first_comment: bool = False) -> dict[Kind, JobSpec]:
    """게시 가능한 유형별 JobSpec. 스토리에는 캡션/댓글을 적용하지 않는다 (지시서)."""
    out: dict[Kind, JobSpec] = {}
    ov = b.output_version
    if b.story:
        out[Kind.STORY] = JobSpec(Kind.STORY, [b.story], project_id=project_id, output_version=ov)
    if b.reels:
        assets = [b.reels] + ([b.reels_cover] if b.reels_cover else [])
        out[Kind.REELS] = JobSpec(
            Kind.REELS,
            assets,
            caption=b.caption,
            first_comment=(b.comments[0] if first_comment and b.comments else ""),
            credits=b.credits,
            project_id=project_id,
            output_version=ov,
        )
    if b.feed:
        out[Kind.FEED] = JobSpec(
            Kind.FEED,
            list(b.feed),
            caption=b.caption,
            first_comment=(b.comments[0] if first_comment and b.comments else ""),
            credits=b.credits,
            project_id=project_id,
            output_version=ov,
        )
    return out

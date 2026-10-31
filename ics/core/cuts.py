"""컷 목록 편집(병합/해제/복원/롤·트림 보정). 모든 함수는 순수 함수 - 새 리스트를 반환한다."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field


@dataclass
class Part:
    """병합 전 개별 원본 컷 (메타데이터 보존용)."""

    id: int
    start: int
    end: int  # exclusive
    name: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class Cut:
    id: int
    start: int
    end: int  # exclusive
    name: str = ""
    parts: list[Part] = field(default_factory=list)
    manual: bool = False  # 사용자가 경계를 수정했는가

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def merged(self) -> bool:
        return len(self.parts) > 1

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(
            id=d["id"],
            start=d["start"],
            end=d["end"],
            name=d.get("name", ""),
            parts=[Part(**p) for p in d.get("parts", [])],
            manual=d.get("manual", False),
        )


def cuts_from_ranges(ranges: list[tuple[int, int, str, dict]]) -> list[Cut]:
    out = []
    for i, (s, e, name, meta) in enumerate(sorted(ranges, key=lambda r: (r[0], r[1])), start=1):
        out.append(Cut(i, s, e, name, [Part(i, s, e, name, meta or {})]))
    return out


def cut_index_at(cuts: list[Cut], frame: int) -> int | None:
    for i, c in enumerate(cuts):
        if c.start <= frame < c.end:
            return i
    return None


def merge(cuts: list[Cut], ids: list[int]) -> list[Cut]:
    """선택한 연속 컷을 하나의 논리 컷으로 병합 (첫 컷 시작 ~ 마지막 컷 종료)."""
    order = [i for i, c in enumerate(cuts) if c.id in set(ids)]
    if len(order) < 2:
        raise ValueError("병합하려면 컷을 2개 이상 선택하세요.")
    if order != list(range(order[0], order[-1] + 1)):
        raise ValueError("연속된 컷만 병합할 수 있습니다.")
    sel = cuts[order[0] : order[-1] + 1]
    parts: list[Part] = []
    for c in sel:
        parts.extend(copy.deepcopy(c.parts))
    # 병합 시점의 경계를 파트에 반영(트림으로 수정된 값 보존)
    for c in sel:
        if not c.merged and c.parts:
            c_part = parts[[p.id for p in parts].index(c.parts[0].id)]
            c_part.start, c_part.end = c.start, c.end
    new_id = max(c.id for c in cuts) + 1
    merged = Cut(
        new_id,
        sel[0].start,
        sel[-1].end,
        name=f"{sel[0].name}+{len(sel) - 1}" if sel[0].name else "",
        parts=parts,
        manual=any(c.manual for c in sel),
    )
    return cuts[: order[0]] + [merged] + cuts[order[-1] + 1 :]


def unmerge(cuts: list[Cut], cut_id: int) -> list[Cut]:
    idx = next((i for i, c in enumerate(cuts) if c.id == cut_id), None)
    if idx is None or not cuts[idx].merged:
        raise ValueError("병합된 컷이 아닙니다.")
    c = cuts[idx]
    restored = []
    for k, p in enumerate(c.parts):
        s, e = max(p.start, c.start), min(p.end, c.end)
        if k == 0:
            s = c.start  # 병합 컷의 바깥 경계 보정은 유지
        if k == len(c.parts) - 1:
            e = c.end
        if e > s:
            restored.append(Cut(p.id, s, e, p.name, [Part(p.id, s, e, p.name, copy.deepcopy(p.meta))], manual=c.manual))
    return cuts[:idx] + restored + cuts[idx + 1 :]


def restore_original(orig: list[dict]) -> list[Cut]:
    return cuts_from_ranges([(o["start"], o["end"], o.get("name", ""), o.get("meta", {})) for o in orig])


def find_issues(cuts: list[Cut]) -> list[dict]:
    """인접 컷 사이의 겹침/빈 구간."""
    issues = []
    for i in range(len(cuts) - 1):
        a, b = cuts[i], cuts[i + 1]
        if a.end > b.start:
            issues.append({"index": i, "kind": "overlap", "frames": a.end - b.start})
        elif a.end < b.start:
            issues.append({"index": i, "kind": "gap", "frames": b.start - a.end})
    return issues


def move_boundary(cuts: list[Cut], index: int, side: str, new_frame: int, mode: str, total_frames: int) -> list[Cut]:
    """경계 이동.
    side='end'  : cuts[index] 의 종료(=다음 컷 시작과 맞닿은 경계)
    side='start': cuts[index] 의 시작
    mode='roll' : 맞닿은 두 경계를 같은 변위로 함께 이동 (편집점 이동)
    mode='trim' : 지정한 한쪽 경계만 이동 (이웃 컷과 겹침/빈 구간 발생 가능)
    """
    out = copy.deepcopy(cuts)
    new_frame = int(new_frame)
    c = out[index]
    if side == "end":
        old = c.end
        nxt = out[index + 1] if index + 1 < len(out) else None
        if mode == "roll" and nxt is not None:
            delta = new_frame - old
            # 이동 가능 범위: 양쪽 컷 모두 최소 1프레임 유지
            delta = max(delta, (c.start + 1) - c.end)
            delta = min(delta, (nxt.end - 1) - nxt.start)
            c.end += delta
            nxt.start += delta
            c.manual = nxt.manual = True
        else:
            c.end = max(c.start + 1, min(new_frame, total_frames))
            c.manual = True
    elif side == "start":
        prv = out[index - 1] if index > 0 else None
        if mode == "roll" and prv is not None:
            old = c.start
            delta = new_frame - old
            delta = max(delta, (prv.start + 1) - prv.end)
            delta = min(delta, (c.end - 1) - c.start)
            c.start += delta
            prv.end += delta
            c.manual = prv.manual = True
        else:
            c.start = min(c.end - 1, max(0, new_frame))
            c.manual = True
    else:
        raise ValueError(side)
    return out


def serialize(cuts: list[Cut]) -> list[dict]:
    return [c.to_dict() for c in cuts]


def deserialize(data: list[dict]) -> list[Cut]:
    return [Cut.from_dict(d) for d in data]

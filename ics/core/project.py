"""프로젝트 컨트롤러 (Qt 비의존). 편집 상태, Undo/Redo, 자동저장, 복구, 모든 편집 명령을 관리한다."""

from __future__ import annotations

import copy
import time
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Callable

from .. import paths
from . import caption as caption_mod
from . import credits as credits_mod
from . import cuts as cuts_mod
from .autosave import AutoSaver
from .frameindex import FrameIndex
from .layout import LayoutParams
from .media import MediaInfo
from .model import (
    FEED_MAX_FINAL,
    FEED_VIDEO_MAX_SEC,
    REELS_MAX_SEC,
    STORY_MAX_SEC,
    EditState,
    FeedItem,
    Selection,
    max_frames_for,
    new_id,
    sel_duration,
)
from .store import Library, ProjectRecord, ProjectStore
from .timecode import frame_to_tc

UNDO_LIMIT = 300
PRESET_SECONDS = {"15": 15, "30": 30, "60": 60, "180": 180}


class Project:
    def __init__(self, record: ProjectRecord, library: Library):
        self.rec = record
        self.library = library
        self.store = ProjectStore(paths.project_db_path(record.id))
        self.state = EditState()
        self.media: MediaInfo | None = None
        self.index: FrameIndex | None = None
        self._undo: list[tuple[str, dict]] = []
        self._redo: list[tuple[str, dict]] = []
        self._group: tuple[str, dict] | None = None
        self.listeners: list[Callable[[str], None]] = []
        self.save_status = "saved"
        self.save_message = ""
        self.status_listeners: list[Callable[[str, str], None]] = []
        self.recovered = False
        self.skipped_snapshots = 0
        self.autosaver = AutoSaver(self.store, on_status=self._on_save_status)
        self._closed = False

    # ================================================================ 수명주기
    def open(self) -> dict:
        crashed = self.store.mark_open()
        payload, skipped = self.store.load_latest()
        self.skipped_snapshots = skipped
        if payload:
            info = payload.get("info", {})
            for k in ("name", "artist", "title", "master_path", "output_dir"):
                if k in info:
                    setattr(self.rec, k, info[k])
            self.state = EditState.from_dict(payload.get("state", {}))
            self.recovered = crashed
        return {"crashed": crashed, "restored": bool(payload), "skipped": skipped}

    def close(self) -> None:
        if self._closed:
            return
        self.autosaver.request(self._payload())
        if not self.autosaver.close():
            raise RuntimeError(
                "프로젝트를 저장하지 못했습니다. 공간/권한을 확인한 뒤 다시 시도하세요. " + self.autosaver.last_error
            )
        self.store.mark_closed()
        self.store.close()
        self._closed = True

    def attach_media(self, info: MediaInfo, index: FrameIndex) -> None:
        self.media, self.index = info, index
        self._notify("media")

    # ================================================================ 알림/저장
    def add_listener(self, fn: Callable[[str], None]) -> None:
        self.listeners.append(fn)

    def _notify(self, kind: str) -> None:
        for fn in list(self.listeners):
            fn(kind)

    def _on_save_status(self, status: str, msg: str) -> None:
        self.save_status, self.save_message = status, msg
        for fn in list(self.status_listeners):
            fn(status, msg)

    def _payload(self) -> dict:
        return {
            "version": 1,
            "info": {
                "name": self.rec.name,
                "artist": self.rec.artist,
                "title": self.rec.title,
                "master_path": self.rec.master_path,
                "output_dir": self.rec.output_dir,
            },
            "state": self.state.to_dict(),
            "media_summary": (
                None
                if not self.media
                else {
                    "size": self.media.size_bytes,
                    "mtime": self.media.mtime,
                    "duration": self.media.duration,
                    "fps": self.media.fps,
                    "w": self.media.width,
                    "h": self.media.height,
                }
            ),
        }

    def request_save(self) -> None:
        self.rec.updated = time.time()
        self.autosaver.request(self._payload())
        try:
            self.library.upsert(self.rec)
        except Exception:  # noqa: BLE001
            pass

    # ================================================================ 편집 트랜잭션/Undo
    @contextmanager
    def edit(self, label: str, kinds: tuple[str, ...] = ("state",)):
        outer = self._group is not None
        snap = None if outer else copy.deepcopy(self.state.to_dict())
        try:
            yield
        except BaseException:
            if snap is not None:
                keep_ui = (self.state.playhead, self.state.ui)
                self.state = EditState.from_dict(snap)
                self.state.playhead, self.state.ui = keep_ui
            raise
        if snap is not None:
            after = self.state.to_dict()
            a, b = dict(snap), dict(after)
            for k in ("playhead", "ui"):
                a.pop(k, None)
                b.pop(k, None)
            if a != b:
                self._undo.append((label, snap))
                del self._undo[:-UNDO_LIMIT]
                self._redo.clear()
        for k in kinds:
            self._notify(k)
        self.request_save()

    def begin_group(self, label: str) -> None:
        if self._group is None:
            self._group = (label, copy.deepcopy(self.state.to_dict()))

    def end_group(self) -> None:
        if self._group is None:
            return
        label, snap = self._group
        self._group = None
        a, b = dict(snap), self.state.to_dict()
        for k in ("playhead", "ui"):
            a.pop(k, None)
            b.pop(k, None)
        if a != b:
            self._undo.append((label, snap))
            del self._undo[:-UNDO_LIMIT]
            self._redo.clear()
        self._notify("state")
        self.request_save()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def _restore(self, snap: dict) -> None:
        keep = (self.state.playhead, self.state.ui)
        self.state = EditState.from_dict(snap)
        self.state.playhead, self.state.ui = keep

    def undo(self) -> str | None:
        if not self._undo:
            return None
        label, snap = self._undo.pop()
        self._redo.append((label, copy.deepcopy(self.state.to_dict())))
        self._restore(snap)
        self._notify("state")
        self.request_save()
        return label

    def redo(self) -> str | None:
        if not self._redo:
            return None
        label, snap = self._redo.pop()
        self._undo.append((label, copy.deepcopy(self.state.to_dict())))
        self._restore(snap)
        self._notify("state")
        self.request_save()
        return label

    # ================================================================ 기본 정보
    @property
    def fps(self) -> Fraction:
        return self.index.fps if self.index else Fraction(24)

    @property
    def count(self) -> int:
        return self.index.count if self.index else 0

    def tc(self, frame: int, drop: bool = False) -> str:
        return frame_to_tc(frame, self.fps, drop)

    def seconds(self, start: int, end: int) -> float:
        return sel_duration(self.index, start, end) if self.index else 0.0

    def update_info(self, **kw) -> None:
        for k, v in kw.items():
            if k in ("name", "artist", "title", "master_path", "output_dir"):
                setattr(self.rec, k, v)
        self._notify("info")
        self.request_save()

    def set_ui(self, key: str, value) -> None:
        """줌/스크롤/탭 같은 UI 상태 - Undo/자동저장 트리거 없이 다음 저장 때 함께 기록."""
        self.state.ui[key] = value

    def set_playhead(self, frame: int) -> None:
        self.state.playhead = int(frame)

    # ================================================================ STORY / REELS
    def _sel(self, which: str) -> Selection:
        return self.state.story if which == "story" else self.state.reels

    def _limit_sec(self, which: str) -> float:
        return STORY_MAX_SEC if which == "story" else REELS_MAX_SEC

    def max_len_frames(self, which: str) -> int:
        return max_frames_for(self._limit_sec(which), self.fps)

    def limited_end(self, start: int, seconds: int | float) -> int:
        """PTS 실제 길이가 제한을 넘지 않는 마지막 배타적 Out을 구한다."""
        deadline = self.index.time_of(start) + Fraction(str(seconds))
        lo, hi = start, self.count
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.index.time_of(mid) <= deadline:
                lo = mid
            else:
                hi = mid - 1
        if lo <= start:
            raise ValueError("현재 프레임 하나의 길이가 출력 시간 제한을 넘습니다.")
        return lo

    def apply_preset(self, which: str, start: int, preset: str) -> str:
        """시작 지점 기준 프리셋 적용. 반환: 안내 메시지(없으면 '')."""
        sel = self._sel(which)
        msg = ""
        with self.edit(f"{which.upper()} 구간 {preset}", (which, "state")):
            start = self.index.clamp(start)
            if preset == "max":
                sec = self._limit_sec(which)
            elif preset == "custom":
                sec = None
            else:
                sec = min(PRESET_SECONDS[preset], self._limit_sec(which))
            if sec is not None:
                end = self.limited_end(start, sec)
                if self.index.time_of(self.count) < self.index.time_of(start) + Fraction(str(sec)):
                    msg = f"영상 끝까지 {self.seconds(start, end):.2f}초만 선택되었습니다."
            else:
                end = max(start + 1, min(sel.end if sel.end > start else start + 1, self.count))
            sel.enabled, sel.start, sel.end, sel.preset = True, start, end, preset
        return msg

    def set_range(self, which: str, start: int, end: int) -> str:
        sel = self._sel(which)
        msg = ""
        with self.edit(f"{which.upper()} In/Out", (which, "state")):
            start = self.index.clamp(start)
            end = max(start + 1, min(int(end), self.count))
            max_end = self.limited_end(start, self._limit_sec(which))
            if end > max_end:
                end = max_end
                msg = f"{which.upper()}는 최대 {int(self._limit_sec(which))}초까지 가능해 Out 을 조정했습니다."
            sel.enabled, sel.start, sel.end, sel.preset = True, start, end, "custom"
        return msg

    def set_selection_layout(self, which: str, layout: LayoutParams) -> None:
        sel = self._sel(which)
        with self.edit(f"{which.upper()} 화면 배치", (which, "state")):
            sel.layout = layout

    def clear_selection(self, which: str) -> None:
        sel = self._sel(which)
        with self.edit(f"{which.upper()} 해제", (which, "state")):
            sel.enabled = False

    # ================================================================ 커버
    def set_cover_frame(self, frame: int | None) -> None:
        with self.edit("커버 프레임", ("cover", "state")):
            self.state.cover.frame = None if frame is None else self.index.clamp(frame)

    def set_cover_layout(self, layout: LayoutParams) -> None:
        with self.edit("커버 배치", ("cover", "state")):
            self.state.cover.layout = layout

    def set_cover_grid(self, aspect: str | None = None, offset: float | None = None) -> None:
        with self.edit("커버 그리드", ("cover", "state")):
            if aspect:
                self.state.cover.grid_aspect = aspect
            if offset is not None:
                self.state.cover.grid_offset = min(1.0, max(0.0, offset))

    # ================================================================ FEED
    def cut_number_at(self, frame: int) -> int | None:
        i = cuts_mod.cut_index_at(self.state.cuts, frame)
        return None if i is None else i + 1

    def add_feed_still(self, frame: int) -> FeedItem:
        with self.edit("피드 스틸 추가", ("feed", "state")):
            frame = self.index.clamp(frame)
            self.state.feed_seq += 1
            it = FeedItem(
                new_id(),
                "still",
                frame,
                frame + 1,
                True,
                LayoutParams(),
                self.state.feed_seq,
                self.cut_number_at(frame),
            )
            self.state.feed.append(it)
        return it

    def add_feed_video(self, start: int, end: int) -> tuple[FeedItem, str]:
        msg = ""
        with self.edit("피드 영상 추가", ("feed", "state")):
            start = self.index.clamp(start)
            end = max(start + 1, min(int(end), self.count))
            max_end = self.limited_end(start, FEED_VIDEO_MAX_SEC)
            if end > max_end:
                end = max_end
                msg = f"피드 영상은 최대 {int(FEED_VIDEO_MAX_SEC)}초라 Out 을 조정했습니다."
            self.state.feed_seq += 1
            it = FeedItem(
                new_id(), "video", start, end, True, LayoutParams(), self.state.feed_seq, self.cut_number_at(start)
            )
            self.state.feed.append(it)
        return it, msg

    def get_feed(self, item_id: str) -> FeedItem | None:
        return next((f for f in self.state.feed if f.id == item_id), None)

    def update_feed_range(self, item_id: str, start: int, end: int) -> str:
        msg = ""
        with self.edit("피드 구간 수정", ("feed", "state")):
            it = self.get_feed(item_id)
            if it is None:
                return ""
            if it.kind == "still":
                it.start = self.index.clamp(start)
                it.end = it.start + 1
            else:
                s = self.index.clamp(start)
                e = max(s + 1, min(int(end), self.count))
                max_end = self.limited_end(s, FEED_VIDEO_MAX_SEC)
                if e > max_end:
                    e, msg = max_end, "피드 영상은 최대 60초입니다."
                it.start, it.end = s, e
        return msg

    def set_feed_layout(self, item_id: str, layout: LayoutParams) -> None:
        with self.edit("피드 배치", ("feed", "state")):
            it = self.get_feed(item_id)
            if it:
                it.layout = layout

    def set_feed_included(self, ids: list[str], included: bool) -> None:
        with self.edit("피드 포함 상태", ("feed", "state")):
            for it in self.state.feed:
                if it.id in ids:
                    it.included = included

    def remove_feed(self, item_id: str) -> None:
        with self.edit("피드 항목 삭제", ("feed", "state")):
            self.state.feed = [f for f in self.state.feed if f.id != item_id]

    def feed_ordered(self) -> list[FeedItem]:
        return sorted(self.state.feed, key=lambda f: f.sort_key())

    def feed_kind(self) -> str:
        """compute_geometry 에 넘기는 FEED 출력 종류: 'orig'(원본 비율) | '45'."""
        return "45" if self.state.feed_ratio == "45" else "orig"

    def cover_kind(self) -> str:
        """REELS 커버의 compute_geometry 종류: 'orig'(원본 비율) | '916'."""
        return "916" if self.state.cover_ratio == "916" else "orig"

    def set_cover_ratio(self, ratio: str) -> None:
        if ratio not in ("orig", "916"):
            raise ValueError(ratio)
        with self.edit("REELS 커버 비율", ("cover", "state")):
            self.state.cover_ratio = ratio

    def set_feed_ratio(self, ratio: str) -> None:
        if ratio not in ("orig", "45"):
            raise ValueError(ratio)
        with self.edit("FEED 출력 비율", ("feed", "state")):
            self.state.feed_ratio = ratio

    def feed_included(self) -> list[FeedItem]:
        return [f for f in self.feed_ordered() if f.included]

    def feed_over_limit(self) -> bool:
        return len(self.feed_included()) > FEED_MAX_FINAL

    def feed_warnings(self) -> list[str]:
        """겹침/중복 가능성 경고 (자동 삭제하지 않음)."""
        out: list[str] = []
        items = [f for f in self.feed_ordered() if f.included]
        idx = {f.id: i + 1 for i, f in enumerate(items)}
        vids = [f for f in items if f.kind == "video"]
        stills = [f for f in items if f.kind == "still"]
        for i, a in enumerate(vids):
            for b in vids[i + 1 :]:
                if a.start < b.end and b.start < a.end:
                    out.append(f"#{idx[a.id]} 영상과 #{idx[b.id]} 영상의 구간이 겹칩니다.")
            for s in stills:
                if a.start <= s.start < a.end:
                    out.append(f"#{idx[s.id]} 스틸이 #{idx[a.id]} 영상 구간 안에 포함되어 있습니다.")
        seen: dict[int, int] = {}
        for s in stills:
            if s.start in seen:
                out.append(f"#{idx[s.id]} 스틸이 #{seen[s.start]} 스틸과 같은 프레임입니다.")
            seen[s.start] = idx[s.id]
        return out

    # ================================================================ 컷(XML)
    def has_manual_cut_edits(self) -> bool:
        return any(c.manual or c.merged for c in self.state.cuts)

    def import_cuts(self, ranges: list[tuple[int, int, str, dict]], meta: dict) -> None:
        with self.edit("XML 컷 불러오기", ("cuts", "state")):
            cs = cuts_mod.cuts_from_ranges(ranges)
            self.state.cuts = cs
            self.state.orig_cuts = [{"start": s, "end": e, "name": n, "meta": m} for s, e, n, m in ranges]
            self.state.xml_meta = dict(meta, imported_at=time.time())

    def clear_cuts(self) -> None:
        with self.edit("컷 정보 제거", ("cuts", "state")):
            self.state.cuts, self.state.orig_cuts, self.state.xml_meta = [], [], {}

    def merge_cuts(self, ids: list[int]) -> None:
        with self.edit("컷 병합", ("cuts", "state")):
            self.state.cuts = cuts_mod.merge(self.state.cuts, ids)

    def unmerge_cut(self, cut_id: int) -> None:
        with self.edit("컷 병합 해제", ("cuts", "state")):
            self.state.cuts = cuts_mod.unmerge(self.state.cuts, cut_id)

    def split_cut_at(self, frame: int) -> tuple[int, int]:
        """frame(재생 헤드)에서 컷을 둘로 나눈다. frame 이 뒤쪽 컷의 첫 프레임이 된다. (앞 컷 id, 뒤 컷 id) 를 돌려준다.

        컷 정보가 아직 없으면(XML 없이 편집) 영상 전체를 한 컷으로 보고 나눈다. 나눌 수 없으면 ValueError."""
        frame = int(frame)
        with self.edit("컷 나누기", ("cuts", "state")):
            cuts = self.state.cuts
            if not cuts:
                cuts = [cuts_mod.Cut(1, 0, self.count, parts=[cuts_mod.Part(1, 0, self.count)])]
            self.state.cuts = cuts_mod.split(cuts, frame)
            i = cuts_mod.cut_index_at(self.state.cuts, frame)
            assert i is not None and i > 0
            return self.state.cuts[i - 1].id, self.state.cuts[i].id

    def restore_original_cuts(self) -> None:
        with self.edit("원래 컷 목록 복원", ("cuts", "state")):
            self.state.cuts = cuts_mod.restore_original(self.state.orig_cuts)

    def move_boundary(self, index: int, side: str, frame: int, mode: str) -> None:
        """드래그 중에는 begin_group/end_group 으로 감싸 한 번의 Undo 로 처리."""
        with self.edit("컷 경계 보정", ("cuts",)):
            self.state.cuts = cuts_mod.move_boundary(self.state.cuts, index, side, frame, mode, self.count)

    def cut_issues(self) -> list[dict]:
        return cuts_mod.find_issues(self.state.cuts)

    # ================================================================ 크레딧 / 캡션
    def set_credits_raw(self, text: str) -> None:
        with self.edit("크레딧 원문", ("credits",)):
            self.state.credits_raw = text

    def parse_credits(self, lookup=None, auto_unique: bool = True) -> list[credits_mod.CreditRow]:
        rows = credits_mod.parse_credits(self.state.credits_raw, lookup, auto_unique)
        with self.edit("크레딧 정규화", ("credits", "state")):
            self.state.credits_rows = [r.to_dict() for r in rows]
            self.state.comments_override = None
        return rows

    def credit_rows(self) -> list[credits_mod.CreditRow]:
        return [credits_mod.CreditRow.from_dict(d) for d in self.state.credits_rows]

    def update_credit_row(self, i: int, row: credits_mod.CreditRow) -> None:
        with self.edit("크레딧 수정", ("credits", "state")):
            self.state.credits_rows[i] = row.to_dict()

    def set_caption_body(self, text: str) -> None:
        with self.edit("캡션 본문", ("credits",)):
            self.state.caption_body = text

    def set_caption_options(self, **kw) -> None:
        with self.edit("캡션 옵션", ("credits",)):
            self.state.caption_options.update(kw)
            self.state.comments_override = None

    def caption_result(self) -> caption_mod.SplitResult:
        o = self.state.caption_options
        lines = credits_mod.rows_to_lines(self.credit_rows())
        res = caption_mod.split_caption(
            self.state.caption_body,
            lines,
            int(o.get("limit", 2200)),
            o.get("count_mode", "utf16"),
            o.get("credits_position", "after"),
        )
        if self.state.comments_override is not None:
            res.comments = list(self.state.comments_override)
            res.comment_counts = [caption_mod.count_chars(c, o.get("count_mode", "utf16")) for c in res.comments]
        return res

    def set_comments_override(self, comments: list[str] | None) -> None:
        with self.edit("댓글 수정", ("credits",)):
            self.state.comments_override = comments

    def set_upload_check(self, key: str, value: bool) -> None:
        with self.edit("업로드 완료 체크", ("ready",)):
            self.state.upload_checks[key] = value

    # ================================================================ 출력 폴더
    def project_folder_name(self) -> str:
        return paths.safe_filename(self.rec.name or "PROJECT")

    def output_root(self) -> Path:
        return Path(self.rec.output_dir) / self.project_folder_name()

    # ================================================================ PROJECT_DATA 내보내기
    def project_data_files(self) -> dict:
        """출력 폴더 PROJECT_DATA 에 기록할 JSON 들."""
        cuts = self.state.cuts
        edits = {
            "xml_meta": self.state.xml_meta,
            "original_cuts": self.state.orig_cuts,
            "current_cuts": [
                {
                    "no": i + 1,
                    "start": c.start,
                    "end": c.end,
                    "merged": c.merged,
                    "manual_adjusted": c.manual,
                    "parts": [{"start": p.start, "end": p.end, "name": p.name} for p in c.parts],
                }
                for i, c in enumerate(cuts)
            ],
            "issues": self.cut_issues(),
        }
        return {
            "PROJECT_SETTINGS.json": {
                "info": self._payload()["info"],
                "state": self.state.to_dict(),
                "media": None if not self.media else self.media.to_dict(),
            },
            "XML_EDITS.json": edits,
        }

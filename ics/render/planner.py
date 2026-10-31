"""렌더링 계획: 출력 파일 목록, 규칙 검증(20개/길이), 경로·용량·충돌 점검."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..core.layout import compute_geometry
from ..core.model import FEED_MAX_FINAL, FEED_VIDEO_MAX_SEC, REELS_MAX_SEC, STORY_MAX_SEC
from ..core.project import Project


@dataclass
class PlanIssue:
    level: str  # error | warn | info
    message: str


@dataclass
class RenderJob:
    id: str
    kind: str  # story | reels | reels_cover | feed_video | feed_still | original_still | captions
    label: str
    out_path: str
    params: dict = field(default_factory=dict)
    status: str = "pending"  # pending | running | done | error | cancelled | skipped
    progress: float = 0.0
    error: str = ""
    result: dict = field(default_factory=dict)
    est_bytes: int = 0
    conflict: bool = False
    eta: float | None = None


@dataclass
class RenderPlan:
    jobs: list[RenderJob] = field(default_factory=list)
    issues: list[PlanIssue] = field(default_factory=list)
    root: str = ""

    @property
    def blocked(self) -> bool:
        return any(i.level == "error" for i in self.issues)

    @property
    def total_est_bytes(self) -> int:
        return sum(j.est_bytes for j in self.jobs)

    @property
    def conflicts(self) -> list[RenderJob]:
        return [j for j in self.jobs if j.conflict]


def estimate_video_bytes(width: int, height: int, fps: float, seconds: float) -> int:
    """CRF 14~15 (maxrate 캡) 의 대략적 비트레이트 기반 예상 용량 - 참고용."""
    mpx = width * height / 1e6
    mbps = min(45.0, 5.5 + 5.5 * mpx)  # 1080x1920(2.07Mpx) 약 17Mbps
    mbps *= min(1.6, max(0.8, fps / 30.0))
    return int(mbps * 1e6 / 8 * seconds + 320_000 / 8 * seconds)


def estimate_still_bytes(width: int, height: int) -> int:
    return int(width * height * 0.45)


def plan_render(project: Project, include_original_stills: bool = True) -> RenderPlan:
    plan = RenderPlan()
    info, index, st = project.media, project.index, project.state
    if info is None or index is None:
        plan.issues.append(PlanIssue("error", "마스터링 영상이 연결되지 않았습니다."))
        return plan
    if not os.path.isfile(info.path):
        plan.issues.append(PlanIssue("error", f"마스터링 원본을 찾을 수 없습니다: {info.path}"))
    if info.hdr:
        plan.issues.append(PlanIssue("error", "HDR 소스는 지원하지 않아 렌더링할 수 없습니다."))
    if not info.audio:
        plan.issues.append(PlanIssue("warn", "오디오 스트림이 없어 영상만 출력됩니다."))
    if info.is_vfr:
        plan.issues.append(PlanIssue("info", "VFR 소스: 출력은 가장 가까운 표준 CFR 로 변환됩니다."))
    if not project.rec.output_dir:
        plan.issues.append(PlanIssue("error", "출력 경로가 지정되지 않았습니다."))
        return plan
    root = project.output_root()
    plan.root = str(root)
    name = project.project_folder_name()
    fps = float(info.fps_frac)
    jid = 0

    def add(kind, label, out, params, est=0) -> RenderJob:
        nonlocal jid
        jid += 1
        j = RenderJob(f"{jid:03d}", kind, label, str(out), params, est_bytes=est)
        j.conflict = Path(out).exists()
        plan.jobs.append(j)
        return j

    # ---------------------------------------------------------------- STORY
    if st.story.enabled:
        s = st.story
        dur = project.seconds(s.start, s.end)
        if s.length < 1:
            plan.issues.append(PlanIssue("error", "STORY 구간이 비어 있습니다."))
        elif index.time_of(s.end) - index.time_of(s.start) > STORY_MAX_SEC:
            plan.issues.append(PlanIssue("error", f"STORY 길이가 {dur:.2f}초로 60초를 넘습니다."))
        else:
            geo = compute_geometry(info.display_w, info.display_h, "916", s.layout)
            add(
                "story",
                f"STORY ({dur:.1f}s)",
                root / "STORY" / f"{name}_STORY_01.mp4",
                {"start": s.start, "end": s.end, "layout": s.layout.to_dict(), "geo": geo.to_dict()},
                estimate_video_bytes(geo.canvas_w, geo.canvas_h, fps, dur),
            )
            if geo.tier_note:
                plan.issues.append(PlanIssue("info", f"STORY: {geo.tier_note}"))

    # ---------------------------------------------------------------- REELS
    if st.reels.enabled:
        s = st.reels
        dur = project.seconds(s.start, s.end)
        if s.length < 1:
            plan.issues.append(PlanIssue("error", "REELS 구간이 비어 있습니다."))
        elif index.time_of(s.end) - index.time_of(s.start) > REELS_MAX_SEC:
            plan.issues.append(PlanIssue("error", f"REELS 길이가 {dur:.2f}초로 180초를 넘습니다."))
        else:
            geo = compute_geometry(info.display_w, info.display_h, "916", s.layout)
            add(
                "reels",
                f"REELS ({dur:.1f}s)",
                root / "REELS" / f"{name}_REELS_01.mp4",
                {"start": s.start, "end": s.end, "layout": s.layout.to_dict(), "geo": geo.to_dict()},
                estimate_video_bytes(geo.canvas_w, geo.canvas_h, fps, dur),
            )
            if geo.tier_note:
                plan.issues.append(PlanIssue("info", f"REELS: {geo.tier_note}"))
    if st.cover.frame is not None:
        geo = compute_geometry(info.display_w, info.display_h, project.cover_kind(), st.cover.layout)
        add(
            "reels_cover",
            "REELS 커버",
            root / "REELS" / f"{name}_REELS_COVER.jpg",
            {"frame": st.cover.frame, "layout": st.cover.layout.to_dict(), "geo": geo.to_dict()},
            estimate_still_bytes(geo.canvas_w, geo.canvas_h),
        )

    # ---------------------------------------------------------------- FEED
    included = project.feed_included()
    if len(included) > FEED_MAX_FINAL:
        plan.issues.append(
            PlanIssue(
                "error",
                f"피드 포함 항목이 {len(included)}개입니다. 최종 출력은 {FEED_MAX_FINAL}개 이하여야 합니다. "
                "FEED 관리 창에서 포함 항목을 줄여주세요.",
            )
        )
    else:
        done_orig: set[int] = set()
        feed_kind = project.feed_kind()
        if feed_kind == "orig" and included:
            ar = info.display_w / info.display_h
            if not (0.8 - 1e-3 <= ar <= 1.91 + 1e-3):
                plan.issues.append(
                    PlanIssue(
                        "warn",
                        f"FEED 원본 비율({info.display_w}:{info.display_h})이 인스타그램 피드 허용 범위(4:5 ~ 1.91:1)를 "
                        "벗어납니다. 인스타그램이 피드에서 가장자리를 잘라 보여 줄 수 있습니다.",
                    )
                )
        for n, it in enumerate(included, start=1):
            geo = compute_geometry(info.display_w, info.display_h, feed_kind, it.layout)
            if it.kind == "video":
                dur = project.seconds(it.start, it.end)
                if index.time_of(it.end) - index.time_of(it.start) > FEED_VIDEO_MAX_SEC:
                    plan.issues.append(PlanIssue("error", f"피드 #{n} 영상이 {dur:.1f}초로 60초를 넘습니다."))
                    continue
                add(
                    "feed_video",
                    f"FEED {n:02d} 영상 ({dur:.1f}s)",
                    root / "FEED" / f"{n:02d}_VIDEO.mp4",
                    {
                        "start": it.start,
                        "end": it.end,
                        "layout": it.layout.to_dict(),
                        "geo": geo.to_dict(),
                        "item": it.id,
                    },
                    estimate_video_bytes(geo.canvas_w, geo.canvas_h, fps, dur),
                )
            else:
                add(
                    "feed_still",
                    f"FEED {n:02d} 스틸",
                    root / "FEED" / f"{n:02d}_STILL.jpg",
                    {"frame": it.start, "layout": it.layout.to_dict(), "geo": geo.to_dict(), "item": it.id},
                    estimate_still_bytes(geo.canvas_w, geo.canvas_h),
                )
                if include_original_stills and it.start not in done_orig:
                    done_orig.add(it.start)
                    ext, _ = ("tiff", "") if info.bit_depth > 8 else ("png", "")
                    cut = project.cut_number_at(it.start) or (
                        0 if project.state.cuts else 1
                    )  # 컷 정보가 없으면 전체를 CUT_001 로 간주
                    add(
                        "original_still",
                        f"원본 스틸 CUT {cut:03d}",
                        root / "ORIGINAL_STILLS" / f"CUT_{cut:03d}_FRAME_{it.start:06d}.{ext}",
                        {"frame": it.start, "item": it.id},
                        info.width * info.height * (6 if ext == "tiff" else 3) // 2,
                    )
        if project.feed_warnings():
            plan.issues.append(
                PlanIssue("warn", f"피드 중복/겹침 가능성 {len(project.feed_warnings())}건 (자동 삭제하지 않음)")
            )

    # ---------------------------------------------------------------- CAPTION
    cap = project.caption_result()
    cdir = root / "CAPTION"
    add(
        "captions",
        "캡션/댓글/크레딧 원문",
        cdir / "CAPTION.txt",
        {"caption": cap.caption, "comments": cap.comments, "credits_raw": st.credits_raw},
        10_000,
    )
    for w in cap.warnings:
        plan.issues.append(PlanIssue("warn", w))

    if not [j for j in plan.jobs if j.kind != "captions"]:
        plan.issues.append(
            PlanIssue("error", "렌더링할 항목이 없습니다. STORY / REELS / FEED 중 하나 이상을 선택하세요.")
        )

    # ---------------------------------------------------------------- 경로/용량 점검
    outdir = Path(project.rec.output_dir)
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        probe = outdir / f".ics_write_test_{os.getpid()}"
        probe.write_text("x")
        probe.unlink()
    except OSError as e:
        plan.issues.append(PlanIssue("error", f"출력 폴더에 쓸 수 없습니다 (권한/연결 확인): {e}"))
    else:
        try:
            free = shutil.disk_usage(outdir).free
            need = int(plan.total_est_bytes * 1.3) + 200 * 1024 * 1024
            if free < need:
                plan.issues.append(
                    PlanIssue(
                        "error", f"디스크 여유 공간이 부족합니다 (필요 약 {need / 1e9:.2f}GB, 여유 {free / 1e9:.2f}GB)."
                    )
                )
        except OSError:
            plan.issues.append(PlanIssue("warn", "디스크 여유 공간을 확인할 수 없습니다 (네트워크 경로일 수 있음)."))
    if plan.conflicts:
        plan.issues.append(
            PlanIssue("warn", f"이미 존재하는 파일 {len(plan.conflicts)}개 - 덮어쓰기 전에 확인이 필요합니다.")
        )
    return plan

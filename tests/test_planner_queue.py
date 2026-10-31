"""렌더 계획/큐: 폴더 규칙, 파일명, 개수 제한, 사전 점검, 오류 중에도 계속, 실패만 재시도, 취소, 로그."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ics.core import frameindex, media
from ics.core.project import Project
from ics.core.store import Library
from ics.render import engine
from ics.render.planner import plan_render
from ics.render.queue import RenderQueue

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def proj(prores_2398, tmp_path):
    lib = Library()
    rec = lib.create("My Project & Test", "ARTIST", "TITLE", str(prores_2398), str(tmp_path / "out"))
    info = media.analyze(str(prores_2398))
    idx = frameindex.build_frame_index(info)
    p = Project(rec, lib)
    p.open()
    p.attach_media(info, idx)
    p.import_cuts([(0, 50, "a", {}), (50, 120, "b", {}), (120, 200, "c", {}), (200, 240, "d", {})], {})
    p.set_range("story", 10, 34)
    p.set_range("reels", 0, 72)
    p.set_cover_frame(77)
    p.add_feed_still(60)
    p.add_feed_video(130, 154)
    p.add_feed_still(5)
    p.set_credits_raw("Director | A (@a)\nEditor: B")
    p.parse_credits()
    p.set_caption_body("Hello #tag")
    yield p
    p.close()


def run(plan, p, overwrite=True, **kw):
    q = RenderQueue(p.media, p.index, plan.root, p.project_data_files(), **kw)
    q.start(plan.jobs, overwrite)
    q.wait(240)
    return q


def test_plan_names_and_folder_rules(proj):
    plan = plan_render(proj)
    assert not plan.blocked
    root = Path(plan.root)
    assert " " not in root.name and "&" not in root.name  # 폴더명 공백/& 금지
    rel = sorted(str(Path(j.out_path).relative_to(root)) for j in plan.jobs)
    name = root.name
    assert f"STORY/{name}_STORY_01.mp4" in rel and f"REELS/{name}_REELS_01.mp4" in rel
    assert f"REELS/{name}_REELS_COVER.jpg" in rel and "CAPTION/CAPTION.txt" in rel
    # FEED 번호는 시간순 (5 -> 60 -> 130)
    feed = [r for r in rel if r.startswith("FEED/")]
    assert feed == ["FEED/01_STILL.jpg", "FEED/02_STILL.jpg", "FEED/03_VIDEO.mp4"]
    stills = [r for r in rel if r.startswith("ORIGINAL_STILLS/")]
    assert stills == ["ORIGINAL_STILLS/CUT_001_FRAME_000005.tiff", "ORIGINAL_STILLS/CUT_002_FRAME_000060.tiff"]


def test_original_still_is_cut_001_when_no_cut_info(proj):
    proj.clear_cuts()
    plan = plan_render(proj)
    assert any(Path(j.out_path).name == "CUT_001_FRAME_000060.tiff" for j in plan.jobs)


def test_full_render_creates_spec_folder_structure_and_logs(proj):
    plan = plan_render(proj)
    run(plan, proj)
    assert all(j.status == "done" for j in plan.jobs), [(j.label, j.status, j.error) for j in plan.jobs]
    root = Path(plan.root)
    for d in ("STORY", "REELS", "FEED", "CAPTION", "ORIGINAL_STILLS", "PROJECT_DATA"):
        assert (root / d).is_dir(), d
    data = root / "PROJECT_DATA"
    assert {p.name for p in data.iterdir()} >= {"PROJECT_SETTINGS.json", "RENDER_LOG.json", "XML_EDITS.json"}
    log = json.loads((data / "RENDER_LOG.json").read_text(encoding="utf-8"))
    run_log = log["runs"][-1]
    assert run_log["source"] == proj.media.path and run_log["ffmpeg"]
    assert len(run_log["entries"]) == len(plan.jobs) and all(e["status"] == "done" for e in run_log["entries"])
    assert not list(root.rglob("*.part"))  # 임시 파일 잔존 없음
    assert (root / "CAPTION" / "CAPTION.txt").read_text(encoding="utf-8").strip()


def test_plan_blocks_when_over_20_feed_items(proj):
    for i in range(20):
        proj.add_feed_still(i + 10)
    assert proj.feed_over_limit()
    plan = plan_render(proj)
    assert plan.blocked and any("20" in i.message for i in plan.issues if i.level == "error")


def test_plan_blocks_when_nothing_selected(proj):
    proj.clear_selection("story")
    proj.clear_selection("reels")
    proj.set_cover_frame(None)
    for f in list(proj.state.feed):
        proj.remove_feed(f.id)
    assert plan_render(proj).blocked


def test_plan_blocks_on_unwritable_output(proj, tmp_path):
    f = tmp_path / "iamafile"
    f.write_text("x")
    proj.rec.output_dir = str(f)  # 폴더가 아니라 파일
    plan = plan_render(proj)
    assert plan.blocked


def test_conflicts_are_reported_and_not_overwritten_when_declined(proj):
    plan = plan_render(proj)
    run(plan, proj)
    plan2 = plan_render(proj)
    assert plan2.conflicts and any("이미 존재" in i.message for i in plan2.issues)
    story = next(j for j in plan2.jobs if j.kind == "story")
    before = Path(story.out_path).stat().st_mtime_ns
    run(plan2, proj, overwrite=False)
    assert all(j.status == "skipped" for j in plan2.jobs if j.kind in ("story", "reels"))
    assert Path(story.out_path).stat().st_mtime_ns == before


def test_continue_after_error_then_retry_only_failed(proj, monkeypatch):
    real = engine.render_video
    calls = {"n": 0}

    def flaky(info, index, spec, final_path, *a, **k):
        if "REELS_01" in final_path and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("boom")
        return real(info, index, spec, final_path, *a, **k)

    monkeypatch.setattr(engine, "render_video", flaky)
    plan = plan_render(proj)
    q = run(plan, proj)
    status = {j.kind: j.status for j in plan.jobs}
    assert status["reels"] == "error" and status["story"] == "done" and status["feed_video"] == "done"  # 중단 없이 계속
    assert next(j for j in plan.jobs if j.kind == "reels").error  # 한국어 사용자 메시지
    q.retry_failed()
    q.wait(240)
    assert all(j.status == "done" for j in plan.jobs)
    assert calls["n"] == 1  # 성공 항목은 재실행 안 함


def test_cancel_leaves_no_partial_files(proj):
    plan = plan_render(proj)
    q = RenderQueue(proj.media, proj.index, plan.root, proj.project_data_files())
    q.start(plan.jobs, True)
    q.cancel_all()
    q.wait(120)
    assert not q.running
    assert all(j.status in ("done", "cancelled") for j in plan.jobs)
    assert not list(Path(plan.root).rglob("*.part"))


def test_long_credits_are_split_into_comment_files(proj):
    rows = "\n".join(f"Role{i:02d} | Person Number {i:02d} (@person_{i:02d})" for i in range(90))
    proj.set_credits_raw(rows)
    proj.parse_credits()
    proj.set_caption_body("본문 " * 20)
    plan = plan_render(proj)
    run(plan, proj)
    cdir = Path(plan.root) / "CAPTION"
    names = sorted(p.name for p in cdir.iterdir())
    assert "CAPTION.txt" in names and "COMMENT_01.txt" in names and "CREDITS_ORIGINAL.txt" in names
    cap = (cdir / "CAPTION.txt").read_text(encoding="utf-8")
    assert len(cap.encode("utf-16-le")) // 2 <= 2200
    joined = "\n".join([cap] + [(cdir / n).read_text(encoding="utf-8") for n in names if n.startswith("COMMENT_")])
    for i in range(90):
        assert f"Role{i:02d} | Person Number {i:02d} (@person_{i:02d})" in joined  # 행이 잘리거나 빠지지 않음


def test_stale_partial_files_from_a_killed_run_are_cleaned(proj):
    plan = plan_render(proj)
    stale = Path(plan.root) / "STORY" / "old_STORY_01.mp4.part"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"incomplete")
    run(plan, proj)
    assert not stale.exists() and not list(Path(plan.root).rglob("*.part"))
    log = json.loads((Path(plan.root) / "PROJECT_DATA" / "RENDER_LOG.json").read_text(encoding="utf-8"))
    assert any(e.get("kind") == "cleanup" for e in log["runs"][-1]["entries"])


def test_final_render_code_never_references_proxy():
    for mod in ("planner.py", "queue.py", "filters.py"):
        text = (ROOT / "ics" / "render" / mod).read_text(encoding="utf-8").lower()
        assert "proxy" not in text, f"{mod} 이(가) 프록시를 참조합니다"

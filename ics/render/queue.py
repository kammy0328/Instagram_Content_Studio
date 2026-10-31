"""렌더링 대기열: 백그라운드 스레드에서 순차 실행, 취소/실패 항목 재시도, 오류가 나도 계속 진행."""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from ..core.frameindex import FrameIndex
from ..core.layout import Geometry
from ..core.media import MediaInfo
from . import engine
from .planner import RenderJob


def _geo(d: dict) -> Geometry:
    return Geometry(**d)


class RenderQueue:
    def __init__(
        self,
        info: MediaInfo,
        index: FrameIndex,
        root: str,
        project_snapshot: dict | None = None,
        on_update: Callable[[RenderJob], None] | None = None,
        on_finished: Callable[[], None] | None = None,
    ):
        self.info, self.index, self.root = info, index, Path(root)
        self.on_update = on_update or (lambda j: None)
        self.on_finished = on_finished or (lambda: None)
        self.jobs: list[RenderJob] = []
        self.log: list[dict] = []
        self.project_snapshot = project_snapshot or {}
        self._thread: threading.Thread | None = None
        self._cancel_all = threading.Event()
        self._cancel_job = threading.Event()
        self._current: RenderJob | None = None
        self._lock = threading.Lock()
        self.running = False
        self.overwrite = True

    # --------------------------------------------------------------- 제어
    def start(self, jobs: list[RenderJob], overwrite: bool = True) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("이미 렌더링 중입니다.")
            self.jobs = jobs
            self.overwrite = overwrite
            self.running = True
            self._cancel_all.clear()
            for j in jobs:
                if j.status in ("cancelled", "error", "running"):
                    j.status, j.progress, j.error = "pending", 0.0, ""
        self._thread = threading.Thread(target=self._run, name="ics-render", daemon=True)
        self._thread.start()

    def cancel_all(self) -> None:
        self._cancel_all.set()
        self._cancel_job.set()

    def cancel_current(self) -> None:
        self._cancel_job.set()

    def retry_failed(self) -> None:
        failed = [j for j in self.jobs if j.status in ("error", "cancelled")]
        if not failed:
            return
        for j in failed:
            j.status, j.progress, j.error = "pending", 0.0, ""
            self.on_update(j)
        self.start(self.jobs, self.overwrite)

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    # --------------------------------------------------------------- 실행
    def _cleanup_partials(self) -> None:
        """이전 렌더링이 강제 종료되어 남은 불완전(.part) 파일을 정리한다."""
        removed = []
        try:
            for f in self.root.rglob("*.part"):
                try:
                    f.unlink()
                    removed.append(str(f))
                except OSError:
                    pass
        except OSError:
            pass
        if removed:
            self.log.append(
                {"kind": "cleanup", "label": "이전 렌더링의 불완전 파일 정리", "removed": removed, "status": "done"}
            )

    def _run(self) -> None:
        t0 = time.time()
        self._cleanup_partials()
        try:
            total_w = sum(max(1.0, j.est_bytes) for j in self.jobs if j.status == "pending")
            done_w = 0.0
            for j in self.jobs:
                if j.status != "pending":
                    continue
                if self._cancel_all.is_set():
                    j.status = "cancelled"
                    self.on_update(j)
                    continue
                self._cancel_job.clear()
                self._current = j
                self._execute(j, t0, done_w, total_w)
                done_w += max(1.0, j.est_bytes)
        finally:
            self._current = None
            self.running = False
            self._write_logs()
            self.on_finished()

    def _execute(self, j: RenderJob, t0: float, done_w: float, total_w: float) -> None:
        j.status, j.progress, j.error = "running", 0.0, ""
        self.on_update(j)
        start = time.time()
        w_self = max(1.0, j.est_bytes)

        def prog(p: float):
            j.progress = p
            frac_total = (done_w + w_self * p) / total_w if total_w else 0
            el = time.time() - t0
            j.eta = (el / frac_total - el) if frac_total > 0.03 else None
            self.on_update(j)

        entry = {
            "id": j.id,
            "kind": j.kind,
            "label": j.label,
            "out": j.out_path,
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            out = Path(j.out_path)
            if out.exists() and not self.overwrite:
                j.status = "skipped"
                entry["note"] = "기존 파일 유지(덮어쓰기 안 함)"
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                j.result = self._dispatch(j, prog)
                j.status, j.progress = "done", 1.0
        except engine.RenderCancelled:
            j.status, j.error = "cancelled", "사용자가 취소했습니다."
        except Exception as e:  # noqa: BLE001
            j.status = "error"
            j.error = self._friendly(e)
            entry["traceback"] = traceback.format_exc()[-2000:]
        entry.update(
            {
                "status": j.status,
                "error": j.error,
                "seconds": round(time.time() - start, 2),
                "result": {k: v for k, v in j.result.items() if k != "command"},
                "command": j.result.get("command"),
            }
        )
        self.log.append(entry)
        self.on_update(j)

    @staticmethod
    def _friendly(e: Exception) -> str:
        if isinstance(e, PermissionError):
            return f"파일 쓰기 권한이 없습니다: {e}"
        if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
            return "디스크 공간이 부족합니다."
        if isinstance(e, FileNotFoundError):
            return f"파일/경로를 찾을 수 없습니다 (NAS 연결 해제 등): {e}"
        return str(e)

    def _dispatch(self, j: RenderJob, prog) -> dict:
        p = j.params
        if j.kind in ("story", "reels", "feed_video"):
            spec = engine.VideoSpec(p["start"], p["end"], _geo(p["geo"]))
            return engine.render_video(self.info, self.index, spec, j.out_path, prog, self._cancel_job)
        if j.kind in ("feed_still", "reels_cover"):
            return engine.render_jpeg_still(self.info, self.index, p["frame"], _geo(p["geo"]), j.out_path)
        if j.kind == "original_still":
            return engine.render_original_still(self.info, self.index, p["frame"], j.out_path)
        if j.kind == "captions":
            return write_caption_files(Path(j.out_path).parent, p["caption"], p["comments"], p["credits_raw"])
        raise ValueError(j.kind)

    # --------------------------------------------------------------- 로그
    def _write_logs(self) -> None:
        try:
            d = self.root / "PROJECT_DATA"
            d.mkdir(parents=True, exist_ok=True)
            log_path = d / "RENDER_LOG.json"
            prev = []
            if log_path.exists():
                try:
                    prev = json.loads(log_path.read_text("utf-8")).get("runs", [])
                except Exception:  # noqa: BLE001
                    prev = []
            from ..core import ffbin

            prev.append(
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "ffmpeg": ffbin.versions(),
                    "source": self.info.path,
                    "entries": self.log,
                }
            )
            tmp = log_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"runs": prev[-20:]}, ensure_ascii=False, indent=1), "utf-8")
            os.replace(tmp, log_path)
            if self.project_snapshot:
                for name, data in self.project_snapshot.items():
                    t = d / name
                    t.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        except OSError:
            pass


def write_caption_files(cdir: Path, caption: str, comments: list[str], credits_raw: str) -> dict:
    cdir.mkdir(parents=True, exist_ok=True)
    written = []
    (cdir / "CAPTION.txt").write_text(caption, "utf-8")
    written.append("CAPTION.txt")
    for old in cdir.glob("COMMENT_*.txt"):
        old.unlink()
    for i, c in enumerate(comments, start=1):
        (cdir / f"COMMENT_{i:02d}.txt").write_text(c, "utf-8")
        written.append(f"COMMENT_{i:02d}.txt")
    (cdir / "CREDITS_ORIGINAL.txt").write_text(credits_raw, "utf-8")
    written.append("CREDITS_ORIGINAL.txt")
    return {"files": written, "path": str(cdir)}

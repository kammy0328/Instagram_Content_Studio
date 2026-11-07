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
            spec = engine.VideoSpec(p["start"], p["end"], _geo(p["geo"]), overlay_png=p.get("overlay_png"))
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
                replace_files_atomically(
                    d,
                    {
                        name: json.dumps(data, ensure_ascii=False, indent=1)
                        for name, data in self.project_snapshot.items()
                    },
                )
        except OSError:
            pass


def replace_files_atomically(target_dir: Path, files: dict[str, str], remove_glob: str | None = None) -> None:
    """여러 텍스트 파일을 '전부 새 내용 또는 전부 이전 내용' 으로 교체한다.
    1) 모든 새 내용을 같은 폴더의 임시 파일에 쓰고 fsync  2) 하나씩 os.replace  3) 중간에 실패하면 이미 바꾼 파일을 되돌린다.
    remove_glob 에 맞는 기존 파일 중 새 목록에 없는 것(예: 줄어든 COMMENT_NN)은 교체가 끝난 뒤 지우며, 실패하면 함께 복원한다."""
    target_dir.mkdir(parents=True, exist_ok=True)
    old: dict[str, bytes | None] = {}
    for name in files:
        f = target_dir / name
        old[name] = f.read_bytes() if f.exists() else None
    stale = [f for f in target_dir.glob(remove_glob) if f.name not in files] if remove_glob else []
    stale_old = {f.name: f.read_bytes() for f in stale}
    tmps: dict[str, Path] = {}
    done: list[str] = []
    try:
        for name, text in files.items():
            tmp = target_dir / (name + ".new")
            tmps[name] = tmp  # 쓰는 도중 실패해도 finally 에서 지울 수 있게 먼저 등록
            with tmp.open("w", encoding="utf-8", newline="") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
        for name, tmp in tmps.items():
            os.replace(tmp, target_dir / name)
            done.append(name)
        for f in stale:
            f.unlink()
    except BaseException:
        for name in done:  # 이미 바꾼 파일을 이전 내용으로 되돌린다
            f = target_dir / name
            try:
                if old[name] is None:
                    f.unlink(missing_ok=True)
                else:
                    f.write_bytes(old[name])
            except OSError:
                pass
        for fname, data in stale_old.items():
            try:
                (target_dir / fname).write_bytes(data)
            except OSError:
                pass
        raise
    finally:
        for tmp in tmps.values():
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def write_caption_files(cdir: Path, caption: str, comments: list[str], credits_raw: str) -> dict:
    files = {"CAPTION.txt": caption}
    for i, c in enumerate(comments, start=1):
        files[f"COMMENT_{i:02d}.txt"] = c
    files["CREDITS_ORIGINAL.txt"] = credits_raw
    replace_files_atomically(cdir, files, remove_glob="COMMENT_*.txt")
    return {"files": list(files), "path": str(cdir)}

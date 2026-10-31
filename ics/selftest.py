"""배포본(exe) 자체 점검.

`InstagramContentStudio.exe --selftest <결과파일>` 로 실행한다. 창을 띄우지 않는 exe(console=False)는 표준출력이
없으므로 결과를 파일로 쓰고, 모두 통과하면 종료 코드 0, 하나라도 실패하면 1 을 돌려준다.

점검 내용: 번들된 FFmpeg/FFprobe 탐색과 인코더, Qt 플랫폼 플러그인, 짧은 영상 생성 -> 분석 -> 프로젝트 ->
STORY/FEED 렌더 -> 결과 검증, 편집 창 생성. 사용자 데이터는 건드리지 않도록 임시 폴더에서만 동작한다.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path


class _Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failed = 0

    def add(self, text: str) -> None:
        self.lines.append(text)

    def run(self, name: str, fn) -> None:
        t0 = time.time()
        try:
            detail = fn()
            self.add(f"[PASS] {name}" + (f": {detail}" if detail else "") + f"  ({time.time() - t0:.1f}s)")
        except Exception as e:  # noqa: BLE001
            self.failed += 1
            self.add(f"[FAIL] {name}: {e}")
            self.add("".join("    " + ln for ln in traceback.format_exc().splitlines(keepends=True)))

    def text(self) -> str:
        status = "ALL PASSED" if not self.failed else f"{self.failed} FAILED"
        return "\n".join([*self.lines, "", f"RESULT: {status}", ""])


def _inside_bundle(path: str) -> bool:
    roots = []
    if getattr(sys, "_MEIPASS", None):
        roots.append(Path(sys._MEIPASS).resolve())
    roots.append(Path(sys.executable).resolve().parent)
    p = Path(path).resolve()
    return any(r == p or r in p.parents for r in roots)


def _check_environment(rep: _Report) -> None:
    frozen = bool(getattr(sys, "frozen", False))
    rep.add(f"python    : {sys.version.split()[0]} ({platform.platform()})")
    rep.add(f"frozen    : {frozen}  exe={sys.executable}")
    rep.add(f"meipass   : {getattr(sys, '_MEIPASS', '-')}")
    rep.add(f"QT_QPA    : {os.environ.get('QT_QPA_PLATFORM', '(default)')}")
    rep.add("")


def _check_ffmpeg() -> str:
    from .core import ffbin

    ff, fp = ffbin.ffmpeg(), ffbin.ffprobe()
    if getattr(sys, "frozen", False) and not (_inside_bundle(ff) and _inside_bundle(fp)):
        raise RuntimeError(f"번들 밖의 FFmpeg 를 사용 중입니다: {ff}")
    v = ffbin.versions()
    missing = [e for e in ("libx264", "aac", "prores_ks", "mjpeg") if not ffbin.has_encoder(e)]
    if missing:
        raise RuntimeError(f"필수 인코더 없음: {missing}")
    zimg = "zscale" if ffbin.has_filter("zscale") else "zscale 없음(swscale 사용)"
    return f"{v['ffmpeg']} | {zimg} | {ff}"


def _check_qt() -> str:
    from PySide6.QtCore import QLibraryInfo, qVersion
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    found = sorted(p.name for p in (plugins / "platforms").glob("*")) if (plugins / "platforms").is_dir() else []
    if getattr(sys, "frozen", False) and sys.platform.startswith("win") and "qwindows.dll" not in found:
        raise RuntimeError(f"qwindows.dll 이 번들에 없습니다 ({plugins}): {found}")
    return f"Qt {qVersion()}, platform={app.platformName()}, plugins={found}"


def _make_clip(path: Path) -> None:
    from .core import ffbin

    cmd = [
        ffbin.ffmpeg(), "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30000/1001:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
        "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-c:a", "aac", "-shortest", str(path),
    ]  # fmt: skip
    p = ffbin.run(cmd, timeout=120)
    if p.returncode != 0 or not path.is_file():
        raise RuntimeError("테스트 영상 생성 실패: " + p.stderr.decode("utf-8", "replace")[-400:])


def _check_pipeline(work: Path, qt_pump) -> str:
    from . import paths
    from .core import frameindex, media
    from .core.iddict import IdDictionary
    from .core.project import Project
    from .core.store import Library
    from .render.planner import plan_render
    from .render.queue import RenderQueue
    from .ui.main_window import EditorWindow
    from .ui.settings import Settings

    clip = work / "selftest_clip.mp4"
    _make_clip(clip)
    info = media.analyze(str(clip))
    idx = frameindex.build_frame_index(info)
    if not 85 <= idx.count <= 95:
        raise RuntimeError(f"프레임 수가 예상(약 90)과 다릅니다: {idx.count}")

    lib = Library()
    rec = lib.create("SELFTEST", "ARTIST", "TITLE", str(clip), str(work / "out"))
    proj = Project(rec, lib)
    proj.open()
    try:
        proj.attach_media(info, idx)
        proj.set_range("story", 10, 33)
        proj.add_feed_still(40)
        proj.add_feed_video(50, 73)
        proj.set_caption_body("Selftest #ok")
        plan = plan_render(proj)
        if plan.blocked:
            raise RuntimeError("렌더 계획이 차단됨: " + "; ".join(i.message for i in plan.issues))
        q = RenderQueue(proj.media, proj.index, plan.root, proj.project_data_files())
        q.start(plan.jobs, True)
        q.wait(240)
        bad = [(j.label, j.status, j.error) for j in plan.jobs if j.status != "done"]
        if bad:
            raise RuntimeError(f"렌더 실패: {bad}")
        outs = [Path(j.out_path) for j in plan.jobs]
        empty = [str(o) for o in outs if not o.is_file() or o.stat().st_size == 0]
        if empty:
            raise RuntimeError(f"출력 누락/빈 파일: {empty}")
        if list(Path(plan.root).rglob("*.part")):
            raise RuntimeError(".part 임시 파일이 남았습니다")

        w = EditorWindow(proj, Settings(), IdDictionary(), paths.cache_dir(rec.id))
        w.resize(1400, 860)
        w.show()
        qt_pump(600)
        w.close()
        qt_pump(200)
        return f"렌더 {len(outs)}개 완료, 편집 창 생성/종료 OK"
    finally:
        proj.close()


def run_selftest(outfile: str | None = None) -> int:
    rep = _Report()
    work = Path(tempfile.mkdtemp(prefix="ics_selftest_"))
    os.environ["ICS_DATA_DIR"] = str(work / "appdata")
    os.environ["ICS_CACHE_DIR"] = str(work / "cache")
    # 화면이 없는 CI/서비스 계정에서도 동작하도록 오프스크린. 이미 지정돼 있으면 존중한다.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    def pump(ms: int) -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        end = time.time() + ms / 1000
        while app is not None and time.time() < end:
            app.processEvents()
            time.sleep(0.005)

    try:
        _check_environment(rep)
        rep.run("FFmpeg/FFprobe 탐색과 인코더", _check_ffmpeg)
        rep.run("Qt 로드와 플랫폼 플러그인", _check_qt)
        rep.run("영상 생성 -> 분석 -> 렌더 -> 편집 창", lambda: _check_pipeline(work, pump))
    except Exception:  # noqa: BLE001
        rep.failed += 1
        rep.add("[FAIL] 자체 점검 중 예외\n" + traceback.format_exc())
    finally:
        shutil.rmtree(work, ignore_errors=True)

    text = rep.text()
    target = Path(outfile) if outfile else Path(tempfile.gettempdir()) / "ics_selftest.txt"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError:
        pass
    if sys.stdout is not None:
        try:
            sys.stdout.write(text)
        except (OSError, UnicodeError):
            pass
    return 1 if rep.failed else 0

"""배포본(exe) 자체 점검.

`InstagramContentStudio.exe --selftest <결과파일>` 로 실행한다. 창을 띄우지 않는 exe(console=False)는 표준출력이
없으므로 결과를 파일로 쓰고, 모두 통과하면 종료 코드 0, 하나라도 실패하면 1 을 돌려준다.

점검 내용: 번들된 FFmpeg/FFprobe 탐색과 인코더, Qt 플랫폼 플러그인, 짧은 영상 생성 -> 분석 -> 프로젝트 ->
STORY/FEED 렌더 -> 결과 검증, 편집 창 생성, 소리 경로(재생용 WAV 생성과 출력 장치). 사용자 데이터는 건드리지 않도록
임시 폴더에서만 동작한다. 점검이 도중에 비정상 종료해도 흔적이 남도록 항목이 끝날 때마다 결과 파일을 갱신한다.
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
    def __init__(self, path: Path | None = None) -> None:
        self.lines: list[str] = []
        self.failed = 0
        self.path = path

    def flush(self, final: bool = False) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                self.text() if final else "\n".join([*self.lines, "", "(진행 중)", ""]), encoding="utf-8"
            )
        except OSError:
            pass

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
        self.flush()

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


_TAG_FIELDS = "stream=color_space,color_primaries,color_transfer,color_range"


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
    from .core import ffbin, frameindex, media
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
        story = next(o for o in outs if o.suffix == ".mp4" and "STORY" in o.parts)
        vs = ffbin.probe_json(str(story), "-select_streams", "v:0", "-show_entries", _TAG_FIELDS)["streams"][0]
        tags = {k: vs.get(k) for k in _TAG_FIELDS.split("=")[1].split(",")}
        want = {"color_space": "bt709", "color_primaries": "bt709", "color_transfer": "bt709", "color_range": "tv"}
        if any(tags[k] != v for k, v in want.items()):
            raise RuntimeError(
                f"출력 색 태그가 BT.709 Limited 가 아닙니다: {tags} (FFmpeg: {ffbin.versions()['ffmpeg']})"
            )

        w = EditorWindow(proj, Settings(), IdDictionary(), paths.cache_dir(rec.id))
        w.resize(1400, 860)
        w.show()
        qt_pump(600)
        w.close()
        qt_pump(200)
        return f"렌더 {len(outs)}개 완료, 편집 창 생성/종료 OK"
    finally:
        proj.close()


def _check_letterbox(work: Path) -> str:
    from .core import ffbin, frameindex, media
    from .core.layout import LayoutParams, compute_geometry
    from .core.letterbox import detect_letterbox
    from .render import engine

    src = work / "letterbox.mp4"
    p = ffbin.run([
        ffbin.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=0x80a0c0:s=320x132:r=24:d=1,pad=320:180:0:24:black",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src),
    ], timeout=60)  # fmt: skip
    if p.returncode:
        raise RuntimeError("검정 띠 테스트 영상 생성 실패")
    info = media.analyze(str(src))
    idx = frameindex.build_frame_index(info)
    crop = detect_letterbox(info, idx, 0, idx.count)
    if crop != (0, 24, 320, 132):
        raise RuntimeError(f"검정 띠 감지 영역 불일치: {crop}")
    for kind in ("916", "45", "orig"):
        g = compute_geometry(320, 180, kind, LayoutParams(auto_crop=True, source_crop=crop))
        arr = engine.decode_still_rgb(info, idx, 3, g)
        if g.mode == "fit":
            arr = arr[g.pad_y : g.pad_y + g.content_h, g.pad_x : g.pad_x + g.content_w]
        if arr[4:-4, 4:-4].mean(axis=2).min() < 70:
            raise RuntimeError(f"{kind}: 크롭 후 검정 띠가 남았습니다.")
        result = engine.render_video(info, idx, engine.VideoSpec(3, 9, g), str(work / f"crop_{kind}.mp4"))
        if result["frames"] != 6:
            raise RuntimeError(f"{kind}: 출력 프레임 수 불일치")
    return "영역 320×132 감지, 9:16/4:5/원본 비율 스틸·영상 출력 통과"


def _check_audio(work: Path, qt_pump) -> str:
    """소리 경로: 번들 FFmpeg 로 재생용 WAV 가 만들어지는지, Qt Multimedia 가 로드되는지, 출력 장치가 있으면 재생이 시작되는지.

    출력 장치가 없는 환경(CI, 서버)은 정상으로 본다. 장치가 있는데 재생을 시작하지 못하면 실패로 본다."""
    from PySide6.QtCore import QLibraryInfo

    from .core import media
    from .ui import services

    clip = work / "audio_clip.mp4"
    _make_clip(clip)
    info = media.analyze(str(clip))
    cache = work / "audio_cache"
    cache.mkdir(exist_ok=True)
    pb = services.ProxyBuilder(info, cache)
    err = pb.build_audio()
    if err:
        raise RuntimeError(err)
    size = pb.audio_path.stat().st_size
    want = 3.0 * 48000 * 4  # 3초, 48kHz, 스테레오, 16bit
    if not 0.8 * want <= size <= 1.2 * want:
        raise RuntimeError(f"재생용 WAV 크기가 예상과 다릅니다: {size} (예상 약 {int(want)})")
    player = services.AudioPlayer(str(pb.audio_path))
    if not player.ok:
        raise RuntimeError(player.error)
    plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "multimedia"
    found = sorted(p.name for p in plugins.glob("*")) if plugins.is_dir() else []
    outs = services.audio_output_names()
    dev = services.default_audio_output()
    head = f"WAV {size // 1024}KB 생성 OK, multimedia 플러그인={found}, 출력 장치 {len(outs)}개 {outs}"
    if dev is None:
        return head + " -> 기본 출력 장치 없음 (CI/서버에서는 정상)"
    player.play(0.0)
    qt_pump(400)
    state = player._sink.state().name if player._sink is not None else "-"
    err = player.error
    player.stop()
    if err:
        raise RuntimeError(f"{head} | 재생 시작 실패: {err}")
    return head + f" -> 기본 장치 '{dev.description()}' 재생 시작 OK (상태 {state})"


def _check_publisher(work: Path) -> str:
    """Publisher: 모듈이 번들에 들어갔는지, 비밀값 보관(Windows 에서는 실제 DPAPI), 저장소/수동 내보내기가 동작하는지."""
    import importlib

    for m in (
        "adapter",
        "capability",
        "cloud",
        "engine",
        "models",
        "relay",
        "scheduler",
        "secrets",
        "service",
        "store",
        "transport",
        "validate",
    ):
        importlib.import_module(f"ics.publisher.{m}")
    from .publisher.models import Kind
    from .publisher.secrets import default_secret_store
    from .publisher.service import PublisherService

    d = work / "publisher"
    d.mkdir(parents=True, exist_ok=True)
    sec = default_secret_store(d / "secrets.json")
    sec.set("selftest", "IGQVJ_SELFTEST_VALUE_0123456789")
    if sec.persistent:
        if sec.get("selftest") != "IGQVJ_SELFTEST_VALUE_0123456789":
            raise RuntimeError("DPAPI 복호화 결과가 다릅니다.")
        if "SELFTEST_VALUE" in (d / "secrets.json").read_text("utf-8"):
            raise RuntimeError("비밀값이 평문으로 저장되었습니다.")
    sec.delete("selftest")

    class _S:
        def __init__(self):
            self.v = {}

        def get(self, k, default=""):
            return self.v.get(k, default)

        def set(self, k, v):
            self.v[k] = v

    svc = PublisherService(d, _S(), sec)
    if len(svc.missing_setup()) != 4:
        raise RuntimeError("설정 부족 점검이 동작하지 않습니다.")
    root = work / "out" / "P"
    for sub in ("STORY", "REELS", "FEED", "CAPTION"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "STORY" / "P_STORY_01.mp4").write_bytes(b"\0\0\0\x18ftypmp42" + b"\0" * 16)
    (root / "CAPTION" / "CAPTION.txt").write_text("본문", "utf-8")
    files = svc.export_manual(root, work / "manual", {Kind.STORY})
    return f"모듈 로드 OK, 비밀값 보관({'DPAPI' if sec.persistent else '메모리'}), 수동 내보내기 {len(files)}개"


def run_selftest(outfile: str | None = None) -> int:
    target = Path(outfile) if outfile else Path(tempfile.gettempdir()) / "ics_selftest.txt"
    rep = _Report(target)
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
        rep.run("검정 띠 감지와 자동 크롭 출력", lambda: _check_letterbox(work))
        rep.run("소리: 재생용 WAV 생성과 출력 장치", lambda: _check_audio(work, pump))
        rep.run("Publisher: 모듈/비밀값 보관/수동 내보내기", lambda: _check_publisher(work))
    except Exception:  # noqa: BLE001
        rep.failed += 1
        rep.add("[FAIL] 자체 점검 중 예외\n" + traceback.format_exc())
    finally:
        shutil.rmtree(work, ignore_errors=True)

    text = rep.text()
    rep.flush(final=True)
    if sys.stdout is not None:
        try:
            sys.stdout.write(text)
        except (OSError, UnicodeError):
            pass
    return 1 if rep.failed else 0

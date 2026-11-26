"""배포본 점검 (실제 화면): 편집 창에서 [··· > 업데이트…] 를 실제 마우스 이벤트로 눌러, 창이 보이고 맨 앞에 있고
단추가 눌리는지 단계마다 확인한다. 서버와 설치는 가짜로 바꿔 네트워크 없이 끝까지 간다.

  InstagramContentStudio.exe --selftest-update-ui <결과 파일>

오프스크린이 아니라 운영체제 창으로 띄우므로 창 순서(다른 창 뒤에 숨음), 투명도(페이드가 안 끝남) 문제를 잡는다."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

DEADLINE_S = 90


def run_update_ui_selftest(outfile: str) -> int:
    out = Path(outfile)
    work = Path(tempfile.mkdtemp(prefix="ics_selftest_ui_"))
    os.environ["ICS_DATA_DIR"] = str(work / "appdata")
    os.environ["ICS_CACHE_DIR"] = str(work / "cache")
    lines: list[str] = []
    failed: list[str] = []

    def log(s: str) -> None:
        lines.append(f"{time.strftime('%H:%M:%S')} {s}")
        try:
            out.write_text("\n".join([*lines, "", "(진행 중)", ""]), encoding="utf-8")
        except OSError:
            pass

    code = 1
    try:
        code = _run(work, log, failed)
    except Exception:  # noqa: BLE001
        failed.append("예외")
        log("[FAIL] 예외\n" + traceback.format_exc())
    finally:
        try:
            from . import paths

            tr = paths.logs_dir() / "update.log"
            if tr.is_file():
                lines.append("---- update.log")
                lines.append(tr.read_text(encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            pass
        lines.append("")
        lines.append("RESULT: " + ("ALL PASSED" if code == 0 and not failed else f"FAILED {failed}"))
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        shutil.rmtree(work, ignore_errors=True)
    return 0 if code == 0 and not failed else 1


def _run(work: Path, log, failed: list[str]) -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QToolButton

    from . import paths, updater
    from .core import frameindex, media
    from .core.iddict import IdDictionary
    from .core.project import Project
    from .core.store import Library
    from .selftest import _make_clip
    from .ui import theme, update_ui
    from .ui.main_window import EditorWindow
    from .ui.settings import Settings
    from .ui.widgets import studio_icon

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    theme.apply(app)
    app.setWindowIcon(studio_icon())
    log(f"플랫폼 {QGuiApplication.platformName()}, 화면 {app.primaryScreen().geometry().getRect()}")

    clip = work / "clip.mp4"
    _make_clip(clip)
    info = media.analyze(str(clip))
    idx = frameindex.build_frame_index(info)
    lib = Library()
    rec = lib.create("UPDATE_UI", "A", "T", str(clip), str(work / "out"))
    proj = Project(rec, lib)
    proj.open()
    proj.attach_media(info, idx)
    win = EditorWindow(proj, Settings(), IdDictionary(), paths.cache_dir(rec.id))
    av = app.primaryScreen().availableGeometry()  # CI 화면(1024x768)에서도 단추가 화면 안에 오게
    win.resize(min(1400, av.width() - 20), min(860, av.height() - 20))
    win.move(av.x() + 10, av.y() + 10)
    win.show()
    win.raise_()
    win.activateWindow()

    # ---- 서버/설치는 가짜 (네트워크 없이, 실제 창 흐름만)
    fake = updater.UpdateInfo("ffff000", "9.9.9", "selftest", "https://x/setup.exe", "setup.exe", 1000, "0" * 64)
    events: list[str] = []

    def fake_check(*_a, **_k):
        time.sleep(0.6)  # 확인 중 창이 보일 시간
        return fake

    def fake_download(_info, _token, dest, progress=None, cancelled=None):
        dest.mkdir(parents=True, exist_ok=True)
        for i in range(1, 6):
            time.sleep(0.15)
            if progress:
                progress(i / 5)
        p = dest / "setup_selftest.exe"
        p.write_bytes(b"x")
        events.append("downloaded")
        return p

    def fake_launch(_p):
        events.append("launch")
        return 4242

    def fake_shutdown():
        events.append("shutdown")
        for w in QApplication.topLevelWidgets():
            if isinstance(w, update_ui.UpdateDialog) and w.isVisible():
                w.accept()

    updater.check = fake_check
    updater.download = fake_download
    updater.launch_installer = fake_launch
    update_ui.shutdown_for_update = fake_shutdown

    def visible_tops():
        return [w for w in QApplication.topLevelWidgets() if w.isVisible()]

    def describe() -> str:
        return "; ".join(
            f"{type(w).__name__}('{w.windowTitle()}', op={w.windowOpacity():.2f}, active={w.isActiveWindow()})"
            for w in visible_tops()
        )

    def on_top(widget) -> bool:
        """운영체제 창 순서로 widget 위치에 실제로 그 창이 보이는가 (다른 창 뒤에 숨지 않았나)."""
        c = widget.mapToGlobal(QPoint(widget.width() // 2, widget.height() // 2))
        hit = QApplication.widgetAt(c)
        return hit is not None and hit.window() is widget.window()

    def check(cond: bool, what: str) -> None:
        log(("[PASS] " if cond else "[FAIL] ") + what)
        if not cond:
            failed.append(what)

    state = {"step": "start", "t": time.time(), "t0": time.time(), "phase": "menu"}

    def goto(step: str) -> None:
        state["step"], state["t"] = step, time.time()

    def elapsed() -> float:
        return time.time() - state["t"]

    def find(cls, pred=lambda w: True):
        for w in visible_tops():
            if isinstance(w, cls) and pred(w):
                return w
        return None

    def tick() -> None:
        try:
            _tick()
        except Exception:  # noqa: BLE001
            failed.append("tick 예외")
            log("[FAIL] " + traceback.format_exc())
            finish()

    def finish() -> None:
        timer.stop()
        for w in visible_tops():
            if w is not win:
                w.close()
        QTimer.singleShot(200, app.quit)

    def _tick() -> None:
        s = state["step"]
        if time.time() - state["t0"] > DEADLINE_S:
            check(False, f"시간 초과 (단계 {s}): {describe()}")
            finish()
            return
        if s == "start" and elapsed() > 1.5:
            tb = [b for b in win.findChildren(QToolButton) if b.accessibleName() == "도구"][0]
            check(on_top(tb), "편집 창의 ··· 단추가 화면 맨 앞에 보임")
            goto("menu")
            QTimer.singleShot(0, lambda: QTest.mouseClick(tb, Qt.MouseButton.LeftButton))
        elif s == "menu":
            m = find(QMenu)
            if m is None:
                if elapsed() > 5:
                    check(False, f"··· 를 눌러도 메뉴가 열리지 않음: {describe()}")
                    finish()
                return
            act = [a for a in m.actions() if a.text() == "업데이트…"][0]
            check(True, "메뉴 열림")
            goto("checking")
            pos = m.actionGeometry(act).center()
            QTimer.singleShot(150, lambda: QTest.mouseClick(m, Qt.MouseButton.LeftButton, pos=pos))
        elif s == "checking":
            box = find(QMessageBox)
            if box is not None:
                check(on_top(box), f"확인 중 창이 맨 앞에 보임 ({describe()})")
                check(find(QMenu) is None, "메뉴가 닫힘")
                goto("dialog")
            elif find(update_ui.UpdateDialog) is not None:
                goto("dialog")
            elif elapsed() > 6:
                check(False, f"업데이트… 를 눌러도 아무 창이 없음: {describe()}")
                finish()
        elif s == "dialog":
            d = find(update_ui.UpdateDialog)
            if d is None:
                if elapsed() > 8:
                    check(False, f"새 버전 창이 나오지 않음: {describe()}")
                    finish()
                return
            if elapsed() < 0.8:  # 페이드가 끝날 시간
                return
            check(d.windowOpacity() >= 0.99, f"새 버전 창이 불투명함 (op={d.windowOpacity():.2f})")
            check(on_top(d.go), f"[지금 업데이트] 단추가 맨 앞에 보임 ({describe()})")
            check(d.isActiveWindow(), "새 버전 창이 활성 창")
            goto("download")
            QTest.mouseClick(d.go, Qt.MouseButton.LeftButton)
        elif s == "download":
            if "shutdown" in events:
                check(events[:3] == ["downloaded", "launch", "shutdown"], f"내려받기 → 실행기 → 종료 순서 {events}")
                goto("auto")
            elif elapsed() > 10:
                check(False, f"[지금 업데이트] 후 진행 안 됨: {events}, {describe()}")
                finish()
        elif s == "auto" and elapsed() > 0.8:
            # 실행 직후 자동 확인이 새 버전을 찾은 경우 (부모 없이 뜨던 창)
            win.raise_()
            win.activateWindow()
            goto("auto_wait")
            QTimer.singleShot(300, lambda: update_ui.AutoUpdate(Settings())._done(fake, ""))
        elif s == "auto_wait":
            d = find(update_ui.UpdateDialog)
            if d is None:
                if elapsed() > 6:
                    check(False, f"자동 확인 창이 나오지 않음: {describe()}")
                    finish()
                return
            if elapsed() < 1.2:
                return
            check(d.windowOpacity() >= 0.99, f"자동 확인 창이 불투명함 (op={d.windowOpacity():.2f})")
            check(on_top(d.later), f"자동 확인 창이 편집 창 앞에 보임 ({describe()})")
            goto("auto_close")
            QTest.mouseClick(d.later, Qt.MouseButton.LeftButton)
        elif s == "auto_close" and elapsed() > 0.6:
            check(find(update_ui.UpdateDialog) is None, "[나중에] 로 닫힘")
            check(on_top(win.preview), "닫은 뒤 편집 창이 다시 맨 앞")
            log(f"끝: {time.time() - state['t0']:.1f}s, {describe()}")
            finish()

    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(tick)
    timer.start()
    app.exec()
    win.close()
    proj.close()
    return 0

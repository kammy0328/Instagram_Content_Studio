"""업데이트 확인/설치 창. 사용자가 [지금 업데이트]를 눌렀을 때만 내려받고 설치한다."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from .. import paths, updater
from ..publisher.secrets import default_secret_store
from .frameless import ChromeDialog

TOKEN_KEY = "github_token"
TRACE_MAX = 200_000  # 기록 파일이 이보다 커지면 앞부분을 버린다


def trace(msg: str) -> None:
    """업데이트 단계 기록 (logs/update.log). '눌렀는데 반응이 없다' 같은 문제를 어디서 멈췄는지로 찾는다."""
    try:
        f = paths.logs_dir() / "update.log"
        if f.is_file() and f.stat().st_size > TRACE_MAX:
            f.write_text(f.read_text(encoding="utf-8", errors="replace")[-TRACE_MAX // 2 :], encoding="utf-8")
        with f.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{updater.current_label()}] {msg}\n")
    except Exception:  # noqa: BLE001 (기록 실패가 업데이트를 막으면 안 된다)
        pass


def _owner(parent):
    """부모가 없으면 지금 활성 창(없으면 보이는 편집 창)을 부모로: 부모 없는 창은 편집 창 뒤로 숨어
    모달인 채 보이지 않을 수 있다 (그러면 앱 전체가 눌러도 반응이 없는 것처럼 된다)."""
    if parent is not None:
        return parent
    app = QApplication.instance()
    if app is None:
        return None
    w = app.activeWindow()
    if w is not None:
        return w
    vis = [x for x in app.topLevelWidgets() if x.isVisible() and not isinstance(x, QMenu)]
    return vis[0] if vis else None


def _front(w) -> None:
    """창을 맨 앞으로 (설치 후 다시 열린 앱처럼 포커스가 없는 경우에도)."""
    w.raise_()
    w.activateWindow()


def secret_store():
    return default_secret_store(paths.app_data_dir() / "update_secrets.json")


class CheckWorker(QObject):
    done = Signal(object, str)  # (UpdateInfo | None, 오류 메시지)

    def __init__(self, repo: str, channel: str, token: str):
        super().__init__()
        self._args = (repo, channel, token)

    def start(self) -> None:
        threading.Thread(target=self._run, name="ics-update-check", daemon=True).start()

    def _run(self) -> None:
        try:
            info, err = updater.check(*self._args), ""
        except updater.UpdateError as e:
            info, err = None, str(e)
        except Exception as e:  # noqa: BLE001 (업데이트 확인 실패가 앱 동작을 막으면 안 된다)
            info, err = None, f"업데이트 확인 실패: {type(e).__name__}"
        trace(f"check result: {'new ' + info.build_id if info else 'none'}{' err=' + err if err else ''}")
        self.done.emit(info, err)


class UpdateDialog(ChromeDialog):
    _prog = Signal(float)
    _finished = Signal(str, str)  # (설치 파일 경로, 오류)

    def __init__(self, info: updater.UpdateInfo, token: str, parent=None):
        super().__init__(parent)
        self.info, self.token = info, token
        self.setWindowTitle("업데이트")
        self.setMinimumWidth(460)
        self._cancel = threading.Event()
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        h = QLabel("새 버전이 있습니다")
        h.setObjectName("h1")
        v.addWidget(h)
        mb = f" · {info.size / 1e6:.0f}MB" if info.size else ""
        newv = f"v{info.version} ({info.build_id})" if info.version else f"빌드 {info.build_id}"
        v.addWidget(QLabel(f"현재 {updater.current_label()}  →  새 버전 {newv}{mb}"))
        if info.notes:
            n = QLabel(info.notes)
            n.setWordWrap(True)
            n.setObjectName("muted")
            v.addWidget(n)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.hide()
        v.addWidget(self.bar)
        self.msg = QLabel("설치하면 프로그램이 잠시 닫혔다가 다시 열립니다. 프로젝트와 설정은 그대로 유지됩니다.")
        self.msg.setWordWrap(True)
        self.msg.setObjectName("muted")
        v.addWidget(self.msg)
        row = QHBoxLayout()
        row.addStretch(1)
        self.later = QPushButton("나중에")
        self.later.clicked.connect(self._on_later)
        self.go = QPushButton("지금 업데이트")
        self.go.setObjectName("primary")
        self.go.clicked.connect(self._start)
        row.addWidget(self.later)
        row.addWidget(self.go)
        v.addLayout(row)
        self._prog.connect(self._on_progress)
        self._finished.connect(self._on_finished)
        self._last_pct = -1

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        trace(f"dialog shown (new {self.info.build_id})")
        QTimer.singleShot(0, lambda: _front(self))

    def _on_progress(self, p: float) -> None:
        """진행률을 숫자로도 보여 준다 (막대만 있으면 큰 파일을 받는 동안 멈춘 것처럼 보인다)."""
        self.bar.setValue(int(p * 1000))
        pct = int(p * 4) * 25
        if pct != self._last_pct:
            self._last_pct = pct
            trace(f"download {pct}%")
        total = self.info.size / 1e6 if self.info.size else 0
        if p >= 1.0:
            self.msg.setText("내려받기 완료 · 파일 확인 중…")
        elif total:
            self.msg.setText(f"내려받는 중… {p * 100:.0f}%  ({p * total:.0f} / {total:.0f}MB)")
        else:
            self.msg.setText(f"내려받는 중… {p * 100:.0f}%")

    def _on_later(self) -> None:
        trace("later")
        self._cancel.set()
        self.reject()

    def _start(self) -> None:
        trace("go clicked")
        self.go.setEnabled(False)
        self.bar.show()
        self.msg.setText("내려받는 중…")

        def run():
            try:
                import tempfile
                from pathlib import Path

                p = updater.download(
                    self.info,
                    self.token,
                    Path(tempfile.gettempdir()) / "ics_update",
                    self._prog.emit,
                    self._cancel.is_set,
                )
                self._finished.emit(str(p), "")
            except Exception as e:  # noqa: BLE001
                self._finished.emit("", str(e) or type(e).__name__)

        threading.Thread(target=run, name="ics-update-download", daemon=True).start()

    def _on_finished(self, path: str, err: str) -> None:
        trace(f"download finished{' err=' + err if err else ''}")
        if err:
            self.msg.setText(f"실패: {err}")
            self.go.setEnabled(True)
            return
        from pathlib import Path

        try:
            pid = updater.launch_installer(Path(path))
            trace(f"launcher started pid={pid}")
        except Exception as e:  # noqa: BLE001
            trace(f"launch failed: {e!r}")
            self.msg.setText(f"설치 파일을 실행하지 못했습니다: {e}\n파일: {path}")
            self.go.setEnabled(True)
            return
        self.msg.setText("설치를 시작합니다. 프로그램이 닫혔다가 1분 안에 자동으로 다시 열립니다.")
        QTimer.singleShot(300, shutdown_for_update)


def shutdown_for_update() -> None:
    """업데이트 설치를 위해 앱을 확실히 끝낸다. 편집 창은 정상적으로 닫아(자동저장/정상 종료 기록) 두고,
    남아 있는 백그라운드 스레드 때문에 프로세스가 살아 있지 않도록 마지막에 프로세스를 직접 끝낸다.
    (프로세스가 남아 있으면 설치 파일이 실행 파일을 바꾸지 못해 업데이트 후 다시 열리지 않는다.)"""
    import os

    trace("shutdown for update")
    app = QApplication.instance()
    for w in list(app.topLevelWidgets()) if app else []:
        if w.isVisible() and not isinstance(w, QDialog):
            w.close()
    if app is not None:
        app.processEvents()
    os._exit(0)


def report_last_update(parent=None) -> None:
    """지난 업데이트 결과를 한 번 알려 준다 (실패하면 이유와 기록 위치까지). 기록이 없으면 아무것도 하지 않는다."""
    try:
        res = updater.last_update_result()
    except Exception:  # noqa: BLE001
        return
    if res is None:
        return
    state, detail = res
    trace(f"last update result: {state}")
    box = QMessageBox(_owner(parent))
    box.setWindowTitle("업데이트")
    if state == "ok":
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"업데이트를 마쳤습니다. (현재 {updater.current_label()})")
    else:
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            "지난 업데이트 설치가 끝나지 않았습니다. 예전 버전으로 다시 열었습니다.\n"
            "[자세히] 의 기록을 알려 주시면 원인을 찾을 수 있습니다.\n"
            f"기록 위치: {updater.update_dir()}"
        )
        box.setDetailedText(detail)
    box.exec()


def check_and_prompt(settings, parent=None) -> None:
    """[업데이트…] 메뉴: 버전을 확인하고, 새 버전이 있으면 설치할지만 묻는다. 입력할 값은 없다."""
    trace("menu: check")
    parent = _owner(parent)
    box = QMessageBox(parent)
    box.setWindowTitle("업데이트")
    box.setText("새 버전을 확인하는 중…")
    # 취소 단추: 서버가 늦거나 결과가 오지 않아도 이 창에 갇히지 않는다 (예전: 단추 없는 창이 남아 멈춘 것처럼 보임)
    box.setStandardButtons(QMessageBox.StandardButton.Cancel)
    box.button(QMessageBox.StandardButton.Cancel).setText("취소")
    box.setModal(True)
    worker = CheckWorker(*current_args(settings))
    result: dict = {}

    def done(info, err):
        result["v"] = (info, err)
        box.done(0)

    worker.done.connect(done)
    worker.start()
    QTimer.singleShot(0, lambda: _front(box))
    box.exec()
    if "v" not in result:
        trace("check cancelled")
        return
    info, err = result["v"]
    settings.set("update_last_check", time.time())
    if err:
        QMessageBox.warning(parent, "업데이트", err)
    elif info is None:
        QMessageBox.information(
            parent,
            "업데이트",
            "최신 버전입니다." if updater.BUILD_ID != "dev" else "개발 실행에서는 업데이트를 확인하지 않습니다.",
        )
    else:
        UpdateDialog(info, updater.default_token(secret_store().get(TOKEN_KEY)), parent).exec()


def current_args(settings) -> tuple[str, str, str]:
    return (
        settings.get("update_repo", updater.DEFAULT_REPO) or updater.DEFAULT_REPO,
        updater.current_channel(settings.get("update_channel", "")),
        updater.default_token(secret_store().get(TOKEN_KEY)),
    )


class AutoUpdate(QObject):
    """실행 직후 한 번, 간격이 지났을 때만 조용히 확인한다. 새 버전이 있을 때만 창을 띄운다."""

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._worker: CheckWorker | None = None

    def start(self) -> None:
        if updater.BUILD_ID == "dev" or not self.settings.get("update_auto", True):
            return
        if not updater.due(float(self.settings.get("update_last_check", 0))):
            return
        trace("auto: check")
        self._worker = CheckWorker(*current_args(self.settings))
        self._worker.done.connect(self._done)
        self._worker.start()

    def _done(self, info, err: str) -> None:
        if not err:
            self.settings.set("update_last_check", time.time())
        if info is not None:
            UpdateDialog(info, updater.default_token(secret_store().get(TOKEN_KEY)), _owner(None)).exec()

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
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from .. import paths, updater
from ..publisher.secrets import default_secret_store

TOKEN_KEY = "github_token"


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
        self.done.emit(info, err)


class UpdateDialog(QDialog):
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
        v.addWidget(QLabel(f"현재 {updater.current_label()}  →  새 빌드 {info.build_id}{mb}"))
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
        self._prog.connect(lambda p: self.bar.setValue(int(p * 1000)))
        self._finished.connect(self._on_finished)

    def _on_later(self) -> None:
        self._cancel.set()
        self.reject()

    def _start(self) -> None:
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
        if err:
            self.msg.setText(f"실패: {err}")
            self.go.setEnabled(True)
            return
        from pathlib import Path

        try:
            updater.launch_installer(Path(path))
        except Exception as e:  # noqa: BLE001
            self.msg.setText(f"설치 파일을 실행하지 못했습니다: {e}\n파일: {path}")
            self.go.setEnabled(True)
            return
        self.msg.setText("설치를 시작합니다. 잠시 후 자동으로 다시 열립니다.")
        QTimer.singleShot(400, QApplication.quit)


def check_and_prompt(settings, parent=None) -> None:
    """[업데이트…] 메뉴: 버전을 확인하고, 새 버전이 있으면 설치할지만 묻는다. 입력할 값은 없다."""
    box = QMessageBox(parent)
    box.setWindowTitle("업데이트")
    box.setText("새 버전을 확인하는 중…")
    box.setStandardButtons(QMessageBox.StandardButton.NoButton)
    box.setModal(True)
    worker = CheckWorker(*current_args(settings))
    result: dict = {}

    def done(info, err):
        result["v"] = (info, err)
        box.accept()

    worker.done.connect(done)
    worker.start()
    box.exec()
    info, err = result.get("v", (None, "확인하지 못했습니다."))
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
        self._worker = CheckWorker(*current_args(self.settings))
        self._worker.done.connect(self._done)
        self._worker.start()

    def _done(self, info, err: str) -> None:
        if not err:
            self.settings.set("update_last_check", time.time())
        if info is not None:
            UpdateDialog(info, updater.default_token(secret_store().get(TOKEN_KEY))).exec()

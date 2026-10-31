"""백그라운드 자동저장. UI 스레드는 직렬화된 payload 만 넘기고, 디스크 쓰기는 작업 스레드가 수행."""

from __future__ import annotations

import threading
import time
from typing import Callable

from .store import ProjectStore


class AutoSaver:
    SAVED, SAVING, ERROR, DIRTY = "saved", "saving", "error", "dirty"

    def __init__(
        self,
        store: ProjectStore,
        on_status: Callable[[str, str], None] | None = None,
        debounce: float = 0.5,
        max_wait: float = 4.0,
    ):
        self.store = store
        self.on_status = on_status or (lambda s, m: None)
        self.debounce = debounce
        self.max_wait = max_wait
        self._cv = threading.Condition()
        self._pending: dict | None = None
        self._first_dirty = 0.0
        self._last_req = 0.0
        self._stop = False
        self._saving = False
        self.last_error = ""
        self.save_count = 0
        self._thread = threading.Thread(target=self._run, name="ics-autosave", daemon=True)
        self._thread.start()

    def request(self, payload: dict) -> None:
        now = time.monotonic()
        with self._cv:
            if self._pending is None:
                self._first_dirty = now
            self._pending = payload
            self._last_req = now
            self._cv.notify_all()
        self.on_status(self.DIRTY, "")

    def _run(self):
        while True:
            with self._cv:
                while not self._stop:
                    if self._pending is not None:
                        now = time.monotonic()
                        due = min(self._last_req + self.debounce, self._first_dirty + self.max_wait)
                        if now >= due:
                            break
                        self._cv.wait(timeout=max(0.01, due - now))
                    else:
                        self._cv.wait()
                if self._stop and self._pending is None:
                    return
                payload, self._pending = self._pending, None
                self._saving = True
            self._write(payload)
            with self._cv:
                self._saving = False
                self._cv.notify_all()
                if self._stop and self._pending is None:
                    return

    def _write(self, payload: dict | None) -> bool:
        if payload is None:
            return True
        self.on_status(self.SAVING, "")
        try:
            self.store.save(payload)
            self.save_count += 1
            self.last_error = ""
            self.on_status(self.SAVED, "")
            return True
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            self.on_status(self.ERROR, f"저장 실패: {e}")
            # 실패한 payload 는 보존해 다음 요청/종료 시 재시도
            with self._cv:
                if self._pending is None:
                    self._pending = payload
                    self._first_dirty = time.monotonic()
                    self._last_req = time.monotonic() + 3.0  # 재시도 지연
            return False

    def flush(self, timeout: float = 10.0) -> bool:
        """대기 중인 저장을 즉시 동기 수행 (종료 시 호출)."""
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._saving and time.monotonic() < deadline:
                self._cv.wait(timeout=0.1)
            payload, self._pending = self._pending, None
        return self._write(payload)

    def close(self):
        ok = self.flush()
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        self._thread.join(timeout=3)
        return ok

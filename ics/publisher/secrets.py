"""비밀값(API 키, 계정 토큰) 보관. Windows 에서는 DPAPI(현재 사용자 계정으로만 복호화)로 암호화해 저장한다.
일반 설정 파일/DB/로그에는 절대 넣지 않는다. DPAPI 가 없는 환경에서는 메모리에만 두고 매번 입력받는다."""

from __future__ import annotations

import base64
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol


class SecretStore(Protocol):
    persistent: bool

    def get(self, name: str) -> str: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class MemorySecretStore:
    persistent = False

    def __init__(self):
        self._d: dict[str, str] = {}

    def get(self, name):
        return self._d.get(name, "")

    def set(self, name, value):
        self._d[name] = value

    def delete(self, name):
        self._d.pop(name, None)


class DpapiSecretStore:
    """파일에는 DPAPI 로 암호화된 값만 저장. protect/unprotect 는 테스트에서 바꿀 수 있다."""

    persistent = True

    def __init__(
        self,
        path: Path,
        protect: Callable[[bytes], bytes] | None = None,
        unprotect: Callable[[bytes], bytes] | None = None,
    ):
        self.path = Path(path)
        self._protect, self._unprotect = protect or _win_protect, unprotect or _win_unprotect

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, d: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d), "utf-8")
        os.replace(tmp, self.path)

    def get(self, name):
        blob = self._load().get(name)
        if not blob:
            return ""
        try:
            return self._unprotect(base64.b64decode(blob)).decode("utf-8")
        except Exception:  # 다른 사용자/PC 에서 복사된 파일 등: 값이 없는 것으로 취급
            return ""

    def set(self, name, value):
        d = self._load()
        d[name] = base64.b64encode(self._protect(value.encode("utf-8"))).decode("ascii")
        self._save(d)

    def delete(self, name):
        d = self._load()
        if d.pop(name, None) is not None:
            self._save(d)


def _blob(data: bytes):
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf, DATA_BLOB


def _win_call(fn_name: str, data: bytes) -> bytes:  # pragma: no cover - Windows 전용
    import ctypes

    blob_in, _keep, DATA_BLOB = _blob(data)
    blob_out = DATA_BLOB()
    fn = getattr(ctypes.windll.crypt32, fn_name)
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI 호출 실패")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _win_protect(data: bytes) -> bytes:  # pragma: no cover
    return _win_call("CryptProtectData", data)


def _win_unprotect(data: bytes) -> bytes:  # pragma: no cover
    return _win_call("CryptUnprotectData", data)


def default_secret_store(path: Path) -> SecretStore:
    return DpapiSecretStore(path) if sys.platform.startswith("win") else MemorySecretStore()

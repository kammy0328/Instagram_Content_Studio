"""FFmpeg/FFprobe 위치 탐색과 서브프로세스 실행 헬퍼 (Windows 콘솔 창 억제 포함)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Sequence


class FFmpegNotFound(RuntimeError):
    pass


def _candidates(name: str) -> list[Path]:
    exe = name + (".exe" if sys.platform.startswith("win") else "")
    out: list[Path] = []
    env = os.environ.get(f"ICS_{name.upper()}")
    if env:
        out.append(Path(env))
    meipass = getattr(sys, "_MEIPASS", None)
    roots = []
    if meipass:
        roots.append(Path(meipass))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).parent)
    roots.append(Path(__file__).resolve().parents[2])  # 프로젝트 루트
    for r in roots:
        out.append(r / "vendor" / "ffmpeg" / exe)
        out.append(r / "ffmpeg" / exe)
        out.append(r / exe)
    return out


@lru_cache(maxsize=None)
def find_binary(name: str) -> str:
    for c in _candidates(name):
        if c.is_file():
            return str(c)
    found = shutil.which(name)
    if found:
        return found
    raise FFmpegNotFound(
        f"{name} 를 찾을 수 없습니다. vendor/ffmpeg 폴더에 {name} 실행 파일을 넣거나 "
        f"환경변수 ICS_{name.upper()} 로 경로를 지정하세요."
    )


def ffmpeg() -> str:
    return find_binary("ffmpeg")


def ffprobe() -> str:
    return find_binary("ffprobe")


def _popen_kwargs() -> dict:
    kw: dict = {}
    if sys.platform.startswith("win"):
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return kw


def run(
    cmd: Sequence[str], timeout: float | None = None, check: bool = False, input: bytes | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, timeout=timeout, check=check, input=input, **_popen_kwargs())


def popen(cmd: Sequence[str], **kw) -> subprocess.Popen:
    kw.update(_popen_kwargs())
    return subprocess.Popen(list(cmd), **kw)


def probe_json(path: str, *args: str) -> dict:
    cmd = [ffprobe(), "-v", "error", "-of", "json", *args, str(path)]
    p = run(cmd)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip() or "ffprobe 실패")
    return json.loads(p.stdout.decode("utf-8", "replace") or "{}")


@lru_cache(maxsize=1)
def versions() -> dict:
    info = {}
    for n in ("ffmpeg", "ffprobe"):
        try:
            p = run([find_binary(n), "-version"], timeout=10)
            info[n] = p.stdout.decode("utf-8", "replace").splitlines()[0]
        except Exception as e:  # noqa: BLE001
            info[n] = f"unavailable: {e}"
    return info


@lru_cache(maxsize=None)
def has_filter(name: str) -> bool:
    try:
        p = run([ffmpeg(), "-hide_banner", "-filters"], timeout=15)
        return any(f" {name} " in ln for ln in p.stdout.decode("utf-8", "replace").splitlines())
    except Exception:  # noqa: BLE001
        return False


@lru_cache(maxsize=None)
def has_encoder(name: str) -> bool:
    try:
        p = run([ffmpeg(), "-hide_banner", "-encoders"], timeout=15)
        return any(f" {name} " in ln for ln in p.stdout.decode("utf-8", "replace").splitlines())
    except Exception:  # noqa: BLE001
        return False

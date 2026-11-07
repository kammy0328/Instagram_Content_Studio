"""앱 데이터/캐시 경로. 프로젝트 DB, 캐시(썸네일·프록시), 출력물은 서로 분리한다."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from . import APP_ID


def app_data_dir() -> Path:
    override = os.environ.get("ICS_DATA_DIR")
    if override:
        base = Path(override)
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_ID
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_ID
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_ID
    base.mkdir(parents=True, exist_ok=True)
    return base


def library_db_path() -> Path:
    return app_data_dir() / "library.sqlite"


def project_dir(project_id: str) -> Path:
    p = app_data_dir() / "projects" / project_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_db_path(project_id: str) -> Path:
    return project_dir(project_id) / "project.sqlite"


def cache_dir(project_id: str) -> Path:
    """썸네일/프록시/프레임 인덱스 캐시. 출력 폴더와 완전히 분리."""
    override = os.environ.get("ICS_CACHE_DIR")
    base = Path(override) if override else app_data_dir() / "cache"
    p = base / project_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = app_data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def id_dictionary_path() -> Path:
    return app_data_dir() / "id_dictionary.sqlite"


_WIN_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(name: str, replacement: str = "_", max_len: int = 120) -> str:
    """Windows 금지 문자·예약어·끝의 점/공백을 안전하게 치환. 공백은 언더스코어(폴더 규칙)."""
    s = _WIN_FORBIDDEN.sub(replacement, name or "")
    s = s.replace("&", "_")
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"_+", "_", s)
    s = s.strip(" ._")
    if not s:
        s = "UNTITLED"
    if s.split(".")[0].upper() in _WIN_RESERVED:
        s = "_" + s
    return s[:max_len]


def resource_path(rel: str) -> Path:
    """번들(PyInstaller)된 데이터 파일 또는 소스 저장소의 같은 경로."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / rel

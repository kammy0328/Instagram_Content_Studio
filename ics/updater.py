"""GitHub Releases 기반 자동 업데이트.

- CI 가 빌드마다 릴리스(build-<채널>)에 설치 파일과 update.json(빌드 ID, sha256)을 올린다.
- 앱은 실행할 때(하루 여러 번 묻지 않도록 간격 제한) update.json 을 받아 현재 빌드 ID 와 비교한다.
- 새 빌드가 있으면 사용자가 [지금 업데이트]를 눌렀을 때만 내려받고 sha256 검증 후 설치 파일을 실행한다 (임의 교체 금지).
- 비공개 저장소는 읽기 전용 토큰(Contents: Read)이 필요하다. 토큰은 DPAPI 보관소에만 저장한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import BUILD_CHANNEL, BUILD_ID, BUILD_TOKEN, __version__

DEFAULT_REPO = "kammy0328/Instagram_Content_Studio"
DEFAULT_CHANNEL = "main"
CHECK_INTERVAL_S = 6 * 3600
USER_AGENT = "InstagramContentStudio-Updater"
API = "https://api.github.com"


class UpdateError(Exception):
    pass


@dataclass
class UpdateInfo:
    build_id: str
    version: str
    notes: str
    asset_url: str
    asset_name: str
    size: int
    sha256: str


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub 자산 내려받기는 다른 호스트(S3 등)로 리다이렉트된다. 그쪽에는 토큰을 보내지 않는다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


def _open(url: str, token: str, accept: str, timeout: float = 30.0):
    if not url.startswith("https://"):
        raise UpdateError("업데이트 주소는 https 여야 합니다.")
    headers = {"User-Agent": USER_AGENT, "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    opener = urllib.request.build_opener(_DropAuthOnRedirect)
    try:
        return opener.open(urllib.request.Request(url, headers=headers), timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404) and not token:
            raise UpdateError(
                "업데이트 서버에 접근할 수 없습니다 (저장소가 비공개이고 이 빌드에 읽기 권한이 없습니다)."
            ) from None
        if e.code in (401, 403):
            raise UpdateError("토큰이 올바르지 않거나 권한이 없습니다 (Contents: Read 필요).") from None
        if e.code == 404:
            raise UpdateError("해당 채널의 릴리스를 찾을 수 없습니다.") from None
        raise UpdateError(f"업데이트 서버 오류 (HTTP {e.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"업데이트 서버에 연결하지 못했습니다: {type(e).__name__}") from None


def _json(url: str, token: str, opener: Callable = _open) -> dict:
    with opener(url, token, "application/vnd.github+json") as r:
        return json.loads(r.read().decode("utf-8"))


def check(
    repo: str = DEFAULT_REPO,
    channel: str = DEFAULT_CHANNEL,
    token: str = "",
    current_build: str = BUILD_ID,
    opener: Callable = _open,
) -> UpdateInfo | None:
    """새 빌드가 있으면 UpdateInfo, 같으면 None. 개발 실행(current_build == 'dev')은 항상 None."""
    if current_build == "dev":
        return None
    rel = _json(f"{API}/repos/{repo}/releases/tags/build-{channel}", token, opener)
    assets = {a["name"]: a for a in rel.get("assets", [])}
    meta_asset = assets.get("update.json")
    if not meta_asset:
        raise UpdateError("릴리스에 update.json 이 없습니다.")
    with opener(meta_asset["url"], token, "application/octet-stream") as r:
        meta = json.loads(r.read().decode("utf-8"))
    name = meta.get("installer", "")
    inst = assets.get(name)
    if not inst or not meta.get("build_id") or not meta.get("sha256"):
        raise UpdateError("update.json 내용이 올바르지 않습니다.")
    if meta["build_id"] == current_build:
        return None
    return UpdateInfo(
        meta["build_id"], str(meta.get("version", "")), str(meta.get("notes", "")),
        inst["url"], name, int(inst.get("size", 0)), meta["sha256"].lower(),
    )  # fmt: skip


def download(
    info: UpdateInfo,
    token: str,
    dest_dir: Path,
    progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    opener: Callable = _open,
) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / info.asset_name
    part = out.with_name(out.name + ".part")
    h = hashlib.sha256()
    done = 0
    try:
        with opener(info.asset_url, token, "application/octet-stream", 60.0) as r, part.open("wb") as f:
            while chunk := r.read(1 << 20):
                if cancelled and cancelled():
                    raise UpdateError("취소됨")
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress and info.size:
                    progress(min(1.0, done / info.size))
        if h.hexdigest() != info.sha256:
            raise UpdateError("내려받은 파일의 검증(sha256)이 맞지 않아 설치하지 않습니다.")
        os.replace(part, out)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return out


def installed_per_machine() -> bool:
    """Program Files 아래에 설치돼 있으면 관리자 권한이 필요하다 (조용히 설치하지 않고 설치 마법사를 연다)."""
    exe = Path(sys.executable).resolve()
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramW6432")]
    return any(r and str(exe).lower().startswith(str(Path(r)).lower()) for r in roots)


def installer_command(setup: Path, silent: bool) -> list[str]:
    cmd = [str(setup)]
    if silent:
        cmd += ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/LAUNCH=1"]
    return cmd


def launch_installer(setup: Path) -> None:
    """설치 파일을 분리 실행한다. 호출한 쪽은 곧바로 앱을 종료해야 한다 (설치가 실행 파일을 교체함)."""
    if not sys.platform.startswith("win"):
        raise UpdateError("자동 설치는 Windows 에서만 지원합니다.")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        installer_command(setup, not installed_per_machine()), close_fds=True, creationflags=flags
    )  # noqa: S603


def due(last_check: float, now: float | None = None) -> bool:
    return (now if now is not None else time.time()) - last_check >= CHECK_INTERVAL_S


def current_channel(configured: str = "") -> str:
    return configured or (BUILD_CHANNEL if BUILD_CHANNEL not in ("", "dev") else DEFAULT_CHANNEL)


def default_token(stored: str = "") -> str:
    """사용자가 입력할 필요 없다: 빌드에 포함된 읽기 전용 토큰(있으면)을 쓰고, 예전에 저장해 둔 토큰은 그다음."""
    return BUILD_TOKEN or stored


def current_label() -> str:
    return f"v{__version__} ({BUILD_ID})"

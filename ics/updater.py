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
    _cleanup_old(dest_dir)
    # 받을 때마다 다른 이름: 예전 업데이트의 설치 파일이 아직 실행 중이거나(파일 잠금) 백신이 검사 중이어도 겹치지 않는다
    stem, suffix = Path(info.asset_name).stem, Path(info.asset_name).suffix
    out = dest_dir / f"{stem}_{info.build_id[:7]}_{os.getpid()}_{int(time.time())}{suffix}"
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
        _replace_retry(part, out)
    except BaseException:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return out


def _replace_retry(src: Path, dst: Path, tries: int = 40, wait: float = 0.25) -> None:
    """막 받은 실행 파일은 백신이 잠깐 잠글 수 있다 (WinError 32): 잠시 기다렸다 다시 시도한다."""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise UpdateError(
                    "내려받은 설치 파일을 다른 프로그램이 사용 중입니다 (백신 검사 또는 이전 설치가 아직 실행 중). "
                    "잠시 후 다시 시도하거나, 작업 관리자에서 'Setup' 이 남아 있으면 끝낸 뒤 다시 시도하세요."
                ) from None
            time.sleep(wait)


def _cleanup_old(dest_dir: Path, keep_hours: float = 1.0) -> None:
    """예전에 받은 설치 파일 정리 (사용 중이면 그냥 둔다). 방금 받은 것은 남긴다."""
    now = time.time()
    for f in dest_dir.glob("*"):
        try:
            if f.is_file() and now - f.stat().st_mtime > keep_hours * 3600:
                f.unlink()
        except OSError:
            pass


def installed_per_machine() -> bool:
    """Program Files 아래에 설치돼 있으면 관리자 권한이 필요하다 (조용히 설치하지 않고 설치 마법사를 연다)."""
    exe = Path(sys.executable).resolve()
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramW6432")]
    return any(r and str(exe).lower().startswith(str(Path(r)).lower()) for r in roots)


def update_dir() -> Path:
    import tempfile

    return Path(tempfile.gettempdir()) / "ics_update"


def install_log_path() -> Path:
    """설치 기록 (Inno Setup /LOG). 업데이트 후 앱이 다시 안 열릴 때 원인 확인용."""
    return update_dir() / "install.log"


def launcher_log_path() -> Path:
    """업데이트 실행기 기록: 앱 종료 대기 → 설치 → 종료 코드 → 다시 실행. 다음 실행 때 결과를 알려 주는 데 쓴다."""
    return update_dir() / "launcher.log"


APP_PROCESS = "InstagramContentStudio"


def installer_command(setup: Path, silent: bool) -> list[str]:
    """설치 명령. 다시 실행은 설치 프로그램이 아니라 실행기(launcher)가 맡는다 (설치가 실패해도 앱이 다시 열리게)."""
    cmd = [str(setup)]
    if silent:
        cmd += ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]
    cmd.append(f"/LOG={install_log_path()}")
    return cmd


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def launcher_script(setup: Path, silent: bool, wait_pid: int, app_exe: Path, build_id: str = "") -> str:
    """앱이 끝난 뒤 설치하고, 결과와 상관없이 앱을 다시 여는 PowerShell 스크립트.

    1) 앱 프로세스가 완전히 끝날 때까지 기다린다 (실행 파일 잠금 해제)
    2) 설치 파일을 실행하고 그 프로세스만 기다린다 (Start-Process -Wait 는 자식까지 기다려 멈출 수 있어 쓰지 않음)
    3) 앱이 떠 있지 않으면 다시 연다. 설치가 실패해도 예전 버전이라도 다시 열린다.
    모든 단계를 launcher.log 에 남긴다."""
    cmd = installer_command(setup, silent)
    args = ",".join(_ps_quote(a) for a in cmd[1:]) or "@()"
    log = _ps_quote(launcher_log_path())
    return "\n".join(
        [
            "$ErrorActionPreference = 'Continue'",
            f"$log = {log}",
            "function L($m) { try { Add-Content -LiteralPath $log -Value ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' ' + $m) -Encoding UTF8 } catch {} }",
            f"L 'start build={build_id} pid={int(wait_pid)}'",
            f"try {{ Wait-Process -Id {int(wait_pid)} -Timeout 90 -ErrorAction Stop }} catch {{ L ('wait: ' + $_) }}",
            "Start-Sleep -Milliseconds 800",
            "$code = -1",
            "try {",
            f"  $p = Start-Process -FilePath {_ps_quote(cmd[0])} -ArgumentList {args} -PassThru -ErrorAction Stop",
            "  $p.WaitForExit()",
            "  $code = $p.ExitCode",
            "  L ('setup exit=' + $code)",
            "} catch { L ('setup failed: ' + $_) }",
            "Start-Sleep -Seconds 2",
            f"if (-not (Get-Process -Name {_ps_quote(APP_PROCESS)} -ErrorAction SilentlyContinue)) {{",
            f"  try {{ Start-Process -FilePath {_ps_quote(app_exe)} -ErrorAction Stop; L 'relaunch ok' }}"
            " catch { L ('relaunch failed: ' + $_) }",
            "} else { L 'app already running' }",
            "L 'done'",
        ]
    )


def launcher_command(
    setup: Path, silent: bool, wait_pid: int, app_exe: Path | None = None, build_id: str = ""
) -> list[str]:
    """실행기 스크립트를 파일로 써 두고 PowerShell 로 실행하는 명령 (창 없음)."""
    app_exe = Path(app_exe) if app_exe else Path(sys.executable)
    script = launcher_script(setup, silent, wait_pid, app_exe, build_id)
    f = update_dir() / "run_update.ps1"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(script, encoding="utf-8-sig")  # Windows PowerShell 5.1 은 BOM 이 있어야 UTF-8 로 읽는다
        tail = ["-File", str(f)]
    except OSError:
        tail = ["-Command", script.replace("\n", "; ")]
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
            *tail]  # fmt: skip


CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _spawn_detached(cmd: list[str]) -> int:
    """앱이 끝나도 같이 죽지 않게 따로 실행하고 pid 를 돌려준다.

    DETACHED_PROCESS 는 쓰지 않는다: 콘솔이 아예 없으면 powershell.exe 가 시작하자마자 아무것도 못 하고 끝난다
    (업데이트 후 설치도, 다시 열기도 안 되던 원인). CREATE_NO_WINDOW 는 보이지 않는 콘솔을 만들어 준다.
    앱이 작업 개체(Job) 안에서 실행 중이면 거기서 빠져나오게 한다 (허용되지 않으면 그냥 실행)."""
    base = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    kw = dict(close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        return subprocess.Popen(cmd, creationflags=base | CREATE_BREAKAWAY_FROM_JOB, **kw).pid  # noqa: S603
    except OSError:
        return subprocess.Popen(cmd, creationflags=base, **kw).pid  # noqa: S603


def launch_installer(setup: Path, wait_pid: int | None = None) -> int:
    """설치 실행기를 분리 실행한다. 설치는 이 프로세스가 끝난 뒤에 시작되므로, 호출한 쪽은 곧바로 앱을 종료해야 한다."""
    if not sys.platform.startswith("win"):
        raise UpdateError("자동 설치는 Windows 에서만 지원합니다.")
    update_dir().mkdir(parents=True, exist_ok=True)
    silent = not installed_per_machine()
    try:
        launcher_log_path().unlink(missing_ok=True)  # 이번 업데이트 기록만 남긴다
    except OSError:
        pass
    try:
        return _spawn_detached(launcher_command(setup, silent, wait_pid or os.getpid(), Path(sys.executable), BUILD_ID))
    except OSError:  # PowerShell 을 못 쓰는 환경: 예전처럼 바로 실행 (앱은 곧바로 종료한다)
        return _spawn_detached(installer_command(setup, silent) + ["/LAUNCH=1"])


def last_update_result(max_age_s: float = 6 * 3600) -> tuple[str, str] | None:
    """지난 업데이트 결과 (상태, 설명). 상태: 'ok' / 'failed'. 기록이 없거나 오래됐으면 None.

    한 번 알려 준 기록은 이름을 바꿔 다시 알리지 않는다."""
    f = launcher_log_path()
    try:
        if not f.is_file() or time.time() - f.stat().st_mtime > max_age_s:
            return None
        text = f.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    if "setup exit=" not in text and "setup failed" not in text:
        return None  # 아직 진행 중이거나 시작하지 못함
    ok = "setup exit=0" in text
    detail = text.strip()
    if not ok:
        try:
            lines = install_log_path().read_text(encoding="utf-8", errors="replace").splitlines()
            detail += "\n\n[설치 기록 마지막 줄]\n" + "\n".join(lines[-12:])
        except OSError:
            pass
    try:
        f.replace(f.with_suffix(".seen.log"))
    except OSError:
        pass
    return ("ok" if ok else "failed"), detail


def due(last_check: float, now: float | None = None) -> bool:
    return (now if now is not None else time.time()) - last_check >= CHECK_INTERVAL_S


def current_channel(configured: str = "") -> str:
    return configured or (BUILD_CHANNEL if BUILD_CHANNEL not in ("", "dev") else DEFAULT_CHANNEL)


def default_token(stored: str = "") -> str:
    """사용자가 입력할 필요 없다: 빌드에 포함된 읽기 전용 토큰(있으면)을 쓰고, 예전에 저장해 둔 토큰은 그다음."""
    return BUILD_TOKEN or stored


def current_label() -> str:
    return f"v{__version__} ({BUILD_ID})"

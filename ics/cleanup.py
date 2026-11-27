"""프로그램을 닫을 때 쓸모없어진 파일 정리 (용량을 괜히 차지하지 않게).

지우는 것
- 중간에 끊긴 임시 파일 (*.part, *.tmp): 프록시/프레임 인덱스/검출 결과를 쓰다 멈춘 것
- 삭제된 프로젝트의 캐시 폴더
- 마스터링 영상이 바뀌었거나(교체, 재출력) 다시 만들어 더는 쓰지 않는 예전 프록시/프레임 인덱스/썸네일
- 오래 안 연 프로젝트의 재생용 프록시 (프로젝트마다 수백 MB). 최근에 연 KEEP_PROXY_PROJECTS 개만 남긴다.
  지운 프로젝트를 다시 열면 뒤에서 다시 만든다 (그동안은 원본으로 재생)
- 렌더링용 임시 오버레이 그림, 화면용 임시 그림
- 업데이트 설치 파일 (설치가 끝난 것)

지우지 않는 것: 프로젝트 저장 파일, 설정, 로그, 인물 검출 결과, 사용자의 원본/출력 폴더.
원본이 NAS 에 있고 연결이 끊겨 있으면, 그 프로젝트는 '예전 것' 판단을 하지 않는다 (잘못 지우지 않게)."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

KEEP_PROXY_PROJECTS = 2  # 재생용 프록시를 남겨 둘 최근 프로젝트 수 (지금 연 프로젝트 포함)
STAT_TIMEOUT = 2.0  # NAS 원본 확인 제한 시간 (초). 넘으면 그 프로젝트는 판단하지 않는다
KEYED_DIRS = ("thumbs", "cut_thumbs", "cutline_thumbs")  # 원본 파일별(키) 하위 폴더가 있는 캐시


@dataclass
class Report:
    removed: list[str] = field(default_factory=list)
    freed: int = 0

    def add(self, p: Path) -> None:
        try:
            if p.is_dir():
                size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                shutil.rmtree(p, ignore_errors=True)
            else:
                size = p.stat().st_size
                p.unlink()
        except OSError:
            return
        self.removed.append(str(p))
        self.freed += size


def file_key(path: str, size: int, mtime: float) -> str:
    """frameindex._cache_key 와 같은 키 (파일 경로/크기/수정 시각)."""
    return hashlib.sha1(f"{path}|{size}|{mtime}".encode()).hexdigest()[:16]


def _stat_key(path: str) -> str | None:
    """원본의 지금 키. 파일이 없거나 확인이 늦으면(NAS 끊김) None."""

    def go():
        st = os.stat(path)
        return file_key(str(path), st.st_size, st.st_mtime)

    ex = ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(go).result(timeout=STAT_TIMEOUT)
    except (OSError, FutureTimeout):
        return None
    finally:
        ex.shutdown(wait=False)


def _key_of(name: str) -> str | None:
    """'proxy_<키>_p2.mkv', 'proxy_<키>.wav', 'frameindex_<키>.json' 의 키."""
    for pre in ("proxy_", "frameindex_"):
        if name.startswith(pre):
            return name[len(pre) :].split(".")[0].split("_")[0]
    return None


def clean_cache(projects: dict[str, str], recent: list[str], keep_proxy: int = KEEP_PROXY_PROJECTS) -> Report:
    """projects: {프로젝트 id: 마스터링 경로}, recent: 최근에 연 순서의 프로젝트 id (앞이 최근)."""
    rep = Report()
    root = paths.cache_root()
    keep_proxy_ids = set(recent[: max(0, keep_proxy)])
    for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        pid = d.name
        if pid not in projects:
            rep.add(d)  # 지운 프로젝트의 캐시
            continue
        for f in list(d.rglob("*")):  # 끊긴 임시 파일
            if f.is_file() and f.suffix in (".part", ".tmp"):
                rep.add(f)
        key = _stat_key(projects[pid])
        if key is not None:
            live = {key}
            # 지금 프록시의 프레임 인덱스 (프록시 파일 자체의 키)
            for px in d.glob(f"proxy_{key}_*.mkv"):
                try:
                    st = px.stat()
                    live.add(file_key(str(px), st.st_size, st.st_mtime))
                except OSError:
                    pass
            for f in d.iterdir():
                k = _key_of(f.name)
                if f.is_file() and k is not None and k not in live:
                    rep.add(f)  # 예전 원본(교체/재출력)의 프록시·인덱스
            for sub in KEYED_DIRS:
                for kd in (d / sub).glob("*") if (d / sub).is_dir() else []:
                    if kd.is_dir() and kd.name not in live:
                        rep.add(kd)
        if pid not in keep_proxy_ids:  # 오래 안 연 프로젝트의 재생용 프록시 (다시 열면 다시 만든다)
            for f in list(d.glob("proxy_*")):
                if f.is_file():
                    rep.add(f)
    return rep


def clean_temp() -> Report:
    """렌더링/화면용 임시 그림, 끝난 업데이트 설치 파일."""
    rep = Report()
    tmp = Path(tempfile.gettempdir())
    for name in ("ics_overlay", "ics_ui"):
        if (tmp / name).is_dir():
            rep.add(tmp / name)
    upd = tmp / "ics_update"
    now = time.time()
    if upd.is_dir():
        for f in upd.iterdir():
            try:
                old = now - f.stat().st_mtime > 600  # 막 받은(설치 중일 수 있는) 파일은 남긴다
            except OSError:
                continue
            if f.is_file() and (f.suffix.lower() in (".exe", ".part", ".ps1") and old):
                rep.add(f)
    return rep


def clean_all(library, current: str | None = None) -> Report:
    """프로그램을 닫을 때. current: 방금까지 연 프로젝트 id (최근 목록 맨 앞)."""
    recs = library.list()
    projects = {r.id: r.master_path for r in recs}
    recent = [r.id for r in sorted(recs, key=lambda r: -(r.updated or 0))]
    if current in projects:
        recent.remove(current)
        recent.insert(0, current)
    rep = clean_cache(projects, recent)
    t = clean_temp()
    rep.removed += t.removed
    rep.freed += t.freed
    return rep

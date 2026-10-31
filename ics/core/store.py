"""프로젝트 저장소: SQLite 트랜잭션, 체크섬 스냅샷, 비정상 종료 복구, 프로젝트 라이브러리."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .. import paths

SCHEMA_VERSION = 1
KEEP_SNAPSHOTS = 30


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


class ProjectStore:
    """프로젝트 1개당 SQLite 파일 1개. 저장은 항상 '새 스냅샷 INSERT' 하나의 트랜잭션이라 원자적이다.
    마지막으로 체크섬이 맞는 스냅샷을 복구 대상으로 삼는다."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._session_lock = self.path.with_name(self.path.name + ".lock").open("a+b")
        try:
            if self._session_lock.seek(0, os.SEEK_END) == 0:
                self._session_lock.write(b"\0")
                self._session_lock.flush()
            self._session_lock.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self._session_lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._session_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as err:
                raise RuntimeError("이 프로젝트는 다른 실행에서 사용 중입니다. 먼저 해당 창을 닫아 주세요.") from err
            self._conn = self._connect()
            self._init()
        except BaseException:
            if hasattr(self, "_conn"):
                self._conn.close()
            self._session_lock.close()
            raise

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
        except BaseException:
            conn.close()
            raise
        return conn

    def _init(self):
        with self._lock:
            c = self._conn
            c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
            c.execute("""CREATE TABLE IF NOT EXISTS snapshots(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, checksum TEXT NOT NULL, data TEXT NOT NULL)""")
            c.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))

    # ------------------------------------------------------------ 세션 상태
    def mark_open(self) -> bool:
        """세션 시작. 직전 세션이 정상 종료되지 않았으면 True(복구 필요) 반환."""
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key='session_open'").fetchone()
            crashed = bool(row and row[0] == "1")
            self._conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('session_open','1')")
            return crashed

    def mark_closed(self) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('session_open','0')")

    # ------------------------------------------------------------ 저장/복구
    def save(self, payload: dict) -> int:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        chk = _checksum(text)
        with self._lock:
            c = self._conn
            c.execute("BEGIN IMMEDIATE")
            try:
                cur = c.execute("INSERT INTO snapshots(ts,checksum,data) VALUES(?,?,?)", (time.time(), chk, text))
                sid = cur.lastrowid
                c.execute(
                    "DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM snapshots ORDER BY id DESC LIMIT ?)",
                    (KEEP_SNAPSHOTS,),
                )
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            return sid

    def load_latest(self) -> tuple[dict | None, int]:
        """체크섬이 유효한 가장 최근 스냅샷. (payload, 건너뛴 손상 스냅샷 수)"""
        skipped = 0
        with self._lock:
            rows = self._conn.execute("SELECT id, checksum, data FROM snapshots ORDER BY id DESC").fetchall()
        for _sid, chk, data in rows:
            if _checksum(data) == chk:
                try:
                    return json.loads(data), skipped
                except json.JSONDecodeError:
                    pass
            skipped += 1
        return None, skipped

    def snapshot_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]

    def close(self):
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._session_lock.close()  # 종료/강제 종료 시 OS가 잠금을 해제한다. 파일은 삭제하지 않는다.


# ============================================================================ 라이브러리
@dataclass
class ProjectRecord:
    id: str
    name: str
    artist: str
    title: str
    master_path: str
    output_dir: str
    created: float
    updated: float


class Library:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.library_db_path()
        self._conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self._conn.execute("""CREATE TABLE IF NOT EXISTS projects(
            id TEXT PRIMARY KEY, name TEXT, artist TEXT, title TEXT, master_path TEXT, output_dir TEXT,
            created REAL, updated REAL)""")

    def list(self) -> list[ProjectRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id,name,artist,title,master_path,output_dir,created,updated FROM projects "
                "ORDER BY updated DESC"
            ).fetchall()
        return [ProjectRecord(*r) for r in rows]

    def get(self, pid: str) -> ProjectRecord | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT id,name,artist,title,master_path,output_dir,created,updated FROM projects WHERE id=?", (pid,)
            ).fetchone()
        return ProjectRecord(*r) if r else None

    def upsert(self, rec: ProjectRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO projects(id,name,artist,title,master_path,output_dir,created,updated) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (rec.id, rec.name, rec.artist, rec.title, rec.master_path, rec.output_dir, rec.created, rec.updated),
            )

    def create(self, name: str, artist: str, title: str, master_path: str, output_dir: str) -> ProjectRecord:
        now = time.time()
        rec = ProjectRecord(uuid.uuid4().hex[:12], name, artist, title, master_path, output_dir, now, now)
        self.upsert(rec)
        return rec

    def delete(self, pid: str, remove_files: bool = True) -> None:
        """프로젝트 DB/캐시를 삭제한다. 사용자의 마스터링 원본과 출력 폴더는 절대 건드리지 않는다."""
        import shutil

        with self._lock:
            self._conn.execute("DELETE FROM projects WHERE id=?", (pid,))
        if remove_files:
            for d in (paths.app_data_dir() / "projects" / pid, paths.cache_dir(pid)):
                shutil.rmtree(d, ignore_errors=True)

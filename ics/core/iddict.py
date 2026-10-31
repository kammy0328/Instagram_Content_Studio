"""인물/제작사 인스타그램 ID 로컬 사전 (SQLite). 동명이인은 자동 확정하지 않는다."""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .. import paths


def name_key(name: str) -> str:
    return re.sub(r"[\s·ㆍ.\-_]+", "", name or "").casefold()


@dataclass
class DictEntry:
    id: int
    name: str
    handle: str
    kind: str = "person"
    note: str = ""
    uses: int = 0


class IdDictionary:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.id_dictionary_path()
        self._lock = threading.Lock()
        self._c = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=10)
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.execute("""CREATE TABLE IF NOT EXISTS entries(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, key TEXT NOT NULL, handle TEXT NOT NULL,
            kind TEXT DEFAULT 'person', note TEXT DEFAULT '', uses INTEGER DEFAULT 0, updated REAL,
            UNIQUE(key, handle))""")

    @staticmethod
    def _row(r) -> DictEntry:
        return DictEntry(r[0], r[1], r[2], r[3], r[4], r[5])

    def add(self, name: str, handle: str, kind: str = "person", note: str = "") -> DictEntry:
        handle = handle.lstrip("@").strip()
        with self._lock:
            self._c.execute(
                "INSERT INTO entries(name,key,handle,kind,note,updated) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(key,handle) DO UPDATE SET name=excluded.name, kind=excluded.kind, "
                "note=CASE WHEN excluded.note!='' THEN excluded.note ELSE entries.note END, updated=excluded.updated",
                (name.strip(), name_key(name), handle, kind, note, time.time()),
            )
            r = self._c.execute(
                "SELECT id,name,handle,kind,note,uses FROM entries WHERE key=? AND handle=?", (name_key(name), handle)
            ).fetchone()
        return self._row(r)

    def update(self, entry_id: int, name: str, handle: str, kind: str, note: str) -> None:
        with self._lock:
            self._c.execute(
                "UPDATE entries SET name=?, key=?, handle=?, kind=?, note=?, updated=? WHERE id=?",
                (name.strip(), name_key(name), handle.lstrip("@").strip(), kind, note, time.time(), entry_id),
            )

    def delete(self, entry_id: int) -> None:
        with self._lock:
            self._c.execute("DELETE FROM entries WHERE id=?", (entry_id,))

    def lookup(self, name: str) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._c.execute("SELECT name,handle FROM entries WHERE key=?", (name_key(name),)).fetchall()
        return [(r[0], r[1]) for r in rows]

    def search(self, q: str = "", limit: int = 500) -> list[DictEntry]:
        q = (q or "").strip()
        with self._lock:
            if not q:
                rows = self._c.execute(
                    "SELECT id,name,handle,kind,note,uses FROM entries ORDER BY name LIMIT ?", (limit,)
                ).fetchall()
            else:
                like = f"%{q.lstrip('@')}%"
                rows = self._c.execute(
                    "SELECT id,name,handle,kind,note,uses FROM entries WHERE name LIKE ? OR handle LIKE ? OR key LIKE ? "
                    "ORDER BY name LIMIT ?",
                    (like, like, f"%{name_key(q)}%", limit),
                ).fetchall()
        return [self._row(r) for r in rows]

    def bump(self, name: str, handle: str) -> None:
        with self._lock:
            self._c.execute("UPDATE entries SET uses=uses+1 WHERE key=? AND handle=?", (name_key(name), handle))

    def count(self) -> int:
        with self._lock:
            return self._c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    def close(self):
        with self._lock:
            self._c.close()

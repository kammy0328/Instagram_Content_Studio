"""Publisher 로컬 DB (프로젝트 DB 와 별개). 중요한 상태 전이는 트랜잭션으로 저장하고, 멱등 키는 DB 제약으로 막는다."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .models import TERMINAL, State, check_transition

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER, kind TEXT NOT NULL, state TEXT NOT NULL,
  account_id TEXT NOT NULL, username TEXT, project_id TEXT, spec_json TEXT NOT NULL,
  approval_hash TEXT, approved_at REAL, idempotency_key TEXT NOT NULL,
  schedule_at REAL, container_id TEXT, media_id TEXT, permalink TEXT,
  error TEXT, next_try_at REAL, tries INTEGER DEFAULT 0, created REAL, updated REAL,
  simulation INTEGER DEFAULT 1, repost_of INTEGER, intent TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_job_active_key ON jobs(idempotency_key)
  WHERE repost_of IS NULL;
CREATE TABLE IF NOT EXISTS batches(
  id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT, selection TEXT, policy TEXT, created REAL);
CREATE TABLE IF NOT EXISTS attempts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, op TEXT, status TEXT, ts REAL, trace TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS comment_jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, body_hash TEXT, body TEXT, comment_id TEXT,
  state TEXT, tries INTEGER DEFAULT 0, error TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER, snapshot_hash TEXT, snapshot_json TEXT, approved_at REAL,
  invalidated_at REAL, invalid_reason TEXT);
"""


@dataclass
class JobRow:
    id: int
    batch_id: int | None
    kind: str
    state: State
    account_id: str
    username: str
    spec: dict
    approval_hash: str | None
    idempotency_key: str
    schedule_at: float | None
    container_id: str | None
    media_id: str | None
    error: str | None
    next_try_at: float | None
    tries: int
    simulation: bool
    intent: str | None


COLS = (
    "id,batch_id,kind,state,account_id,username,spec_json,approval_hash,idempotency_key,schedule_at,"
    "container_id,media_id,error,next_try_at,tries,simulation,intent"
)


def _row(r) -> JobRow:
    return JobRow(
        r[0],
        r[1],
        r[2],
        State(r[3]),
        r[4],
        r[5] or "",
        json.loads(r[6]),
        r[7],
        r[8],
        r[9],
        r[10],
        r[11],
        r[12],
        r[13],
        r[14] or 0,
        bool(r[15]),
        r[16],
    )


class DuplicateJob(Exception):
    def __init__(self, existing_id: int):
        super().__init__(f"같은 내용의 게시 작업이 이미 있습니다 (#{existing_id}).")
        self.existing_id = existing_id


class PublisherStore:
    def __init__(self, path: Path | str = ":memory:"):
        self._c = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
        self._lock = threading.RLock()
        self._c.execute("PRAGMA journal_mode=WAL")
        for stmt in SCHEMA.strip().split(";\n"):
            if stmt.strip():
                self._c.execute(stmt)

    # ------------------------------------------------------------ 배치/작업
    def create_batch(self, account_id: str, selection: list[str], policy: str) -> int:
        with self._lock:
            cur = self._c.execute(
                "INSERT INTO batches(account_id,selection,policy,created) VALUES(?,?,?,?)",
                (account_id, json.dumps(selection), policy, time.time()),
            )
            return cur.lastrowid

    def create_job(
        self,
        batch_id,
        kind,
        account_id,
        username,
        project_id,
        spec: dict,
        key: str,
        simulation: bool,
        repost_of: int | None = None,
    ) -> int:
        now = time.time()
        with self._lock:
            try:
                cur = self._c.execute(
                    "INSERT INTO jobs(batch_id,kind,state,account_id,username,project_id,spec_json,idempotency_key,"
                    "created,updated,simulation,repost_of) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        batch_id,
                        kind,
                        State.DRAFT.value,
                        account_id,
                        username,
                        project_id,
                        json.dumps(spec, ensure_ascii=False),
                        key,
                        now,
                        now,
                        int(simulation),
                        repost_of,
                    ),
                )
            except sqlite3.IntegrityError:
                r = self._c.execute(
                    "SELECT id FROM jobs WHERE idempotency_key=? AND repost_of IS NULL", (key,)
                ).fetchone()
                raise DuplicateJob(r[0]) from None
            return cur.lastrowid

    def get(self, job_id: int) -> JobRow:
        with self._lock:
            r = self._c.execute(f"SELECT {COLS} FROM jobs WHERE id=?", (job_id,)).fetchone()
        if r is None:
            raise KeyError(job_id)
        return _row(r)

    def jobs(self, batch_id: int | None = None, states: set[State] | None = None) -> list[JobRow]:
        q, a = f"SELECT {COLS} FROM jobs", []
        w = []
        if batch_id is not None:
            w.append("batch_id=?")
            a.append(batch_id)
        if states:
            w.append("state IN (%s)" % ",".join("?" * len(states)))
            a += [s.value for s in states]
        if w:
            q += " WHERE " + " AND ".join(w)
        with self._lock:
            return [_row(r) for r in self._c.execute(q + " ORDER BY id", a).fetchall()]

    def set_state(self, job_id: int, new: State, **fields) -> None:
        """상태 전이 + 부가 필드 갱신을 한 트랜잭션으로 저장."""
        with self._lock:
            cur = State(self._c.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
            if cur is not new:
                check_transition(cur, new)
            allowed = {
                "container_id",
                "media_id",
                "error",
                "next_try_at",
                "tries",
                "approval_hash",
                "approved_at",
                "schedule_at",
                "permalink",
                "intent",
                "spec_json",
            }
            sets, args = ["state=?", "updated=?"], [new.value, time.time()]
            for k, v in fields.items():
                if k not in allowed:
                    raise KeyError(k)
                sets.append(f"{k}=?")
                args.append(v)
            args.append(job_id)
            self._c.execute("BEGIN IMMEDIATE")
            try:
                self._c.execute(f"UPDATE jobs SET {','.join(sets)} WHERE id=?", args)
                self._c.execute("COMMIT")
            except BaseException:
                self._c.execute("ROLLBACK")
                raise

    # ------------------------------------------------------------ 이력 (비밀값 금지: 호출자가 마스킹해서 넘긴다)
    def attempt(self, job_id: int, op: str, status: str, trace: str = "", detail: str = "") -> None:
        with self._lock:
            self._c.execute(
                "INSERT INTO attempts(job_id,op,status,ts,trace,detail) VALUES(?,?,?,?,?,?)",
                (job_id, op, status, time.time(), trace, detail[:500]),
            )

    def attempts(self, job_id: int) -> list[tuple]:
        with self._lock:
            return self._c.execute(
                "SELECT op,status,ts,trace,detail FROM attempts WHERE job_id=? ORDER BY id", (job_id,)
            ).fetchall()

    # ------------------------------------------------------------ 승인
    def save_approval(self, job_id: int, snap_hash: str, snap: dict) -> None:
        with self._lock:
            self._c.execute(
                "UPDATE approvals SET invalidated_at=?, invalid_reason='재승인' WHERE job_id=? AND invalidated_at IS NULL",
                (time.time(), job_id),
            )
            self._c.execute(
                "INSERT INTO approvals(job_id,snapshot_hash,snapshot_json,approved_at) VALUES(?,?,?,?)",
                (job_id, snap_hash, json.dumps(snap, ensure_ascii=False, sort_keys=True), time.time()),
            )

    def active_approval(self, job_id: int) -> tuple[str, dict] | None:
        with self._lock:
            r = self._c.execute(
                "SELECT snapshot_hash,snapshot_json FROM approvals WHERE job_id=? AND invalidated_at IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (job_id,),
            ).fetchone()
        return (r[0], json.loads(r[1])) if r else None

    def invalidate_approval(self, job_id: int, reason: str) -> None:
        with self._lock:
            self._c.execute(
                "UPDATE approvals SET invalidated_at=?, invalid_reason=? WHERE job_id=? AND invalidated_at IS NULL",
                (time.time(), reason, job_id),
            )

    # ------------------------------------------------------------ 댓글 작업
    def add_comment_job(self, job_id: int, body_hash: str, body: str) -> int:
        with self._lock:
            return self._c.execute(
                "INSERT INTO comment_jobs(job_id,body_hash,body,state,updated) VALUES(?,?,?,?,?)",
                (job_id, body_hash, body, "PENDING", time.time()),
            ).lastrowid

    def comment_jobs(self, job_id: int) -> list[dict]:
        with self._lock:
            rows = self._c.execute(
                "SELECT id,body_hash,body,comment_id,state,tries,error FROM comment_jobs WHERE job_id=? ORDER BY id",
                (job_id,),
            ).fetchall()
        return [dict(zip(("id", "body_hash", "body", "comment_id", "state", "tries", "error"), r)) for r in rows]

    def update_comment_job(
        self, cid: int, state: str, comment_id: str | None = None, error: str | None = None, tries: int | None = None
    ) -> None:
        with self._lock:
            self._c.execute(
                "UPDATE comment_jobs SET state=?, comment_id=COALESCE(?,comment_id), error=?, "
                "tries=COALESCE(?,tries), updated=? WHERE id=?",
                (state, comment_id, error, tries, time.time(), cid),
            )

    # ------------------------------------------------------------ 이력 검색
    def search(
        self,
        text: str = "",
        kind: str | None = None,
        state: State | None = None,
        project_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> list[JobRow]:
        w, a = [], []
        if text:
            w.append("(spec_json LIKE ? OR username LIKE ?)")
            a += [f"%{text}%", f"%{text}%"]
        if kind:
            w.append("kind=?")
            a.append(kind)
        if state:
            w.append("state=?")
            a.append(state.value)
        if project_id:
            w.append("project_id=?")
            a.append(project_id)
        if since is not None:
            w.append("created>=?")
            a.append(since)
        if until is not None:
            w.append("created<=?")
            a.append(until)
        q = f"SELECT {COLS} FROM jobs" + (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY id DESC"
        with self._lock:
            return [_row(r) for r in self._c.execute(q, a).fetchall()]

    def close(self) -> None:
        with self._lock:
            self._c.close()


def active_states() -> set[State]:
    return {s for s in State if s not in TERMINAL}

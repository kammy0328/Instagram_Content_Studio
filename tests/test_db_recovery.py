"""DB 파일 전체 손상(헤더/페이지) 복구: 별도 백업, 손상본 보관, 백업까지 깨진 경우."""

from __future__ import annotations

import json

import pytest

from ics.core.store import ProjectStore


def _payload(n: int) -> dict:
    return {"state": {"playhead": n, "caption_body": f"본문{n}"}, "info": {"name": "p"}}


def _open(path):
    s = ProjectStore(path)
    s.mark_open()
    return s


def test_backup_is_written_on_every_save_and_previous_one_is_kept(tmp_path):
    s = _open(tmp_path / "p.db")
    s.save(_payload(1))
    s.save(_payload(2))
    assert json.loads(json.loads(s.backup_path.read_text("utf-8"))["data"])["state"]["playhead"] == 2
    assert json.loads(json.loads(s.backup_prev_path.read_text("utf-8"))["data"])["state"]["playhead"] == 1
    s.close()


@pytest.mark.parametrize("damage", ["garbage", "truncate", "header"])
def test_totally_corrupt_db_is_quarantined_and_restored_from_backup(tmp_path, damage):
    db = tmp_path / "p.db"
    s = _open(db)
    for i in range(1, 4):
        s.save(_payload(i))
    s.close()
    raw = db.read_bytes()
    if damage == "garbage":
        db.write_bytes(b"this is not sqlite" * 100)
    elif damage == "truncate":
        db.write_bytes(raw[:50])
    else:
        db.write_bytes(b"X" * 100 + raw[100:])
    for ext in ("-wal", "-shm"):
        (tmp_path / ("p.db" + ext)).unlink(missing_ok=True)
    s2 = ProjectStore(db)
    assert s2.db_recovered == "backup" and s2.quarantined is not None and s2.quarantined.exists()
    s2.mark_open()
    payload, _ = s2.load_latest()
    assert payload["state"]["playhead"] == 3  # 마지막 저장본까지 복구
    s2.save(_payload(4))  # 복구 뒤 계속 저장 가능
    assert s2.load_latest()[0]["state"]["playhead"] == 4
    s2.close()


def test_corrupt_db_and_corrupt_backup_falls_back_to_previous_backup(tmp_path):
    db = tmp_path / "p.db"
    s = _open(db)
    s.save(_payload(1))
    s.save(_payload(2))
    s.close()
    db.write_bytes(b"garbage" * 50)
    for ext in ("-wal", "-shm"):
        (tmp_path / ("p.db" + ext)).unlink(missing_ok=True)
    (tmp_path / "p.db.backup.json").write_text("{broken", encoding="utf-8")
    s2 = ProjectStore(db)
    assert s2.db_recovered == "backup"
    assert s2.load_latest()[0]["state"]["playhead"] == 1  # 직전 백업
    s2.close()


def test_corrupt_db_without_any_backup_starts_empty_but_keeps_the_broken_file(tmp_path):
    db = tmp_path / "p.db"
    db.write_bytes(b"garbage" * 50)
    s = ProjectStore(db)
    assert s.db_recovered == "empty" and s.quarantined.read_bytes().startswith(b"garbage")
    assert s.load_latest() == (None, 0)
    s.close()


def test_deleted_db_is_rebuilt_from_backup(tmp_path):
    db = tmp_path / "p.db"
    s = _open(db)
    s.save(_payload(7))
    s.close()
    for ext in ("", "-wal", "-shm"):
        (tmp_path / ("p.db" + ext)).unlink(missing_ok=True)
    s2 = ProjectStore(db)
    payload, _ = s2.load_latest()
    assert payload["state"]["playhead"] == 7 and s2.db_recovered == "backup"
    s2.close()


def test_backup_failure_does_not_fail_the_save(tmp_path, monkeypatch):
    s = _open(tmp_path / "p.db")
    monkeypatch.setattr("os.fsync", lambda _fd: (_ for _ in ()).throw(OSError(28, "full")))
    s.save(_payload(1))  # 예외 없음
    assert s.load_latest()[0]["state"]["playhead"] == 1
    assert not list(tmp_path.glob("*.tmp"))
    s.close()


def test_healthy_db_is_untouched(tmp_path):
    db = tmp_path / "p.db"
    s = _open(db)
    s.save(_payload(1))
    s.close()
    s2 = ProjectStore(db)
    assert s2.db_recovered is None and s2.quarantined is None
    s2.close()

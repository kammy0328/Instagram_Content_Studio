"""`--selftest` (배포본 점검) 경로가 정상 동작하고 결과 파일을 남기는지 확인한다."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_selftest_passes_and_writes_report(tmp_path, monkeypatch):
    from ics import app

    out = tmp_path / "report" / "selftest.txt"
    # run_selftest 는 임시 데이터 폴더를 쓰므로 환경변수를 바꾼다. 테스트 후 원복되도록 monkeypatch 로 감싼다.
    monkeypatch.setenv("ICS_DATA_DIR", str(tmp_path / "x"))
    monkeypatch.setenv("ICS_CACHE_DIR", str(tmp_path / "y"))
    code = app.main(["--selftest", str(out)])
    text = out.read_text(encoding="utf-8")
    assert code == 0, text
    assert "RESULT: ALL PASSED" in text and "[FAIL]" not in text

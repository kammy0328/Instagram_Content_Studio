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
    assert "[PASS] 검정 띠 감지와 자동 크롭 출력" in text
    assert (
        "[PASS] 소리: 재생용 WAV 생성과 출력 장치" in text and "출력 장치 없음" in text
    )  # 테스트는 오디오 장치에 닿지 않는다


def test_selftest_report_is_written_after_each_step_even_if_it_never_finishes(tmp_path, monkeypatch):
    """도중에 프로세스가 죽어도 그때까지의 결과가 파일에 남는다 (실제 배포본 점검에서 원인을 찾기 위해)."""
    from ics import selftest

    out = tmp_path / "partial.txt"
    rep = selftest._Report(out)
    rep.run("첫째", lambda: "ok")
    text = out.read_text(encoding="utf-8")
    assert "[PASS] 첫째" in text and "(진행 중)" in text
    rep.run("둘째", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert "[FAIL] 둘째: boom" in out.read_text(encoding="utf-8")
    rep.flush(final=True)
    assert "RESULT: 1 FAILED" in out.read_text(encoding="utf-8")

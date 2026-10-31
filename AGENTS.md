# AGENTS.md: AI 작업 지침

이 저장소를 수정하거나 리뷰하는 모든 AI(Codex, Gemini, Claude 등)가 지켜야 할 규칙입니다.
구조와 설계 의도는 `ARCHITECTURE.md`, 사용법은 `README.md`, 리뷰 요청 양식은 `docs/REVIEW_BRIEF.md` 를 먼저 읽으세요.

## 1. 한눈에 보는 프로젝트

마스터링 영상(SDR MP4/MOV) 1개를 인스타그램 STORY / REELS / FEED 파일로 바꾸는 Windows 데스크톱 앱입니다.
Python 3.11+ / PySide6 / FFmpeg(subprocess) / SQLite. 개발 지시서의 우선순위는 다음과 같습니다.

**타임라인 정확도 > 원본 화질 보존 > 피드 편집 > 자동저장 > 렌더링 안정성 > 크레딧 자동화**

## 2. 환경 준비와 검증 명령

```bash
python -m venv .venv && source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
# FFmpeg 필요: libx264, prores_ks, aac 인코더 포함 (Ubuntu: sudo apt-get install ffmpeg)
# 화면 없는 서버에서는 Qt 오프스크린 + 시스템 라이브러리 필요:
#   sudo apt-get install libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1 libglib2.0-0
export QT_QPA_PLATFORM=offscreen

python -m pytest tests            # 전체 (약 1~2분, 실제 인코딩 포함). 수정 후 반드시 실행
ruff check .                      # 린트
black --check .                   # 포맷 확인 (고치려면 black .)
```

빠른 확인용(수 초, 순수 로직만):

```bash
python -m pytest tests/test_timecode.py tests/test_layout.py tests/test_cuts.py tests/test_caption.py tests/test_credits.py tests/test_xml_import.py
```

CI(`.github/workflows/ci.yml`)도 같은 명령을 실행합니다. **세 명령이 모두 통과하지 않으면 PR 을 완료로 보고하지 마세요.**

## 3. 작업 규칙

1. **브랜치/PR**: 자기 이름의 브랜치(`review/codex`, `review/gemini` 등)에서만 작업하고 `main` 에 직접 push 하지 않습니다. 변경은 PR 로 올리고 PR 템플릿을 채웁니다.
2. **테스트 먼저**: 버그를 고칠 때는 먼저 실패하는 테스트를 추가해 재현하고, 수정 후 통과를 확인합니다. 테스트 없이 "고쳤다"고 보고하지 마세요. 실제 실행한 명령과 출력 요약을 PR 에 적습니다.
3. **작게, 한 가지씩**: PR 하나에 한 가지 주제. 관련 없는 파일의 이름 변경, 재정렬, 스타일 변경 금지 (포매터 `black`/`ruff` 설정은 `pyproject.toml` 에 고정되어 있으며 줄 길이는 120).
4. **기능 삭제/단순화 금지**: 개발 지시서의 기능을 임의로 빼거나 약화하지 않습니다. 기술적 제약이 있으면 "제약, 대안, 검증 방법"을 PR 에 적고 사람의 판단을 받습니다.
5. **사용자 메시지는 한국어**: 화면 문구와 오류 메시지는 이해하기 쉬운 한국어로 씁니다. 코드 식별자와 주석 언어는 기존 파일의 관례(주석은 한국어)를 따릅니다.
6. **확인하지 못한 것은 확인하지 못했다고 적기**: 이 환경에서 검증할 수 없는 항목(Windows 실행, 오디오 장치, 실제 Premiere/FCP XML, 인스타그램 앱 동작)은 추측으로 단정하지 말고 "미검증"으로 표시합니다.
7. **의존성 추가 금지(원칙)**: 꼭 필요하면 이유와 라이선스를 PR 에 적습니다. 런타임에 네트워크를 쓰는 코드는 넣지 않습니다 (자동 게시, 인스타그램 로그인 기능 없음이 요구사항).
8. **FFmpeg 버전 차이 주의**: 번들 FFmpeg 는 8.x, 개발/CI(Ubuntu)는 6.1 입니다. 7.1 이상은 인코더 옵션(`-color_primaries`/`-color_trc`)보다 프레임 속성을 우선하므로 출력 태그는 `filters.OUTPUT_TAGS_FILTER`(setparams)로 명시합니다. 색 태그를 다루는 변경은 `test_output_stream_spec` 로 검증하고, 가능하면 `windows-build` 워크플로(Windows + FFmpeg 8.x)도 확인합니다.
9. **비밀/대용량 금지**: API 키, `.env`, FFmpeg 실행 파일, 영상/이미지 결과물, `*.sqlite` 를 커밋하지 않습니다. 테스트용 미디어는 코드로 생성합니다 (`tests/helpers.py`).
10. **폴더/파일명 규칙**: 새 폴더 이름에는 공백 대신 밑줄(`_`)을 쓰고 `&` 는 쓰지 않습니다.

## 4. 건드리면 안 되는 부분 (바꾸려면 이유 + 테스트 + 검증 결과 필요)

| 위치 | 이유 | 관련 테스트 |
|---|---|---|
| `ics/core/frameindex.py` 의 `seek_time`, PTS 기반 인덱스 | 프레임 정확도의 기반. 목표 PTS 보다 1/4 프레임 앞으로 시킹해 반올림으로 다음 프레임이 선택되는 것을 막음 | `test_media_index.py`, `test_render_accuracy.py` |
| `ics/render/engine.py` 의 `clip_times`, `build_video_cmd` (입력 `-ss` + `setpts` + `atrim` + `-frames:v` + `-fps_mode cfr`) | 1프레임도 더하거나 빼지 않는 컷과 오디오 싱크 | `test_render_accuracy.py` |
| `ics/core/decode.py`, `ics/render/filters.py` 의 색 변환 옵션 (행렬/범위를 항상 명시) | 태그가 없어도 색이 일정해야 함. Rec.709 Limited 출력, 스틸은 Full | `test_stills_color.py` |
| `ics/core/media.py` 의 SD/HD 가정 규칙(높이 ≤576 이고 폭 <1280 이면 BT.601), HDR 차단 | 지시서: SDR 전용. 규칙은 mpv/VLC 와 동일 | `test_media_index.py`, `test_stills_color.py` |
| `engine._assert_original` 및 "최종 출력에 프록시 사용 금지" | 최종 화질은 항상 원본에서. 프록시는 미리보기 전용 | `test_render_accuracy.py`, `test_planner_queue.py` |
| `.part` 임시 파일 -> 검증 -> 원자적 이름 변경 | 불완전 파일을 결과 폴더에 남기지 않음 | `test_planner_queue.py` |
| `ics/core/store.py` 체크섬 스냅샷, `mark_open/mark_closed` | 비정상 종료와 손상 복구 | `test_project_state.py` |
| 출력 폴더/파일명 규칙 (`planner.py`) | 개발 지시서 13항 그대로 | `test_planner_queue.py` |
| `ics/core/credits.py` 의 "동명이인 자동 확정 금지, 불확실한 줄은 원문 보존" | 지시서 요구. 자동으로 잘못된 계정을 붙이면 안 됨 | `test_credits.py` |
| 길이 제한 STORY 60초, REELS 180초, FEED 영상 60초, FEED 최종 20개, 겹침은 경고만(자동 삭제 금지) | 지시서 요구 | `test_project_state.py`, `test_planner_queue.py` |
| 인코딩 설정 UI 를 만들지 않는 것 | 지시서: 인코딩 완전 자동 | 해당 없음 |
| Out 은 UI 에서 "현재 프레임 포함", 내부 모델은 `[start, end)` 반열린 구간 | 혼동하기 쉬운 지점. 두 표현을 섞지 말 것 | `test_ui_smoke.py` |

## 5. 코드 스타일

- Python 3.11+, 타입 힌트 사용, 모든 모듈 첫 줄에 `from __future__ import annotations`.
- `ics/core/`, `ics/render/` 는 **Qt 에 의존하지 않습니다.** UI 코드는 `ics/ui/` 에만 둡니다.
- 시간/프레임 계산에는 `float` 대신 `fractions.Fraction` 을 씁니다 (23.976 같은 FPS 오차 방지).
- 컷 편집(`ics/core/cuts.py`)은 순수 함수입니다. 입력 리스트를 수정하지 말고 새 리스트를 반환하세요.
- 스레드에서 UI 를 직접 만지지 말고 Qt 시그널로 전달합니다.
- 임포트 정렬과 포맷은 `ruff --fix` / `black` 이 결정합니다. 손으로 맞추지 마세요.

## 6. 알려진 미검증 항목 (우선 리뷰 대상)

`docs/REVIEW_BRIEF.md` 의 "검증이 필요한 항목" 표를 참고하세요.

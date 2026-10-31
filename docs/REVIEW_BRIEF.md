# REVIEW_BRIEF: 다른 AI 에게 리뷰/검증/수정을 맡길 때

## 브랜치와 PR 방식

| 브랜치 | 용도 |
|---|---|
| `main` | 사람이 승인한 코드만. AI 가 직접 push 하지 않음 |
| `review/codex` | Codex 작업용 |
| `review/gemini` | Gemini 작업용 |
| `review/<이름>` | 그 밖의 AI |

각 AI 는 `main` 에서 자기 브랜치를 받아 작업하고 `main` 으로 PR 을 올립니다. 결과 비교는 다음 명령으로 합니다.

```bash
git diff review/codex review/gemini --stat
git diff main review/codex
```

## 각 AI 에게 줄 요청문 (복사해서 사용)

```
이 저장소는 마스터링 영상을 인스타그램 STORY/REELS/FEED 로 변환하는 Windows 데스크톱 앱입니다.
먼저 README.md, ARCHITECTURE.md, AGENTS.md, docs/REVIEW_BRIEF.md 를 읽으세요.

작업 순서
1. 환경을 준비하고 `python -m pytest tests`, `ruff check .`, `black --check .` 가 통과하는지 먼저 확인해 결과를 보고하세요.
2. 아래 "검증이 필요한 항목"을 코드와 테스트로 점검하고, 버그/위험/누락을 심각도 순으로 목록화하세요 (파일:줄, 재현 방법 포함).
3. 고칠 가치가 있는 것은 먼저 실패하는 테스트를 추가한 뒤 수정하세요. AGENTS.md 의 "건드리면 안 되는 부분"은 이유와 근거가 없으면 바꾸지 마세요.
4. 자기 브랜치(review/<이름>)에서 작업하고 main 으로 PR 을 올리세요. PR 템플릿의 검증 결과에 실제 실행 명령과 출력 요약을 적으세요.
5. 확인하지 못한 것(Windows 실행, 오디오, 실제 XML 등)은 추측하지 말고 "미검증"으로 적으세요.
```

## 검증이 필요한 항목 (우선순위 순)

| # | 영역 | 확인 내용 | 단서 |
|---|---|---|---|
| 1 | 프레임 정확도 | 구간 시작/끝, 스틸 추출이 1프레임도 어긋나지 않는지. 특히 VFR, B-프레임이 있는 H.264/H.265, 시작 PTS 가 0 이 아닌 파일, 드롭프레임 TC | `core/frameindex.py`, `render/engine.py`, `tests/test_render_accuracy.py` |
| 2 | 색 정확도 | 태그 없음/Full range/10·12비트/BT.2020 SDR 에서 입력과 출력의 색 차이. 스틸 JPG ICC 처리 | `core/decode.py`, `render/filters.py`, `tests/test_stills_color.py` |
| 3 | 타임라인 편집 | 병합/해제/롤/트림 경계 이동의 경계 조건(1프레임 컷, 맨 앞/뒤, 겹침/빈 구간), 드래그 중 Undo 그룹 | `core/cuts.py`, `ui/timeline.py` |
| 4 | XML 파서 | 공개 명세와 샘플 기준으로만 작성됨. Premiere/FCP7/FCPXML/EDL 의 다양한 변형(중첩 시퀀스, 속도 변경, 비활성 클립, 멀티 트랙, 드롭프레임 TC)에서 깨지지 않는지 | `core/xml_import.py`, `tests/data/`, `tests/test_xml_import.py` |
| 5 | 자동저장/복구 | 저장 중 강제 종료, 디스크 가득 참, 손상된 DB, 동시 실행 두 개 | `core/store.py`, `core/autosave.py`, `tests/test_project_state.py` |
| 6 | 렌더 안정성 | 취소 타이밍, 출력 폴더가 사라지는 경우, 권한 오류, 같은 이름 충돌, 매우 긴 영상(수 분~수십 분)에서의 메모리/시간 | `render/queue.py`, `render/engine.py` |
| 7 | 크레딧 파서 | 한글/영문 혼용, 다양한 구분자, URL/`@`/전각 문자, 역할 사전 누락, 동명이인 처리, 잘못 자동 확정되는 경우가 없는지 | `core/credits.py`, `tests/test_credits.py` |
| 8 | 캡션 글자 수 | 이모지/ZWJ/변형 선택자/한글 자모 조합에서 세 가지 계산이 올바른지, 분리 시 행이 잘리지 않는지 | `core/caption.py` |
| 9 | UI 동작 | 단축키 충돌, 포커스에 따라 단축키가 막히는 경우, 탭 전환 중 재생 상태, 큰 창/작은 창 레이아웃, HiDPI | `ui/main_window.py`, `ui/panels.py` |
| 10 | Windows 특이점 | 경로(공백, 한글, `&`, 긴 경로, UNC/NAS), 콘솔 창 억제, 파일 잠금, `os.startfile`, 인코딩(cp949) | `core/ffbin.py`, `paths.py`, `ui/dialogs.py` |

## 사람이 직접 확인해야 하는 것 (AI 가 대신할 수 없음)

- Windows 실기에서 설치 파일 빌드와 실행 (`build_windows.bat`)
- 미리보기 오디오 재생과 싱크
- 인스타그램 앱에서의 캡션 2200자 한도, 프로필 그리드 크롭(기본 4:5) 비율
- 실제 작업용 Premiere/FCP XML 로 컷 불러오기
- FFmpeg GPL 빌드 배포 시 라이선스 검토

## 보고 형식 (리뷰 결과)

```
[심각도 높음/중간/낮음] 한 줄 요약
- 위치: path/to/file.py:123
- 재현: (명령 또는 테스트 코드)
- 영향: (어떤 입력에서 어떤 잘못된 결과가 나오는가)
- 제안/수정: (PR 링크 또는 방법)
- 검증: (실행한 명령과 결과)
```

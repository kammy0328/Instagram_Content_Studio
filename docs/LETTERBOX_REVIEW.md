# 검정 띠 자동 크롭 검수 (v1.2.0)

기준: `origin/main` 7defebc. 검수일: 2026-11-08 (한국 시간).

## 동작

- STORY/REELS/릴스 커버/FEED 각 스틸·영상의 화면 배치에 `검정 띠 자동 크롭`과 `다시 감지`를 제공한다.
- 기본값은 꺼짐이며, 끄면 감지 결과는 보존하고 원본 영역을 사용한다. 확대/위치 값은 보존한다.
- 원본 전체의 처음·끝을 포함한 최대 15개 프레임을 균등 샘플링한다. 긴 변 960px 이하로 줄여 분석한다.
- 매우 어두운 샘플은 제외하며, 양쪽에 확인된 공통 띠만 제거한다. 장면별 띠 크기가 다르면 가장 적게 잘리는 공통 영역을 사용한다.
- 표시 좌표(SAR/회전 반영)로 저장하고 기존 배치를 감지 영역 안에서 계산한다. 색 변환 전에 원본을 한 번 크롭한다.
- 소스 보기/ORIGINAL_STILLS는 전체 원본을 유지한다. 출력 미리보기/영상/스틸/STORY 흐린 배경은 감지 영역을 사용한다.
- FEED 원본 비율의 경고도 크롭 후 실제 출력 비율로 판단한다.
- 감지는 백그라운드 단일 작업이며 결과는 Qt 시그널로 전달한다. 다른 항목을 선택하거나 감지 중 끄거나 항목을 삭제해도 다른 항목에 잘못 적용하지 않는다.
- 처음 감지 중인 활성 크롭은 내보내기를 차단한다. 감지 중 저장·종료했다면 다음에 열 때 다시 감지한다.
- 프로젝트의 기존 직렬화 경로로 저장·복원·Undo/Redo한다. 별도 저장소/런타임 의존성을 추가하지 않았다.

## 먼저 재현한 항목

- `python -m pytest tests/test_letterbox.py -q`: 구현 전 13개 실패. 감지 모듈과 크롭 옵션이 없음을 확인했다.
- `python -m pytest tests/test_planner_queue.py -k letterbox_feed_warning`: 수정 전 1 failed.
  예: 960×540 원본에서 상하 60px 제거 → 실제 출력은 약 2.286:1이나 기존 경고는 원본 16:9로 판단해 누락했다.
- 회귀 테스트는 레터박스/필러박스/창 형태 여백, 암전/무띠/변동 띠, 켜기·끄기, Fit/Fill/원본 비율,
  SAR/90도 회전, 실제 스틸·영상 크롭, STORY 흐린 배경에서 프레임 번호 보존,
  저장·재열기·Undo/Redo, 감지 중 항목 변경·끄기·실패를 검사한다.

## 보호 구역

`render/filters.py`의 STORY 배경 가지에 감지 영역 크롭만 추가했다. 전경만 잘라내고 배경에 원본 띠가 남는 불일치를 막기 위해 필요하다.
색 행렬/범위/디더/출력 태그/인코딩 설정은 바꾸지 않았다. 기존 색/배경 회귀 테스트와 실제 크롭 렌더링으로 검사한다.
`frameindex.seek_time`, `decode` 색 옵션, `engine.clip_times/build_video_cmd/_assert_original`, 저장소 체크섬,
`.part` 검증·원자 교체, 출력 파일명, 길이 제한은 바꾸지 않았다.

## 제약과 미검증

- 샘플 사이의 모든 프레임을 검사하지 않는다. 장면별 비율 변화, 검정 배경의 연출, 자막/로고, 압축 노이즈 때문에 오탐/미탐이 가능하다.
- 축소 분석 경계는 그림을 보존하는 방향으로 짝수 정렬하므로 경계에 얇은 띠가 남을 수 있다.
- 불확실한 원본에서는 끄고 전체 프레임을 보존할 수 있다. 모든 장면을 확인한 자동 감지 결과라고 단정하지 않는다.
- 실제 사용자 마스터링 영상/오디오 청취/Premiere·FCP XML/Instagram 업로드/설치·업데이트·제거는 미검증.
- 화면 QA는 코드로 만든 테스트 미디어와 Qt 오프스크린 캡처 기준이다.

## 실제 실행 결과

환경: Windows 11 x64, Python 3.12.10, PySide6 6.12.0, FFmpeg 8.1 shared.

| 명령 | 결과 |
|---|---|
| 수정 전 `python -m pytest tests` | 372 passed in 100.98s |
| 수정 후 `python -m pytest tests` | **398 passed in 141.28s** |
| `ruff check .` | **All checks passed!** |
| `black --check .` | **90 files would be left unchanged** |
| `pyinstaller --noconfirm ics.spec` | **Build complete!** |
| 배포 EXE `--selftest` | **RESULT: ALL PASSED**, 검정 띠 감지와 9:16/4:5/원본 비율 스틸·영상 출력 포함 |
| `git diff --check` | 오류 없음 |
| `ISCC.exe installer/ics_installer.iss` | **Successful compile (114.328 sec)** |

설치 EXE와 포터블 ZIP은 `letterbox-1.2.0-preview` GitHub 미리보기 릴리스로 배포한다.
`dist/README.md`에서 다운로드할 수 있다. 코드와 테스트만 PR로 제공하며 실행 파일/FFmpeg/미디어는 커밋하지 않는다.

회전 테스트 미디어는 `-display_rotation` 입력 옵션으로 생성한다. 이 옵션은
[FFmpeg 6.1 소스](https://github.com/FFmpeg/FFmpeg/blob/n6.1/fftools/ffmpeg_opt.c#L1547)에도 존재한다.
Windows의 FFmpeg 8.1에서는 stream copy 시 `-metadata rotate=90`만으로는 회전 정보가 기록되지 않아
테스트 생성 방식을 바꿨으며, 앱의 기존 회전 해석/색 변환 코드는 바꾸지 않았다.

# v1.1.0 디자인 · Windows 배포 검수

검수일: 2026-11-07. 기준 main: `50880d047ec9ee6670c68f638c2b334e85bc320e`.

## 화면 개선

- 프로젝트 홈에 시작 안내, 출력 종류, 최근 프로젝트, 빈 목록 안내를 배치했다.
- 다크 팔레트, 라벤더 강조색, 공통 앱/설치 아이콘, 단계 번호가 있는 탐색 메뉴를 적용했다.
- 상단은 프로젝트 이름·저장 상태·내보내기에 집중한다. 영상 정보/단축키/업로드 준비는 도구 메뉴에서 연다.
- 미리보기와 인스펙터를 구분하고, 재생 조작과 보기 옵션을 두 줄로 나눴다.
- 타임라인의 보조 편집은 추가 작업 메뉴에 유지한다. 크레딧 조작 버튼은 두 열로 배치했다.
- 원본 시킹, 렌더 명령, 색 변환, 컷/피드 모델, 저장 포맷은 바꾸지 않았다.

## 재현하고 수정한 문제

| 심각도 | 위치 | 재현 | 수정과 검증 |
|---|---|---|---|
| 높음 | `ics.spec`의 Analysis 이후 DLL 목록 | 빌드한 EXE에서 `--selftest` 실행 → QtCore ImportError, exit 1, 결과 파일 없음 | 환경 PATH의 Poppler `icuuc.dll`이 혼입된 것을 Analysis 목록과 PE import로 확인. 해당 파일을 빌드 폴더에서 제외하면 Qt가 로드됨을 먼저 확인했다. spec에서 Qt 자체가 제공하지 않은 동명 ICU DLL을 제외해 Windows OS ICU를 사용한다. 수정 후 실제 EXE 자체 점검 exit 0 |
| 중간 | `ics/ui/main_window.py`의 상단 제목/배치 | `test_editor_and_credits_fit_laptop_viewport` 수정 전 실패: 긴 프로젝트명에서 1280px 요청에도 실제 너비 3588px | 제목을 말줄임표로 표시하고 전체 이름은 툴팁에 보존. 메뉴/재생 바/크레딧 배치를 조정. 실제 한글 글꼴 폭을 사용한 1280×800 회귀 테스트 통과 |
| 중간 | `ics/ui/services.py`의 `AudioPlayer.play` | 배포 EXE 자체 점검에서 `소리 출력 오류(NoError)` 발생. `test_audio_error_uses_value_across_qt_enum_namespaces` 수정 전 1 failed, 1 passed | Qt 버전별 Python Enum 클래스 동일성 대신 오류 값을 비교. NoError와 실제 OpenError 두 경우를 검사. 수정 후 2 passed, 실제 출력 장치에서 ActiveState 확인 |

Qt 오디오 네임스페이스 변경은 [Qt QAudioSink 문서](https://doc.qt.io/qt-6.12/qaudiosink.html)도 참고했다.
정상 상태를 오류로 잘못 표시한 문제를 고친 것이며, 장치 오류를 무시하도록 바꾸지 않았다.

## 실제 실행 결과

환경: Windows 11 x64, Python 3.12.10, PySide6 6.12.0, PyInstaller 6.22.3, Inno Setup 6.7.3.
번들 FFmpeg/FFprobe: `n8.1.3-27-g8dca1586f4-20261031`, GPL shared 빌드. 라이선스 문서를 함께 포함한다.

| 명령 | 결과 |
|---|---|
| 최초 main `python -m pytest tests` | 260 passed in 71.71s |
| 최초 main `ruff check .` / `black --check .` | 모두 통과, 61 files unchanged |
| 수정 통합본 `python -m pytest tests` | **263 passed in 95.92s** |
| 수정 통합본 `ruff check .` | **All checks passed!** |
| 수정 통합본 `black --check .` | **63 files would be left unchanged** |
| 오디오 수정 단독 worktree `python -m pytest tests` | **262 passed in 75.14s** |
| 오디오 수정 단독 `ruff check .` / `black --check .` | 모두 통과, 62 files unchanged |
| `pyinstaller --noconfirm ics.spec` | Windows GUI EXE 빌드 성공 |
| `InstagramContentStudio.exe --selftest 결과.txt` | **exit 0, RESULT: ALL PASSED** |
| `ISCC.exe installer/ics_installer.iss` | **Successful compile**, 설치 EXE 생성 |

EXE 자체 점검은 번들 FFmpeg/인코더, Qt 플러그인, 테스트 영상 생성·분석, 결과물 5개 렌더링,
편집 창 생성·종료, 재생용 WAV 생성, 실제 기본 출력 장치의 재생 시작(ActiveState)을 포함한다.
기존 사용자 프로젝트를 사용하지 않고 임시 폴더에서 수행했다.

화면 QA는 생성한 테스트 영상을 사용해 STORY/FEED/CREDITS/프로젝트 홈/1280×800 화면을 캡처해 확인했다.
일반 Windows 플랫폼으로 실행한 EXE가 프로젝트 홈 창으로 등록되는 것도 확인했다.
실제 화면 캡처 전 해당 창이 닫혀, 배포 EXE의 장시간 수동 조작 검증은 완료로 보지 않는다.

## 다운로드 파일

- `release/InstagramContentStudio_1.1.0_Setup.exe`: 설치용 단일 EXE.
- `release/InstagramContentStudio_1.1.0_Portable.zip`: 폴더 전체를 푼 다음 내부 EXE를 실행한다.
- `release/SHA256SUMS.txt`: 다운로드 파일의 SHA-256.
- `release/UI_preview.png`: 생성한 테스트 영상을 사용한 편집 화면.

EXE/ZIP/테스트 영상/DB/FFmpeg는 Git에 커밋하지 않는다. 아이콘과 소스만 PR에 포함한다.

## 미검증

- 설치 마법사를 끝까지 진행하는 실제 설치/기존 설치 업데이트·제거.
- 사람이 들은 소리의 품질, 장시간 미리보기 AV 싱크. 자동 점검의 ActiveState는 청취 검증을 대신하지 않는다.
- 실제 Premiere/FCP 내보내기 XML, 인스타그램 업로드/캡션/프로필 그리드.
- Windows 10 실기, UNC/NAS/매우 긴 경로, 모든 배율/화면 크기, 모든 미디어 코덱 조합.

테스트와 EXE 자체 점검 범위에서 추가 실패는 없었다. 모든 환경에서 버그가 없다고 보증하는 결과는 아니다.

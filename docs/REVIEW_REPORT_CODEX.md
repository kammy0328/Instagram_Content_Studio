# Codex 리뷰 결과 (2026-10-31)

## 판단과 범위

초기 main `761433f9a2dd3966bd6f075abc1571cf63f44d28`은 그대로 배포 승인하기 어렵다.
아래 결함을 실패 테스트로 재현한 뒤 수정했다. 실제 작업용 XML, 설치 프로그램, 오디오 장치,
인스타그램 앱, DB 전체 손상 복구까지 검증한 것은 아니므로 “문제 없음”으로 판정하지 않는다.
별도 v2.0 개발지시서 원문은 저장소에 없다. README/ARCHITECTURE/AGENTS/REVIEW_BRIEF에
명시된 요구사항과 기존 테스트를 기준으로 검토했다.

파일:줄은 수정이 적용된 통합 작업 트리 기준이다. 개별 PR에서는 공통 선행 변경과 적용 순서에 따라
줄 번호가 조금 달라질 수 있으며, 아래 테스트 이름으로 동일 사례를 재현할 수 있다.

## 환경과 최초 검증

- Windows, Python 3.12, 별도 `.venv`; `pip install -r requirements-dev.txt`.
- FFmpeg/FFprobe: `C:\ffmpeg\bin`, `9.0.2-essentials_build-www.gyan.dev`.
- Qt: PySide6 6.12.0, `QT_QPA_PLATFORM=offscreen`.
- `python -m pytest tests`: **4 failed, 123 passed in 32.32s**.
- `ruff check .`: **All checks passed!**
- `black --check .`: **50 files would be left unchanged**.
- 최초 실패: HDR 검출, 색 태그 인식, 태그 없는 SD 가정, Windows 경로 비교.
  앞의 세 건은 테스트 영상 자체의 메타데이터가 의도와 달랐고, 마지막은 테스트의 POSIX 경로 가정이었다.
  실제 HDR 판정/SD 가정 규칙을 약화해 통과시키지 않았다.

## 발견 사항 (심각도 순)

### [높음, 수정] 저장 실패를 무시한 종료와 저장 순서 경쟁

- 위치: `ics/core/project.py:73`, `ics/core/autosave.py:93`, `ics/ui/main_window.py:1082`.
- 재현: `python -m pytest tests/test_save_regressions.py -k close_failure`.
  `store.save`가 `OSError(28)`을 내도록 주입하고 종료한다. 기존 코드는 실패 결과를 무시해
  정상 종료 표시를 남겼으며, 지속 실패에서는 종료 중 재시도 루프도 발생했다.
- 영향: 최근 편집이 저장되지 않은 채 창이 닫히거나 종료가 지연된다. 기존 flush는 작업 스레드와
  별도로 직접 저장하므로 시간 초과/동시 요청에서 쓰기 순서가 역전될 수 있었다.
- 수정: 저장 작업은 단일 작업 스레드가 수행하고 flush는 완료를 기다린다.
  실패 시 미저장 상태와 세션을 유지하고 창을 닫지 않는다. 공간 확보 후 재시도가 테스트에서 성공한다.
- 범위: 실제 디스크를 채운 실험이 아니라 오류 주입이다.

### [높음, 수정] 같은 프로젝트의 동시 실행이 서로의 상태를 덮어씀

- 위치: `ics/core/store.py:32`, `ics/app.py:127` 인근 프로젝트 열기.
- 재현: `python -m pytest tests/test_save_regressions.py -k 'concurrent_writer or forced_termination'`.
  기존에는 동일 DB의 두 ProjectStore가 모두 열리고 저장할 수 있었다.
- 영향: 마지막 저장이 다른 실행의 편집을 대체하며, session_open도 소유 실행을 구분하지 못한다.
- 수정: DB별 OS 파일 잠금을 보유하고 두 번째 실행을 한국어 메시지로 차단한다.
  별도 Windows 프로세스를 실제 강제 종료한 뒤 잠금 해제, 비정상 종료 감지, 저장본 복구를 확인했다.
  잠금 파일은 삭제하지 않으며 OS가 핸들 종료 시 잠금을 해제한다.

### [높음, 수정] 입력 직후 종료/렌더링 시 캡션·크레딧 누락

- 위치: `ics/ui/credits_page.py:206`, `ics/ui/main_window.py:923`, `ics/app.py:156`.
- 재현: `python -m pytest tests/test_ui_smoke.py -k 'close_saves or close_failure or back_to_projects or render_plan_includes'`.
  텍스트를 바꾸고 500~600ms 안에 닫거나 렌더 계획을 만든다.
- 영향: 화면에는 보이는 입력이 DB/출력 계획에 빠진다. 프로젝트 목록 복귀도 close 취소 결과를 무시하고 창을 삭제했다.
- 수정: 종료/계획 작성 전에 대기 중인 입력을 반영하고, close가 거부되면 기존 창을 유지한다.

### [높음, 수정] VFR의 STORY/REELS/FEED 시간 제한 우회

- 위치: `ics/core/project.py:258`, `ics/render/planner.py:100` 인근 길이 검증.
- 재현: `python -m pytest tests/test_review_regressions.py -k vfr`.
  앞 200초는 10fps, 뒤 200초는 30fps인 합성 PTS 인덱스에서 평균 20fps로 길이를 제한한다.
  기존 STORY 1200프레임은 실제 120초이며 저장 상태를 직접 읽은 렌더 계획도 이를 허용했다.
- 수정: Fraction PTS 차이와 이진 탐색으로 실제 시간에 맞게 Out을 제한한다.
  과거 저장본의 초과 길이는 렌더 계획에서 차단한다. 60/180초, FEED 20개 규칙은 유지한다.
- 범위: 긴 VFR 파일의 실제 인코딩 부하 테스트는 아니다.

### [중간, 수정] 출력 검증이 1프레임 오차를 허용하고 늦은 취소가 기존 결과를 교체

- 위치: `ics/render/engine.py:180`, `ics/render/engine.py:211`.
- 재현: `python -m pytest tests/test_review_regressions.py -k 'verification or cancel_during'`.
  실제 4프레임 결과에 기대값 3/5를 지정하거나, 전체 디코딩 검증 완료 직후 취소 이벤트를 발생시킨다.
- 수정: 프레임 수가 정확히 같아야 통과하며, 검증 후 원자적 교체 직전에 취소를 다시 확인한다.
  기존 결과와 `.part` 정리도 검사한다. 시킹/PTS/오디오 트림/인코딩 레시피는 변경하지 않았다.

### [중간, 수정] 무태그 입력의 최종 영상에 Rec.709 전달함수·원색 태그 누락

- 위치: `ics/render/filters.py:51`.
- 재현: `python -m pytest tests/test_output_metadata.py`.
  무태그 HD 입력을 렌더링하면 color_space/range는 있으나 color_transfer/primaries가 빠졌다.
- 수정: 출력 필터 프레임에도 setparams로 BT.709/Limited를 명시한다.
  색 행렬 계산/SD 가정/스틸 Full range 변환은 유지한다.
  이는 태그 보장 수정이며 BT.2020 SDR의 색역/감마 변환 구현을 뜻하지 않는다.

### [중간, 수정] 병합 컷을 내부 경계 너머로 트림한 후 해제하면 음수 길이

- 위치: `ics/core/cuts.py:94`.
- 재현: `python -m pytest tests/test_review_regressions.py -k unmerge`.
  `[0,40),[40,90),[90,150)` 병합 → 시작을 100으로 트림 → 해제.
  기존 결과에 `[100,40)` 같은 잘못된 구간이 포함됐다.
- 수정: 파트와 현재 바깥 경계의 교집합을 복원하며 사라진 파트를 제외한다.
  원래 컷 복원 데이터는 유지하고 메타데이터도 깊은 복사한다.

### [중간, 수정] 비활성 XML 트랙/클립과 오디오 전용 클립을 영상 컷으로 취급

- 위치: `ics/core/xml_import.py:124`, `ics/core/xml_import.py:214`.
- 재현: `python -m pytest tests/test_xml_import.py -k 'disabled or audio_only'`.
  xmeml track의 enabled=FALSE, FCPXML clip의 enabled=0 또는 srcEnable=audio 사례.
- 수정: 해당 항목을 영상 컷에서 제외하고 원래 트랙 번호를 유지한다.
- 근거: [Apple FCPXML 1.10 DTD](https://developer.apple.com/documentation/professional-video-applications/document-type-definition)의 clip 속성.
  합성 XML 기준이며 실제 Premiere/FCP 내보내기 파일 검증은 아니다.

### [중간, 수정] 크레딧의 유사 도메인/일부 ID만 있는 상호명을 자동 확정

- 위치: `ics/core/credits.py:13`, `ics/core/credits.py:350`.
- 재현: `python -m pytest tests/test_credits.py -k 'similar_domain or only_one_handle'`.
  `Director | A https://notinstagram.com/someone`이 @someone을 만들었고,
  `Production | A & B (@studio)`는 브랜드명인지 불확실한데 자동으로 두 사람으로 나뉘었다.
- 수정: 프로필 URL의 도메인 경계를 확인하고, 모호한 구분자 양쪽 모두 ID가 명확하지 않으면 원문을 검토 대상으로 보존한다.

### [낮음, 수정] 존재하지 않는 드롭프레임 TC를 정상 프레임으로 해석

- 위치: `ics/core/timecode.py:122`.
- 재현: `python -m pytest tests/test_review_regressions.py -k drop_frame`.
  29.97fps의 `00:01:00;00/01`, 59.94fps의 `00:01:00;00~03`.
- 수정: 건너뛰는 라벨은 None을 반환한다. 10분 배수와 기존 왕복 변환은 유지한다.

### [낮음, 수정] 손상 DB 열기 실패 시 Windows 파일 핸들 누수

- 위치: `ics/core/store.py:57` 인근 `_connect`.
- 재현: `python -m pytest tests/test_save_regressions.py -k corrupt_database`.
  SQLite가 아닌 바이트를 DB 경로에 쓴 뒤 열기에 실패하면 파일 삭제가 WinError 32로 실패했다.
- 수정: 초기 PRAGMA 실패 시 연결을 명시적으로 닫는다. DB 자체의 복구 기능을 추가한 것은 아니다.

## 보호 대상 변경 근거

- PTS 인덱스/seek_time, clip_times/build_video_cmd, 프록시 금지, 색 행렬/범위 계산은 그대로다.
- 시간 제한과 렌더 계획은 평균 FPS 대신 PTS의 실제 길이를 쓰도록 바꿨다. VFR 회귀 테스트가 근거다.
- filters는 출력 태그만 보완했다. 무태그 실제 렌더 결과를 ffprobe로 검사했다.
- store는 체크섬 스냅샷/WAL/보관 30개/mark_open·mark_closed의 값과 의미를 유지한다.
  동시 실행 보호와 실패한 초기화의 핸들 해제만 추가했다.
- 크레딧은 “불확실한 줄 원문 보존”을 강화했다. 동명이인 자동 확정은 추가하지 않았다.
- 원자적 출력은 유지했다. 검증과 최종 교체 사이 취소 검사 및 정확한 프레임 수 검증을 추가했다.
- 새로운 런타임 의존성/네트워크 코드/인코딩 설정 UI는 추가하지 않았다.

## 남은 위험과 미검증

| 영역 | 이번 확인 | 남은 항목 |
|---|---|---|
| 프레임/오디오 | 기존 ProRes/H.264/VFR 컷·오디오 클릭 싱크, 추가 H.264/H.265 시작 PTS=5초 스틸 0/1/마지막 프레임 | 모든 코덱·편집점 조합, 비영점 PTS의 오디오 지연, 장시간 출력은 미검증 |
| 색 | 무태그/Full/10·12비트, JPEG ICC 기존 검사 + 출력 태그 회귀 | BT.2020 SDR은 기존 경고만 제공. 색역/감마 변환·육안 색 비교는 미검증 |
| 타임라인 | 기존 드래그 Undo 그룹/롤/트림 + 큰 트림 후 해제 | 겹침이 심한 비정상 입력의 모든 경계 조합은 미검증 |
| XML | 합성 xmeml/FCPXML/EDL, 비활성 표시 처리 | 실제 XML, 중첩/멀티캠 내부 컷, timeMap/누락 duration 등은 미검증. 단순 spine 파서로 완전 호환을 보장할 수 없음 |
| 저장 | 체크섬 불일치 스냅샷 복구, 오류 주입, 동시 프로세스 차단, Windows 프로세스 강제 종료 | **DB 전체 헤더/페이지 손상은 DatabaseError이며 자동 복구 안 됨**. 별도 정상 DB 백업/복구 정책 필요. NAS 잠금과 Unix flock 분기는 미검증 |
| 렌더 | 실제 인코딩/취소/재시도/권한 경로 실패/기존 파일 보존 | 긴 영상의 메모리·속도, 실제 디스크 가득 참/NAS 단절, 스틸·전체 검증 단계의 즉시 취소는 미검증 |
| 크레딧 | 기존 한영/URL/동명이인 + 잘못된 자동 확정 2사례 수정 | 실제 크레딧 자료의 모든 변형은 미검증 |
| 캡션 | 기존 이모지/ZWJ/줄 단위 분리 + 자모/변형 선택자/국기/피부색 ZWJ 4개 추가 로컬 확인 | 분리 불가능한 2311자 단일 인물 행은 2311자 댓글로 남음. 이름 보존을 우선하므로 사용자가 줄여야 함. 실제 앱 한도 미검증 |
| UI/Windows | Windows에서 Qt offscreen 테스트, 파일 잠금과 해제 | 설치 빌드/설치 후 실행, 실제 오디오 장치, HiDPI/작은 화면/모든 포커스 단축키, UNC/긴 경로/cp949 실기 미검증 |

모든 텍스트/PROJECT_DATA 출력의 트랜잭션 단위 원자성이나 다른 프로젝트가 같은 출력 경로를
공유하는 경우의 렌더 잠금까지 보장하지 않는다. 특히 캡션 파일은 직접 쓰기여서 여러 파일을
교체하는 도중 실패할 때 이전/새 결과가 섞일 수 있다 (`ics/render/queue.py:239` 인근).
실제 XML 샘플, 설치 실기, DB 전체 복구와 출력 폴더 동시 사용은 후속 검증/설계 대상으로 남긴다.

## 검증 증거

각 PR 본문의 “검증 결과”에 해당 브랜치에서 실제 실행한 세 명령과 결과를 기록한다.
공통 테스트 미디어 보정 PR을 먼저 병합한 뒤 각 주제 PR을 병합한다.
수정 전에는 새 회귀 테스트의 실패를 실행으로 확인했으며, 저장 실패 종료는 중단이 필요했던
사례까지 포함해 재현했다. 테스트 미디어/출력물/SQLite/가상환경은 커밋하지 않는다.

# ARCHITECTURE

## 1. 목적과 범위

전문 포스트 프로덕션 마스터링 영상(SDR) 1개를 인스타그램용 STORY 1개, REELS 1개(+커버 JPG), FEED 최대 20개(스틸 JPG와 영상 MP4 혼합)로 변환합니다.
범용 SNS 편집기가 아니라 **프레임 정확도와 원본 화질 보존**이 최우선인 전용 도구입니다. 자동 게시와 인스타그램 로그인은 하지 않습니다.

## 2. 계층 구조

```
ics/ui/        PySide6 화면. 사용자 입력을 Project 명령으로 바꾸고, 시그널로 화면을 갱신
   |
ics/core/      Qt 비의존 로직. Project(편집 상태, Undo/Redo, 자동저장)가 중심
ics/render/    렌더 계획(planner) -> 대기열(queue) -> 엔진(engine, ffmpeg)
   |
FFmpeg/FFprobe (subprocess)      SQLite (프로젝트 저장소, ID 사전)
```

의존 방향은 `ui -> core/render -> ffbin`. `core`/`render` 는 UI 없이 테스트할 수 있어야 합니다 (`tests/` 대부분이 UI 없이 동작).

## 3. 주요 모듈

| 모듈 | 역할 |
|---|---|
| `ics/app.py` | 진입점. 프로젝트 목록 -> 원본 확인/재연결 -> 분석 -> 편집 창. 비정상 종료 복구 안내 |
| `ics/core/media.py` | FFprobe 분석. HDR 오류 차단, VFR/태그 누락/인터레이스/SAR/회전 경고 (`Issue` 목록) |
| `ics/core/frameindex.py` | 프레임 번호 <-> 시각. 패킷 PTS 기반, CFR 은 `Fraction`, VFR 은 PTS 표. 파일로 캐시 |
| `ics/core/timecode.py` | 프레임 <-> 타임코드(Non-drop/Drop-frame), 다양한 입력 형식 파싱 |
| `ics/core/layout.py` | 9:16 / 4:5 지오메트리(Fit/Fill), 업스케일 회피 캔버스 선택, 프로필 그리드 창 계산 |
| `ics/core/decode.py` | 원본에서 정확한 프레임을 디코딩, 색 변환 옵션을 항상 명시 |
| `ics/core/cuts.py` | 컷 병합/해제/복원/경계 이동(롤, 트림)과 겹침/빈 구간 검사. 순수 함수 |
| `ics/core/xml_import.py` | Premiere/FCP7 xmeml, FCPXML, EDL 파서와 마스터 프레임 좌표 매핑(오프셋, 경고) |
| `ics/core/credits.py`, `iddict.py` | 크레딧 정규화 `ROLE | NAME (@id)`, 로컬 ID 사전(SQLite) |
| `ics/core/caption.py` | 글자 수 계산(UTF-16 기본/코드포인트/글자), 행 단위 캡션/댓글 분리 |
| `ics/core/model.py` | `EditState` 등 편집 상태. Undo/Redo 와 자동저장의 단위(딕셔너리로 직렬화) |
| `ics/core/project.py` | 컨트롤러. 모든 편집 명령, `edit()` 트랜잭션, Undo/Redo, 그룹(드래그 1회로 묶기) |
| `ics/core/store.py`, `autosave.py` | SQLite 체크섬 스냅샷, 비정상 종료 감지, 백그라운드 디바운스 저장 |
| `ics/render/filters.py` | ffmpeg 필터와 x264/AAC 파라미터 자동 결정(사용자 설정 없음) |
| `ics/render/engine.py` | 영상 인코딩, JPG/원본 스틸 생성, 출력 검증. 항상 원본에서 직접 디코딩 |
| `ics/render/planner.py` | 출력 파일 목록, 규칙(20개/길이) 검증, 경로/용량/충돌 점검 |
| `ics/render/queue.py` | 백그라운드 순차 실행, 취소, 실패 항목만 재시도, 로그 기록, 잔여 `.part` 정리 |
| `ics/ui/main_window.py` | 편집 창 조립, 단축키, 재생/구간/피드/컷/렌더 연결 |
| `ics/ui/timeline.py` | 눈금자/썸네일/컷 레인/구간 레인, 마우스 편집(선택, 경계 드래그, 스냅) |
| `ics/ui/cutstrip.py` | 컷 스트립: 길이와 무관하게 같은 크기의 타일(썸네일, 번호, 길이). 타임라인과 선택을 양방향 동기화, 오른쪽 클릭 메뉴 요청 |
| `ics/ui/services.py` | 프록시 생성(소리 WAV 먼저), 프레임 요청 합치기와 LRU, 썸네일 캐시, 재생 엔진, 오디오(`AudioPlayer`, `default_audio_output`) |

## 4. 데이터 흐름

```
마스터 파일 -> media.analyze -> MediaInfo --+
                               frameindex ---+--> Project(EditState)  <-- UI 명령 / Undo / Redo
                                                      |  \
                                          autosave -> SQLite 스냅샷    planner.plan_render -> RenderPlan(RenderJob 목록)
                                                                                  |
                                                                  RenderQueue(스레드) -> engine -> .part -> 검증 -> 이름 변경
                                                                                  |
                                                       PROJECT_NAME/{STORY,REELS,FEED,CAPTION,ORIGINAL_STILLS,PROJECT_DATA}
```

## 5. 의도적인 설계 결정 (실수처럼 보여도 바꾸지 마세요)

1. **프레임 번호가 모든 계산의 기준**이고 시간은 `Fraction` 입니다. 23.976/29.97 에서 `float` 누적 오차로 프레임이 밀리는 것을 막기 위해서입니다.
2. **정확히 N 프레임 인코딩**: 입력 `-ss`(목표 PTS 보다 1/4 프레임 앞) + `setpts=PTS-STARTPTS` + 오디오 `atrim` + `-frames:v N` + `-fps_mode cfr`.
3. **Out 은 UI 에서 "포함"**(마지막 프레임), 모델은 `[start, end)`. 변환 지점은 `ics/ui/panels.py`, `main_window.set_out`.
4. **색 행렬/범위를 항상 명시**합니다. 태그가 없으면 mpv/VLC 규칙(SD 이면 BT.601, 아니면 BT.709, Limited)으로 가정하고 사용자에게 알립니다. HDR(PQ/HLG/Dolby Vision)은 오류로 차단합니다.
5. **프록시는 미리보기 전용**: all-intra MJPEG(프레임 번호 1:1) + WAV. 최종 영상/스틸은 항상 원본에서 만듭니다.
6. **스틸 두 종류**: FEED JPG 는 출력 캔버스(sRGB ICC, 4:4:4, q97), ORIGINAL_STILLS 는 원본 해상도 무손실(8비트 PNG, 10/12비트 16비트 TIFF). 원본 스틸은 컷 번호가 없으면 `CUT_001`, 컷이 있는데 어느 컷에도 속하지 않으면 `CUT_000`.
7. **불필요한 업스케일 금지**: 소스가 작으면 1080 폭 대신 900/720 폭 캔버스를 고르고 사용자에게 알립니다 (`layout.TIERS`).
8. **원자적 출력**: `*.part` 로 만들고 ffprobe 패킷 수와 전체 디코딩으로 검증한 뒤 이름을 바꿉니다. 실패/취소/강제 종료로 불완전 파일이 결과 폴더에 남지 않습니다.
9. **자동 확정하지 않는 것들**: 동명이인 ID, 모호한 `&`/`and` 이름 분리, 역할/이름 순서가 바뀐 줄은 검토 대상으로 두고 원문을 보존합니다. 피드 겹침은 경고만 하고 자동 삭제하지 않습니다. 기존 파일 덮어쓰기는 사용자 확인 후에만 합니다.
10. **캡션 글자 수 기본은 UTF-16 코드 유닛**(가장 보수적). 인스타그램의 실제 계산 방식은 공개되어 있지 않으므로 세 가지 기준을 모두 보여 줍니다.
11. **Undo/Redo 는 상태 스냅샷(딕셔너리)** 방식이고 드래그 같은 연속 편집은 `begin_group/end_group` 으로 1회로 묶습니다. 재생 위치와 UI 상태(`playhead`, `ui`)는 Undo 대상이 아닙니다.
12. **자동저장은 새 스냅샷 INSERT 한 번**(트랜잭션)이라 원자적입니다. 마지막 30개 중 체크섬이 맞는 최신 것으로 복구합니다.
13. **컷 편집은 XML 을 수정하지 않는 참고 데이터**입니다. 원본 컷 목록(`orig_cuts`)을 보존해 언제든 복원합니다.
14. **인코딩 설정 UI 가 없습니다.** 지시서의 "완전 자동" 요구입니다 (`render/filters.py`).
15. **컷 정보(XML)는 프로젝트에 하나**이고 STORY/REELS/FEED 가 공유합니다 (`state.cuts`). 탭마다 다시 불러오지 않습니다.
16. **컷 스트립은 타일 크기를 길이에 비례시키지 않습니다.** 타임라인 컷 레인은 길이에 비례해 1프레임 컷의 번호가 보이지 않으므로, 선택의 기본 화면은 고정 크기 타일 목록(`cutstrip.py`)입니다. 대표 프레임은 컷의 가운데 프레임입니다.
17. **FEED 영상 추가 구간의 출처 규칙** (`EditorWindow._feed_pending_range`): 가장 최근에 한 조작이 이깁니다. 컷을 선택했다면 선택한 컷들의 시작~끝(Shift 다중 선택이면 그 범위 전체), I/O 로 직접 지정했다면 그 In/Out 입니다. 예전에 남은 In/Out 이 새로 선택한 컷을 덮어쓰지 않게 하려는 규칙입니다.
18. **앱은 `quitOnLastWindowClosed=False`** 입니다 (`app.py`). 편집 창 → 프로젝트 목록 전환 중에는 열린 창이 잠시 없어서 기본값이면 앱이 종료됩니다. 종료는 사용자가 편집 창을 닫거나 프로젝트 목록을 취소할 때만 명시적으로 합니다.
19. **컷 나누기는 재생 헤드 프레임을 뒤쪽 컷의 첫 프레임으로 만듭니다** (`cuts.split`, Premiere 의 Razor 와 같은 관례). 앞 컷은 기존 id 를 유지하고 뒤 컷은 새 id 를 받으며 둘 다 `manual` 입니다. 병합된 컷이면 파트도 경계에서 나뉩니다. 컷 정보가 없으면(XML 없이 편집) 영상 전체를 한 컷으로 보고 나눕니다. 컷 병합/나누기 메뉴의 활성화 판단은 `merge_error`/`split_error` 로 실제 동작과 같은 규칙을 씁니다.
20. **타임라인에는 전체 길이 썸네일 줄이 없습니다.** 컷 썸네일은 컷 스트립이 보여 줍니다. 그래서 `ThumbService` 는 거친 격자를 미리 만들지 않고(`coarse=False`), 필요한 화면(컷 스트립/타임라인 컷 레인/FEED 목록)이 `request_visible`/`ensure` 로 직접 요청합니다. `request_visible` 은 우선 큐를 통째로 바꾸므로 서비스 인스턴스를 용도별로 나눕니다.
21. **소리 경로** (`ProxyBuilder.build_audio` → `AudioPlayer`): ① 재생용 WAV 는 영상 프록시보다 먼저 만들고 `audioReady` 시그널로 즉시 연결합니다. ② 프록시가 이미 캐시에 있어도 `ProxyBuilder` 는 항상 시작합니다(없는 것만 만들고, 소리 연결과 프록시 색인 로드를 같은 경로로 처리). 예전에는 이 경로에서 소리 연결이 빠져 두 번째 열기부터 무음이었습니다. ③ `AudioPlayer` 는 재생할 때마다 그 시점의 기본 출력 장치로 싱크를 새로 만듭니다(헤드셋을 나중에 연결해도 소리가 남). ④ 소리를 못 내면 조용히 넘어가지 않고 `error` 에 사유를 남겨 화면(재생 바 옆 문구, 재생 시작 시 토스트)에 알립니다. ⑤ 소리 끄기는 미리보기 재생에만 적용되며 렌더링 결과에는 영향이 없습니다(설정 `audio_muted` 로 저장). ⑥ 장치 조회는 `default_audio_output()` 한 곳으로 모아 테스트가 실제 오디오 시스템에 닿지 않게 합니다.

## 6. 스레딩 모델

UI 스레드는 화면과 명령만 처리합니다. 별도 스레드: 프레임 디코딩(요청 합치기 + LRU), 썸네일, 재생용 파이프, 프록시 생성, 자동저장, 렌더 대기열. 스레드에서 UI 로 가는 통신은 Qt 시그널(큐드 연결)만 씁니다.

## 7. 테스트 전략

`tests/helpers.py` 가 **각 프레임에 24비트 프레임 번호 코드와 흰 테두리를 새긴 테스트 영상**(ProRes/H.264/VFR/태그 없음/Full range/12비트/HDR 태그)을 코드로 생성합니다. 렌더 결과를 다시 읽어 프레임 번호, 크롭 여부, 여백, 색 오차, 오디오 클릭 위치(싱크)를 검사합니다.
UI 는 오프스크린 Qt 로 핵심 동작(`tests/test_ui_smoke.py`)만 검증합니다.

## 8. 알려진 한계

`README.md` 의 "제약, 대안, 검증 방법" 표와 `docs/REVIEW_BRIEF.md` 를 참고하세요.

# Instagram Content Studio v1.0

전문 포스트 프로덕션 마스터링 영상(SDR, MP4/MOV) 1개를 인스타그램 게시용 콘텐츠로 변환하는 Windows 10/11 데스크톱 프로그램입니다.
(`Instagram_Content_Studio v2.0 개발 지시서` 기준 구현)

| 출력 | 규격 |
|---|---|
| STORY | 1개, 9:16 (1080×1920 기준), 60초 이내, MP4 |
| REELS | 1개, 9:16, 180초 이내, MP4 + 커버 JPG (임의 프레임 선택) |
| FEED | 스틸 JPG + 영상 MP4(60초 이내). 기본은 **원본 비율**(여백/크롭 없음, 폭 1080 이하), 옵션으로 4:5(1080×1350). 등록은 무제한, 최종 출력은 20개 이하 |

자동 게시나 인스타그램 로그인은 없습니다. 파일을 만들어 주고, 업로드는 사용자가 직접 합니다.

---

## 1. 실행 방법

### 개발 환경에서 실행 (Windows / Linux / macOS)

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
# FFmpeg: vendor/ffmpeg/ffmpeg.exe, ffprobe.exe 를 넣거나 PATH 에 있는 ffmpeg 를 사용
python run.py
```

FFmpeg 위치 탐색 순서: 환경변수 `ICS_FFMPEG` / `ICS_FFPROBE` → `vendor/ffmpeg/` → PATH.
libx264 와 AAC 인코더가 들어 있는 빌드가 필요합니다 (`vendor/ffmpeg/README.txt` 참고).

### Windows 설치 파일 만들기

**방법 A. GitHub Actions (권장, Windows PC 없이도 가능)**

저장소의 Actions 탭에서 `Windows build` 를 실행하면(`workflow_dispatch`, 또는 `v*` 태그 푸시) Windows 러너에서 아래를 자동으로 수행합니다.

1. FFmpeg(BtbN GPL shared 빌드) 내려받기 → `vendor/ffmpeg` 에 배치
2. Windows 에서 pytest 전체 실행 (실패하면 중단)
3. PyInstaller 빌드 → **빌드된 exe 를 `--selftest` 로 점검** (번들 FFmpeg, Qt 플러그인, 영상 생성 → 분석 → 렌더 → 편집 창, 출력 색 태그)
4. exe 를 실제로 실행해 8초간 살아있는지 확인하고 화면 캡처를 남김
5. Inno Setup 설치 파일 생성
6. artifact `InstagramContentStudio_Windows` (설치 파일 `InstagramContentStudio_Setup.exe`, 포터블 `InstagramContentStudio_portable.zip`)와 `windows-reports` (테스트 로그, 점검 결과, 화면 캡처) 업로드

**방법 B. Windows PC 에서 직접**

`build_windows.bat` 를 실행합니다. 테스트 → PyInstaller 빌드 → (Inno Setup 6 이 있으면) 설치 파일 생성까지 진행합니다.

- 결과물: `dist\InstagramContentStudio\InstagramContentStudio.exe`
- 설치 파일: `installer\Output\InstagramContentStudio_Setup.exe`

배포본 점검만 따로 하려면: `InstagramContentStudio.exe --selftest 결과.txt` (창 없이 결과 파일을 쓰고, 통과 시 종료 코드 0).

### 테스트와 코드 품질

```bash
pip install -r requirements-dev.txt          # Windows 설치 파일 빌드는 requirements-build.txt
python -m pytest tests                       # 약 130개 테스트, 1~2분 (실제 인코딩 포함)
ruff check .                                 # 린트
black --check .                              # 포맷 (줄 길이 120, 고치려면 black .)
```

화면 없는 서버에서는 `QT_QPA_PLATFORM=offscreen` 이 필요합니다. 선택 환경 변수는 `.env.example` 에 정리되어 있습니다.

---

## 2. 사용 흐름

1. **프로젝트 목록**에서 새 프로젝트를 만들고 마스터링 파일을 선택합니다. 분석 결과(길이, 해상도, 코덱, 비트레이트, FPS/VFR, 비트 심도, 픽셀 종횡비, 색 정보, 오디오)가 표시됩니다. HDR 은 차단됩니다.
2. **STORY / REELS** 탭: 재생헤드를 시작 지점에 두고 15/30/60/180초/최대 버튼을 누르거나, I / O 와 TC 입력으로 구간을 지정합니다. Fit(기본, 원본 전체 + #000000 여백) 또는 Fill(크롭, 확대/위치) 을 고릅니다. REELS 는 커버 프레임을 지정하고 9:16 커버와 프로필 그리드 크롭을 미리 봅니다.
3. **FEED** 탭: `S` 로 스틸, I / O 후 `V` 로 영상을 등록합니다. 관리 창에서 포함/제외를 고르면 원본 시간순(같은 시점은 스틸이 먼저)으로 번호가 붙습니다. 캐러셀 미리보기에서 좌우로 넘겨 확인합니다. 겹침은 경고만 하고 자동 삭제하지 않습니다.
4. **CREDITS** 탭: 크레딧을 붙여 넣고 정규화(`ROLE | NAME (@id)`)합니다. 불확실한 줄은 검토 대상으로 남고 원문이 보존됩니다. 캡션 본문과 글자 수, 캡션/댓글 분리를 확인하고 복사합니다.
5. **렌더링…**: 파일 목록, 개수, 출력 경로, 예상 용량, 충돌을 확인한 뒤 실행합니다. 파일별 상태/진행률/남은 시간이 표시되고, 실패한 항목만 재시도할 수 있습니다.
6. **업로드 준비…**: 파일 열기/폴더 열기, 캡션·댓글 복사, 수동 "업로드 완료" 체크.

타임라인: XML 불러오기(Premiere XML / FCP7 xmeml / FCPXML / EDL) → 컷 클릭, Shift+클릭으로 연속 선택 → **오른쪽 클릭 메뉴**(병합 / 병합 해제 / 현재 위치에서 컷 나누기 / In·Out 지정 / FEED 영상 추가) 또는 툴바·단축키로 병합/해제/원래 컷 복원 (컷 선택은 위쪽 컷 스트립에서 합니다. 타임라인에는 컷 레인이 없고, 눈금자를 클릭/드래그하면 재생 헤드가 움직이며 컷 스트립이 그 컷을 가운데로 보여 줍니다). Fit 에서도 확대(줌)할 수 있습니다.

- **컷 나누기 (C)**: 재생 헤드 위치에서 컷을 둘로 나눕니다. 재생 헤드 프레임이 뒤쪽 컷의 첫 프레임이 됩니다. XML 없이도 쓸 수 있고(전체를 한 컷으로 보고 나눔), Undo 로 되돌릴 수 있습니다.
- **소리 끄기 (Shift+M)**: 재생 바의 '🔊 소리 켜짐' 버튼. 미리보기 재생 소리만 끄며 렌더링 결과에는 영향이 없습니다. 선택은 저장됩니다. 소리를 못 내는 경우(출력 장치 없음 등)에는 재생 바 옆에 사유가 표시됩니다.
XML 없이도 프레임 단위 편집이 가능합니다.

### 기본 단축키 (상단 '단축키' 버튼에서 변경 가능)

| 동작 | 키 | 동작 | 키 |
|---|---|---|---|
| 재생/정지 | Space | 선택 구간 반복 재생 | Shift+Space |
| 역재생 / 정지 / 정재생 | J / K / L | 1프레임 뒤/앞 | ← / → |
| 10프레임 뒤/앞 | Shift+← / Shift+→ | 처음/끝 | Home / End |
| 이전/다음 컷 | ↑ / ↓ | In / Out 지정 | I / O |
| 실행 취소 / 다시 실행 | Ctrl+Z / Ctrl+Shift+Z (Ctrl+Y) | 타임라인 확대/축소/전체 | = / - / Shift+Z |
| 피드 스틸 / 영상 추가 | S / V | 선택 컷 병합 | M |
| 소스/출력 미리보기 | Tab | 스냅 | N |
| 현재 위치에서 컷 나누기 | C | 소리 끄기/켜기 (미리보기만) | Shift+M |
| 탭 이동 | Ctrl+1 ~ 4 | | |

**Out 은 현재 프레임을 포함합니다** (화면에 "Out(포함)" 으로 표시). 내부적으로는 `[In, Out+1)` 구간입니다.

---

## 3. 출력 폴더

```
PROJECT_NAME/
  STORY/            PROJECT_NAME_STORY_01.mp4
  REELS/            PROJECT_NAME_REELS_01.mp4, PROJECT_NAME_REELS_COVER.jpg
  FEED/             01_STILL.jpg, 02_VIDEO.mp4, ...        (최종 캐러셀 순서 번호)
  CAPTION/          CAPTION.txt, COMMENT_01.txt, ..., CREDITS_ORIGINAL.txt
  ORIGINAL_STILLS/  CUT_001_FRAME_000123.tiff              (원본 해상도, 8비트는 PNG, 10/12비트는 16비트 TIFF)
  PROJECT_DATA/     PROJECT_SETTINGS.json, RENDER_LOG.json, XML_EDITS.json
```

- 폴더/파일명의 공백과 `&` 는 `_` 로 바뀌고 Windows 금지 문자는 안전하게 치환됩니다.
- XML 이 없으면 전체를 `CUT_001` 한 개로 취급합니다. 컷이 있는데 어느 컷에도 속하지 않는 프레임은 `CUT_000` 입니다.
- 모든 파일은 `.part` 임시 파일로 만든 뒤 검증(프레임 수·길이·오디오·재생 가능 여부)하고 원자적으로 이름을 바꿉니다. 취소/오류/강제 종료로 불완전 파일이 남지 않으며, 남은 `.part` 는 다음 렌더링 때 정리됩니다.

---

## 4. 화질·정확도 설계

- **프레임 정확도**: ffprobe 패킷 PTS 로 프레임 인덱스를 만들고(CFR 은 유리수 연산, VFR 은 PTS 표), 모든 구간을 프레임 번호 기준으로 계산합니다. 인코딩 때 입력 시킹 + `setpts` + `-frames:v` 로 정확히 N 프레임을 냅니다.
- **원본 화질**: 최종 결과물과 원본 해상도 스틸은 항상 마스터링 원본에서 직접 디코딩합니다. 프록시(재생용 MJPEG)는 미리보기에만 쓰며, 코드에서 최종 출력에 프록시를 쓰면 오류가 나도록 막고 테스트로 검증합니다.
- **색**: 색 행렬/범위를 항상 명시해 변환합니다(Rec.709 Limited 출력, 스틸은 Full). 태그가 없으면 mpv/VLC 와 같은 규칙(높이 ≤576 이고 폭 <1280 이면 SD/BT.601, 그 외 BT.709)으로 가정하고 화면에 알립니다.
- **자동 인코딩(설정 UI 없음)**: libx264 slow, CRF 14~15, High 프로필, GOP 2초, aq-mode 3, AAC 256/160k, faststart. 불필요한 업스케일은 하지 않습니다(원본이 작으면 900×1600 / 720×1280 등 낮은 캔버스를 자동 선택하고 알림 표시). VFR 은 가장 가까운 표준 CFR 로, 60fps 초과는 절반으로 맞춥니다. 오디오는 볼륨을 바꾸지 않고 클리핑/싱크만 점검합니다.
- **속도 참고(개발 컨테이너 측정, Windows PC 와 다를 수 있음)**: 4분 1080p H.264(5,755프레임) 분석 0.06초 + 프레임 인덱스 0.29초, 60초 STORY(1080×1920, slow/CRF 15) 인코딩+검증 약 64초(대략 실시간 1배속). 렌더링은 백그라운드 스레드라 UI 가 멈추지 않습니다.
- **자동저장/복구**: 변경 후 0.5초(최대 4초) 안에 백그라운드로 SQLite 에 저장합니다(WAL, 체크섬 스냅샷 30개). 비정상 종료나 손상된 저장본은 가장 최근의 정상 저장본으로 복구하고 알려 줍니다. 마스터링 파일이 이동했으면 재연결을 안내합니다.

---

## 5. 제약, 대안, 검증 방법 (개발 지시서 18항에 따른 명시)

| 항목 | 제약 | 대안 / 현재 처리 | 검증 방법 |
|---|---|---|---|
| Windows 설치 파일 | Linux 개발 환경에서는 `.exe` 를 직접 만들 수 없음 | GitHub Actions Windows 러너에서 빌드. 러너에서 pytest 전체 통과, 빌드된 exe 의 `--selftest` 통과, 실행 후 창 표시(화면 캡처)까지 자동 확인 | 사람이 직접 설치해 아래 체크리스트 수행 (오디오 재생, 실제 XML 등은 자동 검증 불가) |
| FFmpeg 라이선스 | libx264 포함 GPL 빌드를 같이 배포하면 GPL 의무 발생 | `LICENSES/THIRD_PARTY_NOTICES.md` 에 정리. 별도 실행 파일 호출 방식 | 배포 전 법무/라이선스 검토 |
| 캡션 글자 수 | 인스타그램의 실제 계산 방식은 공개 규격이 없고 변경될 수 있음 | 기본은 UTF-16 코드 유닛(가장 보수적)이며 코드포인트/글자 단위 표시와 전환 가능 | 이모지·한글·복합 이모지가 섞인 2200자 근처 캡션을 실제 앱에 붙여 넣어 확인 |
| XML 호환성 | 파서는 공개 포맷 명세와 샘플 기준으로 작성함. 실제 Premiere/FCP 내보내기 파일로는 아직 검증 못 함 | 시퀀스/트랙 선택, 오프셋, TC/길이 불일치 경고, 수동 보정 제공 | 사용 중인 실제 XML 3~5개로 컷 개수·경계 확인 (지시서도 샘플 확보를 요구) |
| 미리보기 오디오 | 개발/CI 환경에 오디오 장치가 없어 `QAudioSink` 로 실제 소리가 나는지는 검증 못 함 (재생용 WAV 생성, 소리 연결 순서, 음소거, 장치 없음 알림은 자동 검증) | 소리를 못 내면 사유를 화면에 표시, 재생 시마다 현재 기본 출력 장치로 새로 연결, `--selftest` 가 WAV 생성과 장치 상태를 점검 | Windows 에서 재생 시 소리/싱크 확인. 안 들리면 재생 바 옆 '⚠ 소리를 낼 수 없음' 에 마우스를 올린 문구를 알려 주세요 |
| 프로필 그리드 비율 | 인스타그램 앱 업데이트에 따라 달라질 수 있음 | 기본 4:5, 3:4 / 1:1 로 변경 가능, 세로 오프셋 조절 | 현재 앱의 프로필 화면과 대조 |
| 색 공간 | BT.2020 SDR 등 비 BT.709 원색은 감마 변환 없이 경고만 함 | HDR(PQ/HLG/Dolby Vision) 은 오류로 차단 | 해당 소스를 쓸 경우 결과 색을 육안 확인 |
| 인스타그램 규격 | 업로드 후 인스타그램이 다시 인코딩함 | 높은 비트레이트(CRF 14~15, maxrate 40~45M)의 원본 품질을 제공 | 실제 업로드 후 화질 확인 |

---

## 6. Windows 실기 검증 체크리스트

1. Actions 의 `Windows build` 가 성공하고(또는 `build_windows.bat` 가 테스트 통과 후) 설치 파일이 만들어진다.
2. 설치 후 실행 → 새 프로젝트 → 4K/1080p MOV(ProRes), H.264 MP4 각각 열기 → 분석 정보 확인.
3. 재생(소리 포함), J/K/L, 프레임 이동, 스크럽 시 화면이 프레임 번호와 일치.
4. 실제 Premiere XML / FCPXML 불러오기 → 컷 수와 경계 확인 → 병합/해제/드래그 보정, 오른쪽 클릭 병합, 재생 헤드에서 컷 나누기(C).
   소리 끄기(Shift+M)와 헤드셋 연결/해제 후 소리 확인.
5. STORY 60초, REELS 180초, FEED 영상 60초 제한 메시지 확인.
6. 렌더링 후 STORY/REELS/FEED 파일을 Premiere/DaVinci 에 올려 원본과 프레임·색·싱크 비교.
7. ORIGINAL_STILLS 의 TIFF/PNG 가 원본 해상도인지, 색이 맞는지 확인.
8. 렌더링 도중 작업 관리자로 앱 강제 종료 → 재실행 → 복구 안내와 `.part` 정리 확인.
9. 한글/공백/`&` 가 들어간 프로젝트명, 네트워크 드라이브 출력 경로 테스트.
10. 캡션 2200자 한도 직전·직후 문구를 인스타그램 앱에서 직접 붙여 넣어 비교.

---

## 7. 문서와 리뷰 방법

| 파일 | 내용 |
|---|---|
| `ARCHITECTURE.md` | 구조, 모듈 역할, 의도적인 설계 결정 |
| `AGENTS.md` | AI 작업 지침: 검증 명령, 규칙, 건드리면 안 되는 부분 |
| `docs/REVIEW_BRIEF.md` | 다른 AI 에게 리뷰를 맡길 때의 요청문, 브랜치/PR 방식, 검증 필요 항목 |
| `.github/workflows/ci.yml` | push/PR 마다 ruff, black, pytest 실행 |

## 8. 코드 구조

```
run.py                     진입점
ics/app.py                 프로젝트 목록 -> 분석/재연결 -> 편집 창
ics/core/                  Qt 비의존 로직 (media, frameindex, timecode, layout, cuts, xml_import,
                           credits, iddict, caption, model, project, store, autosave, ffbin)
ics/render/                filters, engine(인코딩/스틸), planner(렌더 계획), queue(렌더 큐/로그)
ics/ui/                    main_window, timeline, preview, panels, credits_page, dialogs, services, theme, settings
tests/                     pytest (프레임 정확도, 색, 레이아웃, 컷, XML, 크레딧, 캡션, 자동저장/복구, 큐, UI 스모크)
```

앱 데이터 위치: Windows `%APPDATA%\InstagramContentStudio` (프로젝트 DB, ID 사전, 설정). 캐시(썸네일/프록시/프레임 인덱스)는 프로젝트별 캐시 폴더에 저장되며 지워도 다시 만들어집니다.
환경변수 `ICS_DATA_DIR`, `ICS_CACHE_DIR` 로 위치를 바꿀 수 있습니다.

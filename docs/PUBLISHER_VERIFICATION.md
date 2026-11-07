# Publisher 1단계: 공식 지원 검증 보고서

확인일: 2026-11-01. 방법: Meta/Cloudflare 공식 문서 본문을 가져와 요약 도구로 항목을 추출했다. 요약 단계를 거친 값이므로
**구현 시작 시 원문을 다시 대조한다.** 실제 계정 테스트는 하지 않았다 (Meta 앱/계정이 아직 없음).

## 1. 확인된 사실 (Meta)

| 항목 | 결과 | 출처 |
|---|---|---|
| 예약 게시 파라미터 | **문서에 없음.** 예약 기능을 만드는 앱은 게시 한도를 앱이 직접 지키라고 안내한다. 예약은 앱/서버가 시간이 될 때 게시 API를 호출하는 방식으로 구현한다 | Content Publishing 문서 (두 페이지 공통) |
| 게시 흐름 | `POST /{ig_id}/media` 컨테이너 생성 -> `GET /{container}?fields=status_code` -> `POST /{ig_id}/media_publish` | 위와 동일 |
| 컨테이너 상태 | IN_PROGRESS / FINISHED / ERROR / EXPIRED / PUBLISHED. 24시간 안에 게시하지 않으면 EXPIRED. 폴링은 분당 1회, 최대 5분 권장 | 위와 동일 |
| 미디어 위치 | Instagram Login 방식은 Meta가 가져갈 수 있는 **공개 HTTPS URL**이 필요하다. 이어올리기(resumable) 업로드는 Facebook Login for Business 방식 앱에서만 | 위와 동일 |
| 이미지 | JPEG만 (MPO/JPS 불가) | 위와 동일 |
| 릴스 | `media_type=REELS` + `video_url`. 길이/코덱/용량 상세는 해당 페이지에 없음 | Instagram Login 게시 문서 |
| 스토리 | `media_type=STORIES`. 이미지/영상 지원 범위와 Creator 계정 지원은 해당 페이지에 명시 없음 | 위와 동일 |
| 권한 | instagram_business_basic, instagram_business_content_publish (Instagram Login) | 위와 동일 |
| 한도 조회 | `GET /{ig_id}/content_publishing_limit` | 위와 동일 |

## 2. 문서끼리 충돌하는 항목 (그래서 확정하지 않음)

- **캐러셀 최대 개수**: 일반 Instagram Platform 게시 문서는 **10개**, Instagram Login 게시 문서는 **20개**.
- **24시간 게시 한도**: 한 문서 안에서도 **100건**과 **50건(캐러셀 절)** 이 함께 나온다.

지시서 규칙에 따라 이 값들을 추정해서 쓰지 않는다. CapabilityProfile 에서 캐러셀 한도는 UNKNOWN 으로 두고,
**캐러셀 자동 게시는 실제 계정에서 한도를 확인할 때까지 비활성화**한다. 비활성 상태에서는 수동 내보내기(01, 02 순서 사본 + 캡션)로 대체한다.
한도는 런타임에 `content_publishing_limit` 로 읽을 수 있는 값을 우선한다.

## 3. 확인하지 못한 항목 (UNKNOWN 으로 시작, 실제 테스트 후 활성화)

- Creator 계정의 스토리 게시 지원 (Instagram Login 기준)
- PKCE 지원 여부와 App Secret 요구 여부
- 릴스 영상 규격 (길이, 비율, 코덱, 용량), 커버 지정 필드, 공유 옵션
- 댓글 생성에 필요한 권한 (instagram_business_manage_comments 가 필요하다고 가정하지 않음)
- 앱 모드(개발/라이브)와 App Review 요건, 테스트 계정으로 외부 사용자 없이 쓸 수 있는 범위
- 단일 영상 피드의 실제 게시 유형 (REELS 로 처리되는지)

## 4. 예약을 서버 비용 없이 하는 방법 (Cloudflare 무료 티어)

예약은 API가 지원하지 않으므로 **시간이 됐을 때 대신 호출해 줄 곳**이 필요하다. 사용자의 요청(서버 비용 없이)에 맞춰 Cloudflare 무료 티어로 구성한다.

| 항목 | 무료 한도 (공식 문서) | 이 구성에서의 의미 |
|---|---|---|
| Workers 요청 | 하루 100,000건, 요청/크론당 CPU 10ms, 외부 호출 50건 | 게시 호출/폴링은 대기 시간이라 CPU 로 세지 않는다. 충분 |
| Cron Triggers | 계정당 5개 | 1분 주기 1개로 예약 실행/상태 폴링 |
| R2 | 10GB-월, Class A 100만, Class B 1000만, 송신 무료 | 임시 HTTPS 영상 보관 (게시 확인 후 삭제) |
| D1 | 하루 읽기 500만 행, 쓰기 10만 행, 저장 5GB | 예약 작업/상태 저장 |

한계와 주의:
- 무료 한도는 변할 수 있고, 한도를 넘으면 요청이 막힌다. 앱이 한도 오류를 이력에 남기고 알린다.
- Workers 무료 요청 본문은 크기 제한이 있을 수 있어 **영상은 Worker 를 거치지 않고 R2 서명 URL 로 앱이 직접 올린다** (구현 시 한도 원문 재확인).
- Cloudflare 계정 가입(결제수단 없이 무료 가능한지 포함)과 R2 활성화는 사용자가 직접 해야 한다. 이 환경에서 대신 배포할 수 없다.
- 토큰은 Worker 비밀 저장소/암호화된 D1 에만 두고 앱 패키지에 App Secret 을 넣지 않는다.

## 5. 기능 프로필 초기값

| 기능 | 초기 상태 | 활성화 조건 |
|---|---|---|
| 릴스 게시 | UNKNOWN | 실제 1회 테스트 성공 |
| 단일 이미지 피드 | UNKNOWN | 실제 1회 테스트 성공 |
| 캐러셀 자동 게시 | UNSUPPORTED(비활성) | 검증된 한도까지 실제 게시 성공 |
| 스토리 | UNKNOWN (조건부) | 해당 계정 유형 공식 지원 + 실제 테스트 |
| 댓글 | UNKNOWN | 권한 확인 + 실제 테스트 |
| 예약 게시(자체 구현) | 모의 모드에서만 | 서버(Worker) 배포 + 실제 테스트 |
| 수동 내보내기 | VERIFIED(앱 자체 기능) | 항상 가능 |

## 6. 참고한 공식 자료

- Instagram Platform Content Publishing: https://developers.facebook.com/docs/instagram-platform/content-publishing
- Instagram API with Instagram Login, Content Publishing: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing
- Cloudflare Workers limits: https://developers.cloudflare.com/workers/platform/limits/
- Cloudflare R2 pricing: https://developers.cloudflare.com/r2/pricing/
- Cloudflare D1 pricing: https://developers.cloudflare.com/d1/platform/pricing/

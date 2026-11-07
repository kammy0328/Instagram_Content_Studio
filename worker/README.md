# Instagram Publisher 중계 (Cloudflare Worker, 무료 티어)

Instagram API 에는 예약 게시가 없으므로, **시간이 되면 대신 게시 API 를 호출**하는 중계입니다. PC 가 꺼져 있어도 됩니다.
Worker + Cron(1분) + R2(임시 영상) + D1(작업 상태)만 쓰며 서버 비용이 없습니다 (무료 한도 안에서. 한도는 Cloudflare 가 바꿀 수 있습니다).

## 처음 한 번 설정 (직접 해야 하는 부분)
1. Cloudflare 계정 가입 (무료). 대시보드에서 R2 를 활성화한다 (결제 수단이 필요한지는 Cloudflare 안내를 따른다).
2. 이 폴더에서 `npm install` 후 `npx wrangler login`.
3. `npx wrangler d1 create ics-publisher` -> 출력된 `database_id` 를 `wrangler.toml` 에 넣는다.
4. `npx wrangler d1 execute ics-publisher --remote --file=schema.sql`
5. `npx wrangler r2 bucket create ics-publisher-media`
6. 비밀값 3개 (저장소/파일에 쓰지 말 것):
   - `npx wrangler secret put RELAY_API_KEY`  (긴 무작위 문자열. 같은 값을 앱 설정에 입력)
   - `npx wrangler secret put TOKEN_KEY`  (`openssl rand -base64 32`)
   - `npx wrangler secret put MEDIA_SIGNING_KEY`  (긴 무작위 문자열)
7. `npx wrangler deploy` -> 나온 주소를 `wrangler.toml` 의 `PUBLIC_BASE` 에 넣고 다시 deploy.

## 보안
- 앱 <-> 중계는 `Authorization: Bearer RELAY_API_KEY`. 토큰은 AES-GCM 으로 암호화해 D1 에 저장하고 어떤 응답/로그에도 되돌려 주지 않는다.
- Meta 가 가져가는 영상은 만료 시각이 있는 HMAC 서명 URL(`/m/...`)만 열린다. 버킷은 비공개.
- 로그/이력은 토큰, 서명, 인증 코드를 마스킹한다.

## 동작
`POST /v1/media` (R2 멀티파트 업로드) -> `POST /v1/jobs` (승인 스냅샷 해시 + 예약 시각 + 고정된 payload) -> 1분 주기로 컨테이너 생성 -> 처리 확인 -> 게시 -> (선택) 첫 댓글 -> 유예 후 임시 영상 정리.
- 게시 응답이 유실되면 `RECONCILIATION_REQUIRED` 로 멈추고 자동 재게시하지 않는다. 앱에서 확인 후 `/resolve` 로만 해소한다.
- 예약 시각에서 `MISSED_GRACE_MIN` 분이 넘으면 `MISSED` 로 두고 자동 게시하지 않는다 (`/run-now` 로 사용자가 결정).
- OAuth 토큰 교환은 공식 문서 확인 전이라 **구현하지 않았다**. `/oauth/callback` 은 코드를 5분짜리 일회용 인계로만 보관한다.

## 테스트
`npm test` (Node 내장 테스트 러너, 가짜 D1/R2/Graph 로 실행. 실제 Cloudflare/Meta 호출 없음)

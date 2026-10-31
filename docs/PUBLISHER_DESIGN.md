# Instagram Publisher 설계 (v1.1 지시서 기준, 진행 상태 포함)

## 요구 (사용자 추가 요청 반영)
- 스토리/릴스/피드를 **항상 3개 모두가 아니라 임의 조합(1개, 2개, 3개)** 으로 선택해 게시한다. 선택하지 않은 유형은 작업조차 만들지 않는다.
- **예약 게시**를 지원한다. 단, 공식 문서에 예약 파라미터가 없으므로(검증 보고서 참조) 시간이 되면 게시 API 를 호출하는 방식으로 구현한다.
- 서버 비용 없이 PC 가 꺼져 있어도 되도록 **Cloudflare 무료 티어**(Worker + Cron + R2 + D1)를 중계로 쓴다.

## 구조
```
기존 앱(변경 없음) -> 출력 폴더(STORY/REELS/FEED/CAPTION)
                           | 읽기 전용
                   ics/publisher/adapter.py
                           |
 UI(예정) -> engine.PublishEngine -> transport(모의 | 실제) -> graph.instagram.com
                |       |                 
          store.py   scheduler.py (PC 예약)  /  Cloudflare Worker (클라우드 예약, 예정)
```
- `capability.py`: 기능별 UNKNOWN/VERIFIED/UNSUPPORTED. 확인되지 않은 기능은 꺼지고 수동 내보내기로 안내. 계정/API 버전/권한이 바뀌면 VERIFIED 를 UNKNOWN 으로 되돌린다.
- `models.py`: 작업 상태기계(DRAFT ... PUBLISHED, RETRY_WAIT, AUTH_REQUIRED, RECONCILIATION_REQUIRED, MISSED, MANUAL_REQUIRED ...), 승인 스냅샷 해시, 멱등 키, 변경 사유 비교.
- `store.py`: 프로젝트 DB 와 별개의 SQLite. 멱등 키는 부분 UNIQUE 인덱스로 보장, 상태 전이는 트랜잭션.
- `engine.py`: 조합 선택/사전 점검/승인/업로드/컨테이너/폴링/게시/결과 불명/댓글 분리/정리/취소.
- `transport.py`: `MockTransport`(결함 주입) 와 `GraphTransport`(live 승인 없이는 생성 불가, API 버전 필수).
- `scheduler.py`: PC 예약 실행. 늦게 켜지면 자동 게시하지 않고 MISSED 로 두어 사용자가 확인.

## 핵심 규칙 (테스트로 고정)
1. 승인 없이는 어떤 외부 호출도 하지 않는다. 승인 후 파일/텍스트/계정/예약 시각이 바뀌면 승인이 무효화된다.
2. 게시 응답이 유실되면 `RECONCILIATION_REQUIRED` 로 가며 **자동 재게시하지 않는다**(상태기계에 경로 자체가 없음). 컨테이너 상태 `PUBLISHED` 로만 자동 확정하고 캡션/시각만으로는 확정하지 않는다.
3. 일시 오류만 제한 횟수 재시도, 인증 오류는 재연결 안내, 규격 오류는 자동 재시도 없음. 호출 제한은 대기.
4. 일괄 정책: 기본 `stop`(한 건 실패 시 나머지 시작 안 함), `continue` 선택 가능. 성공한 게시는 자동 삭제하지 않는다.
5. 댓글은 게시 성공과 분리된 작업. 실패해도 게시는 유지, 재시도 전에 기존 댓글을 확인한다.
6. 스토리에는 피드 캡션/댓글을 적용하지 않는다.
7. 캐러셀은 검증된 한도가 있을 때만, 그 한도 안에서만. 자동으로 나누지 않는다.
8. 로그에 토큰/서명 URL 비밀값을 남기지 않는다(마스킹).

## 진행 상태
| 단계 | 상태 |
|---|---|
| 1. 공식 지원 검증 | 보고서 작성 (실제 계정 테스트 전) |
| 2. 기반 + 모의 API | 코어 완료 (테스트 포함). 계정 연결/토큰 저장은 UI 와 함께 |
| 3. 릴스/단일 이미지 | 엔진 구현 + 모의 검증. **실제 게시 테스트는 Meta 앱/승인 후** |
| 4. 혼합 캐러셀/수동 내보내기 | 캐러셀 엔진 구현(비활성 기본). 수동 내보내기 예정 |
| 5. 스토리/댓글/개선 UI | 엔진 구현. UI 예정 |
| 6. Windows 배포 | 예정 |
| 클라우드 예약(Cloudflare) | 설계만. Worker 구현 예정 |

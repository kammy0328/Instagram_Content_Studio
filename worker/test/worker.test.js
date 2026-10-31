import assert from "node:assert/strict";
import { test } from "node:test";
import { decryptToken, encryptToken, mask, signMedia, verifyMedia } from "../src/crypto.js";
import { handle } from "../src/index.js";
import { stepComment, stepJob, tick } from "../src/jobs.js";
import { addJob, fakeGraph, getJob, makeEnv } from "./fakes.js";

const ok = (body) => ({ status: 200, body });
const err = (status, code, message = "x") => ({ status, body: { error: { code, message } } });
const happy = (url, init) => {
  if (url.includes("/media_publish")) return ok({ id: "M1" });
  if (url.includes("/media")) return ok({ id: "C1" });
  if (url.includes("content_publishing_limit")) return ok({ data: [{ quota_usage: 1, config: { quota_total: 50 } }] });
  if (url.includes("/comments") && init.method === "GET") return ok({ data: [] });
  if (url.includes("/comments")) return ok({ id: "CM1" });
  return ok({ status_code: "FINISHED" });
};
const req = (path, { method = "GET", body, headers = {} } = {}) =>
  new Request(`https://relay.test${path}`, { method, body: body == null ? undefined : (typeof body === "string" || body instanceof Uint8Array ? body : JSON.stringify(body)), headers });
const auth = { authorization: "Bearer relay-secret-key" };

test("crypto: 토큰 암복호화, 서명 검증, 마스킹", async () => {
  const env = await makeEnv();
  const e = await encryptToken(env.TOKEN_KEY, "IGQVJ_ABCDEFGHIJKLMNOPQRSTUV");
  assert.ok(!e.includes("IGQVJ"));
  assert.equal(await decryptToken(env.TOKEN_KEY, e), "IGQVJ_ABCDEFGHIJKLMNOPQRSTUV");
  const sig = await signMedia("k", "a.mp4", 5000);
  assert.ok(await verifyMedia("k", "a.mp4", 5000, sig, 1000));
  assert.ok(!(await verifyMedia("k", "a.mp4", 5000, sig, 6000)), "만료");
  assert.ok(!(await verifyMedia("k", "b.mp4", 5000, sig, 1000)), "키 변조");
  assert.ok(!(await verifyMedia("other", "a.mp4", 5000, sig, 1000)), "비밀값 다름");
  assert.ok(!mask("Bearer abc.def access_token=ZZZ sig=Q-w_1").match(/abc|ZZZ|Q-w_1/));
});

test("인증: /v1 은 Bearer 없이 401, 잘못된 키 401", async () => {
  const env = await makeEnv();
  assert.equal((await handle(req("/v1/jobs"), env)).status, 401);
  assert.equal((await handle(req("/v1/jobs", { headers: { authorization: "Bearer nope" } }), env)).status, 401);
  assert.equal((await handle(req("/v1/jobs", { headers: auth }), env)).status, 200);
});

test("미디어: 서명 없으면 403, 서명 있으면 Range 206, HEAD 길이", async () => {
  const env = await makeEnv();
  await env.MEDIA.put("v1", new Uint8Array([0, 1, 2, 3, 4, 5]), { httpMetadata: { contentType: "video/mp4" } });
  const exp = 10 ** 13, sig = await signMedia(env.MEDIA_SIGNING_KEY, "v1", exp);
  assert.equal((await handle(req("/m/v1"), env)).status, 403);
  assert.equal((await handle(req(`/m/v1?exp=${exp}&sig=bad`), env)).status, 403);
  const r = await handle(req(`/m/v1?exp=${exp}&sig=${sig}`, { headers: { range: "bytes=2-4" } }), env);
  assert.equal(r.status, 206);
  assert.equal(r.headers.get("content-range"), "bytes 2-4/6");
  assert.deepEqual([...new Uint8Array(await r.arrayBuffer())], [2, 3, 4]);
  const h = await handle(req(`/m/v1?exp=${exp}&sig=${sig}`, { method: "HEAD" }), env);
  assert.equal(h.headers.get("content-length"), "6");
  assert.equal((await handle(req(`/m/v1?exp=1&sig=${sig}`), env, 5000)).status, 403);
});

test("업로드(멀티파트) 후 작업 등록: 멱등, 미업로드/크기 불일치 거부", async () => {
  const env = await makeEnv();
  const c = await (await handle(req("/v1/media", { method: "POST", headers: auth, body: { id: "job-a-video", content_type: "video/mp4" } }), env)).json();
  const parts = [];
  for (const [i, bytes] of [[1, new Uint8Array([1, 2])], [2, new Uint8Array([3])]]) {
    const r = await handle(req(`/v1/media/job-a-video/parts/${i}?upload_id=${c.upload_id}`, { method: "PUT", headers: auth, body: bytes }), env);
    parts.push(await r.json());
  }
  await handle(req("/v1/media/job-a-video/complete", { method: "POST", headers: auth, body: { upload_id: c.upload_id, parts } }), env);
  const job = { id: "job-a", ig_id: "111", approval_hash: "hh", run_at: 5000, payload: { kind: "reels", api_version: "v99.0", media: [{ key: "job-a-video", role: "video", size: 3 }] } };
  assert.equal((await handle(req("/v1/jobs", { method: "POST", headers: auth, body: job }), env)).status, 201);
  const again = await (await handle(req("/v1/jobs", { method: "POST", headers: auth, body: job }), env)).json();
  assert.equal(again.duplicate, true);
  const n = (await env.DB.prepare("SELECT COUNT(*) c FROM jobs").first()).c;
  assert.equal(n, 1);
  const missing = { ...job, id: "job-b", payload: { ...job.payload, media: [{ key: "nope", role: "video" }] } };
  assert.equal((await handle(req("/v1/jobs", { method: "POST", headers: auth, body: missing }), env)).status, 409);
  const wrong = { ...job, id: "job-c", payload: { ...job.payload, media: [{ key: "job-a-video", role: "video", size: 99 }] } };
  assert.equal((await handle(req("/v1/jobs", { method: "POST", headers: auth, body: wrong }), env)).status, 409);
});

test("계정 토큰: 암호화 저장, 응답/작업 조회에 노출 없음", async () => {
  const env = await makeEnv();
  await handle(req("/v1/accounts/222/token", { method: "PUT", headers: auth, body: { token: "IGQVJ_PLAINTEXT_TOKEN_0123456789", username: "u" } }), env);
  const row = await env.DB.prepare("SELECT token_enc FROM accounts WHERE ig_id='222'").first();
  assert.ok(row.token_enc && !row.token_enc.includes("PLAINTEXT"));
  await addJob(env);
  const body = await (await handle(req("/v1/jobs/job1", { headers: auth }), env)).text();
  assert.ok(!body.includes("IGQVJ") && !body.includes("token"));
});

test("정상 흐름: 예약 -> 컨테이너 -> 처리 -> 게시", async () => {
  const env = await makeEnv(); await addJob(env, { runAt: 1000 });
  const f = fakeGraph(happy);
  assert.equal(await stepJob(env, await getJob(env), 500, f), "SCHEDULED");
  assert.equal(f.calls.length, 0, "시각 전에는 호출 없음");
  assert.equal(await stepJob(env, await getJob(env), 1000, f), "PROCESSING");
  const create = f.calls[0];
  assert.match(create.url, /graph\.test\/v99\.0\/111\/media$/);
  assert.match(create.body, /media_type=REELS/);
  assert.match(create.body, /video_url=https%3A%2F%2Frelay\.test%2Fm%2F/);
  assert.equal(await stepJob(env, await getJob(env), 1000 + 10, f), "PROCESSING", "폴링 간격 전");
  assert.equal(await stepJob(env, await getJob(env), 70000, f), "PUBLISHED", "처리 완료 확인 직후 같은 주기에 게시");
  assert.deepEqual(f.calls.map((c) => c.url.split("/").pop().split("?")[0]).filter((x) => x === "media_publish").length, 1);
  const j = await getJob(env);
  assert.equal(j.media_id, "M1");
  assert.equal(j.error, null);
});

test("놓친 예약: 유예 초과면 MISSED, 자동 게시 안 함, run-now 로만 재개", async () => {
  const env = await makeEnv(); await addJob(env, { runAt: 1000 });
  const f = fakeGraph(happy);
  assert.equal(await stepJob(env, await getJob(env), 1000 + 11 * 60000, f), "MISSED");
  assert.equal(f.calls.length, 0);
  assert.deepEqual(await tick(env, 1000 + 12 * 60000, f), {}, "MISSED 는 tick 이 건드리지 않음");
  const r = await handle(req("/v1/jobs/job1/run-now", { method: "POST", headers: auth }), env, 2_000_000);
  assert.equal(r.status, 200);
  assert.equal((await getJob(env)).state, "SCHEDULED");
});

test("게시 응답 유실(타임아웃): RECONCILIATION_REQUIRED, 재게시 없음", async () => {
  const env = await makeEnv(); await addJob(env);
  let publishCalls = 0;
  const f = fakeGraph((url, init) => {
    if (url.includes("/media_publish")) { publishCalls++; const e = new Error("t"); e.name = "TimeoutError"; throw e; }
    return happy(url, init);
  });
  let now = 1000;
  for (let i = 0; i < 6; i++) { await tick(env, now, f); now += 70000; }
  assert.equal((await getJob(env)).state, "RECONCILIATION_REQUIRED");
  assert.equal(publishCalls, 1);
  for (let i = 0; i < 3; i++) { await tick(env, now, f); now += 70000; }
  assert.equal(publishCalls, 1, "자동 재게시 금지");
  // 사용자가 게시되지 않았음을 확인 -> FAILED (새 승인 필요)
  const r = await handle(req("/v1/jobs/job1/resolve", { method: "POST", headers: auth, body: {} }), env, now);
  assert.equal((await r.json()).state, "FAILED");
});

test("PUBLISHING 중 프로세스 중단 흔적이 있으면 결과 불명으로 멈춤", async () => {
  const env = await makeEnv(); await addJob(env);
  await env.DB.prepare("UPDATE jobs SET state='PUBLISHING', container_id='C1' WHERE id='job1'").run();
  const f = fakeGraph(happy);
  assert.equal(await stepJob(env, await getJob(env), 5000, f), "RECONCILIATION_REQUIRED");
  assert.equal(f.calls.length, 0);
});

test("사용자 해소: 게시물 ID 를 알려주면 PUBLISHED", async () => {
  const env = await makeEnv(); await addJob(env);
  await env.DB.prepare("UPDATE jobs SET state='RECONCILIATION_REQUIRED' WHERE id='job1'").run();
  const r = await handle(req("/v1/jobs/job1/resolve", { method: "POST", headers: auth, body: { media_id: "M9" } }), env, 9000);
  assert.equal((await r.json()).state, "PUBLISHED");
});

test("일시 오류는 제한 횟수 재시도 후 FAILED, 규격 오류는 즉시 FAILED, 인증 오류는 AUTH_REQUIRED", async () => {
  let env = await makeEnv(); await addJob(env);
  let f = fakeGraph((url, init) => (url.endsWith("/111/media") ? err(500, 2, "boom") : happy(url, init)));
  let now = 1000; const seen = [];
  for (let i = 0; i < 8; i++) { seen.push(await stepJob(env, await getJob(env), now, f)); now += 200000; }
  assert.equal((await getJob(env)).state, "FAILED");
  assert.equal(f.calls.filter((c) => c.url.endsWith("/111/media")).length, 4, "최초 1회 + 재시도 3회");
  assert.ok(seen.includes("RETRY_WAIT"));

  env = await makeEnv(); await addJob(env);
  f = fakeGraph(() => err(400, 100, "Invalid parameter"));
  assert.equal(await stepJob(env, await getJob(env), 1000, f), "FAILED");
  assert.equal(f.calls.length, 1, "재시도 없음");

  env = await makeEnv(); await addJob(env);
  f = fakeGraph(() => err(400, 190, "token expired access_token=ABCDEF"));
  assert.equal(await stepJob(env, await getJob(env), 1000, f), "AUTH_REQUIRED");
  const att = await env.DB.prepare("SELECT detail FROM attempts WHERE status='auth'").all();
  assert.ok(att.results.every((a) => !a.detail.includes("ABCDEF")), "로그 마스킹");
});

test("계정 토큰이 없으면 AUTH_REQUIRED", async () => {
  const env = await makeEnv(); await addJob(env);
  await env.DB.prepare("DELETE FROM accounts").run();
  assert.equal(await stepJob(env, await getJob(env), 1000, fakeGraph(happy)), "AUTH_REQUIRED");
});

test("게시 한도에 도달하면 게시하지 않고 대기", async () => {
  const env = await makeEnv(); await addJob(env);
  await env.DB.prepare("UPDATE jobs SET state='READY', container_id='C1' WHERE id='job1'").run();
  const f = fakeGraph((url, init) => (url.includes("content_publishing_limit") ? ok({ data: [{ quota_usage: 50, config: { quota_total: 50 } }] }) : happy(url, init)));
  assert.equal(await stepJob(env, await getJob(env), 1000, f), "RETRY_WAIT");
  assert.ok(!f.calls.some((c) => c.url.includes("media_publish")));
});

test("컨테이너 ERROR 는 FAILED, EXPIRED 는 다시 생성", async () => {
  let env = await makeEnv(); await addJob(env);
  await env.DB.prepare("UPDATE jobs SET state='PROCESSING', container_id='C1', next_try_at=0 WHERE id='job1'").run();
  assert.equal(await stepJob(env, await getJob(env), 1000, fakeGraph(() => ok({ status_code: "ERROR" }))), "FAILED");
  env = await makeEnv(); await addJob(env);
  await env.DB.prepare("UPDATE jobs SET state='PROCESSING', container_id='C1', next_try_at=0 WHERE id='job1'").run();
  assert.equal(await stepJob(env, await getJob(env), 1000, fakeGraph(() => ok({ status_code: "EXPIRED" }))), "CREATING");
});

test("스토리: STORIES 컨테이너, 캡션/댓글 없음", async () => {
  const env = await makeEnv();
  await addJob(env, { kind: "story", extra: { caption: undefined }, media: [{ key: "s-img", role: "image", size: 3 }] });
  const f = fakeGraph(happy);
  await stepJob(env, await getJob(env), 1000, f);
  assert.match(f.calls[0].body, /media_type=STORIES/);
  assert.match(f.calls[0].body, /image_url=/);
  assert.ok(!f.calls[0].body.includes("caption"));
});

test("단일 피드 영상: 게시 유형이 확인되지 않으면 호출하지 않고 FAILED", async () => {
  const env = await makeEnv();
  await addJob(env, { kind: "feed_video" });
  const f = fakeGraph(happy);
  assert.equal(await stepJob(env, await getJob(env), 1000, f), "FAILED");
  assert.equal(f.calls.length, 0);
});

test("캐러셀: 자식 컨테이너 순서대로 만들고 부모에 연결", async () => {
  const env = await makeEnv();
  await addJob(env, { kind: "carousel", media: [{ key: "c1", role: "image", size: 3 }, { key: "c2", role: "image", size: 3 }] });
  let n = 0;
  const f = fakeGraph((url, init) => (url.endsWith("/111/media") ? ok({ id: `K${++n}` }) : happy(url, init)));
  assert.equal(await stepJob(env, await getJob(env), 1000, f), "PROCESSING");
  assert.equal(f.calls.length, 3);
  assert.match(f.calls[2].body, /media_type=CAROUSEL/);
  assert.match(decodeURIComponent(f.calls[2].body), /children=K1,K2/);
});

test("첫 댓글: 게시 성공과 분리, 중복 댓글 방지, 실패해도 게시는 유지", async () => {
  let env = await makeEnv(); await addJob(env, { extra: { first_comment: "크레딧 @x" } });
  await env.DB.prepare("UPDATE jobs SET state='READY', container_id='C1' WHERE id='job1'").run();
  let f = fakeGraph(happy);
  assert.equal(await stepJob(env, await getJob(env), 1000, f), "PUBLISHED");
  assert.equal((await getJob(env)).comment_state, "PENDING");
  await stepComment(env, await getJob(env), 2000, f);
  assert.equal((await getJob(env)).comment_state, "DONE");

  // 이미 같은 댓글이 있으면 다시 쓰지 않는다 (이전 응답 유실 대비)
  env = await makeEnv(); await addJob(env, { extra: { first_comment: "크레딧 @x" } });
  await env.DB.prepare("UPDATE jobs SET state='PUBLISHED', media_id='M1', comment_state='UNKNOWN' WHERE id='job1'").run();
  f = fakeGraph((url, init) => (url.includes("/comments") && init.method === "GET" ? ok({ data: [{ id: "OLD", text: "크레딧 @x" }] }) : happy(url, init)));
  await stepComment(env, await getJob(env), 2000, f);
  assert.equal((await getJob(env)).comment_id, "OLD");
  assert.ok(!f.calls.some((c) => c.method === "POST"));

  // 댓글 실패는 게시 상태를 바꾸지 않는다
  env = await makeEnv(); await addJob(env, { extra: { first_comment: "c" } });
  await env.DB.prepare("UPDATE jobs SET state='PUBLISHED', media_id='M1', comment_state='PENDING' WHERE id='job1'").run();
  f = fakeGraph((url, init) => (url.includes("/comments") ? err(400, 100, "no") : happy(url, init)));
  await stepComment(env, await getJob(env), 2000, f);
  const j = await getJob(env);
  assert.equal(j.state, "PUBLISHED"); assert.equal(j.comment_state, "FAILED");
});

test("취소: 예약은 취소 가능, 게시 요청이 나간 뒤에는 보장 안 함(409)", async () => {
  const env = await makeEnv(); await addJob(env); await addJob(env, { id: "job2" });
  assert.equal((await handle(req("/v1/jobs/job1/cancel", { method: "POST", headers: auth }), env)).status, 200);
  assert.equal(await stepJob(env, await getJob(env), 5000, fakeGraph(happy)), "CANCELLED");
  await env.DB.prepare("UPDATE jobs SET state='PUBLISHING' WHERE id='job2'").run();
  assert.equal((await handle(req("/v1/jobs/job2/cancel", { method: "POST", headers: auth }), env)).status, 409);
});

test("정리: 게시 후 유예가 지나면 임시 영상 삭제, 댓글 대기 중이면 보존, 실패는 오래 보관", async () => {
  const env = await makeEnv(); await addJob(env, { id: "p1" }); await addJob(env, { id: "f1" });
  await env.DB.prepare("UPDATE jobs SET state='PUBLISHED', published_at=1000, comment_state='DONE' WHERE id='p1'").run();
  await env.DB.prepare("UPDATE jobs SET state='FAILED', updated=1000 WHERE id='f1'").run();
  await tick(env, 1000 + 30 * 60000, fakeGraph(happy));
  assert.ok(await env.MEDIA.head("p1-v"), "유예 전 보존");
  await tick(env, 1000 + 61 * 60000, fakeGraph(happy));
  assert.equal(await env.MEDIA.head("p1-v"), null);
  assert.ok(await env.MEDIA.head("f1-v"), "실패 작업은 확인용으로 더 보관");
  await tick(env, 1000 + 8 * 24 * 3600000, fakeGraph(happy));
  assert.equal(await env.MEDIA.head("f1-v"), null);
});

test("OAuth 인계: 코드는 한 번만, 5분 후 만료, 토큰 교환은 하지 않음", async () => {
  const env = await makeEnv();
  assert.equal((await handle(req("/oauth/callback?state=s1&code=CODE1"), env, 1000)).status, 200);
  const a = await handle(req("/v1/oauth/claim", { method: "POST", headers: auth, body: { state: "s1" } }), env, 2000);
  assert.equal((await a.json()).code, "CODE1");
  assert.equal((await handle(req("/v1/oauth/claim", { method: "POST", headers: auth, body: { state: "s1" } }), env, 3000)).status, 404);
  await handle(req("/oauth/callback?state=s2&code=C2"), env, 1000);
  assert.equal((await handle(req("/v1/oauth/claim", { method: "POST", headers: auth, body: { state: "s2" } }), env, 1000 + 6 * 60000)).status, 404);
});

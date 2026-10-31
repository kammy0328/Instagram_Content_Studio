// Instagram Publisher 중계 (Cloudflare Worker). 앱 <-> 중계: Bearer RELAY_API_KEY. Meta 는 서명된 /m/... URL 로 영상을 가져간다.
import { decryptToken, encryptToken, mask, safeEqual, verifyMedia } from "./crypto.js";
import { stepJob, tick } from "./jobs.js";

const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json; charset=utf-8" } });
const bad = (msg, status = 400) => json({ error: msg }, status);

const NOT_CANCELLABLE = new Set(["PUBLISHING", "RECONCILIATION_REQUIRED", "PUBLISHED", "CANCELLED"]);

function authorized(req, env) {
  const h = req.headers.get("authorization") || "";
  return env.RELAY_API_KEY && h.startsWith("Bearer ") && safeEqual(h.slice(7), env.RELAY_API_KEY);
}

const publicJob = (j) => ({
  id: j.id, kind: j.kind, state: j.state, run_at: j.run_at, container_id: j.container_id, media_id: j.media_id,
  error: j.error, tries: j.tries, comment_state: j.comment_state, updated: j.updated, published_at: j.published_at,
});

export async function handle(req, env, now = Date.now(), fetchFn = fetch) {
  const url = new URL(req.url);
  const path = url.pathname;

  // ---- Meta 가 영상을 가져가는 경로 (서명 URL, Range 지원)
  if (path.startsWith("/m/") && (req.method === "GET" || req.method === "HEAD")) {
    const key = decodeURIComponent(path.slice(3));
    if (!(await verifyMedia(env.MEDIA_SIGNING_KEY, key, url.searchParams.get("exp"), url.searchParams.get("sig"), now))) return new Response("forbidden", { status: 403 });
    const range = req.headers.get("range");
    const obj = req.method === "HEAD" ? await env.MEDIA.head(key) : await env.MEDIA.get(key, range ? { range } : undefined);
    if (!obj) return new Response("not found", { status: 404 });
    const headers = new Headers({ "content-type": (obj.httpMetadata && obj.httpMetadata.contentType) || "application/octet-stream", "accept-ranges": "bytes" });
    const size = obj.size;
    if (range && obj.range && req.method === "GET") {
      const { offset = 0, length = size - offset } = obj.range;
      headers.set("content-range", `bytes ${offset}-${offset + length - 1}/${size}`);
      headers.set("content-length", String(length));
      return new Response(obj.body, { status: 206, headers });
    }
    headers.set("content-length", String(size));
    return new Response(req.method === "HEAD" ? null : obj.body, { status: 200, headers });
  }

  // ---- OAuth 리다이렉션 수신: 코드는 짧은 시간 일회용 인계로만 보관 (토큰 교환은 공식 확인 전까지 비활성)
  if (path === "/oauth/callback" && req.method === "GET") {
    const state = url.searchParams.get("state"), code = url.searchParams.get("code");
    if (!state || !code) return new Response("잘못된 요청입니다.", { status: 400 });
    await env.DB.prepare("INSERT OR REPLACE INTO oauth_handoff(state,code,created,claimed) VALUES(?,?,?,0)").bind(state, code, now).run();
    return new Response("연결 코드를 받았습니다. 앱으로 돌아가세요. 이 창은 닫아도 됩니다.", { status: 200, headers: { "content-type": "text/plain; charset=utf-8" } });
  }

  if (!path.startsWith("/v1/")) return bad("not found", 404);
  if (!authorized(req, env)) return bad("unauthorized", 401);

  // ---- 일회용 인계 코드 가져가기
  if (path === "/v1/oauth/claim" && req.method === "POST") {
    const { state } = await req.json();
    const row = await env.DB.prepare("SELECT * FROM oauth_handoff WHERE state=?").bind(state).first();
    if (!row || row.claimed || now - row.created > 5 * 60000) return bad("expired or unknown", 404);
    await env.DB.prepare("UPDATE oauth_handoff SET claimed=1, code='' WHERE state=?").bind(state).run();
    return json({ code: row.code });
  }

  // ---- 계정 토큰 저장 (암호화, 응답/로그로 되돌려 주지 않음)
  let m = path.match(/^\/v1\/accounts\/([^/]+)\/token$/);
  if (m && req.method === "PUT") {
    const b = await req.json();
    if (!b.token) return bad("token required");
    const enc = await encryptToken(env.TOKEN_KEY, b.token);
    await env.DB.prepare("INSERT OR REPLACE INTO accounts(ig_id,username,token_enc,token_expires,updated) VALUES(?,?,?,?,?)")
      .bind(m[1], b.username || "", enc, b.expires_at || null, now).run();
    return json({ ok: true });
  }
  if (m && req.method === "DELETE") {
    await env.DB.prepare("DELETE FROM accounts WHERE ig_id=?").bind(m[1]).run();
    await env.DB.prepare("UPDATE jobs SET state='CANCELLED', error='계정 연결 해제' WHERE ig_id=? AND state IN ('SCHEDULED','RETRY_WAIT','CREATING','PROCESSING','READY')").bind(m[1]).run();
    return json({ ok: true });
  }

  // ---- 영상 업로드 (Worker 요청 본문 한도를 피하려고 R2 멀티파트: 앱이 조각 단위로 올린다)
  if (path === "/v1/media" && req.method === "POST") {
    const b = await req.json();
    if (!b.id || !/^[A-Za-z0-9_.-]{6,100}$/.test(b.id)) return bad("invalid id");
    const key = `${b.id}`;
    const up = await env.MEDIA.createMultipartUpload(key, { httpMetadata: { contentType: b.content_type || "application/octet-stream" } });
    return json({ key, upload_id: up.uploadId });
  }
  m = path.match(/^\/v1\/media\/([^/]+)\/parts\/(\d+)$/);
  if (m && req.method === "PUT") {
    const up = env.MEDIA.resumeMultipartUpload(decodeURIComponent(m[1]), url.searchParams.get("upload_id"));
    const part = await up.uploadPart(Number(m[2]), req.body);
    return json({ part_number: Number(m[2]), etag: part.etag });
  }
  m = path.match(/^\/v1\/media\/([^/]+)\/complete$/);
  if (m && req.method === "POST") {
    const b = await req.json();
    const up = env.MEDIA.resumeMultipartUpload(decodeURIComponent(m[1]), b.upload_id);
    const obj = await up.complete(b.parts.map((p) => ({ partNumber: p.part_number, etag: p.etag })));
    return json({ key: m[1], size: obj.size });
  }
  m = path.match(/^\/v1\/media\/([^/]+)$/);
  if (m && req.method === "PUT") { // 작은 파일(이미지) 한 번에 업로드
    const obj = await env.MEDIA.put(decodeURIComponent(m[1]), req.body, { httpMetadata: { contentType: req.headers.get("content-type") || "application/octet-stream" } });
    return json({ key: m[1], size: obj.size });
  }

  // ---- 예약 작업
  if (path === "/v1/jobs" && req.method === "POST") {
    const b = await req.json();
    if (!b.id || !b.ig_id || !b.approval_hash || !b.payload || !Array.isArray(b.payload.media) || !b.payload.media.length) return bad("invalid job");
    if (typeof b.run_at !== "number") return bad("run_at required (epoch ms)");
    const dup = await env.DB.prepare("SELECT id,state FROM jobs WHERE id=?").bind(b.id).first();
    if (dup) return json({ id: dup.id, state: dup.state, duplicate: true }); // 멱등: 같은 id 는 새로 만들지 않는다
    for (const mm of b.payload.media) { // 업로드가 끝난 객체만 허용
      const o = await env.MEDIA.head(mm.key);
      if (!o) return bad(`media not uploaded: ${mm.key}`, 409);
      if (mm.size && o.size !== mm.size) return bad(`media size mismatch: ${mm.key}`, 409);
    }
    await env.DB.prepare("INSERT INTO jobs(id,ig_id,kind,state,run_at,payload,approval_hash,created,updated) VALUES(?,?,?,?,?,?,?,?,?)")
      .bind(b.id, b.ig_id, b.payload.kind, "SCHEDULED", b.run_at, JSON.stringify(b.payload), b.approval_hash, now, now).run();
    return json({ id: b.id, state: "SCHEDULED" }, 201);
  }
  if (path === "/v1/jobs" && req.method === "GET") {
    const rows = (await env.DB.prepare("SELECT * FROM jobs ORDER BY created DESC LIMIT 100").all()).results || [];
    return json({ jobs: rows.map(publicJob) });
  }
  m = path.match(/^\/v1\/jobs\/([^/]+)(?:\/(cancel|resolve|run-now))?$/);
  if (m) {
    const job = await env.DB.prepare("SELECT * FROM jobs WHERE id=?").bind(m[1]).first();
    if (!job) return bad("not found", 404);
    if (!m[2] && req.method === "GET") return json(publicJob(job));
    if (m[2] === "cancel" && req.method === "POST") {
      if (NOT_CANCELLABLE.has(job.state)) return json({ ...publicJob(job), note: "이미 전송된 게시 요청의 취소는 보장하지 않습니다." }, 409);
      await env.DB.prepare("UPDATE jobs SET state='CANCELLED', updated=? WHERE id=?").bind(now, job.id).run();
      return json({ id: job.id, state: "CANCELLED" });
    }
    if (m[2] === "resolve" && req.method === "POST") { // 결과 불명 해소: 사용자가 확인한 결과만 반영
      if (job.state !== "RECONCILIATION_REQUIRED") return bad("not in reconciliation", 409);
      const b = await req.json();
      if (b.media_id) await env.DB.prepare("UPDATE jobs SET state='PUBLISHED', media_id=?, published_at=?, error=NULL, updated=? WHERE id=?").bind(b.media_id, now, now, job.id).run();
      else await env.DB.prepare("UPDATE jobs SET state='FAILED', error='사용자가 게시되지 않았음을 확인했습니다. 재시도하려면 새 승인이 필요합니다.', updated=? WHERE id=?").bind(now, job.id).run();
      return json(publicJob(await env.DB.prepare("SELECT * FROM jobs WHERE id=?").bind(job.id).first()));
    }
    if (m[2] === "run-now" && req.method === "POST") { // 놓친 예약을 사용자가 지금 게시하기로 한 경우
      if (job.state !== "MISSED") return bad("not missed", 409);
      await env.DB.prepare("UPDATE jobs SET state='SCHEDULED', run_at=?, updated=? WHERE id=?").bind(now, now, job.id).run();
      return json({ id: job.id, state: "SCHEDULED" });
    }
  }
  return bad("not found", 404);
}

export default {
  fetch: (req, env, ctx) => handle(req, env).catch((e) => json({ error: mask(e && e.message ? e.message : "internal") }, 500)),
  scheduled: (_event, env, ctx) => ctx.waitUntil(tick(env)),
};
export { stepJob, tick };

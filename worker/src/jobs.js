// 예약 게시 상태기계 (cron 1분마다 한 걸음씩). Python 엔진(ics/publisher/engine.py)과 같은 규칙:
//  - 게시 요청 전 의도를 먼저 저장하고, 응답이 유실되면 RECONCILIATION_REQUIRED (자동 재게시 금지)
//  - 일시 오류만 제한 횟수 재시도, 인증 오류는 AUTH_REQUIRED, 규격 오류는 FAILED
//  - 성공한 게시는 자동 삭제/롤백하지 않는다. 댓글은 게시와 분리.
import { decryptToken, mask, mediaUrl } from "./crypto.js";
import { Ambiguous, ApiError, Graph, NetworkDown } from "./graph.js";

export const RETRY_DELAYS_MS = [5000, 30000, 120000];
const POLL_MS = 60000;
const TERMINAL = new Set(["PUBLISHED", "FAILED", "CANCELLED"]);

export async function attempt(env, jobId, op, status, detail, now) {
  await env.DB.prepare("INSERT INTO attempts(job_id,op,status,ts,detail) VALUES(?,?,?,?,?)")
    .bind(jobId, op, status, now, mask(detail || "").slice(0, 500)).run();
}

async function update(env, id, fields, now) {
  const keys = Object.keys(fields);
  const sets = keys.map((k) => `${k}=?`).concat("updated=?").join(",");
  await env.DB.prepare(`UPDATE jobs SET ${sets} WHERE id=?`).bind(...keys.map((k) => fields[k]), now, id).run();
}

async function graphFor(env, job, fetchFn) {
  const acc = await env.DB.prepare("SELECT token_enc FROM accounts WHERE ig_id=?").bind(job.ig_id).first();
  if (!acc || !acc.token_enc) return null;
  const payload = JSON.parse(job.payload);
  return new Graph(env, await decryptToken(env.TOKEN_KEY, acc.token_enc), payload.api_version, fetchFn);
}

async function toRetry(env, job, why, now, resume, delay) {
  const tries = (job.tries || 0) + 1;
  if (delay == null) {
    if (tries > RETRY_DELAYS_MS.length) {
      await update(env, job.id, { state: "FAILED", error: `재시도 한도 초과: ${why}`, tries }, now);
      return "FAILED";
    }
    delay = RETRY_DELAYS_MS[tries - 1];
  }
  await update(env, job.id, { state: "RETRY_WAIT", resume_state: resume, tries, next_try_at: now + delay, error: why }, now);
  await attempt(env, job.id, "retry", "wait", `${why} (${delay}ms)`, now);
  return "RETRY_WAIT";
}

async function onApiError(env, job, e, now, resume) {
  await attempt(env, job.id, "api_error", e.kind, `HTTP ${e.http} ${e.code} ${e.message}`, now);
  if (e.kind === "auth") {
    await update(env, job.id, { state: "AUTH_REQUIRED", error: "인증/권한 오류: 앱에서 다시 연결하세요." }, now);
    return "AUTH_REQUIRED";
  }
  if (e.kind === "rate") return toRetry(env, job, `호출 제한: ${e.message}`, now, resume, 600000);
  if (e.kind === "server") return toRetry(env, job, `서버 오류: ${e.message}`, now, resume);
  await update(env, job.id, { state: "FAILED", error: `규격/파라미터 오류: 자동 재시도하지 않습니다. ${e.message}` }, now);
  return "FAILED";
}

async function containerParams(env, p, now) {
  const urls = [];
  for (const m of p.media.filter((x) => x.role !== "cover")) urls.push(await mediaUrl(env, m.key, now));
  return urls;
}

async function createContainer(env, g, job, p, now) {
  const media = p.media.filter((x) => x.role !== "cover");
  const urls = await containerParams(env, p, now);
  const ig = job.ig_id;
  if (p.kind === "reels") return g.createContainer(ig, { media_type: "REELS", video_url: urls[0], caption: p.caption || "" });
  if (p.kind === "story") {
    return g.createContainer(ig, { media_type: "STORIES", [media[0].role === "video" ? "video_url" : "image_url"]: urls[0] });
  }
  if (p.kind === "feed_image") return g.createContainer(ig, { image_url: urls[0], caption: p.caption || "" });
  if (p.kind === "feed_video") {
    if (!p.video_media_type) throw new ApiError(0, 0, "단일 피드 영상의 게시 유형이 확인되지 않았습니다.", "param");
    return g.createContainer(ig, { media_type: p.video_media_type, video_url: urls[0], caption: p.caption || "" });
  }
  if (p.kind === "carousel") {
    const children = job.children ? JSON.parse(job.children) : [];
    for (let i = children.length; i < media.length; i++) { // 자식은 하나씩 저장하며 진행 (중간에 끊겨도 이어서)
      const params = { [media[i].role === "video" ? "video_url" : "image_url"]: urls[i], is_carousel_item: "true" };
      if (media[i].role === "video") params.media_type = "VIDEO";
      children.push(await g.createContainer(ig, params));
      await update(env, job.id, { children: JSON.stringify(children) }, now);
    }
    return g.createContainer(ig, { media_type: "CAROUSEL", children: children.join(","), caption: p.caption || "" });
  }
  throw new ApiError(0, 0, `알 수 없는 유형: ${p.kind}`, "param");
}

// 한 작업을 한 걸음(또는 연속 몇 걸음) 진행하고 새 상태를 돌려준다.
export async function stepJob(env, job, now, fetchFn = fetch) {
  if (TERMINAL.has(job.state) || ["AUTH_REQUIRED", "RECONCILIATION_REQUIRED", "MISSED", "AWAITING_APPROVAL"].includes(job.state)) return job.state;
  const p = JSON.parse(job.payload);
  const grace = Number(env.MISSED_GRACE_MIN || 10) * 60000;

  if (job.state === "SCHEDULED") {
    if (now < job.run_at) return "SCHEDULED";
    if (now - job.run_at > grace) {
      await update(env, job.id, { state: "MISSED", error: "예약 시각이 지나서 자동 게시하지 않았습니다. 확인 후 지금 게시하거나 취소하세요." }, now);
      return "MISSED";
    }
    await update(env, job.id, { state: "CREATING" }, now);
    job = { ...job, state: "CREATING" };
  }
  if (job.state === "RETRY_WAIT") {
    if (job.next_try_at && now < job.next_try_at) return "RETRY_WAIT";
    job = { ...job, state: job.resume_state || "CREATING" };
    await update(env, job.id, { state: job.state }, now);
  }
  if (job.state === "PUBLISHING") { // 게시 요청 도중 중단: 결과 불명
    await update(env, job.id, { state: "RECONCILIATION_REQUIRED", error: "게시 요청 도중 중단되어 결과를 알 수 없습니다." }, now);
    return "RECONCILIATION_REQUIRED";
  }
  const g = await graphFor(env, job, fetchFn);
  if (!g) {
    await update(env, job.id, { state: "AUTH_REQUIRED", error: "계정 토큰이 없습니다. 앱에서 다시 연결하세요." }, now);
    return "AUTH_REQUIRED";
  }
  try {
    if (job.state === "CREATING") {
      await attempt(env, job.id, "create_container", "intent", "", now);
      let cid;
      try { cid = await createContainer(env, g, job, p, now); }
      catch (e) {
        if (e instanceof Ambiguous) { await attempt(env, job.id, "create_container", "unknown", "응답 유실: 생성 여부 불명", now); return toRetry(env, job, "응답 시간 초과", now, "CREATING"); }
        throw e;
      }
      await update(env, job.id, { state: "PROCESSING", container_id: cid, next_try_at: now + POLL_MS }, now);
      await attempt(env, job.id, "create_container", "ok", cid, now);
      return "PROCESSING";
    }
    if (job.state === "PROCESSING") {
      if (job.next_try_at && now < job.next_try_at) return "PROCESSING";
      const st = (await g.status(job.container_id)).status_code;
      if (st === "FINISHED") { await update(env, job.id, { state: "READY" }, now); job = { ...job, state: "READY" }; }
      else if (st === "ERROR") { await update(env, job.id, { state: "FAILED", error: "Meta 가 컨테이너 처리에 실패했습니다 (ERROR)." }, now); return "FAILED"; }
      else if (st === "EXPIRED") { await update(env, job.id, { state: "CREATING", container_id: null, children: null }, now); return "CREATING"; }
      else if (st === "PUBLISHED") { await update(env, job.id, { state: "RECONCILIATION_REQUIRED", error: "컨테이너가 이미 게시된 상태입니다. 게시물을 확인하세요." }, now); return "RECONCILIATION_REQUIRED"; }
      else { await update(env, job.id, { next_try_at: now + POLL_MS }, now); return "PROCESSING"; }
    }
    if (job.state === "READY") {
      try {
        const q = await g.limit(job.ig_id);
        if (q.used != null && q.total != null && q.used >= q.total) return toRetry(env, job, `게시 한도(${q.used}/${q.total})에 도달했습니다.`, now, "READY", 3600000);
      } catch { /* 한도 조회 실패는 막지 않는다 (추측 금지) */ }
      await update(env, job.id, { state: "PUBLISHING", intent: `publish:${job.container_id}` }, now); // 의도 먼저 저장
      await attempt(env, job.id, "publish", "intent", job.container_id, now);
      let mid;
      try { mid = await g.publish(job.ig_id, job.container_id); }
      catch (e) {
        if (e instanceof Ambiguous || e instanceof NetworkDown) {
          await attempt(env, job.id, "publish", "unknown", e.message, now);
          await update(env, job.id, { state: "RECONCILIATION_REQUIRED", error: "게시 요청의 결과를 알 수 없습니다. 자동 재게시하지 않습니다." }, now);
          return "RECONCILIATION_REQUIRED";
        }
        throw e;
      }
      await update(env, job.id, { state: "PUBLISHED", media_id: mid, published_at: now, error: null }, now); // 게시물 ID 먼저 영속 저장
      await attempt(env, job.id, "publish", "ok", mid, now);
      if (p.first_comment) await update(env, job.id, { comment_state: "PENDING" }, now);
      return "PUBLISHED";
    }
  } catch (e) {
    const resume = job.state === "READY" ? "READY" : job.state;
    if (e instanceof ApiError) return onApiError(env, job, e, now, resume);
    if (e instanceof Ambiguous) return toRetry(env, job, "응답 시간 초과", now, resume);
    if (e instanceof NetworkDown) return toRetry(env, job, "네트워크 연결 실패", now, resume);
    throw e;
  }
  return job.state;
}

// 게시 성공 뒤 댓글 (게시 성공과 분리). 응답 유실 가능성이 있어 기존 댓글을 먼저 확인한다.
export async function stepComment(env, job, now, fetchFn = fetch) {
  if (job.state !== "PUBLISHED" || !["PENDING", "UNKNOWN", "FAILED"].includes(job.comment_state || "")) return;
  if ((job.comment_tries || 0) >= 5) return;
  const p = JSON.parse(job.payload);
  const g = await graphFor(env, job, fetchFn);
  if (!g) return;
  const tries = (job.comment_tries || 0) + 1;
  try {
    const existing = await g.comments(job.media_id);
    const dup = existing.find((c) => c.text === p.first_comment);
    if (dup) { await update(env, job.id, { comment_state: "DONE", comment_id: dup.id, comment_tries: tries }, now); return; }
    const cid = await g.addComment(job.media_id, p.first_comment);
    await update(env, job.id, { comment_state: "DONE", comment_id: cid, comment_tries: tries }, now);
  } catch (e) {
    const state = e instanceof Ambiguous || e instanceof NetworkDown ? "UNKNOWN" : "FAILED";
    await update(env, job.id, { comment_state: state, comment_tries: tries }, now);
    await attempt(env, job.id, "comment", state, e.message, now);
  }
}

export async function cleanup(env, now) {
  const delay = Number(env.CLEANUP_DELAY_MIN || 60) * 60000;
  const failKeep = 7 * 24 * 3600000;
  const rows = (await env.DB.prepare("SELECT * FROM jobs WHERE cleaned=0 AND state IN ('PUBLISHED','FAILED','CANCELLED')").all()).results || [];
  for (const j of rows) {
    const age = now - (j.published_at || j.updated || 0);
    const keep = j.state === "PUBLISHED" ? delay : failKeep;
    if (age < keep) continue;
    if (j.state === "PUBLISHED" && j.comment_state && !["DONE"].includes(j.comment_state) && (j.comment_tries || 0) < 5) continue;
    try {
      for (const m of JSON.parse(j.payload).media) await env.MEDIA.delete(m.key);
      await update(env, j.id, { cleaned: 1 }, now);
    } catch (e) { await attempt(env, j.id, "cleanup", "failed", e.message, now); } // 정리 실패는 별도 기록, 다음 주기에 재시도
  }
}

export async function tick(env, now = Date.now(), fetchFn = fetch) {
  const due = (await env.DB.prepare(
    "SELECT * FROM jobs WHERE state IN ('SCHEDULED','CREATING','PROCESSING','READY','RETRY_WAIT','PUBLISHING') ORDER BY run_at LIMIT 10"
  ).all()).results || [];
  const out = {};
  for (const j of due) out[j.id] = await stepJob(env, j, now, fetchFn);
  const comments = (await env.DB.prepare("SELECT * FROM jobs WHERE state='PUBLISHED' AND comment_state IN ('PENDING','UNKNOWN','FAILED') LIMIT 5").all()).results || [];
  for (const j of comments) await stepComment(env, j, now, fetchFn);
  await cleanup(env, now);
  return out;
}

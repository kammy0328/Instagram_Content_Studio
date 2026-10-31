// 테스트용 가짜 D1 (node:sqlite), R2, Graph(fetch). 실제 Cloudflare/Meta 호출 없음.
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

export function fakeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec(readFileSync(new URL("../schema.sql", import.meta.url), "utf8"));
  const stmt = (sql, args = []) => ({
    bind: (...a) => stmt(sql, a),
    run: async () => { db.prepare(sql).run(...args); return { success: true }; },
    first: async () => db.prepare(sql).get(...args) || null,
    all: async () => ({ results: db.prepare(sql).all(...args) }),
  });
  return { prepare: (sql) => stmt(sql), raw: db };
}

export function fakeR2() {
  const store = new Map(); const mp = new Map(); let n = 0;
  const asBytes = async (b) => (b instanceof Uint8Array ? b : new Uint8Array(await new Response(b).arrayBuffer()));
  const obj = (k, ct, range) => {
    const v = store.get(k); if (!v) return null;
    const o = { size: v.bytes.length, httpMetadata: { contentType: ct || v.ct }, body: v.bytes };
    if (range) {
      const m = /bytes=(\d+)-(\d*)/.exec(range); const off = Number(m[1]); const end = m[2] ? Number(m[2]) : v.bytes.length - 1;
      o.range = { offset: off, length: end - off + 1 }; o.body = v.bytes.slice(off, end + 1);
    }
    return o;
  };
  return {
    store,
    put: async (k, body, opts = {}) => { const bytes = await asBytes(body); store.set(k, { bytes, ct: opts.httpMetadata?.contentType }); return { size: bytes.length }; },
    get: async (k, opts) => {
      if (opts?.range && typeof opts.range === "string") throw new TypeError("R2 requires Headers or Range object"); // 실제 R2 와 동일하게 문자열 거부
      return obj(k, null, opts?.range && opts.range.get ? opts.range.get("range") : opts?.range);
    },
    head: async (k) => { const v = store.get(k); return v ? { size: v.bytes.length } : null; },
    delete: async (k) => { store.delete(k); },
    createMultipartUpload: async (k, opts = {}) => { const id = `u${++n}`; mp.set(id, { k, ct: opts.httpMetadata?.contentType, parts: new Map() }); return { uploadId: id }; },
    resumeMultipartUpload: (k, id) => ({
      uploadPart: async (num, body) => { mp.get(id).parts.set(num, await asBytes(body)); return { etag: `e${num}` }; },
      complete: async (parts) => {
        const u = mp.get(id); const chunks = parts.map((p) => u.parts.get(p.partNumber));
        const bytes = new Uint8Array(chunks.reduce((s, c) => s + c.length, 0)); let o = 0;
        for (const c of chunks) { bytes.set(c, o); o += c.length; }
        store.set(u.k, { bytes, ct: u.ct }); return { size: bytes.length };
      },
    }),
  };
}

// 스크립트된 Graph 응답. handler(url, init) -> {status, body} | throw
export function fakeGraph(handler) {
  const calls = [];
  const f = async (url, init) => {
    calls.push({ url: String(url), method: init.method, body: init.body ? String(init.body) : "" });
    const r = await handler(String(url), init, calls);
    return { ok: r.status < 400, status: r.status, json: async () => r.body ?? {} };
  };
  f.calls = calls;
  return f;
}

export async function makeEnv() {
  const { encryptToken } = await import("../src/crypto.js");
  const key = Buffer.alloc(32, 7).toString("base64");
  const env = {
    DB: fakeD1(), MEDIA: fakeR2(), RELAY_API_KEY: "relay-secret-key", TOKEN_KEY: key, MEDIA_SIGNING_KEY: "sign-secret",
    PUBLIC_BASE: "https://relay.test", MISSED_GRACE_MIN: "10", CLEANUP_DELAY_MIN: "60", GRAPH_HOST: "https://graph.test",
  };
  await env.DB.prepare("INSERT INTO accounts(ig_id,username,token_enc,updated) VALUES(?,?,?,?)")
    .bind("111", "tester", await encryptToken(key, "IGQVJ_SECRET_TOKEN_VALUE_0123456789"), 0).run();
  return env;
}

export async function addJob(env, { id = "job1", kind = "reels", runAt = 1000, extra = {}, media } = {}) {
  const m = media || [{ key: `${id}-v`, role: "video", size: 3 }];
  for (const x of m) await env.MEDIA.put(x.key, new Uint8Array([1, 2, 3]));
  const payload = { kind, api_version: "v99.0", caption: "hello #a", media: m, ...extra };
  await env.DB.prepare("INSERT INTO jobs(id,ig_id,kind,state,run_at,payload,approval_hash,created,updated) VALUES(?,?,?,?,?,?,?,?,?)")
    .bind(id, "111", kind, "SCHEDULED", runAt, JSON.stringify(payload), "h", 0, 0).run();
  return payload;
}
export const getJob = (env, id = "job1") => env.DB.prepare("SELECT * FROM jobs WHERE id=?").bind(id).first();

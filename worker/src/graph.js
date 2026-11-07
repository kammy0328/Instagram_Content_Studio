// graph.instagram.com 호출. 엔드포인트는 docs/PUBLISHER_VERIFICATION.md 에서 확인된 범위만 쓴다.
import { mask } from "./crypto.js";

export class ApiError extends Error {
  constructor(http, code, message, kind) {
    super(message);
    this.http = http; this.code = code; this.kind = kind; // auth | rate | server | param
  }
}
export class Ambiguous extends Error {} // 요청이 나갔을 수 있으나 결과를 모름 (타임아웃 등)
export class NetworkDown extends Error {} // 요청이 닿지 않음

export function classify(http, code) {
  if (http === 401 || http === 403 || [190, 102, 10, 200].includes(code)) return "auth";
  if (http === 429 || [4, 17, 32, 613].includes(code)) return "rate";
  if (http >= 500) return "server";
  return "param";
}

export class Graph {
  constructor(env, token, version, fetchFn = fetch, timeoutMs = 20000) {
    this.host = env.GRAPH_HOST || "https://graph.instagram.com";
    this.token = token; this.ver = version; this.f = fetchFn; this.timeout = timeoutMs;
  }

  async call(method, path, params = {}) {
    let url = `${this.host}/${this.ver}/${path}`;
    const init = { method, headers: { Authorization: `Bearer ${this.token}` }, signal: AbortSignal.timeout(this.timeout) };
    const body = new URLSearchParams(params);
    if (method === "GET") url += `?${body}`;
    else { init.body = body; init.headers["Content-Type"] = "application/x-www-form-urlencoded"; }
    let res;
    try {
      res = await this.f(url, init);
    } catch (e) {
      if (e && (e.name === "TimeoutError" || e.name === "AbortError")) throw new Ambiguous("응답 대기 시간 초과");
      throw new NetworkDown("네트워크 연결 실패");
    }
    let data = {};
    try { data = await res.json(); } catch { /* 본문 없음 */ }
    if (!res.ok) {
      const err = data.error || {};
      throw new ApiError(res.status, Number(err.code || 0), mask(String(err.message || "").slice(0, 300)), classify(res.status, Number(err.code || 0)));
    }
    return data;
  }

  createContainer(igId, params) { return this.call("POST", `${igId}/media`, params).then((d) => String(d.id)); }
  status(containerId) { return this.call("GET", containerId, { fields: "status_code,status" }); }
  publish(igId, containerId) { return this.call("POST", `${igId}/media_publish`, { creation_id: containerId }).then((d) => String(d.id)); }
  async limit(igId) {
    const d = await this.call("GET", `${igId}/content_publishing_limit`, { fields: "quota_usage,config" });
    const r = (d.data || [{}])[0] || {};
    return { used: r.quota_usage, total: (r.config || {}).quota_total };
  }
  comments(mediaId) { return this.call("GET", `${mediaId}/comments`, { fields: "id,text" }).then((d) => d.data || []); }
  addComment(mediaId, text) { return this.call("POST", `${mediaId}/comments`, { message: text }).then((d) => String(d.id)); }
}

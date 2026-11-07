// Python 계약 테스트용: 실제 Worker 코드(handle/tick)를 가짜 D1/R2/Graph 와 함께 로컬 HTTP 서버로 띄운다.
// 제어용 엔드포인트(/__tick, /__graph)는 이 테스트 서버에만 있고 배포 코드에는 없다.
import http from "node:http";
import { handle } from "../src/index.js";
import { tick } from "../src/jobs.js";
import { fakeGraph, makeEnv } from "./fakes.js";

const env = await makeEnv();
env.PUBLIC_BASE = "http://127.0.0.1:0";
let mode = "ok";
const graph = fakeGraph((url, init) => {
  if (url.includes("/media_publish")) {
    if (mode === "lose") { const e = new Error("t"); e.name = "TimeoutError"; throw e; }
    return { status: 200, body: { id: "M1" } };
  }
  if (url.includes("/media")) return { status: 200, body: { id: "C" + graph.calls.length } };
  if (url.includes("content_publishing_limit")) return { status: 200, body: { data: [{ quota_usage: 0, config: { quota_total: 50 } }] } };
  if (url.includes("/comments")) return init.method === "GET" ? { status: 200, body: { data: [] } } : { status: 200, body: { id: "CM" } };
  return { status: 200, body: { status_code: "FINISHED" } };
});

const server = http.createServer(async (rq, rs) => {
  const chunks = []; for await (const c of rq) chunks.push(c);
  const body = Buffer.concat(chunks);
  const url = new URL(rq.url, "http://x");
  let res;
  if (url.pathname === "/__tick") { res = Response.json(await tick(env, JSON.parse(body).now, graph)); }
  else if (url.pathname === "/__graph") { mode = JSON.parse(body).mode; res = Response.json({ ok: true }); }
  else if (url.pathname === "/__calls") { res = Response.json(graph.calls); }
  else {
    const init = { method: rq.method, headers: rq.headers };
    if (!["GET", "HEAD"].includes(rq.method)) init.body = body;
    res = await handle(new Request(`https://relay.test${rq.url}`, init), env, Number(rq.headers["x-test-now"]) || Date.now(), graph);
  }
  rs.writeHead(res.status, Object.fromEntries(res.headers));
  rs.end(Buffer.from(await res.arrayBuffer()));
});
server.listen(0, "127.0.0.1", () => console.log(`PORT ${server.address().port}`));

// 서명/암호화 유틸. 비밀값은 로그/응답에 내보내지 않는다.
const enc = new TextEncoder();

const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const b64url = (buf) => b64(buf).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export function safeEqual(a, b) {
  const x = enc.encode(String(a)), y = enc.encode(String(b));
  let d = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) d |= (x[i] || 0) ^ (y[i] || 0);
  return d === 0;
}

async function hmacKey(secret) {
  return crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
}

export async function signMedia(secret, key, exp) {
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(secret), enc.encode(`${key}\n${exp}`));
  return b64url(sig);
}

export async function verifyMedia(secret, key, exp, sig, nowMs) {
  if (!sig || !exp || Number(exp) < nowMs) return false;
  return safeEqual(await signMedia(secret, key, exp), sig);
}

export async function mediaUrl(env, key, nowMs, ttlMs = 24 * 3600 * 1000) {
  const exp = nowMs + ttlMs;
  const sig = await signMedia(env.MEDIA_SIGNING_KEY, key, exp);
  return `${env.PUBLIC_BASE}/m/${encodeURIComponent(key)}?exp=${exp}&sig=${sig}`;
}

async function aesKey(tokenKeyB64) {
  return crypto.subtle.importKey("raw", unb64(tokenKeyB64), "AES-GCM", false, ["encrypt", "decrypt"]);
}

export async function encryptToken(tokenKeyB64, plain) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await aesKey(tokenKeyB64), enc.encode(plain));
  return `${b64(iv)}.${b64(ct)}`;
}

export async function decryptToken(tokenKeyB64, stored) {
  const [iv, ct] = stored.split(".");
  const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(iv) }, await aesKey(tokenKeyB64), unb64(ct));
  return new TextDecoder().decode(pt);
}

const TOKEN_RE = /(IG[A-Za-z0-9_-]{20,}|access_token=[^&\s]+|Bearer\s+[A-Za-z0-9._-]+|sig=[A-Za-z0-9_-]+)/g;
export const mask = (s) => String(s).replace(TOKEN_RE, "***");

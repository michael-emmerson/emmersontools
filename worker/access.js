// Checks the Cloudflare Access login token (Cf-Access-Jwt-Assertion) on each request.
// https://developers.cloudflare.com/cloudflare-one/identity/authorization-cookie/validating-json/
let certCache = { team: "", keys: null, expires: 0 };

function b64urlBytes(s) {
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

async function accessKeys(team, fetchImpl) {
  if (certCache.team === team && certCache.keys && Date.now() < certCache.expires) return certCache.keys;
  const res = await fetchImpl(`https://${team}/cdn-cgi/access/certs`);
  if (!res.ok) throw new Error(`Access certs: HTTP ${res.status}`);
  const keys = (await res.json()).keys || [];
  certCache = { team, keys, expires: Date.now() + 10 * 60 * 1000 };
  return keys;
}

export async function checkAccess(request, env, now = Date.now(), fetchImpl = fetch) {
  const team = (env.ACCESS_TEAM_DOMAIN || "").replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const aud = env.ACCESS_AUD || "";
  if (!team || !aud) return { ok: false, why: "not-configured" };
  const token = request.headers.get("Cf-Access-Jwt-Assertion") || "";
  const parts = token.split(".");
  if (parts.length !== 3) return { ok: false, why: "no-login" };
  try {
    const header = JSON.parse(new TextDecoder().decode(b64urlBytes(parts[0])));
    const payload = JSON.parse(new TextDecoder().decode(b64urlBytes(parts[1])));
    if (header.alg !== "RS256") return { ok: false, why: "bad-login" };
    const jwk = (await accessKeys(team, fetchImpl)).find((k) => k.kid === header.kid);
    if (!jwk) return { ok: false, why: "bad-login" };
    const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
    const signed = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
    const valid = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64urlBytes(parts[2]), signed);
    const auds = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
    if (!valid || !auds.includes(aud) || payload.iss !== `https://${team}` || !(payload.exp * 1000 > now)) {
      return { ok: false, why: "bad-login" };
    }
    return { ok: true, email: payload.email || "" };
  } catch (err) {
    console.error("Access check failed", err);
    return { ok: false, why: "bad-login" };
  }
}

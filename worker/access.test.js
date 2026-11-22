// npm run test:worker
import { test } from "node:test";
import assert from "node:assert/strict";
import { checkAccess } from "./access.js";

const TEAM = "rrr.cloudflareaccess.com";
const AUD = "aud-123";
const env = { ACCESS_TEAM_DOMAIN: TEAM, ACCESS_AUD: AUD };

const { publicKey, privateKey } = await crypto.subtle.generateKey(
  { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
const jwk = { ...(await crypto.subtle.exportKey("jwk", publicKey)), kid: "k1" };
let certFetches = 0;
const fetchCerts = async (url) => {
  certFetches++;
  assert.equal(url, `https://${TEAM}/cdn-cgi/access/certs`);
  return new Response(JSON.stringify({ keys: [jwk] }));
};

const b64url = (bytes) => Buffer.from(bytes).toString("base64url");
async function token(claims, kid = "k1", key = privateKey) {
  const head = b64url(new TextEncoder().encode(JSON.stringify({ alg: "RS256", kid })));
  const body = b64url(new TextEncoder().encode(JSON.stringify(claims)));
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(`${head}.${body}`));
  return `${head}.${body}.${b64url(new Uint8Array(sig))}`;
}
const good = () => ({ aud: [AUD], iss: `https://${TEAM}`, exp: Math.floor(Date.now() / 1000) + 600, email: "owner@example.com" });
const req = (t) => new Request("https://app.example/", t ? { headers: { "Cf-Access-Jwt-Assertion": t } } : {});

test("locked until Access is configured", async () => {
  assert.deepEqual(await checkAccess(req(await token(good())), {}, Date.now(), fetchCerts), { ok: false, why: "not-configured" });
});

test("a valid login passes", async () => {
  const r = await checkAccess(req(await token(good())), env, Date.now(), fetchCerts);
  assert.deepEqual(r, { ok: true, email: "owner@example.com" });
});

test("missing, expired, wrong-audience, wrong-issuer and forged logins are refused", async () => {
  const other = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign"]);
  const cases = [
    [undefined, "no-login"],
    [await token({ ...good(), exp: Math.floor(Date.now() / 1000) - 5 }), "bad-login"],
    [await token({ ...good(), aud: ["someone-else"] }), "bad-login"],
    [await token({ ...good(), iss: "https://evil.cloudflareaccess.com" }), "bad-login"],
    [await token(good(), "k1", other.privateKey), "bad-login"],
    [await token(good(), "unknown-kid"), "bad-login"],
    ["not.a.jwt", "bad-login"],
  ];
  for (const [t, why] of cases) {
    assert.equal((await checkAccess(req(t), env, Date.now(), fetchCerts)).why, why, String(t).slice(0, 20));
  }
});

test("Access certificates are cached", async () => {
  const before = certFetches;
  await checkAccess(req(await token(good())), env, Date.now(), fetchCerts);
  assert.equal(certFetches, before);
});

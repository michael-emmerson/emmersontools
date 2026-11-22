import { test } from "node:test";
import assert from "node:assert/strict";

// index.js imports @cloudflare/containers, which only loads inside Workers, so test a copy of route().
const RRR_PREFIX = "/rrr-coffee";
const src = (await import("node:fs")).readFileSync(new URL("./index.js", import.meta.url), "utf8");
const body = src.slice(src.indexOf("function route("), src.indexOf("async function rrrCoffee"));
const route = new Function("RRR_PREFIX", body + "; return route;")(RRR_PREFIX);

const u = (p) => new URL("https://emmersontools.com" + p);

test("bare /rrr-coffee gets a trailing slash", () => {
  assert.deepEqual(route(u("/rrr-coffee?x=1")), { kind: "redirect", location: "https://emmersontools.com/rrr-coffee/?x=1" });
});
test("prefix is stripped for the container", () => {
  assert.deepEqual(route(u("/rrr-coffee/")), { kind: "rrr", path: "/" });
  assert.deepEqual(route(u("/rrr-coffee/api/state")), { kind: "rrr", path: "/api/state" });
  assert.deepEqual(route(u("/rrr-coffee/app.js")), { kind: "rrr", path: "/app.js" });
});
test("everything else is static", () => {
  for (const p of ["/", "/subscriptions/", "/jsm-receipt-maker/logo.png", "/rrr-coffeex", "/favicon.svg"]) {
    assert.equal(route(u(p)).kind, "assets", p);
  }
});

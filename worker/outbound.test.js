import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// index.js imports @cloudflare/containers, which only loads inside Workers, so check the source.
// A `static outboundByHost = …` class field skips the library's setter, so the r2.internal handler
// is never registered and the container can't load its files (HTTP 530).
const src = readFileSync(new URL("./index.js", import.meta.url), "utf8");

test("the R2 storage handler is registered by assignment, not a static class field", () => {
  assert.doesNotMatch(src, /^\s*static\s+outboundByHost\s*=/m);
  assert.match(src, /^InventoryContainer\.outboundByHost\s*=\s*\{\s*\n\s*\[STORAGE_HOST\]/m);
});

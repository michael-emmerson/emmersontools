// The Worker for emmersontools.com.
//
// Static tools (the homepage, /subscriptions/, /jsm-receipt-maker/, /coffee-chart-builder/, /ai-readiness-rubric/) are served straight from public/ by
// Cloudflare's asset handling and never reach this code. Only /rrr-coffee and /rrr-coffee/* run here
// (see "run_worker_first" in wrangler.jsonc).
//
// RRR Coffee:
// - Every request must carry a valid Cloudflare Access login (checked here as well as by Access itself),
//   because the app can change the Shopify store. Until ACCESS_TEAM_DOMAIN and ACCESS_AUD are set, the
//   app refuses everyone.
// - The /rrr-coffee prefix is removed before the request goes to the container, so the Python app runs
//   exactly as it does on a computer. Its page uses relative links, so they stay under /rrr-coffee/.
// - Requests go to one container instance, so there is only ever one copy of the spreadsheet being edited.
// - The container keeps its files in the R2 bucket (binding FILES) by sending HTTP requests to
//   http://r2.internal, which are answered here (see apps/rrr-coffee/rrr/cloud.py).
import { Container, getContainer } from "@cloudflare/containers";
import { checkAccess } from "./access.js";

export { ContainerProxy } from "@cloudflare/containers";

const RRR_PREFIX = "/rrr-coffee";
const STORAGE_HOST = "r2.internal";
const KEY_RE = /^(?:backups\/)?[A-Za-z0-9 ._()-]{1,200}$/;

async function storage(request, env) {
  const key = decodeURIComponent(new URL(request.url).pathname.slice(1));
  if (!KEY_RE.test(key) || key.includes("..")) return new Response("Bad key", { status: 400 });
  if (request.method === "GET") {
    const obj = await env.FILES.get(key);
    return obj ? new Response(obj.body) : new Response("Not found", { status: 404 });
  }
  if (request.method === "PUT") {
    await env.FILES.put(key, await request.arrayBuffer());
    return new Response("Saved");
  }
  return new Response("Method not allowed", { status: 405 });
}

export class InventoryContainer extends Container {
  defaultPort = 8080;
  sleepAfter = "15m"; // files are saved to R2 on every change, so stopping loses nothing
  enableInternet = true; // Shopify

  static outboundByHost = {
    [STORAGE_HOST]: (request, env) => storage(request, env),
  };

  constructor(ctx, env) {
    super(ctx, env);
    const vars = { RRR_STORAGE_URL: `http://${STORAGE_HOST}`, RRR_DATA_DIR: "/data" };
    // Shopify credentials stored as Worker secrets take priority over anything saved in Settings.
    for (const name of ["SHOPIFY_STORE", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET", "SHOPIFY_ACCESS_TOKEN"]) {
      if (env[name]) vars[name] = env[name];
    }
    this.envVars = vars;
  }
}

function page(status, title, text) {
  const html = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>${title}</title><body style="font-family:system-ui;max-width:36em;margin:4em auto;padding:0 16px">
<h1 style="font-size:1.3em">${title}</h1><p>${text}</p></body>`;
  return new Response(html, { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}

// Where a request goes: "redirect" (add the trailing slash), "rrr" (with the path the container should
// see), or "assets".
function route(url) {
  const path = url.pathname;
  if (path === RRR_PREFIX) return { kind: "redirect", location: `${url.origin}${RRR_PREFIX}/${url.search}` };
  if (path.startsWith(RRR_PREFIX + "/")) return { kind: "rrr", path: path.slice(RRR_PREFIX.length) };
  return { kind: "assets" };
}

async function rrrCoffee(request, env, innerPath) {
  const access = await checkAccess(request, env);
  if (!access.ok) {
    if (access.why === "not-configured") {
      return page(503, "Login not set up yet",
        "RRR Coffee stays locked until Cloudflare Access is turned on for it. See README.md in the repository.");
    }
    return page(403, "Please sign in", "Open this page again to sign in with your Cloudflare Access login.");
  }
  const url = new URL(request.url);
  url.pathname = innerPath;
  // One instance only: the spreadsheet must have a single editor.
  return getContainer(env.APP, "inventory").fetch(new Request(url, request));
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const r = route(url);
    if (r.kind === "redirect") return Response.redirect(r.location, 301);
    if (r.kind === "rrr") return rrrCoffee(request, env, r.path);
    return env.ASSETS.fetch(request);
  },
};

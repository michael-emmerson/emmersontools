# emmersontools.com

The whole of [emmersontools.com](https://emmersontools.com) in one repository: the homepage and every tool, each in its own folder on the site, all deployed together as one Cloudflare Worker.

| Tool | URL | Source |
|---|---|---|
| Homepage | https://emmersontools.com/ | `public/index.html` |
| Subscription Manager | https://emmersontools.com/subscriptions/ | `public/subscriptions/` |
| JSM Receipt Maker | https://emmersontools.com/jsm-receipt-maker/ | `public/jsm-receipt-maker/` (+ local launcher in `apps/jsm-receipt-maker/`) |
| RRR Coffee | https://emmersontools.com/rrr-coffee/ | `apps/rrr-coffee/` (Python app in a container) |
| Coffee Chart Builder | https://emmersontools.com/coffee-chart-builder/ | `public/coffee-chart-builder/` |
| Michael's Day (personal dashboard) | https://claude.ai/artifact/24BwLeGfc1jKAMGZ3NxtXp | Lives on claude.ai, linked from the homepage. Its Outlook and Shopify panels load through Claude's connectors, so it only works when opened in Claude. |

## Layout

```
public/                     everything served as-is (only this folder is public)
  index.html, favicon.svg     homepage
  subscriptions/              Subscription Manager (single static page, data in the browser)
  jsm-receipt-maker/          JSM Receipt Maker (static page + logo + jsPDF, data in the browser)
  coffee-chart-builder/       Coffee Chart Builder (single static page, cupping radar / flavor wheel PNG export)
apps/
  rrr-coffee/                 RRR Coffee Python app, its Dockerfile, tests and local launchers
  jsm-receipt-maker/          local launcher (server.py, Start Receipt Maker.command) and README
worker/
  index.js                    the Worker: serves /rrr-coffee/* from the container, everything else from public/
  access.js                   Cloudflare Access login check for RRR Coffee
wrangler.jsonc              Worker, static assets, container, Durable Object and R2 configuration
```

How requests are handled:

- `/`, `/subscriptions/…`, `/jsm-receipt-maker/…`, `/coffee-chart-builder/…` are plain files from `public/`. They never run any code. `/subscriptions`, `/jsm-receipt-maker` and `/coffee-chart-builder` without the slash redirect to the version with it.
- `/rrr-coffee/…` goes to the Worker, which checks the Cloudflare Access login, removes the `/rrr-coffee` prefix and passes the request to the RRR Coffee container. The app's page uses relative links, so everything it loads stays under `/rrr-coffee/`.

## Adding a tool

1. Put a static tool in `public/<tool-name>/` with an `index.html`. Use relative links inside it (`logo.png`, not `/logo.png`) so it works from its folder.
2. In `public/index.html`, copy one `<li class="tool">…</li>` block and change the link to `/<tool-name>/`, the name, description and URL label.
3. Push to `main`.

A tool that needs a server goes in `apps/<tool-name>/` and gets a prefix in `run_worker_first` (wrangler.jsonc) and a route in `worker/index.js`, the way RRR Coffee does.

## Deploying

Pushes to `main` deploy automatically once the repository is connected (step 3 below). To deploy from a computer instead: install Docker (needed to build the RRR Coffee image), then `npm install` and `npx wrangler deploy`.

Checks: `npm test` (Worker routing and login check), `npm run test:rrr` (RRR Coffee app; copy the spreadsheet into `apps/rrr-coffee/` first).

## Moving from the subdomains to this setup (one time)

Today each tool has its own subdomain and its own Cloudflare project. Do these in order: the first step matters, because the Subscription Manager and JSM Receipt Maker keep their data in the browser, and a browser keeps that data separately for each web address. Data saved at `subscriptions.emmersontools.com` won't appear at `emmersontools.com/subscriptions` until you move it.

1. **Back up browser data from the old addresses**, on every browser and device you use:
   - Subscription Manager at `subscriptions.emmersontools.com`: use **Download backup**.
   - JSM Receipt Maker at `jsmreceiptmaker.emmersontools.com`: use **Back up log**.
   RRR Coffee needs nothing: its spreadsheet is in the R2 bucket `rrr-coffee-inventory`, which this Worker uses too.
2. **Plan.** RRR Coffee's container needs the **Workers Paid** plan, which you already have for `rrr-coffee-webapp`.
3. **Create the Worker from this repository.** Cloudflare dashboard → **Workers & Pages** → **Create** → **Import a repository** → pick this repo. Name it `emmersontools`, root directory `/`, deploy command `npx wrangler deploy`. It builds the RRR Coffee image and deploys everything. It will be at `emmersontools.<your-subdomain>.workers.dev` until step 6.
4. **Login for RRR Coffee (Cloudflare Access).** **Zero Trust → Access → Applications → Add an application → Self-hosted.** Add the public hostname `emmersontools.com` with path `rrr-coffee` (this covers everything under it). Set the policy to **Allow** only your own email address. Copy the application's **Audience (AUD) tag**. Your team domain looks like `yourteam.cloudflareaccess.com` (Zero Trust → Settings).
5. **Secrets.** Worker `emmersontools` → **Settings → Variables and Secrets**, add as type **Secret**:
   - `ACCESS_TEAM_DOMAIN`: the team domain
   - `ACCESS_AUD`: the Audience tag from step 4 (the new application's tag, not the old `workers.dev` one)
   - Optional: `SHOPIFY_STORE`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`, copied from the old `rrr-coffee-webapp` Worker if you set them there. Settings entered inside the app are already in R2 and carry over.
   Until both Access secrets are set, `/rrr-coffee/` shows "Login not set up yet" and lets no one in.
6. **Move the domain.** Open the old homepage Pages project (from `emmersontools-home`) → **Custom domains** → remove `emmersontools.com` (and `www` if used). Then Worker `emmersontools` → **Settings → Domains & Routes → Add → Custom domain** → `emmersontools.com` (and `www.emmersontools.com` if wanted).
7. **Check:** open https://emmersontools.com/, each tool from the homepage, and sign in at https://emmersontools.com/rrr-coffee/.
8. **Restore browser data** at the new addresses: **Restore backup** in the Subscription Manager, **Restore / import** in JSM Receipt Maker, using the backups from step 1.
9. **Retire the old projects.**
   - Delete the old `rrr-coffee-webapp` Worker. Do this: it uses the same R2 bucket, and two copies of RRR Coffee running at once could overwrite each other's spreadsheet changes. Also delete its old Access application for the `workers.dev` address.
   - Remove the `subscriptions`, `jsmreceiptmaker` and `rrr-coffee-webapp` custom domains from their old projects, then delete those projects.
   - Optional: keep old bookmarks working with **Rules → Redirect Rules** in the `emmersontools.com` zone. One rule per subdomain, for example: when hostname equals `subscriptions.emmersontools.com`, dynamic redirect to `concat("https://emmersontools.com/subscriptions", http.request.uri.path)` with status 301. Same for `jsmreceiptmaker` → `/jsm-receipt-maker` and `rrr-coffee-webapp` → `/rrr-coffee`. Each subdomain needs a proxied DNS record (orange cloud) for the rule to run, for example an `AAAA` record pointing to `100::`. Add these only after step 1, because the redirect stops you reaching the old copies' browser data.
   - Archive the old repositories (`emmersontools-subscriptions`, `emmersontools-JSMReceiptMaker`, `rrr-coffee-webapp`) on GitHub once everything works.

## Subscription Manager

A single self-contained page (`public/subscriptions/index.html`) with no build step and no server. All data stays in the visitor's browser (localStorage key `emmersontools.subscriptions.v1`).

Features: dashboard with monthly, yearly and next-30-days totals and a budget meter; categories, search, filters and sorting; alerts for renewals, trials ending, price increases, duplicates, overlaps, low usage and going over budget; manage/cancel links; CSV statement import that finds recurring charges; month calendar and `.ics` export; insights and a 12-month projection; shared and family plans with settle-up; Personal/Business workspaces with seats and notice periods; cheaper-plan switching; cancellation and negotiation assistants; savings tracker; JSON backup/restore and CSV export.

Not built yet (needs a backend): bank linking, email receipt scanning, push notifications, acting on the user's behalf, virtual cards, syncing across devices.

## JSM Receipt Maker

PDF sales and purchase receipts for Just Shoot Me, LLC, with a receipt log kept in the browser. See [apps/jsm-receipt-maker/README.md](apps/jsm-receipt-maker/README.md).

## Coffee Chart Builder

A single self-contained page (`public/coffee-chart-builder/index.html`) with no build step and no server. Pick **Cupping Score** (radar chart of 10 categories scored 0–10, plus Cupper's Correction up to 10 and a final score) or **Flavor Wheel** (12 flavor wedges scored 0–5 in half-point steps), enter the coffee name, and download a 1080×1080 PNG. Nothing is saved; the font (Satoshi) loads from Fontshare.

## RRR Coffee online

Rainy Rooster Roasting inventory: the Python app in `apps/rrr-coffee/`, run in a Cloudflare container behind Cloudflare Access, with the spreadsheet, settings and backups kept in the private R2 bucket `rrr-coffee-inventory`. See [apps/rrr-coffee/README.md](apps/rrr-coffee/README.md) for what it does and how to run it on a computer.

```
browser ──► Cloudflare Access (email login, emmersontools.com/rrr-coffee)
        ──► Worker (worker/index.js): checks the login again, strips /rrr-coffee, sends the request on
        ──► Container: apps/rrr-coffee/app.py, exactly as it runs on a computer
                └── spreadsheet, settings.json and backups ──► R2 bucket "rrr-coffee-inventory"
```

- Each change is saved to R2 before the app says it's saved. The container's own disk is wiped whenever it sleeps.
- To edit in Excel: **Settings → Download the spreadsheet**, edit, then **Upload and replace**. The app backs up the old copy first.
- Every request goes to one container, so there's only ever one copy of the spreadsheet being edited.
- The container sleeps after 15 minutes without use; the first load after that takes a few seconds.

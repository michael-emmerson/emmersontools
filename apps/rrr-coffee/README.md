# RRR Coffee Inventory

A small app that runs on your own computer and opens in your browser. It does what the iPhone app did, but your spreadsheet stays the master copy: every change you make in the app is saved straight into **RRR Coffee Inventory Master.xlsx**. Changes only go to Shopify after you review and approve them on the app's **Shopify** tab.

## What it does

- **Beans**: every column of the spreadsheet. You type in Origin, Varietal, Provider, Arrival Date, Cultivar, Tasting Notes, Cupping Score, Roast Level, Lbs (pounds received), Cost per lb, SKU and UPC. The app shows the sheet's own formulas live as you type: Label Color, Diff to Ave (cost − 8.50), MSRP tiers, COGS (cost + 2.00) and Margin. Tick **Set MSRP manually** to type a price instead, the way the Emerald Tolima and Sudan Rume rows already do.
- **Usage**: log pounds used with a date and a note. Lbs Used and Lbs Left update for each bean.
- **Shopify**: compares the spreadsheet with your store and lists the differences: price (MSRP), missing SKUs, stock counts you type in, and beans that aren't on Shopify yet (which it can create as hidden **draft** products). Nothing is sent until you tick a change, press **Push**, and confirm.
- **Settings**: where the spreadsheet lives, the 8.50 average cost and $2.00 COGS adder, and the Shopify connection.

## Starting it

You need **Python 3.9 or newer** (Macs with recent macOS have it; otherwise get it from python.org. On Windows, tick "Add python.exe to PATH" when installing).

1. Put this folder anywhere on your computer, for example in Documents.
2. Give the app your spreadsheet, in any of these ways:
   - **Upload it in the app.** If the app can't find a spreadsheet when it starts, it shows a **Load your inventory spreadsheet** screen: choose your .xlsx and click **Load spreadsheet**. To swap in a different version later, use **Settings → Load a different spreadsheet**; the current file is copied to the `backups` folder before it's replaced.
   - Put it in this folder (`apps/rrr-coffee`) as `RRR Coffee Inventory Master.xlsx`. The spreadsheet is business data, so it is not kept in the repository (it is listed in `.gitignore`).
   - Point the app at wherever your file lives in **Settings → File location**.

   An uploaded file is checked before anything is replaced: the first sheet must have the header row Origin, Varietal, Provider, Arrival Date, Cultivar, Tasting Notes, Cupping Score, Roast Level, Label Color by Roast Level, Lbs, Cost per lb, Diff to Ave, MSRP, COGS, Margin, SKU, UPC in columns A to Q, columns R and S must be empty or the app's own Lbs Used / Lbs Remaining, and no two beans can share a Varietal. If something's off, the app says what to fix and leaves your current spreadsheet alone.
3. Start it:
   - **Mac**: double-click `start.command`. If macOS says it can't be opened because it's from an unidentified developer, right-click it, choose **Open**, then **Open** again.
   - **Windows**: double-click `start.bat`.
   - **Or** in a terminal: `pip install openpyxl` then `python3 app.py`.
4. Your browser opens at **http://localhost:8765**. Keep the black terminal window open while you use the app; close it to stop the app.

The first start takes a few seconds while it installs its one dependency (openpyxl) into a private `.venv` folder.

The app only listens on your own computer (localhost), so nobody else on your network can reach it.

Online, the same app runs at **https://emmersontools.com/rrr-coffee/** behind the Cloudflare Access email login that protects all of emmersontools.com, with the spreadsheet kept in Cloudflare R2. See the **RRR Coffee online** section of the [main README](../../README.md).

## How it changes the spreadsheet

- Every save goes directly into the .xlsx file. Before each save the app copies the file into a `backups` folder next to it (it keeps the last 50), so you can always go back.
- Your existing columns, formulas, colours and formatting are kept. New beans go into the next empty row with the same formulas as the rows above.
- The first time you save, the app adds:
  - two columns on the main sheet, **R Lbs Used** and **S Lbs Remaining**, as formulas;
  - a **Usage Log** sheet (one row per usage entry; the R column adds them up by Varietal);
  - a **Shopify Sync Log** sheet the first time you push to Shopify (what changed, from, to, and whether it worked).
- Changing the average cost or COGS adder in Settings rewrites the L and N formulas on every row, so the sheet and the app always agree.
- Close the spreadsheet in Excel or Numbers while the app is saving. On Windows Excel locks the file and the app will tell you to close it. On a Mac, Excel doesn't lock it, so if the file is open and you save it in Excel afterwards, Excel's copy overwrites the app's changes. You can still edit the file directly when the app isn't saving; the app re-reads it every time you come back to the browser tab.
- Excel and Numbers recalculate every formula when they open the file. The Finder/Explorer quick preview may show the formula cells blank until the file has been opened and saved once in Excel or Numbers.
- The Varietal is each bean's name in the app and in the Usage Log, so two beans can't share the same Varietal.

## Saving the spreadsheet to your Mac

The **⬇ Save spreadsheet to Mac** button at the top of every tab saves the current spreadsheet, with every change made in the app, as **RRR Coffee Inventory Master.xlsx**, the name the label generation task looks for.

- **Chrome or Edge**: a Save dialog opens. The first time, go to the folder the label task reads from and click **Save** (then **Replace** if the file is already there). The browser remembers that folder, so after that it's just Save, Replace.
- **Safari or Firefox**: it's an ordinary download into your Downloads folder. If an older copy is already there, the browser saves the new one as `RRR Coffee Inventory Master (1).xlsx` or similar, so move the old one out first, or set the browser to ask where to save downloads.

## Connecting Shopify

Shopify no longer lets stores create the old "custom apps" with a copy-and-paste access token, so the app connects through a small private app you create in Shopify's **Dev Dashboard**. It takes about five minutes and you only do it once.

1. Go to **https://dev.shopify.com/dashboard** and sign in with the same Shopify login you use for your store. (From your store admin you can also get there via **Settings → Apps → Develop apps**.)
2. Click **Create app**, name it something like `RRR Inventory`, and create it.
3. Open the app's **Versions** page and create a version. Under **Scopes**, add exactly these:
   `read_products, write_products, read_inventory, write_inventory, read_locations`
   Leave the App URL as the default, then click **Release**.
4. On the app's overview page, click **Install app**, pick your store, and click **Install**.
5. Open the app's **Settings** page and copy the **Client ID** and **Client secret**.
6. In RRR Coffee Inventory, go to **Settings → Shopify connection**, enter your store address (`your-store.myshopify.com`), paste the Client ID and Client secret, click **Save Shopify settings**, then **Test connection**. It should say "Connected to" and your store's name.

The app swaps the Client ID and secret for a short-lived access token each time it talks to Shopify (Shopify's "client credentials" sign-in, which works for stores in your own Shopify organization). The secret is saved only in `settings.json` in this folder, never inside the spreadsheet. Don't share that file. **Remove saved secrets** in Settings deletes it from the file. If you'd rather not store it at all, set the environment variables `SHOPIFY_STORE`, `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET` before starting the app.

If you already have an older custom app with an Admin API access token (`shpat_…`), you can paste that token instead under "Have an older Admin API access token?".

If Test connection says access was denied, check the app is installed on the right store and that the version with the scopes above was released (if you added scopes later, approve them in your Shopify admin).

## Reviewing and pushing to Shopify

Open the **Shopify** tab. For each bean you'll see the Shopify product it's matched to, both prices, pounds left, and Shopify's stock count.

- **Matching**: first by SKU, then by name. Most of your Shopify products have no SKU yet, so many match by name. If a match is wrong or missing, use the drop-down under the product name to pick the right one (or "Not on Shopify"); the app remembers your choice. Pushing the **SKU** change once fills the SKU into Shopify, after which matching is exact.
- **Price**: shown when the sheet's MSRP differs from the Shopify price.
- **Stock**: Shopify's numbers look like bag counts, not pounds, so the app never guesses a stock count unless you tell it how. Either type the count into **Set stock to**, or fill in **Green lbs per bag** in Settings and the app will suggest `lbs left ÷ lbs per bag` for each bean. Each one still needs your tick.
- **Create draft product**: for beans with no Shopify product. It's created hidden (draft) with the MSRP and SKU, so you can add photos and a description and publish it in Shopify yourself.
- Products in Shopify that aren't in the spreadsheet are listed at the bottom and are never touched.

Tick the changes you want, press **Push approved changes to Shopify**, check the list, and confirm. Right before pushing, the app re-reads Shopify. If anything changed there since you looked (a price edited, a sale changing stock), that change is skipped and you're asked to review again, so it never overwrites something it didn't show you. Every push is recorded in the spreadsheet's **Shopify Sync Log** sheet.

## Files

```
app.py               the web server (python3 app.py --help for options)
rrr/formulas.py      the spreadsheet formulas (I, L, M, N, O), ported from the iPhone app
rrr/workbook.py      reads and writes the .xlsx
rrr/shopify.py       Shopify connection, matching, review list and push
static/              the web page
tests/test_app.py    tests (python3 -m unittest discover -s tests)
start.command / start.bat   double-click launchers
settings.json        created on first save: spreadsheet location, Shopify credentials, product links
```

## Tests

Copy `RRR Coffee Inventory Master.xlsx` into this folder first (the tests skip without it), then `python3 -m unittest discover -s tests -v` runs 19 tests on a temporary copy of the spreadsheet. They check that the app reads every bean with the same values Excel saved, that adding, editing, renaming, deleting and logging usage write the right cells and formulas, and that the whole Shopify review-and-push flow works against a fake Shopify server (never your real store), including skipping changes that went stale. They also check that uploading a spreadsheet accepts the master file, rejects files with the wrong layout or duplicate Varietals with a reason, asks before replacing, and backs up the old file. With `pip install formulas` installed, one more test recalculates the saved workbook the way Excel would and checks every formula cell against the app's numbers.

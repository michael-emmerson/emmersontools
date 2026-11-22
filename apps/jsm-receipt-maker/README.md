# Just Shoot Me Receipt Maker

A small web app for **Just Shoot Me, LLC** that builds receipts as PDFs and keeps a log of them.

- **Sales receipts** (numbered `JSM<yy>-####`): firearms and accessories (description, serial #, qty, price), services (classes, gunsmithing, etc.), a **$50 transfer fee per firearm** (added automatically, can be unticked; also available as a standalone line for guns bought elsewhere), and **$25 shipping per item** with an optional override total.
- **Purchase receipts** (numbered `JSMP<yy>-####`): for buying firearms/accessories from a customer, with seller details, a seller certification and signature lines. No transfer or shipping fees.

## Running it

Online: **https://emmersontools.com/jsm-receipt-maker/**. The page itself is `public/jsm-receipt-maker/index.html`, deployed with the rest of the site.

On a Mac without internet: double-click **`Start Receipt Maker.command`** in this folder. A Terminal window opens and the app opens in your browser at http://127.0.0.1:8765. Keep the Terminal window open while you work. Or run `python3 server.py` here. It serves the files from `public/jsm-receipt-maker/` and needs only Python 3.

## Where receipts go

**Download PDF** has the browser download the PDF (usually to Downloads), named like `Reed Harmon Receipt 10-27-2026 JSM26-0001.pdf`. Receipts are customer data and are excluded from this repo via `.gitignore`.

## Receipt log

Every downloaded receipt is recorded in the **Receipt log** panel (number, type, name, date, total) along with the receipt's full contents, so it can be reopened, edited, and downloaded again. Receipt numbers are assigned from the log (next number this year for `JSM` or `JSMP`) and can still be typed over.

- The log lives in the browser's `localStorage` (key `jsmReceiptLog.v1`), per browser and per site address. The local copy (127.0.0.1), the old jsmreceiptmaker.emmersontools.com copy and emmersontools.com/jsm-receipt-maker each keep separate logs: use **Back up log** on one and **Restore / import** on the other to move it.
- **Back up log** downloads a `.json` backup; **Restore / import** loads a backup into any browser, merging by receipt number.
- **Restore / import** also accepts receipt PDFs: it reads the receipt number, name and date from each file name, which is how receipts made before the log existed get added so numbering continues correctly.
- **Export CSV** downloads the log as a spreadsheet.

## Configuration

Fee amounts and business details are set near the top of the `<script>` section in `public/jsm-receipt-maker/index.html` (`TRANSFER_FEE`, `SHIPPING_FEE`, `BUSINESS`). See `How to use.txt` for the full walkthrough.

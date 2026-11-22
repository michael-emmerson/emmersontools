"""End-to-end tests: spreadsheet read/write, formula results and the Shopify approve-and-push flow
(against a fake Shopify server, never a real store).

Run:  python3 -m unittest discover -s tests -v
Optional: pip install formulas   (also recalculates the saved workbook like Excel would and compares)
"""
import base64
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import app  # noqa: E402
from rrr import cloud, formulas, shopify  # noqa: E402
from rrr.workbook import Inventory, SheetError, check_upload, install_upload  # noqa: E402

SOURCE = os.path.join(ROOT, "RRR Coffee Inventory Master.xlsx")
if not os.path.exists(SOURCE):
    # The spreadsheet is business data, so it isn't kept in the repository. Copy it here to run the tests.
    raise unittest.SkipTest(f"Copy 'RRR Coffee Inventory Master.xlsx' into {ROOT} to run these tests.")


class FakeShopify:
    """Just enough of the Admin API to exercise the app: OAuth client credentials + 5 GraphQL operations."""

    def __init__(self):
        self.products = [
            self._product(1, "Mexico - Organic Chiapas El Paraiso", "", 18.99, 16),
            self._product(2, "Kenya Nyeri Gichathaini AA", "", 20.99, 5),
            self._product(3, "Colombia - Emerald Bean Single Plot Collection Gesha", "COL-EMGESHA", 34.99, 37),
            self._product(4, "Sample Beans", "", 3.50, 999),
        ]
        self.calls, self.next_id = [], 100
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.endswith("/admin/oauth/access_token"):
                    ok = body.get("grant_type") == "client_credentials" and body.get("client_secret") == "secret"
                    return self._reply(200 if ok else 401, {"access_token": "shpat_test", "expires_in": 86399} if ok else {"errors": "bad"})
                if self.headers.get("X-Shopify-Access-Token") != "shpat_test":
                    return self._reply(401, {"errors": "[API] Invalid API key or access token"})
                return self._reply(200, {"data": fake.handle(body["query"], body.get("variables") or {})})

            def _reply(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    @staticmethod
    def _product(n, title, sku, price, stock):
        return {"id": f"gid://shopify/Product/{n}", "title": title, "status": "ACTIVE", "variants": {"nodes": [{
            "id": f"gid://shopify/ProductVariant/{n}", "title": "Default Title", "sku": sku, "price": f"{price:.2f}",
            "inventoryQuantity": stock, "inventoryItem": {"id": f"gid://shopify/InventoryItem/{n}", "tracked": True,
                "inventoryLevels": {"nodes": [{"location": {"id": "gid://shopify/Location/1", "name": "Shop"},
                                               "quantities": [{"name": "available", "quantity": stock}]}]}}}]}}

    def variant(self, vid):
        for p in self.products:
            for v in p["variants"]["nodes"]:
                if v["id"] == vid:
                    return p, v
        raise KeyError(vid)

    def handle(self, query, variables):
        op = re.search(r"(query|mutation)\s+(\w+)?", query).group(2) or "shop"
        self.calls.append((op, variables))
        if op == "shop":
            return {"shop": {"name": "Test Roastery", "myshopifyDomain": "test.myshopify.com", "currencyCode": "USD"}}
        if op == "Products":
            return {"products": {"nodes": self.products, "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if op == "UpdateVariants":
            for upd in variables["variants"]:
                _, v = self.variant(upd["id"])
                if "price" in upd:
                    v["price"] = upd["price"]
                if "inventoryItem" in upd:
                    v["sku"] = upd["inventoryItem"]["sku"]
            return {"productVariantsBulkUpdate": {"productVariants": [], "userErrors": []}}
        if op == "SetStock":
            q = variables["input"]["quantities"][0]
            _, v = self.variant(q["inventoryItemId"].replace("InventoryItem", "ProductVariant"))
            level = v["inventoryItem"]["inventoryLevels"]["nodes"][0]["quantities"][0]
            if q["changeFromQuantity"] != level["quantity"]:
                return {"inventorySetQuantities": {"inventoryAdjustmentGroup": None, "userErrors": [
                    {"field": ["changeFromQuantity"], "message": "The quantity changed", "code": "CHANGE_FROM_QUANTITY_STALE"}]}}
            level["quantity"] = q["quantity"]
            return {"inventorySetQuantities": {"inventoryAdjustmentGroup": {"id": "x"}, "userErrors": []}}
        if op == "CreateProduct":
            self.next_id += 1
            p = self._product(self.next_id, variables["product"]["title"], "", 0, 0)
            p["status"] = variables["product"]["status"]
            self.products.append(p)
            return {"productCreate": {"product": {"id": p["id"], "title": p["title"], "variants": {"nodes": [
                {"id": p["variants"]["nodes"][0]["id"]}]}}, "userErrors": []}}
        raise AssertionError(op)

    def close(self):
        self.server.shutdown()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "Inventory.xlsx")
        shutil.copy(SOURCE, self.path)
        self.inv = Inventory(self.path)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def bean(self, name):
        return next(b for b in self.inv.snapshot()["beans"] if b["varietal"] == name)


class WorkbookTests(Base):
    def test_reads_the_12_beans_with_the_sheets_own_values(self):
        import openpyxl
        snap = self.inv.snapshot()
        self.assertEqual(len(snap["beans"]), 12)
        self.assertEqual(snap["settings"], {"average_cost": 8.5, "cogs_adder": 2.0})
        cached = openpyxl.load_workbook(SOURCE, data_only=True).worksheets[0]
        for b in snap["beans"]:
            r = b["row"]
            self.assertEqual(b["label_color"], cached[f"I{r}"].value)
            for key, col in (("diff_to_average", "L"), ("msrp", "M"), ("cogs", "N"), ("margin", "O")):
                self.assertAlmostEqual(b[key], cached[f"{col}{r}"].value, 9, f"row {r} {key}")
        self.assertTrue(self.bean("Emerald Tolima")["msrp_manual"])

    def test_add_edit_usage_delete_round_trip(self):
        row = self.inv.add_bean({"origin": "Test", "varietal": "Test Bean", "roast_level": "Dark", "lbs": "12",
                                 "cost_per_lb": "10.25", "arrival_date": "2026-10-01", "sku": "TST-1", "upc": "0123"})
        self.assertEqual(row, 14)
        b = self.bean("Test Bean")
        self.assertEqual((b["msrp"], b["label_color"], b["upc"]), (24.99, "#CAE0F0", "0123"))
        self.inv.log_usage(row, "Test Bean", "2.5", "2026-10-02", "batch 1")
        self.inv.update_bean(row, "Test Bean", {"varietal": "Renamed", "msrp_manual": True, "msrp": "29.99"})
        b = self.bean("Renamed")
        self.assertEqual((b["lbs_used"], b["lbs_remaining"], b["msrp"], b["msrp_manual"]), (2.5, 9.5, 29.99, True))
        self.inv.update_bean(row, "Renamed", {"msrp_manual": False})
        self.assertEqual(self.bean("Renamed")["msrp"], 24.99)
        with self.assertRaises(SheetError):  # stale page
            self.inv.update_bean(row, "Test Bean", {"lbs": 1})
        with self.assertRaises(SheetError):  # duplicate name
            self.inv.update_bean(row, "Renamed", {"varietal": "Buku Abel"})
        self.inv.delete_bean(3, "Nyeri Gichathaini AA")
        snap = self.inv.snapshot()
        self.assertEqual(len(snap["beans"]), 12)
        self.assertEqual(self.bean("Renamed")["row"], 13)
        self.assertEqual(self.bean("Emerald Tolima")["msrp"], 34.99)
        self.assertTrue(os.listdir(os.path.join(self.dir, "backups")))

    def test_settings_rewrite_formulas(self):
        self.inv.update_settings(9.0, 2.5)
        snap = self.inv.snapshot()
        self.assertEqual(snap["settings"], {"average_cost": 9.0, "cogs_adder": 2.5})
        b = self.bean("Organic Chiapas El Paraiso")
        self.assertAlmostEqual(b["cogs"], 10.1)
        self.assertEqual(b["msrp"], 18.99)

    def test_saved_workbook_recalculates_to_the_apps_numbers(self):
        try:
            import formulas as engine
        except ImportError:
            self.skipTest("pip install formulas to run this check")
        row = self.inv.add_bean({"varietal": "Calc Check", "roast_level": "French", "lbs": 8, "cost_per_lb": 10.1})
        self.inv.log_usage(row, "Calc Check", 3, "2026-10-03", "")
        self.inv.log_usage(2, "Organic Chiapas El Paraiso", 1.25, "2026-10-03", "")
        self.inv.update_settings(8.6, 2.25)
        solution = engine.ExcelModel().loads(self.path).finish().calculate()
        values = {k.split("!")[-1]: v for k, v in solution.items() if "SHEET1" in k.upper() and "!" in k}

        def cell(ref):
            v = values[ref]
            v = v.value[0][0] if hasattr(v, "value") else v
            return v.item() if hasattr(v, "item") else v

        for b in self.inv.snapshot()["beans"]:
            r = b["row"]
            self.assertEqual(cell(f"I{r}"), b["label_color"])
            for key, col in (("diff_to_average", "L"), ("msrp", "M"), ("cogs", "N"), ("margin", "O"),
                             ("lbs_used", "R"), ("lbs_remaining", "S")):
                self.assertAlmostEqual(float(cell(f"{col}{r}")), b[key], 9, f"row {r} {col}")


def xlsx_bytes(edit=None):
    """The master spreadsheet as bytes, optionally changed by edit(workbook) first."""
    import openpyxl
    if edit is None:
        with open(SOURCE, "rb") as fh:
            return fh.read()
    wb = openpyxl.load_workbook(SOURCE)
    edit(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


class UploadTests(Base):
    def assertRejected(self, data, fragment, filename="Inventory.xlsx"):
        with self.assertRaises(SheetError) as ctx:
            check_upload(data, filename)
        self.assertIn(fragment, str(ctx.exception))

    def test_master_spreadsheet_is_accepted(self):
        summary = check_upload(xlsx_bytes(), "RRR Coffee Inventory Master.xlsx")
        self.assertEqual((summary["sheet"], summary["beans"], summary["warnings"]), ("Sheet1", 12, []))

    def test_wrong_files_are_rejected_with_a_reason(self):
        self.assertRejected(b"", "isn't an .xlsx", "Inventory.xls")
        self.assertRejected(b"a,b\n", "isn't an .xlsx", "Inventory.csv")
        self.assertRejected(b"not a zip", "Couldn't read that file")
        self.assertRejected(b"", "empty")

        def other_columns(wb):
            wb.worksheets[0]["K1"].value = "Price"
        self.assertRejected(xlsx_bytes(other_columns), "K1 should be “Cost per lb”")

        def duplicate(wb):
            wb.worksheets[0]["B3"].value = "buku abel "
        self.assertRejected(xlsx_bytes(duplicate), "same Varietal")

        def busy_column_r(wb):
            wb.worksheets[0]["R1"].value = "Notes"
        self.assertRejected(xlsx_bytes(busy_column_r), "Column R")

        def inventory_second(wb):
            wb.create_sheet("Cover", 0)
        self.assertRejected(xlsx_bytes(inventory_second), "on the “Sheet1” sheet")

    def test_header_spacing_and_case_are_forgiven_and_blank_varietals_warned(self):
        def edit(wb):
            ws = wb.worksheets[0]
            ws["D1"].value = "arrival  date"
            ws["B4"].value = None
        summary = check_upload(xlsx_bytes(edit))
        self.assertEqual(summary["beans"], 12)
        self.assertIn("no Varietal", summary["warnings"][0])

    def test_install_backs_up_the_old_file_and_writes_the_upload_unchanged(self):
        self.inv.log_usage(2, "Organic Chiapas El Paraiso", 1, "2026-10-01", "")
        before = read(self.path)
        upload = xlsx_bytes()
        result = install_upload(self.path, upload, "new.xlsx")
        self.assertEqual(read(self.path), upload)
        self.assertEqual(read(result["backup"]), before)
        self.assertEqual(self.bean("Organic Chiapas El Paraiso")["lbs_used"], 0)
        with self.assertRaises(SheetError):
            install_upload(self.path, b"junk", "junk.xlsx")
        self.assertEqual(read(self.path), upload)  # a bad upload never touches the file


class ShopifyTests(Base):
    def setUp(self):
        super().setUp()
        self.fake = FakeShopify()
        self.client = shopify.ShopifyClient("test", client_id="id", client_secret="secret", base_url=self.fake.url)
        self.prefs = {"links": {}, "lbs_per_bag": None, "location_id": ""}

    def tearDown(self):
        self.fake.close()
        super().tearDown()

    def review(self):
        return shopify.build_review(self.inv.snapshot()["beans"], self.client.catalog(), self.prefs)

    def row(self, review, name):
        return next(r for r in review["rows"] if r["varietal"] == name)

    def test_review_matches_by_sku_then_name_and_proposes_changes(self):
        rev = self.review()
        self.assertEqual(self.row(rev, "Emerald Tolima")["match"], "SKU")
        chiapas = self.row(rev, "Organic Chiapas El Paraiso")
        self.assertEqual(chiapas["match"], "name")
        self.assertEqual([c["type"] for c in chiapas["changes"]], ["sku"])  # price matches, SKU missing in Shopify
        sudan = self.row(rev, "Granja La Esperanza Sudan Rume")
        self.assertEqual([c["type"] for c in sudan["changes"]], ["create"])
        self.assertIn("Sample Beans", [e["title"] for e in rev["extras"]])
        self.assertFalse(any(c["type"] == "stock" for r in rev["rows"] for c in r["changes"]))  # off by default
        self.assertEqual(self.fake.calls.count(("UpdateVariants", {})), 0)

    def test_nothing_is_written_without_approval_and_only_approved_items_are(self):
        self.inv.update_bean(2, "Organic Chiapas El Paraiso", {"cost_per_lb": 10.5})  # MSRP 18.99 -> 24.99
        rev = self.review()
        chiapas = self.row(rev, "Organic Chiapas El Paraiso")
        price = next(c for c in chiapas["changes"] if c["type"] == "price")
        self.assertEqual((price["from"], price["to"]), (18.99, 24.99))
        self.assertFalse([c for c in self.fake.calls if c[0] != "Products"])
        beans = self.inv.snapshot()["beans"]
        results, _ = shopify.push(self.client, beans, self.prefs, [{"id": price["id"], "from": 18.99}])
        self.assertTrue(all(r["ok"] for r in results), results)
        self.assertEqual(self.fake.variant("gid://shopify/ProductVariant/1")[1]["price"], "24.99")
        self.assertEqual(self.fake.variant("gid://shopify/ProductVariant/1")[1]["sku"], "")  # SKU not approved

    def test_stale_review_is_skipped(self):
        self.inv.update_bean(2, "Organic Chiapas El Paraiso", {"cost_per_lb": 10.5})
        rev = self.review()
        price = next(c for c in self.row(rev, "Organic Chiapas El Paraiso")["changes"] if c["type"] == "price")
        self.fake.variant("gid://shopify/ProductVariant/1")[1]["price"] = "21.00"  # someone edits in Shopify
        results, _ = shopify.push(self.client, self.inv.snapshot()["beans"], self.prefs, [{"id": price["id"], "from": 18.99}])
        self.assertFalse(results[0]["ok"])
        self.assertEqual(self.fake.variant("gid://shopify/ProductVariant/1")[1]["price"], "21.00")

    def test_manual_stock_suggested_stock_and_create(self):
        beans = self.inv.snapshot()["beans"]
        results, links = shopify.push(self.client, beans, self.prefs, [
            {"id": "stock|gid://shopify/ProductVariant/3", "from": 37, "to": "30"},
            {"id": "create|Granja La Esperanza Sudan Rume", "from": None},
        ])
        self.assertTrue(all(r["ok"] for r in results), results)
        self.assertEqual(self.fake.variant("gid://shopify/ProductVariant/3")[1]["inventoryItem"]["inventoryLevels"]
                         ["nodes"][0]["quantities"][0]["quantity"], 30)
        created_variant = links["Granja La Esperanza Sudan Rume"]
        product, variant = self.fake.variant(created_variant)
        self.assertEqual((product["status"], variant["price"]), ("DRAFT", "45.99"))
        self.prefs["links"].update(links)
        self.prefs["lbs_per_bag"] = 0.75
        sudan = self.row(self.review(), "Granja La Esperanza Sudan Rume")
        self.assertEqual(sudan["match"], "linked by you")
        stock = next(c for c in sudan["changes"] if c["type"] == "stock")
        self.assertEqual(stock["to"], 12)  # 9 lbs / 0.75


class ServerTests(Base):
    """The HTTP layer: the page loads, and requests from other sites are refused."""

    def setUp(self):
        super().setUp()
        settings_path = os.path.join(self.dir, "settings.json")
        with open(settings_path, "w") as fh:
            json.dump({"spreadsheet": self.path}, fh)
        app.Handler.settings = app.Settings(settings_path)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://localhost:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        super().tearDown()

    def call(self, path, body=None, headers=None):
        hdrs = {"Content-Type": "application/json", "X-RRR-App": "1"} if body is not None else {}
        hdrs.update(headers or {})
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers=hdrs, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_state_and_static(self):
        status, body = self.call("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["beans"]), 12)
        self.assertEqual(self.call("/")[0], 200)
        self.assertEqual(self.call("/../app.py")[0], 404)

    def test_cross_site_posts_are_refused(self):
        self.assertEqual(self.call("/api/beans/delete", {"row": 2, "expect": "x"}, {"X-RRR-App": ""})[0], 403)
        self.assertEqual(self.call("/api/state", None, {"Host": "evil.example"})[0], 403)

    def test_spreadsheet_download(self):
        req = urllib.request.Request(self.base + "/api/spreadsheet/download")
        with urllib.request.urlopen(req) as resp:
            self.assertIn("attachment", resp.headers["Content-Disposition"])
            self.assertEqual(resp.read(), open(self.path, "rb").read())

    def test_usage_via_http_and_secrets_hidden(self):
        status, _ = self.call("/api/usage/add", {"row": 2, "expect": "Organic Chiapas El Paraiso", "lbs": 1.5})
        self.assertEqual(status, 200)
        self.assertEqual(self.bean("Organic Chiapas El Paraiso")["lbs_used"], 1.5)
        self.call("/api/settings", {"shopify": {"shop": "x", "client_secret": "topsecret"}})
        _, body = self.call("/api/state")
        self.assertNotIn(b"topsecret", body)
        self.assertTrue(json.loads(body)["app_settings"]["shopify"]["client_secret_set"])


    def upload(self, data, replace=None, filename="Upload.xlsx"):
        body = {"filename": filename, "data": base64.b64encode(data).decode()}
        if replace is not None:
            body["replace"] = replace
        status, raw = self.call("/api/spreadsheet/upload", body)
        return status, json.loads(raw)

    def test_first_run_without_a_spreadsheet_loads_an_upload(self):
        os.remove(self.path)
        _, body = self.call("/api/state")
        self.assertTrue(json.loads(body)["missing"])
        status, res = self.upload(xlsx_bytes())
        self.assertEqual((status, res["beans"], res["backup"]), (200, 12, None))
        _, body = self.call("/api/state")
        state = json.loads(body)
        self.assertNotIn("error", state)
        self.assertEqual(len(state["beans"]), 12)

    def test_replacing_needs_confirmation_and_keeps_a_backup(self):
        def fewer(wb):
            wb.worksheets[0].delete_rows(3, 5)
        status, res = self.upload(xlsx_bytes(fewer))
        self.assertEqual(status, 409)
        self.assertIn("already a spreadsheet", res["error"])
        self.assertEqual(len(self.inv.snapshot()["beans"]), 12)
        status, res = self.upload(xlsx_bytes(fewer), replace=True)
        self.assertEqual((status, res["beans"]), (200, 7))
        self.assertEqual(len(self.inv.snapshot()["beans"]), 7)
        self.assertTrue(os.path.exists(res["backup"]))
        self.assertEqual(len(Inventory(res["backup"]).snapshot()["beans"]), 12)

    def test_bad_upload_is_explained_before_asking_to_replace(self):
        status, res = self.upload(b"PK not really", filename="x.xlsx")
        self.assertEqual(status, 400)
        self.assertIn("Couldn't read that file", res["error"])
        status, res = self.upload(b"", filename="x.numbers", replace=True)
        self.assertIn("isn't an .xlsx", res["error"])
        self.assertEqual(self.call("/api/spreadsheet/upload", {"data": "@@@"})[0], 400)

    def test_upload_goes_next_to_the_app_if_the_saved_folder_is_gone(self):
        target = os.path.join(self.dir, "fallback.xlsx")
        app.Handler.settings.save({"spreadsheet": os.path.join(self.dir, "gone", "x.xlsx")})
        original = app.DEFAULT_SHEET
        app.DEFAULT_SHEET = target
        try:
            status, res = self.upload(xlsx_bytes())
        finally:
            app.DEFAULT_SHEET = original
        self.assertEqual((status, res["path"]), (200, target))
        self.assertEqual(app.Handler.settings.load()["spreadsheet"], target)


class FakeR2:
    """Stands in for the Worker's http://r2.internal storage endpoint."""

    def __init__(self):
        self.objects, self.down = {}, False
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, status, data=b""):
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                key = urllib.parse.unquote(self.path[1:])
                if fake.down:
                    return self._reply(500)
                return self._reply(200, fake.objects[key]) if key in fake.objects else self._reply(404)

            def do_PUT(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                if fake.down:
                    return self._reply(500)
                fake.objects[urllib.parse.unquote(self.path[1:])] = body
                self._reply(200)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"


class CloudTests(unittest.TestCase):
    """Cloud mode: files live in a throwaway data folder and every change is mirrored to R2 before the
    app says it's saved, so a restarted container picks up where it left off."""

    def setUp(self):
        self.r2 = FakeR2()
        self.dirs = []
        self.start()

    def start(self):
        if getattr(self, "server", None):
            self.server.shutdown()
            self.server.server_close()
        data_dir = tempfile.mkdtemp()  # a fresh, empty disk, like a restarted container
        self.dirs.append(data_dir)
        app.Handler.settings = app.Settings(app.enable_cloud(self.r2.url, data_dir))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.r2.server.shutdown()
        self.r2.server.server_close()
        app.CLOUD = None
        for d in self.dirs:
            shutil.rmtree(d)

    def call(self, path, body=None):
        hdrs = {"Content-Type": "application/json", "X-RRR-App": "1", "Host": "rrr.example.com"}
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers=hdrs, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_first_run_upload_survives_a_restart(self):
        status, state = self.call("/api/state")
        self.assertEqual(status, 200)  # any host is fine: the Worker checks the login
        self.assertTrue(state["missing"])
        self.assertTrue(state["app_settings"]["cloud"])
        upload = {"filename": "x.xlsx", "data": base64.b64encode(xlsx_bytes()).decode()}
        self.assertEqual(self.call("/api/spreadsheet/upload", upload)[0], 200)
        self.assertIn(app.SHEET_NAME, self.r2.objects)
        self.assertEqual(self.call("/api/usage/add", {"row": 2, "expect": "Organic Chiapas El Paraiso", "lbs": 2})[0], 200)
        self.assertTrue(any(k.startswith("backups/") for k in self.r2.objects))

        self.start()
        _, state = self.call("/api/state")
        self.assertEqual(len(state["beans"]), 12)
        self.assertEqual(state["usage"][0]["lbs"], 2)

    def test_settings_survive_and_the_file_location_cant_be_changed(self):
        self.call("/api/spreadsheet/upload", {"filename": "x.xlsx", "data": base64.b64encode(xlsx_bytes()).decode()})
        status, _ = self.call("/api/settings", {"spreadsheet": "/etc/passwd.xlsx", "lbs_per_bag": 5,
                                                "average_cost": 8.5, "cogs_adder": 2})
        self.assertEqual(status, 200)
        self.start()
        _, state = self.call("/api/state")
        self.assertEqual(state["app_settings"]["lbs_per_bag"], 5)
        self.assertEqual(os.path.basename(state["app_settings"]["spreadsheet"]), app.SHEET_NAME)
        self.assertTrue(state["app_settings"]["spreadsheet"].startswith(self.dirs[-1]))

    def test_a_failed_save_to_storage_is_reported(self):
        self.call("/api/spreadsheet/upload", {"filename": "x.xlsx", "data": base64.b64encode(xlsx_bytes()).decode()})
        self.r2.down = True
        status, res = self.call("/api/usage/add", {"row": 2, "expect": "Organic Chiapas El Paraiso", "lbs": 1})
        self.assertEqual(status, 503)
        self.assertIn("couldn't be saved to Cloudflare storage", res["error"])
        self.r2.down = False  # the next change catches up on everything still unsaved
        self.assertEqual(self.call("/api/usage/add", {"row": 2, "expect": "Organic Chiapas El Paraiso", "lbs": 1})[0], 200)
        self.start()
        _, state = self.call("/api/state")
        self.assertEqual(len(state["usage"]), 2)

    def test_storage_outage_at_start_stops_the_app(self):
        self.r2.down = True
        with self.assertRaises(cloud.StorageError):
            app.enable_cloud(self.r2.url, self.dirs[-1])


if __name__ == "__main__":
    unittest.main()

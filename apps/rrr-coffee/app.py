"""RRR Coffee Inventory: a local web app that reads and writes the inventory spreadsheet and pushes
approved changes to Shopify.

Run:  python3 app.py            then open http://localhost:8765
Options: --port 8765  --no-browser  --settings path/to/settings.json

Cloud mode (the Cloudflare Container): set RRR_STORAGE_URL and the spreadsheet, settings.json and backups
live in RRR_DATA_DIR and are mirrored to R2 (see rrr/cloud.py and CLOUDFLARE.md).
"""
import argparse
import base64
import binascii
import json
import os
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from rrr import cloud, shopify
from rrr.workbook import MAX_UPLOAD_BYTES, Inventory, SheetError, check_upload, install_upload

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
DEFAULT_SHEET = os.path.join(HERE, "RRR Coffee Inventory Master.xlsx")
SECRET_FIELDS = ("client_secret", "access_token")
SETTINGS_LOCK = threading.Lock()
MAX_BODY_BYTES = MAX_UPLOAD_BYTES * 4 // 3 + 64 * 1024  # an uploaded spreadsheet arrives base64-encoded
SHEET_NAME = "RRR Coffee Inventory Master.xlsx"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Cloud mode: None when running on someone's own computer.
CLOUD = None  # {"sheet": path, "mirror": cloud.Mirror}


class Settings:
    """App preferences in settings.json (next to app.py). The spreadsheet holds everything else."""

    def __init__(self, path):
        self.path = path

    def load(self):
        data = {}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        data.setdefault("spreadsheet", DEFAULT_SHEET)
        if CLOUD:
            data["spreadsheet"] = CLOUD["sheet"]  # in the cloud the file always lives in the data folder
        shop = data.setdefault("shopify", {})
        for key in ("shop", "client_id", "client_secret", "access_token", "location_id"):
            shop.setdefault(key, "")
        shop.setdefault("api_version", shopify.DEFAULT_API_VERSION)
        data.setdefault("lbs_per_bag", None)
        data.setdefault("links", {})
        # Environment variables win, so secrets can stay out of the file if preferred.
        for key, env in (("shop", "SHOPIFY_STORE"), ("client_id", "SHOPIFY_CLIENT_ID"),
                         ("client_secret", "SHOPIFY_CLIENT_SECRET"), ("access_token", "SHOPIFY_ACCESS_TOKEN")):
            if os.environ.get(env):
                shop[key] = os.environ[env]
        return data

    def save(self, data):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


def public_settings(data):
    shop = dict(data["shopify"])
    for key in SECRET_FIELDS:
        shop[key + "_set"] = bool(shop.pop(key))
    return {"spreadsheet": data["spreadsheet"], "shopify": shop, "lbs_per_bag": data["lbs_per_bag"],
            "cloud": bool(CLOUD)}


def client_from(data):
    s = data["shopify"]
    return shopify.ShopifyClient(s["shop"], s["access_token"], s["client_id"], s["client_secret"], s["api_version"],
                                 base_url=os.environ.get("RRR_SHOPIFY_BASE_URL"))


def prefs_from(data):
    return {"links": data["links"], "lbs_per_bag": data["lbs_per_bag"], "location_id": data["shopify"]["location_id"]}


class Handler(BaseHTTPRequestHandler):
    settings: Settings = None
    server_version = "RRRCoffee/1.0"

    def log_message(self, fmt, *args):  # quieter console
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("%s\n" % (fmt % args))

    # Helpers -----------------------------------------------------------------------------------------
    def _send(self, status, body, content_type="application/json", headers=None):
        if CLOUD and self.command == "POST":
            # Make sure every change is in R2 before saying it's saved: the container's disk doesn't last.
            try:
                CLOUD["mirror"].sync()
            except cloud.StorageError as exc:
                if status < 400:
                    status, content_type = 503, "application/json"
                    body = {"error": "The change was made, but it couldn't be saved to Cloudflare storage, so it "
                                     f"could be lost if the app restarts. Try again in a moment. ({exc})"}
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status, message):
        self._send(status, {"error": message})

    def _host_ok(self):
        if CLOUD:
            return True  # Cloudflare Access on emmersontools.com handles login
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("localhost", "127.0.0.1", "[::1]")

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise SheetError("That file is too big to be the inventory spreadsheet (over 20 MB).", 413)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def _inventory(self, data=None):
        return Inventory((data or self.settings.load())["spreadsheet"])

    # Routing -------------------------------------------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Open the app at http://localhost.")
        path = urlparse(self.path).path
        try:
            if path == "/api/state":
                data = self.settings.load()
                try:
                    snap = self._inventory(data).snapshot()
                except SheetError as exc:
                    snap = {"error": str(exc), "missing": not os.path.exists(data["spreadsheet"]),
                            "beans": [], "usage": [], "settings": {}}
                return self._send(200, {**snap, "app_settings": public_settings(data)})
            if path == "/api/shopify/review":
                return self._shopify_review()
            if path == "/api/spreadsheet/download":
                return self._download_spreadsheet()
            return self._static(path)
        except SheetError as exc:
            return self._error(exc.status, str(exc))
        except shopify.ShopifyError as exc:
            return self._error(502, str(exc))

    def do_POST(self):
        # Requests must come from this app's own page: right host, JSON body, and our custom header
        # (a page on another site can't send that header to localhost without permission we never grant).
        if not self._host_ok() or self.headers.get("X-RRR-App") != "1" \
                or "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._error(HTTPStatus.FORBIDDEN, "Request not allowed.")
        path = urlparse(self.path).path
        try:
            body = self._json_body()
            routes = {
                "/api/beans/add": self._add_bean,
                "/api/beans/update": self._update_bean,
                "/api/beans/delete": self._delete_bean,
                "/api/usage/add": self._add_usage,
                "/api/usage/delete": self._delete_usage,
                "/api/settings": self._save_settings,
                "/api/spreadsheet/upload": self._upload_spreadsheet,
                "/api/shopify/link": self._link,
                "/api/shopify/push": self._push,
                "/api/shopify/test": self._test_shopify,
            }
            if path not in routes:
                return self._error(404, "Not found")
            return routes[path](body)
        except SheetError as exc:
            return self._error(exc.status, str(exc))
        except shopify.ShopifyError as exc:
            return self._error(502, str(exc))
        except (ValueError, KeyError, TypeError) as exc:
            return self._error(400, f"Bad request: {exc}")

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        full = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
            return self._error(404, "Not found")
        types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml"}
        with open(full, "rb") as fh:
            return self._send(200, fh.read(), types.get(os.path.splitext(full)[1], "application/octet-stream"))

    # Beans and usage -----------------------------------------------------------------------------------
    def _add_bean(self, body):
        row = self._inventory().add_bean(body["bean"])
        self._send(200, {"ok": True, "row": row})

    def _update_bean(self, body):
        data = self.settings.load()
        old, new = self._inventory(data).update_bean(int(body["row"]), body["expect"], body["bean"])
        if old != new and old in data["links"]:
            with SETTINGS_LOCK:
                data = self.settings.load()
                data["links"][new] = data["links"].pop(old)
                self.settings.save(data)
        self._send(200, {"ok": True})

    def _delete_bean(self, body):
        name = self._inventory().delete_bean(int(body["row"]), body["expect"])
        with SETTINGS_LOCK:
            data = self.settings.load()
            if data["links"].pop(name, None) is not None:
                self.settings.save(data)
        self._send(200, {"ok": True})

    def _add_usage(self, body):
        self._inventory().log_usage(int(body["row"]), body["expect"], body.get("lbs"), body.get("date"), body.get("note"))
        self._send(200, {"ok": True})

    def _delete_usage(self, body):
        self._inventory().delete_usage(int(body["id"]), body["expect"])
        self._send(200, {"ok": True})

    # Settings ------------------------------------------------------------------------------------------
    def _save_settings(self, body):
        with SETTINGS_LOCK:
            data = self.settings.load()
            if "spreadsheet" in body and not CLOUD:
                path = os.path.abspath(os.path.expanduser(body["spreadsheet"].strip().strip('"')))
                if not path.lower().endswith(".xlsx"):
                    raise SheetError("The spreadsheet must be an .xlsx file.")
                if not os.path.exists(path):
                    raise SheetError(f"Can't find a file at {path}.")
                data["spreadsheet"] = path
            if "shopify" in body:
                for key, value in body["shopify"].items():
                    if key in data["shopify"] and value is not None and not (key in SECRET_FIELDS and value == ""):
                        data["shopify"][key] = value.strip() if isinstance(value, str) else value
                if body["shopify"].get("clear_secrets"):
                    for key in SECRET_FIELDS:
                        data["shopify"][key] = ""
            if "lbs_per_bag" in body:
                value = body["lbs_per_bag"]
                data["lbs_per_bag"] = float(value) if value not in (None, "") else None
                if data["lbs_per_bag"] is not None and data["lbs_per_bag"] <= 0:
                    raise SheetError("Pounds per bag must be more than 0, or blank to turn stock suggestions off.")
            self.settings.save(data)
        if "average_cost" in body or "cogs_adder" in body:
            average, adder = float(body["average_cost"]), float(body["cogs_adder"])
            self._inventory(data).update_settings(average, adder)
        self._send(200, {"ok": True})

    def _upload_spreadsheet(self, body):
        """Makes an uploaded .xlsx the app's spreadsheet. It replaces the file at the current spreadsheet
        location (backed up first); if that folder is gone, it goes next to app.py instead."""
        try:
            data = base64.b64decode(body.get("data") or "", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SheetError("The upload didn't arrive intact. Try again.") from exc
        with SETTINGS_LOCK:
            settings = self.settings.load()
            target = settings["spreadsheet"]
            if not os.path.isdir(os.path.dirname(os.path.abspath(target))):
                target = DEFAULT_SHEET
            check_upload(data, body.get("filename") or "")  # say what's wrong with the file before asking to replace
            if os.path.exists(target) and not body.get("replace"):
                raise SheetError(f"There's already a spreadsheet at {target}. Confirm you want to replace it "
                                 "(it will be backed up first).", 409)
            result = install_upload(target, data, body.get("filename") or "")
            if settings["spreadsheet"] != result["path"]:
                settings["spreadsheet"] = result["path"]
                self.settings.save(settings)
        self._send(200, {"ok": True, **result})

    def _download_spreadsheet(self):
        path = self.settings.load()["spreadsheet"]
        if not os.path.isfile(path):
            raise SheetError("There's no spreadsheet to download yet.", 404)
        with open(path, "rb") as fh:
            data = fh.read()
        name = os.path.basename(path).replace('"', "")
        self._send(200, data, XLSX_TYPE, {"Content-Disposition": f'attachment; filename="{name}"'})

    # Shopify -------------------------------------------------------------------------------------------
    def _shopify_review(self):
        data = self.settings.load()
        client = client_from(data)
        snap = self._inventory(data).snapshot()
        catalog = client.catalog()
        review = shopify.build_review(snap["beans"], catalog, prefs_from(data))
        locations = {}
        for p in catalog:
            for v in p["variants"]:
                for lv in v["levels"]:
                    locations[lv["location_id"]] = lv["location"]
        review["locations"] = [{"id": k, "name": v} for k, v in locations.items()]
        review["shop"] = client.shop
        self._send(200, review)

    def _test_shopify(self, _body):
        info = client_from(self.settings.load()).shop_info()
        self._send(200, {"ok": True, "shop": info})

    def _link(self, body):
        with SETTINGS_LOCK:
            data = self.settings.load()
            if body.get("variant_id"):
                data["links"][body["varietal"]] = body["variant_id"]
            else:
                data["links"].pop(body["varietal"], None)
            self.settings.save(data)
        self._send(200, {"ok": True})

    def _push(self, body):
        approved = body.get("approved") or []
        if not approved:
            raise SheetError("Tick at least one change to push.")
        data = self.settings.load()
        inventory = self._inventory(data)
        snap = inventory.snapshot()
        results, new_links = shopify.push(client_from(data), snap["beans"], prefs_from(data), approved)
        if new_links:
            with SETTINGS_LOCK:
                data = self.settings.load()
                data["links"].update(new_links)
                self.settings.save(data)
        log_error = None
        try:
            inventory.log_sync([(r["varietal"], r["change"], _cell(r["from"]), _cell(r["to"]), r["result"])
                                for r in results])
        except SheetError as exc:
            log_error = f"Pushed, but couldn't record it in the spreadsheet's Shopify Sync Log: {exc}"
        self._send(200, {"results": results, "log_error": log_error})


def _cell(value):
    return "" if value is None else value


def enable_cloud(storage_url, data_dir, settings_path=None):
    """Turns on cloud mode: files live in data_dir and are mirrored to R2. Returns the settings.json path."""
    global CLOUD
    os.makedirs(data_dir, exist_ok=True)
    sheet = os.path.join(data_dir, SHEET_NAME)
    settings_path = settings_path or os.path.join(data_dir, "settings.json")
    mirror = cloud.Mirror(storage_url, {SHEET_NAME: sheet, "settings.json": settings_path},
                          os.path.join(data_dir, "backups"))
    found = mirror.restore()  # fails loudly: starting with an empty sheet could hide the real one
    print("Restored from Cloudflare storage: " + (", ".join(found) or "nothing yet (first run)"))
    CLOUD = {"sheet": sheet, "mirror": mirror}
    return settings_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=int(os.environ.get("RRR_PORT", 8765)))
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (the container uses 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--settings", default=None, help="default: settings.json next to app.py")
    args = parser.parse_args()

    settings_path = args.settings or os.path.join(HERE, "settings.json")
    if os.environ.get("RRR_STORAGE_URL"):
        settings_path = enable_cloud(os.environ["RRR_STORAGE_URL"], os.environ.get("RRR_DATA_DIR", "/data"),
                                     args.settings)
    Handler.settings = Settings(settings_path)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://localhost:{args.port}"
    sheet = Handler.settings.load()["spreadsheet"]
    print(f"RRR Coffee Inventory is running at {url}\nSpreadsheet: {sheet}\nPress Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Just Shoot Me Receipt Maker.

A tiny local web server (Python standard library only) that serves the receipt
form in your browser. The app itself runs entirely in the browser: PDFs are
downloaded by the browser and the receipt log is kept in the browser, so the
same files also work as a static site (e.g. Cloudflare Pages).

Run:  python3 server.py
"""

import os
import socket
import threading
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

# The app's files live with the rest of the website, in public/jsm-receipt-maker.
APP_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "public", "jsm-receipt-maker"))
PREFERRED_PORT = 8765

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/logo.png": ("logo.png", "image/png"),
    "/vendor/jspdf.umd.min.js": ("vendor/jspdf.umd.min.js", "application/javascript"),
    "/vendor/jspdf.plugin.autotable.min.js": ("vendor/jspdf.plugin.autotable.min.js", "application/javascript"),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the Terminal window quiet

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path not in STATIC_FILES:
            return self.send_error(404)
        rel, ctype = STATIC_FILES[path]
        with open(os.path.join(APP_DIR, rel), "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def find_port():
    for port in range(PREFERRED_PORT, PREFERRED_PORT + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit("No free port found.")


def main():
    port = find_port()
    server = HTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port
    print("Just Shoot Me Receipt Maker is running at " + url)
    print("PDFs download through your browser (usually to Downloads).")
    print("Leave this window open while you work. Press Ctrl+C (or close it) to stop.")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

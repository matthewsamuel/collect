#!/usr/bin/env python3
"""
Lokale server voor het Scraper Dashboard.
Start met:  python server.py
Surf naar:  http://localhost:5050
"""

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).parent
PORT = 5050

# ── Scraper-status (gedeeld tussen threads) ──────────────────────────────────
_lock = threading.Lock()
_state = {
    "running": False,
    "log": [],          # lijst van strings
    "exit_code": None,
}


def _run_scraper():
    with _lock:
        _state["running"] = True
        _state["log"] = []
        _state["exit_code"] = None

    proc = subprocess.Popen(
        [sys.executable, str(BASE_DIR / "scraper.py")],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(BASE_DIR),
    )

    for line in proc.stdout:
        with _lock:
            _state["log"].append(line.rstrip())

    proc.wait()
    with _lock:
        _state["running"] = False
        _state["exit_code"] = proc.returncode


# ── HTTP handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # stil houden

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, path: Path, mime: str):
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            self.serve_file(BASE_DIR / "dashboard.html", "text/html; charset=utf-8")

        elif path == "/api/products":
            self.serve_file(BASE_DIR / "products.json", "application/json; charset=utf-8")

        elif path == "/api/status":
            with _lock:
                self.send_json({
                    "running": _state["running"],
                    "exit_code": _state["exit_code"],
                    "log_lines": len(_state["log"]),
                })

        elif path == "/api/log":
            # ?from=N  → stuur alleen regels vanaf index N
            qs = self.path.partition("?")[2]
            from_idx = 0
            for part in qs.split("&"):
                if part.startswith("from="):
                    try:
                        from_idx = int(part[5:])
                    except ValueError:
                        pass
            with _lock:
                lines = _state["log"][from_idx:]
                running = _state["running"]
                exit_code = _state["exit_code"]
            self.send_json({"lines": lines, "running": running, "exit_code": exit_code})

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/run":
            with _lock:
                already = _state["running"]
            if already:
                self.send_json({"ok": False, "error": "Scraper loopt al"}, 409)
                return
            t = threading.Thread(target=_run_scraper, daemon=True)
            t.start()
            self.send_json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Dashboard beschikbaar op  http://localhost:{PORT}")
    print("Stop met Ctrl+C\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer gestopt.")

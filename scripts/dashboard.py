#!/usr/bin/env python3
"""
dashboard.py
MyScope 实时仪表盘 — 零依赖 Web Server
端口：8095，通过 Cloudflare Tunnel 暴露为 dashboard.arjo.us.ci
"""

import json
import os
import time
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests as http_req
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

PORT = int(os.environ.get("DASHBOARD_PORT", 8095))
LOGS_DIR = Path(__file__).parent.parent / "logs"
LAST_RUN_FILE = LOGS_DIR / "last_run.json"
METRICS_FILE = LOGS_DIR / "metrics.jsonl"
HTML_FILE = Path(__file__).parent / "dashboard_page.html"

MEMORY_URL = os.environ.get("MEMORY_API_URL", "https://memory.arjo.us.ci")
MEMORY_TOKEN = os.environ.get("MEMORY_API_TOKEN", "")
ANDA_BASE_URL = os.environ.get("ANDA_BASE_URL", "http://localhost:8090")
ANDA_SPACE_ID = os.environ.get("ANDA_SPACE_ID", "anda_main")
ANDA_SPACE_TOKEN = os.environ.get("ANDA_SPACE_TOKEN", "")


def get_status() -> dict:
    """脚本存活状态"""
    now = datetime.now()
    data = {}
    if LAST_RUN_FILE.exists():
        try:
            data = json.loads(LAST_RUN_FILE.read_text())
        except Exception:
            pass

    scripts = []
    for name, last_str in data.items():
        try:
            last = datetime.fromisoformat(last_str)
            hours_ago = (now - last).total_seconds() / 3600
            if hours_ago < 25:
                status = "green"
            elif hours_ago < 48:
                status = "yellow"
            else:
                status = "red"
            scripts.append({
                "name": name,
                "last_run": last_str,
                "hours_ago": round(hours_ago, 1),
                "status": status,
            })
        except Exception:
            scripts.append({"name": name, "last_run": last_str, "hours_ago": -1, "status": "red"})

    return {"scripts": scripts, "checked_at": now.isoformat(timespec="seconds")}


def get_metrics(days: int = 7) -> list[dict]:
    """读取最近 N 天的 metrics"""
    if not METRICS_FILE.exists():
        return []

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    results = []
    try:
        with open(METRICS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("date", "") >= cutoff:
                        results.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return results


def get_indexes() -> dict:
    """实时查询各索引文档数"""
    headers = {"Authorization": f"Bearer {MEMORY_TOKEN}", "Content-Type": "application/json"}
    indexes = {}

    # Meilisearch indexes
    for idx in ["memory_chunks", "wiki_entries", "hubble_radius"]:
        try:
            r = http_req.get(
                f"{MEMORY_URL}/search",
                params={"q": "", "index": idx, "limit": 1},
                headers=headers,
                timeout=5,
            )
            if r.status_code == 200:
                data = r.json()
                indexes[idx] = data.get("total", data.get("estimatedTotalHits", len(data.get("results", []))))
            else:
                indexes[idx] = -1
        except Exception:
            indexes[idx] = -1

    # Anda concepts/propositions
    try:
        r = http_req.get(
            f"{ANDA_BASE_URL}/v1/{ANDA_SPACE_ID}/info",
            headers={"Authorization": f"Bearer {ANDA_SPACE_TOKEN}"},
            timeout=5,
        )
        if r.status_code == 200:
            info = r.json().get("result", {})
            indexes["anda_concepts"] = info.get("concepts", 0)
            indexes["anda_propositions"] = info.get("propositions", 0)
        else:
            indexes["anda_concepts"] = -1
            indexes["anda_propositions"] = -1
    except Exception:
        indexes["anda_concepts"] = -1
        indexes["anda_propositions"] = -1

    return indexes


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._serve_html()
        elif path == "/api/status":
            self._json_response(get_status())
        elif path == "/api/metrics":
            days = int(params.get("days", [7])[0])
            self._json_response(get_metrics(days))
        elif path == "/api/indexes":
            self._json_response(get_indexes())
        else:
            self.send_error(404)

    def _serve_html(self):
        try:
            content = HTML_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(500, "dashboard_page.html not found")

    def _json_response(self, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Suppress default access logs (too noisy for auto-refresh)
        pass


def main():
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"[MyScope Dashboard] listening on http://0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Dashboard] shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()

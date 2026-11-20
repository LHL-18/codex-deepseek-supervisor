"""Loopback dashboard with task-scoped mode controls; never submits model calls."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from collaboration_trace import RUN_DIR, list_runs, now, read_run, sanitize
from collaboration_mode import read_mode, start_mode, end_mode
from collaboration_progress import progress

ASSET_DIR = Path(__file__).resolve().parent
ASSETS = {
    "/": ("collaboration_dashboard.html", "text/html; charset=utf-8"),
    "/dashboard.js": ("collaboration_dashboard.js", "text/javascript; charset=utf-8"),
    "/dashboard.css": ("collaboration_dashboard.css", "text/css; charset=utf-8"),
}


def snapshot(run_dir=RUN_DIR):
    runs = list_runs(run_dir)
    summaries = []
    for item in runs:
        result = item.get("result", {})
        summaries.append({key: item.get(key) for key in ("id", "source", "created_at", "updated_at", "title", "state")})
        summaries[-1].update({
            "model": result.get("model", (item.get("request") or {}).get("model", "未记录")),
            "usage": result.get("usage", {}), "elapsed_seconds": result.get("elapsed_seconds"),
            "ok": result.get("ok"),
            "api_attempted": result.get("api_attempted", True),
            "progress": progress(item),
        })
    return {"runs": summaries, "server_time": now(), "timezone": "Asia/Shanghai", "mode": read_mode(run_dir)}


class DashboardHandler(BaseHTTPRequestHandler):
    run_dir = RUN_DIR
    secret = ""
    control_token = secrets.token_urlsafe(32)

    def valid_host(self):
        return self.headers.get("Host", "") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def json_response(self, data, status=200):
        payload = json.dumps(sanitize(data, self.secret), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        origin = self.headers.get("Origin")
        expected_origin = "http://" + self.headers.get("Host", "")
        if (not self.valid_host() or origin != expected_origin
                or self.headers.get("X-Collaboration-Token") != self.control_token):
            self.json_response({"error": "本地控制请求校验失败"}, 403)
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.json_response({"error": "Expected JSON"}, 415)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 4096:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("Expected an object")
            route = urlsplit(self.path).path
            if route == "/api/mode/start":
                data = start_mode(body.get("title"), self.run_dir)
            elif route == "/api/mode/end":
                data = end_mode("用户在看板结束协作，恢复 Codex 独立处理", self.run_dir, body.get("session_id"))
            else:
                self.json_response({"error": "Unknown control"}, 404)
                return
            self.json_response({"mode": data})
        except (ValueError, TypeError) as exc:
            self.json_response({"error": str(exc)}, 400)

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if not self.valid_host():
            self.send_error(403)
            return
        route = urlsplit(self.path).path
        if route == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        try:
            if route in ASSETS:
                filename, content_type = ASSETS[route]
                payload = (ASSET_DIR / filename).read_bytes()
            elif route == "/api/runs":
                payload = json.dumps(sanitize(snapshot(self.run_dir), self.secret), ensure_ascii=False).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            elif route == "/api/mode":
                self.json_response({"mode": read_mode(self.run_dir), "control_token": self.control_token})
                return
            elif route.startswith("/api/runs/"):
                run = read_run(self.run_dir, route.removeprefix("/api/runs/"))
                run["progress"] = progress(run)
                payload = json.dumps(sanitize(run, self.secret), ensure_ascii=False).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            else:
                self.send_error(404)
                return
        except (OSError, ValueError, KeyError):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    from deepseek_worker import load_config, WorkerError
    try:
        DashboardHandler.secret = load_config()["DEEPSEEK_API_KEY"]
    except (WorkerError, OSError):
        pass
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    print(f"Collaboration dashboard: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

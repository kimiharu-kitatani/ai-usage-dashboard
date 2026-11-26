#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI使用量ダッシュボード ローカルAPIサーバー (127.0.0.1:8768)
- index.html (file:// または http://) からの個別更新リクエストを受け付ける
- target = claude | codex | antigravity | all
- desktop/widget.pyw のバックグラウンドスレッド、または単体プロセスとして動作
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DATA_JSON = os.path.join(REPO, "data", "usage.json")
COLLECTOR_PY = os.path.join(HERE, "collector.py")
CLAUDE_PING_PS1 = os.path.join(HERE, "claude_ping.ps1")
HOST = "127.0.0.1"
PORT = 8768
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CREATE_NEW_CONSOLE = 0x00000010 if os.name == "nt" else 0

if HERE not in sys.path:
    sys.path.insert(0, HERE)
from collector import find_claude_exe  # noqa: E402

RELAY_URL = os.environ.get("AI_USAGE_RELAY_URL", "https://kick-in.xsrv.jp/ai-usage-relay/relay.php")
RELAY_TOKEN = os.environ.get("AI_USAGE_RELAY_TOKEN", "aiu_relay_2026_k9m4x7p2")
RELAY_POLL_SEC = 5
_RELAY_STARTED = False

_TARGET_LOCKS = {
    "claude": threading.Lock(),
    "codex": threading.Lock(),
    "antigravity": threading.Lock(),
    "all": threading.Lock(),
}
_ON_UPDATED_CB = None


def _python_exe():
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.name == "nt" and os.path.exists(pyw):
        return pyw
    return sys.executable or "python"


def _read_usage():
    if not os.path.exists(DATA_JSON):
        return None
    try:
        with open(DATA_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _async_git_push():
    def _run():
        try:
            subprocess.run(
                [_python_exe(), COLLECTOR_PY, "--push-only", "--reason", "web-push"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=120,
                creationflags=NO_WINDOW,
            )
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


def run_refresh(target: str) -> dict:
    if target not in _TARGET_LOCKS:
        return {"ok": False, "error": f"不明な対象です: {target}"}

    lock = _TARGET_LOCKS[target]
    if not lock.acquire(blocking=False):
        return {"ok": False, "error": f"{target} は現在取得中です。少し待ってください。"}

    try:
        py = _python_exe()
        if target == "claude":
            cmd = [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", CLAUDE_PING_PS1,
                "-TimeoutSec", "60", "-OnlyClaude"
            ]
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=240, creationflags=NO_WINDOW)
        elif target in ("codex", "antigravity"):
            cmd = [py, COLLECTOR_PY, "--only", target, "--no-push", "--reason", f"web-{target}"]
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=120, creationflags=NO_WINDOW)
        else:  # all
            cmd = [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", CLAUDE_PING_PS1,
                "-TimeoutSec", "60"
            ]
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=260, creationflags=NO_WINDOW)

        data = _read_usage()
        if _ON_UPDATED_CB:
            try:
                _ON_UPDATED_CB()
            except Exception:
                pass

        _async_git_push()

        svc_data = (data or {}).get("services", {}).get(target) if target != "all" else None
        svc_err = svc_data.get("error") if isinstance(svc_data, dict) else None
        return {
            "ok": True,
            "target": target,
            "returncode": r.returncode,
            "service_error": svc_err,
            "data": data,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "target": target, "error": "取得がタイムアウトしました"}
    except Exception as e:
        return {"ok": False, "target": target, "error": str(e)}
    finally:
        lock.release()


def get_claude_login_command() -> dict:
    exe = find_claude_exe()
    if not exe:
        return {"ok": False, "command": "claude auth login", "error": "claude.exe が見つかりません"}
    return {"ok": True, "command": f'"{exe}" auth login', "exe": exe}


def start_claude_login() -> dict:
    info = get_claude_login_command()
    if not info.get("ok"):
        return info
    exe = info["exe"]
    try:
        proc = subprocess.Popen(
            [exe, "auth", "login"],
            creationflags=CREATE_NEW_CONSOLE,
        )
        _log(f"started claude auth login (pid={proc.pid})")
        return {
            "ok": True,
            "started": True,
            "pid": proc.pid,
            "command": info["command"],
            "message": "Claude Code の認証画面を起動しました。ログイン完了後に「↻ 最新取得」を押してください。",
        }
    except Exception as e:
        return {"ok": False, "command": info["command"], "error": f"起動に失敗しました: {e}"}


class DashboardAPIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json(200, {"ok": True, "pid": os.getpid()})
            return
        if parsed.path in ("/api/status", "/api/usage"):
            data = _read_usage()
            self._send_json(200, {"ok": True, "data": data})
            return
        if parsed.path == "/api/claude-login":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._send_json(403, {"ok": False, "error": "Forbidden"})
                return
            self._send_json(200, get_claude_login_command())
            return
        self._send_json(404, {"ok": False, "error": "Not Found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/refresh":
            qs = parse_qs(parsed.query)
            target = (qs.get("target") or ["all"])[0].strip().lower()
            res = run_refresh(target)
            self._send_json(200 if res.get("ok") else 500, res)
            return
        if parsed.path == "/api/claude-login":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._send_json(403, {"ok": False, "error": "Forbidden"})
                return
            res = start_claude_login()
            self._send_json(200 if res.get("ok") else 500, res)
            return
        self._send_json(404, {"ok": False, "error": "Not Found"})


def _log(msg: str):
    try:
        state_dir = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "ai-usage-dashboard")
        os.makedirs(state_dir, exist_ok=True)
        log_path = os.path.join(state_dir, "api_server.log")
        if os.path.exists(log_path) and os.path.getsize(log_path) > 512 * 1024:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-200:]
            with open(log_path, "w", encoding="utf-8") as f:
                f.writelines(tail)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _is_already_running() -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/health", timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(isinstance(data, dict) and data.get("ok"))
    except Exception:
        return False


def _relay_request(action: str, method: str = "GET", payload: dict = None, timeout: int = 10):
    if not RELAY_URL:
        return None
    sep = "&" if "?" in RELAY_URL else "?"
    url = f"{RELAY_URL}{sep}action={urllib.parse.quote(action)}&token={urllib.parse.quote(RELAY_TOKEN)}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": "ai-usage-dashboard-pc/1.0"}
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _relay_poll_loop():
    last_err = None
    _log(f"relay poller started: {RELAY_URL}")
    while True:
        try:
            res = _relay_request("poll", method="GET", timeout=10)
            if last_err is not None:
                _log("relay poller recovered")
                last_err = None
            job = (res or {}).get("job") if isinstance(res, dict) else None
            if isinstance(job, dict) and job.get("id"):
                job_id = job["id"]
                target = str(job.get("target") or "all").strip().lower()
                _log(f"relay job received: id={job_id} target={target}")
                result = run_refresh(target)
                _relay_request(
                    "complete",
                    method="POST",
                    payload={
                        "id": job_id,
                        "ok": bool(result.get("ok")),
                        "target": target,
                        "service_error": result.get("service_error"),
                        "error": result.get("error"),
                        "data": result.get("data"),
                    },
                    timeout=20,
                )
                _log(f"relay job completed: id={job_id} ok={bool(result.get('ok'))}")
        except Exception as e:
            err_s = str(e)
            if err_s != last_err:
                _log(f"relay poll error: {err_s}")
                last_err = err_s
        time.sleep(RELAY_POLL_SEC)


def start_relay_poller():
    global _RELAY_STARTED
    if _RELAY_STARTED or not RELAY_URL:
        return
    _RELAY_STARTED = True
    threading.Thread(target=_relay_poll_loop, daemon=True).start()


def start_api_server_thread(on_updated=None):
    """widget.pyw 等からバックグラウンドデーモンスレッドで起動する"""
    global _ON_UPDATED_CB
    _ON_UPDATED_CB = on_updated
    if _is_already_running():
        return None
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), DashboardAPIHandler)
    except OSError:
        return None
    _log(f"api_server started in widget thread (pid={os.getpid()})")
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    start_relay_poller()
    return httpd


if __name__ == "__main__":
    if _is_already_running():
        sys.exit(0)
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), DashboardAPIHandler)
    except OSError as e:
        _log(f"bind failed on {HOST}:{PORT}: {e}")
        sys.exit(0)
    _log(f"api_server standalone started (pid={os.getpid()})")
    start_relay_poller()
    print(f"AI Usage Dashboard API server listening on http://{HOST}:{PORT}", flush=True)
    httpd.serve_forever()

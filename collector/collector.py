#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI usage-limit collector (Claude Code / Codex) -> data/usage.json

公開してよい値（使用率 %、リセット時刻、リセット回数）だけを出力します。
トークン・メールアドレス・アカウントID・会話ログは一切出力しません。
認証情報ファイル（~/.claude/.credentials.json, ~/.codex/auth.json 等）は読みません。

使い方:
  python collector.py                 # 取得して data/usage.json を書き、git commit & push
  python collector.py --no-push       # 取得して書くだけ
  python collector.py --no-push --out C:\\temp\\usage.json --debug
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import queue

JST = dt.timezone(dt.timedelta(hours=9), "JST")
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(REPO, "data", "usage.json")
STATE_DIR = os.environ.get("AIUSAGE_STATE_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share"), "ai-usage-dashboard")
STATUSLINE_CACHE = os.path.join(STATE_DIR, "claude_statusline.json")
LOG_FILE = os.path.join(STATE_DIR, "collector.log")

DEBUG = False
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def log(msg):
    line = "%s %s" % (dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if sys.stderr:
        try:
            sys.stderr.write(line + "\n")
        except Exception:
            pass


def dbg(label, text):
    if DEBUG:
        text = EMAIL_RE.sub("<redacted-email>", str(text))
        log("[debug] %s:\n%s" % (label, text))


def iso(ts):
    """unix seconds / ms / ISO string -> ISO8601 JST string, else None"""
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)):
            t = float(ts)
            if t > 1e12:
                t /= 1000.0
            return dt.datetime.fromtimestamp(t, JST).isoformat(timespec="seconds")
        if isinstance(ts, str) and ts.strip():
            s = ts.strip()
            if re.fullmatch(r"\d+(\.\d+)?", s):
                return iso(float(s))
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return d.astimezone(JST).isoformat(timespec="seconds")
    except Exception:
        return None
    return None


def pct(v):
    if v is None:
        return None
    try:
        f = float(v)
    except Exception:
        return None
    if f != f or f < 0:
        return None
    return round(f, 1)


def sanitize(msg):
    """公開用エラーメッセージからメールアドレス・ホームディレクトリのパスを除去"""
    if msg is None:
        return None
    msg = EMAIL_RE.sub("<email>", str(msg))
    home = os.path.expanduser("~")
    for h in {home, home.replace("\\", "/")}:
        if h and len(h) > 3:
            msg = msg.replace(h, "~")
    return msg[:300]


def drop_expired(svc, note_list):
    """キャッシュ由来の値で、リセット時刻を過ぎた枠は推測せず null にする"""
    now = dt.datetime.now(JST)
    for key, label in (("five_hour", "5時間枠"), ("weekly", "週間枠")):
        w = svc.get(key) or {}
        r = w.get("resets_at")
        try:
            if r and dt.datetime.fromisoformat(r) <= now:
                svc[key] = {"used_percent": None, "resets_at": None}
                note_list.append("%sは記録後にリセット済み（次回利用時に更新）" % label)
        except Exception:
            pass


def empty_service():
    return {
        "five_hour": {"used_percent": None, "resets_at": None},
        "weekly": {"used_percent": None, "resets_at": None},
        "reset_count": None,
        "source": None,
        "as_of": None,
        "error": None,
    }


def find_exe(name):
    p = shutil.which(name)
    if p:
        return p
    if IS_WIN:
        for ext in (".cmd", ".exe", ".bat"):
            p = shutil.which(name + ext)
            if p:
                return p
    return None


def kill_tree(proc):
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=NO_WINDOW, timeout=15)
        else:
            proc.kill()
    except Exception:
        pass


# ---------------------------------------------------------------- Codex
def codex_via_app_server(timeout=45):
    exe = find_exe("codex")
    if not exe:
        raise RuntimeError("codex コマンドが見つかりません")
    proc = subprocess.Popen([exe, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    q = queue.Queue()

    def reader(stream, tag):
        for raw in iter(stream.readline, b""):
            q.put((tag, raw.decode("utf-8", "replace").strip()))
        q.put((tag, None))

    threading.Thread(target=reader, args=(proc.stdout, "out"), daemon=True).start()
    threading.Thread(target=reader, args=(proc.stderr, "err"), daemon=True).start()

    def send(obj):
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def wait_id(want, deadline):
        while time.time() < deadline:
            try:
                tag, line = q.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            if line is None:
                if tag == "out":
                    raise RuntimeError("codex app-server が終了しました")
                continue
            if tag == "err":
                dbg("codex stderr", line[:500])
                continue
            try:
                msg = json.loads(line)
            except Exception:
                dbg("codex non-json", line[:300])
                continue
            if msg.get("id") == want and ("result" in msg or "error" in msg):
                return msg
            dbg("codex other msg", (msg.get("method") or "") + " " + str(msg.get("id")))
        raise RuntimeError("codex app-server 応答タイムアウト")

    try:
        deadline = time.time() + timeout
        send({"method": "initialize", "id": 0, "params": {"clientInfo": {
            "name": "ai_usage_dashboard", "title": "AI Usage Dashboard", "version": "0.1.0"}}})
        m = wait_id(0, deadline)
        if "error" in m:
            raise RuntimeError("initialize error: %s" % m["error"].get("message"))
        send({"method": "initialized", "params": {}})
        send({"method": "account/rateLimits/read", "id": 1})
        m = wait_id(1, deadline)
        if "error" in m:
            raise RuntimeError("rateLimits/read error: %s" % m["error"].get("message"))
        return m.get("result") or {}
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            kill_tree(proc)


def codex_bucket_to_service(result, svc):
    rl = None
    by = result.get("rateLimitsByLimitId") or {}
    if isinstance(by, dict) and isinstance(by.get("codex"), dict):
        rl = by["codex"]
    if rl is None:
        rl = result.get("rateLimits") or {}
    shape = {}
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        mins = w.get("windowDurationMins")
        shape[key] = mins
        entry = {"used_percent": pct(w.get("usedPercent")), "resets_at": iso(w.get("resetsAt"))}
        if mins == 300:
            svc["five_hour"] = entry
        elif mins == 10080:
            svc["weekly"] = entry
    dbg("codex window shape", json.dumps(shape))
    rc = result.get("rateLimitResetCredits")
    if isinstance(rc, dict) and rc.get("availableCount") is not None:
        svc["reset_count"] = int(rc["availableCount"])
        exps = [c.get("expiresAt") for c in (rc.get("credits") or []) if isinstance(c, dict) and c.get("expiresAt")]
        if exps:
            svc["reset_count_expires_at"] = iso(min(exps))


def codex_via_session_log():
    """最新 rollout-*.jsonl の token_count イベントから rate_limits だけを抜き出す（会話内容は読まない/出力しない）"""
    base = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
    files = glob.glob(os.path.join(base, "*", "*", "*", "rollout-*.jsonl"))
    if not files:
        raise RuntimeError("Codex セッションログが見つかりません")
    files.sort(key=os.path.getmtime, reverse=True)
    for path in files[:5]:
        found = None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"token_count"' not in line or '"rate_limits"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                p = ev.get("payload") or {}
                if p.get("type") == "token_count" and isinstance(p.get("rate_limits"), dict):
                    found = (ev.get("timestamp"), p["rate_limits"])
        if found:
            return found
    raise RuntimeError("セッションログに rate_limits がありません")


def collect_codex():
    svc = empty_service()
    errors = []
    try:
        res = codex_via_app_server()
        codex_bucket_to_service(res, svc)
        svc["source"] = "codex app-server account/rateLimits/read"
        svc["as_of"] = dt.datetime.now(JST).isoformat(timespec="seconds")
        if svc["five_hour"]["used_percent"] is None and svc["weekly"]["used_percent"] is None:
            errors.append("app-server は応答しましたが 5h/週 の枠が含まれていません")
        else:
            return svc
    except Exception as e:
        errors.append("app-server: %s" % e)
    try:
        ts, rl = codex_via_session_log()
        for key in ("primary", "secondary"):
            w = rl.get(key)
            if not isinstance(w, dict):
                continue
            mins = w.get("window_minutes")
            resets = w.get("resets_at")
            if resets is None and w.get("resets_in_seconds") is not None and ts:
                base = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                resets = (base + dt.timedelta(seconds=float(w["resets_in_seconds"]))).timestamp()
            entry = {"used_percent": pct(w.get("used_percent")), "resets_at": iso(resets)}
            if mins == 300:
                svc["five_hour"] = entry
            elif mins == 10080:
                svc["weekly"] = entry
        svc["source"] = "codex session log (最後に Codex を使った時点の値)"
        svc["as_of"] = iso(ts)
        drop_expired(svc, errors)
    except Exception as e:
        errors.append("session log: %s" % e)
    svc["error"] = " / ".join(errors) if errors else None
    return svc


# ---------------------------------------------------------------- Claude
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def get_tz(name):
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        pass
    if name in ("Asia/Tokyo", "Japan", "JST"):
        return JST
    if name in ("UTC", "Etc/UTC", "GMT"):
        return dt.timezone.utc
    return None


def parse_reset_text(text, now=None):
    """'3pm (Asia/Tokyo)', '10:59am (Asia/Tokyo)', 'Oct 3, 10am (Asia/Tokyo)', 'Oct 3 at 10am' -> ISO or None"""
    if not text:
        return None
    s = text.strip()
    tzname = None
    m = re.search(r"\(([^)]+)\)", s)
    if m:
        tzname = m.group(1).strip()
        s = (s[:m.start()] + s[m.end():]).strip()
    tz = get_tz(tzname) if tzname else JST
    if tz is None:
        return None
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(tz)
    tm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", s, re.I)
    if not tm:
        tm24 = re.search(r"\b(\d{1,2}):(\d{2})\b", s)
        if not tm24:
            return None
        hour, minute = int(tm24.group(1)), int(tm24.group(2))
    else:
        hour, minute = int(tm.group(1)) % 12, int(tm.group(2) or 0)
        if tm.group(3).lower() == "pm":
            hour += 12
    dm = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", s, re.I)
    try:
        if dm:
            month, day = MONTHS[dm.group(1).lower()[:3]], int(dm.group(2))
            cand = now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if cand < now - dt.timedelta(days=2):
                cand = cand.replace(year=cand.year + 1)
        else:
            cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if cand < now - dt.timedelta(minutes=1):
                cand += dt.timedelta(days=1)
    except ValueError:
        return None
    return cand.astimezone(JST).isoformat(timespec="seconds")


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def parse_claude_usage(text):
    """claude /usage のテキストから (five_hour, weekly) を抽出。見つからない項目は None"""
    t = ANSI_RE.sub("", text or "")
    lines = [l.strip() for l in t.splitlines()]
    sections = {}
    cur = None
    for l in lines:
        low = l.lower()
        if re.match(r"^current session\b", low):
            cur = "five_hour"
        elif re.match(r"^current week\s*\(all models\)", low):
            cur = "weekly"
        elif re.match(r"^current week\b", low) or re.match(r"^(extra usage|current month)\b", low):
            cur = "other"
        if cur:
            sections.setdefault(cur, []).append(l)
    out = {}
    for key in ("five_hour", "weekly"):
        body = " \n ".join(sections.get(key, []))
        if not body:
            out[key] = None
            continue
        pm = re.search(r"(\d+(?:\.\d+)?)\s*%\s*used", body, re.I)
        rm = re.search(r"resets?\s+(?:at\s+|on\s+)?([^\n·|]+)", body, re.I)
        out[key] = {
            "used_percent": pct(pm.group(1)) if pm else None,
            "resets_at": parse_reset_text(rm.group(1)) if rm else None,
        }
    return out


def claude_via_cli(timeout=120):
    exe = find_exe("claude")
    if not exe:
        raise RuntimeError("claude コマンドが見つかりません")
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    r = subprocess.run([exe, "-p", "/usage"], stdin=subprocess.DEVNULL, capture_output=True,
                       timeout=timeout, creationflags=NO_WINDOW, env=env)
    out = r.stdout.decode("utf-8", "replace")
    err = r.stderr.decode("utf-8", "replace")
    dbg("claude -p /usage exit", r.returncode)
    dbg("claude -p /usage stdout", out[:3000])
    dbg("claude -p /usage stderr", err[:1000])
    return out + "\n" + err


def claude_via_statusline_cache(max_age_min=60):
    if not os.path.exists(STATUSLINE_CACHE):
        raise RuntimeError("Claude の値がまだ記録されていません（statusLine に claude_statusline.py を設定し、PC で Claude Code を使うと記録されます）")
    with open(STATUSLINE_CACHE, "r", encoding="utf-8") as f:
        c = json.load(f)
    cap = c.get("captured_at")
    rl = c.get("rate_limits") or {}
    res = {}
    for key, src in (("five_hour", "five_hour"), ("weekly", "seven_day")):
        w = rl.get(src)
        res[key] = {"used_percent": pct(w.get("used_percentage")), "resets_at": iso(w.get("resets_at"))} if isinstance(w, dict) else None
    return cap, res


TRY_CLAUDE_CLI = False


def collect_claude():
    svc = empty_service()
    errors = []
    if TRY_CLAUDE_CLI:
        r = collect_claude_cli(svc, errors)
        if r:
            return r
    return collect_claude_statusline(svc, errors)


def collect_claude_cli(svc, errors):
    # 注: Claude Code v2.1.220 (Windows) では `claude -p "/usage"` は利用上限ではなく
    # セッションのコスト集計（Total cost: ...）しか出力しないことを確認済み。既定では無効。
    try:
        text = claude_via_cli()
        parsed = parse_claude_usage(text)
        got = False
        for key in ("five_hour", "weekly"):
            if parsed.get(key):
                svc[key] = parsed[key]
                got = got or parsed[key]["used_percent"] is not None
        if got:
            svc["source"] = "claude -p /usage"
            svc["as_of"] = dt.datetime.now(JST).isoformat(timespec="seconds")
            if svc["five_hour"]["resets_at"] is None or svc["weekly"]["resets_at"] is None:
                svc["error"] = "一部のリセット時刻を解析できませんでした"
            return svc
        first = ANSI_RE.sub("", text).strip().splitlines()
        errors.append("claude -p /usage の出力を解析できません（先頭: %s）" % EMAIL_RE.sub("<email>", (first[0] if first else "空"))[:120])
    except subprocess.TimeoutExpired:
        errors.append("claude -p /usage がタイムアウト")
    except Exception as e:
        errors.append("claude cli: %s" % e)
    return None


def collect_claude_statusline(svc, errors):
    try:
        cap, res = claude_via_statusline_cache()
        for key in ("five_hour", "weekly"):
            if res.get(key):
                svc[key] = res[key]
        svc["source"] = "Claude Code statusline (最後に Claude Code を使った時点の値)"
        svc["as_of"] = iso(cap)
        drop_expired(svc, errors)
    except Exception as e:
        errors.append(str(e))
    svc["error"] = " / ".join(errors) if errors else None
    return svc


# ---------------------------------------------------------------- git
def git(*args, check=True):
    r = subprocess.run(["git", "-C", REPO] + list(args), capture_output=True, timeout=120, creationflags=NO_WINDOW)
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr.decode("utf-8", "replace").strip()[:300]))
    return r


def push(out_path):
    rel = os.path.relpath(out_path, REPO).replace("\\", "/")
    git("add", "--", rel)
    if git("diff", "--cached", "--quiet", "--", rel, check=False).returncode == 0:
        log("変更なし")
        return
    git("commit", "-m", "update usage.json", "--", rel)
    git("pull", "--rebase", "--autostash")
    git("push")
    log("pushed")


def main():
    global DEBUG
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--only", choices=["claude", "codex"])
    ap.add_argument("--try-claude-cli", action="store_true", help='claude -p "/usage" も試す（v2.1.220 では上限値は出ない）')
    a = ap.parse_args()
    global TRY_CLAUDE_CLI
    DEBUG = a.debug
    TRY_CLAUDE_CLI = a.try_claude_cli

    data = {"schema": 1, "fetched_at": None, "services": {}}
    old = {}
    if os.path.exists(a.out):
        try:
            with open(a.out, "r", encoding="utf-8") as f:
                old = json.load(f).get("services", {})
        except Exception:
            old = {}
    for name, fn in (("claude", collect_claude), ("codex", collect_codex)):
        if a.only and a.only != name:
            data["services"][name] = old.get(name) or empty_service()
            continue
        try:
            data["services"][name] = fn()
        except Exception as e:
            s = empty_service()
            s["error"] = "unexpected: %s" % e
            data["services"][name] = s
        data["services"][name]["error"] = sanitize(data["services"][name].get("error"))
        log("%s: %s" % (name, json.dumps(data["services"][name], ensure_ascii=False)))
    data["fetched_at"] = dt.datetime.now(JST).isoformat(timespec="seconds")

    txt = json.dumps(data, ensure_ascii=False, indent=2)
    if EMAIL_RE.search(txt):  # 念のため
        txt = EMAIL_RE.sub("<redacted>", txt)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt + "\n")
    os.replace(tmp, a.out)
    if not a.no_push:
        try:
            push(a.out)
        except Exception as e:
            log("push error: %s" % e)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

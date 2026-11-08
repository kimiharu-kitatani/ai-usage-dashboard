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
  python collector.py --throttle-min 10   # 前回実行から10分未満なら何もしない（フックから使用）
"""
import argparse
import math
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
STAMP_FILE = os.path.join(STATE_DIR, "last_run.stamp")
LOCK_FILE = os.path.join(STATE_DIR, "collector.lock")

# 公開リポジトリのコミットに個人のメールアドレスが載らないよう、GitHub の noreply アドレスで commit する
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "ai-usage-collector",
    "GIT_AUTHOR_EMAIL": "103965321+kimiharu-kitatani@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "ai-usage-collector",
    "GIT_COMMITTER_EMAIL": "103965321+kimiharu-kitatani@users.noreply.github.com",
}

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


# ---------------------------------------------------------------- Antigravity
AGY_MODEL_RE = re.compile(
    r"<USER_SETTINGS_CHANGE>\s*The user changed setting `Model Selection` from [^\n]+? to ([A-Za-z0-9 _.\-()]+?)\.\s*No need"
)


def find_agy_exe():
    p = find_exe("agy")
    if p:
        return p
    local_app = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
    cand = os.path.join(local_app, "agy", "bin", "agy.exe" if IS_WIN else "agy")
    if os.path.exists(cand):
        return cand
    return None


def agy_via_cli(timeout=30):
    exe = find_agy_exe()
    if not exe:
        raise RuntimeError("agy コマンドが見つかりません")
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    r = subprocess.run(
        [exe, "-p", "/usage", "--print-timeout", "25s"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        creationflags=NO_WINDOW,
        env=env,
    )
    out = r.stdout.decode("utf-8", "replace")
    err = r.stderr.decode("utf-8", "replace")
    dbg("agy -p /usage exit", r.returncode)
    dbg("agy -p /usage stdout", out[:3000])
    return out + "\n" + err


def _empty_agy_win():
    return {
        "used_percent": None,
        "remain_percent": None,
        "resets_at": None,
        "prompts": 0,
        "steps": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "total_tokens": 0,
    }


def _add_agy_win(dst, prompts=0, steps=0, inp=0, out=0, cache=0):
    dst["prompts"] += prompts
    dst["steps"] += steps
    dst["input_tokens"] += inp
    dst["output_tokens"] += out
    dst["cache_read_tokens"] += cache
    dst["total_tokens"] += inp + out


def collect_antigravity(now=None, old_agy=None):
    """公式 CLI `agy -p /usage` から各モデル枠（Gemini Models / Claude and GPT models 等）の
    5時間枠・週間枠の残量%とリセット時刻を取得し、ローカル transcript の回数・トークン数を付加する。
    """
    now = now or dt.datetime.now(JST)
    cut_5h = now - dt.timedelta(hours=5)
    cut_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cut_1w = now - dt.timedelta(days=7)

    old_models_by_name = {}
    if isinstance(old_agy, dict):
        for om in old_agy.get("models") or []:
            if isinstance(om, dict) and om.get("name"):
                old_models_by_name[om["name"]] = om

    models = {}
    errors = []

    def ensure_model(m_name):
        if m_name not in models:
            slug = re.sub(r"[^a-z0-9]+", "_", m_name.lower()).strip("_")
            models[m_name] = {
                "key": "agy_" + slug,
                "name": m_name,
                "five_hour": _empty_agy_win(),
                "today": _empty_agy_win(),
                "weekly": _empty_agy_win(),
                "last_used_at": None,
            }
        return models[m_name]

    # 1) 公式 `agy -p /usage` からクォータ残量%とリセット時刻を取得
    got_cli = False
    try:
        raw_out = agy_via_cli()
        for line in raw_out.splitlines():
            cols = [c.strip() for c in ANSI_RE.sub("", line).split("\t")]
            if len(cols) < 4:
                continue
            b_name, l_type, rem_s, rst_s = cols[0], cols[1].lower(), cols[2], cols[3]
            if not b_name or b_name.lower().startswith("fetching"):
                continue
            m_rem = re.search(r"(\d+(?:\.\d+)?)\s*%", rem_s)
            if not m_rem:
                continue
            rem_pct = pct(m_rem.group(1))
            used_pct = round(max(0.0, min(100.0, 100.0 - rem_pct)), 1) if rem_pct is not None else None
            rst_iso = iso(rst_s)
            entry = ensure_model(b_name)
            if "five hour" in l_type or "5h" in l_type:
                entry["five_hour"]["used_percent"] = used_pct
                entry["five_hour"]["remain_percent"] = rem_pct
                entry["five_hour"]["resets_at"] = rst_iso
                got_cli = True
            elif "week" in l_type:
                entry["weekly"]["used_percent"] = used_pct
                entry["weekly"]["remain_percent"] = rem_pct
                entry["weekly"]["resets_at"] = rst_iso
                got_cli = True
    except Exception as e:
        errors.append("agy cli: %s" % e)

    # CLI が失敗した場合は前回のクォータ値を引き継ぎ（期限切れは drop_expired で消去）
    if not got_cli and old_models_by_name:
        for b_name, om in old_models_by_name.items():
            entry = ensure_model(b_name)
            for k in ("five_hour", "weekly"):
                ow = om.get(k) or {}
                entry[k]["used_percent"] = ow.get("used_percent")
                entry[k]["remain_percent"] = ow.get("remain_percent")
                entry[k]["resets_at"] = ow.get("resets_at")
            drop_expired(entry, errors)

    # Argon 系バケット名（3枠目）を特定
    argon_bucket = None
    for b_name in models:
        if "argon" in b_name.lower() or ("pro" in b_name.lower() and b_name != "Gemini Models"):
            argon_bucket = b_name
            break

    def map_raw_to_buckets(raw_name):
        u = str(raw_name or "").upper()
        if any(x in u for x in ("CLAUDE", "SONNET", "OPUS", "HAIKU", "GPT")):
            return ["Claude and GPT models"]
        if "ARGON" in u or "PRO" in u:
            res = ["Gemini Models"]
            if argon_bucket:
                res.append(argon_bucket)
            return res
        return ["Gemini Models"]

    # 2) ローカル transcript から回数・ステップ数・トークン数を集計
    brain_dir = os.path.expanduser("~/.gemini/antigravity/brain")
    pattern = os.path.join(brain_dir, "*", ".system_generated", "logs", "transcript.jsonl")
    files = glob.glob(pattern)
    totals = {
        "five_hour": _empty_agy_win(),
        "today": _empty_agy_win(),
        "weekly": _empty_agy_win(),
    }
    latest_ts = None
    latest_model = None

    for fpath in files:
        cur_raw = "Gemini Flash"
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    stype = obj.get("type")
                    content = obj.get("content") or ""
                    if stype == "USER_INPUT" and "Model Selection" in content:
                        m = AGY_MODEL_RE.search(content)
                        if m:
                            cur_raw = m.group(1).strip()
                    ts = _parse_iso(obj.get("created_at"))
                    if ts is None:
                        continue
                    is_prompt = (stype == "USER_INPUT" and obj.get("source") == "USER_EXPLICIT")
                    is_step = (stype == "PLANNER_RESPONSE")
                    if not (is_prompt or is_step):
                        continue

                    inp = int(obj.get("input_tokens") or 0) if is_step else 0
                    out = int(obj.get("output_tokens") or 0) if is_step else 0
                    cache = int(obj.get("cache_read_tokens") or 0) if is_step else 0
                    p_cnt = 1 if is_prompt else 0
                    s_cnt = 1 if is_step else 0

                    ts_iso = ts.isoformat(timespec="seconds")
                    if latest_ts is None or ts > latest_ts:
                        latest_ts = ts
                        latest_model = cur_raw

                    for b_name in map_raw_to_buckets(cur_raw):
                        if b_name not in models:
                            continue
                        m_entry = models[b_name]
                        if m_entry["last_used_at"] is None or ts_iso > m_entry["last_used_at"]:
                            m_entry["last_used_at"] = ts_iso
                        if ts >= cut_1w:
                            _add_agy_win(m_entry["weekly"], p_cnt, s_cnt, inp, out, cache)
                        if ts >= cut_today:
                            _add_agy_win(m_entry["today"], p_cnt, s_cnt, inp, out, cache)
                        if ts >= cut_5h:
                            _add_agy_win(m_entry["five_hour"], p_cnt, s_cnt, inp, out, cache)

                    if ts >= cut_1w:
                        _add_agy_win(totals["weekly"], p_cnt, s_cnt, inp, out, cache)
                    if ts >= cut_today:
                        _add_agy_win(totals["today"], p_cnt, s_cnt, inp, out, cache)
                    if ts >= cut_5h:
                        _add_agy_win(totals["five_hour"], p_cnt, s_cnt, inp, out, cache)
        except Exception:
            continue

    model_list = list(models.values())
    for m_entry in model_list:
        try:
            update_day_track(m_entry, old_models_by_name.get(m_entry["name"]))
        except Exception:
            pass

    return {
        "as_of": now.isoformat(timespec="seconds") if got_cli else (old_agy.get("as_of") if isinstance(old_agy, dict) else None),
        "source": "agy -p /usage" if got_cli else "agy cache",
        "current_model": latest_model,
        "totals": totals,
        "models": model_list,
        "error": sanitize(" / ".join(errors)) if errors else None,
    }



# ---------------------------------------------------------------- git
def git(*args, check=True):
    env = dict(os.environ)
    env.update(GIT_IDENTITY)
    env["GIT_TERMINAL_PROMPT"] = "0"
    r = subprocess.run(["git", "-C", REPO] + list(args), capture_output=True, timeout=120,
                       creationflags=NO_WINDOW, env=env, stdin=subprocess.DEVNULL)
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


def acquire_lock(stale_sec=600):
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        if os.path.exists(LOCK_FILE) and time.time() - os.path.getmtime(LOCK_FILE) > stale_sec:
            os.remove(LOCK_FILE)
    except Exception:
        pass
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except Exception:
        pass


def minutes_since_last_run():
    try:
        return (time.time() - os.path.getmtime(STAMP_FILE)) / 60.0
    except Exception:
        return None


def touch_stamp():
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STAMP_FILE, "w") as f:
        f.write(dt.datetime.now(JST).isoformat(timespec="seconds"))


def main():
    global DEBUG
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--only", choices=["claude", "codex", "antigravity"])
    ap.add_argument("--try-claude-cli", action="store_true", help='claude -p "/usage" も試す（v2.1.220 では上限値は出ない）')
    ap.add_argument("--throttle-min", type=float, default=0, help="前回実行からこの分数未満なら何もせず終了")
    ap.add_argument("--reason", default="manual", help="ログ用の起動理由")
    a = ap.parse_args()
    global TRY_CLAUDE_CLI
    DEBUG = a.debug
    TRY_CLAUDE_CLI = a.try_claude_cli

    if not acquire_lock():
        log("別の collector が実行中のためスキップ (%s)" % a.reason)
        return 0
    try:
        if a.throttle_min > 0:
            m = minutes_since_last_run()
            if m is not None and m < a.throttle_min:
                return 0
        if not a.no_push:
            touch_stamp()
        log("start (%s)" % a.reason)
        return run(a)
    finally:
        release_lock()



def _parse_iso(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=JST)
        return d.astimezone(JST)
    except Exception:
        return None


def week_start_of(resets_at):
    d = _parse_iso(resets_at)
    if not d:
        return None
    return d - dt.timedelta(days=7)


def day_index_of(week_start, now=None):
    """Day 1..7. Boundaries = week_start + k*24h (floor elapsed/24h + 1)."""
    if week_start is None:
        return None
    now = now or dt.datetime.now(JST)
    elapsed = (now - week_start).total_seconds()
    if elapsed < 0:
        return 1
    d = int(elapsed // 86400) + 1
    if d < 1:
        d = 1
    if d > 7:
        d = 7
    return d


def update_day_track(svc, old_svc):
    """Keep week_start + day_start_used[day] in svc["day_track"].
    New day index → day_start_used = last_weekly_used from before the boundary
    (previous fetch). Day 1 with no prior in this week → 0. Missing → null (不明).
    """
    weekly = (svc or {}).get("weekly") or {}
    wu = weekly.get("used_percent")
    ws = week_start_of(weekly.get("resets_at"))
    old_track = ((old_svc or {}).get("day_track") or {}) if old_svc else {}
    if ws is None or wu is None:
        if old_track:
            svc["day_track"] = old_track
        return
    try:
        wu = float(wu)
    except (TypeError, ValueError):
        if old_track:
            svc["day_track"] = old_track
        return
    ws_iso = ws.isoformat(timespec="seconds")
    old_ws = _parse_iso(old_track.get("week_start"))
    same_week = old_ws is not None and abs((old_ws - ws).total_seconds()) < 120
    if same_week:
        day_start_used = dict(old_track.get("day_start_used") or {})
        # normalize keys to str
        day_start_used = {str(k): v for k, v in day_start_used.items()}
        last_wu = old_track.get("last_weekly_used")
        try:
            last_wu = float(last_wu) if last_wu is not None else None
        except (TypeError, ValueError):
            last_wu = None
    else:
        day_start_used = {}
        last_wu = None
    d = day_index_of(ws)
    dkey = str(d)
    if dkey not in day_start_used:
        if d == 1:
            day_start_used[dkey] = 0.0
        elif last_wu is not None:
            day_start_used[dkey] = last_wu
        else:
            day_start_used[dkey] = None  # 不明（推測しない）
    svc["day_track"] = {
        "week_start": ws_iso,
        "day_start_used": day_start_used,
        "last_weekly_used": wu,
    }


def export_browser_data(data, destination=None):
    """file:// 用に公開表示項目だけを data/usage-local.js に書き出す。"""
    if not isinstance(data, dict):
        return
    payload = {"fetched_at": data.get("fetched_at"), "services": {}}
    for key in ("claude", "codex"):
        raw = (data.get("services") or {}).get(key) or {}
        service = {"as_of": raw.get("as_of")}
        for name in ("five_hour", "weekly"):
            window = raw.get(name) or {}
            up = window.get("used_percent")
            try:
                up = float(up) if up is not None and not isinstance(up, bool) else None
                if up is not None and (not math.isfinite(up) or up < 0 or up > 100):
                    up = None
            except (TypeError, ValueError):
                up = None
            service[name] = {"used_percent": up, "resets_at": window.get("resets_at")}
        for name in ("reset_count", "reset_count_expires_at"):
            if name in raw:
                service[name] = raw[name]
        payload["services"][key] = service

    agy_raw = (data.get("services") or {}).get("antigravity")
    if isinstance(agy_raw, dict):
        payload["services"]["antigravity"] = {
            "as_of": agy_raw.get("as_of"),
            "current_model": agy_raw.get("current_model"),
            "totals": agy_raw.get("totals"),
            "models": agy_raw.get("models") or [],
        }

    destination = destination or os.path.join(os.path.dirname(os.path.abspath(DEFAULT_OUT)), "usage-local.js")
    text = "window.AI_USAGE_LOCAL = " + json.dumps(payload, ensure_ascii=True, allow_nan=False) + ";\n"
    temporary = destination + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    os.replace(temporary, destination)


def run(a):
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
        # 今日の枠用: 日境界時点の週使用率を記録（既存キーは維持）
        try:
            update_day_track(data["services"][name], old.get(name))
        except Exception as e:
            log("day_track error (%s): %s" % (name, e))
        log("%s: %s" % (name, json.dumps(data["services"][name], ensure_ascii=False)))

    # Antigravity モデル別使用状況
    if a.only and a.only != "antigravity":
        if "antigravity" in old:
            data["services"]["antigravity"] = old["antigravity"]
    else:
        try:
            data["services"]["antigravity"] = collect_antigravity(old_agy=old.get("antigravity"))
        except Exception as e:
            data["services"]["antigravity"] = {
                "as_of": None,
                "source": "Antigravity local transcript logs",
                "current_model": None,
                "totals": {},
                "models": [],
                "error": sanitize("unexpected: %s" % e),
            }
        log("antigravity: %d models" % len(data["services"]["antigravity"].get("models") or []))

    data["fetched_at"] = dt.datetime.now(JST).isoformat(timespec="seconds")

    txt = json.dumps(data, ensure_ascii=False, indent=2)
    if EMAIL_RE.search(txt):  # 念のため
        txt = EMAIL_RE.sub("<redacted>", txt)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt + "\n")
    os.replace(tmp, a.out)
    try:
        local_js = os.path.join(os.path.dirname(os.path.abspath(a.out)), "usage-local.js")
        export_browser_data(data, local_js)
    except Exception as e:
        log("export_browser_data error: %s" % e)
    if not a.no_push:
        try:
            push(a.out)
        except Exception as e:
            log("push error: %s" % e)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

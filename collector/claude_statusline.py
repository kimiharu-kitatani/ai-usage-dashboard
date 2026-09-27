#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Code の statusLine 用スクリプト。
Claude Code が stdin に渡す JSON から rate_limits（5時間枠・7日枠の使用率とリセット時刻）だけを
ローカルのキャッシュファイルに保存し、ステータスラインに短く表示します。
会話内容・セッションID・パス等は保存しません。collector.py がこのキャッシュを読みます。
"""
import datetime as dt
import json
import os
import sys

STATE_DIR = os.environ.get("AIUSAGE_STATE_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share"), "ai-usage-dashboard")
CACHE = os.path.join(STATE_DIR, "claude_statusline.json")


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        data = {}
    rl = data.get("rate_limits") if isinstance(data, dict) else None
    keep = {}
    if isinstance(rl, dict):
        for k in ("five_hour", "seven_day"):
            w = rl.get(k)
            if isinstance(w, dict):
                keep[k] = {"used_percentage": w.get("used_percentage"), "resets_at": w.get("resets_at")}
    if keep:
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            tmp = CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                           "rate_limits": keep}, f)
            os.replace(tmp, CACHE)
        except Exception:
            pass
    model = ((data.get("model") or {}).get("display_name") if isinstance(data, dict) else None) or "Claude"
    parts = []
    for k, label in (("five_hour", "5h"), ("seven_day", "7d")):
        v = (keep.get(k) or {}).get("used_percentage")
        if v is not None:
            try:
                parts.append("%s: %.0f%%" % (label, float(v)))
            except Exception:
                pass
    sys.stdout.write("[%s]%s\n" % (model, (" | " + " ".join(parts)) if parts else ""))


if __name__ == "__main__":
    main()

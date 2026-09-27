#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Code の statusLine 用スクリプト。
- stdin の JSON から rate_limits（5時間枠・7日枠の使用率とリセット時刻）だけをローカルに保存
  （会話内容・セッションID・パス等は保存しない）
- 前回の collector 実行から10分以上経っていれば、collector.py（取得→push）を裏で起動（待たない）
- ステータスラインには「5h 23% / 週 8%」のような短い行を表示
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hooklib  # noqa: E402

CACHE = os.path.join(hooklib.STATE_DIR, "claude_statusline.json")


def fmt_pct(v):
    try:
        return "%d%%" % round(float(v))
    except Exception:
        return "--"


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    rl = data.get("rate_limits")
    keep = {}
    if isinstance(rl, dict):
        for k in ("five_hour", "seven_day"):
            w = rl.get(k)
            if isinstance(w, dict):
                keep[k] = {"used_percentage": w.get("used_percentage"), "resets_at": w.get("resets_at")}
    if keep:
        try:
            os.makedirs(hooklib.STATE_DIR, exist_ok=True)
            tmp = CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"captured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                           "rate_limits": keep}, f)
            os.replace(tmp, CACHE)
        except Exception:
            pass
        try:
            hooklib.maybe_spawn("claude-statusline")
        except Exception:
            pass

    parts = []
    if "five_hour" in keep:
        parts.append("5h " + fmt_pct(keep["five_hour"].get("used_percentage")))
    if "seven_day" in keep:
        parts.append("週 " + fmt_pct(keep["seven_day"].get("used_percentage")))
    model = (data.get("model") or {}).get("display_name") or ""
    line = " / ".join(parts) if parts else "使用量: 次の応答後に表示"
    out = ("[%s] " % model if model else "") + line + "\n"
    sys.stdout.buffer.write(out.encode("utf-8"))


if __name__ == "__main__":
    main()

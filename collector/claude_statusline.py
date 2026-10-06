#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Code の statusLine 用スクリプト。
- stdin の JSON から rate_limits（5時間枠・7日枠の使用率とリセット時刻）だけをローカルに保存
  （会話内容・セッションID・パス等は保存しない）
- 診断用に statusline_diag.json を更新（最終実行時刻・Claude Code のバージョン・rate_limits の有無/型だけ）
- 前回の collector 実行から10分以上経っていれば、collector.py（取得→push）を裏で起動（待たない）
  ※ rate_limits が来ない場合も起動する（Codex の値はこれで更新される）
- ステータスラインには「5h 23% / 週 8%」のような短い行を表示
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hooklib  # noqa: E402

CACHE = os.path.join(hooklib.STATE_DIR, "claude_statusline.json")
DIAG = os.path.join(hooklib.STATE_DIR, "statusline_diag.json")


def fmt_pct(v):
    try:
        return "%d%%" % round(float(v))
    except Exception:
        return "--"


def write_json(path, obj):
    os.makedirs(hooklib.STATE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def main():
    parse_ok = True
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
    except Exception:
        data, parse_ok = {}, False
    if not isinstance(data, dict):
        data, parse_ok = {}, False
    rl = data.get("rate_limits")
    keep = {}
    if isinstance(rl, dict):
        for k in ("five_hour", "seven_day"):
            w = rl.get(k)
            if isinstance(w, dict):
                keep[k] = {"used_percentage": w.get("used_percentage"), "resets_at": w.get("resets_at")}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if keep:
        try:
            write_json(CACHE, {"captured_at": now, "rate_limits": keep})
        except Exception:
            pass

    # 診断情報（内容は保存しない）
    try:
        prev = {}
        if os.path.exists(DIAG):
            with open(DIAG, "r", encoding="utf-8") as f:
                prev = json.load(f)
        ctx = data.get("context_window") if isinstance(data.get("context_window"), dict) else {}
        diag = {
            "last_run": now,
            "runs": int(prev.get("runs", 0)) + 1,
            "stdin_json_ok": parse_ok,
            "claude_version": data.get("version"),
            "rate_limits": ("absent" if "rate_limits" not in data else
                            "null" if rl is None else type(rl).__name__),
            "windows": sorted(rl.keys()) if isinstance(rl, dict) else [],
            "api_called": bool(ctx.get("total_input_tokens")),
            "last_seen_with_rate_limits": now if keep else prev.get("last_seen_with_rate_limits"),
        }
        write_json(DIAG, diag)
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
    model = (data.get("model") or {}).get("display_name") if isinstance(data.get("model"), dict) else ""
    if parts:
        line = " / ".join(parts)
    elif "rate_limits" in data and rl is None:
        line = "使用量: Claude Code から提供されていません"
    else:
        line = "使用量: 次の応答後に表示"
    out = ("[%s] " % model if model else "") + line + "\n"
    sys.stdout.buffer.write(out.encode("utf-8"))


if __name__ == "__main__":
    main()

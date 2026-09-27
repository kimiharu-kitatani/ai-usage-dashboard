#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Codex のターン完了時に呼ばれるスクリプト。次のどちらからでも使える:
- ~/.codex/config.toml の notify = [...]（JSON が1つ引数で渡される。会話内容を含むため種別以外は読まず・保存しない）
- ~/.codex/hooks.json の Stop フック（stdin の JSON は読まない。標準出力には何も出さない）
前回実行から10分以上経っていれば collector.py を裏で起動する（待たない）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hooklib  # noqa: E402


def main():
    kind = None
    if len(sys.argv) > 1:
        try:
            kind = json.loads(sys.argv[-1]).get("type")
        except Exception:
            kind = None
    if kind not in (None, "agent-turn-complete"):
        return 0
    try:
        hooklib.maybe_spawn("codex-notify")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

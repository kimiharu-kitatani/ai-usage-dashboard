#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Codex の notify 用スクリプト（~/.codex/config.toml の notify = [...] から呼ばれる）。
Codex はターン完了ごとに JSON を1つ引数で渡すが、会話内容を含むため中身は読まず・保存しない。
種別が agent-turn-complete のときだけ、前回実行から10分以上経っていれば collector.py を裏で起動する。
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

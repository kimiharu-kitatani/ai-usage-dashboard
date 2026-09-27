#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Code の statusLine と Codex の notify に、このリポジトリのフックを登録します。
- 既に statusLine / notify が設定されている場合は上書きせず、内容を表示して終了します
- 変更前に settings.json / config.toml のバックアップ（*.bak-日時）を作ります
- 設定ファイルの他の内容（トークン等）は表示しません

使い方:
  python collector\\install_hooks.py --check      # 状態確認のみ（変更しない）
  python collector\\install_hooks.py              # 未設定なら登録
  python collector\\install_hooks.py --uninstall  # このリポジトリが登録した設定だけ外す
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
CLAUDE_SETTINGS = os.path.join(HOME, ".claude", "settings.json")
CODEX_CONFIG = os.path.join(os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex"), "config.toml")
STATUSLINE_SCRIPT = os.path.join(HERE, "claude_statusline.py").replace("\\", "/")
NOTIFY_SCRIPT = os.path.join(HERE, "codex_notify.py")
MARK = "claude_statusline.py"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def redact(s):
    s = EMAIL_RE.sub("<email>", str(s))
    for h in {HOME, HOME.replace("\\", "/")}:
        s = s.replace(h, "~")
    return s


def backup(path):
    base = path + ".bak-" + dt.datetime.now().strftime("%Y%m%d%H%M%S")
    b, n = base, 1
    while os.path.exists(b):
        b, n = "%s-%d" % (base, n), n + 1
    shutil.copy2(path, b)
    return b


def pythonw():
    exe = sys.executable
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else exe


# ------------------------------------------------------------ Claude
def claude(mode):
    data = {}
    if os.path.exists(CLAUDE_SETTINGS):
        try:
            with open(CLAUDE_SETTINGS, "r", encoding="utf-8-sig") as f:
                txt = f.read()
            data = json.loads(txt) if txt.strip() else {}
        except Exception as e:
            print("[Claude] settings.json を JSON として読めないため変更しません: %s" % e)
            return
    sl = data.get("statusLine")
    ours = isinstance(sl, dict) and MARK in str(sl.get("command", ""))
    if mode == "check":
        print("[Claude] settings.json: %s / statusLine: %s" % (
            "あり" if os.path.exists(CLAUDE_SETTINGS) else "なし",
            redact(json.dumps(sl, ensure_ascii=False)) if sl is not None else "未設定"))
        return
    if mode == "uninstall":
        if ours:
            print("[Claude] バックアップ: %s" % redact(backup(CLAUDE_SETTINGS)))
            del data["statusLine"]
            write_json(data)
            print("[Claude] statusLine を削除しました")
        else:
            print("[Claude] このリポジトリの statusLine は設定されていません（変更なし）")
        return
    if sl is not None:
        if ours:
            print("[Claude] 既にこのリポジトリの statusLine が設定済みです（変更なし）: %s" % redact(sl.get("command")))
        else:
            print("[Claude] 既存の statusLine があるため上書きしません: %s" % redact(json.dumps(sl, ensure_ascii=False)))
        return
    if os.path.exists(CLAUDE_SETTINGS):
        print("[Claude] バックアップ: %s" % redact(backup(CLAUDE_SETTINGS)))
    data["statusLine"] = {"type": "command", "command": "python " + STATUSLINE_SCRIPT}
    write_json(data)
    print("[Claude] statusLine を設定しました: %s" % redact(data["statusLine"]["command"]))


def write_json(data):
    os.makedirs(os.path.dirname(CLAUDE_SETTINGS), exist_ok=True)
    tmp = CLAUDE_SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, CLAUDE_SETTINGS)


# ------------------------------------------------------------ Codex
NOTIFY_LINE_RE = re.compile(r"^\s*notify\s*=", re.M)


def codex(mode):
    try:
        import tomllib
    except ImportError:
        tomllib = None
    exists = os.path.exists(CODEX_CONFIG)
    txt = ""
    if exists:
        with open(CODEX_CONFIG, "r", encoding="utf-8-sig") as f:
            txt = f.read()
    cur = None
    if tomllib and txt:
        try:
            cur = tomllib.loads(txt).get("notify")
        except Exception as e:
            print("[Codex] config.toml を解析できないため変更しません: %s" % e)
            return
    elif NOTIFY_LINE_RE.search(txt):
        cur = "（行あり）"
    ours = cur is not None and "codex_notify.py" in str(cur)
    if mode == "check":
        print("[Codex] config.toml: %s / notify: %s" % ("あり" if exists else "なし",
                                                      redact(cur) if cur is not None else "未設定"))
        return
    if mode == "uninstall":
        if ours:
            print("[Codex] バックアップ: %s" % redact(backup(CODEX_CONFIG)))
            new = "\n".join(l for l in txt.splitlines() if not ("codex_notify.py" in l and NOTIFY_LINE_RE.match(l)))
            new = new.replace("# ai-usage-dashboard: Codex のターン完了後に使用量を更新\n", "")
            with open(CODEX_CONFIG, "w", encoding="utf-8", newline="\n") as f:
                f.write(new.lstrip("\n") + ("\n" if new and not new.endswith("\n") else ""))
            print("[Codex] notify を削除しました")
        else:
            print("[Codex] このリポジトリの notify は設定されていません（変更なし）")
        return
    if cur is not None:
        if ours:
            print("[Codex] 既にこのリポジトリの notify が設定済みです（変更なし）")
        else:
            print("[Codex] 既存の notify があるため上書きしません: %s" % redact(cur))
        return
    line = "notify = ['%s', '%s']" % (pythonw(), NOTIFY_SCRIPT)
    if "'" in pythonw() + NOTIFY_SCRIPT:
        print("[Codex] パスに ' が含まれるため自動設定できません")
        return
    new = "# ai-usage-dashboard: Codex のターン完了後に使用量を更新\n" + line + "\n\n" + txt
    if tomllib:
        try:
            parsed = tomllib.loads(new)
            assert isinstance(parsed.get("notify"), list)
        except Exception as e:
            print("[Codex] 追記後の TOML 検証に失敗したため変更しません: %s" % e)
            return
    if exists:
        print("[Codex] バックアップ: %s" % redact(backup(CODEX_CONFIG)))
    os.makedirs(os.path.dirname(CODEX_CONFIG), exist_ok=True)
    with open(CODEX_CONFIG, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    print("[Codex] notify を設定しました: %s" % redact(line))


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true")
    g.add_argument("--uninstall", action="store_true")
    ap.add_argument("--only", choices=["claude", "codex"])
    a = ap.parse_args()
    mode = "check" if a.check else "uninstall" if a.uninstall else "install"
    if a.only in (None, "claude"):
        claude(mode)
    if a.only in (None, "codex"):
        codex(mode)


if __name__ == "__main__":
    main()

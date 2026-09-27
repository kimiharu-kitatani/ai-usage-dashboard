# -*- coding: utf-8 -*-
"""フック（Claude Code statusLine / Codex notify）共通: 10分スロットルで collector.py を裏で起動する"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COLLECTOR = os.path.join(HERE, "collector.py")
STATE_DIR = os.environ.get("AIUSAGE_STATE_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share"), "ai-usage-dashboard")
STAMP_FILE = os.path.join(STATE_DIR, "last_run.stamp")
THROTTLE_MIN = float(os.environ.get("AIUSAGE_THROTTLE_MIN") or 10)


def _pythonw():
    exe = sys.executable or "python"
    if os.name == "nt":
        cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(cand):
            return cand
    return exe


def due():
    try:
        return (time.time() - os.path.getmtime(STAMP_FILE)) / 60.0 >= THROTTLE_MIN
    except OSError:
        return True  # まだ一度も実行していない


def maybe_spawn(reason):
    """前回実行から THROTTLE_MIN 分以上経っていれば collector を切り離して起動（待たない）"""
    if os.environ.get("AIUSAGE_NO_SPAWN") == "1" or not due():
        return False
    args = [_pythonw(), COLLECTOR, "--throttle-min", str(THROTTLE_MIN), "--reason", reason]
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              cwd=os.path.dirname(HERE), close_fds=True)
    if os.name == "nt":
        DETACHED, NEW_GROUP, NO_WINDOW, BREAKAWAY = 0x00000008, 0x00000200, 0x08000000, 0x01000000
        for flags in (DETACHED | NEW_GROUP | NO_WINDOW | BREAKAWAY, DETACHED | NEW_GROUP | NO_WINDOW):
            try:
                subprocess.Popen(args, creationflags=flags, **kw)
                return True
            except OSError:
                continue
        return False
    subprocess.Popen(args, start_new_session=True, **kw)
    return True

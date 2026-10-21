# AI利用枠 デスクトップウィジェット（枠なし・1行）。data/usage.json をローカルで読むだけ。
import json, os, ctypes, subprocess, threading, time, datetime as dt, tkinter as tk
from ctypes import wintypes
from tkinter import font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data", "usage.json")
STATE = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "ai-usage-dashboard")
POS = os.path.join(STATE, "widget.json")
JST = dt.timezone(dt.timedelta(hours=9))
DM = "#c9ced8"
KEY = "#010203"  # 透明にする色（他では使わない）
HALO = "#000000"
BG, FG, MUTED, LINE, TRACK = "#1b1e25", "#e8eaf0", "#9aa1ae", "#3a3f4b", "#4b5060"
SERVICES = [("claude", "Claude"), ("codex", "Codex")]
WINS = [("five_hour", "5h"), ("weekly", "1w")]
PAD = 4
MARGIN = 4

class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

def work_area(hwnd):
    """ウィジェットがあるモニターの作業領域（タスクバーを除く）"""
    try:
        u = ctypes.windll.user32
        mon = u.MonitorFromWindow(wintypes.HWND(hwnd), 2)  # MONITOR_DEFAULTTONEAREST
        mi = MONITORINFO(); mi.cbSize = ctypes.sizeof(MONITORINFO)
        if u.GetMonitorInfoW(mon, ctypes.byref(mi)):
            r, m = mi.rcWork, mi.rcMonitor
            return r.left, r.top, r.right, r.bottom, m.right - m.left
        r = wintypes.RECT(); u.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
        return r.left, r.top, r.right, r.bottom, u.GetSystemMetrics(0)
    except Exception:
        return None

def color(rem):
    return TRACK if rem is None else "#3b82f6" if rem >= 50 else "#eab308" if rem >= 20 else "#ef4444"

def todt(v):
    if not v: return None
    try: return dt.datetime.fromisoformat(str(v)).astimezone(JST)
    except Exception: return None

def fmt(d): return d.strftime("%m/%d %H:%M") if d else "不明"

def left(d):
    if not d: return ""
    s = int((d - dt.datetime.now(JST)).total_seconds())
    if s <= 0: return "リセット済み"
    day, s = divmod(s, 86400); h, s = divmod(s, 3600); m = s // 60
    return "あと" + (f"{day}日" if day else "") + (f"{h}時間" if day or h else "") + f"{m}分"

def ago(d):
    if not d: return ""
    m = int((dt.datetime.now(JST) - d).total_seconds() // 60)
    return f"{m}分前" if m < 60 else f"{m // 60}時間{m % 60}分前" if m < 1440 else f"{m // 1440}日前"

class W:
    def __init__(self):
        self.r = tk.Tk(); self.r.overrideredirect(True); self.r.attributes("-topmost", True)
        self.r.configure(bg=KEY)
        try: self.r.wm_attributes("-transparentcolor", KEY)  # 1行表示の背景を透明に
        except Exception: pass
        self.f = tkfont.Font(family="Yu Gothic UI", size=9, weight="bold")
        self.fs = tkfont.Font(family="Yu Gothic UI", size=8)
        self.fd = tkfont.Font(family="Yu Gothic UI", size=11)
        self.fdb = tkfont.Font(family="Yu Gothic UI", size=11, weight="bold")
        self.c = tk.Canvas(self.r, bg=KEY, highlightthickness=0, bd=0)
        self.c.pack()
        import sys
        self.detail = "--detail" in sys.argv; self.data = None
        self.busy = False; self.status = None; self.err = None; self.spin = 0; self.tip = None
        self.c.bind("<ButtonPress-1>", self.start); self.c.bind("<B1-Motion>", self.move)
        self.c.bind("<ButtonRelease-1>", self.save_pos); self.c.bind("<Double-Button-1>", lambda e: self.toggle())
        self.m = tk.Menu(self.r, tearoff=0)
        self.m.add_command(label="詳細", command=self.toggle)
        self.m.add_command(label="更新", command=self.reload)
        self.m.add_separator(); self.m.add_command(label="閉じる", command=self.close)
        self.r.protocol("WM_DELETE_WINDOW", self.close)
        self.c.bind("<Button-3>", lambda e: self.m.tk_popup(e.x_root, e.y_root))
        for k, d in (("<Left>", (-1, 0)), ("<Right>", (1, 0)), ("<Up>", (0, -1)), ("<Down>", (0, 1))):
            self.r.bind(k, lambda e, d=d: self.snap(*d))
        self.first = False
        try:
            p = json.load(open(POS, encoding="utf-8")); self.r.geometry(f"+{int(p['x'])}+{int(p['y'])}")
        except Exception:
            self.first = True; self.r.geometry("+0+0")
            try: self.r.attributes("-alpha", 0.0)
            except Exception: pass
        self.reload(); self.loop()
        self.r.after(300, self.r.focus_force)

    def hwnd(self):
        try: return ctypes.windll.user32.GetAncestor(self.r.winfo_id(), 2) or self.r.winfo_id()
        except Exception: return self.r.winfo_id()

    def snap(self, dx, dy):
        wa = work_area(self.hwnd())
        if not wa: return
        L, T, R, B, mw = wa
        # Tk と Win32 で DPI スケールが違う場合に合わせる
        k = self.r.winfo_screenwidth() / mw if mw else 1.0
        if abs(k - 1) > 0.02: L, T, R, B = (round(v * k) for v in (L, T, R, B))
        self.r.update_idletasks()
        w, h = self.r.winfo_width(), self.r.winfo_height()
        x, y = self.r.winfo_x(), self.r.winfo_y()
        def nxt(cur, lo, hi, size, d):
            stops = [lo + MARGIN, lo + (hi - lo - size) // 2, hi - size - MARGIN]
            c = [s for s in stops if (s < cur - 1 if d < 0 else s > cur + 1)]
            return (max(c) if d < 0 else min(c)) if c else cur
        if dx: x = nxt(x, L, R, w, dx)
        if dy: y = nxt(y, T, B, h, dy)
        self.home = None
        self.r.geometry(f"+{x}+{y}"); self.save_pos(xy=(x, y))

    def start(self, e):
        self.r.focus_force()
        self.dx, self.dy = e.x_root - self.r.winfo_x(), e.y_root - self.r.winfo_y()
    def move(self, e):
        self.home = None
        self.r.geometry(f"+{e.x_root - self.dx}+{e.y_root - self.dy}")
    def save_pos(self, e=None, xy=None):
        try:
            os.makedirs(STATE, exist_ok=True)
            home = getattr(self, "home", None)
            x, y = xy or (home[:2] if home else (self.r.winfo_x(), self.r.winfo_y()))
            json.dump({"x": x, "y": y, "w": self.r.winfo_width(), "h": self.r.winfo_height()}, open(POS, "w", encoding="utf-8"))
        except Exception: pass
    def toggle(self):
        self.r.update_idletasks()
        x, y, w, h = self.r.winfo_x(), self.r.winfo_y(), self.r.winfo_width(), self.r.winfo_height()
        if not self.detail:
            wa = work_area(self.hwnd())
            ar = ab = False
            if wa:
                ar = x + w >= wa[2] - MARGIN - 2 or x + w / 2 > (wa[0] + wa[2]) / 2
                ab = y + h >= wa[3] - MARGIN - 2 or y + h / 2 > (wa[1] + wa[3]) / 2
            self.home = (x, y, w, h, ar, ab)   # 開く前の位置（ユーザーの位置）
        self.detail = not self.detail; self.draw()

    def loop(self): self.reload(); self.r.after(5 * 60 * 1000, self.loop)
    def reload(self):
        try: self.data = json.load(open(DATA, encoding="utf-8"))
        except Exception: self.data = None
        self.draw()

    def icon(self, key, x, y):
        c = self.c; cx, cy = x + 7, y + 7
        if key == "claude":
            import math
            for a in range(0, 180, 30):
                r = math.radians(a); dx, dy = 6.5 * math.cos(r), 6.5 * math.sin(r)
                c.create_line(cx - dx, cy - dy, cx + dx, cy + dy, fill="#D97757", width=2, capstyle="round")
        else:
            c.create_rectangle(x, y, x + 14, y + 14, fill="#000000", outline="#6b7280")
            c.create_line(x + 3, y + 4, x + 6, y + 7, x + 3, y + 10, fill="#ffffff", width=1.6)
            c.create_line(x + 7.5, y + 10.5, x + 11, y + 10.5, fill="#ffffff", width=1.6)
        return x + 14

    def text(self, x, y, s, fnt=None, fill=FG):
        fnt = fnt or self.f
        if getattr(self, "_halo", False):  # 透明背景でも読めるよう黒い縁取り
            for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
                self.c.create_text(x + ox, y + oy, text=s, anchor="w", font=fnt, fill=HALO)
        self.c.create_text(x, y, text=s, anchor="w", font=fnt, fill=fill)
        return x + fnt.measure(s)

    def draw(self):
        c = self.c; c.delete("all"); self._halo = True
        svcs = (self.data or {}).get("services", {})
        H = 20; y = PAD + H // 2; x = PAD + 2
        for i, (key, name) in enumerate(SERVICES):
            if i:
                x += 5; c.create_line(x, PAD + 2, x, PAD + H - 2, fill="#9aa1ae", width=2); x += 6
            x = self.icon(key, x, y - 7) + 4
            s = svcs.get(key) or {}
            for wk, wl in WINS:
                w = s.get(wk) or {}
                u = w.get("used_percent")
                rem = None if u is None else max(0.0, min(100.0, 100 - float(u)))
                x = self.text(x, y, wl, self.fs, MUTED) + 3
                bw, bh = 24, 10
                c.create_rectangle(x - 1, y - bh / 2 - 1, x + bw + 1, y + bh / 2 + 1, outline=HALO, fill=HALO if rem is None else "#202430")
                c.create_rectangle(x + bw + 1, y - 3, x + bw + 4, y + 3, fill=HALO, outline=HALO)
                c.create_rectangle(x, y - bh / 2, x + bw, y + bh / 2, outline="#d0d4dc", dash=(2, 2) if rem is None else None)
                c.create_rectangle(x + bw + 1, y - 2, x + bw + 3, y + 2, fill="#d0d4dc", outline="#d0d4dc")
                if rem:
                    c.create_rectangle(x + 2, y - bh / 2 + 2, x + 2 + (bw - 3) * rem / 100, y + bh / 2 - 1, fill=color(rem), width=0)
                x += bw + 6
                x = (self.text(x, y, "取得なし", self.fs, MUTED) if rem is None else self.text(x, y, f"{round(rem)}%")) + 6
        x += 6
        for tag, label, fn in (("b_det", "▲" if self.detail else "▼", self.toggle), ("b_rel", self.rel_label(), self.refresh)):
            c.create_rectangle(x, PAD + 2, x + 16, PAD + H - 2, outline="#9aa1ae", fill=BG, tags=tag)
            c.create_text(x + 8, y, text=label, font=self.fs, fill=FG, tags=tag)
            c.tag_bind(tag, "<ButtonRelease-1>", lambda e, fn=fn: self.r.after(1, fn))
            if tag == "b_rel":
                c.tag_bind(tag, "<Enter>", lambda e: self.show_tip(e)); c.tag_bind(tag, "<Leave>", lambda e: self.hide_tip())
            x += 19
        width = x - 2 + PAD
        height = PAD * 2 + H
        self._halo = False
        if self.detail:
            fd, fdb = self.fd, self.fdb
            self._panel = c.create_rectangle(0, PAD + H + 1, 2000, 2000, fill=BG, outline=LINE)
            LH = fd.metrics("linespace") + 4
            x0 = PAD + 10; c1 = x0 + 8 + fdb.measure("1w") + 12
            c2 = c1 + fdb.measure("残り100%") + 16
            yy = PAD + H + 6
            c.create_line(PAD + 2, yy - 3, width - PAD - 2, yy - 3, fill=LINE)
            fa = todt((self.data or {}).get("fetched_at"))
            for si, (key, name) in enumerate(SERVICES):
                s = svcs.get(key) or {}
                if si: yy += 4; c.create_line(x0, yy - 2, max(width, 300) - PAD - 6, yy - 2, fill=LINE); yy += 4
                self.icon(key, x0, yy + LH // 2 - 7)
                width = max(width, self.text(x0 + 20, yy + LH // 2, name, fdb) + PAD + 10); yy += LH
                for wk, wl in WINS:
                    w = s.get(wk) or {}; u = w.get("used_percent"); d = todt(w.get("resets_at"))
                    rem = None if u is None else max(0.0, min(100.0, 100 - float(u)))
                    cy = yy + LH // 2
                    self.text(x0 + 8, cy, wl, fdb, DM)
                    self.text(c1, cy, "取得なし" if rem is None else f"残り{round(rem)}%", fdb, DM if rem is None else color(rem))
                    rt = (f"リセット {fmt(d)}（{left(d)}）" if d else "リセット 取得なし")
                    width = max(width, self.text(c2, cy, rt, fd) + PAD + 10); yy += LH
                a = todt(s.get("as_of")); rc = s.get("reset_count")
                old = a and (dt.datetime.now(JST) - a).total_seconds() > 6 * 3600
                line = f"取得 {fmt(a)}（{ago(a)}）" if a else "取得 不明"
                if old: line += "  古い値"
                rcs = "リセット回数 " + ("取得なし" if rc is None else f"{rc}回")
                self.text(x0 + 8, yy + LH // 2, rcs, fd, DM); yy += LH
                width = max(width, self.text(x0 + 8, yy + LH // 2, line, fd, "#eab308" if old else DM) + PAD + 10); yy += LH
            msg = "最終更新 " + fmt(fa) + (f"（{ago(fa)}）" if fa else "")
            if self.busy: msg += "  更新中…"
            if self.err: msg += "  更新失敗: " + self.err[:60]
            yy += 4; c.create_line(x0, yy - 2, width - PAD - 6, yy - 2, fill=LINE); yy += 2
            width = max(width, self.text(x0, yy + LH // 2, msg, fd, "#f87171" if self.err else DM) + PAD + 10); yy += LH
            height = yy + PAD + 2
            c.coords(self._panel, 0, PAD + H + 1, width - 1, height - 1)
        c.config(width=width, height=height)
        self.r.after(10, self.clamp)

    def rel_label(self):
        if self.busy: return "◐◓◑◒"[self.spin % 4]
        return {"ok": "✓", "ng": "✗"}.get(self.status, "↻")

    def show_tip(self, e):
        self.hide_tip()
        t = "更新中…" if self.busy else ("更新失敗: " + self.err) if self.err else "今すぐ取得（Claude・Codex）"
        self.tip = tk.Toplevel(self.r); self.tip.overrideredirect(True); self.tip.attributes("-topmost", True)
        tk.Label(self.tip, text=t, bg="#000000", fg="#ffffff", font=self.fs, padx=4, pady=1, wraplength=360, justify="left").pack()
        self.tip.geometry(f"+{e.x_root + 8}+{e.y_root + 14}")

    def hide_tip(self):
        if self.tip: self.tip.destroy(); self.tip = None

    def refresh(self):
        """↻: 裏で Claude ping + Codex 取得 + push を実行し、終わったら表示を読み直す"""
        if self.busy: return
        self.busy = True; self.status = None; self.err = None; self.draw(); self.animate()
        threading.Thread(target=self._work, daemon=True).start()

    def animate(self):
        if not self.busy: return
        self.spin += 1; self.draw(); self.r.after(250, self.animate)

    def _work(self):
        err = None
        try:
            before = (json.load(open(DATA, encoding="utf-8")) or {}).get("fetched_at")
        except Exception:
            before = None
        try:
            ps = os.path.join(os.path.dirname(HERE), "collector", "claude_ping.ps1")
            r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", ps],
                               capture_output=True, timeout=240, creationflags=0x08000000, stdin=subprocess.DEVNULL)
            after = (json.load(open(DATA, encoding="utf-8")) or {}).get("fetched_at")
            if after == before:
                err = "データが更新されませんでした（別の取得が実行中の可能性）"
            else:
                logp = os.path.join(STATE, "collector.log")
                try:
                    tail = open(logp, encoding="utf-8", errors="replace").read()[-3000:]
                    last = [l for l in tail.splitlines() if "ping:" in l or "push error" in l]
                    if last and "updated=False" in last[-1]: err = "Claude の値を取れませんでした（Codex は更新）"
                    elif any("push error" in l for l in last[-2:]): err = "公開ページへの反映に失敗"
                except Exception: pass
        except subprocess.TimeoutExpired:
            err = "タイムアウト"
        except Exception as e:
            err = str(e)
        self.r.after(0, self._done, err)

    def _done(self, err):
        self.busy = False; self.err = err; self.status = "ng" if err else "ok"
        self.reload()
        self.r.after(3000 if not err else 8000, self._clear_status)

    def _clear_status(self):
        if not self.busy: self.status = None; self.draw()

    def close(self):
        self.save_pos(); self.r.destroy()

    def clamp(self):
        """画面下端・右端からはみ出したら作業領域内に戻す（下端に付けていれば上に伸びる）"""
        wa = work_area(self.hwnd())
        if not wa: return
        L, T, R, B, _ = wa
        self.r.update_idletasks()
        w, h, x, y = self.r.winfo_width(), self.r.winfo_height(), self.r.winfo_x(), self.r.winfo_y()
        if self.first:  # 保存位置なし: メインモニターの作業領域の右上
            self.first = False
            try:
                r = wintypes.RECT(); ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
                k = self.r.winfo_screenwidth() / (ctypes.windll.user32.GetSystemMetrics(0) or 1)
                L, T, R, B = (round(v * k) for v in (r.left, r.top, r.right, r.bottom))
            except Exception: pass
            x, y = R - w - MARGIN, T + MARGIN
            self.r.geometry(f"+{x}+{y}"); self.r.attributes("-alpha", 1.0); self.save_pos(xy=(x, y)); return
        # 保存位置が見える範囲外（モニター構成が変わった等）なら、いちばん近いモニターの作業領域に収める
        nx = max(L + MARGIN, min(x, R - w - MARGIN)); ny = max(T + MARGIN, min(y, B - h - MARGIN))
        home = getattr(self, "home", None)
        if home:
            hx, hy, hw, hh, ar, ab = home
            if self.detail:
                # 右寄せなら左へ、下寄せなら上へ伸ばす。ずらした位置は保存しない
                nx = hx + hw - w if ar else hx; ny = hy + hh - h if ab else hy
                nx = max(L + MARGIN, min(nx, R - w - MARGIN)); ny = max(T + MARGIN, min(ny, B - h - MARGIN))
            else:
                nx, ny = hx, hy; self.home = None
            if (nx, ny) != (x, y): self.r.geometry(f"+{nx}+{ny}")
            return
        if (nx, ny) != (x, y): self.r.geometry(f"+{nx}+{ny}"); self.save_pos(xy=(nx, ny))

def single_instance():
    try:
        k = ctypes.windll.kernel32
        k.CreateMutexW.restype = wintypes.HANDLE
        h = k.CreateMutexW(None, False, "Local\\AIUsageDashboardWidget")
        return h if k.GetLastError() != 183 else None  # ERROR_ALREADY_EXISTS
    except Exception:
        return True

if __name__ == "__main__":
    _mutex = single_instance()
    if _mutex:
        W().r.mainloop()

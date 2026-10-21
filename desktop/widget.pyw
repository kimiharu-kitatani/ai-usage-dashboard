# AI利用枠 デスクトップウィジェット（枠なし・1行）。data/usage.json をローカルで読むだけ。
import json, os, ctypes, datetime as dt, tkinter as tk
from ctypes import wintypes
from tkinter import font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data", "usage.json")
STATE = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "ai-usage-dashboard")
POS = os.path.join(STATE, "widget.json")
JST = dt.timezone(dt.timedelta(hours=9))
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
        self.r.configure(bg=BG)
        self.f = tkfont.Font(family="Yu Gothic UI", size=9, weight="bold")
        self.fs = tkfont.Font(family="Yu Gothic UI", size=8)
        self.c = tk.Canvas(self.r, bg=BG, highlightthickness=1, highlightbackground=LINE, bd=0)
        self.c.pack()
        import sys
        self.detail = "--detail" in sys.argv; self.data = None
        self.c.bind("<ButtonPress-1>", self.start); self.c.bind("<B1-Motion>", self.move)
        self.c.bind("<ButtonRelease-1>", self.save_pos); self.c.bind("<Double-Button-1>", lambda e: self.toggle())
        self.m = tk.Menu(self.r, tearoff=0)
        self.m.add_command(label="詳細", command=self.toggle)
        self.m.add_command(label="更新", command=self.reload)
        self.m.add_separator(); self.m.add_command(label="閉じる", command=self.r.destroy)
        self.c.bind("<Button-3>", lambda e: self.m.tk_popup(e.x_root, e.y_root))
        for k, d in (("<Left>", (-1, 0)), ("<Right>", (1, 0)), ("<Up>", (0, -1)), ("<Down>", (0, 1))):
            self.r.bind(k, lambda e, d=d: self.snap(*d))
        try:
            p = json.load(open(POS, encoding="utf-8")); self.r.geometry(f"+{p['x']}+{p['y']}")
        except Exception:
            self.r.geometry("+40+40")
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
        self.r.geometry(f"+{x}+{y}"); self.save_pos(xy=(x, y))

    def start(self, e):
        self.r.focus_force()
        self.dx, self.dy = e.x_root - self.r.winfo_x(), e.y_root - self.r.winfo_y()
    def move(self, e): self.r.geometry(f"+{e.x_root - self.dx}+{e.y_root - self.dy}")
    def save_pos(self, e=None, xy=None):
        try:
            os.makedirs(STATE, exist_ok=True)
            x, y = xy or (self.r.winfo_x(), self.r.winfo_y())
            json.dump({"x": x, "y": y, "w": self.r.winfo_width(), "h": self.r.winfo_height()}, open(POS, "w", encoding="utf-8"))
        except Exception: pass
    def toggle(self): self.detail = not self.detail; self.draw()
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
        self.c.create_text(x, y, text=s, anchor="w", font=fnt, fill=fill)
        return x + fnt.measure(s)

    def draw(self):
        c = self.c; c.delete("all")
        svcs = (self.data or {}).get("services", {})
        H = 20; y = PAD + H // 2; x = PAD + 2
        for i, (key, name) in enumerate(SERVICES):
            if i:
                x += 5; c.create_line(x, PAD + 2, x, PAD + H - 2, fill=LINE); x += 6
            x = self.icon(key, x, y - 7) + 4
            s = svcs.get(key) or {}
            for wk, wl in WINS:
                w = s.get(wk) or {}
                u = w.get("used_percent")
                rem = None if u is None else max(0.0, min(100.0, 100 - float(u)))
                x = self.text(x, y, wl, self.fs, MUTED) + 3
                bw, bh = 24, 10
                c.create_rectangle(x, y - bh / 2, x + bw, y + bh / 2, outline=MUTED, dash=(2, 2) if rem is None else None)
                c.create_rectangle(x + bw + 1, y - 2, x + bw + 3, y + 2, fill=MUTED, outline=MUTED)
                if rem:
                    c.create_rectangle(x + 2, y - bh / 2 + 2, x + 2 + (bw - 3) * rem / 100, y + bh / 2 - 1, fill=color(rem), width=0)
                x += bw + 6
                x = (self.text(x, y, "取得なし", self.fs, MUTED) if rem is None else self.text(x, y, f"{round(rem)}%")) + 6
        x += 2; c.create_line(x, PAD + 2, x, PAD + H - 2, fill=LINE); x += 4
        for tag, label, fn in (("b_det", "▲" if self.detail else "▼", self.toggle), ("b_rel", "↻", self.reload)):
            c.create_rectangle(x, PAD + 2, x + 16, PAD + H - 2, outline=LINE, fill=BG, tags=tag)
            c.create_text(x + 8, y, text=label, font=self.fs, fill=FG, tags=tag)
            c.tag_bind(tag, "<ButtonRelease-1>", lambda e, fn=fn: self.r.after(1, fn))
            x += 19
        width = x - 2 + PAD
        height = PAD * 2 + H
        if self.detail:
            yy = PAD + H + 2
            fa = todt((self.data or {}).get("fetched_at"))
            for key, name in SERVICES:
                s = svcs.get(key) or {}
                parts = []
                for wk, wl in WINS:
                    w = s.get(wk) or {}; d = todt(w.get("resets_at"))
                    parts.append(f"{wl} ↻{fmt(d)} {left(d)}" if d else f"{wl} リセット時刻 取得なし")
                a = todt(s.get("as_of"))
                old = a and (dt.datetime.now(JST) - a).total_seconds() > 6 * 3600
                rc = s.get("reset_count")
                parts.append(("古い値 " if old else "") + f"値 {fmt(a)}（{ago(a)}）")
                parts.append("リセット回数 " + ("取得なし" if rc is None else f"{rc}回"))
                self.icon(key, PAD + 2, yy + 2)
                xe = self.text(PAD + 20, yy + 9, " · ".join(parts), self.fs, "#eab308" if old else FG)
                width = max(width, xe + PAD + 2); yy += 17
            if not fa:
                xe = self.text(PAD + 2, yy + 8, "data/usage.json を読めません", self.fs, "#ef4444"); width = max(width, xe + PAD); yy += 16
            height = yy + PAD
        c.config(width=width, height=height)

if __name__ == "__main__":
    W().r.mainloop()

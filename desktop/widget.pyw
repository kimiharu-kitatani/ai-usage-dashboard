# AI利用枠 デスクトップウィジェット（枠なし・1行）。data/usage.json をローカルで読むだけ。
import json, os, sys, base64, ctypes, math, subprocess, threading, time, datetime as dt, webbrowser, tkinter as tk
from ctypes import wintypes
from tkinter import font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data", "usage.json")
STATE = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "ai-usage-dashboard")
POS = os.path.join(STATE, "widget.json")
NOTIFIED = os.path.join(STATE, "notified.json")
NOTIFY_MAX_AGE = 6 * 3600  # スリープ等で遅れた通知はこの秒数以内なら出す
JST = dt.timezone(dt.timedelta(hours=9))
DM = "#c9ced8"
BG_ALPHA = 0.25  # 1行表示の背景の濃さ（0=透明〜1=不透明）
BG_ALPHA_DARK = 0.25   # 後ろが暗いときの濃さ
BG_ALPHA_LIGHT = 1.0   # 後ろが明るいときの濃さ
LUM_TO_LIGHT = 0.6     # 明るさがこれを超えたら LIGHT に
LUM_TO_DARK = 0.45     # これを下回ったら DARK に（ちらつき防止の幅）
SAMPLE_MS = 3000       # 背景の明るさを調べる間隔
KEY = "#010203"  # 透明にする色（他では使わない）
HALO = "#000000"
BG, FG, MUTED, LINE, TRACK = "#1b1e25", "#e8eaf0", "#9aa1ae", "#3a3f4b", "#4b5060"
PACE_OK, PACE_WARN, PACE_BAD = "#22c55e", "#eab308", "#ef4444"
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

def short_left(d):
    """1行表示用の残り時間（1日未満 h:mm、以上 Nd）"""
    if not d: return ""
    s = int((d - dt.datetime.now(JST)).total_seconds())
    if s <= 0: return "0:00"
    if s >= 86400: return f"{s // 86400}d"
    return f"{s // 3600}:{s % 3600 // 60:02d}"


def rem_color(rem):
    """残量%の色（電池と同じ閾値: >=50緑 / 20-50黄 / <20赤）"""
    if rem is None: return MUTED
    if rem >= 50: return PACE_OK
    if rem >= 20: return PACE_WARN
    return PACE_BAD

def today_quota(five, weekly, day_track):
    """今日の枠の残量%。day_track.day_start_used[d] が必要。推測しない。
    shown = min(今日ペース残%, 5h残%)。five_limited なら 5h 側。
    """
    five = five or {}; weekly = weekly or {}; day_track = day_track or {}
    wu = weekly.get("used_percent"); wr = todt(weekly.get("resets_at"))
    fu = five.get("used_percent"); fr = todt(five.get("resets_at"))
    if wu is None or wr is None:
        return {"unknown": True}
    try: wu = float(wu)
    except (TypeError, ValueError):
        return {"unknown": True}
    ws = todt(day_track.get("week_start")) or (wr - dt.timedelta(days=7))
    elapsed = (dt.datetime.now(JST) - ws).total_seconds()
    d = int(elapsed // 86400) + 1 if elapsed >= 0 else 1
    if d < 1: d = 1
    if d > 7: d = 7
    week_allowed = d * 100 / 7
    dsu_map = day_track.get("day_start_used") or {}
    if str(d) not in dsu_map and d not in dsu_map:
        return {"unknown": True}
    dsu = dsu_map.get(str(d), dsu_map.get(d))
    if dsu is None:
        return {"unknown": True}
    try: dsu = float(dsu)
    except (TypeError, ValueError):
        return {"unknown": True}
    today_q = week_allowed - dsu
    today_u = wu - dsu
    now = dt.datetime.now(JST)
    if fr is not None and fr < now:
        five_remain = 100.0
        five_used = 0.0
    elif fu is not None:
        try:
            five_used = float(fu)
            five_remain = 100.0 - five_used
        except (TypeError, ValueError):
            return {"unknown": True}
    else:
        return {"unknown": True}
    if today_q <= 0:
        return {
            "unknown": False, "no_quota": True, "shown": None,
            "label": "今日の枠なし", "tag": "日", "color": PACE_BAD,
            "week_allowed": week_allowed, "today_quota": today_q,
            "today_used": today_u, "five_limited": False, "day": d,
            "five_remain": five_remain, "remain_today": today_q - today_u,
        }
    remain_today = today_q - today_u
    remain_pct = max(0.0, (remain_today / today_q) * 100.0)
    five_limited = five_remain < remain_today
    if five_limited:
        shown = max(0, int(math.floor(five_remain)))
        label = "5時間枠で制限中"
        tag = "5h"
        rem_for_color = five_remain
    else:
        shown = max(0, int(math.floor(remain_pct)))
        label = "今日の分で制限中"
        tag = "日"
        rem_for_color = remain_pct
    return {
        "unknown": False, "no_quota": False, "shown": shown, "label": label,
        "tag": tag, "color": rem_color(rem_for_color),
        "week_allowed": week_allowed,
        "today_quota": today_q, "today_used": today_u,
        "five_limited": five_limited, "day": d,
        "remain_pct": remain_pct, "five_remain": five_remain,
        "remain_today": remain_today,
    }

def today_line(tr):
    """詳細用の一文（サブテキスト）。"""
    if tr.get("unknown"):
        return "今日の枠 不明"
    if tr.get("no_quota"):
        return "今日の枠なし"
    tq = tr["today_quota"]
    tu = max(0.0, tr["today_used"])
    tq_s = f"{tq:.1f}".rstrip("0").rstrip(".")
    tu_s = f"{tu:.1f}".rstrip("0").rstrip(".")
    base = f"今日使える分 {tq_s}% のうち {tu_s}% 使用（週の枠全体に対する%）"
    if tr.get("five_limited"):
        return base + " · 5時間枠で制限中"
    return base + " · 今日の分で制限中"

HELP_URL = "https://kimiharu-kitatani.github.io/ai-usage-dashboard/help.html"


WD = "月火水木金土日"
def outlook(rem, d):
    """週の残り%とリセット曜日から見通し文（値がなければ None）"""
    if rem is None or not d: return None
    w = WD[d.weekday()] + "曜日"
    if rem >= 50: return f"順調です。{w}のリセットまで余裕を持って利用できる見込みです。"
    if rem >= 20: return f"やや注意。{w}のリセットまで計画的に。"
    return f"余裕少なめ。{w}のリセットまで注意。"

def toast(title, body):
    """Windows のトースト通知（PowerShell の WinRT API。追加インストール不要）"""
    def q(t): return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;").replace('"', "&quot;")
    xml = f"<toast><visual><binding template='ToastGeneric'><text>{q(title)}</text><text>{q(body)}</text></binding></visual></toast>"
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
          "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
          "$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml('" + xml.replace("'", "''") + "');"
          "$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    enc = base64.b64encode(ps.encode("utf-16-le")).decode()
    try:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", enc],
                         creationflags=0x08000000, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception: pass

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
        self.ft = tkfont.Font(family="Yu Gothic UI", size=10, weight="bold")  # あとX%
        self.fd = tkfont.Font(family="Yu Gothic UI", size=11)
        self.fdb = tkfont.Font(family="Yu Gothic UI", size=11, weight="bold")
        # 半透明の背景用ウィンドウ（本体の真後ろに置き、一緒に動かす）
        self.bgw = tk.Toplevel(self.r); self.bgw.overrideredirect(True); self.bgw.configure(bg=BG)
        self.bg_alpha = BG_ALPHA_DARK
        try: self.bgw.attributes("-alpha", BG_ALPHA_DARK); self.bgw.attributes("-topmost", True)
        except Exception: pass
        self.row_w, self.row_h = 10, 10
        self.c = tk.Canvas(self.r, bg=KEY, highlightthickness=0, bd=0)
        self.c.pack()
        self.detail = "--detail" in sys.argv; self.data = None
        self.busy = False; self.status = None; self.err = None; self.spin = 0; self.tip = None
        for wdg in (self.c, self.bgw):
            wdg.bind("<ButtonPress-1>", self.start); wdg.bind("<B1-Motion>", self.move)
            wdg.bind("<ButtonRelease-1>", self.save_pos); wdg.bind("<Double-Button-1>", lambda e: self.toggle())
        self.r.bind("<Configure>", lambda e: self.sync_bg())
        self.m = tk.Menu(self.r, tearoff=0)
        self.m.add_command(label="詳細", command=self.toggle)
        self.m.add_command(label="更新", command=self.reload)
        self.m.add_separator(); self.m.add_command(label="閉じる", command=self.close)
        self.r.protocol("WM_DELETE_WINDOW", self.close)
        self.c.bind("<Button-3>", lambda e: self.m.tk_popup(e.x_root, e.y_root))
        self.bgw.bind("<Button-3>", lambda e: self.m.tk_popup(e.x_root, e.y_root))
        for k, d in (("<Left>", (-1, 0)), ("<Right>", (1, 0)), ("<Up>", (0, -1)), ("<Down>", (0, 1))):
            self.r.bind(k, lambda e, d=d: self.snap(*d)); self.bgw.bind(k, lambda e, d=d: self.snap(*d))
        self.first = False
        try:
            p = json.load(open(POS, encoding="utf-8")); self.r.geometry(f"+{int(p['x'])}+{int(p['y'])}")
        except Exception:
            self.first = True; self.r.geometry("+0+0")
            try: self.r.attributes("-alpha", 0.0)
            except Exception: pass
        self.reload(); self.loop(); self.r.after(5000, self.check_resets)
        if "--test-toast" in sys.argv: toast("AI利用枠ウィジェット", "テスト通知: Claude の5時間枠がリセットされました（表示確認用）")
        self.r.after(300, self.r.focus_force); self.r.after(500, self.keep_bg); self.r.after(800, self.adapt_bg)

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

    def sync_bg(self):
        try:
            self.bgw.geometry(f"{self.row_w}x{self.row_h}+{self.r.winfo_x()}+{self.r.winfo_y()}")
            self.r.lift(); self.bgw.lower(self.r)
        except Exception: pass

    def sample_lum(self):
        """ウィジェットのすぐ外側の数点の画面色から明るさ(0-1)を出す。
        GetPixel は物理ピクセル座標。tk の論理座標に DPI 倍率をかける。"""
        try:
            u, g = ctypes.windll.user32, ctypes.windll.gdi32
            # DPI 倍率（論理→物理）。ウィンドウの DPI を優先
            scale = 1.0
            try:
                hwnd = self.r.winfo_id()
                # winfo_id は Tk の内部ハンドル。親の実 HWND を取る
                hwnd = u.GetParent(hwnd) or hwnd
                dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                if dpi:
                    scale = dpi / 96.0
            except Exception:
                try:
                    scale = float(ctypes.windll.shcore.GetScaleFactorForDevice(0)) / 100.0
                except Exception:
                    try:
                        hdc = u.GetDC(0); dpi = g.GetDeviceCaps(hdc, 88); u.ReleaseDC(0, hdc)
                        scale = dpi / 96.0
                    except Exception:
                        scale = 1.0
            x = int(self.r.winfo_x() * scale)
            y = int(self.r.winfo_y() * scale)
            w = int(self.row_w * scale)
            h = int(self.row_h * scale)
            # 仮想画面の物理サイズ（マルチモニタ）
            vx = u.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
            vy = u.GetSystemMetrics(77)
            vw = u.GetSystemMetrics(78)
            vh = u.GetSystemMetrics(79)
            pad = max(4, int(6 * scale))
            pts = []
            for i in range(1, 6):
                px = x + w * i // 6
                pts += [(px, y - pad), (px, y + h + pad)]
            pts += [(x - pad, y + h // 2), (x + w + pad, y + h // 2)]
            dc = u.GetDC(0); vals = []
            for px, py in pts:
                if vx <= px < vx + vw and vy <= py < vy + vh:
                    c = g.GetPixel(dc, px, py)
                    if c != 0xFFFFFFFF:
                        r, gg, b = c & 255, (c >> 8) & 255, (c >> 16) & 255
                        vals.append((0.2126 * r + 0.7152 * gg + 0.0722 * b) / 255)
            u.ReleaseDC(0, dc)
            return sum(vals) / len(vals) if vals else None
        except Exception:
            return None

    def adapt_bg(self):
        self.adapt_once(); self.r.after(SAMPLE_MS, self.adapt_bg)

    def adapt_once(self):
        lum = self.sample_lum()
        if lum is not None:
            cur = getattr(self, "bg_alpha", BG_ALPHA_DARK)
            new = BG_ALPHA_LIGHT if lum > LUM_TO_LIGHT else BG_ALPHA_DARK if lum < LUM_TO_DARK else cur
            if new != cur:
                self.bg_alpha = new
                try: self.bgw.attributes("-alpha", new)
                except Exception: pass

    def keep_bg(self):
        self.sync_bg(); self.r.after(1500, self.keep_bg)

    def start(self, e):
        self.r.focus_force(); self.sync_bg()
        self.dx, self.dy = e.x_root - self.r.winfo_x(), e.y_root - self.r.winfo_y()
    def move(self, e):
        self.home = None
        self.r.geometry(f"+{e.x_root - self.dx}+{e.y_root - self.dy}"); self.sync_bg()
    def save_pos(self, e=None, xy=None):
        try:
            os.makedirs(STATE, exist_ok=True)
            home = getattr(self, "home", None)
            x, y = xy or (home[:2] if home else (self.r.winfo_x(), self.r.winfo_y()))
            self.r.after(300, self.adapt_once)  # 動かした直後にも明るさを確認
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

    def check_resets(self):
        """保存された resets_at を過ぎたら1回だけ通知（遅れは6時間まで）。その後に値を取り直す"""
        try:
            try: done = json.load(open(NOTIFIED, encoding="utf-8"))
            except Exception: done = {}
            now = dt.datetime.now(JST); fired = []; changed = False
            svcs = (self.data or {}).get("services", {})
            for key, name in SERVICES:
                for wk, wl in WINS:
                    d = todt(((svcs.get(key) or {}).get(wk) or {}).get("resets_at"))
                    if not d or d > now: continue
                    k = f"{key}:{wk}:{d.isoformat()}"
                    if k in done: continue
                    done[k] = now.isoformat(timespec="seconds"); changed = True
                    if (now - d).total_seconds() <= NOTIFY_MAX_AGE:
                        toast(f"{name} の{'5時間' if wk == 'five_hour' else '週間'}枠がリセットされました",
                              f"リセット時刻 {fmt(d)}")
                        fired.append(key)
            if changed:
                cut = (now - dt.timedelta(days=30)).isoformat()
                done = {k: v for k, v in done.items() if v >= cut}
                os.makedirs(STATE, exist_ok=True); json.dump(done, open(NOTIFIED, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            if fired and not self.busy:
                # Claude はping 1回（↻と同じ）。Codex だけなら collector のみ（Claude ping なし）
                if "claude" in fired: self.r.after(60 * 1000, self.refresh)
                else: self.r.after(60 * 1000, self.fetch_codex)
        except Exception: pass
        self.r.after(60 * 1000, self.check_resets)

    def fetch_codex(self):
        if self.busy: return
        def run():
            try:
                py = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                subprocess.run([py if os.path.exists(py) else sys.executable, os.path.join(os.path.dirname(HERE), "collector", "collector.py"),
                                "--reason", "widget-reset"], timeout=180, creationflags=0x08000000, stdin=subprocess.DEVNULL, capture_output=True)
            except Exception: pass
            self.r.after(0, self.reload)
        threading.Thread(target=run, daemon=True).start()

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
            tr = today_quota(s.get("five_hour"), s.get("weekly"), s.get("day_track"))
            if tr.get("unknown"):
                x = self.text(x, y, "不明", self.ft, MUTED) + 2
                x = self.text(x, y, "日", self.fs, MUTED) + 6
            elif tr.get("no_quota"):
                x = self.text(x, y, "枠なし", self.ft, PACE_BAD) + 2
                x = self.text(x, y, "日", self.fs, MUTED) + 6
            else:
                x = self.text(x, y, f"{tr['shown']}%", self.ft, tr["color"]) + 2
                x = self.text(x, y, tr.get("tag") or "日", self.fs, MUTED) + 6
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
        self.row_w, self.row_h = width, height
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
                tr = today_quota(s.get("five_hour"), s.get("weekly"), s.get("day_track"))
                tline = today_line(tr)
                tcol = MUTED if tr.get("unknown") else tr.get("color", FG)
                width = max(width, self.text(x0 + 8, yy + LH // 2, tline, fdb if not tr.get("unknown") else fd, tcol) + PAD + 10); yy += LH
                for wk, wl in WINS:
                    w = s.get(wk) or {}; u = w.get("used_percent"); d = todt(w.get("resets_at"))
                    rem = None if u is None else max(0.0, min(100.0, 100 - float(u)))
                    cy = yy + LH // 2
                    self.text(x0 + 8, cy, wl, fdb, DM)
                    # 小さい電池バー（週は目盛り＋ペース線）
                    bx = c1
                    bw, bh = 48, 10
                    c.create_rectangle(bx, cy - bh / 2, bx + bw, cy + bh / 2, outline="#d0d4dc",
                                       dash=(2, 2) if rem is None else None)
                    c.create_rectangle(bx + bw + 1, cy - 2, bx + bw + 3, cy + 2, fill="#d0d4dc", outline="#d0d4dc")
                    if rem:
                        c.create_rectangle(bx + 2, cy - bh / 2 + 2, bx + 2 + (bw - 3) * rem / 100,
                                           cy + bh / 2 - 1, fill=color(rem), width=0)
                    if wk == "weekly":
                        for di in range(1, 7):
                            tx = bx + bw * di / 7
                            c.create_line(tx, cy - bh / 2 + 2, tx, cy + bh / 2 - 2, fill="#6b7280", width=1)
                        wa = tr.get("week_allowed")
                        if wa is not None:
                            px = bx + (bw * min(100.0, max(0.0, wa)) / 100.0)
                            c.create_line(px, cy - bh / 2 - 1, px, cy + bh / 2 + 1, fill="#ffffff", width=2)
                    rem_txt = "取得なし" if rem is None else f"残り{round(rem)}%"
                    self.text(bx + bw + 8, cy, rem_txt, fdb, DM if rem is None else color(rem))
                    rt = (f"リセット {fmt(d)}（{left(d)}）" if d else "リセット 取得なし")
                    width = max(width, self.text(c2 + 40, cy, rt, fd) + PAD + 10); yy += LH
                wkw = s.get("weekly") or {}; wu = wkw.get("used_percent")
                ol = outlook(None if wu is None else max(0.0, min(100.0, 100 - float(wu))), todt(wkw.get("resets_at")))
                if ol: width = max(width, self.text(x0 + 8, yy + LH // 2, ol, fd, FG) + PAD + 10); yy += LH
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
            # 説明リンク（クリックでブラウザ。自動では開かない）
            help_tag = "b_help"
            hx = x0
            c.create_text(hx, yy + LH // 2, text="説明（計算の見方）↗", anchor="w", font=fd, fill="#60a5fa", tags=help_tag)
            width = max(width, hx + fd.measure("説明（計算の見方）↗") + PAD + 10)
            c.tag_bind(help_tag, "<ButtonRelease-1>", lambda e: self.r.after(1, lambda: webbrowser.open(HELP_URL)))
            yy += LH
            height = yy + PAD + 2
            c.coords(self._panel, 0, PAD + H + 1, width - 1, height - 1)
        c.config(width=width, height=height)
        self.sync_bg()
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

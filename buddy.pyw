"""Claude Buddy: a little creature that peeks out from a screen edge when Claude Code needs you.

Click it to jump to that session, or Allow / Deny a permission request right in its bubble.
Hover it for today's stats. Right-click it (or the tray icon) for characters, sounds and quiet modes.

    pythonw buddy.pyw          start (only one copy runs)
    python buddy.pyw --quit    stop the running copy
"""
import glob
import json
import os
import random
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sysutil  # noqa: E402

try:
    from PIL import Image, ImageTk
    import render
except ImportError:
    from tkinter import messagebox
    tk.Tk().withdraw()
    messagebox.showerror("Claude Buddy", "Claude Buddy needs Pillow.\n\nRun:  python -m pip install pillow")
    sys.exit(1)

SESSIONS = os.path.join(HERE, "sessions")
PID_FILE = os.path.join(HERE, "widget.pid")
CONF = os.path.join(HERE, "buddy_conf.json")
STATS = os.path.join(HERE, "stats.json")
SOUNDS = os.path.join(HERE, "sounds")
ICON = os.path.join(HERE, "assets", "buddy.ico")

DEFAULTS = {"char": "golden", "side": "random", "sound": "voice", "approve": True, "approve_wait": 60,
            "say_hi": True, "quiet_hours": False, "dnd_until": 0}
VOICE = {"golden": "woof!", "corgi": "boof!", "husky": "awoo!", "cat": "meow!", "frog": "ribbit!"}
EDGES = ("bottom", "left", "right", "top")
STALE = 6 * 3600
SLEEPY_AFTER, REMIND_AFTER = 120, 300
STARTUP_GRACE = 600  # alerts older than this when the buddy starts are skipped
HI_EVERY = (25 * 60, 45 * 60)
QUIET = (22, 8)  # quiet hours: 10pm to 8am
TEST = "__test__"


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def fmt_minutes(secs):
    m = int(secs // 60)
    if m < 1:
        return "under a minute"
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"


# --- windows -----------------------------------------------------------------

if sysutil.WIN:
    import ctypes
    from ctypes import wintypes

    u32, g32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    H = ctypes.c_void_p
    for fn, res, args in [
        (u32.GetAncestor, H, [H, wintypes.UINT]), (u32.GetDC, H, [H]), (u32.ReleaseDC, ctypes.c_int, [H, H]),
        (u32.GetWindowLongW, ctypes.c_long, [H, ctypes.c_int]),
        (u32.SetWindowLongW, ctypes.c_long, [H, ctypes.c_int, ctypes.c_long]),
        (u32.ShowWindow, wintypes.BOOL, [H, ctypes.c_int]),
        (u32.SetWindowPos, wintypes.BOOL, [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]),
        (u32.UpdateLayeredWindow, wintypes.BOOL, [H, H, ctypes.c_void_p, ctypes.c_void_p, H, ctypes.c_void_p,
                                                  wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]),
        (u32.SetForegroundWindow, wintypes.BOOL, [H]),
        (g32.CreateCompatibleDC, H, [H]), (g32.DeleteDC, wintypes.BOOL, [H]),
        (g32.CreateDIBSection, H, [H, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), H, wintypes.DWORD]),
        (g32.SelectObject, H, [H, H]), (g32.DeleteObject, wintypes.BOOL, [H]),
    ]:
        fn.restype, fn.argtypes = res, args

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    class LayeredWindow:
        """Per-pixel-alpha window: smooth edges, and clicks fall through the transparent parts."""

        def __init__(self, root):
            self.root = root
            root.withdraw()  # the layered style only takes if it's set while the window is hidden
            root.update_idletasks()
            self.hwnd = u32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT
            ex = u32.GetWindowLongW(self.hwnd, -20)
            u32.SetWindowLongW(self.hwnd, -20, ex | 0x80000 | 0x80 | 0x08000000)  # LAYERED|TOOLWINDOW|NOACTIVATE
            self.size, self.dc, self.bmp, self.bits, self.old = None, None, None, ctypes.c_void_p(), None
            self.visible = True
            self.update(Image.new("RGBA", (1, 1)), -10, -10)
            root.deiconify()
            self.hide()

        def _surface(self, w, h):
            if self.size == (w, h):
                return
            self._free()
            screen = u32.GetDC(None)
            self.dc = g32.CreateCompatibleDC(screen)
            u32.ReleaseDC(None, screen)
            bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            buf = (ctypes.c_byte * (ctypes.sizeof(bmi) + 4))()
            ctypes.memmove(buf, ctypes.byref(bmi), ctypes.sizeof(bmi))
            self.bmp = g32.CreateDIBSection(self.dc, buf, 0, ctypes.byref(self.bits), None, 0)
            self.old = g32.SelectObject(self.dc, self.bmp)
            self.size = (w, h)

        def _free(self):
            if self.dc:
                g32.SelectObject(self.dc, self.old)
                g32.DeleteObject(self.bmp)
                g32.DeleteDC(self.dc)
                self.dc = None

        def update(self, img, x, y):
            w, h = img.size
            if self.size != (w, h):
                self.root.geometry(f"{w}x{h}+{x}+{y}")  # keep Tk's idea of the size in sync for mouse events
            self._surface(w, h)
            r, g, b, a = img.convert("RGBa").split()  # premultiplied, as Windows wants
            data = Image.merge("RGBA", (b, g, r, a)).tobytes()
            ctypes.memmove(self.bits, data, len(data))
            pt, sz, src = wintypes.POINT(x, y), wintypes.SIZE(w, h), wintypes.POINT(0, 0)
            blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, use per-pixel alpha
            screen = u32.GetDC(None)
            u32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(pt), ctypes.byref(sz), self.dc,
                                    ctypes.byref(src), 0, ctypes.byref(blend), 2)  # ULW_ALPHA
            u32.ReleaseDC(None, screen)
            if not self.visible:
                u32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
                u32.SetWindowPos(self.hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)  # topmost
                self.visible = True

        def hide(self):
            if self.visible:
                u32.ShowWindow(self.hwnd, 0)
                self.visible = False

        def grab_focus(self):
            u32.SetForegroundWindow(self.hwnd)

    class Tray:
        """System tray icon, so the buddy can be reached while it's hidden."""
        WM_TRAY = 0x8001

        def __init__(self, icon_path, tip):
            from ctypes import WINFUNCTYPE
            self.events = []
            k32, s32 = ctypes.WinDLL("kernel32"), ctypes.WinDLL("shell32")
            self.s32 = s32
            LRESULT = ctypes.c_ssize_t
            WNDPROC = WINFUNCTYPE(LRESULT, H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
            u32.DefWindowProcW.restype, u32.DefWindowProcW.argtypes = LRESULT, [H, wintypes.UINT, wintypes.WPARAM,
                                                                                wintypes.LPARAM]

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                            ("cbWndExtra", ctypes.c_int), ("hInstance", H), ("hIcon", H), ("hCursor", H),
                            ("hbrBackground", H), ("lpszMenuName", wintypes.LPCWSTR),
                            ("lpszClassName", wintypes.LPCWSTR)]

            class NOTIFYICONDATAW(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", H), ("uID", wintypes.UINT),
                            ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", H),
                            ("szTip", ctypes.c_wchar * 128), ("dwState", wintypes.DWORD),
                            ("dwStateMask", wintypes.DWORD), ("szInfo", ctypes.c_wchar * 256),
                            ("uVersion", wintypes.UINT), ("szInfoTitle", ctypes.c_wchar * 64),
                            ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", H)]

            def proc(hwnd, msg, wp, lp):
                if msg == self.WM_TRAY:
                    ev = lp & 0xFFFF
                    if ev == 0x202:      # WM_LBUTTONUP
                        self.events.append("left")
                    elif ev == 0x205:    # WM_RBUTTONUP
                        self.events.append("right")
                    return 0
                return u32.DefWindowProcW(hwnd, msg, wp, lp)

            self._proc = WNDPROC(proc)  # keep a reference, or Windows calls into freed memory
            k32.GetModuleHandleW.restype = H
            inst = k32.GetModuleHandleW(None)
            wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=inst, lpszClassName="ClaudeBuddyTray")
            u32.RegisterClassW(ctypes.byref(wc))
            u32.CreateWindowExW.restype = H
            u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H, H, H, H]
            self.hwnd = u32.CreateWindowExW(0, "ClaudeBuddyTray", "Claude Buddy", 0, 0, 0, 0, 0, None, None, inst, None)
            u32.LoadImageW.restype = H
            u32.LoadImageW.argtypes = [H, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            hicon = u32.LoadImageW(None, icon_path, 1, 0, 0, 0x10 | 0x40)  # IMAGE_ICON, LOADFROMFILE|DEFAULTSIZE
            self.nid = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd, uID=1,
                                       uFlags=0x1 | 0x2 | 0x4, uCallbackMessage=self.WM_TRAY, hIcon=hicon)
            self.nid.szTip = tip[:127]
            s32.Shell_NotifyIconW(0, ctypes.byref(self.nid))  # NIM_ADD

        def set_tip(self, tip):
            if self.nid.szTip != tip[:127]:
                self.nid.szTip = tip[:127]
                self.s32.Shell_NotifyIconW(1, ctypes.byref(self.nid))  # NIM_MODIFY

        def remove(self):
            self.s32.Shell_NotifyIconW(2, ctypes.byref(self.nid))  # NIM_DELETE


class TkWindow:
    """macOS / Linux fallback: a borderless Tk window showing the frame as an image."""

    def __init__(self, root):
        self.root = root
        self.bg = "systemTransparent" if sysutil.MAC else "#202124"
        if sysutil.MAC:
            root.attributes("-transparent", True)
        root.config(bg=self.bg)
        self.label = tk.Label(root, bg=self.bg, bd=0, highlightthickness=0)
        self.label.pack()
        self.photo = None
        self.visible = False
        root.withdraw()

    def update(self, img, x, y):
        if not sysutil.MAC:  # no per-pixel alpha on X11: flatten onto the background
            flat = Image.new("RGBA", img.size, self.bg)
            flat.alpha_composite(img)
            img = flat
        self.photo = ImageTk.PhotoImage(img)
        self.label.config(image=self.photo)
        self.root.geometry(f"{img.width}x{img.height}+{x}+{y}")
        if not self.visible:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.visible = True

    def hide(self):
        if self.visible:
            self.root.withdraw()
            self.visible = False

    def grab_focus(self):
        self.root.focus_force()


# --- the buddy ---------------------------------------------------------------

class Buddy:
    def __init__(self):
        self.conf = dict(DEFAULTS, **load(CONF, {}))
        self.stats = load(STATS, {})
        self.k = sysutil.dpi_scale()

        self.root = tk.Tk()
        self.root.title("Claude Buddy")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.win = LayeredWindow(self.root) if sysutil.WIN else TkWindow(self.root)

        self.sessions = {}       # sid -> record from the hook
        self.fake = {}           # test alerts
        self.prev_state = {}     # sid -> state at the last poll
        self.shown = {}          # sid -> alert_at we already popped up for
        self.handled = {}        # sid -> alert_at the user dealt with
        self.reminded = set()
        self.pending = []
        self.current = None
        self.mode = "hidden"     # hidden | alert | hi
        self.progress, self.target = 0.0, 0.0
        self.edge, self.anchor, self.area = "bottom", 0.5, (0, 0, 1, 1)
        self.t0 = self.alert_started = self.started = time.time()
        self.hover = None
        self.boxes, self.crop = {}, (0, 0)
        self.hi_until = 0
        self.next_hi = time.time() + random.uniform(*HI_EVERY)

        self.vars = {name: (tk.BooleanVar if isinstance(DEFAULTS[name], bool) else tk.StringVar)(
            value=self.conf[name]) for name in ("char", "side", "sound", "approve", "say_hi", "quiet_hours")}

        r = self.root
        r.bind("<Motion>", self.on_motion)
        r.bind("<Leave>", lambda e: self.set_hover(None))
        r.bind("<ButtonRelease-1>", self.on_click)
        r.bind("<Button-3>", lambda e: self.popup_menu(e.x_root, e.y_root))
        if sysutil.MAC:
            r.bind("<Button-2>", lambda e: self.popup_menu(e.x_root, e.y_root))

        self.tray = None
        if sysutil.WIN:
            try:
                self.ensure_icon()
                self.tray = Tray(ICON, "Claude Buddy")
            except Exception:
                self.tray = None

        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        self.poll()
        self.tick()

    # --- settings ---
    def set_conf(self, name, value):
        self.conf[name] = value
        save(CONF, self.conf)

    def from_var(self, name):
        self.set_conf(name, self.vars[name].get())
        if name == "char":
            self.test()

    def ensure_icon(self):
        if not os.path.exists(ICON):
            os.makedirs(os.path.dirname(ICON), exist_ok=True)
            render.icon("golden", 256).save(ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])

    def quiet(self):
        """Do-not-disturb, quiet hours, or a full-screen app / presentation."""
        if time.time() < self.conf.get("dnd_until", 0):
            return True
        if self.conf.get("quiet_hours"):
            h = time.localtime().tm_hour
            if h >= QUIET[0] or h < QUIET[1]:
                return True
        return sysutil.presenting()

    def set_dnd(self, secs):
        if secs == "8am":
            now = time.localtime()
            until = time.mktime((now.tm_year, now.tm_mon, now.tm_mday + (1 if now.tm_hour >= 8 else 0),
                                 8, 0, 0, 0, 0, -1))
        else:
            until = time.time() + secs if secs else 0
        self.set_conf("dnd_until", until)

    # --- stats ---
    def today(self):
        d = time.strftime("%Y-%m-%d")
        if self.stats.get("date") != d:
            self.stats = {"date": d, "waited": 0, "pings": 0}
        return self.stats

    def stats_text(self):
        s = self.today()
        pings = s["pings"]
        if not pings:
            return "no pings yet today"
        return f"claude waited {fmt_minutes(s['waited'])} on you · {pings} ping{'s' * (pings != 1)}"

    # --- sessions ---
    def poll(self):
        now = time.time()
        sessions = {}
        for p in glob.glob(os.path.join(SESSIONS, "*.json")):
            if p.endswith(".answer.json"):
                continue
            rec = load(p, None)
            if rec and now - rec.get("updated", 0) < STALE:
                sessions[os.path.basename(p)[:-5]] = rec
        sessions.update(self.fake)
        self.sessions = sessions

        # alerts that got answered in the terminal count as handled, and add to "time waited"
        for sid, rec in sessions.items():
            before = self.prev_state.get(sid)
            if before in ("ask", "done") and rec["state"] == "working":
                at = rec.get("alert_at", 0)
                if self.shown.get(sid) == at and self.handled.get(sid) != at:
                    self.today()["waited"] += min(rec["updated"] - at, 3600)
                    save(STATS, self.stats)
                self.handled[sid] = at
            self.prev_state[sid] = rec["state"]

        quiet = self.quiet()
        fresh = None
        for sid, rec in sessions.items():
            at = rec.get("alert_at", 0)
            if rec["state"] == "working" or at <= max(self.shown.get(sid, 0), self.handled.get(sid, 0)):
                continue
            if at < self.started - STARTUP_GRACE:
                self.handled[sid] = at  # old news from before the buddy started
                continue
            if sid != TEST and sysutil.is_focused(rec.get("pids") or [], rec.get("project", ""), rec.get("hwnd")):
                self.handled[sid] = at  # they're already looking at it
                continue
            if quiet:
                continue  # stays fresh; pops when the quiet time is over
            self.shown[sid] = at
            self.today()["pings"] += 1
            save(STATS, self.stats)
            if fresh is None or rec["state"] == "ask":
                fresh = sid

        self.update_pending()

        if fresh:
            self.current = fresh
            self.pop("alert")
            self.ping(sessions[fresh]["state"])
        elif self.pending:
            if self.current not in self.pending:
                self.current = self.pending[0]
                self.t0 = time.time() - 4  # little hop for the next one
            if self.mode != "alert" and not quiet:
                self.pop("alert")
        elif self.mode == "alert":
            self.hide()

        # ignored for a while: doze off, then one more nudge
        if self.mode == "alert" and self.current:
            key = (self.current, sessions[self.current].get("alert_at"))
            if now - self.alert_started > REMIND_AFTER and key not in self.reminded:
                self.reminded.add(key)
                self.t0 = self.alert_started = now
                self.ping(sessions[self.current]["state"])

        if (self.mode == "hidden" and self.conf.get("say_hi") and now > self.next_hi and not quiet
                and any(now - r.get("updated", 0) < 1800 for r in sessions.values())):
            self.next_hi = now + random.uniform(*HI_EVERY)
            self.say_hi()

        if self.tray:
            n = len(self.pending)
            head = f"{n} session{'s' * (n != 1)} waiting" if n else "Claude Buddy"
            self.tray.set_tip(f"{head}\n{self.stats_text()}")
            self.handle_tray()
        self.root.after(400, self.poll)

    def update_pending(self):
        """Alerts we popped up for that nobody has dealt with yet, questions first."""
        sessions = self.sessions
        self.pending = sorted(
            (sid for sid, rec in sessions.items()
             if rec["state"] != "working" and self.shown.get(sid) == rec.get("alert_at")
             and self.handled.get(sid) != rec.get("alert_at") and (sid != TEST or sid in self.fake)),
            key=lambda s: (sessions[s]["state"] != "ask", sessions[s].get("alert_at", 0)))

    def handle_tray(self):
        while self.tray.events:
            ev = self.tray.events.pop(0)
            if ev == "left":
                if self.pending:
                    self.pop("alert")
                else:
                    self.say_hi()
            else:
                x, y = self.root.winfo_pointerxy()
                self.popup_menu(x, y)

    def ping(self, state):
        sound = self.conf.get("sound", "voice")
        if sound == "off":
            return
        if sound == "beep":
            sysutil.play(None, state)
            return
        names = render.CHARACTERS.get(self.conf["char"], render.CHARACTERS["golden"])["sounds"]
        sysutil.play(os.path.join(SOUNDS, (names[0] if state == "ask" else names[1]) + ".wav"), state)

    # --- show / hide ---
    def pop(self, mode):
        was_hidden = self.progress == 0 and self.target == 0
        self.mode = mode
        if was_hidden:
            side = self.conf.get("side", "random")
            self.edge = random.choice(EDGES) if side == "random" else side
            self.anchor = random.uniform(0.15, 0.85)
            self.area = sysutil.work_area_at_cursor() or (0, 0, self.root.winfo_screenwidth(),
                                                          self.root.winfo_screenheight())
        self.target = 1.0
        self.t0 = self.alert_started = time.time()

    def hide(self):
        self.target = 0.0
        self.mode = "hidden"
        self.hover = None

    def say_hi(self):
        self.pop("hi")
        self.hi_until = time.time() + 4

    def test(self):
        now = time.time()
        self.fake[TEST] = {"state": "ask", "project": "test", "message": "Bash: npm test", "alert_at": now,
                           "updated": now, "waiting": True, "suggest": True, "kind": "permission"}

    def resolve(self, sid):
        rec = self.sessions.get(sid, {})
        self.handled[sid] = rec.get("alert_at")
        self.fake.pop(sid, None)
        self.update_pending()
        if self.pending:
            self.current = self.pending[0]
            self.t0 = time.time() - 4
        else:
            self.hide()

    # --- drawing ---
    def tick(self):
        if self.progress < self.target:
            self.progress = min(self.target, self.progress + 0.08)
        elif self.progress > self.target:
            self.progress = max(self.target, self.progress - 0.08)
        if self.mode == "hi" and time.time() > self.hi_until and self.hover is None:
            self.hide()
        if self.progress > 0:
            try:
                self.draw()
            except KeyError:
                self.hide()
        else:
            self.win.hide()
        self.root.after(33 if self.progress > 0 or self.target > 0 else 250, self.tick)

    def content(self):
        """(record, title, subtitle, buttons, pager) for whatever we're showing."""
        if self.mode == "hi" or self.current not in self.sessions:
            rec, title, sub = {"state": "hi"}, "hi! just checking in", "you're doing great"
            buttons, pager = (), None
        else:
            rec = self.sessions[self.current]
            proj = rec.get("project", "claude")
            n = len(self.pending)
            pager = f"{self.pending.index(self.current) + 1}/{n}" if n > 1 and self.current in self.pending else None
            buttons = ()
            if rec["state"] == "ask":
                title = f"{VOICE.get(self.conf['char'], 'woof!')} i need you"
                sub = f"{proj} · {rec.get('message', '')}"
                if rec.get("waiting") and self.conf.get("approve"):
                    buttons = ("allow", "always", "deny") if rec.get("suggest") else ("allow", "deny")
            else:
                if rec.get("party"):
                    title = "tests passed!"
                elif rec.get("celebrate"):
                    title = f"done after {fmt_minutes(rec.get('took', 0))}!"
                else:
                    title = "all done!"
                sub = f"{proj} · your turn"
        if self.hover == "creature":
            title, sub, buttons, pager = "today so far", self.stats_text(), (), None
        if len(sub) > 48:
            sub = sub[:47] + "…"
        return rec, title, sub, buttons, pager

    def draw(self):
        rec, title, sub, buttons, pager = self.content()
        now = time.time()
        t = now - self.t0
        state = rec["state"]
        sleepy = self.mode == "alert" and now - self.alert_started > SLEEPY_AFTER
        crit = render.creature(self.conf["char"], state, t, self.k, sleepy=sleepy,
                               party=bool(rec.get("party")) and state == "done",
                               celebrate=bool(rec.get("celebrate")) and state == "done")
        accent = render.ACCENT["stats" if self.hover == "creature" else state]
        hover = self.hover if self.hover in buttons else None
        bub, hits = render.bubble(title, sub, accent, render.TAIL[self.edge], self.k, buttons, pager, hover)
        img, boxes = render.compose(self.edge, crit, bub, hits, self.k)

        # place against the edge, sliding in; crop whatever would spill past the work area
        l, t_, r, b = self.area
        w, h = img.size
        e = 1 - (1 - self.progress) ** 3
        if self.edge in ("bottom", "top"):
            x = int(min(max(l + (r - l) * self.anchor - w / 2, l), r - w))
            y = int(b - h * e) if self.edge == "bottom" else int(t_ - h + h * e)
        else:
            y = int(min(max(t_ + (b - t_) * self.anchor - h / 2, t_), b - h))
            x = int(r - w * e) if self.edge == "right" else int(l - w + w * e)
        x1, y1, x2, y2 = max(x, l), max(y, t_), min(x + w, r), min(y + h, b)
        if x2 - x1 < 2 or y2 - y1 < 2:
            self.win.hide()
            return
        self.crop = (x1 - x, y1 - y)
        self.boxes = boxes
        self.win.update(img.crop((x1 - x, y1 - y, x2 - x, y2 - y)), x1, y1)

    # --- mouse ---
    def hit(self, e):
        x, y = e.x + self.crop[0], e.y + self.crop[1]
        for name in ("allow", "always", "deny", "prev", "next", "bubble", "creature"):
            box = self.boxes.get(name)
            if box and box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                return name
        return None

    def set_hover(self, name):
        self.hover = name

    def on_motion(self, e):
        self.set_hover(self.hit(e))

    def on_click(self, e):
        name = self.hit(e)
        if self.mode != "alert" or self.current not in self.sessions:
            self.hide()
            return
        sid, rec = self.current, self.sessions[self.current]
        if name in ("allow", "always", "deny"):
            if sid != TEST:
                save(os.path.join(SESSIONS, f"{sid}.answer.json"), {"req": rec.get("req"), "behavior": name})
            self.resolve(sid)
        elif name in ("prev", "next") and len(self.pending) > 1:
            i = self.pending.index(sid) if sid in self.pending else 0
            self.current = self.pending[(i + (1 if name == "next" else -1)) % len(self.pending)]
            self.t0 = time.time() - 4
        elif name in ("bubble", "creature"):
            if sid != TEST:
                sysutil.focus(rec.get("pids") or [], rec.get("project", ""), rec.get("hwnd"))
            self.resolve(sid)

    # --- menu ---
    def popup_menu(self, x, y):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label=self.stats_text(), state="disabled")
        if self.pending:
            m.add_command(label=f"Show waiting sessions ({len(self.pending)})", command=lambda: self.pop("alert"))
        m.add_separator()

        def radio_menu(name, options):
            sub = tk.Menu(m, tearoff=0)
            for value, label in options:
                sub.add_radiobutton(label=label, value=value, variable=self.vars[name],
                                    command=lambda n=name: self.from_var(n))
            return sub

        m.add_cascade(label="Character", menu=radio_menu("char", [(k, c["name"]) for k, c in
                                                                  render.CHARACTERS.items()]))
        m.add_cascade(label="Peek from", menu=radio_menu("side", [(s, s.title()) for s in ("random",) + EDGES]))
        m.add_cascade(label="Sound", menu=radio_menu("sound", [("voice", "Character voice"), ("beep", "System beep"),
                                                               ("off", "Off")]))
        m.add_checkbutton(label="Allow / Deny from the bubble", variable=self.vars["approve"],
                          command=lambda: self.from_var("approve"))
        m.add_checkbutton(label="Say hi now and then", variable=self.vars["say_hi"],
                          command=lambda: self.from_var("say_hi"))
        m.add_separator()
        dnd = tk.Menu(m, tearoff=0)
        dnd.add_command(label="For 1 hour", command=lambda: self.set_dnd(3600))
        dnd.add_command(label="Until 8am", command=lambda: self.set_dnd("8am"))
        dnd.add_command(label="Off", command=lambda: self.set_dnd(0))
        on = time.time() < self.conf.get("dnd_until", 0)
        m.add_cascade(label="Do not disturb" + (" (on)" if on else ""), menu=dnd)
        m.add_checkbutton(label="Quiet hours (10pm–8am)", variable=self.vars["quiet_hours"],
                          command=lambda: self.from_var("quiet_hours"))
        m.add_separator()
        m.add_command(label="Test wave", command=self.test)
        m.add_command(label="Quit", command=self.quit)
        self.win.grab_focus()
        m.tk_popup(x, y)

    def quit(self):
        if self.tray:
            self.tray.remove()
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        self.root.destroy()


def running_pid():
    try:
        pid = int(open(PID_FILE).read().strip())
    except (OSError, ValueError):
        return None
    return pid if pid != os.getpid() and sysutil.pid_alive(pid) else None


def main():
    os.makedirs(SESSIONS, exist_ok=True)
    pid = running_pid()
    if "--quit" in sys.argv:
        if pid:
            sysutil.kill(pid)
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        return
    if pid:
        return  # already running
    if sysutil.WIN:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    Buddy().root.mainloop()


if __name__ == "__main__":
    main()

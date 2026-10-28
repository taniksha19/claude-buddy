"""Claude Buddy: a puppy that peeks out from a screen edge and waves when Claude needs you.

Left-click: dismiss (it ducks back).  Right-click: menu (side / mute / test / quit).
"""
import ctypes
import glob
import json
import math
import os
import random
import time
import tkinter as tk
import winsound
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = os.path.join(HERE, "sessions")
PID_FILE = os.path.join(HERE, "widget.pid")
CONF = os.path.join(HERE, "buddy_conf.json")
STALE = 6 * 3600
KEY = "#ff00fe"  # rendered transparent
EDGES = ("bottom", "left", "right", "top")

# The puppy is drawn upright in its own coordinates, standing on y=178.
# Only the window (DX0, DY0)-(DX0+DOGW, DY0+DOGH) of it peeks past the screen edge.
DX0, DY0, DOGW, DOGH = 45, 40, 160, 102
BUBW, BUBH = 240, 58

COLORS = {"done": "#ff8fab", "ask": "#ff4d6d"}  # collar / bubble / hearts
DOG, DOG_EDGE = "#f6d9a8", "#b8865a"
EAR, EAR_EDGE = "#a86b3c", "#7d4c28"
PATCH, MUZZLE = "#e9b97f", "#fff3e0"
INK, BLUSH, TONGUE = "#4a3040", "#ffa3b5", "#ff7f9a"
FONT = ("Segoe UI Semibold", -12)


def work_area():
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom


class Pen:
    """Draws on the canvas through a rotation, so the puppy can peek from any edge."""

    def __init__(self, c, fn, dang):
        self.c, self.fn, self.dang = c, fn, dang

    def _pts(self, coords):
        out = []
        for i in range(0, len(coords), 2):
            out.extend(self.fn(coords[i], coords[i + 1]))
        return out

    def _box(self, x1, y1, x2, y2):
        a, b = self.fn(x1, y1)
        c, d = self.fn(x2, y2)
        return min(a, c), min(b, d), max(a, c), max(b, d)

    def oval(self, *box, **kw):
        self.c.create_oval(*self._box(*box), **kw)

    def arc(self, *box, start=0, **kw):
        self.c.create_arc(*self._box(*box), start=start + self.dang, **kw)

    def line(self, *coords, **kw):
        self.c.create_line(*self._pts(coords), **kw)

    def polygon(self, *coords, **kw):
        self.c.create_polygon(*self._pts(coords), **kw)

    def text(self, x, y, **kw):
        self.c.create_text(*self.fn(x, y), **kw)


class Buddy:
    def __init__(self):
        self.conf = self.load_conf()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", KEY)
        self.root.configure(bg=KEY)
        self.c = tk.Canvas(self.root, bg=KEY, highlightthickness=0)
        self.c.pack()

        self.alert = None      # record currently being shown
        self.seen = {}         # session -> alert_at already handled
        self.progress = 0.0    # 0 = hidden, 1 = fully peeking out
        self.target = 0.0
        self.offset = 0.5
        self.t0 = time.time()
        self.layout("bottom")

        self.side_var = tk.StringVar(value=self.conf.get("side", "random"))
        self.menu = tk.Menu(self.root, tearoff=0)
        sides = tk.Menu(self.menu, tearoff=0)
        for s in ("random",) + EDGES:
            sides.add_radiobutton(label=s.title(), value=s, variable=self.side_var, command=self.set_side)
        self.menu.add_cascade(label="Peek from", menu=sides)
        self.menu.add_command(label="Mute", command=self.toggle_mute)
        self.menu.add_command(label="Test wave", command=self.test)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.quit)
        self.refresh_menu()

        self.c.bind("<Button-1>", lambda e: self.hide())
        self.c.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        self.place()
        self.root.withdraw()
        self.poll()
        self.animate()

    # --- config ---
    def load_conf(self):
        try:
            return json.load(open(CONF))
        except (OSError, ValueError):
            return {}

    def save_conf(self):
        with open(CONF, "w") as f:
            json.dump(self.conf, f)

    def set_side(self):
        self.conf["side"] = self.side_var.get()
        self.save_conf()

    def toggle_mute(self):
        self.conf["muted"] = not self.conf.get("muted")
        self.save_conf()
        self.refresh_menu()

    def refresh_menu(self):
        self.menu.entryconfig(1, label="Unmute" if self.conf.get("muted") else "Mute")

    def test(self):
        self.show({"state": "ask", "project": "test", "message": "hi! i need you :)"})

    def quit(self):
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        self.root.destroy()

    # --- layout per edge: canvas size, puppy rotation, bubble spot ---
    def layout(self, edge):
        self.edge = edge
        if edge == "bottom":
            self.cw, self.ch = BUBW, BUBH + DOGH
            ox = (BUBW - DOGW) / 2
            fn, dang = (lambda x, y: (ox + x - DX0, BUBH + y - DY0)), 0
            self.bubble = (BUBW / 2, BUBH / 2 - 2, "down")
        elif edge == "top":  # hanging upside-down
            self.cw, self.ch = BUBW, BUBH + DOGH
            ox = (BUBW - DOGW) / 2
            fn, dang = (lambda x, y: (ox + DOGW - (x - DX0), DOGH - (y - DY0))), 180
            self.bubble = (BUBW / 2, DOGH + BUBH / 2 + 2, "up")
        elif edge == "right":
            self.cw, self.ch = BUBW + DOGH, DOGW
            fn, dang = (lambda x, y: (BUBW + y - DY0, DOGW - (x - DX0))), 90
            self.bubble = (BUBW / 2, DOGW / 2, "right")
        else:  # left
            self.cw, self.ch = DOGH + BUBW, DOGW
            fn, dang = (lambda x, y: (DOGH - (y - DY0), x - DX0)), -90
            self.bubble = (DOGH + BUBW / 2, DOGW / 2, "left")
        self.pen = Pen(self.c, fn, dang)
        self.c.config(width=self.cw, height=self.ch)

    # --- show / hide ---
    def show(self, rec):
        was_hidden = self.progress == 0 and self.target == 0
        self.alert = rec
        if was_hidden:
            side = self.conf.get("side", "random")
            self.layout(random.choice(EDGES) if side == "random" else side)
            self.offset = random.uniform(0.15, 0.85)
        self.target = 1.0
        self.t0 = time.time()
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        if not self.conf.get("muted"):
            winsound.MessageBeep(winsound.MB_ICONHAND if rec["state"] == "ask" else winsound.MB_ICONASTERISK)

    def hide(self):
        self.target = 0.0

    def place(self):
        l, t, r, b = work_area()
        p = 1 - (1 - self.progress) ** 3  # ease-out
        w, h = self.cw, self.ch
        if self.edge in ("bottom", "top"):
            x = int(l + (r - l - w) * self.offset)
            y = int(b - h * p) if self.edge == "bottom" else int(t - h + h * p)
        else:
            y = int(t + (b - t - h) * self.offset)
            x = int(l - w + w * p) if self.edge == "left" else int(r - w * p)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # --- session polling ---
    def poll(self):
        now = time.time()
        sessions = {}
        for p in glob.glob(os.path.join(SESSIONS, "*.json")):
            try:
                rec = json.load(open(p))
            except (OSError, ValueError):
                continue
            if now - rec.get("updated", 0) < STALE:
                sessions[os.path.basename(p)] = rec

        fresh = None
        for sid, rec in sessions.items():
            if rec["state"] != "working" and rec.get("alert_at", 0) > self.seen.get(sid, 0):
                self.seen[sid] = rec["alert_at"]
                if fresh is None or rec["state"] == "ask":
                    fresh = rec
        if fresh:
            self.show(fresh)
        elif self.target and self.alert and self.alert.get("project") != "test":
            # Duck back by itself once nothing is waiting on you anymore.
            if not any(r["state"] != "working" for r in sessions.values()):
                self.hide()

        self.root.after(400, self.poll)

    # --- drawing ---
    def animate(self):
        step = 0.07
        if self.progress < self.target:
            self.progress = min(self.target, self.progress + step)
        elif self.progress > self.target:
            self.progress = max(self.target, self.progress - step)
            if self.progress == 0:
                self.root.withdraw()
        if self.progress > 0:
            self.place()
            self.c.delete("all")
            rec = self.alert or {"state": "done", "project": "", "message": ""}
            self.draw_puppy(rec)
            self.draw_bubble(rec)
        self.root.after(16, self.animate)

    def draw_puppy(self, rec):
        pen = self.pen
        asking = rec["state"] == "ask"
        accent = COLORS.get(rec["state"], COLORS["done"])
        t = time.time() - self.t0
        excited = t < 6  # wave hard at first, then calm down
        speed = 10 if excited else 4

        bounce = abs(math.sin(t * 5)) if excited else abs(math.sin(t * 1.5)) * 0.25
        stretch = 0.06 * (bounce - 0.5)
        cx = 125
        base = 178 - bounce * 10
        hy = base - 52
        hrx, hry = 38 * (1 - stretch), 31 * (1 + stretch)

        # floating hearts / sparkles
        for i in range(3):
            ph = (t * 0.5 + i / 3) % 1
            px = cx + (i - 1) * 32 + math.sin(ph * 6 + i) * 8
            py = hy - hry - 4 - ph * 40
            size = -int(12 + 6 * math.sin(ph * math.pi))
            glyph = "✦" if asking and i != 1 else "♥"
            pen.text(px, py, text=glyph, fill=accent, font=("Segoe UI Symbol", size))

        # body + collar (mostly past the screen edge; peeks out on the hop)
        pen.oval(cx - 28, base - 38, cx + 28, base, fill=DOG, outline=DOG_EDGE, width=3)
        pen.line(cx - 22, base - 33, cx, base - 29, cx + 22, base - 33, smooth=True, fill=accent, width=6, capstyle="round")
        pen.oval(cx - 5, base - 30, cx + 5, base - 20, fill="#ffd166", outline="#d9a520", width=2)

        # head, eye patch, floppy ears that flap with the hop
        pen.oval(cx - hrx, hy - hry, cx + hrx, hy + hry, fill=DOG, outline=DOG_EDGE, width=3)
        pen.oval(cx + 4, hy - 20, cx + 28, hy + 4, fill=PATCH, outline="")
        flap = (1 - bounce) * 6
        for s in (-1, 1):
            pen.polygon(cx + s * 22, hy - hry + 2,
                        cx + s * (hrx + 10), hy - hry + 10,
                        cx + s * (hrx + 10), hy + 12 + flap,
                        cx + s * (hrx - 2), hy + 16 + flap,
                        cx + s * (hrx - 8), hy - 6,
                        smooth=True, fill=EAR, outline=EAR_EDGE, width=2)
        # muzzle + nose
        pen.oval(cx - 15, hy + 1, cx + 15, hy + 23, fill=MUZZLE, outline="")
        pen.oval(cx - 6, hy + 1, cx + 6, hy + 9, fill=INK, outline="")
        pen.oval(cx - 3, hy + 2, cx, hy + 4, fill="white", outline="")

        # eyes: big and shiny, happy ^ ^ squint now and then
        eye_y = hy - 7
        happy = not asking and (t % 4) > 3.2
        blink = (t % 3.3) < 0.1
        for s in (-1, 1):
            ex_ = cx + s * 15
            if happy:
                pen.arc(ex_ - 7, eye_y - 4, ex_ + 7, eye_y + 8, start=20, extent=140, style="arc", outline=INK, width=3)
            elif blink:
                pen.arc(ex_ - 7, eye_y - 8, ex_ + 7, eye_y + 4, start=200, extent=140, style="arc", outline=INK, width=3)
            else:
                h = 10 if asking else 9
                pen.oval(ex_ - 7, eye_y - h, ex_ + 7, eye_y + h, fill=INK, outline="")
                pen.oval(ex_ - 3, eye_y - h + 3, ex_ + 2, eye_y - h + 8, fill="white", outline="")
                pen.oval(ex_ + 3, eye_y + 2, ex_ + 5, eye_y + 4, fill="white", outline="")
        for s in (-1, 1):
            pen.oval(cx + s * 26 - 6, hy + 7, cx + s * 26 + 6, hy + 12, fill=BLUSH, outline="")

        # waving arm + paw with toe beans
        sx, sy = cx + 46, base - 22
        ang = math.radians(-85 + 30 * math.sin(t * speed))
        ex, ey = sx + 40 * math.cos(ang), sy + 40 * math.sin(ang)
        pen.line(sx, sy, ex, ey, fill=DOG_EDGE, width=14, capstyle="round")
        pen.line(sx, sy, ex, ey, fill=DOG, width=9, capstyle="round")
        pen.oval(ex - 10, ey - 10, ex + 10, ey + 10, fill=DOG, outline=DOG_EDGE, width=3)
        pen.oval(ex - 4, ey - 1, ex + 4, ey + 5, fill=BLUSH, outline="")
        for bx, by in ((-6, -5), (0, -7), (6, -5)):
            pen.oval(ex + bx - 2, ey + by - 2, ex + bx + 2, ey + by + 2, fill=BLUSH, outline="")

        # mouth: happy "w" with tongue out, or a worried little "o" + sweat drop
        my = hy + 12
        if asking:
            o = 2.5 + 1.5 * abs(math.sin(t * 6))
            pen.oval(cx - o, my + 2 - o, cx + o, my + 2 + o, fill=INK, outline="")
            dx, dy = cx - 36, hy - 24 + 3 * math.sin(t * 3)
            pen.polygon(dx, dy - 8, dx - 5, dy + 2, dx, dy + 6, dx + 5, dy + 2, smooth=True, fill="#9ad4f5", outline="#5aa9d6")
        else:
            bob = 2 * math.sin(t * 8)
            pen.oval(cx - 5, my + 1, cx + 5, my + 12 + bob, fill=TONGUE, outline="#e05a7a", width=1)
            pen.arc(cx - 8, my - 5, cx, my + 3, start=180, extent=180, style="arc", outline=INK, width=2)
            pen.arc(cx, my - 5, cx + 8, my + 3, start=180, extent=180, style="arc", outline=INK, width=2)

    def draw_bubble(self, rec):
        c = self.c
        asking = rec["state"] == "ask"
        accent = COLORS.get(rec["state"], COLORS["done"])
        title = "woof! i need you ♡" if asking else "all done! ♡"
        msg = rec.get("message", "")
        msg = msg if len(msg) <= 32 else msg[:31] + "…"
        bx, by, tail = self.bubble
        txt = c.create_text(bx, by, text=f"{title}\n{rec.get('project', '')} · {msg}",
                            fill=INK, font=FONT, justify="center")
        x1, y1, x2, y2 = c.bbox(txt)
        x1, y1, x2, y2 = x1 - 11, y1 - 6, x2 + 11, y2 + 6
        r = 13
        c.create_polygon(x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
                         x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
                         smooth=True, fill="#fffaf3", outline=accent, width=2)
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        if tail == "down":
            tri, seam = (mx - 7, y2, mx + 7, y2, mx, y2 + 9), (mx - 6, y2, mx + 6, y2)
        elif tail == "up":
            tri, seam = (mx - 7, y1, mx + 7, y1, mx, y1 - 9), (mx - 6, y1, mx + 6, y1)
        elif tail == "right":
            tri, seam = (x2, my - 7, x2, my + 7, x2 + 9, my), (x2, my - 6, x2, my + 6)
        else:
            tri, seam = (x1, my - 7, x1, my + 7, x1 - 9, my), (x1, my - 6, x1, my + 6)
        c.create_polygon(*tri, fill="#fffaf3", outline=accent, width=2)
        c.create_line(*seam, fill="#fffaf3", width=3)
        c.tag_raise(txt)


if __name__ == "__main__":
    os.makedirs(SESSIONS, exist_ok=True)
    Buddy().root.mainloop()

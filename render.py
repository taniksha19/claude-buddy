"""Pillow renderer: the characters, their moods, the speech bubble, and the per-edge layouts.

Everything is drawn supersampled and scaled down, so edges stay smooth at any display scaling.
"""
import functools
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SS = 3                  # supersampling factor for the creature
DW, DH = 170, 110       # the strip that peeks out (logical px); the screen edge is at y = DH
CX, BASE = DW / 2, DH + 36  # creature centre line and where its feet would be (past the edge)

INK, BLUSH, TONGUE = "#4a3040", "#ffa3b5", "#ff7f9a"
PAPER, MUTED = "#fffaf3", "#8a7280"
ACCENT = {"ask": "#ff4d6d", "done": "#ff8fab", "hi": "#ffb38a", "stats": "#a78bfa"}

CHARACTERS = {
    "golden": dict(name="Golden pup", body="#f6d9a8", edge="#b8865a", ear="#a86b3c", ear_edge="#7d4c28",
                   patch="#e9b97f", muzzle="#fff3e0", ears="floppy", nose="dog", eye=INK,
                   sounds=("bark", "yip")),
    "corgi": dict(name="Corgi", body="#f2a65a", edge="#b86e2c", inner="#ffc9a8", muzzle="#fffaf3",
                  blaze=True, ears="big", nose="dog", eye=INK, sounds=("bark", "yip")),
    "husky": dict(name="Husky", body="#8e99a6", edge="#56616e", inner="#f1c9cf", muzzle="#f7f9fb",
                  mask=True, ears="pointy", nose="dog", eye="#2f8fd1", sounds=("awoo", "yip")),
    "cat": dict(name="Cat", body="#f4b26b", edge="#c07a35", inner="#ffc4c9", muzzle="#fff1e0",
                stripes="#d98a3d", whiskers=True, ears="pointy", nose="cat", eye=INK, sounds=("meow", "mew")),
    "frog": dict(name="Frog", body="#93d48e", edge="#4f9a4b", muzzle="#e6f5c9", ears="frog", nose="frog",
                 eye="#2b3a2b", sounds=("ribbit", "ribbit")),
}


# --- fonts -------------------------------------------------------------------

FONT_FILES = {
    "title": ["segoeprb.ttf", "Arial Rounded Bold.ttf", "DejaVuSans-Bold.ttf"],
    "body": ["seguisb.ttf", "Arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf"],
    "bold": ["seguibl.ttf", "segoeuib.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"],
}
FONT_DIRS = [os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts"), "/System/Library/Fonts",
             "/System/Library/Fonts/Supplemental", "/Library/Fonts", "/usr/share/fonts/truetype/dejavu"]


@functools.lru_cache(maxsize=64)
def font(kind, px):
    for name in FONT_FILES[kind]:
        for d in FONT_DIRS:
            path = os.path.join(d, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, max(6, round(px)))
    return ImageFont.load_default()


# --- drawing helpers ---------------------------------------------------------

def chaikin(pts, n=2):
    for _ in range(n):
        out = []
        for i in range(len(pts)):
            (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % len(pts)]
            out += [(0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1), (0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1)]
        pts = out
    return pts


def heart_pts(cx, cy, s):
    pts = []
    for i in range(28):
        a = 2 * math.pi * i / 28
        x = 16 * math.sin(a) ** 3
        y = -(13 * math.cos(a) - 5 * math.cos(2 * a) - 2 * math.cos(3 * a) - math.cos(4 * a))
        pts.append((cx + x * s / 17, cy + y * s / 17))
    return pts


def star_pts(cx, cy, r):
    pts = []
    for i in range(8):
        a = math.pi / 4 * i - math.pi / 2
        rr = r if i % 2 == 0 else r * 0.32
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


class Painter:
    """ImageDraw in logical units, scaled by k."""

    def __init__(self, w, h, k):
        self.k = k
        self.img = Image.new("RGBA", (max(1, round(w * k)), max(1, round(h * k))), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def _p(self, pts):
        return [(x * self.k, y * self.k) for x, y in pts]

    def ellipse(self, cx, cy, rx, ry, fill=None, outline=None, width=0):
        k = self.k
        self.d.ellipse([(cx - rx) * k, (cy - ry) * k, (cx + rx) * k, (cy + ry) * k],
                       fill=fill, outline=outline, width=round(width * k))

    def poly(self, pts, fill=None, outline=None, width=0, smooth=0):
        if smooth:
            pts = chaikin(pts, smooth)
        flat = self._p(pts)
        self.d.polygon(flat, fill=fill)
        if outline and width:
            self.line(pts + [pts[0]], outline, width)

    def line(self, pts, fill, width):
        flat = self._p(pts)
        w = round(width * self.k)
        self.d.line(flat, fill=fill, width=w, joint="curve")
        r = w / 2
        for x, y in (flat[0], flat[-1]):
            self.d.ellipse([x - r, y - r, x + r, y + r], fill=fill)

    def arc(self, cx, cy, rx, ry, start, extent, fill, width):
        """Counter-clockwise degrees from 3 o'clock, with rounded ends."""
        k = self.k
        box = [(cx - rx) * k, (cy - ry) * k, (cx + rx) * k, (cy + ry) * k]
        self.d.arc(box, -(start + extent), -start, fill=fill, width=round(width * k))
        r = width * k / 2
        for a in (start, start + extent):
            x, y = (cx + rx * math.cos(math.radians(a))) * k, (cy - ry * math.sin(math.radians(a))) * k
            self.d.ellipse([x - r, y - r, x + r, y + r], fill=fill)

    def lid(self, cx, cy, rx, ry, fill):
        k = self.k
        self.d.chord([(cx - rx) * k, (cy - ry) * k, (cx + rx) * k, (cy + ry) * k], 180, 360, fill=fill)

    def text(self, x, y, s, kind, px, fill):
        self.d.text((x * self.k, y * self.k), s, font=font(kind, px * self.k), fill=fill, anchor="mm")


# --- the creature ------------------------------------------------------------

def creature(char, state="done", t=0.0, k=1.0, sleepy=False, party=False, celebrate=False, excited=None,
             lift=0, particles=True, paw=True):
    """The peeking strip for one frame, upright (screen edge at the bottom), at scale k."""
    ch = CHARACTERS.get(char, CHARACTERS["golden"])
    p = Painter(DW, DH, k * SS)
    asking = state == "ask"
    accent = ACCENT.get(state, ACCENT["done"])
    body, edge = ch["body"], ch["edge"]
    if excited is None:
        excited = t < 6
    speed = 10 if excited else (2.5 if sleepy else 4)

    bounce = abs(math.sin(t * 5)) if excited else abs(math.sin(t * 1.5)) * (0.1 if sleepy else 0.25)
    stretch = 0.06 * (bounce - 0.5)
    cx = CX
    base = BASE - lift - bounce * 10
    hy = base - 52
    wide = 42 if ch["ears"] == "frog" else 38
    hrx, hry = wide * (1 - stretch), (28 if ch["ears"] == "frog" else 31) * (1 + stretch)
    top = hy - hry

    # --- particles, behind everything
    if particles and (party or celebrate):
        colors = ["#ff4d6d", "#ffd166", "#06d6a0", "#4cc9f0", "#b388ff"]
        for i in range(14):
            ph = (t * 0.45 + i * 0.137) % 1
            x = 12 + (i * 53) % (DW - 24) + math.sin(t * 2 + i) * 6
            y = 4 + ph * (top - 4)
            a = t * 4 + i
            pts = [(x + dx * math.cos(a) - dy * math.sin(a), y + dx * math.sin(a) + dy * math.cos(a))
                   for dx, dy in ((3.2, 1.6), (-3.2, 1.6), (-3.2, -1.6), (3.2, -1.6))]
            p.poly(pts, fill=colors[i % len(colors)])
    if not particles:
        pass
    elif sleepy:
        for i in range(3):
            ph = (t * 0.35 + i / 3) % 1
            p.text(cx + 30 + ph * 26, top - 4 - ph * 34, "z", "title", 9 + ph * 9, (*_rgb(MUTED), int(255 * (1 - ph))))
    elif not (party or celebrate):
        for i in range(3):
            ph = (t * 0.5 + i / 3) % 1
            px = cx + (i - 1) * 32 + math.sin(ph * 6 + i) * 8
            py = top - 4 - ph * 40
            size = 5 + 3 * math.sin(ph * math.pi)
            alpha = int(255 * min(1, (1 - ph) * 2.5))
            if asking and i != 1:
                p.poly(star_pts(px, py, size * 1.3), fill=(*_rgb(accent), alpha))
            else:
                p.poly(heart_pts(px, py, size), fill=(*_rgb(accent), alpha))

    # --- ears that sit behind the head
    if ch["ears"] in ("pointy", "big"):
        big = ch["ears"] == "big"
        twitch = 3 * math.sin(t * 14) if (t % 2.7) < 0.25 else 0
        for s in (-1, 1):
            tip = (cx + s * (34 if big else 28) + (twitch if s > 0 else 0), top - (26 if big else 17))
            outer = [(cx + s * (36 if big else 32), top + 16), tip, (cx + s * 6, top + 3)]
            p.poly(outer, fill=body, outline=edge, width=2.4, smooth=1)
            mx, my = sum(x for x, _ in outer) / 3, sum(y for _, y in outer) / 3 + 3
            inner = [(mx + (x - mx) * 0.55, my + (y - my) * 0.55) for x, y in outer]
            p.poly(inner, fill=ch["inner"], smooth=1)

    # --- head
    p.ellipse(cx, hy, hrx, hry, fill=body, outline=edge, width=2.6)
    if ch.get("patch"):
        p.ellipse(cx + 16, hy - 8, 12, 12, fill=ch["patch"])
    if ch.get("mask"):
        p.ellipse(cx, hy + 9, 29, 19, fill=ch["muzzle"])
        for s in (-1, 1):
            p.ellipse(cx + s * 13, hy - 19, 4.5, 3, fill=ch["muzzle"])
    if ch.get("blaze"):
        p.poly([(cx - 3, top + 3), (cx + 3, top + 3), (cx + 9, hy + 6), (cx - 9, hy + 6)], fill=ch["muzzle"], smooth=2)
    if ch.get("stripes"):
        for dx in (-9, 0, 9):
            p.line([(cx + dx, top + 3), (cx + dx * 0.8, top + 11)], ch["stripes"], 3)

    if ch["ears"] == "floppy":
        flap = (1 - bounce) * 6
        for s in (-1, 1):
            p.poly([(cx + s * 22, top + 2), (cx + s * (hrx + 10), top + 10), (cx + s * (hrx + 10), hy + 12 + flap),
                    (cx + s * (hrx - 2), hy + 16 + flap), (cx + s * (hrx - 8), hy - 6)],
                   fill=ch["ear"], outline=ch["ear_edge"], width=2, smooth=2)

    # --- muzzle + nose
    if ch["nose"] != "frog" and not ch.get("mask"):
        p.ellipse(cx, hy + 12, 15, 11, fill=ch["muzzle"])
    if ch["nose"] == "dog":
        p.ellipse(cx, hy + 5, 6, 4.2, fill=INK)
        p.ellipse(cx - 1.8, hy + 3.6, 1.8, 1.1, fill="white")
    elif ch["nose"] == "cat":
        p.poly([(cx - 4, hy + 3), (cx + 4, hy + 3), (cx, hy + 7.5)], fill="#ff8fa3", smooth=1)
    else:
        for s in (-1, 1):
            p.ellipse(cx + s * 4, hy + 3, 1.3, 1.1, fill=edge)

    # --- eyes
    if ch["ears"] == "frog":
        eye_y, eye_dx = top + 3, 17
        for s in (-1, 1):
            p.ellipse(cx + s * eye_dx, eye_y, 14, 13, fill=body, outline=edge, width=2.4)
    else:
        eye_y, eye_dx = hy - 7, 15
    happy = state != "ask" and not sleepy and ((t % 4) > 3.2 or celebrate and t < 2.5)
    blink = (t % 3.3) < 0.1
    for s in (-1, 1):
        ex = cx + s * eye_dx
        if happy:
            p.arc(ex, eye_y + 2, 6.5, 6, 30, 120, INK, 2.6)
        elif blink:
            p.arc(ex, eye_y - 2, 6.5, 6, 210, 120, INK, 2.6)
        else:
            h = 9.5 if asking else 8.5
            if ch["eye"] != INK and ch["ears"] != "frog":
                p.ellipse(ex, eye_y, 7, h, fill=ch["eye"])
                p.ellipse(ex, eye_y + 1, 4, h * 0.55, fill=INK)
            else:
                p.ellipse(ex, eye_y, 7, h, fill=ch["eye"])
            p.ellipse(ex - 1.5, eye_y - h + 4, 2.6, 2.6, fill="white")
            p.ellipse(ex + 3, eye_y + 2.5, 1.2, 1.2, fill="white")
            if sleepy:
                p.lid(ex, eye_y, 7.6, h + 0.6, body)
                p.line([(ex - 7, eye_y), (ex + 7, eye_y)], INK, 2)
    if ch.get("whiskers"):
        for s in (-1, 1):
            for dy, ang in ((0, -6), (5, 6)):
                x0 = cx + s * 17
                p.line([(x0, hy + 8 + dy), (x0 + s * 16, hy + 8 + dy + ang * 0.5)], ch["edge"], 1.4)
    for s in (-1, 1):
        p.ellipse(cx + s * 26, hy + 9.5, 6, 3, fill=BLUSH)

    # --- mouth
    my = hy + 12
    if ch["ears"] == "frog":
        if asking:
            p.ellipse(cx, my + 2, 4, 3 + abs(math.sin(t * 6)), fill=INK)
        else:
            p.arc(cx, my - 6, 20, 9, 200, 140, INK, 2.4)
    elif asking:
        o = 2.5 + 1.5 * abs(math.sin(t * 6))
        p.ellipse(cx, my + 2, o, o, fill=INK)
    else:
        if ch["nose"] == "dog" and not sleepy:
            p.ellipse(cx, my + 6 + math.sin(t * 8), 4.8, 6, fill=TONGUE, outline="#e05a7a", width=0.8)
        p.arc(cx - 4, my - 1, 4, 4, 180, 180, INK, 1.8)
        p.arc(cx + 4, my - 1, 4, 4, 180, 180, INK, 1.8)
    if asking:  # sweat drop
        dx, dy = cx - hrx + 2, top + 8 + 3 * math.sin(t * 3)
        p.poly([(dx, dy - 8), (dx - 5, dy + 2), (dx, dy + 6), (dx + 5, dy + 2)], fill="#9ad4f5", outline="#5aa9d6",
               width=1, smooth=2)

    # --- party hat
    if party:
        apex = (cx + 10, top - 30)
        l, r = (cx - 14, top + 6), (cx + 20, top - 2)
        p.poly([l, apex, r], fill=accent, outline="#c9184a", width=1.6)
        for f in (0.35, 0.68):
            a = (l[0] + (apex[0] - l[0]) * f, l[1] + (apex[1] - l[1]) * f)
            b = (r[0] + (apex[0] - r[0]) * f, r[1] + (apex[1] - r[1]) * f)
            p.line([a, b], "#ffd166", 2.6)
        p.ellipse(*apex, 4.5, 4.5, fill="#ffd166", outline="#e0a800", width=1)

    # --- waving paw, in front, coming up from past the screen edge
    if not paw:
        return _finish(p.img, k, celebrate, t)
    sx, sy = cx + 46, base - 22
    ang = math.radians(-85 + 30 * math.sin(t * speed))
    ex, ey = sx + 40 * math.cos(ang), sy + 40 * math.sin(ang)
    p.line([(sx, sy), (ex, ey)], edge, 14)
    p.line([(sx, sy), (ex, ey)], body, 9)
    p.ellipse(ex, ey, 10, 10, fill=body, outline=edge, width=2.6)
    if ch["ears"] == "frog":
        for bx, by in ((-6, -7), (0, -9.5), (6, -7)):
            p.ellipse(ex + bx, ey + by, 3, 3, fill=ch["muzzle"], outline=edge, width=1)
    else:
        p.ellipse(ex, ey + 2, 4, 3, fill=BLUSH)
        for bx, by in ((-6, -5), (0, -7), (6, -5)):
            p.ellipse(ex + bx, ey + by, 2, 2, fill=BLUSH)

    return _finish(p.img, k, celebrate, t)


def _finish(img, k, celebrate, t):
    if celebrate and t < 2.5:  # happy wiggle
        img = img.rotate(12 * math.sin(t * 9) * (1 - t / 2.5), resample=Image.BICUBIC,
                         center=(CX * k * SS, DH * k * SS))
    return img.resize((round(DW * k), round(DH * k)), Image.LANCZOS)


def _rgb(hexcolor):
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# --- speech bubble -----------------------------------------------------------

BUTTONS = {"allow": ("Allow", "#4cbf86", "#3aa772"), "always": ("Always", "#6f9cf2", "#5482dc"),
           "deny": ("Deny", "#f2777a", "#dc5d61")}


@functools.lru_cache(maxsize=32)
def bubble(title, sub, accent, tail, k, buttons=(), pager=None, hover=None):
    """Returns (image, hitboxes). Hitboxes are in image pixels: name -> (x1, y1, x2, y2)."""
    ft, fs, fb = font("title", 14 * k), font("body", 11 * k), font("bold", 10.5 * k)
    pad, tail_len, shadow = 12 * k, 9 * k, 4 * k
    measure = ImageDraw.Draw(Image.new("L", (1, 1)))
    tw = measure.textlength(title, font=ft) + 16 * k
    sw = measure.textlength(sub, font=fs) if sub else 0
    pager_w = measure.textlength(pager, font=fb) + 30 * k if pager else 0
    btn = []
    for name in buttons:
        label = BUTTONS[name][0]
        btn.append((name, label, measure.textlength(label, font=fb) + 22 * k))
    btn_w = sum(w for _, _, w in btn) + 6 * k * max(0, len(btn) - 1)
    inner_w = max(tw + pager_w, sw, btn_w, 120 * k)
    title_h, sub_h, btn_h = 22 * k, (16 * k if sub else 0), (26 * k if btn else 0)
    inner_h = title_h + sub_h + (btn_h + 6 * k if btn else 0)
    bw, bh = inner_w + 2 * pad, inner_h + pad * 1.2

    # margins around the bubble for the tail and the shadow
    ml = mt = shadow
    mr, mb = shadow, shadow * 1.5
    if tail == "down":
        mb += tail_len
    elif tail == "up":
        mt += tail_len
    elif tail == "left":
        ml += tail_len
    else:
        mr += tail_len
    W, H = round(ml + bw + mr), round(mt + bh + mb)
    x1, y1, x2, y2 = ml, mt, ml + bw, mt + bh
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    tri = {"down": [(mx - 8 * k, y2 - 2), (mx + 8 * k, y2 - 2), (mx + 2 * k, y2 + tail_len)],
           "up": [(mx - 8 * k, y1 + 2), (mx + 8 * k, y1 + 2), (mx + 2 * k, y1 - tail_len)],
           "left": [(x1 + 2, my - 8 * k), (x1 + 2, my + 8 * k), (x1 - tail_len, my + 2 * k)],
           "right": [(x2 - 2, my - 8 * k), (x2 - 2, my + 8 * k), (x2 + tail_len, my + 2 * k)]}[tail]

    # shape mask, supersampled
    S = SS
    mask = Image.new("L", (W * S, H * S), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([x1 * S, y1 * S, x2 * S, y2 * S], radius=14 * k * S, fill=255)
    md.polygon([(x * S, y * S) for x, y in tri], fill=255)
    border = max(1, round(2 * k * S)) * 2 + 1
    inner = mask.filter(ImageFilter.MinFilter(border))
    mask, inner = mask.resize((W, H), Image.LANCZOS), inner.resize((W, H), Image.LANCZOS)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sh = Image.new("RGBA", (W, H), (60, 30, 50, 0))
    sh.putalpha(mask.point(lambda v: v * 0.28).filter(ImageFilter.GaussianBlur(3 * k)))
    img.alpha_composite(sh, (0, round(2 * k)))
    img.alpha_composite(_solid(W, H, accent, mask))
    img.alpha_composite(_solid(W, H, PAPER, inner))

    d = ImageDraw.Draw(img)
    hits = {}
    tx = x1 + pad
    ty = y1 + pad * 0.6
    d.text((tx, ty + title_h / 2), title, font=ft, fill=INK, anchor="lm")
    hx = tx + measure.textlength(title, font=ft) + 9 * k
    d.polygon(heart_pts(hx, ty + title_h / 2 + 1 * k, 5.5 * k), fill=accent)
    if pager:
        pw = measure.textlength(pager, font=fb)
        px2 = x2 - pad
        cy = ty + title_h / 2
        d.text((px2, cy), "\u203a", font=fb, fill=accent, anchor="rm")
        d.text((px2 - 12 * k, cy), pager, font=fb, fill=MUTED, anchor="rm")
        d.text((px2 - 16 * k - pw, cy), "\u2039", font=fb, fill=accent, anchor="rm")
        hits["next"] = (px2 - 10 * k, cy - 11 * k, px2 + 4 * k, cy + 11 * k)
        hits["prev"] = (px2 - 26 * k - pw, cy - 11 * k, px2 - 12 * k - pw, cy + 11 * k)
    if sub:
        d.text((tx, ty + title_h + sub_h / 2), sub, font=fs, fill=MUTED, anchor="lm")
    if btn:
        bx = tx
        by = ty + title_h + sub_h + 6 * k
        for name, label, w in btn:
            _, color, dark = BUTTONS[name]
            box = (bx, by, bx + w, by + btn_h)
            d.rounded_rectangle(box, radius=btn_h / 2, fill=dark if hover == name else color)
            d.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), label, font=fb, fill="white", anchor="mm")
            hits[name] = box
            bx += w + 6 * k
    return img, hits


def _solid(w, h, color, alpha):
    layer = Image.new("RGBA", (w, h), _rgb(color) + (255,))
    layer.putalpha(alpha)
    return layer


# --- layouts -----------------------------------------------------------------

ROTATE = {"bottom": None, "top": Image.ROTATE_180, "right": Image.ROTATE_90, "left": Image.ROTATE_270}
TAIL = {"bottom": "down", "top": "up", "right": "right", "left": "left"}


def compose(edge, crit, bub=None, hits=None, k=1.0):
    """Places the creature against `edge` and the bubble on the screen side of it.

    Returns (image, hitboxes) where hitboxes also include "creature".
    """
    c = crit.transpose(ROTATE[edge]) if ROTATE[edge] is not None else crit
    overlap = round(16 * k)  # bubble may cover the empty sky above the head
    if bub is None:
        return c, {"creature": (0, 0, c.width, c.height)}
    bw, bh = bub.size
    if edge in ("bottom", "top"):
        W, H = max(c.width, bw), c.height + bh - overlap
        cx0, bx0 = (W - c.width) // 2, (W - bw) // 2
        cy0, by0 = (bh - overlap, 0) if edge == "bottom" else (0, c.height - overlap)
    else:
        W, H = c.width + bw - overlap, max(c.height, bh)
        cy0, by0 = (H - c.height) // 2, (H - bh) // 2
        cx0, bx0 = (bw - overlap, 0) if edge == "right" else (0, c.width - overlap)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.alpha_composite(c, (cx0, cy0))
    out.alpha_composite(bub, (bx0, by0))
    boxes = {n: (a + bx0, b + by0, cc + bx0, d + by0) for n, (a, b, cc, d) in (hits or {}).items()}
    boxes["creature"] = (cx0, cy0, cx0 + c.width, cy0 + c.height)
    boxes["bubble"] = (bx0, by0, bx0 + bw, by0 + bh)
    return out, boxes


def icon(char="golden", size=256):
    """Square head portrait, for the tray icon and the README."""
    s = size / 88
    img = creature(char, "done", t=1.0, k=s, excited=False, lift=34, particles=False, paw=False)
    box = (round((CX - 44) * s), round(5 * s), round((CX + 44) * s), round(93 * s))
    return img.crop(box).resize((size, size), Image.LANCZOS)


if __name__ == "__main__":  # quick preview sheet
    out = sys.argv[1] if len(sys.argv) > 1 else "preview.png"
    k = 2
    sheet = Image.new("RGBA", (round(DW * k) * 5, round(DH * k) * 2), "#2b2d31")
    for i, name in enumerate(CHARACTERS):
        sheet.alpha_composite(creature(name, "done", 1.0, k), (round(DW * k) * i, 0))
        sheet.alpha_composite(creature(name, "ask", 1.3, k, sleepy=(i == 4), party=(i == 0)),
                              (round(DW * k) * i, round(DH * k)))
    sheet.save(out)

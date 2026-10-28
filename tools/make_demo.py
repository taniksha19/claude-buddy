"""Renders docs/demo.gif and docs/characters.png straight from the renderer (no screen capture)."""
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from PIL import Image, ImageDraw  # noqa: E402

import render  # noqa: E402

W, H, FPS, K = 560, 315, 15, 1.0
DOCS = os.path.join(ROOT, "docs")


def backdrop():
    """A made-up editor, so the demo shows nobody's real screen."""
    img = Image.new("RGBA", (W, H), "#1e2230")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 22], fill="#161925")
    for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        d.ellipse([10 + i * 16, 7, 19 + i * 16, 16], fill=c)
    d.rectangle([0, 22, 44, H], fill="#191c28")
    rnd = random.Random(4)
    colors = ["#7aa2f7", "#bb9af7", "#9ece6a", "#e0af68", "#565f89", "#c0caf5"]
    for row in range(17):
        y = 34 + row * 16
        d.text((50, y - 1), f"{row + 1:>2}", fill="#3b4261")
        x = 76 + rnd.choice((0, 0, 16, 32))
        for _ in range(rnd.randint(1, 5)):
            w = rnd.randint(18, 70)
            if x + w > W - 20:
                break
            d.rounded_rectangle([x, y + 3, x + w, y + 9], radius=3, fill=rnd.choice(colors))
            x += w + 8
    return img


SCENES = [  # character, edge, record, seconds
    ("golden", "bottom", dict(state="ask", title="woof! i need you", sub="my-app · Bash: npm run build",
                              buttons=("allow", "always", "deny")), 3.0),
    ("corgi", "right", dict(state="done", title="all done!", sub="website · your turn"), 2.4),
    ("cat", "top", dict(state="done", title="tests passed!", sub="api · your turn", party=True), 2.6),
    ("frog", "left", dict(state="ask", title="ribbit! i need you", sub="docs · Which style guide?"), 2.4),
]


def frame(bg, char, edge, rec, t, progress, anchor=0.5):
    crit = render.creature(char, rec["state"], t, K, party=rec.get("party", False))
    bub, hits = render.bubble(rec["title"], rec["sub"], render.ACCENT[rec["state"]], render.TAIL[edge], K,
                              rec.get("buttons", ()), None, "allow" if 1.8 < t < 2.4 and rec.get("buttons") else None)
    img, _ = render.compose(edge, crit, bub, hits, K)
    w, h = img.size
    e = 1 - (1 - progress) ** 3
    if edge in ("bottom", "top"):
        x = int(W * anchor - w / 2)
        y = int(H - h * e) if edge == "bottom" else int(-h + h * e)
    else:
        y = int(H * anchor - h / 2)
        x = int(W - w * e) if edge == "right" else int(-w + w * e)
    out = bg.copy()
    out.paste(img, (x, y), img)  # paste (unlike alpha_composite) allows negative offsets
    return out


def main():
    os.makedirs(DOCS, exist_ok=True)
    bg = backdrop()
    frames = []
    for char, edge, rec, secs in SCENES:
        n = int(secs * FPS)
        for i in range(n):
            t = i / FPS
            slide_in = min(1, t / 0.45)
            slide_out = min(1, max(0, (secs - t) / 0.35))
            frames.append(frame(bg, char, edge, rec, t, min(slide_in, slide_out),
                                anchor=0.62 if edge in ("bottom", "top") else 0.55))
        frames += [bg.copy()] * 4
    pal = [f.convert("RGB").quantize(colors=128, method=Image.MEDIANCUT, dither=Image.NONE) for f in frames]
    pal[0].save(os.path.join(DOCS, "demo.gif"), save_all=True, append_images=pal[1:], duration=int(1000 / FPS),
                loop=0, optimize=True, disposal=1)

    lineup = Image.new("RGBA", (5 * 150 + 10, 190), "#fdf6f0")
    d = ImageDraw.Draw(lineup)
    for i, (key, ch) in enumerate(render.CHARACTERS.items()):
        lineup.alpha_composite(render.icon(key, 130), (10 + i * 150, 10))
        d.text((10 + i * 150 + 65, 165), ch["name"], fill=render.INK, font=render.font("title", 15), anchor="mm")
    lineup.save(os.path.join(DOCS, "characters.png"))
    print("wrote docs/demo.gif", round(os.path.getsize(os.path.join(DOCS, "demo.gif")) / 1024), "KB")


if __name__ == "__main__":
    main()

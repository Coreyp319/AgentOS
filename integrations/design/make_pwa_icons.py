#!/usr/bin/env python3
"""Unified PWA icon family for the AgentOS web apps — ONE source of truth.

Every AgentOS PWA wears the same **instrument plate** (the deep-navy glass register from
integrations/design/instrument-tokens.md) with a distinct **monoline glyph** gradient-filled
from the sanctioned palette, so the home screen reads as one coherent set:

  • atrium (the status-panel launch view) — an ARCHWAY (the front door)           · blue→copper
  • lucid  (the dream loop)              — a CRESCENT + star (dreaming, at night) · indigo→violet
  • share  (phone→box photo ingest)     — an ARROW into a TRAY (ingest)           · blue→copper

The indigo→violet for Lucid is the instrument register's own "aurora cool" ramp (auroraMid
#4A5AD2 → auroraHi #8A6BDC) — it nods to Lucid's old purple while staying in the family palette.

Outputs, per app: icon-192 / icon-512 (rounded, purpose "any"), icon-512-maskable (full-bleed,
safe-zone), apple-touch-icon (180, full-bleed for iOS), and an SVG favicon. The reserved warm
(#ff9957) is never used.

Run:  python3 integrations/design/make_pwa_icons.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parent.parent.parent

# ── palette (mirrors instrument-tokens.md) ────────────────────────────────────────────────
NAVY_TOP = (26, 34, 56)     # --inst-horizon #1a2238
NAVY_BASE = (16, 18, 26)    # a touch under --inst-base #12141c, so the plate has depth
# accent endpoints lifted a step for legibility at icon scale — still calm, copper stays well
# under the reserved warm (#ff9957).
BLUE = (140, 176, 255)      # lifted --inst-blue
COPPER = (236, 154, 92)     # lifted --brand-warm (< reserved #ff9957)
INDIGO = (94, 112, 228)     # aurora mid, lifted
VIOLET = (162, 128, 236)    # aurora hi, lifted

APPS = {
    "atrium": {"glyph": "arch",  "accent": (BLUE, COPPER)},
    "lucid":  {"glyph": "moon",  "accent": (INDIGO, VIOLET)},
    "share":  {"glyph": "inbox", "accent": (BLUE, COPPER)},
}

SS = 4  # supersample factor for crisp anti-aliased strokes


# ── plate ─────────────────────────────────────────────────────────────────────────────────

def _round_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def plate(size: int, maskable: bool) -> Image.Image:
    """The shared instrument plate: a deep-navy vertical gradient (lighter at the top, like the
    panel sky) with a soft top sheen and a 1px glass hairline. Rounded for 'any'; full-bleed for
    maskable / apple-touch (the OS supplies the mask)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    for y in range(size):
        t = y / (size - 1)
        r = round(NAVY_TOP[0] + (NAVY_BASE[0] - NAVY_TOP[0]) * t)
        g = round(NAVY_TOP[1] + (NAVY_BASE[1] - NAVY_TOP[1]) * t)
        b = round(NAVY_TOP[2] + (NAVY_BASE[2] - NAVY_TOP[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b, 255)
    # soft top-centre sheen
    sheen = Image.new("L", (size, size), 0)
    sd = ImageDraw.Draw(sheen)
    sd.ellipse([-size * 0.3, -size * 0.62, size * 1.3, size * 0.36], fill=42)
    sheen = sheen.filter(ImageFilter.GaussianBlur(size * 0.06))
    grad = Image.composite(Image.new("RGBA", (size, size), (255, 255, 255, 255)), grad, sheen)

    if maskable:
        img = grad
    else:
        radius = round(size * 0.225)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        img.paste(grad, (0, 0), _round_mask(size, radius))
        # 1px glass hairline just inside the edge
        ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        rd = ImageDraw.Draw(ring)
        inset = max(1, round(size * 0.012))
        rd.rounded_rectangle([inset, inset, size - 1 - inset, size - 1 - inset],
                             radius=radius - inset, outline=(255, 255, 255, 30),
                             width=max(1, round(size * 0.006)))
        img.alpha_composite(ring)
    return img


# ── glyph drawing (returns an L mask: white glyph on black) ────────────────────────────────

def _draw(size: int, scale: float, fn) -> Image.Image:
    """Run `fn(draw, T, W)` where T maps 0..100 glyph-space → pixels (centred, glyph fills ~52%·scale
    of the icon) and W is the stroke width. Drawn supersampled, then downscaled for clean AA."""
    S = size * SS
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    span = S * 0.56 * scale
    W = max(2, round(S * 0.06 * scale))

    def T(x, y):
        return (S / 2 + (x - 50) / 100 * span, S / 2 + (y - 50) / 100 * span)

    fn(d, T, W)
    return m.resize((size, size), Image.LANCZOS)


def _seg(d, T, p0, p1, W):
    """A round-capped stroke segment in glyph space."""
    a, b = T(*p0), T(*p1)
    d.line([a, b], fill=255, width=W)
    rr = W / 2
    for c in (a, b):
        d.ellipse([c[0] - rr, c[1] - rr, c[0] + rr, c[1] + rr], fill=255)


def glyph_arch(size, scale):
    """An archway / portal — two legs and a semicircular top, open at the foot."""
    def fn(d, T, W):
        top = T(28, 50); right = T(72, 50)
        bbox = [top[0], top[1] - (right[1] - top[1]) * 0 - (T(72, 50)[0] - T(28, 50)[0]) / 2,
                right[0], top[1] + (T(72, 50)[0] - T(28, 50)[0]) / 2]
        d.arc(bbox, 180, 360, fill=255, width=W)
        _seg(d, T, (28, 50), (28, 70), W)   # left leg
        _seg(d, T, (72, 50), (72, 70), W)   # right leg
    return _draw(size, scale, fn)


def glyph_moon(size, scale):
    """A crescent (a disc minus an offset disc) + a small four-point star."""
    def fn(d, T, W):
        # crescent: big disc minus an offset disc, drawn as a filled mask diff
        c1 = T(52, 50); r1 = (T(76, 50)[0] - T(52, 50)[0])
        c2 = T(63, 43); r2 = r1 * 0.92
        big = Image.new("L", d.im.size, 0)
        ImageDraw.Draw(big).ellipse([c1[0]-r1, c1[1]-r1, c1[0]+r1, c1[1]+r1], fill=255)
        cut = Image.new("L", d.im.size, 0)
        ImageDraw.Draw(cut).ellipse([c2[0]-r2, c2[1]-r2, c2[0]+r2, c2[1]+r2], fill=255)
        from PIL import ImageChops
        cres = ImageChops.subtract(big, cut)
        d.bitmap((0, 0), cres, fill=255)
        # a four-point sparkle, upper-left
        sx, sy = T(31, 33); s = W * 1.7
        d.polygon([(sx, sy - s), (sx + s * 0.32, sy - s * 0.32), (sx + s, sy),
                   (sx + s * 0.32, sy + s * 0.32), (sx, sy + s), (sx - s * 0.32, sy + s * 0.32),
                   (sx - s, sy), (sx - s * 0.32, sy - s * 0.32)], fill=255)
    return _draw(size, scale, fn)


def glyph_inbox(size, scale):
    """A downward arrow descending into an open tray — 'ingest'."""
    def fn(d, T, W):
        # tray: an open-topped box (bottom + two short uprights)
        _seg(d, T, (30, 66), (70, 66), W)
        _seg(d, T, (30, 66), (30, 56), W)
        _seg(d, T, (70, 66), (70, 56), W)
        # arrow into it
        _seg(d, T, (50, 28), (50, 56), W)
        _seg(d, T, (50, 57), (42, 48), W)
        _seg(d, T, (50, 57), (58, 48), W)
    return _draw(size, scale, fn)


GLYPHS = {"arch": glyph_arch, "moon": glyph_moon, "inbox": glyph_inbox}


# ── compose ────────────────────────────────────────────────────────────────────────────────

def _gradient(size: int, c0, c1) -> Image.Image:
    """A diagonal gradient (top-left c0 → bottom-right c1)."""
    g = Image.new("RGBA", (size, size))
    px = g.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = (round(c0[0] + (c1[0]-c0[0])*t), round(c0[1] + (c1[1]-c0[1])*t),
                        round(c0[2] + (c1[2]-c0[2])*t), 255)
    return g


def render(app: str, size: int, maskable: bool) -> Image.Image:
    cfg = APPS[app]
    base = plate(size, maskable)
    scale = 0.80 if maskable else 1.0
    mask = GLYPHS[cfg["glyph"]](size, scale)
    grad = _gradient(size, *cfg["accent"])
    # a faint glyph drop-glow for depth (the accent, blurred)
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow.paste(grad, (0, 0), mask)
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.02))
    base.alpha_composite(Image.blend(Image.new("RGBA", (size, size), (0, 0, 0, 0)), glow, 0.5))
    base.paste(grad, (0, 0), mask)
    return base


# ── SVG favicon (hand-built so it's crisp + tiny; mirrors the PNG glyphs) ──────────────────

def _hex(c): return "#%02x%02x%02x" % c

def svg_favicon(app: str) -> str:
    cfg = APPS[app]; c0, c1 = cfg["accent"]
    grad = (f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{_hex(c0)}"/><stop offset="1" stop-color="{_hex(c1)}"/>'
            f'</linearGradient>')
    plate = (f'<rect x="2" y="2" width="92" height="92" rx="21" fill="#12141c"/>'
             f'<rect x="2" y="2" width="92" height="92" rx="21" fill="url(#sky)"/>')
    sky = ('<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
           '<stop offset="0" stop-color="#1a2238" stop-opacity=".9"/>'
           '<stop offset="1" stop-color="#12141c" stop-opacity="0"/></linearGradient>')
    sw = 6.0
    if cfg["glyph"] == "arch":
        g = (f'<path d="M30 50 A18 18 0 0 1 66 50" fill="none" stroke="url(#g)" stroke-width="{sw}" stroke-linecap="round"/>'
             f'<path d="M30 50 V70 M66 50 V70" stroke="url(#g)" stroke-width="{sw}" stroke-linecap="round" fill="none"/>')
    elif cfg["glyph"] == "moon":
        g = ('<path d="M62 30 A22 22 0 1 0 62 70 A17 17 0 1 1 62 30 Z" fill="url(#g)"/>'
             '<path d="M33 30 l2.4 5 5 2.4 -5 2.4 -2.4 5 -2.4 -5 -5 -2.4 5 -2.4 Z" fill="url(#g)"/>')
    else:  # inbox
        g = (f'<path d="M30 66 H70 M30 66 V56 M70 66 V56 M50 30 V57 M50 58 l-8 -9 M50 58 l8 -9" '
             f'fill="none" stroke="url(#g)" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">'
            f'<defs>{sky}{grad}</defs>{plate}{g}</svg>\n')


# ── outputs ────────────────────────────────────────────────────────────────────────────────

def write_png_set(outdir: Path, app: str, apple: bool = True, main_maskable: bool = False):
    # main_maskable: render the 192/512 "main" icons full-bleed too (for apps whose manifest
    # declares them purpose "any maskable", e.g. the Share hub).
    outdir.mkdir(parents=True, exist_ok=True)
    render(app, 192, main_maskable).save(outdir / "icon-192.png")
    render(app, 512, main_maskable).save(outdir / "icon-512.png")
    render(app, 512, True).save(outdir / "icon-512-maskable.png")
    if apple:
        render(app, 180, True).save(outdir / "apple-touch-icon.png")


def contact_sheet(path: Path):
    cell, pad = 200, 24
    apps = list(APPS)
    sheet = Image.new("RGBA", (len(apps) * (cell + pad) + pad, 3 * (cell + pad) + pad), (8, 9, 14, 255))
    for col, app in enumerate(apps):
        for row, im in enumerate([render(app, cell, False), render(app, cell, True),
                                  render(app, cell, True)]):
            sheet.alpha_composite(im, (pad + col * (cell + pad), pad + row * (cell + pad)))
    sheet.save(path)


def main():
    atrium = REPO / "integrations/status-panel/icons"
    write_png_set(atrium, "atrium")

    lucid_pub = REPO / "spikes/dreaming/lucid/web/public"
    lucid_dist = REPO / "spikes/dreaming/lucid/web/dist"
    for d in (lucid_pub, lucid_dist):
        if d.parent.exists():
            write_png_set(d, "lucid")
            (d / "favicon.svg").write_text(svg_favicon("lucid"))

    share = REPO / "spikes/dreaming/lucid/share_assets"
    write_png_set(share, "share", apple=False, main_maskable=True)  # share manifest = "any maskable"

    contact_sheet(Path("/tmp/icon_family.png"))
    print("wrote icon family for:", ", ".join(APPS))
    print("contact sheet → /tmp/icon_family.png")


if __name__ == "__main__":
    main()

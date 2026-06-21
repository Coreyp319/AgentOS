#!/usr/bin/env python3
"""Generate the Atrium PWA icons from the instrument 'mark' (the status panel's brand square):
a rounded square with a blue→copper diagonal gradient and a darker inset square — no new art,
just the existing mark rendered to PNG. Run once:  python3 make_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ICONS = HERE / "icons"
ICONS.mkdir(exist_ok=True)

BG = (18, 20, 28)          # --inst-base #12141c
BLUE = (122, 162, 255)     # --inst-blue #7aa2ff
COPPER = (224, 136, 79)    # --brand-warm #e0884f


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def make(size, maskable=False):
    img = Image.new("RGBA", (size, size), BG + (255,))
    # safe zone: maskable icons get padding so the OS mask never clips the mark.
    pad = int(size * 0.18) if maskable else int(size * 0.10)
    inner = size - 2 * pad
    # diagonal blue→copper gradient tile
    grad = Image.new("RGBA", (inner, inner))
    px = grad.load()
    for y in range(inner):
        for x in range(inner):
            t = (x + y) / (2 * (inner - 1))
            px[x, y] = _lerp(BLUE, COPPER, t) + (255,)
    radius = int(inner * 0.27)
    grad.putalpha(_rounded_mask(inner, radius))
    img.alpha_composite(grad, (pad, pad))
    # darker inset square (the mark::after cutout), centered
    cut = int(inner * 0.36)
    co = pad + (inner - cut) // 2
    cutimg = Image.new("RGBA", (cut, cut), BG + (200,))
    cutimg.putalpha(_rounded_mask(cut, int(cut * 0.22)).point(lambda v: int(v * 0.78)))
    img.alpha_composite(cutimg, (co, co))
    return img


def main():
    make(192).save(ICONS / "icon-192.png")
    make(512).save(ICONS / "icon-512.png")
    make(512, maskable=True).save(ICONS / "icon-512-maskable.png")
    print("wrote:", *(p.name for p in sorted(ICONS.glob("*.png"))))


if __name__ == "__main__":
    main()

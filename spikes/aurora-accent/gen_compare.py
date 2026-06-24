#!/usr/bin/env python3
"""Generate a faithful BEFORE/AFTER illustration of the Aurora look from the ACTUAL token
values (radii, elevation-shadow alphas, focus colours, AuroraDark scheme RGBs). This is a
spec render, NOT a live Qt screenshot — the harness can't render a real window. Emits SVG.
"""
W, H, PW = 1240, 660, 620

# --- AuroraDark scheme + token values (the real numbers) ---------------------
DESK="#15131f"; WIN="#282532"; MENU="#2c2838"; VIEW="#24212e"; BTN="#403c50"
BORDER="#464256"; HAIR="#0d0c14"; TXT="#e6e4f0"; DIM="#9690aa"; WHITE="#fafafc"
FOCUS_SOLID="#c7bde5"          # AuroraDark DecorationFocus 199,189,229 (the new solid ring)
FOCUS_TRANS="#c7a8fa"          # breeze focus 199,168,250 @0.30 alpha (translucent)

def esc(s): return s.replace("&","&amp;").replace("<","&lt;")
def text(x,y,s,fill=TXT,size=15,w=400,anchor="start",fam="sans-serif"):
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-family="{fam}" font-size="{size}" '
            f'font-weight="{w}" text-anchor="{anchor}">{esc(s)}</text>')

def panel(ox, after):
    crx = 6 if after else 5            # control corner radius
    frx = 12 if after else 5           # floating-surface corner radius
    wrx = 10 if after else 4
    accent = "#765cc4" if after else "#7c5cc4"   # 118,92,196 vs 124,92,196
    title  = "AFTER · aurora" if after else "BEFORE · breeze"
    cap1   = "6px controls · 12px floating cards" if after else "5px corners · everything flat"
    cap2   = "soft elevation lift · SOLID lavender focus" if after else "no lift · translucent focus (fails WCAG)"
    e=[f'<rect x="{ox}" y="0" width="{PW}" height="{H}" fill="{DESK}"/>']
    wx,wy,ww,wh = ox+34, 56, PW-68, H-150
    # window
    if after:
        e.append(f'<rect x="{wx}" y="{wy}" width="{ww}" height="{wh}" rx="{wrx}" fill="{WIN}" filter="url(#winlift)"/>')
    else:
        e.append(f'<rect x="{wx}" y="{wy}" width="{ww}" height="{wh}" rx="{wrx}" fill="{WIN}" stroke="{HAIR}" stroke-width="1"/>')
    e.append(text(ox+PW/2, 38, title, TXT, 22, 700, "middle"))
    e.append(text(wx+22, wy+30, "Control gallery", DIM, 13, 400))

    # --- buttons row ---------------------------------------------------------
    by = wy+50; bw,bh,gap = 120,38,18; bx=wx+22
    def button(x,label,fill,tcol,focused=False):
        o=[f'<rect x="{x}" y="{by}" width="{bw}" height="{bh}" rx="{crx}" fill="{fill}" stroke="{BORDER}" stroke-width="1"/>',
           text(x+bw/2, by+bh/2+5, label, tcol, 15, 500, "middle")]
        if focused:
            if after:
                o.append(f'<rect x="{x-2}" y="{by-2}" width="{bw+4}" height="{bh+4}" rx="{crx+2}" fill="none" stroke="{FOCUS_SOLID}" stroke-width="2"/>')
            else:
                o.append(f'<rect x="{x-2}" y="{by-2}" width="{bw+4}" height="{bh+4}" rx="{crx+2}" fill="none" stroke="{FOCUS_TRANS}" stroke-width="2" stroke-opacity="0.30"/>')
        return o
    e += button(bx, "Normal", BTN, TXT)
    e += button(bx+bw+gap, "Accent", accent, WHITE)
    e += button(bx+2*(bw+gap), "Focused", BTN, TXT, focused=True)
    e.append(text(bx, by+bh+24, "← Tab focus ring", DIM, 12, 400))

    # --- the floating menu (the headline difference) -------------------------
    mx,my,mw,mh = wx+22, by+80, 250, 176
    filt = ' filter="url(#menulift)"' if after else ''
    strk = '' if after else f' stroke="{HAIR}" stroke-width="1"'
    e.append(f'<rect x="{mx}" y="{my}" width="{mw}" height="{mh}" rx="{frx}" fill="{MENU}"{filt}{strk}/>')
    items=["Open…","Save","Rename","Delete"]
    iy=my+14
    for i,it in enumerate(items):
        ry=iy+i*38
        if i==1:  # highlighted (hover) item — uses the focus-color fill
            e.append(f'<rect x="{mx+8}" y="{ry-4}" width="{mw-16}" height="32" rx="{crx}" fill="{FOCUS_SOLID}" fill-opacity="0.22" stroke="{accent}" stroke-width="1"/>')
        if i==2:
            e.append(f'<line x1="{mx+12}" y1="{ry-12}" x2="{mx+mw-12}" y2="{ry-12}" stroke="{BORDER}" stroke-width="1"/>')
        e.append(text(mx+22, ry+16, it, TXT, 15, 400))
    e.append(text(mx+mw+18, my+24, ("12px rounded" if after else "5px square"), DIM, 13, 600))
    e.append(text(mx+mw+18, my+44, ("+ soft drop shadow" if after else "no shadow"), DIM, 13, 400))
    e.append(text(mx+mw+18, my+64, "(menus float)" if after else "(flat)", DIM, 12, 400))

    # --- text field with a selected-text highlight (the accent) --------------
    fx,fy,fw,fh = wx+22, my+mh+34, 320, 36
    e.append(f'<rect x="{fx}" y="{fy}" width="{fw}" height="{fh}" rx="{crx}" fill="{VIEW}" stroke="{BORDER}" stroke-width="1"/>')
    # "select me" highlighted with the accent (selection band)
    e.append(f'<rect x="{fx+12}" y="{fy+7}" width="84" height="{fh-14}" rx="3" fill="{accent}"/>')
    e.append(text(fx+18, fy+fh/2+5, "select me", WHITE, 14, 500))
    e.append(text(fx+104, fy+fh/2+5, " · the highlight is the accent", DIM, 14, 400))

    # caption
    e.append(text(wx+22, wy+wh-30, cap1, TXT, 14, 600))
    e.append(text(wx+22, wy+wh-12, cap2, DIM, 13, 400))
    return "\n".join(e)

svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<defs>
  <filter id="menulift" x="-60%" y="-60%" width="220%" height="220%">
    <feDropShadow dx="0" dy="8" stdDeviation="14" flood-color="#000000" flood-opacity="0.28"/>
  </filter>
  <filter id="winlift" x="-30%" y="-30%" width="160%" height="160%">
    <feDropShadow dx="0" dy="4" stdDeviation="12" flood-color="#000000" flood-opacity="0.18"/>
  </filter>
</defs>
<rect width="{W}" height="{H}" fill="{DESK}"/>
{panel(0, False)}
{panel(PW, True)}
<line x1="{PW}" y1="20" x2="{PW}" y2="{H-20}" stroke="{BORDER}" stroke-width="1" stroke-dasharray="4 6"/>
{text(W/2, H-18, "Aurora widget look — illustration from the actual token values (radii / elevation alphas / scheme RGBs), not a live Qt capture", DIM, 13, 400, "middle")}
</svg>'''

open("/tmp/aurora-before-after.svg","w").write(svg)
print("wrote /tmp/aurora-before-after.svg")

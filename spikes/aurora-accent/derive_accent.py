#!/usr/bin/env python3
"""derive_accent.py — derive the Aurora widget accent from the idle wallpaper hue.

Pure function of the sampled wallpaper hue. NO randomness. Re-runnable.

Idle source: the live wallpaper is the UE "Indigo Channel" (Style 14). Its locked
resting field-colour is the indigo-violet volumetric fog body:
    FOG_INSCATTER = LinearColor(0.020, 0.022, 0.060)   # indigo_channel_setup.py:87
This is the colour the desktop SITS IN when calm (fills the frame), so it is the
"resting signature". (The cyan focal core #6FC8DE is a transient highlight rake,
not the resting field — we sample the field, not the rake.)

The known-good accent (saturation + lightness anchor) is the charter violet:
    VIOLET = (124, 92, 196)   # white-text 4.81:1, focus-ring >=3:1

Derivation: keep the violet's S and L; rotate its HUE a BOUNDED fraction k toward
the wallpaper hue, then CLAMP the result into a violet arc [255,285] deg so a
green/amber wallpaper can never pull the accent out of violet identity.
"""
from __future__ import annotations
import colorsys

# ---- WCAG (identical math to integrations/aurora-theme/tools/check-contrast.py)
def _lin(c): c/=255.0; return c/12.92 if c<=0.03928 else ((c+0.055)/1.055)**2.4
def _lum(rgb):
    r,g,b=(_lin(x) for x in rgb); return 0.2126*r+0.7152*g+0.0722*b
def ratio(fg,bg):
    l1,l2=_lum(fg),_lum(bg); hi,lo=max(l1,l2),min(l1,l2); return (hi+0.05)/(lo+0.05)

# ---- sRGB <-> HSL helpers (hue in degrees) ----------------------------------
def rgb_to_hsl(rgb):
    r,g,b=[x/255.0 for x in rgb]
    h,l,s=colorsys.rgb_to_hls(r,g,b)
    return h*360.0, s, l
def hsl_to_rgb(h,s,l):
    r,g,b=colorsys.hls_to_rgb((h%360.0)/360.0, l, s)
    return tuple(round(x*255) for x in (r,g,b))

def lin_to_srgb8(lin):
    # UE LinearColor -> sRGB 0..255 (for reading the authored fog hue)
    def enc(c): return 12.92*c if c<=0.0031308 else 1.055*(c**(1/2.4))-0.055
    return tuple(round(min(1.0,max(0.0,enc(c)))*255) for c in lin)

# ---- inputs -----------------------------------------------------------------
FOG_INSCATTER_LIN = (0.020, 0.022, 0.060)     # indigo_channel_setup.py:87
VIOLET = (124, 92, 196)                        # charter accent (anchor)

# bounded-arc parameters
K = 0.35                  # blend factor: 35% of the way toward the wallpaper hue
ARC = (255.0, 285.0)      # violet clamp arc (degrees)

def hue_blend_clamped(violet_h, wall_h, k, arc):
    # shortest-path rotation violet_h -> wall_h, then take k of it
    d = (wall_h - violet_h + 540.0) % 360.0 - 180.0   # in (-180,180]
    h = violet_h + k * d
    lo, hi = arc
    return min(hi, max(lo, h))

def accent_meets_aa(h, s, l, need=4.5):
    return ratio((255,255,255), hsl_to_rgb(h,s,l)) >= need

def derive(name, wall_rgb, k=K, arc=ARC):
    vH,vS,vL = rgb_to_hsl(VIOLET)
    wH,wS,wL = rgb_to_hsl(wall_rgb)
    newH = hue_blend_clamped(vH, wH, k, arc)
    # Keep violet S; keep violet L as the TARGET, but enforce a luminance floor so
    # white-on-accent stays >=4.5 at the chosen hue. If the hue makes the violet L
    # too bright for AA, darken L just enough (deterministic bisection). This makes
    # the contrast guarantee hold across the WHOLE clamp arc, not only at fog hue.
    L = vL
    if not accent_meets_aa(newH, vS, L):
        lo, hi = 0.30, vL            # search darker
        for _ in range(40):
            mid=(lo+hi)/2
            if accent_meets_aa(newH, vS, mid): lo=mid
            else: hi=mid
        L = lo
    accent = hsl_to_rgb(newH, vS, L)
    return {"name":name,"wall_rgb":wall_rgb,"wall_hsl":(round(wH,1),round(wS,3),round(wL,3)),
            "violet_hsl":(round(vH,1),round(vS,3),round(vL,3)),
            "new_h":round(newH,2),"new_l":round(L,4),"accent":accent}

# ---- derive a family from an accent (Selection / Decoration / WM) ------------
def family(accent):
    aH,aS,aL = rgb_to_hsl(accent)
    # Selection BackgroundAlternate: a slightly DARKER accent band.
    # charter 108,78,172 vs 124,92,196 ~= L*0.875.
    sel_alt = hsl_to_rgb(aH, aS, aL*0.875)
    # Light-mode Decoration: focus = the accent; hover is DARKER + a touch more chroma
    # (charter light focus=124,92,196 -> hover=150,120,214 is actually a lighter tint,
    #  but on a LIGHT body the ring must clear 3:1, so we DARKEN toward the alt band:
    #  hover = focus L*0.875 keeps it == sel_alt-ish, guaranteeing >=3:1).
    light_focus = accent
    light_hover = hsl_to_rgb(aH, aS, aL*0.875)
    # Dark-mode Decoration: a LIGHT tint of the accent so it reads on dark bodies.
    # charter dark focus=199,168,250 (L~0.82), hover=160,130,220 (L~0.686).
    dark_focus = hsl_to_rgb(aH, min(1.0,aS*0.92), 0.82)
    dark_hover = hsl_to_rgb(aH, min(1.0,aS*0.78), 0.686)
    return {"sel_alt":sel_alt,"light_focus":light_focus,"light_hover":light_hover,
            "dark_focus":dark_focus,"dark_hover":dark_hover}

def report(d):
    a=d["accent"]; fam=family(a)
    # contrast checks
    white=(255,255,255)
    # dark-mode body backgrounds the focus ring sits on (View/Window BackgroundNormal):
    dark_view_bg=(36,33,46); dark_win_bg=(40,37,50)
    light_view_bg=(253,252,255); light_win_bg=(242,240,248)  # exact AuroraLight View/Window bodies
    print(f"\n=== {d['name']} ===")
    print(f"  wallpaper RGB {d['wall_rgb']}  HSL {d['wall_hsl']}")
    print(f"  violet anchor HSL {d['violet_hsl']}  -> new hue {d['new_h']} deg")
    print(f"  ACCENT (Selection:BackgroundNormal, both modes) = {a[0]},{a[1]},{a[2]}")
    print(f"  Selection:BackgroundAlternate                   = {fam['sel_alt'][0]},{fam['sel_alt'][1]},{fam['sel_alt'][2]}")
    print(f"  -- contrast --")
    print(f"  white-on-accent (selected text)        {ratio(white,a):.2f}:1  (need >=4.5)")
    print(f"  white-on-accentAlt (selected alt band) {ratio(white,fam['sel_alt']):.2f}:1  (need >=4.5)")
    print(f"  LIGHT mode:")
    print(f"    DecorationFocus (=accent)   {a[0]},{a[1]},{a[2]}   ring-on-viewBg {ratio(a,light_view_bg):.2f}:1 (need >=3)")
    print(f"    DecorationHover             {fam['light_hover'][0]},{fam['light_hover'][1]},{fam['light_hover'][2]}   hover-on-viewBg {ratio(fam['light_hover'],light_view_bg):.2f}:1")
    print(f"    WM activeBlend (=accent)    {a[0]},{a[1]},{a[2]}")
    print(f"  DARK mode:")
    print(f"    DecorationFocus (tint)      {fam['dark_focus'][0]},{fam['dark_focus'][1]},{fam['dark_focus'][2]}   ring-on-viewBg {ratio(fam['dark_focus'],dark_view_bg):.2f}:1 (need >=3)")
    print(f"    DecorationHover             {fam['dark_hover'][0]},{fam['dark_hover'][1]},{fam['dark_hover'][2]}   hover-on-viewBg {ratio(fam['dark_hover'],dark_win_bg):.2f}:1 (need >=3)")
    print(f"    WM activeBlend / ForegroundActive (=darkFocus) {fam['dark_focus'][0]},{fam['dark_focus'][1]},{fam['dark_focus'][2]}")

if __name__ == "__main__":
    fog_srgb = lin_to_srgb8(FOG_INSCATTER_LIN)
    print(f"FOG_INSCATTER linear {FOG_INSCATTER_LIN} -> sRGB8 {fog_srgb}  HSL {tuple(round(x,3) for x in rgb_to_hsl(fog_srgb))}")
    print(f"VIOLET anchor {VIOLET} HSL {tuple(round(x,3) for x in rgb_to_hsl(VIOLET))}")
    # primary: the actual idle source
    report(derive("Indigo Channel fog (LIVE idle source)", fog_srgb))
    # sanity / robustness probes (the clamp must hold):
    report(derive("PROBE: green wallpaper (120deg) — must stay violet", (60,180,75)))
    report(derive("PROBE: amber wallpaper (40deg) — must stay violet", (230,170,40)))
    report(derive("PROBE: already-violet (270deg) — accent ~unchanged", (140,90,210)))

#!/usr/bin/env python3
# g7_contrast.py — ADR-0019 G7 acceptance gate (HARD GATE before Phase 2).
#
# Warm-state WCAG 2.2 contrast check. When the wallpaper/tray blooms WARM (a request
# needs the user's OK), the "needs your OK" text and the Allow/Cancel controls must
# still meet WCAG AA contrast MEASURED AGAINST THE WARM-BLOOMED FIELD, on BOTH the
# Hills and Flow wallpapers — at the warm BREATH PEAK (the worst-contrast frame), at
# low brightness, against the worst (highest-luminance) pixel in each element's bbox.
#
# This is a MEASUREMENT + CI GATE, not a redesign. It prints a PASS/FAIL table and
# exits non-zero if any required surface fails, so it can gate Phase 2. Where a surface
# fails (the design doc records the tray #ff9957 warm-on-warm text does), it prints a
# concrete, revertible FALLBACK recommendation with the ratio that fix would achieve.
#
# Standalone, stdlib-only, deterministic. No numpy / Pillow.
#
# Sources (read-only) the colors are taken from:
#   - docs/design/reviewable-request-queue.md  (the G7 section: measured ratios + fixes)
#   - integrations/design/instrument-tokens.md  (the canonical instrument tokens)
#   - spikes/keyhole/contents/ui/InstrumentPalette.qml  (tray warm/text/blue tokens)
#   - apps/dreaming/lucid/lucid_web.py  &  web/src/theme.css  (web panel --inst-* block)
#   - spikes/hills-reactive/aurora.frag  (the Hills/Flow warm-bloom compositing, lines
#       709-713 Hills, 956-960 Flow — vec3(1.00,0.60,0.34) bloom riding the bright field)
#
# SPDX-License-Identifier: MIT
import sys

# ---------------------------------------------------------------------------
# WCAG 2.2 contrast — the formula, implemented exactly (SC 1.4.3 / 1.4.11).
# ---------------------------------------------------------------------------

def _channel_to_linear(c8: float) -> float:
    """sRGB gamma-expand one 0..1 channel to linear light (WCAG / sRGB spec)."""
    cs = c8 / 255.0 if c8 > 1.0 else c8  # accept 0..255 or already-normalised 0..1
    return cs / 12.92 if cs <= 0.03928 else ((cs + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb) -> float:
    """WCAG relative luminance of an sRGB colour.

    rgb may be a '#rrggbb' string or a 3-tuple of 0..255 (or 0..1) channels.
    L = 0.2126*R + 0.7152*G + 0.0722*B over linearised channels.
    """
    r, g, b = _coerce_rgb(rgb)
    return (0.2126 * _channel_to_linear(r)
            + 0.7152 * _channel_to_linear(g)
            + 0.0722 * _channel_to_linear(b))


def contrast_ratio(rgb1, rgb2) -> float:
    """WCAG contrast ratio (L1+0.05)/(L2+0.05), L1 the lighter of the two."""
    l1 = relative_luminance(rgb1)
    l2 = relative_luminance(rgb2)
    hi, lo = (l1, l2) if l1 >= l2 else (l2, l1)
    return (hi + 0.05) / (lo + 0.05)


def _coerce_rgb(rgb):
    """Accept '#rrggbb', '#rgb', or an (r,g,b) tuple in 0..255 -> (r,g,b) 0..255 floats."""
    if isinstance(rgb, str):
        h = rgb.lstrip('#')
        if len(h) == 3:
            h = ''.join(ch * 2 for ch in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return tuple(float(c) for c in rgb)


# ---------------------------------------------------------------------------
# The real surfaces to check, with their real colours.
#
# Two background classes (split by surface exposure, verified against code in the
# design doc):
#
#  1. The LUCID WEB PANEL does NOT composite the live wallpaper — its backdrop-filter
#     blurs its OWN opaque navy body gradient (theme.css / lucid_web.py:202-248). So web
#     tokens are measured against the panel's own glass card / navy body, NOT the bloom.
#
#  2. The KEYHOLE TRAY genuinely floats over the warm-bloomed wallpaper. Tray text/glyph
#     therefore composites against the WARM-BLOOMED FIELD on Hills AND Flow.
#
# ---- warm-bloomed field colours (Hills / Flow) ----------------------------
# The shader (spikes/hills-reactive/aurora.frag) layers a warm bloom
#   vec3(1.00, 0.60, 0.34) * lowGlow * uAgentWarm * breath * <0.42 Hills | 0.75 Flow>
# onto the bright base field, at gate conditions uAgentState=2, uAgentWarm=0.9, uDark=1,
# breath peak sin(t*0.62)=+1, 20%/low brightness, worst (highest-luminance) bbox pixel.
#
# The two FIELD_* values below are calibrated so that the warm tray token #ff9957 over
# them reproduces the design doc's CODE-VERIFIED measured ratios (Hills 3.92:1, Flow
# 3.07:1, docs/design/reviewable-request-queue.md:496-498) to 2 d.p. — i.e. the gate
# encodes the verified rendered-frame measurement, not a re-derived shader replay (a
# naive shader replay of the additive bloom over-brightens the field; the measured
# numbers are the authority). They are deliberately neutral greys at the measured
# luminance: contrast depends only on luminance, and using a grey keeps the calibration
# transparent and avoids over-claiming a precise hue for the composited bloom.
#
# >>> ASSUMPTION FLAGGED FOR CONFIRMATION <<<  (see REPORT):
#   FIELD_WARM_HILLS / FIELD_WARM_FLOW are luminance-calibrated to the doc's measured
#   3.92 / 3.07 ratios against #ff9957, NOT sampled pixels exported from the live shader.
#   If the wallpaper grade changes, re-measure against an exported frame and update these.
FIELD_WARM_HILLS = "#4e4e4e"   # L≈0.0769 -> #ff9957 over it = 3.94:1 (doc Hills 3.92; 8-bit-grey nearest)
FIELD_WARM_FLOW  = "#5e5e5e"   # L≈0.1120 -> #ff9957 over it = 3.07:1 (doc Flow, worst)

# ---- the panel's own (wallpaper-independent) backgrounds -------------------
# The lucid web panel's effective body/card background is BRIGHTER than the raw
# --inst-base #12141c (L=0.0071): the backdrop-filter blurs the OWN navy body GRADIENT
# (base -> deep -> horizon, theme.css / lucid_web.py:202-248) and the controls sit on
# that lifted mid, so the doc's measured ratios — Allow 6.06, Cancel 5.32, body 12.4 —
# all resolve to ONE effective background luminance (L≈0.0197, a grey ~#262626). That
# self-consistency across three independent tokens is the cross-check that the encoded
# background is right. The 46%-glass card the seed caption sits on resolves to the SAME
# effective luminance (its 3.82:1 for #7a8090 also lands at L≈0.0196) — so one constant
# serves both. (We keep two NAMES for clarity of which surface each row models.)
NAVY_BODY        = "#262626"   # effective navy body/gradient mid the web controls sit on
GLASS_CARD_EFF   = "#262626"   # effective 46%-glass card over navy (seed caption/border)
INST_BASE        = "#12141c"   # --inst-base: deepest opaque navy (the F2 opaque-chip floor)

# ---- tokens (from instrument-tokens.md / InstrumentPalette.qml / theme.css) -
WARM_TOKEN   = "#ff9957"   # --inst-warm: RESERVED needs-you GLOW/halo hue (still the ring fill)
SKIN_TEXT    = "#e6e9f0"   # --inst-text / skin.text: primary text (the F2 fallback fill)
# G7/ADR-0019 F2a APPLIED in InstrumentPalette.qml: warmText(dark) #ff9957 -> #e6e9f0; the tray
# label+glyph FILL is now skin.text, warmth lives only in the aurora ring/halo (WARM_TOKEN).
TRAY_LABEL_FG = SKIN_TEXT  # de-coupled needs-you label+glyph fill (was WARM_TOKEN #ff9957)
# G7/ADR-0019 F1b APPLIED in web/src/theme.css: --inst-label #7a8090 -> #878c9b (4.53:1 on glass)
INST_LABEL   = "#878c9b"   # --inst-label: quiet labels / captions (web seed caption) — F1b lightened
INST_MUTED   = "#8a90a0"   # --inst-muted: 4.72:1 on glass (the F1 demote target)
WEB_ALLOW    = "#7aa2ff"   # web Allow control (--inst-blue)
WEB_CANCEL   = "#ec7676"   # web Cancel control (--st-red)
BODY_LABEL   = "#e6e9f0"   # web body "needs your OK" label (--inst-text)

# AA thresholds (WCAG 2.2):
AA_TEXT = 4.5   # SC 1.4.3 normal (incl. bold-but-not-large) text
AA_UI   = 3.0   # SC 1.4.11 non-text: glyph / border / focus / large

# Each row: (label, fg, bg, wallpaper, min_ratio, doc_measured_or_None)
#   doc_measured: the design-doc measured ratio, used to assert the gate stays faithful
#                 to the verified numbers (None where we compute from real flat tokens).
SURFACES = [
    # ---- the KEYHOLE TRAY over the warm-bloomed wallpaper (the real exposure) ----
    ('tray "needs your OK" label',  TRAY_LABEL_FG, FIELD_WARM_HILLS, "Hills", AA_TEXT, None),
    ('tray "needs your OK" label',  TRAY_LABEL_FG, FIELD_WARM_FLOW,  "Flow",  AA_TEXT, None),
    ('tray needs-you glyph (mark)', TRAY_LABEL_FG, FIELD_WARM_HILLS, "Hills", AA_UI,   None),
    ('tray needs-you glyph (mark)', TRAY_LABEL_FG, FIELD_WARM_FLOW,  "Flow",  AA_UI,   None),

    # ---- the LUCID WEB PANEL (own opaque navy body / glass card, NOT the bloom) ----
    ('web "needs your OK" body label', BODY_LABEL, NAVY_BODY,      "Hills", AA_TEXT, 12.4),
    ('web "needs your OK" body label', BODY_LABEL, NAVY_BODY,      "Flow",  AA_TEXT, 12.4),
    ('web Allow control',              WEB_ALLOW,  NAVY_BODY,      "Hills", AA_TEXT, 6.06),
    ('web Allow control',              WEB_ALLOW,  NAVY_BODY,      "Flow",  AA_TEXT, 6.06),
    ('web Cancel control',             WEB_CANCEL, NAVY_BODY,      "Hills", AA_TEXT, 5.32),
    ('web Cancel control',             WEB_CANCEL, NAVY_BODY,      "Flow",  AA_TEXT, 5.32),
    # the seed thumbnail's caption/selection border (image itself is exempt; its
    # adjacent caption + border are NOT) sit on the 46%-glass card -> --inst-label.
    ('seed thumbnail caption (label)', INST_LABEL, GLASS_CARD_EFF, "Hills", AA_TEXT, None),
    ('seed thumbnail caption (label)', INST_LABEL, GLASS_CARD_EFF, "Flow",  AA_TEXT, None),
    ('seed thumbnail selection border', INST_LABEL, GLASS_CARD_EFF, "Hills", AA_UI,  None),
    ('seed thumbnail selection border', INST_LABEL, GLASS_CARD_EFF, "Flow",  AA_UI,  None),
]


# ---------------------------------------------------------------------------
# Fallback recommendations for the known failures (design-doc fallback ladder).
# Keyed on (label-substring, threshold-class) so each failing surface prints a
# concrete, revertible fix WITH the ratio that fix would achieve.
# ---------------------------------------------------------------------------

def _fallbacks_for(label, fg, bg, threshold):
    """Return a list of (description, achieved_ratio_or_None) fixes for a failure."""
    out = []
    is_tray = label.startswith('tray')
    is_caption = label.startswith('seed thumbnail')
    if is_tray:
        # F2 (the Blocker): de-couple warm from the text FILL. Keep label+glyph at
        # skin.text (#e6e9f0); express "needs you" via the aurora ring/halo warmth +
        # bold weight, NOT the letterforms.
        r = contrast_ratio(SKIN_TEXT, bg)
        out.append((f"F2a — de-couple warm from the text FILL: render label+glyph at "
                    f"skin.text {SKIN_TEXT} (warmth moves to the aurora ring/halo + bold "
                    f"weight, not the letterforms)", r))
        # F2.2: opaque instrument-register chip behind tray text so it never composites
        # against the wallpaper — measured against the navy body instead of the bloom.
        r2 = contrast_ratio(WARM_TOKEN, INST_BASE)
        out.append((f"F2b — guarantee an opaque instrument chip ({INST_BASE}) behind the "
                    f"tray text so it never composites against the wallpaper (then even "
                    f"the bright warm {WARM_TOKEN} is legible)", r2))
        # reduced-transparency escape hatch (opaque tray chip forced).
        out.append(("F2c — prefers-reduced-transparency: force the opaque tray chip "
                    "(same as F2b) — a guaranteed-AA escape hatch", r2))
    elif is_caption:
        # F1: lighten web --inst-label to clear 4.5:1 on the glass card, OR demote info
        # usages to --inst-muted (already 4.72:1 on glass).
        r_muted = contrast_ratio(INST_MUTED, bg)
        out.append((f"F1a — demote the caption/border to --inst-muted {INST_MUTED} "
                    f"(already clears AA on the glass card)", r_muted))
        # Compute the lightest grey-shift of --inst-label that just clears the threshold.
        fix_hex, fix_ratio = _lighten_to_threshold(fg, bg, threshold)
        out.append((f"F1b — lighten --inst-label from {INST_LABEL} to ~{fix_hex} so it "
                    f"clears {threshold}:1 on the glass card", fix_ratio))
    else:
        # generic: lighten the fg until it clears.
        fix_hex, fix_ratio = _lighten_to_threshold(fg, bg, threshold)
        out.append((f"lighten {fg} to ~{fix_hex} to clear {threshold}:1", fix_ratio))
    return out


def _lighten_to_threshold(fg, bg, threshold):
    """Find the smallest uniform lightening of fg (toward white) that clears threshold
    against bg. Deterministic search in 1/255 steps. Returns (hex, achieved_ratio)."""
    r0, g0, b0 = _coerce_rgb(fg)
    for step in range(0, 256):
        t = step / 255.0
        r = r0 + (255 - r0) * t
        g = g0 + (255 - g0) * t
        b = b0 + (255 - b0) * t
        cand = (r, g, b)
        if contrast_ratio(cand, bg) >= threshold:
            hexcode = '#%02x%02x%02x' % (round(r), round(g), round(b))
            return hexcode, contrast_ratio(cand, bg)
    return '#ffffff', contrast_ratio((255, 255, 255), bg)


# ---------------------------------------------------------------------------
# main(): print the PASS/FAIL table, exit non-zero on any required failure.
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    print("=" * 86)
    print("ADR-0019 G7 — warm-state WCAG 2.2 contrast gate (HARD GATE before Phase 2)")
    print("Measured at the warm BREATH PEAK against the warm-bloomed field, low brightness,")
    print("worst (highest-luminance) bbox pixel. Hills + Flow.  Thresholds: 4.5 text / 3.0 UI.")
    print("=" * 86)
    hdr = f"{'surface':<34}{'wall':<6}{'fg':>9}{'bg':>9}{'ratio':>8}{'min':>6}  {'result'}"
    print(hdr)
    print("-" * 86)

    failures = []
    drift = []  # rows whose computed ratio disagrees with the doc-measured value
    for label, fg, bg, wall, minr, doc in SURFACES:
        ratio = contrast_ratio(fg, bg)
        ok = ratio >= minr
        mark = "PASS" if ok else "FAIL"
        print(f"{label:<34}{wall:<6}{fg:>9}{bg:>9}{ratio:>8.2f}{minr:>6.1f}  {mark}")
        if not ok:
            failures.append((label, fg, bg, wall, minr, ratio))
        # 0.12 tolerance: the encoded backgrounds are neutral 8-bit greys, which can't
        # land on every doc-measured ratio exactly (the doc values are themselves rounded
        # from a real frame). A larger gap means the encoded field/glass colour has
        # genuinely drifted from the measurement and must be re-checked.
        if doc is not None and abs(ratio - doc) > 0.12:
            drift.append((label, wall, ratio, doc))

    print("-" * 86)
    print(f"{len(SURFACES)} surfaces checked  ·  "
          f"{len(SURFACES) - len(failures)} PASS  ·  {len(failures)} FAIL")

    if drift:
        print()
        print("!! CALIBRATION DRIFT — computed ratio diverged from the design-doc measured")
        print("   value (the encoded field/glass colours need re-checking against the doc):")
        for label, wall, ratio, doc in drift:
            print(f"   - {label} [{wall}]: computed {ratio:.2f} vs doc {doc:.2f}")

    if failures:
        print()
        print("FAILURES — concrete, revertible fallback recommendations (design-doc ladder):")
        seen = set()
        for label, fg, bg, wall, minr, ratio in failures:
            key = (label, bg)
            if key in seen:
                continue
            seen.add(key)
            print()
            print(f"  X {label}  [{wall} field {bg}]  =  {ratio:.2f}:1  (needs {minr:.1f}:1)")
            for desc, achieved in _fallbacks_for(label, fg, bg, minr):
                if achieved is None:
                    print(f"      -> {desc}")
                else:
                    verdict = "clears AA" if achieved >= minr else "still short"
                    print(f"      -> {desc}")
                    print(f"         achieves {achieved:.2f}:1  ({verdict})")
        print()
        print("G7 GATE: FAIL — must pass on BOTH Hills and Flow before Phase 2 ships.")
        return 1

    print()
    print("G7 GATE: PASS — all required surfaces meet WCAG AA on both Hills and Flow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#version 440
//
// porthole.frag — the keyhole glyph's LIVING aurora background (ADR-0012 §7 amend).
//
// PRISM register: a calm, dark INSTRUMENT centre ringed by an iridescent
// chromatic-dispersion corona — the small sibling of com.nimbus.aurora's Prism
// style (uStyle 7: prismCorona() + the r∓ca per-channel split that fringes white
// light into a rainbow). Re-paletted to the deep-navy instrument register and kept
// CALM in the centre so the light glyph ink stays AA-legible; the iridescence lives
// out in the rim, where the glyph isn't. ONE shader drives BOTH keyhole surfaces
// (the tray glyph + the popup header), so they move together — exactly as the
// earlier Flow porthole did.
//
// Reactivity mirrors the wallpaper's grammar (the same live floats it reads):
//   uBusy → faster corona spin + wider dispersion + a touch brighter
//   uWarm → reserved dawn-glow LOW in the frame (needs_you)
//   uSnag → desaturate + dim (NEVER red)   uGray → unknown: cold, colourless ghost
//
// The square ShaderEffect renders as a PORTHOLE DISC: an anti-aliased circular alpha
// mask is computed here (transparent corners), so the QML needs no mask item and no
// QtQuick.Effects / Qt5Compat import — dependency-light, like the rest of the keyhole.
//
// Uniforms map by NAME to ShaderEffect properties; qt_Matrix/qt_Opacity are filled by
// Qt's default vertex pipeline. Compile (matches the wallpaper qsb's --qt6 target set):
//   /usr/lib/qt6/bin/qsb --qt6 -o porthole.frag.qsb porthole.frag
//
// SPDX-License-Identifier: GPL-3.0-or-later   (derived from nimbus aurora's aurora.frag)
//
layout(location = 0) in  vec2 qt_TexCoord0;   // 0..1 across the square (y DOWN)
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4  qt_Matrix;      // default vertex shader
    float qt_Opacity;     // QtQuick fade
    float iTime;          // seconds, ever-increasing (QML advances it; frozen = still frame)
    vec2  iResolution;    // porthole size in px (square)
    float uBusy;          // 0..1 working intensity  (faster spin + wider dispersion + brighter)
    float uWarm;          // 0..1 needs_you           (warm dawn glow, low in frame)
    float uSnag;          // 0..1 snag                (desaturate + dim — never red)
    float uIntensity;     // overall vividness (the porthole's "energy"; idle calm, working lifts)
    float uGray;          // 0..1 unknown             (cold, colourless, de-energized ghost)
    float uMusic;         // 0..~1.5 SECONDARY beat-shimmer (level+beat), PRE-GATED by the
                          // wallpaper's MusicReact host-side; 0 = no audio influence. Only
                          // ever touches the OUTER streak tips, never the state-bearing core.
    // The live nimbus-aurora WALLPAPER ramp (5 stops, dark→bright), injected by the host
    // from the wallpaper config (Theme preset or Custom Color0..4) so the porthole wears
    // the SAME palette the wallpaper paints. All-zero (unset) → a calm Big Sur default.
    vec4  uC0;
    vec4  uC1;
    vec4  uC2;
    vec4  uC3;
    vec4  uC4;
};

// ---- value noise + fbm (verbatim shape from aurora.frag / aurora-web.html) ----
float hash(vec2 p){ p = fract(p * vec2(123.34, 345.45)); p += dot(p, p + 34.345); return fract(p.x * p.y); }
float vnoise(vec2 p){
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    float a = hash(i), b = hash(i + vec2(1.0, 0.0)), c = hash(i + vec2(0.0, 1.0)), d = hash(i + vec2(1.0, 1.0));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}
float fbm(vec2 p){ float v = 0.0, a = 0.55; mat2 r = mat2(0.80, 0.60, -0.60, 0.80);
    for (int i = 0; i < 5; i++) { v += a * vnoise(p); p = r * p * 2.02 + 11.3; a *= 0.5; } return v; }

vec3 ramp(float t, vec3 c0, vec3 c1, vec3 c2, vec3 c3, vec3 c4){
    t = clamp(t, 0.0, 1.0) * 4.0;
    if (t < 1.0) return mix(c0, c1, t);
    if (t < 2.0) return mix(c1, c2, t - 1.0);
    if (t < 3.0) return mix(c2, c3, t - 2.0);
    return mix(c3, c4, t - 3.0);
}

// Prism corona intensity at radius `rr` (the shape from aurora.frag's prismCorona,
// softened for the small disc): a clear calm CENTRE, a bright iridescent RING at
// `reach`, and streaks RADIATING outward & fading. `sharp` sets the ring width so it
// doesn't alias on a ~40px porthole. Sampling at r∓ca per channel splits the rainbow.
// Base stays in [0,1] (smoothstep-free product of exp × spike), so no pow-of-neg NaN.
float prismCorona(float rr, float reach, float spike, float sharp){
    float dr  = rr - reach;                          // <0 inside the clean centre, >0 outside
    float rim = exp(-dr * dr * sharp);               // thin bright iridescent RING at the rim
    float rad = step(0.0, dr) * exp(-dr * 4.2);      // prismatic streaks RADIATING outward, fading
    return rim * (0.45 + 0.55 * spike) + rad * spike * 0.95;
}

void main(){
    // y-up uv to match the GL-convention wallpaper shader (qt_TexCoord0 is y-down)
    vec2 uv = vec2(qt_TexCoord0.x, 1.0 - qt_TexCoord0.y);
    vec2 p  = uv - 0.5;                          // porthole is square -> aspect 1
    float r   = length(p);                       // 0 centre .. 0.5 edge .. ~0.707 corner
    float ang = atan(p.y, p.x);

    // The ramp IS the active wallpaper's 5 stops (dark→bright), so the corona wears the
    // SAME palette as the nimbus-aurora wallpaper and tracks whatever theme is set. The
    // CENTRE stays the dark low stops (c0/c1) so the light glyph ink stays AA-legible;
    // the palette rises through c2→c4 where the corona band samples. ~0 → Big Sur default.
    vec3 c0 = uC0.rgb, c1 = uC1.rgb, c2 = uC2.rgb, c3 = uC3.rgb, c4 = uC4.rgb;
    if (dot(c0 + c1 + c2 + c3 + c4, vec3(1.0)) < 0.02) {
        c0 = vec3(0.051, 0.059, 0.161); c1 = vec3(0.110, 0.180, 0.451);
        c2 = vec3(0.271, 0.322, 0.722); c3 = vec3(0.561, 0.361, 0.722);
        c4 = vec3(0.980, 0.549, 0.451);                // Big Sur (matches the wallpaper default)
    }

    float t    = iTime * (0.6 + 0.9 * uBusy);    // calm drift; busy speeds the spin
    float spin = 0.18 * t;                       // the corona turns slowly

    // procedural ridged streaks sampled on a CIRCLE (cos/sin of the angle), so they're
    // periodic with NO seam at atan's ±π branch cut; `spin` rotates them → the corona
    // turns, the radial term shimmers the streaks as they march outward.
    float sn    = fbm(vec2(cos(ang + spin), sin(ang + spin)) * 3.2 + vec2(0.0, r * 1.4));
    float spike = pow(1.0 - smoothstep(0.0, 0.55, abs(sn * 2.0 - 1.0)), 2.0);

    // the iridescent ring sits inside the disc rim; busy nudges its reach out a touch.
    float reach = 0.34 + 0.03 * uBusy;
    // dispersion: wide enough to actually READ as a rainbow on a tiny disc; busy splits harder.
    float ca    = 0.045 + 0.045 * uBusy;
    float sharp = 230.0;                         // ring width ~2.5px on a ~40px porthole

    // CHROMATIC DISPERSION: corona sampled at r∓ca per channel → R/G/B separate into a
    // rainbow fringe on every streak edge.
    float iR = prismCorona(r - ca, reach, spike, sharp);
    float iG = prismCorona(r,      reach, spike, sharp);
    float iB = prismCorona(r + ca, reach, spike, sharp);
    float corona = (iR + iG + iB) * (1.0 / 3.0);

    // BASE corona colour from the instrument ramp — a slow radial sweep through the
    // palette (theme-tinted, never a hardcoded ROYGBIV), lifted by a subtle cosine
    // iridescence so the rim shimmers through cool hues even at icon scale.
    float band = abs(fract(r * 1.4 - 0.05 * t + 0.10 * spike) * 2.0 - 1.0);
    vec3  spec = ramp(0.30 + 0.55 * band, c0, c1, c2, c3, c4);
    vec3  irid = 0.5 + 0.5 * cos(6.2831853 * (vec3(0.0, 0.33, 0.66) + r * 1.6 + ang * 0.15 + 0.05 * t));
    spec = mix(spec, spec * (0.72 + 0.56 * irid), 0.30);   // accent hue leads; a light iridescent shimmer rides it

    // CHROMATIC ABERRATION: modulate the theme colour by the per-channel corona
    // intensities, so streak/rim edges fringe in a TINT of the theme's own colour.
    vec3 prism = spec * vec3(iR, iG, iB) + spec * corona * 0.30;

    // the dark field the corona floats on (the low palette stops); calm centre. Darkened
    // a touch so the glyph stays AA-legible whatever theme supplies the low stops.
    vec3 field = mix(c0, c1, 0.30) * 0.78;
    vec3 col   = field + prism * 1.5;            // the wallpaper stops are already bright
    col *= (1.0 + 0.22 * uBusy);                 // busy: a touch brighter

    // music (SECONDARY channel): a subtle beat-shimmer on the OUTER streak tips only, so it
    // can never be mistaken for the core-corona cues that encode fleet state. Rides the
    // existing streak field (spike) and the crest hue; pre-gated by MusicReact (0 → silent).
    float outer = smoothstep(reach, reach + 0.14, r);   // strictly beyond the rim
    col += c4 * outer * spike * uMusic * 0.5;

    // warm: the reserved dawn-glow low in the frame (needs_you) — rides the corona too
    vec3  warmHue = vec3(1.0, 0.60, 0.34);
    float low  = 1.0 - smoothstep(0.0, 0.60, uv.y);                // strongest at the bottom
    float glow = low * uWarm * (0.45 + 0.55 * corona);
    col = mix(col, warmHue, 0.40 * glow);

    // snag: pull toward luma (desaturate) and dim — calm, never red
    float luma = dot(col, vec3(0.30, 0.59, 0.11));
    col = mix(col, vec3(luma), 0.60 * uSnag);
    col *= (1.0 - 0.32 * uSnag);

    // unknown: a cold, colourless, de-energized ghost (matches the QML _desat path)
    float gluma = dot(col, vec3(0.30, 0.59, 0.11));
    col = mix(col, vec3(gluma), 0.88 * uGray);
    col *= (1.0 - 0.42 * uGray);

    col *= uIntensity;

    // circular porthole mask: opaque inside the inscribed circle, transparent corners.
    // ~1.5px feather from iResolution (no fwidth needed -> safe on every qsb target).
    float aa   = 1.5 / max(iResolution.y, 1.0);
    float mask = 1.0 - smoothstep(0.5 - aa, 0.5, r);

    fragColor = vec4(col, 1.0) * (mask * qt_Opacity);   // premultiplied alpha
}

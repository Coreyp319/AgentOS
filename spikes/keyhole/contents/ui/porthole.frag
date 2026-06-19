#version 440
//
// porthole.frag — the keyhole glyph's LIVING aurora background (ADR-0012 §7 amend).
//
// The SAME "Flow" look as the status-panel backdrop (spikes/ambient-backdrop/
// aurora-web.html) and faithful to com.nimbus.aurora's aurora.frag (hash/vnoise/
// fbm/fbm3 + the iq domain-warp `flowField`, 5-stop `ramp`), re-paletted to the
// deep-navy INSTRUMENT register (integrations/design/instrument-tokens.md) and kept
// dark/low-contrast so the light glyph ink stays AA-legible over the brightest crest.
// Driving the QML glyph and the HTML panel from one shader keeps both surfaces' aurora
// identical — they move together.
//
// Reactivity mirrors the wallpaper's grammar (the same live floats the wallpaper reads):
//   uBusy → faster flow + a touch brighter   uWarm → slow dawn glow LOW in frame (needs_you)
//   uSnag → thicker/dimmer, desaturated (NEVER red)   uGray → unknown: cold colourless ghost
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
    float uBusy;          // 0..1 working intensity  (faster flow + a touch brighter)
    float uWarm;          // 0..1 needs_you           (warm dawn glow, low in frame)
    float uSnag;          // 0..1 snag                (desaturate + dim — never red)
    float uIntensity;     // overall vividness (the porthole's "energy"; idle calm, working lifts)
    float uGray;          // 0..1 unknown             (cold, colourless, de-energized ghost)
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
float fbm3(vec2 p){ float v = 0.0, a = 0.6; mat2 r = mat2(0.80, 0.60, -0.60, 0.80);
    for (int i = 0; i < 3; i++) { v += a * vnoise(p); p = r * p * 2.02 + 11.3; a *= 0.5; } return v; }

vec3 ramp(float t, vec3 c0, vec3 c1, vec3 c2, vec3 c3, vec3 c4){
    t = clamp(t, 0.0, 1.0) * 4.0;
    if (t < 1.0) return mix(c0, c1, t);
    if (t < 2.0) return mix(c1, c2, t - 1.0);
    if (t < 3.0) return mix(c2, c3, t - 2.0);
    return mix(c3, c4, t - 3.0);
}

// nimbus aurora flowField (advected domain-warp), verbatim shape.
float flowField(vec2 base, float t){
    vec2 q = vec2(fbm3(base + vec2(0.0, 0.12 * t)), fbm3(base + vec2(5.2, 1.7 - 0.10 * t)));
    vec2 r = vec2(fbm3(base + 1.8 * q + vec2(1.7, 9.2 + 0.08 * t)), fbm3(base + 1.8 * q + vec2(8.3, 2.8 - 0.07 * t)));
    return fbm(base + 2.2 * r);
}

void main(){
    // y-up uv to match the GL-convention web shader (qt_TexCoord0 is y-down)
    vec2 uv = vec2(qt_TexCoord0.x, 1.0 - qt_TexCoord0.y);
    vec2 p  = uv - 0.5;                          // porthole is square -> aspect 1

    // Instrument deep-navy register (verbatim from aurora-web.html, uDark branch):
    // deliberately deep + muted so the light glyph ink stays legible over the crest.
    vec3 c0 = vec3(0.026, 0.034, 0.052);   // deepest — near --inst-base
    vec3 c1 = vec3(0.042, 0.064, 0.105);   // deep blue
    vec3 c2 = vec3(0.064, 0.104, 0.168);   // cool blue
    vec3 c3 = vec3(0.092, 0.156, 0.232);   // muted teal
    vec3 c4 = vec3(0.150, 0.245, 0.330);   // soft crest (no bright cyan)

    float t = iTime * 0.6 * (0.6 + 1.0 * uBusy);   // calm but visible drift; busy speeds it
    vec2  base = p * 2.8 + vec2(0.12 * t, -0.06 * t);
    float f = flowField(base, t);

    float field = smoothstep(0.28, 0.82, f);
    vec3  col = ramp(field, c0, c1, c2, c3, c4);
    col *= (1.0 + 0.22 * uBusy);                   // busy: a touch brighter

    // snag: pull toward luma (desaturate) and dim — calm, never red
    float luma = dot(col, vec3(0.30, 0.59, 0.11));
    col = mix(col, vec3(luma), 0.60 * uSnag);
    col *= (1.0 - 0.32 * uSnag);

    // warm: the reserved dawn-glow, low in the frame (needs_you)
    vec3  warmHue = vec3(1.0, 0.60, 0.34);
    float low  = 1.0 - smoothstep(0.0, 0.60, uv.y);                 // strongest at the bottom
    float glow = low * uWarm * (0.45 + 0.35 * flowField(base * 0.6, t * 0.5));
    col = mix(col, warmHue, 0.40 * glow);

    // unknown: a cold, colourless, de-energized ghost (matches the QML _desat path)
    float gluma = dot(col, vec3(0.30, 0.59, 0.11));
    col = mix(col, vec3(gluma), 0.88 * uGray);
    col *= (1.0 - 0.42 * uGray);

    col *= uIntensity;

    // circular porthole mask: opaque inside the inscribed circle, transparent corners.
    // ~1.5px feather from iResolution (no fwidth needed -> safe on every qsb target).
    float rr   = length(p);                        // 0 centre .. 0.5 edge .. ~0.707 corner
    float aa   = 1.5 / max(iResolution.y, 1.0);
    float mask = 1.0 - smoothstep(0.5 - aa, 0.5, rr);

    fragColor = vec4(col, 1.0) * (mask * qt_Opacity);   // premultiplied alpha
}

#version 440
//
// Nimbus Aurora — cursor-reactive animated background.
// Qt 6 ShaderEffect fragment shader (Vulkan-style GLSL; compile with `qsb`).
//
// A domain-warped flowing gradient in the Big Sur / WhiteSur palette. Drifts on
// its own over time; a warm light blooms under the pointer and gently pushes the
// flow aside (the "interactive" part). Members of the uniform block below map by
// NAME to properties on the QML ShaderEffect.
//
// Window-reactive hook (v2): add `uWin[N]` rects + counts here and displace/glow
// the field around them exactly like the cursor bloom does. Left out of v1.
//
layout(location = 0) in  vec2 qt_TexCoord0;   // 0..1 across the wallpaper
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4  qt_Matrix;        // used by the default vertex shader
    float qt_Opacity;       // fade applied by QtQuick
    float iTime;            // seconds, ever-increasing
    vec2  iResolution;      // wallpaper size in px
    vec2  iMouse;           // pointer, normalised 0..1 (y already flipped in QML)
    float iMouseActive;     // 1 while the pointer is over us, eased to 0 when it leaves
    float uSpeed;           // motion multiplier (config)
    float uInteractivity;   // cursor influence 0..1 (config)
    float uDark;            // 1 = dark variant, 0 = light (follows the colour scheme)
    float uIntensity;       // colour vividness; 1 = as-designed, <1 muted, >1 punchy
    int   uTheme;           // 0 Big Sur·1 Monterey·2 Graphite·3 Sunset·4 Nord·5 Laserwave·6 Vaporwave·7 Cyberpunk·8 Outrun·9 Custom
    int   uStyle;           // base look: 0 Flow·1 Hills·2 Silk curtains·3 Caustics·4 Ink in water·5 Laserwave·6 Vaporwave (marble colonnade)·7 Cyberpunk (neon datascape)
    // custom palette (used when uTheme == 5); .rgb of each, low → high stop
    vec4  uColor0;
    vec4  uColor1;
    vec4  uColor2;
    vec4  uColor3;
    vec4  uColor4;
    // --- window reactivity (v2) -------------------------------------------
    // Up to 6 window rects (x,y,w,h) normalised 0..1 to THIS wallpaper's screen
    // (y down, matching iMouse). Individual vec4s — ShaderEffect array uniforms
    // are unreliable. The moving window also rides in uActiveWin/uActiveVel.
    vec4  uWin0;
    vec4  uWin1;
    vec4  uWin2;
    vec4  uWin3;
    vec4  uWin4;
    vec4  uWin5;
    vec4  uActiveWin;       // moving/active window rect (x,y,w,h); .z<=0 => none
    vec2  uActiveVel;       // its velocity, screen-widths per second (x,y)
    float uWinReact;        // master response 0..1 (config)
    float uActiveMove;      // 1 while a window is moving, eased to 0 (QML)
    int   uWinCount;        // number of valid uWin slots (0..6)
    // --- music reactivity -------------------------------------------------
    // Fed from the audio bridge (pw-cat → FFT → state file), eased in QML.
    float uMusicReact;      // master response 0..1 (config)
    float uBass;            // 0..1 low-band energy
    float uMid;             // 0..1 mid-band energy
    float uTreble;          // 0..1 high-band energy
    float uLevel;           // 0..1 overall loudness
    float uBeat;            // 0..1 transient pulse, decays fast (a ripple trigger)
    // --- surface yaw (window-drag banks the ground plane, eased + spring-back in QML)
    float uYaw;             // radians; rotates the Laserwave grid's ground plane
    float uPitch;           // -1..1 car pitch (cursor-Y); deepens the Laserwave hills when cresting
    float uHill;            // 0..1 Laserwave rolling-hill strength (0 = flat grid)
    // --- agent reactivity (AgentOS spike) ---------------------------------
    // Fed from agent.json (the fleet-state bridge), eased in QML like the music feed.
    int   uAgentState;      // 0 idle · 1 working · 2 needs_you · 3 acting · 4 snag (informational)
    float uAgentBusy;       // 0..1 working intensity  (faster flow + focus + a touch brighter)
    float uAgentWarm;       // 0..1 needs_you          (slow warm dawn glow low behind the ridges)
    float uAgentSnag;       // 0..1 snag               (thicker haze, slower, dimmer — calm, never red)
};

// Persistent reactive feedback field (react.frag): r excitation (cursor trails,
// window wakes, beat bursts) · g music throb · b expanding beat ripple.
layout(binding = 1) uniform sampler2D reactTex;

// ---- value noise + fbm ----------------------------------------------------
float hash(vec2 p) {
    p = fract(p * vec2(123.34, 345.45));
    p += dot(p, p + 34.345);
    return fract(p.x * p.y);
}
float vnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    float a = hash(i);
    float b = hash(i + vec2(1.0, 0.0));
    float c = hash(i + vec2(0.0, 1.0));
    float d = hash(i + vec2(1.0, 1.0));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}
float fbm(vec2 p) {
    float v = 0.0, a = 0.55;
    mat2 rot = mat2(0.80, 0.60, -0.60, 0.80);
    for (int i = 0; i < 5; i++) {
        v += a * vnoise(p);
        p = rot * p * 2.02 + 11.3;
        a *= 0.5;
    }
    return v;
}
// cheaper 3-octave fbm for SMOOTH domain-warp displacements and feathering, where
// the top two octaves are sub-pixel after scaling and add cost but no visible
// detail. Reserve the full 5-octave fbm() for the visible field samples.
float fbm3(vec2 p) {
    float v = 0.0, a = 0.6;
    mat2 rot = mat2(0.80, 0.60, -0.60, 0.80);
    for (int i = 0; i < 3; i++) {
        v += a * vnoise(p);
        p = rot * p * 2.02 + 11.3;
        a *= 0.5;
    }
    return v;
}

// ---- window-rect helpers --------------------------------------------------
// map a normalised point (0..1, y down) into the same centred aspect-correct
// space as `p` and the cursor, so windows and the pointer share one geometry.
vec2 toP(vec2 n) { return (n * iResolution - 0.5 * iResolution) / iResolution.y; }
// signed distance to a box (centre c, half-extents h): <0 inside, >0 outside.
float sdBox(vec2 pt, vec2 c, vec2 h) {
    vec2 d = abs(pt - c) - h;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
}
// outward normal of that box's SDF — proper per-edge/corner normals so flow routes
// around the box SHAPE (its real w×h), not a radial point at its centre. Outside an
// edge it points straight out; near a corner it's diagonal. Falls back to radial
// when inside (where the window covers the wallpaper anyway).
vec2 boxNormal(vec2 pt, vec2 c, vec2 h) {
    vec2  d = abs(pt - c) - h;
    vec2  n = max(d, 0.0) * sign(pt - c);
    float l = length(n);
    return l > 1e-4 ? n / l : normalize(pt - c + 1e-4);
}
vec4 winAt(int i) {
    if (i == 0) return uWin0; if (i == 1) return uWin1; if (i == 2) return uWin2;
    if (i == 3) return uWin3; if (i == 4) return uWin4; return uWin5;
}

// ---- palettes (5 stops each) ---------------------------------------------
void palette(int theme, out vec3 c0, out vec3 c1, out vec3 c2, out vec3 c3, out vec3 c4) {
    if (theme == 1) {            // Monterey — teal → blue → pink
        c0 = vec3(0.04, 0.09, 0.20);
        c1 = vec3(0.07, 0.38, 0.52);
        c2 = vec3(0.20, 0.52, 0.78);
        c3 = vec3(0.62, 0.44, 0.74);
        c4 = vec3(0.96, 0.66, 0.72);
    } else if (theme == 2) {     // Graphite — WhiteSur neutral, silver on slate
        c0 = vec3(0.07, 0.08, 0.10);
        c1 = vec3(0.16, 0.18, 0.22);
        c2 = vec3(0.32, 0.35, 0.40);
        c3 = vec3(0.55, 0.58, 0.64);
        c4 = vec3(0.82, 0.85, 0.90);
    } else if (theme == 3) {     // Sunset — plum → magenta → orange → gold
        c0 = vec3(0.10, 0.05, 0.18);
        c1 = vec3(0.35, 0.10, 0.30);
        c2 = vec3(0.72, 0.22, 0.38);
        c3 = vec3(0.95, 0.45, 0.28);
        c4 = vec3(1.00, 0.80, 0.45);
    } else if (theme == 4) {     // Nord — arctic polar night → frost → snow
        c0 = vec3(0.18, 0.20, 0.25);
        c1 = vec3(0.23, 0.26, 0.32);
        c2 = vec3(0.37, 0.51, 0.67);
        c3 = vec3(0.53, 0.75, 0.82);
        c4 = vec3(0.85, 0.87, 0.91);
    } else if (theme == 5) {     // Laserwave — purple-black → electric purple → hot magenta → cyan
        c0 = vec3(0.07, 0.04, 0.12);
        c1 = vec3(0.16, 0.08, 0.30);
        c2 = vec3(0.55, 0.16, 0.70);
        c3 = vec3(0.95, 0.35, 0.74);
        c4 = vec3(0.22, 0.90, 0.92);
    } else if (theme == 6) {     // Vaporwave — dusk indigo → lavender → candy pink → pastel cyan
        c0 = vec3(0.12, 0.07, 0.22);
        c1 = vec3(0.38, 0.24, 0.56);
        c2 = vec3(0.74, 0.42, 0.95);
        c3 = vec3(1.00, 0.45, 0.80);
        c4 = vec3(0.40, 0.88, 0.98);
    } else if (theme == 7) {     // Cyberpunk — ink black → deep teal → electric cyan → magenta → acid yellow
        c0 = vec3(0.02, 0.02, 0.05);
        c1 = vec3(0.04, 0.14, 0.24);
        c2 = vec3(0.00, 0.58, 0.76);
        c3 = vec3(1.00, 0.16, 0.56);
        c4 = vec3(0.98, 0.92, 0.20);
    } else if (theme == 8) {     // Outrun — night-purple → neon magenta → sun-orange → gold (a neon sunset grid)
        c0 = vec3(0.05, 0.02, 0.16);
        c1 = vec3(0.20, 0.06, 0.40);
        c2 = vec3(0.85, 0.15, 0.55);
        c3 = vec3(1.00, 0.42, 0.30);
        c4 = vec3(1.00, 0.86, 0.30);
    } else {                     // Big Sur — indigo → blue → violet → coral
        c0 = vec3(0.05, 0.06, 0.16);
        c1 = vec3(0.11, 0.18, 0.45);
        c2 = vec3(0.27, 0.32, 0.72);
        c3 = vec3(0.56, 0.36, 0.72);
        c4 = vec3(0.98, 0.55, 0.45);
    }
}
vec3 ramp(float t, vec3 c0, vec3 c1, vec3 c2, vec3 c3, vec3 c4) {
    t = clamp(t, 0.0, 1.0) * 4.0;
    if (t < 1.0) return mix(c0, c1, t);
    if (t < 2.0) return mix(c1, c2, t - 1.0);
    if (t < 3.0) return mix(c2, c3, t - 2.0);
    return mix(c3, c4, t - 3.0);
}

// ---- base looks -----------------------------------------------------------
// The "base field": how the background colour is drawn, before any reactive
// light is layered on. Switched by uStyle. Each style takes the SHARED inputs
// (the reactive warp field warpP, the static aspect-corrected point p, time t,
// the live palette c0..c4 — already theme/scheme/custom-resolved, the dark
// blend uDark, and the music packet `mus`) and outputs a colour PLUS a 0..1
// `shade` field that says "where the aurora is bright". The shared cursor/window/
// music light (back in main) rides `shade`, so every style MUST set it sensibly
// or beat flares and the ribbon bloom won't sit on the picture.
//
// MUSIC PACKET — `mus` carries four eased, master-gated drives, each with a role
// a style picks from so the reaction fits its metaphor instead of one shared term:
//   mus.x mBass  — slow low-end surge → SCALE / REACH / drift (swell the field)
//   mus.y mMid   — band "body"        → BRIGHTNESS / DENSITY (fill it in)
//   mus.z mLevel — overall loudness   → global luminance breath (used in main)
//   mus.w mBeat  — gated transient    → a style-native PULSE (puff/flare/ripple)
// Keep music on slow amplitudes, never on fast spatial motion (that reads as
// jitter — treble is intentionally not in the packet for the same reason).
//
// HARD RULE: build colour only from c0..c4 — never hardcoded RGB. That is what
// gives every style all 6 themes, both schemes, and the custom picker for free.

// Flow style's domain-warped ribbon field, evaluated at an already-advected base
// coordinate. Split out so the flow-map advection (below) can sample it at two
// phase offsets and crossfade — the advection, not a static warp, is what makes the
// motion bend around windows.
float flowField(vec2 base, float t, out float rx) {
    vec2 q = vec2(fbm3(base + vec2(0.0, 0.12 * t)),
                  fbm3(base + vec2(5.2, 1.7 - 0.10 * t)));
    vec2 r = vec2(fbm3(base + 1.8 * q + vec2(1.7, 9.2 + 0.08 * t)),
                  fbm3(base + 1.8 * q + vec2(8.3, 2.8 - 0.07 * t)));
    rx = r.x;
    return fbm(base + 2.2 * r);   // visible field: full 5 octaves
}

// Caustics' thin ridged web at an already-advected coordinate (advection removed
// from the internals so the flow-map can drive it). beatThresh widens on a beat.
float causticField(vec2 wp, float t, float beatThresh) {
    vec2 warp = vec2(fbm3(wp * 0.8 + vec2(0.0,  0.08 * t)),
                     fbm3(wp * 0.8 + vec2(4.0, -0.07 * t))) - 0.5;
    vec2 sp   = wp * 1.7 + warp * 0.7;
    float ridge = abs(fbm(sp) * 2.0 - 1.0);   // visible web: full 5 octaves
    return pow(1.0 - smoothstep(0.0, beatThresh, ridge), 1.4);
}

// Ink density (turbulent body + filaments) at an already-advected coordinate.
float inkField(vec2 wp, float t, float mMid, float mBeat) {
    vec2 warp = vec2(fbm3(wp * 1.1 + vec2(0.0, 0.12 * t)),
                     fbm3(wp * 1.1 + vec2(3.7, 0.10 * t))) - 0.5;
    vec2 ip   = wp * 1.15 + warp * 1.2;
    float turb = pow(abs(fbm(ip * 1.7) * 2.0 - 1.0), 1.3);   // visible filaments: 5 octaves
    float body = smoothstep(0.38, 0.66, fbm(ip));
    return pow(clamp(body * (0.85 + 0.6 * turb + 0.25 * mMid)
                     + 0.20 * mBeat * body, 0.0, 1.0), 0.72);
}

// Per-pixel current that routes around every window (stones in the stream): start
// from baseV, and near each window remove the component heading INTO it so the flow
// diverts around the box edges. Time-independent -> routes the flow even when idle.
// Shared by every flow-mapped style; uses the static aspect-corrected point p for
// the (fixed) window geometry.
vec2 windowFlow(vec2 p, vec2 baseV, float t) {
    vec2  V      = baseV;
    float wreact = clamp(uWinReact, 0.0, 1.0);
    if (wreact > 0.001) {
        // FEATHER (computed ONCE — it's loop-invariant): a clean exp() halo makes the
        // deflection mirror corner-to-corner (too uniform to read as water), so we
        // modulate the routing strength with low-freq noise. Anchored to SCREEN
        // position (not the window centre): a moving window slides THROUGH this
        // slowly-drifting field, so dragging never makes the feather flicker (keying
        // it to wc made the noise race with the drag = white shimmer). fbm3 is plenty
        // for this smooth modulation. Stays > 0 so the current never leaks in.
        float feather = wreact * (0.45 + 0.85 * fbm3(p * 2.2 + vec2(0.0, 0.4 * t)));
        for (int i = 0; i < 6; i++) {
            if (i >= uWinCount) break;
            vec4 w = winAt(i);
            if (w.z <= 0.0) continue;
            vec2  wc  = toP(w.xy + 0.5 * w.zw);
            vec2  wh  = 0.5 * w.zw * iResolution / iResolution.y;
            float sd  = max(sdBox(p, wc, wh), 0.0);
            if (sd > 1.4) continue;                     // negligible influence -> skip the work
            float g   = exp(-sd * 3.5) * feather;       // routing reach around the stone
            vec2  nrm = boxNormal(p, wc, wh);           // outward from the box EDGE
            float vn  = dot(V, nrm);                    // <0 = flow heading into it
            V = mix(V, V - min(vn, 0.0) * nrm * 1.2, clamp(g, 0.0, 1.0));  // cancel inward + bow
        }
    }
    return V;
}

// ===================== shared 3-D scene helpers ==============================
// Both neon styles below (Cyberpunk "Datascape" and Vaporwave "Elysium") are
// REAL raymarched 3-D scenes — genuine perspective + reflections give depth a
// 2-D cut-out can't fake. y is up, ground at y=0. Colour comes ONLY from the
// palette c0..c4 (white is used as a neutral value-lift for marble, like the
// light-mode grade does), so both keep every theme/scheme/custom for free.
float sdBox3(vec3 p, vec3 b) {
    vec3 d = abs(p) - b;
    return length(max(d, 0.0)) + min(max(d.x, max(d.y, d.z)), 0.0);
}
float sdBox2(vec2 p, vec2 b) {              // box infinite along the missing axis
    vec2 d = abs(p) - b;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
}
float sdSphere(vec3 p, float r) { return length(p) - r; }
// vertical capped cylinder: axis +y, radius r, half-height h, centred at origin
float sdCylinderY(vec3 p, float r, float h) {
    vec2 d = abs(vec2(length(p.xz), p.y)) - vec2(r, h);
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0));
}

// ============ Cyberpunk: "Datascape" — a Tron-style neon data grid ===========
// A CLEAN, emissive (not foggy) flythrough over a glowing grid floor: light
// pulses race along the lines like data on a circuit board, edge-lit data-blocks
// rise off the grid, and a neon horizon glows in the distance. Digital, not
// photoreal — the cheaper, more abstract reading. Camera drifts forward.
#define DG_CELL 2.0
void dgCell(vec2 id, out float h, out float w) {
    float occupied = step(0.40, hash(id * 0.91 + 4.7));   // ~60% of cells hold a block
    float r        = hash(id * 1.7 + 0.3);
    h = occupied * (0.45 + 3.4 * r * r);                  // mostly low, a few tall
    w = 0.34 + 0.32 * hash(id + 2.0);                     // footprint < CELL/2 (no overlap)
}
float mapData(vec3 p) {
    float d  = p.y;                                       // grid floor
    vec2  id = floor(p.xz / DG_CELL);
    vec3  q  = p; q.xz = mod(p.xz + 0.5 * DG_CELL, DG_CELL) - 0.5 * DG_CELL;
    float h, w; dgCell(id, h, w);
    if (h > 0.001)
        d = min(d, sdBox3(vec3(q.x, p.y - h * 0.5, q.z), vec3(w, h * 0.5, w)));
    return d;
}
vec3 dataNormal(vec3 p) {
    vec2 e = vec2(0.015, 0.0);
    return normalize(vec3(mapData(p + e.xyy) - mapData(p - e.xyy),
                          mapData(p + e.yxy) - mapData(p - e.yxy),
                          mapData(p + e.yyx) - mapData(p - e.yyx)));
}
// neon edge-glow on a data-block: bright where two faces meet (all 12 edges) so
// the block reads as a wireframe-lit solid, not a painted box.
vec3 dataEdge(vec3 p, vec3 c0, vec3 c1, vec3 c2, vec3 c3, vec3 c4) {
    vec2  id = floor(p.xz / DG_CELL);
    vec3  q  = p; q.xz = mod(p.xz + 0.5 * DG_CELL, DG_CELL) - 0.5 * DG_CELL;
    float h, w; dgCell(id, h, w);
    if (h <= 0.001) return vec3(0.0);
    vec3  lp = vec3(q.x, p.y - h * 0.5, q.z);
    vec3  e  = vec3(w, h * 0.5, w) - abs(lp);             // ~0 on a face
    float m1 = min(e.x, min(e.y, e.z));
    float m3 = max(e.x, max(e.y, e.z));
    float m2 = e.x + e.y + e.z - m1 - m3;                 // 2nd-smallest -> ~0 ON AN EDGE
    float edge = smoothstep(0.07, 0.0, m2) + 0.18 * smoothstep(0.22, 0.0, m2);
    vec3  nc   = ramp(0.42 + 0.5 * hash(id + 5.0), c0, c1, c2, c3, c4);
    return nc * edge;
}
// emissive grid floor: glowing lines + data packets streaming along them, a
// beat ripple expanding from under the camera, and a cursor that lights traces.
vec3 dataFloor(vec3 pos, float t, vec3 c0, vec3 c1, vec3 c2, vec3 c3, vec3 c4,
               vec2 curFloor, float curGate, float camZ, float mMid, float mBeat) {
    vec2  g  = pos.xz;
    vec2  fr = abs(fract(g) - 0.5);                       // 0 at a line, 0.5 at cell centre
    // fwidth-scaled edge -> lines anti-alias (stay ~1px) into the distance instead of
    // shimmering, while staying crisp up close. Kills the worst "low-res" jaggies.
    vec2  lwd = 0.02 + 1.4 * fwidth(g);
    float lx = smoothstep(lwd.x, 0.0, 0.5 - fr.x);
    float lz = smoothstep(lwd.y, 0.0, 0.5 - fr.y);
    float lines = max(lx, lz);
    // a finer secondary grid (circuit traces) for surface detail
    vec2  fr2  = abs(fract(g * 4.0) - 0.5);
    vec2  lwd2 = 0.05 + 1.4 * fwidth(g * 4.0);
    float fine = max(smoothstep(lwd2.x, 0.0, 0.5 - fr2.x), smoothstep(lwd2.y, 0.0, 0.5 - fr2.y));
    vec3  lineCol = mix(c2, c3, 0.5);
    vec3  packCol = mix(c3, c4, 0.5);
    vec3  col = c0 * 0.04;                                // near-black substrate
    col += lineCol * lines * (0.55 + 0.45 * mMid);
    col += lineCol * fine * 0.10;                         // fine circuit detail
    // data packets streaming along the lines (z fast, x slower the other way)
    float pkZ = smoothstep(0.86, 1.0, fract(g.y * 0.22 - t * 0.55 + hash(vec2(floor(g.x), 1.0))));
    float pkX = smoothstep(0.92, 1.0, fract(g.x * 0.22 + t * 0.30 + hash(vec2(floor(g.y), 7.0))));
    col += packCol * (pkZ * lx + pkX * lz) * 1.5;
    // a beat sends a bright ripple expanding across the grid from under the viewer
    float ringR = fract(t * 0.6) * 30.0;
    float ring  = exp(-abs(length(g - vec2(0.0, camZ)) - ringR) * 1.1);
    col += packCol * ring * lines * mBeat * 2.5;
    // the cursor lights the traces around its floor projection (style-native)
    col += lineCol * exp(-length(g - curFloor) * 0.8) * curGate * (0.35 + lines) * 1.4;
    // --- TRAFFIC: light traces racing along the grid "roads" (the avenues) ---
    // headlights (warm-white) run +z, taillights (cool) run -z on the z-roads, with
    // cross-traffic on the x-roads. A short exp tail behind each makes them streak.
    vec3  headC = mix(c4, vec3(1.0), 0.5);
    vec3  tailC = mix(c3, c2, 0.25);
    float laneZ = floor(g.x + 0.5);                       // nearest z-running road
    float hz1   = hash(vec2(laneZ, 3.0));
    float carZf = exp(-fract(g.y * 0.45 - t * (1.1 + 0.9 * hz1) + hz1 * 7.0) * 9.0)
                * step(0.45, hash(vec2(laneZ, 5.0)));      // headlights +z
    float hz2   = hash(vec2(laneZ, 9.0));
    float carZb = exp(-fract(-g.y * 0.40 - t * (0.9 + 0.8 * hz2) + hz2 * 3.0) * 9.0)
                * step(0.55, hash(vec2(laneZ, 2.0)));      // taillights -z
    col += (headC * carZf + tailC * carZb) * lx * 1.9;
    float laneX = floor(g.y + 0.5);                       // cross traffic on the x-roads
    float hx1   = hash(vec2(laneX, 6.0));
    float carX  = exp(-fract(g.x * 0.42 - t * (1.0 + 0.8 * hx1) + hx1 * 5.0) * 9.0)
                * step(0.60, hash(vec2(laneX, 8.0)));
    col += mix(headC, tailC, 0.5) * carX * lz * 1.6;
    return col;
}
vec3 dataSky(vec3 rd, vec3 c0, vec3 c1, vec3 c2, vec3 c3) {
    float up  = clamp(rd.y, 0.0, 1.0);
    vec3  col = mix(mix(c1, c2, 0.35), c0 * 0.12, pow(up, 0.5));   // neon horizon -> deep top
    col += mix(c2, c3, 0.5) * exp(-max(rd.y, 0.0) * 7.0) * 0.55;   // horizon glow band
    return col;
}
vec3 datascapeScene(vec2 pp, vec2 mp, float curGate, float t,
                    vec3 c0, vec3 c1, vec3 c2, vec3 c3, vec3 c4, vec4 mus, out float bright) {
    float mBass = mus.x, mMid = mus.y, mBeat = mus.w;
    // fly ABOVE the blocks (tallest ~3.9) looking down — a flythrough over a
    // circuit board; a lower camera flies INTO the tall blocks and clips them.
    vec3 ro = vec3(0.0, 6.5 + 0.4 * mBass, t * 2.4);
    vec3 ta = ro + vec3(0.0, -3.2, 5.0);
    vec3 fw = normalize(ta - ro);
    vec3 rt = normalize(cross(vec3(0.0, 1.0, 0.0), fw));
    vec3 uo = cross(fw, rt);
    vec3 rd = normalize(fw * 1.7 + rt * pp.x * 1.7 - uo * pp.y * 1.7);
    // project the cursor onto the grid floor (so it can light traces there)
    vec3  crd = normalize(fw * 1.7 + rt * mp.x * 1.7 - uo * mp.y * 1.7);
    float ct  = (crd.y < -0.01) ? -ro.y / crd.y : -1.0;
    vec2  curFloor = (ct > 0.0) ? (ro + crd * ct).xz : vec2(1e4);

    float dist = 0.0; int hit = 0; vec3 pos = ro;
    for (int i = 0; i < 90; i++) {
        pos = ro + rd * dist;
        float dd = mapData(pos);
        if (dd < 0.0022 * dist + 0.001) { hit = 1; break; }
        dist += dd * 0.9;
        if (dist > 70.0) break;
    }
    vec3 col;
    if (hit == 1) {
        vec3 n = dataNormal(pos);
        if (n.y > 0.6 && pos.y < 0.12) {
            col = dataFloor(pos, t, c0, c1, c2, c3, c4, curFloor, curGate, ro.z, mMid, mBeat);
            // glossy reflection of the blocks off the grid floor (Tron sheen)
            vec3 rr = reflect(rd, vec3(0.0, 1.0, 0.0));
            vec3 rp = pos + vec3(0.0, 0.01, 0.0);
            float rdist = 0.0; int rhit = 0; vec3 rpos = rp;
            for (int j = 0; j < 34; j++) {
                rpos = rp + rr * rdist;
                float dd = mapData(rpos);
                if (dd < 0.004 * rdist + 0.002) { rhit = 1; break; }
                rdist += dd * 0.9;
                if (rdist > 34.0) break;
            }
            if (rhit == 1)
                col += dataEdge(rpos, c0, c1, c2, c3, c4) * 0.45;     // dim mirrored neon
        } else {
            vec3 em = dataEdge(pos, c0, c1, c2, c3, c4);
            col  = c0 * 0.05 + em * (1.5 + 1.2 * mBass);              // bass lifts the blocks
            col += em * mBeat * 1.2;                                  // beat flares them
            // faint "data readout" flecks on the dark faces — surface detail, not flat black
            vec2  fc  = (abs(n.x) > abs(n.z)) ? vec2(pos.z, pos.y) : vec2(pos.x, pos.y);
            vec2  fcc = floor(fc * 7.0);
            float fl  = step(0.84, hash(fcc)) * (0.4 + 0.6 * sin(t * 3.0 + hash(fcc) * 31.0));
            col += mix(c2, c3, 0.5) * max(fl, 0.0) * 0.16;
        }
        col = mix(col, mix(c2, c3, 0.5) * 0.4, 1.0 - exp(-dist * 0.018));  // clean digital depth fade
    } else {
        col = dataSky(rd, c0, c1, c2, c3);
    }
    // --- ATMOSPHERE: neon glow haze (light pollution) thickening with distance,
    //     plus faint light motes drifting in the air, so the grid breathes air ---
    col += mix(c2, c3, 0.5) * (1.0 - exp(-dist * 0.02)) * 0.10;     // far neon haze
    vec2  mq   = pp * 7.0 + vec2(t * 0.25, -t * 0.6);              // drifting motes
    vec2  mc   = floor(mq);
    float mh   = hash(mc);
    float mote = step(0.93, mh) * smoothstep(0.5, 0.0, length(fract(mq) - 0.5))
               * (0.5 + 0.5 * sin(t * 3.0 + mh * 40.0));
    col += mix(c3, c4, 0.5) * mote * 0.22;
    bright = clamp(dot(col, vec3(0.4)), 0.0, 1.0);
    col = col / (1.0 + col * 0.45);                       // soft rolloff -> neon bloom
    return col;
}

// ============ Vaporwave: "Elysium" — an endless pastel-marble colonnade =======
// Two rows of fluted classical columns under a continuous architrave recede
// toward the slitted "Floral Shoppe" sun, over a glossy checkered-marble floor
// that MIRRORS the pastel sky. A focal sphere drifts in the aisle. Dreamy,
// deeply 3-D, unmistakably vaporwave. Palette only (+ white as a marble lift).
#define CO_PZ 3.0      // column spacing in z
#define CO_X  2.1      // half-width of the aisle (column-row offset in x)
#define CO_H  3.2      // column height
#define CO_R  0.38     // column radius
// distances to each material; returns the scene SDF, sets `mat` (0 floor,
// 1 column, 2 architrave, 3 focal sphere) at the nearest surface.
float mapColonnade(vec3 p, float roz, float t, out int mat) {
    float fl = p.y;                                       // floor
    vec3  q  = p; q.z = mod(p.z + 0.5 * CO_PZ, CO_PZ) - 0.5 * CO_PZ;
    float cL = sdCylinderY(vec3(p.x + CO_X, p.y - CO_H * 0.5, q.z), CO_R, CO_H * 0.5);
    float cR = sdCylinderY(vec3(p.x - CO_X, p.y - CO_H * 0.5, q.z), CO_R, CO_H * 0.5);
    float colmn = min(cL, cR);
    float bh = 0.30;                                      // architrave half-height
    float bL = sdBox2(vec2(p.x + CO_X, p.y - (CO_H + bh)), vec2(CO_R + 0.20, bh));
    float bR = sdBox2(vec2(p.x - CO_X, p.y - (CO_H + bh)), vec2(CO_R + 0.20, bh));
    float beam = min(bL, bR);
    vec3  sc  = vec3(0.0, 1.7 + 0.18 * sin(t * 0.7), roz + 8.0);   // drifts with the camera
    float sph = sdSphere(p - sc, 0.95);
    float d = fl; mat = 0;
    if (colmn < d) { d = colmn; mat = 1; }
    if (beam  < d) { d = beam;  mat = 2; }
    if (sph   < d) { d = sph;   mat = 3; }
    return d;
}
float mapColD(vec3 p, float roz, float t) { int m; return mapColonnade(p, roz, t, m); }
vec3 colNormal(vec3 p, float roz, float t) {
    vec2 e = vec2(0.015, 0.0);
    return normalize(vec3(mapColD(p + e.xyy, roz, t) - mapColD(p - e.xyy, roz, t),
                          mapColD(p + e.yxy, roz, t) - mapColD(p - e.yxy, roz, t),
                          mapColD(p + e.yyx, roz, t) - mapColD(p - e.yyx, roz, t)));
}
// the pastel sky + slitted "Floral Shoppe" sun (with chromatic-aberration
// fringing), evaluated along a view ray. `sunMask` reports the sun's coverage.
vec3 colSky(vec3 rd, float t, vec3 c0, vec3 c1, vec3 c2, vec3 c3, vec3 c4, float mBass, out float sunMask) {
    vec2  sky2 = rd.xy / max(rd.z, 0.12);                 // screen-like sky coordinate
    float up   = clamp(rd.y * 1.4 + 0.25, 0.0, 1.0);
    vec3  col  = mix(mix(c3, c4, 0.35), mix(c2, c1, 0.5), up);   // pink horizon -> lavender/teal
    float cl   = fbm(vec2(sky2.x * 1.1 - t * 0.05, sky2.y * 1.4 + 2.0));
    col = mix(col, mix(c4, c3, 0.5), smoothstep(0.55, 0.95, cl) * 0.18 * up);   // drifting clouds
    vec2  sc = vec2(0.0, 0.16);
    float Rs = 0.30 + 0.05 * mBass;                       // bass swells the sun
    float sy   = clamp((sky2.y - (sc.y - Rs)) / (2.0 * Rs), 0.0, 1.0);
    float slit = smoothstep(0.42, 0.50, fract(sy * 7.0));
    float cut  = mix(1.0, slit, smoothstep(0.45, 1.0, sy));
    float ca   = 0.008;
    float mR = smoothstep(Rs, Rs - 0.05, length((sky2 - sc) - vec2(ca, 0.0))) * cut;
    float mG = smoothstep(Rs, Rs - 0.05, length( sky2 - sc))                  * cut;
    float mB = smoothstep(Rs, Rs - 0.05, length((sky2 - sc) + vec2(ca, 0.0))) * cut;
    vec3 sunCol = mix(c3, c4, smoothstep(0.0, 0.55, 1.0 - sy));
    col.r = mix(col.r, sunCol.r, mR);
    col.g = mix(col.g, sunCol.g, mG);
    col.b = mix(col.b, sunCol.b, mB);
    float halo = exp(-length(sky2 - sc) * 3.8);
    col += sunCol * halo * 0.22;
    sunMask = max(max(max(mR, mG), mB), halo * 0.5);
    return col;
}
// marble surface shade for columns / architrave / the iridescent focal sphere
vec3 colSurface(vec3 pos, vec3 n, int mat, vec3 rd, float roz, float t,
                vec3 c0, vec3 c1, vec3 c2, vec3 c3, vec3 c4, float mBeat) {
    vec3  sunDir = normalize(vec3(0.0, 0.25, 1.0));
    vec3  marble = mix(mix(c3, c4, 0.4), vec3(1.0), 0.55);    // pale pinkish marble from palette
    if (mat == 3) {
        float sm; vec3 refl = colSky(reflect(rd, n), t, c0, c1, c2, c3, c4, 0.0, sm);
        float fres = pow(1.0 - max(dot(-rd, n), 0.0), 2.5);
        vec3  irid = 0.5 + 0.5 * cos(6.2831 * (n * 0.5 + vec3(0.0, 0.33, 0.66)));
        irid = mix(irid, mix(c3, c4, 0.5), 0.5);             // tie the iridescence to the palette
        return mix(refl, irid, 0.4) + fres * mix(c4, vec3(1.0), 0.5) * 0.6;
    }
    // subtle marble veining (stretched fbm streaks) so the stone isn't flat fill
    float vein = fbm(pos.xz * vec2(2.2, 6.0) + pos.y * 1.5);
    marble *= 0.86 + 0.22 * smoothstep(0.35, 0.72, vein);
    float dif = max(dot(n, sunDir), 0.0);
    float sky = 0.5 + 0.5 * n.y;                             // hemispheric skylight
    vec3  col = marble * (0.46 + 0.52 * dif + 0.30 * sky + 0.12 * max(-n.y, 0.0));  // +floor bounce
    if (mat == 1) {
        float zz  = mod(pos.z + 0.5 * CO_PZ, CO_PZ) - 0.5 * CO_PZ;
        float lpx = (pos.x < 0.0 ? pos.x + CO_X : pos.x - CO_X) + 1e-5;
        col *= 0.85 + 0.15 * cos(atan(zz, lpx) * 16.0);      // vertical fluting
        col *= 0.55 + 0.45 * smoothstep(0.0, 0.7, pos.y / CO_H);  // ambient occlusion toward the base
    }
    col += mix(c3, c4, 0.5) * mBeat * 0.5;                   // a beat pulses warm light down the colonnade
    return col;
}
vec3 colonnadeScene(vec2 pp, vec2 mp, float curGate, float t,
                    vec3 c0, vec3 c1, vec3 c2, vec3 c3, vec3 c4, vec4 mus, out float bright) {
    float mBass = mus.x, mBeat = mus.w;
    vec3 ro = vec3(0.0, 1.5, t * 1.6);
    vec3 ta = ro + vec3(0.0, -0.05, 5.0);
    vec3 fw = normalize(ta - ro);
    vec3 rt = normalize(cross(vec3(0.0, 1.0, 0.0), fw));
    vec3 uo = cross(fw, rt);
    vec3 rd = normalize(fw * 1.6 + rt * pp.x * 1.6 - uo * pp.y * 1.6);
    // cursor projected to the floor -> a warm spotlight pool (style-native)
    vec3  crd = normalize(fw * 1.6 + rt * mp.x * 1.6 - uo * mp.y * 1.6);
    float ct  = (crd.y < -0.01) ? -ro.y / crd.y : -1.0;
    vec2  curFloor = (ct > 0.0) ? (ro + crd * ct).xz : vec2(1e4);

    float dist = 0.0; int hit = 0, mat = 0; vec3 pos = ro;
    for (int i = 0; i < 96; i++) {
        pos = ro + rd * dist;
        float dd = mapColonnade(pos, ro.z, t, mat);
        if (dd < 0.0022 * dist + 0.001) { hit = 1; break; }
        dist += dd * 0.9;
        if (dist > 60.0) break;
    }
    vec3 col;
    if (hit == 1) {
        vec3 n = colNormal(pos, ro.z, t);
        if (mat == 0) {
            // glossy checkered marble floor: reflect the sky + a short surface march
            vec3 gn = vec3(0.0, 1.0, 0.0);
            vec3 rr = reflect(rd, gn);
            float sm; vec3 refl = colSky(rr, t, c0, c1, c2, c3, c4, mBass, sm);
            vec3 rp = pos + gn * 0.02; float rdist = 0.0; int rhit = 0, rmat = 0; vec3 rpos = rp;
            for (int j = 0; j < 40; j++) {
                rpos = rp + rr * rdist;
                float dd = mapColonnade(rpos, ro.z, t, rmat);
                if (dd < 0.004 * rdist + 0.002 && rmat != 0) { rhit = 1; break; }
                rdist += dd * 0.9;
                if (rdist > 30.0) break;
            }
            if (rhit == 1)
                refl = colSurface(rpos, colNormal(rpos, ro.z, t), rmat, rr, ro.z, t, c0, c1, c2, c3, c4, mBeat);
            float chk  = mod(floor(pos.x) + floor(pos.z), 2.0);
            vec3  tile = mix(mix(c1, c2, 0.5), mix(mix(c3, c4, 0.4), vec3(1.0), 0.55), 0.45 + 0.4 * chk);
            col = mix(tile * 0.5, refl, 0.62);
            col += mix(c3, c4, 0.5) * exp(-abs(pos.x) * 1.2) * 0.18 * (1.0 + mBass);  // sun mirrored down the aisle
        } else {
            col = colSurface(pos, n, mat, rd, ro.z, t, c0, c1, c2, c3, c4, mBeat);
        }
        float sm; vec3 haze = colSky(rd, t, c0, c1, c2, c3, c4, mBass, sm);
        col = mix(col, haze, 1.0 - exp(-dist * 0.045));      // dreamy pastel distance haze
        if (curGate > 0.001) {                               // cursor spotlight pooled on the floor
            float cd = length(pos.xz - curFloor);
            col += mix(c4, vec3(1.0, 0.95, 0.9), 0.4) * exp(-cd * 1.3) * curGate * 0.45;
        }
    } else {
        float sm; col = colSky(rd, t, c0, c1, c2, c3, c4, mBass, sm);
    }
    bright = clamp(dot(col, vec3(0.4)), 0.0, 1.0);
    return col;
}

vec3 baseLook(int style, vec2 warpP, vec2 p, vec2 mp, float curGate, float t,
              vec3 c0, vec3 c1, vec3 c2, vec3 c3, vec3 c4,
              vec4 mus, out float shade)
{
    float mBass = mus.x, mMid = mus.y, mLevel = mus.z, mBeat = mus.w;
    // a calm vertical "sky" most styles sit on (lighter high, deeper low)
    float grad = clamp(0.5 - p.y * 0.9, 0.0, 1.0);
    vec3  sky  = mix(c1, c0, grad);

    // ---- shared FLOW: one coherent current the whole scene drifts ALONG, so
    //      every style reads as flowing-through rather than boiling in place.
    //      The rule: translate the SAMPLE POINT (advection = flow); putting time
    //      into the noise offsets instead just morphs it where it sits (= jitter).
    //      The current's pace eases up with the music's ENERGY — smoothly, never
    //      stepped on the beat (a beat-driven lurch is itself jitter). Each style
    //      scales `flow` to taste; the direction is a gentle diagonal drift.
    float flowAmt = t * (1.0 + 1.2 * mLevel + 0.8 * mBass);  // distance travelled
    vec2  flow    = vec2(0.92, 0.39) * flowAmt;

    if (style == 1) {
        // --- Hills: layered ridgelines receding into haze, with a drifting
        //     depth-of-field. Distant hills sit near the horizon, lighter and
        //     hazier (atmospheric perspective); nearer hills are lower, darker,
        //     and parallax-drift faster. A focus plane breathes between depths so
        //     layers slip in and out of focus — the in-focus band shows surface
        //     texture, the rest goes soft. Palette stops only.
        const int LAYERS = 5;
        vec3 col = sky;                                   // sky behind the hills
        shade = 0.12;                                     // dim baseline (sky)
        // --- AgentOS reactivity (spike) ------------------------------------
        // working: the same scene, running harder (faster parallax + faster focus
        //   breath + a touch brighter). snag: slower (calm, "stopped, waiting").
        float aBusy = clamp(uAgentBusy, 0.0, 1.0);
        float aSnag = clamp(uAgentSnag, 0.0, 1.0);
        float aPace = (1.0 + 0.9 * aBusy) * (1.0 - 0.5 * aSnag);   // flow/focus speed
        vec2  aflow = flow * aPace;                       // Hills-local advection
        // bass breathes the focus plane; working quickens that breath, snag slows it.
        float focus = 0.5 + 0.45 * sin(t * 0.40 * aPace) + 0.12 * mBass * sin(t * 0.8);
        for (int i = 0; i < LAYERS; i++) {
            float d  = 1.0 - float(i) / float(LAYERS - 1); // far (1) drawn first .. near (0) last
            float nr = 1.0 - d;
            // parallax: near layers ride the current faster across the frame
            float px = warpP.x * (0.9 + 1.0 * nr) + aflow.x * (0.25 + 0.55 * nr);
            // bumpy silhouette: two octaves so even the FAR ridge has genuine
            // shape (a single low-freq arc reads as a flat stripe). Far gets a
            // touch more amplitude than before so the topmost ridgeline undulates.
            float ridge = ((fbm(vec2(px * 1.5 + d * 7.0,  0.0)) - 0.5) * 0.70
                         + (fbm(vec2(px * 3.7 + d * 7.0, 11.0)) - 0.5) * 0.30)
                        * mix(0.22, 0.30, nr);
            // horizons spread across the whole frame: far high, near low
            float horizon = mix(-0.26, 0.40, nr) + ridge;
            float blur = abs(d - focus);                  // depth of field
            // crisp in focus, soft off it — but distant ridges are NEVER razor-
            // sharp (atmospheric haze), so add depth-scaled softness: far stays a
            // hazy band, near can still snap crisp when in focus.
            float edge = mix(0.005, 0.07, blur) + 0.05 * d;
            float fill = smoothstep(horizon - edge, horizon + edge, warpP.y);
            // surface texture only where this layer is in focus; a beat makes the
            // in-focus hillside shimmer briefly, mid lifts its overall presence
            float tex = (vnoise(vec2(px * 5.0, warpP.y * 5.0)) - 0.5)
                      * (0.10 + 0.10 * mMid + 0.16 * mBeat + 0.12 * aBusy)
                      * (1.0 - smoothstep(0.0, 0.45, blur));
            // tonal depth: near = dark cool stops, far = pale (no hot coral);
            // distant hills melt partway into the sky haze
            vec3 hue = ramp(0.10 + 0.52 * d, c0, c1, c2, c3, c4) * (1.0 + tex);
            hue = mix(hue, sky, 0.40 * d);
            col   = mix(col,   hue,               fill);
            shade = mix(shade, 0.30 + 0.50 * nr,  fill);  // near hills are the bright field
        }
        // working: gentle overall lift + more bright-field so the shared bloom rides.
        col *= (1.0 + 0.08 * aBusy);
        shade = clamp(shade + 0.10 * aBusy, 0.0, 1.0);
        // needs_you: a slow warm dawn glow gathering low and centre, behind the
        //   ridges — the ONE deliberate warmth (design-ux), localised so foreground
        //   stays legible. Breathes on a ~10s cycle.
        float aWarm   = clamp(uAgentWarm, 0.0, 1.0);
        float breath  = 0.55 + 0.45 * sin(t * 0.62);
        float lowGlow = smoothstep(0.15, -0.65, warpP.y)      // strongest low in frame
                      * exp(-abs(p.x) * 0.9);                 // gathered toward centre
        col += vec3(1.00, 0.60, 0.34) * lowGlow * aWarm * breath * 0.42;
        // snag: thicken the atmospheric haze + desaturate/dim — reads as "stopped,
        //   waiting". Calm, never alarming, never red.
        col = mix(col, sky, 0.35 * aSnag);
        float snagLuma = dot(col, vec3(0.299, 0.587, 0.114));
        col = mix(col, vec3(snagLuma), 0.35 * aSnag);
        col *= (1.0 - 0.12 * aSnag);
        return col;
    }

    if (style == 2) {
        // --- Silk curtains: a few soft vertical light bands whose x-centres
        //     wander, swayed laterally by a low-freq fbm — aurora borealis hung
        //     from the top. Tinted up through c2..c4. shade = band intensity.
        // curtains = soft bright ridges of a 1-D field that DRIFTS sideways on the
        // current, so they stream through the frame and renew at the edge instead of
        // swaying in place; a slow second coord lets the folds breathe gently.
        float fx   = warpP.x * 2.6 - flow.x * 0.5;
        float n    = fbm(vec2(fx, 0.30 * t + 6.0));
        // max(0,…): fbm can exceed 1, so the inner term can go negative — and pow()
        // of a negative base is NaN, which rendered as WHITE FLASHES (most visible
        // when a drag perturbs warpP past the threshold). Clamp the base.
        float band = pow(max(0.0, 1.0 - abs(n * 2.0 - 1.0)), 3.5);  // a few vertical bands
        // curtains stream most of the height, brightest up top, soft floor
        float vfall = smoothstep(-0.7, 0.55, -warpP.y + 0.18 * fbm(vec2(warpP.x * 2.0 - flow.x * 0.3, 0.0)));
        // mid lifts band intensity; a beat sends a brief flare down the curtains
        shade = clamp(band * vfall * (1.0 + 0.22 * mMid + 0.30 * mBeat), 0.0, 1.0);
        // lower peak so curtains glow rather than blow out to flat coral
        vec3 curtain = ramp(0.30 + 0.45 * shade, c0, c1, c2, c3, c4);
        return mix(sky, curtain, clamp(shade * (0.9 + 0.10 * mBass), 0.0, 1.0));
    }

    if (style == 3) {
        // --- Caustics: slow water-light, a thin ridged web that now FLOWS AROUND
        //     windows (stones in the stream) via the shared velocity field + a
        //     two-phase flow-map, exactly like Flow. The veins glide and reconnect
        //     as they travel; near a window the current diverts around the box edges
        //     so the web bends past it. THIN web language preserved (causticField).
        vec2  V  = windowFlow(p, vec2(0.92, 0.39), t);        // diagonal current, routed
        float D  = 0.5 * (1.0 + 0.5 * mLevel + 0.4 * mBass);  // veins drift slowly
        float ph = fract(t * 0.6);
        float pA = ph, pB = fract(ph + 0.5);
        float wA = 1.0 - abs(2.0 * pA - 1.0);
        float wB = 1.0 - abs(2.0 * pB - 1.0);
        float bt = 0.16 + 0.10 * mBeat;
        float caustic = causticField(warpP - V * (pA * D), t, bt) * wA
                      + causticField(warpP - V * (pB * D), t, bt) * wB;
        shade = clamp(caustic * (1.0 + 0.20 * mMid), 0.0, 1.0);
        vec3 deep  = mix(c1, c0, clamp(0.5 - p.y * 0.7, 0.0, 1.0));
        // luminous but COOL glint (mid stops only, no full-coral): water-light veins
        vec3 glint = ramp(0.38 + 0.32 * shade, c0, c1, c2, c3, c4);
        return mix(deep, glint, shade * 0.95);
    }

    if (style == 4) {
        // --- Ink in water: turbulent pigment plumes that RISE and now also FLOW
        //     AROUND windows. The base current leans upward (ink rises); the shared
        //     velocity field routes it around the box edges, and the two-phase
        //     flow-map advects the density so plumes billow as they travel and
        //     divert past windows. Cool water c0..c1, pigment up through c2..c4.
        vec2  V  = windowFlow(p, vec2(0.25, -0.95), t);       // upward current, routed
        float D  = 0.8 * (1.0 + 0.5 * mLevel + 0.4 * mBass);
        float ph = fract(t * 0.6);
        float pA = ph, pB = fract(ph + 0.5);
        float wA = 1.0 - abs(2.0 * pA - 1.0);
        float wB = 1.0 - abs(2.0 * pB - 1.0);
        float ink = inkField(warpP - V * (pA * D), t, mMid, mBeat) * wA
                  + inkField(warpP - V * (pB * D), t, mMid, mBeat) * wB;
        shade = ink;
        vec3 water   = mix(c1, c0, clamp(0.5 - p.y * 0.7, 0.0, 1.0));
        vec3 pigment = ramp(0.34 + 0.55 * ink, c0, c1, c2, c3, c4);
        return mix(water, pigment, clamp(ink * (1.0 + 0.08 * mBass), 0.0, 1.0));
    }

    if (style == 5) {
        // --- Laserwave: a neon perspective grid riding a reflective WATER surface
        //     under a banded retro sun. The water RIPPLES INTERACTIVELY — expanding
        //     rings from the cursor, a music throb, and an ambient chop — and the sun
        //     is mirrored in it as a ripple-shimmered reflection. Twinkling starfield
        //     + nebula in the deep sky, faint CRT scanlines. shade = neon energy.
        const float HOR = -0.04;                          // horizon a touch above centre
        vec2  sc   = vec2(0.0, HOR - 0.22);               // sun centre, above horizon
        float Rs   = 0.22;
        vec3  col5;
        float shd;
        if (p.y > HOR) {                                  // ---- reflective WATER surface ----
            float depth = (p.y - HOR);
            // RIPPLE field — the interactive part: ambient chop + expanding rings from
            // the cursor + a music throb. Scaled up near the viewer (lower in frame =
            // closer water) so it reads as a receding surface, not a flat decal.
            float amb    = 0.010 * sin((p.x * 5.0 + p.y * 16.0) - t * 1.5)
                         + 0.008 * (fbm(vec2(p.x * 3.0, p.y * 7.0 - t * 0.4)) - 0.5);
            float dC     = length(p - mp);
            float curRip = sin(dC * 52.0 - t * 7.0) * exp(-dC * 4.5) * curGate * 0.022;
            float musRip = sin(depth * 55.0 - t * 5.0) * (0.45 * mBeat + 0.25 * mLevel) * 0.020;
            float rip    = (amb + curRip + musRip) / max(depth + 0.05, 0.06);
            // neon wireframe grid riding the water, x displaced by the ripples
            float drive = t * (1.6 + 1.2 * mBass);
            // YAW: rotate the ground plane about the vertical axis at the vanishing
            // point so the whole surface banks (window-drag drives uYaw, eased +
            // spring-back in QML). (ax,az) are the world coords pre-1/d; rotate, then
            // divide, so perspective stays correct. uYaw=0 → the original grid.
            float cy = cos(uYaw), sy = sin(uYaw);
            float ax = 1.1 * (warpP.x + rip);
            float az = 0.16;
            // --- 3-D rolling TERRAIN: the neon grid DRAPES over a smooth height field
            //     that varies across BOTH the road's width and its length, so the
            //     contour lines genuinely deform (horizontals curve, verticals bend)
            //     instead of sliding on a flat plane. We sample the height at the FLAT
            //     ground coords, then re-project depth (a raised point reads as farther/
            //     higher), so the lines climb hills with correct perspective. Scrolls
            //     toward the viewer with `drive`; uPitch swells it; uHill = strength.
            float d0  = depth + 0.012;                       // flat depth
            float xg  = (ax * cy - az * sy) / d0;            // flat ground coords (terrain domain)
            float zg  = (ax * sy + az * cy) / d0 + drive;
            float H   = sin(zg * 0.55 + xg * 0.45) * 0.55    // smooth low-freq hills, 2-D
                      + sin(xg * 0.80 - zg * 0.30 + 1.7) * 0.30
                      + sin(zg * 1.20 + xg * 0.25 + 4.0) * 0.15;
            H        *= uHill * (0.85 + 0.35 * clamp(uPitch, -1.0, 1.0));
            float hk  = 0.24 * smoothstep(0.0, 0.07, depth); // pin the skyline; full strength below it
            float d   = max(d0 * (1.0 - clamp(H, -1.3, 1.3) * hk), 0.004);  // clamp so lines never fold
            float xl   = (ax * cy - az * sy) / d;            // grid coords on the HILLY surface
            float zl   = (ax * sy + az * cy) / d + drive;
            float w    = 0.085 + 0.06 * d;
            float wz   = w + 0.8 * fwidth(zl);            // anti-alias the dense lines toward the horizon
            float wx   = w + 0.8 * fwidth(xl);
            float gz   = smoothstep(wz, 0.0, min(fract(zl), 1.0 - fract(zl)));
            float gx   = smoothstep(wx, 0.0, min(fract(xl), 1.0 - fract(xl)));
            float grid = max(gz, gx);
            float fade = smoothstep(0.0, 0.06, depth);
            grid      *= fade;
            vec3 waterBase = mix(c1, c0, clamp(depth * 1.6, 0.0, 1.0));
            vec3 lineCol   = ramp(0.55 + 0.40 * grid, c0, c1, c2, c3, c4);
            col5 = mix(waterBase, lineCol, grid);
            // the sun MIRRORED in the water — a compressed, ripple-shimmered reflection
            vec2  rp2   = vec2(p.x + rip * 7.0, HOR - depth * 0.6);
            float rhalo = exp(-length(rp2 - sc) * 4.5);
            col5 += mix(c4, c3, 0.4) * rhalo * 0.50 * fade * (1.0 + 0.4 * mBass);
            // a luminous central aisle (sun glow straight down), rippling
            float refl  = exp(-abs(warpP.x + rip * 4.0) * 2.6) * smoothstep(0.02, 0.5, depth);
            col5 += mix(c3, c4, 0.5) * refl * 0.16;
            // wet crest sparkle riding the ripples
            col5 += mix(c3, c4, 0.5)
                  * smoothstep(0.86, 1.0, sin(p.x * 30.0 + p.y * 90.0 - t * 3.0 + rip * 22.0) * 0.5 + 0.5)
                  * fade * 0.10;
            shd = max(max(grid * (1.0 + 0.4 * mBeat), refl * 0.6), rhalo * 0.6);
        } else {                                          // ---- sky + banded sun ----
            float up     = smoothstep(HOR, HOR - 0.8, p.y);   // 0 at horizon → 1 at top
            vec3  skyCol = mix(c1, c0, up);
            // nebula: faint fbm cloud body in the deep sky, palette-tinted
            float neb = fbm(vec2(p.x * 1.4 + 3.0, p.y * 1.8 - t * 0.05));
            skyCol = mix(skyCol, mix(c2, c3, 0.5), neb * neb * 0.18 * up);
            // starfield (twinkling), fading in toward the top
            vec2  sg   = floor((p + 8.0) * 110.0);
            float sr   = hash(sg);
            float star = step(0.987, sr) * (0.4 + 0.6 * sin(t * 4.0 + sr * 40.0));
            skyCol += c4 * max(star, 0.0) * 0.6 * up;
            float sd     = length(p - sc);
            float sy     = clamp((p.y - (sc.y - Rs)) / (2.0 * Rs), 0.0, 1.0);  // 0 top → 1 bottom of disc
            float slit   = smoothstep(0.45, 0.55, fract(sy * 9.0));            // horizontal cuts
            float cut    = mix(1.0, slit, smoothstep(0.40, 1.0, sy));          // only in the lower half
            float disc   = smoothstep(Rs, Rs - 0.012, sd) * cut;
            float halo   = exp(-sd * 5.0);                 // soft outer glow
            vec3  sunCol = mix(c4, c3, sy);
            col5  = mix(skyCol, sunCol, disc);
            col5 += sunCol * halo * 0.40 * (1.0 + 0.4 * mBeat);               // bloom
            shd = max(disc, halo * 0.5);
        }
        // ---- shared atmosphere ----
        float hz = exp(-abs(p.y - HOR) * 8.0);             // hot horizon haze band
        col5 += mix(c3, c4, 0.4) * hz * 0.40;
        shd = max(shd, hz * 0.6);
        col5 *= 1.0 - 0.05 * (0.5 + 0.5 * sin(p.y * 380.0));   // faint CRT scanlines
        shade = clamp(shd, 0.0, 1.0);
        return col5;
    }

    if (style == 6) {
        // --- Vaporwave "Elysium": a raymarched endless pastel-marble COLONNADE
        //     (colonnadeScene) — two rows of fluted columns under an architrave
        //     receding to the slitted Floral-Shoppe sun, over a glossy checkered-
        //     marble floor that mirrors the pastel sky, with an iridescent focal
        //     sphere. Uses the UNWARPED point p (warping a 3-D camera reads as
        //     wobble); cursor lights a floor spotlight, beat pulses the colonnade,
        //     bass swells the sun. Finished with VHS scanlines + vignette.
        vec3 col6 = colonnadeScene(p, mp, curGate, t, c0, c1, c2, c3, c4, mus, shade);
        col6 *= 1.0 - 0.05 * (0.5 + 0.5 * sin(p.y * 220.0));   // VHS scanlines
        float band = smoothstep(0.05, 0.0, abs(fract((p.y + 0.5) - t * 0.08) - 0.5));
        col6 += mix(c3, c4, 0.5) * band * 0.04;                // drifting VHS tracking band
        col6 *= 1.0 - 0.16 * dot(p, p);                        // soft vignette
        return col6;
    }

    if (style == 7) {
        // --- Cyberpunk "Datascape": a raymarched Tron-style neon DATA GRID
        //     (datascapeScene) — a glowing grid floor with data packets racing
        //     along the lines, edge-lit data-blocks, and a neon horizon glow.
        //     Clean/digital, not foggy. Uses the UNWARPED point p; cursor lights
        //     the floor traces, beat ripples the grid, bass lifts the blocks.
        return datascapeScene(p, mp, curGate, t, c0, c1, c2, c3, c4, mus, shade);
    }

    // --- style 0 (default) Flow: domain-warped fbm ribbons that genuinely FLOW
    //     AROUND windows (stones in the stream), idle or not. The trick is that the
    //     advection — not a static coord-warp — carries the routing: a static warp
    //     leaves the temporal derivative (the apparent motion) uniform everywhere,
    //     so it only ever reads as a lens on top of a straight current.
    const float SC = 0.85;
    // --- AgentOS reactivity (spike) ---
    float aBusy = clamp(uAgentBusy, 0.0, 1.0);
    float aWarm = clamp(uAgentWarm, 0.0, 1.0);
    float aSnag = clamp(uAgentSnag, 0.0, 1.0);
    float aPace = (1.0 + 0.8 * aBusy) * (1.0 - 0.5 * aSnag);   // current speed

    // 1) PER-PIXEL VELOCITY field: base diagonal current routed around every window
    //    (see windowFlow). Time-independent -> it routes the flow even when idle.
    vec2 V = windowFlow(p, vec2(0.92, 0.39), t);

    // 2) FLOW-MAP advection along V: each phase scrolls a BOUNDED distance D then
    //    resets; two phases offset by half a cycle, triangle-crossfaded, so the
    //    reset is invisible and the spatial variation in V never accumulates into
    //    scramble. This is what makes the apparent motion curve around windows.
    float D  = 1.0 * (1.0 + 0.5 * mLevel + 0.4 * mBass) * aPace;  // bass reaches further; agent paces it
    float ph = fract(t * 0.6 * aPace);
    float pA = ph, pB = fract(ph + 0.5);
    float wA = 1.0 - abs(2.0 * pA - 1.0);
    float wB = 1.0 - abs(2.0 * pB - 1.0);                 // wA + wB == 1 always
    vec2  b0 = warpP * SC;
    float rxA, rxB;
    float fA = flowField(b0 - V * (pA * D), t, rxA);
    float fB = flowField(b0 - V * (pB * D), t, rxB);
    float f  = fA * wA + fB * wB;
    float rx = rxA * wA + rxB * wB;

    // bass swells the bright field (lowers the threshold so ribbons broaden);
    // working broadens them further.
    shade = smoothstep(0.25 - 0.06 * mBass - 0.10 * aBusy, 0.85, f + 0.12 * rx);
    vec3 ribbon = ramp(shade, c0, c1, c2, c3, c4);
    // mid fills in ribbon body; bass already widened where they reach
    float ribbonAmt = (0.65 + 0.08 * mBass + 0.10 * mMid + 0.10 * aBusy) * smoothstep(0.1, 0.7, f);
    vec3 col = mix(sky, ribbon, ribbonAmt);
    // working: a faint cool rise (lift the bright field a touch).
    col *= (1.0 + 0.06 * aBusy);
    // needs_you: a slow warm breath from lower-centre, riding the brightest crests
    //   — the ONE deliberate warmth (design-ux), localised so foreground stays legible.
    float breath = 0.55 + 0.45 * sin(t * 0.62);
    float lowc   = smoothstep(0.2, -0.7, warpP.y) * exp(-abs(p.x) * 0.9);
    col += vec3(1.00, 0.60, 0.34) * lowc * (0.5 + 0.5 * shade) * aWarm * breath * 0.75;
    // snag: desaturate + slightly dim the ribbons — calm "stalled", never red.
    float snagLuma = dot(col, vec3(0.299, 0.587, 0.114));
    col = mix(col, vec3(snagLuma), 0.40 * aSnag);
    col *= (1.0 - 0.10 * aSnag);
    return col;
}

void main() {
    vec2 frag = qt_TexCoord0 * iResolution;
    // aspect-correct space, centred, ~[-0.x..0.x] x [-0.5..0.5]
    vec2 p = (frag - 0.5 * iResolution) / iResolution.y;

    // ---- musical energy (computed FIRST — it drives the flow and amplifies
    //      every other reaction, so music reads as the scene's life, not a layer)
    float music  = clamp(uMusicReact, 0.0, 1.0);
    // the music packet handed to every style (see baseLook). Four eased drives,
    // each master-gated, with distinct roles: bass=scale, mid=body, level=breath,
    // beat=pulse. Treble is intentionally absent — too fast, it reads as jitter.
    // mScale dials the whole packet to ~1/5 of its former range: at full strength
    // the reaction jumped straight to chaotic. The slider still scales within this
    // gentler ceiling, so music breathes the scene rather than thrashing it.
    float mScale = music * 0.2;
    float mBass  = uBass   * mScale;
    float mMid   = uMid    * mScale;
    float mLevel = uLevel  * mScale;
    // beat is gated to STRONG transients only (weak-beat flicker removed) so the
    // pulse is a deliberate hit shared by the per-style puff AND the light flare
    float mBeat  = smoothstep(0.30, 0.80, uBeat) * mScale;
    vec4  mus    = vec4(mBass, mMid, mLevel, mBeat);
    // one shared "drive" the whole scene responds to (low+mid body — treble is
    // too fast to map to motion without it reading as jitter)
    float energy = clamp(mLevel * 0.55 + mBass * 0.45 + mMid * 0.15, 0.0, 1.0);

    // time: bass nudges the drift just slightly so the low end has a slow surge
    float t = iTime * 0.06 * max(uSpeed, 0.0) * (1.0 + 0.10 * mBass);

    // ---- cursor: a soft bloom that pulls the flow toward the pointer ---------
    vec2 mp = (iMouse * iResolution - 0.5 * iResolution) / iResolution.y;
    float md = length(p - mp);
    float curGate = iMouseActive * clamp(uInteractivity, 0.0, 1.0);
    float bloom = exp(-md * 3.2) * curGate;
    vec2 warpP = p + (mp - p) * bloom * 0.45;            // shared warp field

    // ---- windows are STONES IN THE STREAM: the current can't pass through them, so
    //      near each window we take the flow heading INTO it and route that around
    //      the sides — the field bends past the window, speeds up at its flanks, and
    //      relaxes into a calmer wake behind, exactly like water round a stone. This
    //      warps warpP, so EVERY style's field flows around windows. The edge band
    //      also feeds the light pooling below; the whole thing breathes with music. -
    vec2  fdir    = normalize(vec2(0.92, 0.39));   // base current dir (matches `flow`)
    float react   = clamp(uWinReact, 0.0, 1.0);
    float winGlow = 0.0;
    vec2  winPush = vec2(0.0);
    // Flow-mapped styles (0 Flow, 3 Caustics, 4 Ink) route the flow PROPERLY via a
    // per-pixel velocity + flow-map advection, so the static winPush warp would only
    // be a lens on top — skip computing it for them (kept for Hills/Silk). winGlow is
    // still needed by all styles for the edge light below.
    bool flowMapped = (uStyle == 0 || uStyle == 3 || uStyle == 4);
    if (react > 0.001) {
        for (int i = 0; i < 6; i++) {
            if (i >= uWinCount) break;
            vec4 w = winAt(i);
            if (w.z <= 0.0) continue;
            vec2 c = toP(w.xy + 0.5 * w.zw);
            vec2 h = 0.5 * w.zw * iResolution / iResolution.y;
            float sd = max(sdBox(p, c, h), 0.0);          // 0 inside, grows outside
            if (sd > 1.0) continue;                       // negligible influence -> skip
            float g  = exp(-sd * 5.0);                    // influence hugging the window
            winGlow += g;
            if (flowMapped) continue;                     // winPush unused for these styles
            // split the current here into its outward-normal and tangential parts.
            // Where it heads INTO the stone (fn<0) push it out (blocking the face)
            // and along the tangent (routing round the sides); at the flanks the push
            // is purely tangential (flow accelerates past); downstream it fades to
            // nothing (the wake). Smooth — no sign flips — so the field has no seams.
            vec2  nrm  = normalize(p - c + 1e-4);         // outward from the stone
            float fn   = dot(fdir, nrm);                  // <0 = flow heading into it
            vec2  ftan = fdir - fn * nrm;                 // tangential (around) part
            winPush += (ftan * 1.1 - min(fn, 0.0) * nrm * 1.6) * g;
        }
        warpP += winPush * react * 0.08 * (1.0 + 0.4 * energy) * float(!flowMapped);
    }

    // ---- a MOVING window shoves the fluid, not just trails light: near the window
    //      the flow is dragged along the motion, so the field is caught in its
    //      slipstream (the wake light below then pools into this disturbance). It's
    //      speed-gated, so a parked or slowly-settling window leaves the field be,
    //      and rides the SAME eased uActiveMove as the wake so it fades out as one.
    if (react > 0.001 && uActiveMove > 0.001 && uActiveWin.z > 0.0) {
        vec2  wc   = toP(uActiveWin.xy + 0.5 * uActiveWin.zw);
        vec2  wh   = 0.5 * uActiveWin.zw * iResolution / iResolution.y;
        float wsd  = max(sdBox(p, wc, wh), 0.0);     // 0 inside the window, grows out
        float prox = exp(-wsd * 4.5);                // disturbance hugs the window
        float spd  = length(uActiveVel);
        vec2  vd   = spd > 1e-4 ? uActiveVel / spd : vec2(0.0);
        // sample AGAINST the motion -> the field appears to drag along with the
        // window. (Flip the sign for a bow-wave-ahead read instead of a slipstream.)
        warpP -= vd * prox * uActiveMove * clamp(spd * 2.0, 0.0, 1.0) * react * 0.09;
    }

    // ---- music warps the shared field too: a gentle bass zoom only (the treble
    //      flutter that used to live here read as high-frequency jitter) -------
    warpP *= 1.0 + 0.04 * mBass;

    // ---- persistent reactive feedback (react.frag): cursor trails, music ripples
    //      and window wakes that LINGER and flow after the input stops. Sampled in
    //      screen-uv; its gradient bends the shared flow so excitation pushes the
    //      field outward (ripples/wakes), and the field itself glows below.
    vec2  ruv = qt_TexCoord0;
    vec2  rpx = 1.0 / iResolution;
    vec3  rx  = texture(reactTex, ruv).rgb;     // r excitation · g music · b ripple
    float exc = rx.r;
    vec2  exGrad = vec2(texture(reactTex, ruv + vec2(rpx.x, 0.0)).r - texture(reactTex, ruv - vec2(rpx.x, 0.0)).r,
                        texture(reactTex, ruv + vec2(0.0, rpx.y)).r - texture(reactTex, ruv - vec2(0.0, rpx.y)).r);
    // neon "scene" styles get a stronger flow-displacement so the reaction shows by
    // MOVING their (bright) imagery near the cursor, where an additive glow can't show
    float dgain = (uStyle == 6) ? 3.2 : ((uStyle == 5 || uStyle == 7) ? 1.8 : 1.0);
    warpP += exGrad * (0.5 + 0.5 * energy) * dgain;

    vec3 c0, c1, c2, c3, c4;
    palette(uTheme, c0, c1, c2, c3, c4);
    if (uTheme == 9) {   // user's custom palette overrides the preset
        c0 = uColor0.rgb; c1 = uColor1.rgb; c2 = uColor2.rgb;
        c3 = uColor3.rgb; c4 = uColor4.rgb;
    }

    // The palette above is tuned for the DARK variant. For LIGHT, lift the stops
    // toward white and desaturate so it reads as an airy daytime sky, not washed
    // out. uDark (1 dark, 0 light) blends between the two grades.
    if (uDark < 0.999) {
        // lift toward an airy daytime grade — but MUCH less than before: the old
        // factors (0.82/0.74…) lifted the dark stops almost to the bright ones,
        // collapsing the inter-stop spread so light mode read as flat milk. Lift
        // dark stops least so the gradient keeps its contrast.
        //
        // NEON THEMES (Laserwave 5, Cyberpunk 7, Outrun 8) are neon-on-black by
        // nature — the full daytime lift washes them to pale milk and kills the
        // identity, so scale their lift right down: they stay dark and saturated
        // even under a LIGHT colour scheme. Vaporwave (6) is deliberately excluded
        // — its pastel light variant IS the authentic look, so it lifts normally.
        float lift = (uTheme == 5 || uTheme == 7 || uTheme == 8) ? 0.22 : 1.0;
        c0 = mix(mix(c0, vec3(0.80, 0.85, 0.95), 0.55 * lift), c0, uDark);
        c1 = mix(mix(c1, vec3(0.78, 0.84, 0.95), 0.50 * lift), c1, uDark);
        c2 = mix(mix(c2, vec3(0.84, 0.83, 0.95), 0.42 * lift), c2, uDark);
        c3 = mix(mix(c3, vec3(0.92, 0.82, 0.92), 0.34 * lift), c3, uDark);
        c4 = mix(mix(c4, vec3(1.00, 0.86, 0.78), 0.24 * lift), c4, uDark);
    }

    // Shared reactive accents, drawn FROM the live palette so every response —
    // cursor, window, beat — reads as the same aurora light, never a foreign blob.
    vec3 accentWarm = mix(c4, vec3(1.0, 0.93, 0.82), 0.30);
    vec3 accentCool = mix(c2, c3, 0.5);

    // ---- BASE LOOK: draw the background field for the selected style. Returns
    //      the colour and a 0..1 `shade` (where the aurora is bright) that the
    //      shared reactive light below rides. Everything after here is shared.
    float shade;
    vec3 col = baseLook(uStyle, warpP, p, mp, curGate, t, c0, c1, c2, c3, c4, mus, shade);

    // depth: in dark mode darken troughs for contrast; in light mode keep it airy
    col *= mix(0.97, 0.86, uDark) + mix(0.10, 0.26, uDark) * shade;
    // loudness gently swells the whole scene's luminance (subtle, breathing)
    col *= 1.0 + 0.10 * mLevel;

    // ---- ONE additive light field shared by every reaction ------------------
    // Accumulate all reactive light here, in the shared accent palette, then
    // screen-blend it once. Overlapping responses merge smoothly and never clip
    // to white — THIS is what makes the effects flow into one another instead of
    // stacking as separate coloured glows.
    vec3 light = vec3(0.0);

    // cursor warmth (soft bloom + a tighter core); brighter while the music's up
    light += accentWarm * (bloom * (0.30 + 0.22 * shade)
                         + exp(-md * 9.0) * curGate * 0.22) * (1.0 + 0.25 * energy);

    if (react > 0.001) {
        // aurora light leaking around every window edge, breathing with energy
        light += accentCool * winGlow * react * 0.05 * (0.85 + 0.4 * energy);

        // the dragged window trails a glowing wake — same accent family, so it
        // belongs to the scene rather than reading as a separate comet
        if (uActiveMove > 0.001 && uActiveWin.z > 0.0) {
            vec2  c  = toP(uActiveWin.xy + 0.5 * uActiveWin.zw);
            vec2  h  = 0.5 * uActiveWin.zw * iResolution / iResolution.y;
            float sd = max(sdBox(p, c, h), 0.0);
            float speed = length(uActiveVel);
            vec2  vdir  = speed > 1e-4 ? uActiveVel / speed : vec2(0.0);
            // points behind the motion (opposite the velocity) light up the most
            float behind = clamp(dot(normalize(p - c + 1e-4), -vdir), 0.0, 1.0);
            float ring = exp(-sd * 8.0) * uActiveMove;                 // rim all round
            float wake = exp(-sd * 3.2) * behind * uActiveMove
                       * clamp(speed * 1.5, 0.0, 1.0);                 // trailing tail
            light += (ring * 0.14 + wake * 0.38) * mix(accentCool, accentWarm, 0.5) * react;
        }
    }

    if (music > 0.001) {
        float cd = length(p);
        // bass: a slow warm swell breathing from the centre, riding the ribbons
        light += accentWarm * exp(-cd * 1.7) * mBass * 0.15 * (0.5 + shade);
        // beat: the same gated pulse the styles use (mBeat) gives a soft flare
        // through the aurora's own bright field, so the light flash and the
        // per-style structural puff fire together as one hit, then settle back
        light += accentWarm * mBeat * smoothstep(0.45, 0.9, shade) * 0.16;
        // (treble shimmer/flutter removed entirely — the per-pixel sparkle and the
        //  fast warp were the main source of "chaos"; treble no longer drives any
        //  motion, so the scene stays a slow breathing field)
    }

    // ---- persistent reactive feedback glow: the lingering trails / ripples /
    //      wakes from react.frag, drawn in the same accent palette so they read as
    //      the aurora's own light. This is the "wow" layer — it flows on AFTER the
    //      cursor stops, beats ripple outward, and window wakes drift and fade.
    // The neon "scene" styles (Laserwave 5, Vaporwave 6, Cyberpunk 7) draw bright,
    // busy imagery that visually swamps a soft additive glow — and the screen-blend
    // below suppresses light where the scene is already bright. Give them a stronger
    // reactive gain so the cursor/beat reads as clearly as on the soft styles.
    float rgain = (uStyle == 6) ? 2.4 : ((uStyle == 5 || uStyle == 7) ? 1.8 : 1.0);
    light += mix(accentWarm, accentCool, 0.4) * exc * (0.30 + 0.30 * shade) * rgain;
    light += accentCool * rx.b * 0.5 * rgain;   // expanding beat ripple ring
    col   *= 1.0 + rx.g * 0.7;                   // music level/bass throb swells the field

    // Hue-preserving highlight guard. The reactive feedback `exc` accumulates in
    // react.frag (decay ~0.95, clamped to 8), so a DWELLING dragged window builds a
    // large value; multiplied by rgain it over-drives `light` far past 1, and the
    // screen-blend below then clips every channel to 1.0 — a WHITE blob that swamps
    // the accent tint. Scale the whole additive field down by its own peak channel
    // so an over-driven reaction saturates toward its accent COLOUR (channel ratios
    // held) instead of going white. A no-op below 1.0 — ordinary cursor/beat glows
    // (which sit well under 1) are untouched; only the blow-out case is reined in.
    light *= 1.0 / max(max(max(light.r, light.g), light.b), 1.0);

    // screen-blend the shared light so layered responses combine gracefully
    col = col + light * (1.0 - col);
    // Tint toward the accent at the excitation — the only reactive lever that shows on
    // BRIGHT scenes (additive glow is screen-blend-suppressed; warp does nothing for a
    // style that draws from p). Vaporwave (6) is bright pastel AND warp-independent, so
    // it leans entirely on this: push it hard, toward the saturated cool accent so it
    // reads against the pastel.
    vec3  rtint   = (uStyle == 6) ? accentCool : accentWarm;
    float tintAmt = clamp(exc, 0.0, 1.0) * ((uStyle == 6) ? 0.55 : 0.22 * (rgain - 1.0));
    col = mix(col, rtint, tintAmt);

    // intensity: scale saturation around luma (1 = unchanged, 0 = greyscale)
    float luma = dot(col, vec3(0.299, 0.587, 0.114));
    col = mix(vec3(luma), col, clamp(uIntensity, 0.0, 2.0));

    // dither to kill 8-bit banding on the smooth gradient
    col += (hash(frag + iTime) - 0.5) / 255.0;

    fragColor = vec4(clamp(col, 0.0, 1.0), 1.0) * qt_Opacity;
}

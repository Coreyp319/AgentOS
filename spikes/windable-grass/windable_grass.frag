#version 440
//
// SPIKE: "windable-grass" — a painterly, waving AMBER FIELD whose WIND is a live
// procedural uniform. The live/interactive mode of the creative pipeline
// (docs/design/0023-creative-environment-pipeline.md, canonical first sample).
//
// ADR-0009 reconciliation (docs/adr/0009): the procedural shader is the PRIMARY live
// renderer. A generated render (Blender EEVEE field) supplies only the *look* — an
// amber painterly palette/structure — sampled here as `dreamTex` (dream-as-texture).
// The MOTION is procedural in the shader, carried by `windDir`/`gust`, so the wind can
// actually REDIRECT live; a baked loop bakes ONE wind direction (exactly ADR-0009's
// argument against video carrying the signal).
//
// PAINTERLY AS STRUCTURE (ADR-0023 P2.9, art-director dissent). The old look was
// "default game-grass + a posterize filter": per-blade high-freq tonal scatter under a
// global palette-snap, plus a smooth two-tone gradient sky. That reads as a FILTER.
// References (art-director's bar): David Holland's BotW/Totoro UE4 meadow + the 80.lv
// Ghibli-in-Blender breakdowns. The painterly recipe both teach is STRUCTURAL, not a
// post-pass: (1) 1-2-tone colormaps — color lives in FLATTENED REGIONS, not per-pixel;
// (2) "removed all but the slightest roughness" — kill per-pixel brightness scatter so
// patches read FLAT; (3) large-scale blurred noise → a gradient drives region color
// (subtle, smooth transitions); (4) simple painted CLOUD shapes behind the scene, not a
// smooth gradient sky; (5) directional brush grain. So here:
//   - a low-frequency REGION field quantises the field into a few painted color zones
//     (tip-weighted blade clumps inherit their region's color + a 2-level tone), so color
//     varies in PATCHES, never per-blade;
//   - the green note #9bb04a is a COOL REGION played against the warm amber regions
//     (region contrast), never a uniform tint;
//   - brush grain is sampled ALONG the wind direction (anisotropic → visible directional
//     strokes that lean with the wind);
//   - the sky is a SCUMBLED 2-TONE cloud band (pale over light, broken by soft noise),
//     not the generic flat gradient.
//
// THE IDLE INVARIANT (load-bearing, ADR-0009 contract): when every signal uniform is
// zero — windDir = (0,0), gust = 0, the agent floats 0, uStale = 0, uReducedMotion = 0 —
// the field resolves to a NEUTRAL resting motion (a slow ambient sway, NOT still), and
// crucially nothing in the look depends on the signal terms. The signal terms are guarded
// ADDITIVES that collapse to identity at 0 — no constant-term leak. (We diff a fixed-iTime
// all-zero capture to prove it: windable_grass_idle.png is the baseline; a redirect only
// adds bow. uReducedMotion only DAMPS signal-driven motion — at idle there is none to damp,
// so it too is identity. uStale is a guarded additive grade — identity at 0.)
//
// Palette (locked, the brief's): #b8862f #e3c46a #f4e3a1 #7d5e22 #9bb04a
//   amber-mid / amber-light / amber-pale / amber-shadow / green-base-note
//
layout(location = 0) in  vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4  qt_Matrix;
    float qt_Opacity;
    float iTime;
    vec2  iResolution;
    // --- LIVE WIND SIGNAL (the new sibling uniforms; design-0023 Interactivity) -------
    // windDir : unit-ish vector, the direction the field BOWS. (0,0) => neutral.
    //           Producer maps it from the last window-drag vector (eased by feed.rs spring).
    // gust    : 0..1 extra bend amplitude + ripple. Maps from drag SPEED (eased). 0 => calm.
    vec2  windDir;
    float gust;
    // master gate for the live signal (config; identical role to uMusicReact/uAgentReact).
    // 0 => the wind signal is fully inert (only the neutral ambient sway remains).
    float uWindReact;
    // --- agent reactivity (the SAME contract as aurora.frag:63-69 / dream_field.frag) --
    int   uAgentState;   // 0 idle · 1 working · 2 needs_you · 4 snag
    float uAgentBusy;    // 0..1
    float uAgentWarm;    // 0..1   (the ONE warm source)
    float uAgentSnag;    // 0..1
    // dreamTex presence: 0 => pure procedural look (fallback); 1 => warp+grade the render.
    float uDreamMix;
    // --- a11y / liveness (ADR-0023 P2.12) ----------------------------------------------
    // uReducedMotion : 0..1 motion-sensitivity damp. DAMPS gust amplitude + ripple + the
    //                  depth parallax of the brush grain. At 1 the field still SWAYS (the
    //                  neutral baseSway is barely touched — calm-rest is preserved) but the
    //                  signal-driven motion is largely stilled. Guarded: at idle there is no
    //                  signal motion, so this is identity vs the all-zero baseline.
    float uReducedMotion;
    // uStale : 0..1 producer-dead / stale-feed grade. A DISTINCT look from calm idle: a
    //          cool desaturated wash + a faint vignette so "I can't read the desktop" can
    //          never masquerade as serene idle. Guarded additive — identity at 0.
    float uStale;
};

// The dream: a generated amber-field render (Blender EEVEE, palette-clamped + painterly
// post-grade). In real QML this is a still PNG or a VideoOutput → ShaderEffectSource, wired
// exactly like reactTex/dreamTex today. Falls back to procedural when uDreamMix == 0.
layout(binding = 1) uniform sampler2D dreamTex;

// ----------------------------------------------------------------------------------------
// cheap value noise + fbm (2 octaves; the dream supplies high-freq richness when present)
float hash21(vec2 p){ p = fract(p*vec2(123.34,345.45)); p += dot(p, p+34.345); return fract(p.x*p.y); }
float vnoise(vec2 p){
    vec2 i = floor(p), f = fract(p);
    vec2 u = f*f*(3.0-2.0*f);
    float a = hash21(i), b = hash21(i+vec2(1,0)), c = hash21(i+vec2(0,1)), d = hash21(i+vec2(1,1));
    return mix(mix(a,b,u.x), mix(c,d,u.x), u.y);
}
float fbm(vec2 p){
    float s = 0.0, a = 0.55;
    for (int i=0;i<2;i++){ s += a*vnoise(p); p *= 2.03; a *= 0.5; }
    return s;
}

// locked palette
const vec3 P_AMBER_MID    = vec3(0.722, 0.525, 0.184); // #b8862f
const vec3 P_AMBER_LIGHT  = vec3(0.890, 0.769, 0.416); // #e3c46a
const vec3 P_AMBER_PALE   = vec3(0.957, 0.890, 0.631); // #f4e3a1
const vec3 P_AMBER_SHADOW = vec3(0.490, 0.369, 0.133); // #7d5e22
const vec3 P_GREEN_BASE   = vec3(0.608, 0.690, 0.290); // #9bb04a

// palette-reduce: snap a colour toward the nearest locked swatch by `amount` (painterly
// posterise toward the brief's amber set). amount=0 => identity. (Still used as a gentle
// FINISH on the dream-as-texture path — the procedural path now builds its color from the
// regions directly, so it does not need this snap.)
vec3 paletteReduce(vec3 c, float amount){
    vec3 sw[5];
    sw[0]=P_AMBER_MID; sw[1]=P_AMBER_LIGHT; sw[2]=P_AMBER_PALE; sw[3]=P_AMBER_SHADOW; sw[4]=P_GREEN_BASE;
    float bestD = 1e9; vec3 best = c;
    for (int i=0;i<5;i++){ float d = dot(c-sw[i], c-sw[i]); if (d<bestD){ bestD=d; best=sw[i]; } }
    return mix(c, best, amount);
}

// ----------------------------------------------------------------------------------------
// REGION COLOR (painterly-as-structure core). A flattened color zone: a large-scale
// blurred-noise field picks among a SMALL set of palette tones — amber regions plus the
// cool green note as a contrasting region — then a SECOND low-freq field adds a single
// 2-tone light/shadow level inside the region. This is the "1-2 tone colormap, removed
// roughness" recipe: color lives in patches, with smooth transitions and NO per-pixel
// brightness scatter. `rc` in [0,1) selects the region; `tone` is the in-region 2-level.
vec3 regionColor(float rc, float tone){
    // four flattened color zones across the field. Warm ambers DOMINATE; the green note is
    // ONE cool region (region contrast, not a tint), kept low-contrast against amber — its
    // band is the NARROWEST and its saturation is pulled gently toward amber so it reads as
    // a cool NOTE within the field, not a separate plant (the art-director's "contrast, not
    // dominance"). The shadow region is softened toward mid so dark patches don't silhouette.
    vec3 zone;
    if      (rc < 0.38) zone = P_AMBER_MID;                          // the body amber
    else if (rc < 0.66) zone = P_AMBER_LIGHT;                        // a lit amber patch
    else if (rc < 0.80) zone = mix(P_GREEN_BASE, P_AMBER_MID, 0.22); // cool green NOTE (narrow)
    else                zone = mix(P_AMBER_SHADOW, P_AMBER_MID, 0.25);// a softened shadow patch
    // 2-tone within the region: a single quantised light/dark step (not a continuous ramp),
    // so the patch reads FLAT. tone>0.5 => the lit tone, else the base tone. Low contrast.
    float step2 = step(0.5, tone);
    return mix(zone * 0.90, mix(zone, P_AMBER_PALE, 0.18), step2);
}

void main() {
    vec2 uv = qt_TexCoord0;            // 0..1, origin top-left in Qt
    vec2 p  = vec2(uv.x, 1.0 - uv.y);  // flip so y grows UP (sky high, soil low)
    float aspect = max(iResolution.x, 1.0) / max(iResolution.y, 1.0);

    // === signal gates: every term collapses to identity at 0 (the idle invariant) ======
    float wg    = clamp(uWindReact, 0.0, 1.0);
    vec2  wind  = windDir * wg;                       // (0,0) at idle
    float windLen = length(wind);
    float g     = clamp(gust, 0.0, 1.0) * wg;         // 0 at idle
    float aBusy = clamp(uAgentBusy, 0.0, 1.0);
    float aWarm = clamp(uAgentWarm, 0.0, 1.0);
    float aSnag = clamp(uAgentSnag, 0.0, 1.0);
    float rm    = clamp(uReducedMotion, 0.0, 1.0);    // motion damp; 0 at idle
    float stale = clamp(uStale, 0.0, 1.0);            // producer-dead grade; 0 at idle
    // motion scale: reduced-motion damps SIGNAL-driven motion (gust/ripple/parallax) toward
    // a near-still field. At idle (g=0, windLen=0) every term it multiplies is already 0,
    // so this is identity vs the all-zero baseline.
    float mo    = 1.0 - 0.85 * rm;                    // 1 normally, 0.15 fully reduced

    // === NEUTRAL ambient sway — this is the resting motion at idle (NOT signal-driven) ==
    // A tiny, slow, omni-directional breathing of the blades. This is what "byte-identical
    // idle" looks like: alive but with zero directional bias, identical regardless of any
    // signal because it reads NONE of the signal terms. Working speeds it (pace), but pace
    // is multiplicative => at aBusy=0 it is the bare neutral rate.
    float pace   = 1.0 + 0.8 * aBusy;                 // working: same scene, running harder
    float baseSway = 0.012 * sin(iTime * 0.55 * pace + p.x * 3.0);

    // === procedural blade field ========================================================
    // Horizon: distance up the screen. Blades get shorter / hazier toward the horizon.
    float horizon = smoothstep(0.78, 0.30, p.y);      // 1 in foreground, 0 at sky
    float depth   = p.y;                              // 0 fg .. 1 far

    // Per-column blade phase: a dense vertical comb of blades via a high-freq x coordinate.
    float bladeX = p.x * (90.0 + 40.0 * (1.0 - depth)) * aspect;
    float col    = floor(bladeX);
    float within = fract(bladeX) - 0.5;               // -0.5..0.5 across one blade
    float jitter = hash21(vec2(col, 7.0));            // per-blade randomness (seed-friendly)

    // === WIND BEND (procedural, the live signal) =======================================
    // Each blade bends in the wind DIRECTION, more toward the tip (higher p.y within a
    // tuft). Bend = neutral sway  +  directional gust. At wind=(0,0),g=0 the directional
    // term is exactly 0 => only baseSway remains => identity with idle. Reduced-motion (mo)
    // damps the gust ripple — a window-drag still bows the field but the jitter calms.
    float tipWeight = smoothstep(0.0, 1.0, fract(p.y * 4.0 + jitter)); // along-blade height
    float gustWave  = sin(iTime * (1.1*pace) + p.x * 6.0 + jitter*6.28);
    float bendAmt   = baseSway                                        // neutral resting sway
                    + wind.x * (0.06 + 0.10*g) * tipWeight            // directional bow (x)
                    + windLen * 0.04 * g * gustWave * tipWeight * mo; // gust ripple (damped)
    // sample the blade comb at a wind-displaced x: this is the field visibly "bowing".
    float bentX  = within + bendAmt * (8.0 + 6.0*g);

    // blade mask: a soft vertical stroke, broken into tufts by along-blade falloff.
    float blade  = smoothstep(0.42, 0.0, abs(bentX));
    float tuft   = smoothstep(1.05, 0.2, fract(p.y*4.0 + jitter) + (1.0-horizon)*0.6);
    float bladeM = blade * tuft * horizon;

    // === PAINTERLY REGIONS (the structural look — see header) ==========================
    // 1) A large-scale, BLURRED region field. Low frequency => big patches; advected very
    //    slightly along the wind so patches "lean" with a redirect (the parallax term that
    //    reduced-motion damps). This is the Substance "blurred noise → gradient" move.
    vec2  windNrm  = windLen > 1e-4 ? wind / windLen : vec2(0.0);
    float parallax = (windLen * 0.04 * g) * mo;                  // tiny, signal-only, damped
    vec2  regUV    = vec2(p.x * 2.2 * aspect, p.y * 1.9) + windNrm * parallax;
    // big patches + a fainter finer octave so region BOUNDARIES scumble (soft, blurred
    // transitions) rather than snapping — the painterly "smooth transitions" recipe.
    float regBig   = fbm(regUV) * 0.82 + fbm(regUV * 2.7 + 5.0) * 0.18;
    // 2) Clump blades into patches: nearby columns share a region by quantising on a coarse
    //    column index, so a CLUMP of blades inherits one region color (not per-blade noise).
    float clump    = hash21(vec2(floor(col / 6.0), 3.0));        // per-6-blade-clump id
    float rc       = fract(regBig + clump * 0.14);               // region selector 0..1
    // 3) The in-region 2-tone level: a second low-freq field, quantised. Tip-lit patches
    //    skew toward the light tone (a gentle directional read), still 2-level (flat).
    float toneFld  = fbm(regUV * 1.7 + 11.3) * 0.7 + tipWeight * 0.5 * horizon;
    vec3  grass    = regionColor(rc, toneFld);
    // 4) DIRECTIONAL BRUSH GRAIN aligned to the wind. Sampled along the wind axis so the
    //    strokes lean with a redirect; at idle (windNrm=0) it falls back to the field's own
    //    vertical grain — a fixed, neutral canvas texture (no signal, identity-safe).
    vec2  brushDir = windLen > 1e-4 ? windNrm : vec2(0.18, 1.0);
    vec2  brushPerp= vec2(-brushDir.y, brushDir.x);
    vec2  bp       = vec2(dot(p, brushDir) * 5.0, dot(p, brushPerp) * 30.0 * aspect);
    float brush    = fbm(bp + vec2(0.0, bendAmt * 3.0));         // streaks across the stroke
    grass         *= (0.92 + 0.10 * brush);                      // gentle, NOT per-pixel scatter

    // Sky / soil base. The SOIL keeps a flattened amber, region-tinted at the base.
    vec3  soil   = mix(P_AMBER_SHADOW, P_AMBER_MID, depth);
    soil         = mix(soil, regionColor(rc, 0.0), 0.35);        // patchy soil, on-region

    // === SCUMBLED 2-TONE SKY (replaces the generic flat gradient) =======================
    // Two palette tones scumbled into soft horizontal cloud bands. A cool olive-green note
    // sits low at the horizon (region contrast with the warm field); pale amber clouds
    // drift over a light-amber sky. Broken by soft noise so it reads as PAINTED cloud
    // shapes, not a smooth ramp. Drift is a fixed ambient pace (NOT signal) so the sky is
    // alive at idle too and stays identity-safe; reduced-motion does not still the sky (a
    // calm horizon is not the motion that triggers vestibular discomfort).
    float skyT     = smoothstep(0.30, 1.0, p.y);
    vec3  skyBase  = mix(mix(P_GREEN_BASE, P_AMBER_LIGHT, 0.55), P_AMBER_LIGHT, skyT);
    // two scumbled cloud bands: low-freq horizontal noise, quantised to soft 2-tone shapes.
    float cloudN1  = fbm(vec2(p.x * 2.2 * aspect - iTime * 0.010, p.y * 3.5 + 4.0));
    float cloudN2  = fbm(vec2(p.x * 3.6 * aspect + iTime * 0.006, p.y * 5.0 + 19.0));
    float band     = smoothstep(0.52, 0.74, cloudN1 + 0.12 * sin(p.x * 5.0));
    float wisp      = smoothstep(0.58, 0.80, cloudN2);
    vec3  sky      = skyBase;
    sky            = mix(sky, P_AMBER_PALE, band * 0.55 * skyT);   // pale cloud body
    sky            = mix(sky, P_AMBER_LIGHT, wisp * 0.30 * skyT);  // a second, lighter tone
    // keep the sky low-contrast (painterly): pull it gently toward its own mean.
    sky            = mix(sky, vec3(dot(sky, vec3(0.33))), 0.10);

    // === compose the procedural painting ===============================================
    vec3 proc    = mix(soil, sky, smoothstep(0.30, 0.85, p.y));
    proc         = mix(proc, grass, bladeM);
    // a faint, EVEN canvas tooth (low amplitude, not the old per-pixel posterize scatter).
    proc        += (fbm(p * vec2(120.0, 150.0)) - 0.5) * 0.018;

    // === dream-as-texture: warp the generated render by the SAME wind, then grade =======
    // The render carries the LOOK; the wind here is the live MOTION applied to it. The
    // displacement uses the same bend so the baked field bows with the live signal.
    vec2 warp = vec2(bendAmt * 0.5, baseSway * 0.3);
    vec3 dream = texture(dreamTex, uv + warp).rgb;
    dream      = paletteReduce(dream, 0.18);                     // keep it on-palette (gentle)

    // choose source: procedural fallback (uDreamMix=0) ↔ graded dream (uDreamMix=1).
    vec3 col3 = mix(proc, dream, clamp(uDreamMix, 0.0, 1.0));

    // === AGENT GRAMMAR (identical roles to the committed Hills/Flow shader) =============
    // working: a hair brighter (capped, multiplicative, identity at 0). Pace already
    // applied to motion above.
    col3 *= (1.0 + 0.07 * aBusy);

    // needs_you: the ONE warm source — additive dawn glow under a HIGHLIGHT GUARD so
    // working + warm (+ a future loud-music feed) can't compound past white. Same dawn RGB
    // as the committed grammar. Localised low+centre. Identity at aWarm=0.
    float breath   = 0.55 + 0.45 * sin(iTime * 0.62);
    float lowGlow  = smoothstep(0.40, -0.10, p.y) * exp(-pow((uv.x-0.5)*2.2, 2.0));
    float headroom = clamp(1.0 - max(col3.r, max(col3.g, col3.b)), 0.0, 1.0); // GUARD
    col3 += vec3(1.00, 0.60, 0.34) * lowGlow * aWarm * breath * 0.42 * headroom;

    // snag: desaturate + dim, never red. Identity at aSnag=0.
    float luma = dot(col3, vec3(0.299, 0.587, 0.114));
    col3 = mix(col3, vec3(luma), 0.35 * aSnag);
    col3 *= (1.0 - 0.12 * aSnag);

    // === STALE / PRODUCER-DEAD (ADR-0023 P2.12) ========================================
    // A DISTINCT look from calm idle: a cool, desaturated wash + a soft vignette so a dead
    // producer ("I can't read the desktop") never reads as serene idle. Cool (toward the
    // olive-grey of the sky mean), NOT warm and NOT red — warmth is reserved for needs_you.
    // Guarded additive: at uStale=0 every term is multiplied/mixed by 0 => identity.
    float sLuma  = dot(col3, vec3(0.299, 0.587, 0.114));
    vec3  sCool  = mix(vec3(sLuma), vec3(0.52, 0.55, 0.50), 0.35);   // desaturated cool grey
    float vign   = 1.0 - 0.45 * smoothstep(0.35, 1.05, length((uv - 0.5) * vec2(aspect, 1.0)));
    col3 = mix(col3, sCool, 0.55 * stale);                          // desaturate toward cool
    col3 *= mix(1.0, vign, stale);                                  // vignette only when stale

    fragColor = vec4(clamp(col3, 0.0, 1.0) * qt_Opacity, 1.0);
}

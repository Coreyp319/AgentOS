#version 440
//
// SPIKE: "dream-as-texture" — generated video as a TEXTURE INPUT to a procedural
// system, NOT as the output medium. The counter-direction to ADR-0008's
// "video loop keyed by discretized state".
//
// Premise: a ComfyUI/Wan clip is a slowly-evolving COLOUR/STRUCTURE source
// (`dreamTex`, a looping mp4 sampled per-frame). The LIVE continuous floats
// {busy,warm,snag} drive a procedural domain-warp + the SAME additive highlight
// guard the Hills/Flow grammar already uses — so the agent signal is expressed
// frame-by-frame on top of the dream, never by *which* clip plays.
//
// The point this proves: the richness ADR-0008 wants video for (model-generated
// scenes) and the frame-by-frame float responsiveness the embodiment grammar
// requires are NOT in tension — you keep both if the video is the SUBSTRATE the
// procedural layer warps, not the deliverable. idle (all-zero) is byte-identical
// to "dream played untouched": every agent term below collapses to identity at 0.
//
layout(location = 0) in  vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4  qt_Matrix;
    float qt_Opacity;
    float iTime;
    vec2  iResolution;
    // --- agent reactivity (the SAME contract as aurora.frag:63-69) ----------
    int   uAgentState;   // 0 idle · 1 working · 2 needs_you · 4 snag
    float uAgentBusy;    // 0..1
    float uAgentWarm;    // 0..1   (the ONE warm source)
    float uAgentSnag;    // 0..1
    // config (master gates, identical role to uMusicReact)
    float uAgentReact;   // 0..1 master response (config); 0 => fully inert
};

// The dream: a looping generated clip, sampled as an evolving field. In the real
// QML this is a VideoOutput → ShaderEffectSource feeding a sampler2D, exactly how
// reactTex is wired today. In the spike harness it's a still or a short loop.
layout(binding = 1) uniform sampler2D dreamTex;

// --- cheap value-noise warp (1 octave; the dream supplies the richness) -------
float hash(vec2 p){ p=fract(p*vec2(123.34,345.45)); p+=dot(p,p+34.345); return fract(p.x*p.y); }
float vnoise(vec2 p){
    vec2 i=floor(p), f=fract(p); vec2 u=f*f*(3.0-2.0*f);
    float a=hash(i), b=hash(i+vec2(1,0)), c=hash(i+vec2(0,1)), d=hash(i+vec2(1,1));
    return mix(mix(a,b,u.x),mix(c,d,u.x),u.y);
}

void main() {
    vec2 uv = qt_TexCoord0;

    // === ALL agent terms collapse to identity at 0 (the idle invariant) =======
    float g    = clamp(uAgentReact, 0.0, 1.0);   // master gate
    float aBusy = clamp(uAgentBusy, 0.0, 1.0) * g;
    float aWarm = clamp(uAgentWarm, 0.0, 1.0) * g;
    float aSnag = clamp(uAgentSnag, 0.0, 1.0) * g;

    // --- working: a slow procedural domain-warp of the dream, scaled by busy.
    // The dream itself never speeds up (it's a fixed clip); the WARP does. This
    // is the "same scene, running harder" rule from vision.md:94, applied to a
    // texture instead of to ridge geometry. At aBusy=0 the warp vanishes -> the
    // dream is sampled at its true uv (identity).
    float pace = 1.0 + 0.9 * aBusy;
    vec2  w    = vec2(
        vnoise(uv * 3.0 + vec2(iTime * 0.05 * pace, 0.0)),
        vnoise(uv * 3.0 + vec2(0.0, iTime * 0.05 * pace + 11.0))
    ) - 0.5;
    // snag stills the warp further (flow below idle, vision.md:97).
    float warpAmt = (0.018 * aBusy) * (1.0 - 0.6 * aSnag);
    vec2  duv = uv + w * warpAmt;

    vec3 dream = texture(dreamTex, duv).rgb;

    // --- working: a touch brighter + a hair more bright-field (mirrors
    // aurora.frag:704-705). Capped, multiplicative, identity at 0.
    dream *= (1.0 + 0.08 * aBusy);

    // --- needs_you: the ONE warm source — additive, under a highlight guard so
    // working + warm + a loud-music feed could not compound past white. Same
    // dawn RGB as the committed grammar (aurora.frag:713). Localised low+centre
    // so foreground stays legible. Identity at aWarm=0.
    float breath  = 0.55 + 0.45 * sin(iTime * 0.62);
    float lowGlow = smoothstep(0.30, -0.20, uv.y) * exp(-pow((uv.x-0.5)*2.2, 2.0));
    float headroom = clamp(1.0 - max(dream.r, max(dream.g, dream.b)), 0.0, 1.0); // GUARD
    dream += vec3(1.00, 0.60, 0.34) * lowGlow * aWarm * breath * 0.42 * headroom;

    // --- snag: desaturate + dim, never red (aurora.frag:716-719). Identity at 0.
    float luma = dot(dream, vec3(0.299, 0.587, 0.114));
    dream = mix(dream, vec3(luma), 0.35 * aSnag);
    dream *= (1.0 - 0.12 * aSnag);

    fragColor = vec4(clamp(dream, 0.0, 1.0), 1.0);
}

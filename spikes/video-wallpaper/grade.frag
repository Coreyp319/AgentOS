#version 440
// Minimal agent.json grade over a decoding video frame.
// Mirrors aurora.frag's snag/warm/working knobs: idle (all uniforms 0) = byte-identical passthrough.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(binding = 1) uniform sampler2D src;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float uBusy;
    float uWarm;
    float uSnag;
};
void main() {
    vec4 c = texture(src, qt_TexCoord0);
    // snag: desaturate + dim (never red) — same law as aurora.frag
    float luma = dot(c.rgb, vec3(0.2126, 0.7152, 0.0722));
    c.rgb = mix(c.rgb, vec3(luma), uSnag * 0.7);
    c.rgb *= (1.0 - uSnag * 0.25);
    // warm: additive low warm glow (the one warmth)
    c.rgb += vec3(0.10, 0.05, 0.0) * uWarm;
    // busy handled by playbackRate on the QML side; keep additive here too (subtle sharpen-free lift)
    c.rgb *= (1.0 + uBusy * 0.04);
    fragColor = c * qt_Opacity;
}

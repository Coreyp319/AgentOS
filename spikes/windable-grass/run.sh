#!/usr/bin/env bash
# Render the windable amber grass field across a few wind directions + agent states by
# hand-writing the signal shim and capturing a PNG each. Fixed iTime in the harness means
# frames differ ONLY by the signal uniforms.
#
# Run ON the live session (offscreen has no GL context here -> blank frames; see
# spikes/hills-reactive/README.md). Recompile the shader first if you edited the .frag.
set -u
cd "$(dirname "$0")"
QML="$(command -v qml6 || command -v qml)"
[ -n "$QML" ] || { echo "no qml6/qml runtime"; exit 1; }
QSB="${QSB:-/usr/lib/qt6/bin/qsb}"

# (re)compile the shader if qsb is available (it lives off-PATH at /usr/lib/qt6/bin/qsb)
if [ -x "$QSB" ]; then
  "$QSB" --qt6 -o windable_grass.frag.qsb windable_grass.frag \
    && echo "compiled windable_grass.frag.qsb"
fi

# wind_data.js shape (the harness reads these as the uniform values):
#   windX, windY  : wind vector components (the bow direction)
#   gust          : 0..1 gust strength (extra bend + ripple)
#   windReact     : 0..1 master gate for the wind signal (config)
#   state/busy/warm/snag : the shared agent contract
#   dreamMix      : 0 procedural look | 1 dream-as-texture
#   reducedMotion : 0..1 prefers-reduced-motion damp (ADR-0023 P2.12)
#   stale         : 0..1 producer-dead / stale-feed grade (ADR-0023 P2.12)
emit() {
  cat > wind_data.js <<EOF
var windX = ${1}
var windY = ${2}
var gust = ${3}
var windReact = ${4}
var state = ${5}
var busy = ${6}
var warm = ${7}
var snag = ${8}
var dreamMix = ${9}
var reducedMotion = ${10:-0}
var stale = ${11:-0}
EOF
}

run_one() {
  local name="$1"
  rm -f out.png
  timeout 25 "$QML" harness.qml >"log_$name.txt" 2>&1
  if [ -s out.png ]; then
    mv out.png "grass_${name}.png"
    echo "  $name -> grass_${name}.png ($(stat -c%s "grass_${name}.png") bytes)"
  else
    echo "  $name -> FAILED (see log_$name.txt)"; tail -4 "log_$name.txt" | sed 's/^/      /'
  fi
}

echo "rendering windable amber field:"
#     windX windY gust react state busy warm snag dreamMix [reducedMotion stale]
emit  0     0     0    0     0     0    0    0    0            ; run_one idle           # IDLE: neutral sway, no bias
emit -0.9   0.05  0.35 1     0     0    0    0    0            ; run_one wind_left      # drag a window LEFT -> field bows left
emit  0.9   0.05  0.35 1     0     0    0    0    0            ; run_one wind_right     # drag RIGHT -> field bows right
emit  0.5  -0.5   0.9  1     0     0    0    0    0            ; run_one wind_gust      # fast diagonal drag -> strong gust
emit -0.6   0.0   0.5  1     1     0.85 0    0    0            ; run_one wind_working   # wind + working pace
emit  0.4   0.1   0.3  1     2     0    0.9  0    0            ; run_one wind_needsyou  # wind + the ONE warm glow
emit  0.4   0.1   0.3  1     4     0    0    0.9  0            ; run_one wind_snag      # wind + snag desat/dim
emit -0.7   0.05  0.4  1     0     0    0    0    1            ; run_one dream_left     # dream-as-texture, EEVEE g_0001.png, bowing left
emit  0.6  -0.4   0.85 1     0     0    0    0    0   1  0     ; run_one reduced_motion # SAME gust as wind_gust but prefers-reduced-motion: ripple/parallax damped
emit  0     0     0    0     0     0    0    0    0   0  1     ; run_one stale          # producer DEAD: cool desat + vignette, distinct from idle
echo done

#!/usr/bin/env bash
# Render Hills at each agent state by hand-writing agent.json and capturing a PNG.
set -u
cd "$(dirname "$0")"
QML="$(command -v qml6 || command -v qml)"
[ -n "$QML" ] || { echo "no qml6/qml runtime"; exit 1; }
PREFIX="${PREFIX:-flow}"   # output filename prefix (set to the wallpaper being rendered)

emit() {
  # canonical bridge artifact (the "hand-written state file")
  printf '{"state":%s,"busy":%s,"warm":%s,"snag":%s}\n' "$1" "$2" "$3" "$4" > agent.json
  # harness shim with the same values (qml6 loads this reliably at startup)
  printf 'var state = %s\nvar busy = %s\nvar warm = %s\nvar snag = %s\n' "$1" "$2" "$3" "$4" > agent_data.js
}

run_one() {
  local name="$1"
  rm -f out.png
  # render on the live session (offscreen has no GL context here -> blank frames).
  timeout 25 "$QML" harness.qml >"log_$name.txt" 2>&1
  if [ -s out.png ]; then
    mv out.png "${PREFIX}_${name}.png"
    echo "  $name -> ${PREFIX}_${name}.png ($(stat -c%s "${PREFIX}_${name}.png") bytes)"
  else
    echo "  $name -> FAILED (see log_$name.txt)"; tail -3 "log_$name.txt" | sed 's/^/      /'
  fi
}

echo "rendering Hills per agent state:"
emit 0 0    0    0     ; run_one idle
emit 1 0.85 0    0     ; run_one working
emit 2 0    0.90 0     ; run_one needs_you
emit 4 0    0    0.90  ; run_one snag
echo done

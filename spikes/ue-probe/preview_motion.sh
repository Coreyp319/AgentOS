#!/usr/bin/env bash
# preview_motion.sh — render a SEQUENCE of CalmWallpaper frames (preview_seq.py)
# and assemble them into a motion clip (mp4) + an inline filmstrip (png) so the
# sub-threshold wave is actually visible. Reliable launcher (plain & + watchdog,
# like preview_shot.sh; launch_offscreen.sh's setsid gets culled here).
#
# USAGE (author the scene FIRST, ideally sped-up so the slow wave is legible):
#   ABYSSAL_SPEED=6 ABYSSAL_ACCENT=teal SCENE_SCRIPT=gradient_wave_setup.py \
#     MARK='Abyssal gradient-wave scene built' MIN_FREE_MIB=12000 \
#     bash spikes/ue-probe/author_scene.sh
#   bash spikes/ue-probe/preview_motion.sh
# Outputs: spikes/ue-probe/out/abyssal_motion.mp4 + abyssal_filmstrip.png

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EDITOR="${HOME}/UnrealEngine/Engine/Binaries/Linux/UnrealEditor"
PROJECT="${HOME}/UnrealProjects/AgentOSBlank/AgentOSBlank.uproject"
MAP="${MAP:-/Game/AgentOS/CalmWallpaper}"
SEQ_PY="${SCRIPT_DIR}/preview_seq.py"
LOG="${SCRIPT_DIR}/ue_motion.log"
SHOTDIR="${HOME}/UnrealProjects/AgentOSBlank/Saved/Screenshots/LinuxEditor"
OUT="${SCRIPT_DIR}/out"; mkdir -p "$OUT"
MP4="${OUT}/abyssal_motion.mp4"
STRIP="${OUT}/abyssal_filmstrip.png"

FPS="${MOTION_FPS:-20}"          # playback fps of the assembled clip
MIN_FREE_MIB="${MIN_FREE_MIB:-9000}"
TIMEOUT="${MOTION_TIMEOUT:-220}"
MARK='DONE preview_seq'

[[ -x "$EDITOR"  ]] || { echo "FAIL: editor not found: $EDITOR" >&2; exit 1; }
[[ -f "$SEQ_PY"  ]] || { echo "FAIL: seq script not found: $SEQ_PY" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "FAIL: ffmpeg not found" >&2; exit 1; }

FREE_MIB="$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d ' ')"
echo "VRAM free: ${FREE_MIB:-unknown} MiB (need >= ${MIN_FREE_MIB})"
if [[ "${PREVIEW_FORCE:-0}" != "1" ]]; then
  [[ -n "$FREE_MIB" && "$FREE_MIB" -ge "$MIN_FREE_MIB" ]] || { echo "ABORT: card busy"; exit 3; }
fi

idx() { ls "$SHOTDIR"/HighresScreenshot*.png 2>/dev/null | sed -E 's/.*HighresScreenshot0*([0-9]+)\.png/\1/' | sort -n | tail -1; }
LAST_BEFORE="$(idx)"; LAST_BEFORE="${LAST_BEFORE:-0}"
START=$((LAST_BEFORE + 1))
echo "sequence will start at frame index $START"

ARGS=( "$PROJECT" "$MAP"
  -RenderOffscreen -unattended -stdout -FullStdOutLogOutput
  -ResX=960 -ResY=540 -nosound -nosplash
  "-ExecCmds=py ${SEQ_PY}" )

echo "capturing motion sequence of $MAP ..."
"$EDITOR" "${ARGS[@]}" > "$LOG" 2>&1 &
UE_PID=$!
echo "editor pid=$UE_PID (watchdog ${TIMEOUT}s)"
waited=0; verdict="timeout"
while [ "$waited" -lt "$TIMEOUT" ]; do
  kill -0 "$UE_PID" 2>/dev/null || { verdict="exited"; break; }
  grep -qa "$MARK" "$LOG" 2>/dev/null && { verdict="marker"; break; }
  sleep 3; waited=$((waited + 3))
done
[ "$verdict" = "marker" ] && sleep 2
if kill -0 "$UE_PID" 2>/dev/null; then kill -TERM "$UE_PID" 2>/dev/null; sleep 2; kill -9 "$UE_PID" 2>/dev/null; fi
wait "$UE_PID" 2>/dev/null
echo "=== editor stopped (verdict=$verdict after ${waited}s) ==="

LAST_AFTER="$(idx)"; LAST_AFTER="${LAST_AFTER:-0}"
COUNT=$((LAST_AFTER - LAST_BEFORE))
echo "captured ${COUNT} new frames (index ${START}..${LAST_AFTER})"
[ "$COUNT" -ge 8 ] || { echo "FAIL: too few frames ($COUNT). Inspect $LOG."; exit 4; }

# Assemble mp4 (loopable, yuv420p for broad playback).
ffmpeg -y -framerate "$FPS" -start_number "$START" \
  -i "$SHOTDIR/HighresScreenshot%05d.png" -frames:v "$COUNT" \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart "$MP4" >/dev/null 2>&1 \
  && echo "mp4 -> $MP4" || echo "WARN: mp4 assembly failed"

# Filmstrip: 6 evenly-spaced frames tiled 3x2 into one PNG for inline viewing.
STEP=$(( COUNT / 6 )); [ "$STEP" -lt 1 ] && STEP=1
ffmpeg -y -start_number "$START" -i "$SHOTDIR/HighresScreenshot%05d.png" \
  -frames:v "$COUNT" -vf "select='not(mod(n\,${STEP}))',scale=440:-2,tile=3x2:padding=6:color=0x101418" \
  -frames:v 1 "$STRIP" >/dev/null 2>&1 \
  && echo "filmstrip -> $STRIP" || echo "WARN: filmstrip failed"

echo "PASS: motion assembled ($COUNT frames @ ${FPS}fps)."

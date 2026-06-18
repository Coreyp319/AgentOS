#!/usr/bin/env bash
# run.sh — drive the Phase-0 creative pipeline end-to-end via flatpak Blender (EEVEE).
#
# THROWAWAY SPIKE (spikes/ is excluded from the cargo workspace). This drives Blender
# DIRECTLY (no forge MCP server) so the loop is independently verifiable. In production
# the same render is wrapped by the AdoptScope lease — see the note at the bottom.
#
# Usage:
#   ./run.sh                       # full 14s drift of the canonical amber field
#   ./run.sh --quick               # 6-frame smoke render (fast)
#   ./run.sh briefs/other.json     # a different validated brief
#   BLENDER="blender" ./run.sh     # use a system blender instead of flatpak
#
# Determinism: fixed seed in layout.py; same brief + flags => same frames.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/out"

# Separate the brief (the single *.json positional, if any) from forwarded flags.
# Everything that is NOT the brief is forwarded to render_move.py after the `--`.
BRIEF=""
FWD=()
for a in "$@"; do
  case "$a" in
    *.json) BRIEF="$a" ;;
    *)      FWD+=("$a") ;;
  esac
done
[ -z "$BRIEF" ] && BRIEF="$HERE/briefs/amber_field.json"

# Absolutize the brief path — flatpak Blender runs with cwd=/app, so a relative path
# would not resolve inside the sandbox (it has `host` fs access, so an ABS path works).
case "$BRIEF" in
  /*) : ;;
  *)  BRIEF="$(cd "$(dirname "$BRIEF")" && pwd)/$(basename "$BRIEF")" ;;
esac

# Blender launcher: flatpak by default, overridable with $BLENDER.
if [ -n "${BLENDER:-}" ]; then
  BL=("$BLENDER")
elif command -v blender >/dev/null 2>&1; then
  BL=(blender)
else
  BL=(flatpak run org.blender.Blender)
fi

echo "[run] validating brief (pure python, no Blender)..."
python3 "$HERE/validate_brief.py" "$BRIEF" || { echo "[run] brief INVALID — aborting before Blender"; exit 2; }

echo "[run] launching Blender (EEVEE) headless: ${BL[*]}"
echo "[run] brief=$BRIEF out=$OUT extra=${FWD[*]:-<none>}"
mkdir -p "$OUT"

"${BL[@]}" --background --factory-startup --python "$HERE/render_move.py" -- \
  --brief "$BRIEF" --out "$OUT" "${FWD[@]}"
rc=$?

echo "[run] blender exit=$rc"
if [ "$rc" -ne 0 ]; then exit "$rc"; fi

# Encode the mp4 from the FINAL frames with the system ffmpeg (this Blender flatpak
# build has no FFMPEG output enum; render.json records which frame dir to encode).
MP4="$OUT/move.mp4"
if command -v ffmpeg >/dev/null 2>&1 && [ -f "$OUT/render.json" ]; then
  FINAL_DIR=$(python3 -c "import json;print(json.load(open('$OUT/render.json'))['final_dir'])")
  PATTERN=$(python3 -c "import json;print(json.load(open('$OUT/render.json'))['final_pattern'])")
  FPS=$(python3 -c "import json;print(json.load(open('$OUT/render.json'))['fps'])")
  START=$(python3 -c "import json;print(json.load(open('$OUT/render.json'))['frame_start'])")
  echo "[run] encoding mp4 from $FINAL_DIR/$PATTERN @ ${FPS}fps -> $MP4"
  ffmpeg -y -framerate "$FPS" -start_number "$START" -i "$FINAL_DIR/$PATTERN" \
    -c:v libx264 -pix_fmt yuv420p -crf 20 "$MP4" >/dev/null 2>&1 \
    && echo "[run] mp4 written: $MP4" \
    || echo "[run] ffmpeg encode failed; frames remain in $FINAL_DIR/"
else
  echo "[run] no system ffmpeg or render.json — skipping mp4; frames remain."
fi

FIRST=$(python3 -c "import json,os;d=json.load(open('$OUT/render.json'));print(os.path.join(d['final_dir'], d['final_pattern']%d['frame_start']))" 2>/dev/null)
echo
echo "[run] artifacts:"
echo "  raw frames:    $OUT/frames/"
echo "  graded frames: $OUT/graded/   (the painterly post-grade)"
echo "  mp4:           $MP4"
echo "  blend:         $OUT/scene.blend"
echo "  manifest:      $OUT/render.json"
echo
echo "[run] seed the lucid dream tree from the first frame (manual in Phase 0):"
echo "  python3 $HERE/../dreaming/lucid/lucid_engine.py start amber --image \"${FIRST:-$OUT/graded/g_0001.png}\""
exit 0

# ---------------------------------------------------------------------------
# PRODUCTION HOOK (not wired in the spike — documented per the task):
#   In production this render runs OWNED by the agentosd VRAM coordinator under the
#   AdoptScope lease (ADR-0022 / Design-0022), exactly like integrations/blender/
#   phase0-render.sh wraps render.py: the daemon Spawns the flatpak, AdoptScope
#   moves the flatpak's transient scope under agentosd's cgroup so a preempt
#   SIGKILLs the whole render (and any helper) and reclaims VRAM. The lucid seam
#   below is then driven by the substrate, not by hand. The spike deliberately
#   does NOT acquire the lease so it stays independently runnable.
# ---------------------------------------------------------------------------

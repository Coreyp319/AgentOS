#!/usr/bin/env bash
# Gentle coexistence probe — force the SMALLEST Ollama model resident with keep_alive.
#
# Phase-A feasibility (AgentOS). Loads moondream:latest (1.7 GB on disk, ~2-2.5 GB VRAM
# resident) via a trivial /api/generate call with keep_alive=10m, then reads /api/ps
# (read-only) to confirm residency and report size_vram.
#
# RUN IT YOURSELF (do not auto-run). It does exactly ONE small generate, no pull.
#   bash spikes/ue-probe/ollama_load_small.sh
# Override the model:
#   MODEL=qwen2.5vl:3b bash spikes/ue-probe/ollama_load_small.sh
#
# Picked from `ollama list`: moondream:latest is the smallest installed model.
set -euo pipefail

HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
MODEL="${MODEL:-moondream:latest}"
KEEP="${KEEP_ALIVE:-10m}"

echo "[ollama-probe] host=$HOST  model=$MODEL  keep_alive=$KEEP"
echo "[ollama-probe] /api/ps BEFORE:"
curl -s "http://$HOST/api/ps" || echo "  (ollama unreachable)"
echo

echo "[ollama-probe] loading model resident (one tiny generate)..."
curl -s "http://$HOST/api/generate" -d "{
  \"model\": \"$MODEL\",
  \"prompt\": \"hi\",
  \"keep_alive\": \"$KEEP\",
  \"stream\": false
}" | (python3 -c 'import sys,json; d=json.load(sys.stdin); print("[ollama-probe] generate done; total_duration_ms=%.0f" % (d.get("total_duration",0)/1e6))' 2>/dev/null || echo "[ollama-probe] generate returned (non-JSON or error)")
echo

echo "[ollama-probe] /api/ps AFTER (residency + size_vram):"
# Pretty-print residency: name, size, size_vram (the GPU portion).
curl -s "http://$HOST/api/ps" | python3 -c '
import sys, json
d = json.load(sys.stdin)
ms = d.get("models", [])
if not ms:
    print("  NO models resident — load did not stick (check ollama logs).")
gib = 1024**3
for m in ms:
    sz = m.get("size", 0)
    vr = m.get("size_vram", 0)
    pct = (100*vr/sz) if sz else 0
    print(f"  {m.get(\"name\",\"?\"):28s} total={sz/gib:5.2f} GiB  "
          f"size_vram={vr/gib:5.2f} GiB ({pct:.0f}% on GPU)  until={m.get(\"expires_at\",\"?\")}")
' || curl -s "http://$HOST/api/ps"
echo
echo "[ollama-probe] >>> $MODEL should now be RESIDENT (size_vram > 0) <<<"
echo "[ollama-probe] release early with: curl -s http://$HOST/api/generate -d '{\"model\":\"$MODEL\",\"keep_alive\":0}'"

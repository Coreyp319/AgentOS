#!/usr/bin/env bash
# Launch the ComfyUI shared backend (UI + HTTP API on 127.0.0.1:8188).
# UI:  http://127.0.0.1:8188   API: same host/port (/prompt, /history, /free)
set -e
COMFY="${COMFY_ROOT:-$HOME/ComfyUI}"
# --preview-method latent2rgb: live denoise preview during sampling (nearly free; no extra
# VRAM/model — a cheap linear latent→RGB approximation). Watch it in the web UI or over the
# WebSocket. Override with COMFY_PREVIEW=none|taesd|auto.
# --disable-smart-memory (on by default): fully evict models after use — esp. the 10.8 GiB umt5
# text encoder — so the Wan 2.2 14B expert loads fully resident instead of streaming ~8 GiB off
# CPU each step. Trade-off: no cross-run model cache (rapid same-model iteration pays a reload).
# Mirrors comfyui.service. Set COMFY_SMART_MEMORY=1 to keep the cache for tight iterate-in-UI loops.
SMART_MEM="--disable-smart-memory"
if [ -n "${COMFY_SMART_MEMORY:-}" ]; then SMART_MEM=""; fi

# AMD (ROCm) tuning (ADR-0048 Phase 2). If the active GPU is AMD, ComfyUI needs the PyTorch SDPA
# attention backend (flash/xformers aren't reliable on RDNA3) and the expandable-segments HIP
# allocator to avoid fragmentation OOMs. Gated NVIDIA-first (matching the substrate's GpuBackend
# precedence): a working nvidia-smi means NVIDIA is the compute target, so these stay off — they
# only turn on when there's NO usable NVIDIA and an AMD card (PCI vendor 0x1002) is present.
AMD_FLAGS=""
if ! { command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; }; then
  for v in /sys/class/drm/card[0-9]*/device/vendor; do
    [ -r "$v" ] || continue
    if [ "$(cat "$v")" = "0x1002" ]; then
      AMD_FLAGS="--use-pytorch-cross-attention"
      export PYTORCH_HIP_ALLOC_CONF="${PYTORCH_HIP_ALLOC_CONF:-expandable_segments:True}"
      echo "start-comfyui: AMD GPU (ROCm) — SDPA attention + expandable-segments on (experimental; video is slower, no fp8 on RDNA3 — ADR-0048)." >&2
      break
    fi
  done
fi

# Port-race guard (ADR-0015): ComfyUI must be coordinator-owned so its lease is real and a preempt
# can SIGKILL it. If something ALREADY answers on :8188 (a stray comfyui.service, or a manual run),
# a second launch would race the port, die, and silently re-introduce the "stale lease + unleased
# compute" bug. Refuse loudly instead — the daemon-owned Spawn then exits non-zero and lucid fails
# OPEN (no dream), never the silent bug. The always-on comfyui.service is intentionally disabled;
# start it by hand only for manual ComfyUI work (no dream running).
if curl -sf -m 2 "http://127.0.0.1:8188/system_stats" >/dev/null 2>&1; then
  echo "start-comfyui: ComfyUI already answering on :8188 — refusing to launch a second instance." >&2
  echo "  (Disable the always-on comfyui.service so the coordinator can own ComfyUI: " >&2
  echo "   systemctl --user disable --now comfyui.service)" >&2
  exit 3
fi

exec "$COMFY/.venv/bin/python" "$COMFY/main.py" --listen 127.0.0.1 --port 8188 \
  --preview-method "${COMFY_PREVIEW:-latent2rgb}" $SMART_MEM $AMD_FLAGS "$@"

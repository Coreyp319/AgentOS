#!/usr/bin/env bash
# Launch the ComfyUI shared backend (UI + HTTP API on 127.0.0.1:8188).
# UI:  http://127.0.0.1:8188   API: same host/port (/prompt, /history, /free)
set -e
COMFY="${COMFY_ROOT:-/home/corey/ComfyUI}"
# --preview-method latent2rgb: live denoise preview during sampling (nearly free; no extra
# VRAM/model — a cheap linear latent→RGB approximation). Watch it in the web UI or over the
# WebSocket. Override with COMFY_PREVIEW=none|taesd|auto.
exec "$COMFY/.venv/bin/python" "$COMFY/main.py" --listen 127.0.0.1 --port 8188 \
  --preview-method "${COMFY_PREVIEW:-latent2rgb}" "$@"

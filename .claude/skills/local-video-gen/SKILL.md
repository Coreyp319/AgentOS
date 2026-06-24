---
name: local-video-gen
description: Playbook for local open-source video generation on AgentOS (ComfyUI on a 24GB RTX 4090), powering the "dreaming" feature. Covers the open-weights model landscape (Wan 2.2, Hunyuan 1.5, LTX-2), the SFW/NSFW realism recipe, the proven ComfyUI HTTP-API client/converter, VRAM/OOM/GGUF gotchas, and the watch-list for when fast generation gets good enough to ship ambiently. Use when generating/testing/integrating local text- or image-to-video, picking a model or quant, debugging ComfyUI workflow conversion or OOM, fixing distorted/"monstrous" anatomy, or planning the dreaming/video roadmap in this repo.
---

# Local video generation (AgentOS "dreaming")

The strategic bet: open-weights local video is **poor-when-fast, good-when-slow** today.
The job is to be ready for the inflection when **fast gets good enough** to ship ambiently.
See [REFERENCE.md](REFERENCE.md) §Watch-list. Decisions: `docs/adr/0008`→`0009`; research:
`docs/research/0001-0003`. Backend code: **`apps/dreaming/`** (gated on the substrate).

## Quick start
```sh
# 1. ComfyUI lives at ~/ComfyUI (uv-managed Python 3.12 venv, torch 2.6.0+cu124).
~/Documents/AgentOS/apps/dreaming/start-comfyui.sh        # UI + HTTP API on :8188

# 2. Generate from a shipped ComfyUI template (mp4 -> ~/ComfyUI/output):
cd ~/Documents/AgentOS/apps/dreaming
python3 comfy_client.py run-template <template.json> --prompt "..." --length 49

# 3. Release VRAM so Ollama/desktop can reclaim the GPU:
python3 comfy_client.py free
```
Templates: `~/ComfyUI/.venv/lib/python3.12/site-packages/comfyui_workflow_templates_media_video/templates/`
(`video_wan2_2_*`, `video_hunyuan_video_1.5_*`). `comfy_client.py` converts them to the API graph.

## The five rules that keep mattering
1. **Base models can't do realistic human anatomy.** They lack the training data → distorted
   "monstrous" output. Need an **anatomy-trained checkpoint or low-noise NSFW LoRA**. (REF §NSFW)
2. **Lightning/lightx2v 4-step speed LoRA on the HIGH-noise expert melts anatomy** — the
   high-noise expert lays out bodies. This (not "no NSFW data") is the #1 distortion cause.
   For realism: non-distilled, OR keep high-noise Lightning-free. (REF §NSFW)
3. **VRAM saturation → CPU-offload thrash → 30min+ stalls.** Keep res low enough to fit; restart
   ComfyUI for a clean ~20GB; **fp8 ≫ GGUF for speed** (GGUF dequant is slow). (REF §VRAM)
4. **A client timeout does NOT cancel the server job.** Never submit a new gen after a timeout
   without `POST /interrupt` or a restart, or the queue runs away. (REF §Runbook)
5. **Always surface the `/prompt` 400 body** — ComfyUI's validation errors pinpoint the fix.
   `comfy_client.py` already does this.

## Common tasks
- **Generate / test a model** → REF §Runbook (T2V via `test_aio.py` / `test_gguf_moe.py`).
- **Fix distorted NSFW** → REF §NSFW realism recipe (non-distilled + 720p + I2V + low-noise LoRA).
- **Pick a model / quant** → REF §Model landscape.
- **Debug "monstrous", OOM, slow, or workflow-conversion errors** → REF §VRAM, §Converter.
- **Get NSFW weights** → REF §Weights & auth (Civitai is free; HF mirrors; **CSAM exclusions**).
- **Plan the OS integration / roadmap** → REF §Watch-list, §Integration.

## Hard constraints (non-negotiable)
- **Excluded, always:** any content involving minors or non-consensual real-person likenesses.
  A known CSAM repo (`deadman44/*`) must never be downloaded/used. (REF §Weights & auth)
- Local, single-user, on-device only. Adult content is a model/LoRA capability, not a license grant.
- Reversible + model-proposes/code-disposes: a dream is a cached file the wallpaper *may* play,
  never a state mutation. Video-gen is an evictable VRAM leaseholder (ADR-0004/0010).

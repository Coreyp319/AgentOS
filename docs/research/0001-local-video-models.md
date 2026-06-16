# Research: local open-source video generation for dreaming (RTX 4090 / 24 GB)

Date: 2026-06-15. Feeds [ADR-0008](../adr/0008-dreaming-via-local-video-gen.md).
Six parallel research lanes (models, runtime, NSFW capability, 24 GB
feasibility, licensing, plus repo recon). Primary sources cited inline.

## Question
Simplest way to generate local video segments on an RTX 4090 (24 GB), SFW +
NSFW, serving two surfaces: the ambient dreaming wallpaper (I2V) and an
on-demand KRunner action (T2V).

## Models (24 GB-viable, mid-2026)

| Model | License | T2V/I2V | 24 GB fit | ~5 s clip | NSFW path |
|---|---|---|---|---|---|
| **Wan 2.2** 14B / 5B | **Apache-2.0** | both | 5B native (~8 GB); 14B fp8 ~480p, heavy at 720p | 5B ~4–9 min; 14B ~9 min (or ~fast w/ 4-step lightx2v LoRA) | largest Civitai LoRA ecosystem |
| **HunyuanVideo 1.5** (8.3B) | Tencent community (EU/UK/KR excluded; ships filter) | both | ~14 GB w/ offload | ~75 s distilled | large LoRA ecosystem |
| **LTX-2** (19B) | Apache-2.0 | both + audio | comfortable fp8 ~14 GB | ~90 s (fastest) | smaller ecosystem |
| CogVideoX-5B | custom (5B) / Apache (2B) | both | INT8 ~ fits | ~2–3 min | modest |
| Mochi-1 | Apache-2.0 | T2V | offload, slow | 8–15 min | thin |
| Wan 2.5/2.6/2.7 | **API-only, not open** | — | — | — | — |

Closed/over-VRAM (Open-Sora 2.0 40 GB+, original HunyuanVideo 45–60 GB) excluded.

## Runtime — simplest that is BOTH a UI and an API
1. **ComfyUI headless** — UI + HTTP API (`/prompt` + ws + `/history`, `/free` to
   release VRAM), day-one model support, the LoRA ecosystem. Chosen.
   ([ws example](https://github.com/comfyanonymous/ComfyUI/blob/master/script_examples/websockets_api_example.py),
   [dynamic VRAM/free](https://blog.comfy.org/p/dynamic-vram-in-comfyui-saving-local))
2. **HuggingFace diffusers sidecar** — tightest in-process VRAM control
   (graded offload + instant release + named output path); best *future*
   production path, but no UI. ([memory docs](https://huggingface.co/docs/diffusers/optimization/memory))
3. Wan2GP / native CLIs — cold-load per call, no resident/release control. For
   one-shot use only.

## NSFW capability + licensing (the load-bearing facts)
- **No major video-model license bans adult content** by its terms. Apache-2.0
  (Wan, Mochi, CogVideoX-2B, LTX-2) carry **no** acceptable-use restrictions.
- Capability comes from **community fine-tunes / LoRAs** (Civitai is the hub),
  not from disabling a filter — base weights aren't explicit-trained.
- Wan 2.2 ships a weight-level filter that LoRAs override in practice; Hunyuan
  ships a runtime filter community builds strip locally.
- **Universal red line in every license / the law:** no minors, no
  non-consensual real-person likenesses. Honored — out of scope.
- **Bundling:** only Apache-2.0 weights (Wan 2.2) are clean to ship. Hunyuan's
  Tencent license excludes EU/UK/KR territory + mandates a NOTICE → local-only.
  CogVideoX-5B / LTX-0.9.x have custom per-version terms (verify per checkpoint).

Sources: Wan [Apache LICENSE](https://huggingface.co/Wan-AI/Wan2.2-T2V-A14B),
Hunyuan [Tencent license](https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5/blob/main/LICENSE),
LTX-2 [Apache launch](https://www.prnewswire.com/news-releases/lightricks-releases-ltx-2-the-first-complete-open-source-ai-video-foundation-model-302593012.html),
quant/VRAM via Comfy-Org repackaged repos + community GGUF (city96, QuantStack).

## Decision (see ADR-0008)
ComfyUI shared backend; **Wan 2.2** (default, bundle-clean) + **HunyuanVideo
1.5** (quality, local-only) on fp8 for 24 GB; two surfaces (wallpaper I2V +
KRunner T2V); video-gen is an evictable VRAM leaseholder under ADR-0004.

## Note on process
The web research ran as 6 regular Agent subagents after the Workflow harness
stalled twice at the recon phase (no subagent transcripts produced); the
fallback path worked. Recorded so we prefer Agent-tool fan-out here until the
workflow stall is understood.

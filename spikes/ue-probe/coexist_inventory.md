# Coexistence model inventory (Phase-A feasibility)

Captured 2026-06-19 (read-only: `ls`, `ollama list`, ComfyUI `/object_info`).
GPU: RTX 4090, 24 GiB (ComfyUI reports `vram_total` 25,277,693,952 B ≈ 23.5 GiB usable).
ComfyUI 0.25.0, launched with `--disable-smart-memory` (no aggressive auto-eviction;
a loaded checkpoint stays resident until `/free` or the next graph needs the VRAM).

## ComfyUI checkpoints — `~/ComfyUI/models/checkpoints/`
| File | On-disk | Type / note |
|---|---|---|
| `sd_turbo.safetensors` | 5,214,561,328 B ≈ **4.86 GiB** | **SMALLEST reliable image ckpt** — SD2.1-Turbo, self-contained (CLIP+VAE baked), 1-step capable. **GENTLE-PROBE PICK.** |
| `divingIllustriousReal_v40VAE.safetensors` | 6,938,046,678 B ≈ 6.46 GiB | SDXL-Illustrious image ckpt (CLIP+VAE baked). Heavier fallback image option. |
| `wan2.2-rapid-mega-aio-nsfw-v12.2.safetensors` | 23,284,018,448 B ≈ 21.7 GiB | Wan 2.2 14B AIO **video** — the HEAVY case (~17 GB resident). Do NOT use for the gentle probe. |

Only these three are visible to `CheckpointLoaderSimple` (confirmed via `/object_info`).
The `unet/`, `diffusion_models/`, `vae/` dirs hold Wan/Hunyuan/LTX **video** components
(all 8–16 GB class) — none are a small image checkpoint, so they are out of scope for the
gentle probe. Full listing kept below for completeness.

### unet/ (GGUF video UNets — heavy)
- `10Eros_v1-Q4_K_M.gguf` 14,296,160,672 B ≈ 13.3 GiB
- `wan22_enhNSFW_nolight_cf_Q6K_high.gguf` 12,003,662,336 B ≈ 11.2 GiB
- `wan22_enhNSFW_nolight_cf_Q6K_low.gguf` 12,003,662,336 B ≈ 11.2 GiB

### diffusion_models/ (Wan/Hunyuan video — heavy)
- `hunyuanvideo1.5_480p_t2v_cfg_distilled_fp8_scaled.safetensors` ≈ 7.76 GiB
- `hunyuanvideo1.5_720p_i2v_cfg_distilled_fp8_scaled.safetensors` ≈ 7.76 GiB
- `hunyuanvideo1.5_720p_t2v_fp16.safetensors` ≈ 15.5 GiB
- `Wan2.2_Remix_NSFW_i2v_14b_high_lighting_fp8_e4m3fn_v3.0.safetensors` ≈ 13.3 GiB
- `Wan2.2_Remix_NSFW_i2v_14b_low_lighting_fp8_e4m3fn_v3.0.safetensors` ≈ 13.3 GiB
- `wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors` ≈ 13.3 GiB
- `wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors` ≈ 13.3 GiB
- `wan2.2_ti2v_5B_fp16.safetensors` ≈ 9.31 GiB

### vae/
- `hunyuanvideo15_vae_fp16.safetensors` ≈ 2.35 GiB
- `LTX23_audio_vae_bf16.safetensors` ≈ 0.34 GiB
- `LTX23_video_vae_bf16.safetensors` ≈ 1.35 GiB
- `wan_2.1_vae.safetensors` ≈ 0.24 GiB
- `wan2.2_vae.safetensors` ≈ 1.31 GiB

## Ollama models — `ollama list`
Currently **0 models resident** (`/api/ps` → `{"models":[]}`), confirmed read-only.

| Model | Disk size | Note |
|---|---|---|
| `moondream:latest` | **1.7 GB** | **SMALLEST — GENTLE-PROBE PICK.** Tiny VLM, resident VRAM ≈ 2–2.5 GB. |
| `qwen2.5vl:3b` | 3.2 GB | Next-smallest, small VLM. |
| `gemma4:latest` | 9.6 GB | mid |
| `gemma4-64k:latest` | 9.6 GB | mid (64k ctx) |
| `gemma4:26b-a4b-it-qat` | 15 GB | large |
| `gemma4-26b-64k:latest` | 15 GB | large |
| `hermes4-14b:latest` | 15 GB | large (Q8) |
| `hf.co/bartowski/NousResearch_Hermes-4-14B-GGUF:Q8_0` | 15 GB | (same as above) |
| `qwen3.6:27b` | 17 GB | large |
| `qwen3.6-27b-64k:latest` | 17 GB | large (64k ctx) |
| `hermes4.3-36b:latest` | 21 GB | largest |
| `hf.co/NousResearch/Hermes-4.3-36B-GGUF:Q4_K_M` | 21 GB | (same as above) |

## Picks for the gentle coexistence probe
- **ComfyUI:** `sd_turbo.safetensors` (≈ 4.86 GiB disk; resident ≈ 3–4.5 GiB at 512×512).
- **Ollama:** `moondream:latest` (1.7 GB disk; resident ≈ 2–2.5 GB).

## Rough gentle-probe VRAM budget (all three, 24 GiB card)
| Tenant | Est. resident VRAM |
|---|---|
| Throttled UE wallpaper | ~4.5 GiB (observed in `vram_*.log`, ~4,450 MiB) |
| ComfyUI sd_turbo (512², 1-step) | ~3–4.5 GiB |
| Ollama moondream | ~2–2.5 GiB |
| **Sum** | **~10–11.5 GiB** |
| Plasma/desktop compositor headroom | ~1–2 GiB |
| **Headroom remaining under 23.5 GiB** | **~10–12 GiB** — comfortable, no OOM expected. |

## Heavy-case note (Wan 14B)
If ComfyUI instead loads the Wan 2.2 14B AIO (~17 GiB resident) alongside a ~4.5 GiB
throttled UE and a small Ollama model (~2.5 GiB), the sum is **~24 GiB**, which meets or
exceeds the 23.5 GiB usable budget once desktop headroom is counted → **expected OOM /
ComfyUI CUDA alloc failure, or eviction**. In that regime the throttled UE must fall back
to the shader wallpaper (yield the GPU), OR ComfyUI must be VRAM-leased so only one heavy
tenant is resident at a time. This is exactly the ADR-0018 warm-pool / heavy-lane
partition + the ADR-0006/0010 coordinator's job. The gentle probe (sd_turbo) is the safe
first measurement; the heavy case is a known cliff, measured second and expected to need
fall-back.

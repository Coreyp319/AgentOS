# Local video generation — reference

Companion to [SKILL.md](SKILL.md). Hardware baseline: **RTX 4090, 24 GB**, CachyOS.
Backend at `~/ComfyUI`; repo glue at `spikes/dreaming/`. Dated facts are mid-2026.

---

## Model landscape (open-weights, 24 GB)

| Model | License | T2V/I2V | 24 GB fit | Speed | NSFW ecosystem | Use for |
|---|---|---|---|---|---|---|
| **Wan 2.2 5B (TI2V)** | Apache-2.0 | both | native, ~8 GB | fast | via 14B LoRAs | quick iteration, smoke tests |
| **Wan 2.2 14B (A14B)** | Apache-2.0 | both (sep T2V/I2V) | fp8 ✓ / 720p heavy | med (4-step LoRA) | **largest** (Civitai) | **default for quality + NSFW** |
| **HunyuanVideo 1.5 (8.3B)** | Tencent (EU/UK/KR excluded) | both | ~14 GB | med | thin | motion/cinematic; avoid for NSFW |
| **LTX-2 (19B)** | Apache-2.0 | both + audio | fp8 ~14 GB | **fastest** | small | speed; watch for realism gains |

**Architecture note (Wan 2.2 14B):** Mixture-of-Experts — a **high-noise expert** (early steps:
composition, bodies, motion) + a **low-noise expert** (late steps: skin, detail). Run as two
chains, each `UNETLoader → ModelSamplingSD3 → KSamplerAdvanced`, split at a step boundary; do not
merge. Most 14B LoRAs/checkpoints ship paired **high + low** files.

**Closed / API-only (NOT local, content-filtered — ignore for this project):** Seedance (ByteDance),
Veo (Google), Kling, Hailuo, Sora, **Wan 2.5/2.6/2.7** (weights never released). They top quality
leaderboards, which is why they're talked about — but they cannot run locally. The latest *open*
Wan is **2.2**.

---

## NSFW realism recipe

Base Wan/Hunyuan have **no explicit/anatomy training** → "monstrous" output. Two compounding fixes,
in priority order (biggest lever first):

1. **Use anatomy-trained weights.** Either a dedicated **NSFW fine-tune checkpoint** (anatomy baked
   in) or a **low-noise NSFW LoRA** on the base. Removing a safety filter alone does nothing — the
   data isn't there.
2. **Keep the high-noise expert Lightning-free.** The 4-step lightx2v/Lightning LoRA on high-noise
   melts bodies. Prefer **non-distilled** checkpoints, or a 4+4 hybrid (Lightning on low only).
3. **Image-to-video from a clean first frame** — the single biggest anatomy win after (1)/(2).
   The video model animates a body that's already correct instead of inventing one. (T2V is the
   anatomy lottery.)
4. **Resolution 720p-class, not 480p** — too few pixels = mangled hands/faces. Subject large in
   frame; **portrait** for a single subject.
5. **81 frames @ 16 fps** (~5 s); >121 frames burns first frames + speeds up motion. Chain segments
   for longer.
6. **Steps/CFG:** non-distilled ~20 steps, CFG ~3–3.5 for finals; 4+4 hybrid for iteration; pure
   4-step Lightning = draft only. LoRAs **low-noise only**, strength 0.5–1.0, realism LoRA first.
7. **Post:** CodeFormer face-restore → upscale → RIFE interpolation.

**Camps:** (A) dedicated NSFW fine-tune checkpoint = lowest hassle, best realism for most;
(B) base + curated low-noise LoRA stack = max control, fiddlier. **Wan 2.2 ≫ Hunyuan 1.5** for NSFW
realism (ecosystem depth). The hands problem is mitigated, never fully solved.

---

## Weights & auth

**HuggingFace = no auth.** `https://huggingface.co/<repo>/resolve/main/<file>` — just `wget`/`curl`.
Notable open mirrors: `Instara/instareal-wan-2.2` (realism LoRA, free mirror of the Civitai one),
`lopi999/Wan2.2-I2V_General-NSFW-LoRA` (I2V, H+L), `FX-FeiHou/wan2.2-Remix` (non-distilled,
anatomy-focused, ships workflows), `Phr00t/WAN2.2-14B-Rapid-AllInOne` (all-in-one, Lightning-baked,
deprecated/weak faces).

**Civitai = FREE to download** (it is not a paid service). You only need a **free account + API
token** (Account → API Keys) because mature content sits behind login (and the `civitai.red`
mirror); enable mature content in account settings. The optional paid side (membership / "Buzz") is
only for *their* cloud generation — irrelevant when generating locally. Download by version id:
`curl -sL -H "Authorization: Bearer $CIVITAI_TOKEN" https://civitai.com/api/download/models/<versionId> -o out`.
**New accounts get rate-limited (HTTP 429)** — back off and retry, one file at a time.

Strong Civitai picks (need token): "WAN 2.2 Enhanced NSFW (SVI)" checkpoint (maintained; *nolightning*
= non-distilled; GGUF Q4KM/Q6K/Q8 + fp8; separate H/L), Instagirl/Instareal realism LoRAs.

**⚠️ HARD EXCLUSION — never download/use:** `deadman44/Wan2.2_T2i_T2v_LoRA` and related — its card
advertises **minor-targeted (CSAM) LoRAs**. Categorically off-limits. All work here excludes minors
and non-consensual real-person likenesses; recommended models carry anti-minor/anti-deepfake terms.

---

## The ComfyUI client/converter (`spikes/dreaming/comfy_client.py`)

Pure-stdlib client + a UI-workflow→`/prompt` API converter. The converter is the load-bearing,
hard-won piece — it handles every gotcha discovered shipping the test matrix:

- **`ui_to_api(wf)`** converts a saved/template ComfyUI workflow to the API graph.
- **`flatten_subgraphs`** — inlines subgraph instances (templates increasingly ship these, e.g.
  `video_wan2_2_14B_t2v`); remaps ids/links, drops unfed boundary inputs to internal widget
  defaults, rewires subgraph outputs to the parent.
- **Bypass pass-through (`resolve_src`)** — mode-4 (bypassed) nodes (e.g. `EasyCache` in the Hunyuan
  template, or a super-res branch) must **pass their input through by type**, not be deleted, or
  required `model` links vanish. mode-2/Note nodes are dropped.
- **Widget/link alignment** — `widgets_values` aligns to **all** widget inputs in `object_info`
  order, *including widgets "converted to inputs"* (consume their slot, let the link win). Skip the
  `control_after_generate` companion after `seed`/`noise_seed`. Detect combos in **both** forms
  (`[[opts],{}]` and `["COMBO",{options}]`).
- **Version-drift backfill** — fill any *required* widget the template omitted (e.g. `SaveVideo`
  gained `format`/`codec`) from `object_info` defaults.
- **pos/neg resolution** — find the node with both `positive` and `negative` inputs (KSampler, or
  `CFGGuider` behind `SamplerCustomAdvanced`) and walk back to the `CLIPTextEncode`. Never assume
  "first CTE = positive" (templates often list negative first).
- **Always surface `/prompt` 400 bodies** (`_post` raises with the body) — ComfyUI validation errors
  name the exact node/input.
- **`free_vram()`** POSTs `/free` (empty body — don't JSON-parse; just check 2xx).

Helpers built around it: `test_aio.py` (single all-in-one checkpoint → T2V) and `test_gguf_moe.py`
(dual-expert GGUF, non-distilled, real CFG).

---

## VRAM / OOM / GGUF gotchas (24 GB)

- **Restart ComfyUI for a clean slate** (~3.5 GB desktop baseline, ~20 GB free). Leftover resident
  models from prior gens cause OOM. Kill by **port** (`fuser -k 8188/tcp`) — never `pkill -f
  "main.py …"` with a pattern that also matches your own shell command (it kills the tool's shell;
  symptom: exit code 144).
- **OOM is usually resolution, not the model.** Step down: 640×1152 → 576×1024 → 512×896 → 480×832.
- **VRAM saturation → CPU-offload thrash → catastrophic slowdown.** A non-distilled GGUF run at
  640×1152/81f filled VRAM, forced layer offload to RAM, and ran **>33 min then timed out**. Keep
  the model+activations inside VRAM.
- **fp8 ≫ GGUF for speed.** GGUF (Q6K/Q8) adds dequant overhead. Use GGUF only when VRAM-bound;
  prefer fp8 safetensors otherwise.
- **A client timeout doesn't cancel the server job.** The job keeps running and a naive
  retry/sweep *queues more* behind it (runaway). After a timeout: `POST /interrupt` (or restart)
  before the next attempt. Do single gens, not submit-on-timeout sweeps, against a live server.
- GGUF experts load via `UnetLoaderGGUF` (files in `~/ComfyUI/models/unet/`); ComfyUI swaps
  high↔low between sampler stages, so peak VRAM ≈ one expert + activations.

### Failure modes (symptom → cause)
- **Blank / flat "cream" frame (latent never denoised)** = model↔workflow mismatch. Causes seen:
  an **SVI** checkpoint run in a vanilla workflow (SVI variants *require* the author's SVI workflow —
  "otherwise it won't work"); an **I2V-only** model run as T2V (no image conditioning); wrong VAE;
  or a broken sampler split. **Fix:** match the model's *required* workflow, or pick a variant built
  for a standard workflow (e.g. Remix v3, or a non-SVI Enhanced variant). **Check what workflow a
  model needs before generating** — model names like "SVI", "FLF2V", "VACE" imply special graphs.
- **Output resolution = half what you asked** = VAE/latent compression mismatch (the all-in-one's
  baked VAE vs the latent node's assumption). Use the model's matching latent node / bundled workflow.
- **`/prompt` HTTP 400** = graph validation; read the body (node + input named). Usually a missing
  required widget (version drift), a dropped link (bypass not passed through), or widget misalignment.

### The blind-iteration limit (important)
An automated agent **cannot see the rendered frames**, so it cannot judge anatomy/aesthetics or
catch "looks wrong but technically valid" (blank, melted, off-prompt) without the user looking. For
final NSFW/aesthetic tuning, **drive ComfyUI's web UI (:8188) with the model author's recommended
workflow** — instant visual feedback beats blind agent iteration. The agent's job is setup
(install, weights, the client/converter, a known-good baseline) and documenting; the human does the
see-it-to-tune-it last mile.

---

## Benchmarks (measured 2026-06-16, RTX 4090, these prompts/settings)

| Config | Res / frames / steps | Time | Note |
|---|---|---|---|
| Wan 2.2 **5B** fp8 T2V | 768×432 / 49 / 20 | **~30 s** | fast baseline; SFW good |
| Wan 2.2 **5B** fp8 (NSFW prompt) | 768×432 / 49 / 20 | ~64 s | base = poor anatomy |
| Wan 2.2 **14B** fp8 + 4-step lightx2v | 640×640 / 81 / 4+4 | ~5 min | distilled; anatomy hit |
| **Hunyuan 1.5** fp16 T2V | 768×432 / 49 / 20 | ~234 s | works; thin NSFW |
| **Phr00t AIO** (distilled, fp8) | 640×1152→**320×576** / 81 / 6 | ~124 s | VAE/latent halved output res |
| **Enhanced NSFW GGUF Q6K** (nolightning) | 480×832 / 49 / 10 | **~420 s** | GGUF + non-distilled = slow |
| Enhanced NSFW GGUF Q6K | 640×1152 / 81 / 20 | **>2000 s (timeout)** | VRAM-saturation offload thrash |

Takeaway: today, **fast configs (≤~2 min) give poor anatomy; good-anatomy configs cost 7–30+ min**
— impractical for ambient use. That gap is the whole strategic problem below.

---

## Watch-list — "when fast gets good enough"

The thesis: capabilities are **poor-when-fast, good-when-slow**; there will be an inflection where a
single config is *both*. Monitor these; when one lands, re-validate and revisit ADR-0009:

- **Distillation that preserves anatomy** — successors to lightx2v/Lightning that don't wreck the
  high-noise expert; "few-step but full-quality" checkpoints (the current killer constraint).
- **Faster quants without the dequant tax** — NVFP4/fp4 on Ada, better fp8 kernels, GGUF speedups;
  anything that makes 720p/81f land in <~60 s without offload.
- **Natively-fast open models** — the LTX-2.x line (speed-first, Apache); any new open arch that
  closes the quality gap at low step counts.
- **Open weight drops** — if Wan 2.5+/a successor ever releases weights (currently API-only), or a
  new open SOTA appears (track Artificial Analysis video arena for open models closing on
  Seedance/Veo/Kling).
- **ComfyUI / runtime perf** — native model-offload improvements, sage/flash attention defaults,
  better MoE scheduling (`WanMoEScheduler`).

**Decision triggers:** when a config does **720p, 81f, good anatomy, in ≲60 s on 24 GB**, the
ambient "dream-as-texture" surface (REF §Integration) becomes viable and ADR-0009's "video is not
the ambient medium" should be re-opened. Until then: on-demand (KRunner) only, which tolerates slow.

---

## Integration into the OS (per ADR-0009)

One backend (ComfyUI), **two surfaces**:
- **Surface A — ambient "dream-as-texture":** a generated clip is a `sampler2D` the procedural
  **shader warps** (shader stays the primary renderer of agent state); SFW-only, muted, post-graded,
  idle byte-identical. `spikes/dream-as-texture/`. **Gated** on fast-enough gen (Watch-list).
- **Surface B — on-demand KRunner action:** `video: <prompt>` → T2V, opened when ready (a Plasma 6
  D-Bus runner, no C++). Tolerates slow today. Must be consent-gated + opt-in before shipping.

**Substrate gating (the real blocker):** `agentosd` must own the ComfyUI process — `POST /free`
measured freeing **0 VRAM**, so eviction needs PID ownership + admission-control + `SIGKILL`
(ADR-0004/0010 coordinator). Video-gen **XOR** live inference on the GPU. A dream is a cached file
the wallpaper *may* play — reversible, never a state mutation (ADR-0005 spirit).

**Output the wallpaper consumes:** short seamless-loop `webm`/`mp4`, keyed off `agent.json` state,
played by a QML `Video` element (not yet built). Generation is offline/cached to avoid live VRAM
contention.

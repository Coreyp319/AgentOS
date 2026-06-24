# Local video generation — reference

Companion to [SKILL.md](SKILL.md). Hardware baseline: **RTX 4090, 24 GB**, CachyOS.
Backend at `~/ComfyUI`; repo glue at `apps/dreaming/`. Dated facts are mid-2026.

---

## Model landscape (open-weights, 24 GB)

| Model | License | T2V/I2V | 24 GB fit | Speed | NSFW ecosystem | Use for |
|---|---|---|---|---|---|---|
| **Wan 2.2 5B (TI2V)** | Apache-2.0 | both | native, ~8 GB | fast | via 14B LoRAs | quick iteration, smoke tests |
| **Wan 2.2 14B (A14B)** | Apache-2.0 | both (sep T2V/I2V) | fp8 ✓ / 720p heavy | med (4-step LoRA) | **largest** (Civitai) | **default for quality + NSFW** |
| **HunyuanVideo 1.5 (8.3B)** | Tencent (EU/UK/KR excluded) | both | ~14 GB | med | thin | motion/cinematic; avoid for NSFW |
| **LTX-2 (19B) / 2.3 (22B)** | Apache-2.0 | both + audio | 2: fp8 ~14 GB · **2.3: GGUF-only on 24 GB** (note) | **fastest** | 2.3: Sulphur-2, 10Eros | speed; watch for realism gains |

**Architecture note (Wan 2.2 14B):** Mixture-of-Experts — a **high-noise expert** (early steps:
composition, bodies, motion) + a **low-noise expert** (late steps: skin, detail). Run as two
chains, each `UNETLoader → ModelSamplingSD3 → KSamplerAdvanced`, split at a step boundary; do not
merge. Most 14B LoRAs/checkpoints ship paired **high + low** files.

**Version note (LTX-2.3 vs LTX-2, 24 GB):** LTX-2.3 is ~**22B** (not the 19B LTX-2 above). Full BF16
≈ 46 GB, fp8 ≈ 29–30 GB, NVFP4 ≈ 21.7 GB — so **fp8 does NOT fit cleanly on 24 GB** (the standard
desktop build wants 32 GB; a separate 24 GB "Multifunctional" variant exists). On the 4090 LTX-2.3 is
**GGUF-bound**: **Q6_K (17.8 GB)** is the quality/headroom sweet spot, Q8_0 (22.8 GB) is too tight
(offload thrash). The rare case where §VRAM's "GGUF only when VRAM-bound" actually fires.

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

## Long scenes (chaining I2V segments)

Wan/Hunyuan are trained on ~5 s clips; past ~121 frames they burn/repeat the opening
frames and speed up motion. For 30 s–1 min, **chain dependent I2V rounds**: seed a first
frame → I2V → take the **last frame** → I2V again → … → `ffmpeg` concat. This is a
**sequential pipeline**, not a parallel queue (segment N+1 needs N's last frame), and
errors **accumulate** (colour/detail/anatomy drift by ~segment 6) — fewer, larger-frame
segments drift less, and a **clean `--seed-image`** (not the T2V lottery) is the biggest
anatomy win.

**Tool: `apps/dreaming/chain_video.py`** (built + smoke-verified 2026-06-17). Wraps
`comfy_client.py`; per segment it injects the prior last frame into `LoadImage`, sets
`WanImageToVideo` w/h/length + prompt (re-injected each segment to fight drift), runs the
Remix NSFW I2V workflow, then `ffmpeg` pulls the last frame and finally concats — **dropping
the duplicated seam frame** (seg 1 keeps all N; each later seg contributes N−1, else the join
stutters). Resolves pos/neg via the node whose `positive`/`negative` link *directly* to a
`CLIPTextEncode` (this workflow routes conditioning through `WanImageToVideo`, which fools
`comfy_client.pos_neg_text_nodes` into returning pos==neg). On timeout it `POST /interrupt`s
(rule #4) and `/free`s at the end.

    ./chain_video.py --seed-image still.png --prompt "…" --duration 30   # best anatomy
    ./chain_video.py --seed-t2v --prompt "…" --segments 6                # self-seed (lottery)

`--duration S` computes segments from `--length` (default 81f); 30 s ≈ 6×81f @ 720×1280.
Smoke-verified: 2×33f @ 480×832 → 33+32 = **65 frames / 4.06 s @ 16fps**, clean seam. Cost is
the per-segment tier ×N: ~30 s of good Remix realism ≈ **45 min–3 h**, GPU-locked — an
overnight/on-demand job (the §Watch-list "good-when-slow" wall ×N). It queues serially behind
live jobs; free Ollama + `systemctl --user restart comfyui.service` first for a clean ~20 GB.

---

## Weights & auth

**HuggingFace = no auth.** `https://huggingface.co/<repo>/resolve/main/<file>` — just `wget`/`curl`.
Notable open mirrors: `Instara/instareal-wan-2.2` (realism LoRA, free mirror of the Civitai one),
`lopi999/Wan2.2-I2V_General-NSFW-LoRA` (I2V, H+L), `FX-FeiHou/wan2.2-Remix` (non-distilled,
anatomy-focused, ships workflows), `Phr00t/WAN2.2-14B-Rapid-AllInOne` (all-in-one, Lightning-baked,
deprecated/weak faces).

**LTX-2.3 NSFW (newest line — watch-list, not default).** `TenStrip/LTX2.3-10Eros` — an NSFW
**I2V-only** finetune (layer-scaled step merge on `SulphurAI/Sulphur-2-base`, itself an uncensored
LTX-2.3 T2V/I2V finetune). The creator ships their own ComfyUI **workflows**
(`TenStrip/LTX2.3-10Eros_Workflows`) **and custom nodes** (`github.com/TenStrip/10S-Comfy-nodes`), so
the converter may need a bypass/subgraph pass before it slots into `comfy_client.py`. 24 GB path =
GGUF mirror `vantagewithai/LTX2.3-10Eros-GGUF` (Q3_K→Q8_0; **Q6_K = 17.8 GB** sweet spot). I2V-by-
design (no T2V anatomy lottery — matches NSFW rule #3) and **prompt-heavy** ("LTX must be commanded");
**discourages distilled LoRAs** → no few-step mode → still good-when-slow, *not* the speed inflection.
License unstated on the card (LTX-2 base = Lightricks license). Experimental; vet per-file for the
minors/real-likeness exclusions below before download.

**Working 24 GB I2V recipe (VERIFIED 2026-06-19 — clean coherent clip in ~85 s).** GGUF route on the
box's installed LTX-2 stack (`comfyui-vrgamedevgirl` + `ComfyUI-GGUF` + KJNodes): `UnetLoaderGGUF` ←
`10Eros_v1-Q4_K_M.gguf` (14.3 GB → `models/unet`); `DualCLIPLoaderGGUF` ← [gemma-3-12b TE (12.8 GB →
`models/text_encoders`), `ltx-2.3_text_projection_bf16.safetensors` (2.3 GB), type **`ltxv`**];
`VAELoaderKJ` ×2 ← `LTX23_video_vae_bf16` / `LTX23_audio_vae_bf16` (from `Kijai/LTX2.3_comfy`, ~1.8 GB).
Build with `apps/dreaming/build_10eros_i2v.py`: it clones ComfyUI's own `video_ltx2_i2v_distilled`
template, swaps those loaders in, **prunes the 2nd-stage spatial-upscale refine** (halves VRAM, no
upscaler download) and **bypasses the V3 `ResizeImageMaskNode`** (`scale_method` enum drift). Key
gotchas: (1) the flattened template wires `EmptyLTXVLatentVideo.length` → `GetImageSize.width`, so it
over-runs to ~505 frames and **melts past LTX's ~5 s window** — pin `length` to a literal (≤121).
(2) `ollama stop <model>` first — Ollama auto-loads and eats ~19 GB.
(3) **output resolution is hardwired** to an `EmptyImage → ImageScaleBy(0.5) → GetImageSize → 512×288`
path, NOT the seed; `longer_edge` only resizes the *conditioning* image. Pin
`EmptyLTXVLatentVideo.width/height` to literals (seed aspect, /32) **≥768** or **people render as a
particle-mist** (low res = mangled faces, REF rule #4). (4) pruning the refine stage leaves the base
stage on its **partial distilled sigma schedule** (8 steps front-loaded near σ=1, meant to hand to
stage 2) → undercooked detail; swap `ManualSigmas` for **`LTXVScheduler(steps≈20)`** for a full
single-stage denoise. (3)+(4) are *the* fix for "people are a mist of particles" on the minimal graph.
The fp8mixed all-in-one (34 GB)
and fp8 transformer (30 GB) do **not** fit 24 GB; GGUF Q4_K_M (or Q6_K, 17.8 GB) is the only clean fit.

**Node-pack install gotchas (for TenStrip's *full* UI workflows).** The `~/ComfyUI/.venv` is
**uv-managed and has NO `pip`** — `python -m pip install` silently no-ops ("No module named pip");
use **`uv pip install --python ~/ComfyUI/.venv/bin/python <pkg>`** (pin `torch==2.6.0+cu124` +
`numpy==2.4.4` via `-c` so a pack can't downgrade the core — numba/librosa try to). The full
`10Eros_10SNodes_*` workflows need: `10s-comfy-nodes`, `ComfyMath`, `comfyui-various` (+`soundfile`),
`ComfyUI-Easy-Use` (+`lark`), `RES4LYF` (+`pywavelets`/pywt), `ControlAltAI-Nodes`, `ComfyUI-mxToolkit`,
and `ComfyUI-LTXVideo` for `STGGuiderAdvanced`. **LTXVideo breaks on kornia 0.8.3** (`cannot import
'pad' from kornia.geometry.transform.pyramid`) — patch `pyramid_blending.py`: drop `pad` from the
kornia import and add `pad = F.pad` (old kornia `pad` was a thin `torch.nn.functional.pad` wrapper).
`RTXVideoSuperResolution` needs NVIDIA's proprietary MAXINE `nvvfx` SDK — leave it disabled and bypass
that one node (final super-res) in the workflow. Installed-pack order matters: `comfyui-vrgamedevgirl`
sorts after `ComfyUI-LTXVideo`, so it wins on the shared `LTXV*` nodes (the minimal recipe is unaffected).

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

## The ComfyUI client/converter (`apps/dreaming/comfy_client.py`)

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

**Known gap (found 2026-06-19, smoke-testing the `TenStrip/LTX2.3-10Eros` workflow):** the converter
does **not yet resolve virtual nodes** — KJNodes `SetNode`/`GetNode` (set/get-by-name routing),
single-value widget nodes (`mxSlider`/`PrimitiveFloat`/`TwoWaySwitch`/`Sigmas Easing`), and reroutes.
ComfyUI's own *Save (API)* inlines these; `ui_to_api` emits them as real API nodes, so any input that
wires *through* a Get/Set points at an unknown `class_type` → `/prompt` 400 (in `10Eros_…_TiledSampler.json`:
**24 Get/Set nodes, 20 real inputs routed through them**). Wan-2.2 templates/Remix don't use Get/Set, so
this only bites on LTX-2 community graphs. Fix = a **virtual-node pass** (map each `SetNode` title→its
source link; rewrite each matching `GetNode` consumer to that source; inline single-value widget nodes),
a sibling to the existing subgraph/bypass passes. The converter is otherwise robust on this graph
(91 nodes, no subgraphs, zero bypassed nodes, `MarkdownNote` correctly dropped).

Helpers built around it: `test_aio.py` (single all-in-one checkpoint → T2V) and `test_gguf_moe.py`
(dual-expert GGUF, non-distilled, real CFG).

---

## VRAM / OOM / GGUF gotchas (24 GB)

- **Restart ComfyUI for a clean slate** (~3.5 GB desktop baseline, ~20 GB free). Leftover resident
  models from prior gens cause OOM. ComfyUI now runs as a **systemd user service** — restart with
  **`systemctl --user restart comfyui.service`**, NOT `fuser -k 8188/tcp` + manual relaunch: systemd
  auto-respawns it, so a port-kill races the respawn and you collide on the port / DB lock (seen
  2026-06-16). If you ever do launch by hand, never `pkill -f "main.py …"` with a pattern that also
  matches your own shell command (it kills the tool's shell; symptom: exit code 144).
- **OOM is usually resolution, not the model.** Step down: 640×1152 → 576×1024 → 512×896 → 480×832.
- **A completing run's speed ceiling is the text-encoder offload, not resolution.** On torch < 2.8
  ComfyUI falls back to the legacy ModelPatcher (startup warns: *"DynamicVRAM support requires
  Pytorch 2.8"*) and **under-evicts** — it parks ~8.6 GB of the 10.8 GB umt5 encoder instead of
  dropping it, so the Wan 2.2 14B expert streams ~8 GB off CPU *every step* even on an otherwise-idle
  24 GB GPU. Measured 2026-06-16, 81f/720×1280/4+4: **9:47** encoder-parked → **3:16 with `--disable-smart-memory`**
  (~3.0× faster; both experts then log `loaded completely … full load: True`, zero offload). The flag is
  now baked into `comfyui.service` + `start-comfyui.sh` (opt out per-launch with `COMFY_SMART_MEMORY=1`).
  Trade-off: no cross-run model cache, so tight iterate-in-UI loops pay a reload.
  **Caveat — not bulletproof on torch 2.6:** the flag reliably evicts on a *fresh* server, but a later
  *warm* run was seen to regress to full offload (`0.00 MB usable`, TE not evicted) with **no contention** —
  the legacy patcher's nondeterminism. Guaranteed-clean recipe for a heavy run: **`systemctl --user restart
  comfyui.service` first**. The durable fix is **torch ≥ 2.8** (DynamicVRAM patcher).
- **VRAM saturation → CPU-offload thrash → catastrophic slowdown.** A non-distilled GGUF run at
  640×1152/81f filled VRAM, forced layer offload to RAM, and ran **>33 min then timed out**. Keep
  the model+activations inside VRAM. Contention with **live Ollama inference** is the common trigger
  on this box: Ollama holding 4–5 GB drops ComfyUI's "usable" estimate to near-zero, forcing a
  whole-model offload (or an outright OOM in self-attention at high frame counts). Free it first
  (next bullet).
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
  closes the quality gap at low step counts. *Datapoint (2026-06): NSFW finetunes have now landed on
  this line — `Sulphur-2`, `10Eros` on LTX-2.3 — but 2.3 is 22B / GGUF-bound on 24 GB and shuns
  distill LoRAs, so it's good-when-slow, not yet the inflection.*
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

**Freeing Ollama's VRAM by hand (until the lease lands):** `ollama` runs as a **system service**
(`ollama.service`, as the `ollama` user), so its runner is **not killable as your user** — it needs
root. Worse, **`ollama stop <model>` can wedge**: a model pinned by a live client (Hermes'
connection pool) sticks in `Stopping…` indefinitely, even after keep-alive expiry (seen 2026-06-16
with 16 open connections; two `ollama stop` calls had no effect). The reclaim that actually works is
**`sudo systemctl restart ollama.service`**. This is the substrate thesis in miniature: cooperative
`stop`/`/free` fail, and only privileged PID-owned admission control reliably reclaims the GPU.

**Output the wallpaper consumes:** short seamless-loop `webm`/`mp4`, keyed off `agent.json` state,
played by a QML `Video` element (not yet built). Generation is offline/cached to avoid live VRAM
contention.

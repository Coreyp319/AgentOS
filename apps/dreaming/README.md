# apps/dreaming — local video generation backend

> **Spike / pre-shippable.** The *generation* backend works (all five paths validated
> 2026-06-16, below), but the feature is gated on the unbuilt VRAM coordinator and the
> ADR-0005 tx. The design council reframed the medium — see
> **[ADR-0009](../../docs/adr/0009-dreaming-shader-primary-video-as-texture.md)**, which
> supersedes [ADR-0008](../../docs/adr/0008-dreaming-via-local-video-gen.md).

ONE local backend (ComfyUI) shared by two surfaces, **two distinct media**:

- **Surface A — ambient "dreaming"** (`agentosd` driven): the procedural shader stays the
  primary renderer of agent state; a generated clip, if present, is a **texture the shader
  warps** (dream-as-texture, `../dream-as-texture/`), SFW-only, muted, post-graded — *not*
  a video-loop-keyed-by-state. Consumer not built.
- **Surface B — KRunner "generate"** (user driven): `video: <prompt>` → text-to-video.
  Backend works; the UX is **not** shippable (must become notification-as-control, opt-in,
  consent-gated — ADR-0009 §2). Currently auto-opens the result, a consent breach to fix.

## Pieces

| File | Role |
|---|---|
| `comfy_client.py` | Shared backend client. Converts a ComfyUI UI workflow → API graph, submits, returns the mp4 path, and exposes `free` (VRAM-yield lever). Pure stdlib. |
| `krunner_video_runner.py` | Surface B. A Plasma 6 **D-Bus** KRunner runner (no C++ build) that dispatches `video: <prompt>` to the shared backend. |
| `dist/agentos-video.desktop` | KRunner registration for the D-Bus runner. |

## Backend (ComfyUI)

Installed at `~/ComfyUI` (uv-managed Python 3.12 venv, CUDA torch). Launch:

```sh
cd ~/ComfyUI && .venv/bin/python main.py --listen 127.0.0.1 --port 8188
```

UI at <http://127.0.0.1:8188>; HTTP API on the same port.

Models (24 GB / RTX 4090, fp8 where possible): **Wan 2.2** (Apache-2.0; 5B TI2V
+ 14B T2V with 4-step lightx2v LoRAs) and **HunyuanVideo 1.5** (Tencent
community license — note EU/UK/KR territory exclusion before any redistribution).

## Programmatic use

```sh
# text-to-video via a shipped ComfyUI template, mp4 lands in ~/ComfyUI/output
python3 comfy_client.py run-template <template.json> --prompt "a calm aurora over hills" --length 49

# release VRAM so Ollama / nimbus-flux can reclaim the GPU (ADR-0004 yield)
python3 comfy_client.py free
```

## KRunner use

Already registered + running on this box. To activate in the live launcher,
restart KRunner so it loads the new D-Bus plugin:

```sh
kquitapp6 krunner 2>/dev/null; kstart krunner   # or: log out/in
# then, in KRunner (Alt+Space):   video: a neon city at night
```

Manual (re)setup:

```sh
# deps (system python): python3-dbus, python3-gobject   # present on this box
cp dist/agentos-video.desktop ~/.local/share/krunner/dbusplugins/
python3 krunner_video_runner.py &     # or a --user systemd unit
```

Verify the service without restarting krunner:

```sh
dbus-send --session --print-reply --dest=org.agentos.krunner.video \
  /krunner org.kde.krunner1.Match string:"video: a neon city"
```

## Validation (2026-06-16, RTX 4090 24 GB)

All generations via the HTTP API through `comfy_client.py`; ComfyUI web UI live
at :8188 (same templates). mp4s in `~/ComfyUI/output/`.

| Test | Model | Result |
|---|---|---|
| `wan5b_sfw` | Wan 2.2 5B | ✅ h264 768×432, 49f, 2.0s (~30s) |
| `wan5b_nsfw` | Wan 2.2 5B | ✅ h264 768×432, 49f, 2.0s (~64s) |
| `wan14b_sfw` | Wan 2.2 14B | ✅ h264 640×640, 81f, 5.0s (slow — both shards swap on 24 GB) |
| `hunyuan_sfw` | Hunyuan 1.5 | ✅ h264 768×432, 49f (~234s) |
| `hunyuan_nsfw` | Hunyuan 1.5 | ✅ h264 768×432, 49f (~220s) |

`comfy_client.py` proven to convert flat + **subgraph** templates, expand
**bypassed (mode 4)** nodes as pass-through, backfill version-drift required
inputs, and release VRAM via `/free`.

## Status — spike, gated on the substrate (per ADR-0009)

Working *generation* backend; the safety / coordination / UX layer around it is not built.
Open blockers before any of this ships:

- **Coordinator (ADR-0009 §3):** `agentosd` must own the ComfyUI PID + admission-control
  (predict-before-load) + `SIGKILL` backstop. `POST /free` measured freeing **0 VRAM** — it
  is a hint, not the evict lever. Video-gen and live inference are mutually exclusive on VRAM.
- **Surface B UX (§2):** replace auto-`xdg-open` with notification-as-control
  (Preview / Set-as-wallpaper / Discard); first-run consent gate; `EnabledByDefault=false`;
  fail-closed NSFW red-line guard; plain failure copy (no traceback in the toast).
- **Surface A (§2):** dream-as-texture consumer (`../dream-as-texture/`), software-decode +
  `ShaderEffect` grade (`../video-wallpaper/`), system-owned post-grade, NSFW hard-walled,
  muted. Gated behind the on-session loop-seam run.
- **Security:** authenticate the D-Bus `Run()` (currently unauthenticated → GPU DoS).
- **Model preference (Corey, 2026-06-16): Wan 2.2 14B is the quality pick** — visibly better
  than 5B in the output folder. Viable for *both* surfaces because generation is async + cached
  + admission-gated: latency isn't the binding constraint, **VRAM contention is** (handled by the
  coordinator, ADR-0009 §3). Pair with the 4-step lightx2v LoRA for speed. 5B / Hunyuan-distilled
  stay the low-VRAM / fast fallback. Per-surface default workflow/model config still TODO.

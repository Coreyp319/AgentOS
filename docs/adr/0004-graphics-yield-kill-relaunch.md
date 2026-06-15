# ADR-0004: Graphics VRAM yield via conditional kill/relaunch

- Status: Accepted
- Date: 2026-06-15

## Context
On a single 24GB GPU, the always-on `nimbus-flux` wallpaper renders ray-traced GI
(`bevy_solari`, ~3.5GB resident) while Hermes wants to serve 17–21GB models. They
collide only for the largest model (21GB + 3.5GB > 24GB). Two findings constrain the
fix:

- **Spike #2:** `nimbus-flux` cannot shed VRAM live. `bevy_solari`'s acceleration
  structure (BLAS/TLAS, the dominant cost) exposes no clear-API; `SIGSTOP` frees zero
  VRAM; in-engine live shedding recovers only ~50–150MB — useless against a 17–21GB
  model.
- **Research:** there is no hardware or software VRAM partitioning on a consumer 4090.
  MIG is datacenter-only (the 4090 is absent from NVIDIA's supported list); per-process
  software caps are unreliable (PyTorch's `set_per_process_memory_fraction` demonstrably
  overruns). The state of the art for sharing one GPU is read-pressure-and-evict/restart.

## Decision
The graphics yield is **conditional kill/relaunch**:
- Trigger only when `model_vram + graphics_vram > total_vram` (rare — the default 17GB
  model fits alongside the wallpaper; only the 36B model and Blender renders collide).
- To yield: kill `nimbus-flux` and relaunch with `NIMBUS_FLUX_RT=0` (~0.5–1.5GB freed,
  ~800ms flicker, ~zero new engine code). Optionally evict idle Ollama models via
  `ollama stop` / `keep_alive=0` to hand VRAM back.
- Restore ray-tracing when inference goes idle.

## Consequences
- Coarse (a visible ~800ms flicker) but rare, so the user-visible cost is small.
- `agentosd` decides via NVML VRAM reads + Ollama `/api/ps` (see the v0 monitor).
- Live in-engine shedding is explicitly out of scope until/unless `bevy_solari` grows a
  way to release its acceleration structure.

## Real-data refinement (2026-06-15)
The read-only monitor (`agentosd monitor`), run against the live box with per-process
NVML attribution, corrected the premise of this ADR:

- The graphics footprint is **dominated by ordinary user apps** (firefox, VS Code,
  spotify, plasmashell, kwin) — ~2.5GB *even with nimbus-flux not running*. `agentosd`
  cannot and must not kill those.
- Therefore the **wallpaper-RT eviction lever is conditional and secondary**: it frees
  VRAM only when nimbus-flux is actually running, and even then ~1.5GB against a 21GB
  model. `llama-server` was measured at **19.5GB** for the 18GB-reported 27B model, so
  headroom is thinner than the self-reported sizes suggest.
- The **primary** arbitration lever is therefore **model-side**: fit the model to the
  current real budget (pick/swap a smaller quant; evict idle Ollama models via
  `ollama stop`), leaning on `OLLAMA_MAX_LOADED_MODELS=1` + `keep_alive`. Wallpaper-RT
  eviction is a minor add-on, applied only when nimbus-flux is the swing consumer.
- The monitor now credits the eviction saving only when nimbus-flux is detected among
  the graphics processes; otherwise the verdict states no wallpaper is available to evict.
- The 36B model (Q4, ~21.8GB est.) does **not** fit alongside a normal desktop on 24GB
  and needs CPU offload regardless — the truce mainly helps the ~18–20GB range.

# ComfyUI — standalone unit (disabled by default)

ComfyUI is the local image/video generation backend for the dreaming pipeline. **It is NOT
run as an always-on boot service** — the agentosd VRAM coordinator owns its lifecycle.

## Who starts ComfyUI?
The dreaming path (`lucid_web` / `create_from_image`, via `lucid_linear.lease_spawn`) asks the
coordinator to **Spawn a coordinator-owned ComfyUI under a batch lease** (`spikes/dreaming/
start-comfyui.sh`), so a preemption can SIGKILL it and reclaim VRAM (ADR-0006/0010/0015). ComfyUI
comes up on demand when a dream needs it and is released (killed) when idle or preempted.

An **always-on** `comfyui.service` breaks this:
- it holds `:8188`, so `start-comfyui.sh`'s port-race guard refuses the coordinator-owned Spawn
  (`exit 3`) and **every dream fails open — requests never reach ComfyUI**; and
- it squats VRAM idle, tightening the admission knife-edge.

So `apply.sh` installs the unit but leaves it **disabled** (and self-heals a previously-enabled
one). This is the ADR-0015 contract: *"the always-on comfyui.service is intentionally disabled;
start it by hand only for manual ComfyUI work (no dream running)."*

## Install / remove
```
./apply.sh      # install the unit, DISABLED (does not enable/start it)
./restore.sh    # disable + remove the unit (leaves ComfyUI itself untouched)
```

## Manual standalone ComfyUI (UI iteration, no dream running)
```
systemctl --user start comfyui.service     # http://127.0.0.1:8188 (UI + HTTP API)
systemctl --user stop  comfyui.service     # before running a dream again
journalctl --user -u comfyui -f
```
Do not leave it running while dreaming — the coordinator can't own a ComfyUI it didn't spawn.

## VRAM coexistence
ComfyUI loads **no model weights until a job is queued** — the idle server holds only the CUDA
context (~0.5 GiB). Job-time VRAM (Wan 2.2 / Hunyuan, the heavy lane) is arbitrated by the
agentosd lease (ADR-0006/0010), which evicts this batch lane when interactive work preempts.

## Notes
- `--listen 127.0.0.1`: loopback only (matches `COMFY_HOST=127.0.0.1:8188`).
- `--preview-method latent2rgb`: near-free live denoise preview; no extra model/VRAM.
- This unit is intentionally self-contained (no dependency on throwaway `spikes/`); it mirrors
  `spikes/dreaming/start-comfyui.sh` (the launcher the coordinator actually Spawns).

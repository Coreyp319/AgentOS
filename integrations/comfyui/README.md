# ComfyUI — boot service (dreaming backend)

Brings up the ComfyUI HTTP API + UI on `http://127.0.0.1:8188` at login, so the
dreaming pipeline (`spikes/dreaming/comfy_client.py`, which is an HTTP client and
assumes a *running* ComfyUI) always has a backend without a manual launch.

## Install / remove
```
./apply.sh      # install + enable + start the --user service
./restore.sh    # disable + remove the unit (leaves ComfyUI itself untouched)
```

## VRAM coexistence
ComfyUI loads **no model weights until a job is queued** — the idle server holds only
the CUDA context (~0.5 GiB), so it coexists with the live RT wallpaper. Job-time VRAM
(the heavy lane: Wan 2.2 / Hunyuan) is arbitrated by the agentosd VRAM coordinator /
lease (ADR-0006/0010), which evicts this batch lane when interactive work preempts.

If you ever want ComfyUI to leave a hard headroom margin for the compositor even
mid-job, add `--reserve-vram <GiB>` to `ExecStart` in `comfyui.service` (omitted by
default so the video model gets the full budget; the lease is the real safety net).

## Notes
- `--listen 127.0.0.1`: loopback only (matches `COMFY_HOST=127.0.0.1:8188`).
- `--preview-method latent2rgb`: near-free live denoise preview; no extra model/VRAM.
- Logs: `journalctl --user -u comfyui -f`
- This unit is intentionally self-contained (no dependency on throwaway `spikes/`); it
  mirrors `spikes/dreaming/start-comfyui.sh`.

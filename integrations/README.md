# integrations/ — wiring AgentOS to its neighbors

AgentOS is the resource+safety substrate; the heavy lifting is done by neighbors it
coordinates. This directory captures how those neighbors are wired into the system —
including what comes up **at boot** as `--user` systemd services.

## The boot stack

| Service | Unit | Endpoint | Status |
|---|---|---|---|
| **AgentOS status panel** (boot-health view; opens at login) | `status-panel/agentos-status-panel.service` | http://127.0.0.1:9123 | `status-panel/apply.sh` |
| **Hermes agent** (gateway daemon: kanban, cron, delegation, messaging) | `hermes/gateway/hermes-gateway.service` | — | already installed ✓ |
| **Hermes web dashboard** (config, sessions, kanban board) | `hermes/dashboard/hermes-dashboard.service` | http://127.0.0.1:9119 | `hermes/dashboard/apply.sh` |
| **ComfyUI** (dreaming backend) | `comfyui/comfyui.service` | http://127.0.0.1:8188 | `comfyui/apply.sh` |
| Ollama (model runtime) | system unit (`ollama.service`) | http://127.0.0.1:11434 | already installed ✓ |

The **status panel** is the front door: it opens in the browser at login and shows the
live state of *every* service below (plus the Nimbus desktop services from the WhiteSur
pack — wallpaper, reactivity bridges, notifications, theming), with quick links into the
Hermes dashboard and ComfyUI. Its service catalog is editable — see `status-panel/README.md`.

`hermes/gpu-coordinator/` is a Hermes *plugin* (not a systemd unit): it holds the
agentosd interactive VRAM lease around Hermes inference so live AI preempts the
overnight dream/batch lane (ADR-0006/0010).

## Bring up everything not yet running
```
./apply-all.sh        # installs the dashboard + ComfyUI services (idempotent)
```
The Hermes gateway and Ollama are already installed and enabled, so `apply-all.sh`
leaves them alone and just ensures the two missing pieces are up.

## VRAM note
ComfyUI is a heavy VRAM consumer, but idle it loads no weights (just the CUDA context),
so it coexists with the live RT wallpaper. Job-time VRAM is serialized by the agentosd
lease, which evicts the batch lane on interactive preempt. See `comfyui/README.md`.

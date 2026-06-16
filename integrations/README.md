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
| **Lucid** (interactive dream-loop surface, ADR-0015) | `lucid/agentos-lucid.service` | http://127.0.0.1:8765 | `lucid/apply.sh` |
| Ollama (model runtime) | system unit (`ollama.service`) | http://127.0.0.1:11434 | already installed ✓ |

The **status panel** is the front door: it shows the live state of *every* service below
(plus the Nimbus desktop services from the WhiteSur pack — wallpaper, reactivity bridges,
notifications, theming), with quick links, inline "Why?" logs, and copy-don't-execute
recovery. Per ADR-0017 it opens at login **only when something needs attention** (clean
boots stay silent; the keyhole tray carries the calm). Its catalog is editable — see
`status-panel/README.md`; the cross-surface division of labor is in `design/surface-labor.md`.

`hermes/gpu-coordinator/` is a Hermes *plugin* (not a systemd unit): it holds the
agentosd interactive VRAM lease around Hermes inference so live AI preempts the
overnight dream/batch lane (ADR-0006/0010).

## Bring up everything not yet running
```
./apply-all.sh        # installs dashboard + ComfyUI + Lucid + status-panel services (idempotent)
```
The Hermes gateway and Ollama are already installed and enabled, so `apply-all.sh`
leaves them alone and just ensures the remaining pieces are up.

## VRAM note
ComfyUI is a heavy VRAM consumer, but idle it loads no weights (just the CUDA context),
so it coexists with the live RT wallpaper. Job-time VRAM is serialized by the agentosd
lease, which evicts the batch lane on interactive preempt. See `comfyui/README.md`.

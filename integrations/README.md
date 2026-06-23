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
recovery. Per ADR-0026 it opens at login **only when something needs attention** (clean
boots stay silent; the keyhole tray carries the calm). Its catalog is editable — see
`status-panel/README.md`; the cross-surface division of labor is in `design/surface-labor.md`.

`hermes/gpu-coordinator/` is a Hermes *plugin* (not a systemd unit): it holds the
agentosd interactive VRAM lease around Hermes inference so live AI preempts the
overnight dream/batch lane (ADR-0006/0010).

## Install / uninstall — the component driver
Every capability is a row in **`components.conf`** (the single source of truth) driven by
`install.sh` / `uninstall.sh`. Each is opt-in/opt-out, idempotent, and independently reversible:

```
./install.sh                 # interactive checklist (default-on preselected)
./install.sh --list          # explain the architecture + the registry grouped by how each part ties in
./install.sh --defaults      # the default local stack, non-interactive
./install.sh --only lucid,share-hub
./install.sh --without comfyui
./install.sh --all
./uninstall.sh [same flags]  # the aggregate reverse (reverse order)
./apply-all.sh               # thin back-compat wrapper = install.sh --defaults
```

The driver **stays user-scope and never escalates**: a component whose `root` is `sudo`
(the Firefox policy pin) or `manual` (tailscale exposure) is **printed at the end** for you to
run, not executed. A component that fails logs and the run continues (no half-applied abort).
Add/retire/re-default a capability by editing one line in `components.conf` — no driver change.
The Hermes gateway and Ollama are already enabled, so the drivers leave them alone.

### Desktop right-click → Lucid ("Create Video from Image")
Two `no`-root components install the local halves — **`dolphin-create`** (ServiceMenu `.desktop`,
a static file → survives all restarts) and **`browser-host`** (native messaging host installed to
`~/.local/share/agentos/` + per-browser manifests → persistent). The Firefox **extension** survives
a Firefox restart only via the **`firefox-pin`** component: an **AMO-signed XPI** (committed at
`browser-create-video/signed/`, with a `.sha256` sidecar) staged to a **root-owned**
`/usr/local/lib/agentos/` and **merged** into `/etc/firefox/policies/policies.json`
(`force_installed`). That's the lone root step, so it's printed:
```
sudo browser-create-video/policy/apply-policy.sh     # signature-verified, idempotent, merge-safe
```
Re-signing is a rare developer step (`browser-create-video/sign.sh`, only on an extension change).
Full rationale + Chromium notes: `browser-create-video/README.md`.

## VRAM note
ComfyUI is a heavy VRAM consumer, but idle it loads no weights (just the CUDA context),
so it coexists with the live RT wallpaper. Job-time VRAM is serialized by the agentosd
lease, which evicts the batch lane on interactive preempt. See `comfyui/README.md`.

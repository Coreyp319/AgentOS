# Relationship to the precursor pack (WhiteSur / Nimbus)

> Reference map, not a decision. The governing decision is [ADR-0001](adr/0001-substrate-not-orchestrator.md)
> (substrate, not a new desktop). This note answers a recurring question: *which pack files have
> representation in this repo, and in what form?* Verified 2026-06-23.

## The two repos

The desktop AgentOS sits under is the **Nimbus pack** at `~/whitesur-cachyos-pack` — a WhiteSur/macOS
re-skin of CachyOS/Plasma 6, organized as 12 numbered modules (`1-base` … `12-dreaming`). It is a
**separate git repo**, the precursor to this project.

**AgentOS inherits the pack's *intent*, not its *code*.** This repo vendors **zero** pack files. Where
an `integrations/` component engages a pack module it does exactly one of three things:

- **Re-express** — carry the look forward in a new engine (theming → Union CSS).
- **Supersede** — replace the mechanism with a substrate-native one (notifications → Plasma-native;
  window-reactive → D-Bus → Rust sink; shader dreaming → ComfyUI video).
- **Leave alone** — point back to the pack as canonical and only add a thin glue/data layer
  (the reactive wallpaper).

The bulk of the desktop look (base, krunner, login/lock, system QoL, Dolphin quicklook, app-unify, the
shader engine, the local-AI Modelfiles) has **no copy here, by design**.

## Module map

| Pack module | AgentOS representation | Relationship |
|---|---|---|
| `1-base` (WhiteSur+macOS install) | — | **pack-only** |
| `2-settings-refine` (CoreyLavender, Kvantum, icons) | `integrations/aurora-theme`, `aurora-panel`, `design` | **re-expressed** |
| `3-krunner-finder` (spotlight-blur, row-tweak, claude-runner) | — | **pack-only** |
| `4-login-lock` | — | **pack-only** |
| `5-system-qol` (fish) | — | **pack-only** |
| `6-local-ai` (Modelfiles, kv-cache, hermes-forge, ui-audit) | `integrations/hermes`, `integrations/models` (different concern); ui-audit → [ADR-0034](adr/0034-deterministic-ui-audit-and-screenshot-auditability.md) | **mostly pack-only** |
| `7-notifications` (swaync) | `integrations/swaync-aurora`, `swaync-race`, `aurora-notifications` | **superseded → Plasma-native** |
| `8-dolphin-quicklook` | — | **pack-only** |
| `9-gpu-effects` (aurora wallpaper, window-reactive) | `integrations/reactive-wallpaper`, `window-drag-wind` | **glue net-new; renderer inherited** |
| `10-shader-engine` (hexen, nimbus-flux, dream) | — (kill/relaunch target only) | **pack-only** |
| `11-app-unify` (chromium/electron/firefox/flatpak) | — | **pack-only** |
| `12-dreaming` (shader nightly-dream) | `spikes/dreaming`, `integrations/lucid*`, `comfyui` | **reframed (shader → video)** |

## The engaged modules, in detail

### Theming — `2-settings-refine` → `aurora-theme` (re-expressed)
`AuroraDark.colors` is the pack's `CoreyLavender` palette rebranded and WCAG-nudged (one focus-ring value,
one negative red, one selection bg; `aurora-theme/README.md`). The WhiteSur *shape* (two-radius, elevation
ramp) is re-expressed as a **Union CSS** style — the Plasma 6.7 CSS engine — replacing Kvantum
(`widgetStyle=Union`; `restore.sh` falls back to kvantum). Default-**off**, reversible. `aurora-panel`
(re-accents the panel the pack left WhiteSur-blue) and `design/` (instrument tokens) are net-new. See
[ADR-0042](adr/0042-continuous-ui-improvement-via-union-css.md). Vendors no pack files; the
`Nimbus-dark-refined` icons are referenced by name, assumed installed by the pack.

### Notifications — `7-notifications` → Plasma-native (superseded)
swaync is **retired** (`integrations/components.conf` tags `swaync-race` RETIRED 2026-06-23). plasmashell now
owns `org.freedesktop.Notifications`. `swaync-aurora` (a fresh CSS rewrite, *not* derived from the pack's
macOS-mimic style) and `swaync-race` (the abandoned boot-race; drop-ins left `.disabled`) are vestigial.
`aurora-notifications` is the live styling path — it restyles the *native* Plasma toast. All notification
components are **off by default**.

### Reactive wallpaper — `9-gpu-effects` → `reactive-wallpaper` (renderer inherited, feed net-new)
`reactive-wallpaper/apply.sh` ships **zero plugin files**. It plasma-scripts the live wallpaper to
`com.nimbus.aurora` + a `Style`, and explicitly *refuses* to copy QML over the installed plugin — it tells
you to run `~/whitesur-cachyos-pack/9-gpu-effects/install.sh` instead. The net-new repo contribution is the
**data feed**: `agentosd feed` writes `agent.json` + `heartbeat.json` (`crates/agentosd/src/feed.rs`),
consumed by the pack's plugin. `window-drag-wind` *supersedes* the pack's `aurora-bridge.py` window-reactive
daemon with a KWin script emitting `Gust()` over D-Bus `org.agentos.Wind1` → Rust `crate::wind` sink →
`wind.json` (see `docs/design/0023-window-drag-wind-producer.md`, [ADR-0030](adr/0030-reactive-wallpaper-mood-grammar-and-feed-disposer.md)).

### Dreaming — `12-dreaming` + `10-shader-engine/dream` → Lucid/ComfyUI (reframed)
The pack's dreaming was Bevy/GLSL/WFC with **no AI-gen meshes**. [ADR-0009](adr/0009-dreaming-shader-primary-video-as-texture.md)
supersedes [ADR-0008](adr/0008-dreaming-via-local-video-gen.md) (§1/§4/§6). AgentOS's "dreaming" is a
net-new ComfyUI text/image-to-video pipeline ("Lucid", `spikes/dreaming/lucid/`) sharing **zero** files with
the pack. The Modelfiles, `ollama-kv-cache.conf`, and `hermes-forge/` stay pack-only (Ollama-serving config
AgentOS deliberately doesn't reimplement — [ADR-0002](adr/0002-thin-gateway-configure-ollama.md)).
`integrations/models` (presence resolver/audit) and `integrations/hermes` (gpu-coordinator lease plugin,
[ADR-0006](adr/0006-hermes-plugin-no-fork.md)) are net-new substrate concerns, not ports. The autonomous
ui-audit cron is reframed under [ADR-0034](adr/0034-deterministic-ui-audit-and-screenshot-auditability.md),
with its implementation in `~/.hermes/skills/`, outside both repos.

## The one bidirectional case: `aurora.frag`

The agent-reactive shader uniforms (`uAgentState/uAgentBusy/uAgentWarm/uAgentSnag`) were **prototyped as an
AgentOS spike and upstreamed *into* the pack**, where they now live with `"AgentOS reactivity (spike)"`
comments. So in this one case the code flowed AgentOS → pack, and the **pack is canonical**.

The repo still carries `spikes/hills-reactive/aurora.frag`, but as of 2026-06-23 the two have **diverged**:

| | repo spike | pack canonical |
|---|---|---|
| path | `spikes/hills-reactive/aurora.frag` | `~/whitesur-cachyos-pack/9-gpu-effects/interactive-bg/contents/shaders/aurora.frag` |
| size / mtime | 70.3 KB · 2026-06-15 | 74.9 KB · 2026-06-23 |
| delta | frozen Jun-15 snapshot | +73 lines ahead — adds the "Prism" (style 15) iridescent-dispersion look the spike lacks |

This drift is **expected, not a defect**: `spikes/` is throwaway and excluded from the Cargo workspace
(CLAUDE.md), and the canonical reactive shader is the pack's. If a fix is needed, edit the pack's copy — do
not treat the spike as a source of truth. (Pattern context: [ADR-0046](adr/0046-graduate-live-products-from-spikes.md),
graduating live products out of spikes.)

## If you are auditing for drift or planning a migration

These live in **exactly one place — the precursor** — and have no representation here: `1-base`,
`3-krunner-finder`, `4-login-lock`, `5-system-qol`, `8-dolphin-quicklook`, `11-app-unify`, the
`10-shader-engine` engines (hexen/nimbus-flux/dream/journey-browser), and the `6-local-ai` Modelfiles +
`ollama-kv-cache.conf` + `hermes-forge`. Touch them in the pack repo, not here.

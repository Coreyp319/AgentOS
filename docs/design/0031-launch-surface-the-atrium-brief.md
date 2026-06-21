# Design brief — The launch surface (working name "the Atrium")

- Status: Decided (council synthesis, mediator). Proposes; code + human dispose.
- Date: 2026-06-20
- Mediator: design-discourse-mediator
- Relates to: ADR-0026 (boot stack + status panel — the contract this extends), ADR-0012
  (keyhole — steady-state "is it alive now", not duplicated), ADR-0001 (Plasma/KRunner is the
  launcher — don't reinvent), ADR-0005 (apply/rollback tx floor — still undelivered; recovery
  stays copy-don't-execute), `integrations/design/surface-labor.md` (the division-of-labor
  contract this adds a fourth verb to).
- ADR stub: `docs/adr/0031-launch-surface.md`

## The question
There is no single "open the thing on purpose" front door for the AgentOS service family
(Lucid :8765, Hermes board :9119, ComfyUI :8188, models :9124, Share :8770, status panel :9123).
The keyhole tray owns the ambient glance; the status panel owns boot-health diagnose/recover and
opens only on `attention>0`. Neither owns the deliberate *go-there* verb. Decide what the launch
surface is, its IA, its interaction/recovery model, its identity, and how it stays calm —
desktop AND phone over the tailnet.

## Decision (one direction)
**Launch is a legitimate fourth verb in the surface-labor contract, delivered as a thin layer
over what already exists — NOT a new app, NOT a grown status-panel `/` root, NOT a dashboard.**
The split is by device, because the launcher whitespace is real on phone and absent on desktop:

1. **Desktop front door = Plasma, not a web page.** Add KRunner reachability (via `.desktop`
   entries indexed by KRunner first; a `org.kde.krunner1` D-Bus runner only if usage earns it)
   so typing "lucid / board / models / share" opens the URL. This is the ADR-0001 reuse answer:
   KRunner *is* the launcher. The status panel keeps owning diagnose-and-recover.

2. **Phone front door = an installable PWA** served by the *existing* `status_panel.py` process
   at a new **read-only** route (a "launch view": `?view=launch` / `/atrium`), via a
   `manifest.webmanifest` + minimal service worker. The phone has no KRunner and no tray — the
   PWA is the only honest face there, and PWA installability works because `tailscale serve`
   gives the loopback service a stable HTTPS origin (raw `127.0.0.1` on desktop does not
   meaningfully install — which reinforces the device split).

3. **One contract, one register, one process.** Both renderers read the existing `/status.json`
   and `services.json`; both wear `instrument-tokens.md` verbatim; no second poller, no second
   health truth, no new daemon, no new port. The launch view is a render branch + two static
   routes.

## Information architecture
- **Reuse the existing taxonomy verbatim** (`services.json` groups: AI core · AgentOS substrate ·
  Ambient desktop · Notifications · Desktop QoL · Dreaming). Do not invent a launch/monitor/control
  taxonomy.
- **Derive launch/monitor from existing fields — zero schema change.** A service with a `url`
  (hermes-dashboard, comfyui, models, lucid) is a **destination** (door prominent). A service
  without a `url` (feeds, watchers, wallpaper) is **monitor-only** (status only — you cannot
  "open" the wallpaper feed; saying so would be dishonest).
- **One catalog gap to close:** the Share hub (:8770) is exposed over the tailnet
  (`agentosd-remote.sh:16,22`) but is **absent from `services.json`** — so the launchable set and
  the tailnet-served set do NOT actually agree today (the council assumed they did). Add a
  `share-hub` entry with its `url`/`health` so the launch view and the remote front are coherent.

## Interaction & recovery model
- **Launch = link-out only** (open a URL / `xdg-open`). Mutates nothing. Fully reversible by
  construction.
- **Recovery stays copy-don't-execute** (ADR-0026 rule 1; surface-labor rule 5). No start/stop
  buttons until ADR-0005's tx engine lands — at which point an actuator is its own future ADR.
- **Honest when blind, and distinguish the path from the box.** On phone over tailnet, three
  failures currently collapse to one "status unavailable": (a) service down, (b) panel server
  down, (c) tailnet/`tailscale serve` path down. Until the surface can tell (c) apart, do NOT
  ship a string that blames the box. Gate the "Copy fix"/"copied — run it" affordance off on a
  remote origin (you have no shell on the phone) — a copy you can't run is a quiet lie.
- **On-demand backends read as "wake," not "down."** ComfyUI is `on_demand` — its idle card is
  "dormant — tap to wake", not a failure.

## Identity & calm
- **No new app face, no new logo, no mascot.** The header `.mark` porthole stays; it should
  sample the same instrument horizon color as the keyhole's AuroraRing so they read as one object.
- **Add zero new color constants.** The launch view needs only `color-mix`-derived role aliases
  (`--launch-tile-fill`, `--launch-tile-hover`, `--launch-door = --inst-blue`) mirrored into
  `instrument-tokens.md`. The **reserved warm `#ff9957` is NOT spent** on "openable" or hover —
  warm stays the exclusive needs-you cue (`instrument-tokens.md:28`). A down/attention card may
  inherit the panel's amber edge ONLY because the panel already earned it there.
- **Motion budget (motion-designer's lane):** exactly one always-on ambient motion (the existing
  2px horizon breath); cards are still at rest. All other motion is edge-driven one-shots on true
  state change or user action. The WebGL backdrop stays **non-reactive** (`panel.html:99-104`) —
  the launcher is a still room; live mood lives on the wallpaper and tray. One
  `prefers-reduced-motion` block governs CSS transitions AND the WebGL backdrop. A flapping unit
  must resolve to a still state (no infinite `starting`/`stopping` breath).
- **Sound (sound-designer's lane):** NO launch-pad-specific sounds. The only legal audio is the
  pre-existing single swaync earcon on a new-failure-edge-after-boot (ADR-0026 rule 3). Per-service
  chimes are forbidden — they sonify the poll, not the edge. A phone-only, capability-gated,
  opt-in haptic mirror of that same edge is allowed.
- **Phone vs desktop is a cadence + density difference, not a layout fork.** Phone: slower poll
  (~8-10s foregrounded, suspend on hidden), larger thumb targets, hold the backdrop static on
  battery, chromeless `display: standalone`. Desktop: KRunner is primary, the view is a fallback.

## Accepted tradeoffs
- The launch view shows up to ~10s-stale health on phone (slow poll) — acceptable for a glance
  surface, and the existing `generated_at` stale-gate keeps it honest.
- Two renderers (KRunner desktop + PWA phone) could drift — mitigated by one `/status.json` +
  one `services.json` + one token register (the same coherence guarantee ADR-0026 rule 4 already
  enforces for panel↔tray).
- No personalization (favorites/pin) in v1 — ADR-0026 forbids it on the login panel. The phone
  PWA is a *different* JTBD (tapped daily, not viewed for 30s at login); whether it earns pinned
  doors is an open question for `personalization-loop-reviewer`, not assumed in.

## Open dependencies / unsatisfied preconditions
- **Standing-process cost is unmeasured.** ADR-0026's own backlog flags "should the panel server
  idle-exit/socket-activate rather than stand all session" as undecided. Adding a PWA leans toward
  always-on. Get a first-paint + idle-RAM + backdrop frame-time number from a spike before
  endorsing the always-installable posture. (Owner: design-technologist / rater-feasibility.)
- **The `acting` state is reserved but never emitted** (`--st-acting`, `instrument-tokens.md:46`;
  `feed.rs` never produces it). Any "launch while the agent is touching the desktop" affordance is
  blocked on the computer-use/kwin-mcp actuation path — ship nothing for it now. (Owner:
  interaction-designer + wayland-computeruse-reviewer.)

## Spike-first
Build `spikes/atrium/` (a copy of `panel.html` + a ~40-line patch adding the two read-only routes
to a copy of `status_panel.py`) and measure before any `integrations/` change (ADR-0005 floor).

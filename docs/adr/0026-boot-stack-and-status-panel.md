# ADR-0026: The boot stack and the status panel (read-only boot-health)

- Status: Proposed
- Date: 2026-06-16
- Relates to: ADR-0001 (don't reinvent — Hermes is the orchestrator, systemd is the service
  manager, Plasma is the control center), ADR-0012 (keyhole tray — the steady-state ambient
  instrument), ADR-0005 (apply/rollback tx — the reversibility floor, still undelivered),
  ADR-0006/0010/0013 (the VRAM coordinator the heavy services lease through).
- Design inputs: the 5-reviewer panel on the panel (a11y/UX/ambient/visual/craft) and the QoL
  design pass (interaction/ambient/delight/product). See `integrations/design/surface-labor.md`.

## Context

AgentOS gained a **boot stack**: the user's everyday AI + ambient desktop services now start at
login as `--user` systemd units — Hermes gateway (already present), the Hermes web dashboard,
ComfyUI, Lucid, plus the Nimbus desktop services (wallpaper, reactivity, notifications, theming),
with Ollama as a system unit. Installed/reversed per-piece (`integrations/*/apply.sh`,
`restore.sh`) and together via `integrations/apply-all.sh`.

On top of that sits a **status panel** (`integrations/status-panel/`): a small, stdlib-only,
loopback HTTP server that reports the live state of every service (systemd state + port
reachability) and renders it as a calm web page in the keyhole-instrument register. It is the
front door for the one question a boot stack creates — *"did everything come up?"*

This is a behavior change (login now relates to a browser surface; ~18 services autostart; the
panel retired an earlier dashboard-only opener), so per our conventions it earns an ADR — chiefly
to **bound** it. A "thing with ~18 autostarting services and a web console" exerts gravity toward
exactly the products ADR-0001 forbids.

## Decision

The boot stack and status panel are **read-only, reversible, and deliberately small**. Five rules:

1. **Read-only / copy-don't-execute.** The panel server has no write endpoints and never mutates
   system state. It *proposes* — "Copy fix" copies a `systemctl reset-failed && restart` line, a
   "bring stack up" affordance copies the `apply-all.sh` path — and the human *disposes*. This
   preserves "model proposes / code disposes" and keeps us off the wrong side of the ADR-0005
   reversibility line, which has no runtime tx engine yet.
2. **Honest when blind.** The payload carries a `generated_at` stamp; stale data or an unreachable
   server drops the panel to a grey "degraded" state and dims the (possibly wrong) list. It never
   looks confidently green while blind. `absent` / `down` / `ran ✓` / `ready` / `unknown` are
   distinguished, not flattened.
3. **The all-clear is silence.** The panel opens at login **only when `summary.attention > 0`**.
   A clean boot opens nothing; the keyhole tray and quiet wallpaper carry the calm. A *new failure
   edge after boot settled* earns exactly one swaync toast — boot-time amber never does.
4. **One health contract, many renderers.** `/status.json` is the single source; the keyhole tray
   consumes the same contract. The panel owns "did boot succeed + diagnose + recover"; the tray
   owns steady-state "is it alive right now". They do not duplicate each other. (See the
   surface-labor contract.)
5. **The catalog is hand-edited JSON.** Adding/removing a watched service is an edit to
   `services.json`, not a UI. That friction is intentional — it keeps the panel a viewer.

## Non-goals (decided, not deferred)

These are out of scope because they reinvent something we explicitly reuse, or cross the read-only
line without the ADR-0005 floor:

- **No start/stop/restart buttons** in the panel — that is systemd (and Plasma's service UI), and
  it crosses into mutation with no tx/rollback under it.
- **No metrics / graphs / history / uptime charts** — that is Grafana / the Hermes dashboard /
  the keyhole's VRAM slice. The panel answers a boolean per service, not "how much / how fast".
- **No catalog-editing UI, inline config, or log *streaming*** — service-manager territory.
  (A read-only, on-demand *log tail* on a failed row is allowed — it's diagnosis, not management.)
- **No personalization chrome** (favorites/reorder/themes) on a surface you view for 30 s at login.
- **No second live fleet view** — that is the keyhole tray's job.

## Consequences

- The panel stays cheap and trustworthy; the gravity toward "control center" is resisted by a
  citable decision, not a vibe.
- Recovery is bounded to copy-paste until ADR-0005 lands a real apply/rollback engine; only then
  would an in-panel actuator even be reconsidered (and as its own ADR).
- Known backlog this ADR does **not** fix: the wallpaper feed degrading a dead Hermes to plain
  `idle` rather than a quieter-than-idle stale posture (`feed.rs`); whether the panel server should
  idle-exit/socket-activate rather than stand all session. Both are flagged, not decided here.

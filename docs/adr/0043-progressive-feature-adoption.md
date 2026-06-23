# ADR-0043 — Progressive feature adoption from the status / Atrium surface

Status: Accepted (Phase 1 built — read-only catalog + one-click user-scope adopt/un-adopt)
Date: 2026-06-22
Relates to / amends: ADR-0026 (boot stack + status panel — read-only/no-buttons rule),
ADR-0031 (launch surface / Atrium), ADR-0039 (dispatch-an-agent — the out-of-sandbox worker
spine this reuses), ADR-0001 (substrate, don't-reinvent), ADR-0005 (apply/rollback tx —
the reversibility floor), ADR-0011 (model proposes, code disposes).

## Context

The installer (`integrations/components.conf` + `install.sh`/`uninstall.sh`) is a real,
reversible component registry: every capability is one pipe-separated row with an `apply`
and a `restore`, opt-in/opt-out, idempotent, user-scope-by-default. But it lives in a
terminal. AgentOS should let a user **progressively adopt features over time from the
status / Atrium page** — see what is installed, what is available, and turn one on — instead
of installing the whole stack up front or hand-editing config.

The friction: ADR-0026 and ADR-0031 deliberately make those surfaces **read-only**. Both list
as an explicit non-goal "**no start/stop/restart buttons (copy-don't-execute only)**". ADR-0026
foresaw this exact moment — "only [once a reversible apply/rollback tx exists] would an in-panel
actuator even be reconsidered (and as its own ADR)". This is that ADR.

What changed since ADR-0026 wrote that line: the **component registry now provides the
reversibility** ADR-0005 was meant to. Every registry row already pairs `apply` with `restore`
— a per-component transaction. An "adopt" is `install.sh --only <id>`; an "un-adopt" is
`uninstall.sh --only <id>`. So an adopt button is not an unbounded actuator; it is a registry
operation with a guaranteed inverse. That is materially different from the "start/stop/restart
button" ADR-0026 barred (an arbitrary verb on an arbitrary unit with no inverse), which is why
adoption is acceptable where that was not.

## Decision

### Scope the amendment — adoption only

We amend ADR-0026/0031's "no buttons" non-goal **only for registry adoption**, and only for the
`root: no` (user-scope) tier. The general "no start/stop/restart of arbitrary units" rule stands.
Diagnose/recover stays copy-don't-execute (ADR-0039); the dispatch path is unchanged.

### The safety spine — inherited verbatim from ADR-0039

The enforcement point is **the box, never the client**. The hardened, loopback-bound panel
(`ProtectHome=read-only`, `NoNewPrivileges=true`, `ProtectSystem=strict`) never mutates state
itself; it validates, records a ledger entry in `$XDG_RUNTIME_DIR`, and launches the actual
`install.sh`/`uninstall.sh` as a **transient `systemd-run --user` unit** outside the sandbox —
the identical mechanism `dispatch.py`/`dispatch_run.py` already use. Reused unchanged:

- **Anti-CSRF token** minted per process, served same-origin only (`GET /adopt/token`), required
  as `X-Adopt-Token`, plus a `Sec-Fetch-Site: cross-site` reject and a bounded body read.
- **Catalog-validation, not wire strings.** The POST carries a component *id* that only selects a
  trusted `components.conf` row; the command run is the registry's own `apply`/`restore` path, and
  `install.sh --only <id>` re-validates the id (`sel_csv` rejects an unknown id). No injection.
- **Atomic flock'd ledger** with dedupe / in-flight cap / cooldown / a reaper for a SIGKILLed
  worker, and an at-most-once `claim`.
- **Honest, reversible job states**: `queued → applying → adopted | failed` and
  `unadopting → available | failed`. A failed adopt is never reported as success.

### Two gates stronger than dispatch — because adoption installs software

1. **Local-origin only.** `POST /adopt` requires a provably-local origin (`origin.can_copy_fix`:
   loopback peer **and** loopback Host **and** zero forwarding headers). The phone (tailnet PWA)
   **sees** the catalog read-only — adopted/available badges — but the adopt/un-adopt button is
   **desktop-only**. Adopting installs services on the box; that is not a remote action. (Dispatch
   allowed the phone; adoption does not.)
2. **`root: no` only.** The worker runs under `NoNewPrivileges`, so it *cannot* escalate anyway.
   `root: sudo` (firefox-pin) and `root: manual` (tailscale-remote) components are **never**
   one-click; they render as `needs-you` and surface the exact printed command to run yourself —
   structurally identical to the existing copy-don't-execute one-liner. The driver already prints
   (never runs) these; the surface honors the same contract.
3. **Install-only for lease-bearing / self units.** `core-substrate` (the VRAM coordinator) and
   `status-panel` (this page) can be *adopted* from the panel but never one-click *removed* —
   stopping the coordinator SIGKILLs a running dream (lease kill-on-drop) and removing the panel
   kills the surface you're on. They surface no Remove button and un-adopt is refused server-side
   (`NO_ONECLICK_REMOVE`); take them down from a terminal. Mirrors dispatch.py's `NEVER_AUTO_UNITS`.

### "Is it adopted?" — a read-only, honest-when-blind detection scheme

`GET /components.json` parses the registry (same trim/skip as `_driver.sh`) and computes a state
per component from its install artifact, read-only — no `apply`, no `start`:

| tier | artifact probed (read-only) | adopted when |
|---|---|---|
| core / service | `systemctl --user is-enabled <unit>` | the unit file is present (`enabled`/`disabled`/`static`…); `comfyui` ships intentionally disabled, so *present* = adopted, not *enabled* |
| desktop (plasmoid) | `kpackagetool6 --type Plasma/Applet --list` | the applet id is listed |
| desktop (KWin script) | `kreadconfig6 kwinrc Plugins <id>Enabled` | `true` |
| desktop (aurora) | `kreadconfig6 kdeglobals KDE widgetStyle` | `Union` — the active style `restore.sh` reverts (apply/restore toggle the global; the CSS files persist by design, so the live setting, not file presence, is the honest adopted signal) |
| desktop (servicemenu / native host) | the installed `.desktop` / host manifest file | the file exists |
| privileged / remote | root policy file / `tailscale serve` state | rendered `needs-you` regardless (never one-click) |

A probe that cannot determine state (e.g. `kpackagetool6` absent on a headless host) returns
`unknown`, never a confident "available" — the same honest-when-blind rule the producers use.

### Kill-switch

`AGENTOS_ADOPT=0` disables the whole adopt path (the catalog stays viewable). Mirrors
`AGENTOS_DISPATCH`.

## Non-goals

- **No remote (phone) adoption.** Read-only catalog there; the button is desktop-only.
- **No sudo/manual one-click.** Those stay copy-don't-execute (printed).
- **No arbitrary start/stop/restart.** This is registry adopt/un-adopt only; ADR-0026's broader
  read-only rule otherwise stands.
- **No dependency resolver / ordering engine.** Adopt is one component at a time; the driver
  already continues-on-failure and components are independent.

## Consequences

- The status/Atrium surface gains its first registry-backed write action, gated harder than the
  dispatch write it reuses (local-only, root:no-only). The read-only posture is preserved for
  every other action and for every remote origin.
- Adoption is reversible by construction (the registry's `restore`), satisfying the
  "reversible by default" non-negotiable that ADR-0026 was waiting on ADR-0005 to provide.
- New surface: `GET /components.json`, `GET /adopt/token`, `GET /adopt.json`, `POST /adopt`, plus
  `adopt.py` (in-sandbox core) and `adopt_run.py` (out-of-sandbox worker), mirroring the dispatch
  pair. Tested headless; the privileged path gets a security review like ADR-0039's.

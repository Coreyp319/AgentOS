# ADR-0031: The launch surface — a fourth surface-labor verb (KRunner + a launch-view PWA)

- Status: Accepted — v1 **folded into production** (`integrations/status-panel/`, 2026-06-20): the
  launch view (`launch.html` at `/atrium` and `/?view=launch`), the PWA shell (manifest + service
  worker + icons), origin-aware doors + the server-emitted loopback signal (gap #4), the 1.5s status
  cache, and the KRunner `.desktop` launchers all ship and are tested (63 status-panel tests green).
  Remaining gates are **verification, not build**: the live `tailscale serve` PWA install on the box,
  and the S1 lease-aware door (gated on `agentosd lease` being installed). The spike (`spikes/atrium/`)
  and the §6 catalog/a11y fixes shipped earlier (commits `abcc011`, `30a07b3`, `3cbceb8`).
- Date: 2026-06-20
- Relates to: ADR-0026 (boot stack + status panel — this EXTENDS its renderer with a launch
  verb and inherits its five rules verbatim; it does NOT mutate `/` into a hub and does NOT
  supersede it), ADR-0012 (keyhole — keeps "is it alive now"; the launcher does not duplicate the
  live fleet view), ADR-0001 (Plasma/KRunner is the launcher — we add entries/a runner, not an
  app), ADR-0005 (apply/rollback tx floor still undelivered — in-surface actuators stay out of
  scope). Amends `integrations/design/surface-labor.md` with a fourth row.
- Design input: council synthesis (`docs/design/0031-launch-surface-the-atrium-brief.md`).

## Context
The boot stack created a service family with no single deliberate "open the thing" front door.
The keyhole tray owns the ambient glance; the status panel owns diagnose/recover and opens only
on `attention>0`. "Launch" is a fourth job and currently has no home. The risk: a "launch pad /
hub / dashboard" is exactly the control-center gravity ADR-0026 and the surface-labor contract
were written to resist.

## Decision
1. **Launch is a fourth verb in the surface-labor contract**, delivered as a thin layer over
   existing assets — not a new app, not a grown status-panel `/` root, not a second live fleet
   view, not a dashboard.
2. **Desktop = Plasma/KRunner** (`.desktop` entries first; a `org.kde.krunner1` runner only if
   usage earns it). The status panel keeps owning diagnose/recover.
3. **Phone = an installable PWA** served by the existing `status_panel.py` at a read-only launch
   view (`?view=launch` / `/atrium`), via `manifest.webmanifest` + a minimal service worker.
   Install works over the existing `tailscale serve` HTTPS origin; no new port, no new daemon.
4. **One contract, one register.** Both renderers read `/status.json` + `services.json` and wear
   `instrument-tokens.md`. Launch/monitor is derived from the `url` field (present = destination;
   absent = monitor-only) — zero schema change.
5. **Launch = link-out only; recovery = copy-don't-execute** (no start/stop until ADR-0005). On a
   remote origin the copy-the-fix affordance is suppressed (no shell on the phone) and the
   degraded state distinguishes a tailnet-path failure from a box failure once the data can.
6. **Catalog coherence (three drift points, all verified):** reconcile `services.json` ⊕
   `components.conf` ⊕ `agentosd-remote.sh` so the launchable set and the tailnet-served set
   actually agree: (a) add the Share hub (:8770), tailnet-served but absent from `services.json`;
   (b) mark ComfyUI :8188 not-tailnet-served (it has a `url` but is deliberately not exposed —
   `agentosd-remote.sh:6,50` — so on a remote origin it is a *dead door*, render monitor-only /
   "desktop only"); (c) add the status panel's own :9123 `url`/`health` (tailnet-served, no
   `services.json` entry). Add a one-line test: every `url`-bearing service on a remote origin maps
   to a served port.
7. **Origin-aware doors.** "Is a destination" is gated on `url` present **AND** served-on-this-origin
   — on a remote origin, rewrite loopback→tailnet host and suppress un-served ports; never a dead
   door. The "Copy fix" affordance is suppressed via a **server-emitted loopback signal**, not the
   client `location.host` read (`panel.html:489`, which a reverse proxy rewrites).

## Non-goals (inherited from ADR-0026, re-asserted for the launch surface)
- No start/stop/restart buttons (copy-don't-execute only).
- No metrics/graphs/history.
- No favorites/reorder/themes/personalization chrome in v1 (the phone-daily JTBD personalization
  question is deferred to personalization-loop-reviewer, not decided here).
- No second live fleet view (the keyhole owns that).
- No reactive backdrop (the launcher is a still room; live mood is the wallpaper/tray).
- No new logo/mark, no second tray icon, no new color constants.
- No launch-pad-specific sounds (only the pre-existing swaync new-failure earcon).

## Spike results (`spikes/atrium/`, measured 2026-06-20 — see its `MEASUREMENTS.md`)
- **Degraded-state contrast (gap #1, the live a11y cap): RESOLVED.** Measured: the original
  `opacity:.5;saturate(.6)` treatment drops body text to **4.3:1 — fails WCAG AA**; the cap was
  real. The fix (dim the *signal* — dots/glass — keep the *text*) measures **12.69:1**. The launch
  view ships the fix; the production `panel.html:159` rule should adopt it when the view folds in.
- **Standing cost (Q1): RESOLVED for the launch view.** It is a *still room* — no WebGL/canvas/rAF
  → ≈0 GPU, no reactive backdrop. As a route on the existing status-panel daemon it adds **0 new
  standing processes**. Shell is ~20 KB, 1 request, 0 blocking external resources.
- **PWA installability (gap #5): static PASS** (manifest + SW + 192/512/maskable icons). The
  always-installable posture is now cost-justified (≈0 marginal cost), softening the ADR-0026
  idle-exit tension for *this* surface.
- **Server loopback signal (gap #4) + origin-aware doors (gaps #2/#3): built + unit-tested** in the
  spike (a shell command never reaches a remote client; an un-served service is never a dead door).

## Cross-surface a11y fix (found by the spike's adversarial review — now FIXED)
- `--inst-label` measured **3.86:1 over the glass — failing WCAG AA**, contradicting the claim in
  `integrations/design/instrument-tokens.md` that it "clears 4.5:1". This is the *shared* instrument
  token (status panel + keyhole + this view). The same defect was already found once for the Lucid
  web app (ADR-0019 / G7, fixed there to `#878c9b`) but never propagated back to the canonical
  token. **Fixed here by lifting `#7a8090 → #878c9b` across the canonical doc, `panel.html`, both
  keyhole `InstrumentPalette.qml`, and the spike** — now measures **4.55:1 ✅** (`--inst-muted`
  already passed at 4.84:1). The launch view also ships the gap-#1 stale-state fix (text 13.03:1;
  the old treatment measured 4.34:1, confirming that cap was real too).

## Implementation update (2026-06-20) — folded into production
The proven spike bits now live in `integrations/status-panel/`, served by the **existing**
status-panel daemon (no new port, no new process — the unit runs `status_panel.py` in place):
- **Launch view** `launch.html` at `/atrium` and `/?view=launch`; `/` stays the diagnose panel
  (never mutated into a hub). An "observatory almanac" treatment (serif index, dotted leaders,
  still-room atmosphere) renders the same `/launch.json` contract.
- **Origin-aware doors + server loopback signal (gaps #2/#3/#4)** ported into `status_panel.py`
  (`classify_origin` / `door_for` / `build_launch`): on a remote origin a loopback door is rewritten
  to the tailnet host, an un-served door (ComfyUI :8188, `tailnet:false`) renders desktop-only, and
  the "Copy fix" shell one-liner is emitted **only** to a provably-local request. **Same fix applied
  to the existing `panel.html`** — its copy-fix + "bring stack up" affordances are now gated on the
  server's `can_copy_fix`, and its footer/open-links are origin-honest (it no longer client-reads
  `location.host`), because the status panel itself (:9123) is tailnet-served.
- **1.5s TTL status cache** (`cached_status()`, behind a lock) backs both `/status.json` and
  `/launch.json` so a wedged unit can't fan out into N stuck request threads.
- **PWA shell**: `manifest.webmanifest` (start_url `/atrium`), `sw.js` (caches the launch shell only,
  never live state, never intercepts `/`), `icons/` (regenerable via `make_icons.py` — no new art).
- **Desktop launch verb**: `gen_launchers.py` emits one KRunner `.desktop` per door; `apply.sh`
  installs them, `restore.sh` removes them (reversible). Daemon now **refuses a non-loopback bind**
  unless `AGENTOS_STATUS_ALLOW_NONLOOPBACK=1` (`tailscale serve` is the only sanctioned exposure).
- **Tests**: `tests/test_launch.py` ports the spike's origin/security invariants + adds route,
  cache, launcher-generation, and host-validation tests (67 status-panel tests total, green).

### Adversarial review pass (2026-06-20) — fixed inline
Five parallel reviewers (security / reversibility / resource-safety / a11y / channels). No
CRITICAL/HIGH security holes — the core invariant (a shell one-liner never reaches a remote
client) was confirmed fail-closed. Fixed inline: (a) **resource-safety [High]** — the status cache
now serves-stale-while-refreshing (the slow `build_status()` runs OUTSIDE the lock) so a wedged
`systemctl` can't park every request thread; (b) **a11y [High]** — `panel.html`'s stale-state now
dims the *signal*, not the *text* (the gap-#1 fix the launch view already had — closes the cap at
the old `panel.html:159`); (c) **a11y [Blocker]** — the "desktop only" / "fix on desktop" cue got
its own dashed-cool treatment + aria-label (was visually identical to the "system" badge); (d)
**channels [High]** — origin headers split into universal proxy-headers vs advisory identity-headers
(never trust an identity header's *presence/absence* — tagged devices & Funnel carry none); (e)
**security** — `/icons/..` now 404s cleanly (was an uncaught `IsADirectoryError`), host rewrite
validates DNS labels + port range, `why`/peer-IP stripped from client payloads; (f)
**reversibility** — `.desktop` launchers written atomically and pruned/removed **only** if they
carry our `X-AgentOS-Launch` marker (never delete a user's same-named file); (g) **channels [Low]**
— the panel poll now backs off with jitter on failure instead of a fixed-rate retry storm.

## Open / gating (remaining)
- **Live `tailscale serve` origin:** confirm PWA install + service-worker scope under the actual
  HTTPS cert/reverse-proxy on the box (the static checks pass; the live origin is unverified).
- **`serve`, never `funnel` (channels [High], documented constraint):** the trust model assumes
  "remote == authenticated tailnet device." `agentosd-remote.sh` uses `tailscale serve --https`
  (tailnet-only); it must never be swapped for `funnel`, which would expose the service map + doors
  publicly while the panel still reads "over your tailnet." (Header-based Funnel auto-detection is
  unreliable — tagged tailnet devices also carry no identity headers — so this stays a deployment
  constraint + the loopback bind guard, not a runtime degrade.)
- **Runtime served-port coupling (channels [Medium], future hardening):** `door_for` trusts the
  catalog's `tailnet:false` flags to mark un-served ports desktop-only; the `CatalogDriftGuard` test
  keeps `services.json` ⊕ `agentosd-remote.sh` honest, but only at CI time. A shared served-ports
  artifact both the serve script and `status_panel.py` consume at runtime would make "never a dead
  phone door" a runtime invariant, not a dev-box test. Acceptable for v1 given the test.
- **`launch.html` a11y (advisory, owned by the design thread):** the new "observatory almanac"
  view is contract-compatible and largely strong (reduced-motion reaches the cascade, decorative
  spans hidden, monitor-only renders as a non-focusable div). Open advisory items for that thread:
  the gated S1 cost note in the accessible name reads as a kept promise; `--inst-label` should be
  spot-measured over the lighter radial-wash corners (not just flat glass); focus restoration when
  a door flips `<a>`→`<div>` (open↔desktop-only) lands on `<body>`.
- **S1 (lease-aware door):** the affordance is designed + laid out, gated on `agentosd lease`
  (`org.agentos.Coordinator1`) being installed (built-not-installed) to supply the real verdict.
- The reserved `acting` state is never emitted; any "launch during actuation" affordance is
  blocked on the computer-use/kwin-mcp path.

## Consequences
- The launcher stays a thin, reversible layer; the control-center gravity is resisted by a citable
  decision, not a vibe.
- This surface adds a *go-there* verb and **zero** undo/recover-the-service capability. The ≤2-step
  control promise stays with ADR-0005; launching mutates nothing (link-out only).
- Recovery is bounded to copy-paste (and suppressed where it can't be run) until ADR-0005 lands.
- Spike-first: build and measure in `spikes/atrium/` before any `integrations/` change.
- **Moat track (v2):** an `org.agentos.Coordinator1`-aware door — surfacing the admission verdict at
  hover/focus ("opening now yields ~1.5 GB from your model") — is the one move that lifts this off the
  homelab-launcher market floor. Gated on the lease daemon being installed (built-not-installed
  today). Design the affordance now; build it after install.

## Rating verdict (Round 1)
ITERATE · **5.0/10** (uncapped design 8.02). Strong, on-vision, all non-negotiables clean — pinned by
two caps: a market-fit hard ceiling (me-too homelab launcher as scoped; only the lease-aware door
clears it) and an unmeasured WCAG contrast on the degraded/stale state (`panel.html:159`), which is
the phone's default landing. Close both via `docs/design/0031-launch-surface-FINAL-council-brief.md`
§3 gap plan; re-rate after `spikes/atrium/` lands the numbers.

## Recorded dissent
- **visual-systems-designer / brand-identity-designer** argued for a single "hub mode" of the
  status panel (a `body[data-mode]` switch on the same page) over a separate route — held as a
  legitimate alternative; the device split was chosen because the desktop launcher (KRunner)
  already exists natively and a web launch view earns its keep only on the phone.
- **art-director** proposed a desktop KRunner runner as primary (a transient summon surface)
  rather than KRunner reachability as a fast-follow; the difference is sequencing, not direction.

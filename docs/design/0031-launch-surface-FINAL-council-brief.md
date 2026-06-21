# FINAL council brief — The launch surface ("the Atrium") — ADR-0031

- Status: **Decided direction · ITERATE to 9.0** (council synthesis, mediator). Proposes; code + human dispose.
- Date: 2026-06-20
- Mediator: design-discourse-mediator
- Mode: **PROPOSAL verdict, not a code verdict.** The substrate it reuses is shipped
  (`status_panel.py` + `services.json` + `instrument-tokens.md`, all verified on disk). The launch
  surface itself — the PWA manifest/SW, the origin-aware door logic, the `.desktop` generation,
  `spikes/atrium/` — is **unbuilt**. A strong aggregate must NOT be read as "the launcher exists."
- Relates to: ADR-0026 (boot stack + status panel — extends its renderer with a launch verb,
  inherits its five rules verbatim, does NOT mutate `/` into a hub), ADR-0012 (keyhole — keeps the
  live glance, not duplicated), ADR-0001 (Plasma/KRunner IS the launcher — don't reinvent),
  ADR-0005 (apply/rollback tx floor still undelivered — recovery stays copy-don't-execute),
  `integrations/design/surface-labor.md` (the division-of-labor contract this adds a fourth verb to).
- Artifacts: ADR stub `docs/adr/0031-launch-surface.md` · design brief
  `docs/design/0031-launch-surface-the-atrium-brief.md` · this consolidated final.

---

## 1. The decided direction (unchanged — endorsed)

**Launch is a legitimate fourth verb in the surface-labor contract, delivered as a thin layer over
what already exists — NOT a new app, NOT a grown status-panel `/` root, NOT a dashboard.** The
split is by device:

1. **Desktop = Plasma/KRunner.** `.desktop` entries first (KRunner indexes them for free; zero new
   build surface; no krunner runner precedent in-repo); a `org.kde.krunner1` runner only if usage
   earns it. The status panel keeps owning diagnose/recover.
2. **Phone = a read-only PWA launch view** on the existing `status_panel.py`
   (`manifest.webmanifest` + minimal SW), installable over the existing `tailscale serve` HTTPS
   origin — no new port, no new daemon.
3. **One contract, one register, one process.** Both renderers read `/status.json` + `services.json`
   and wear `instrument-tokens.md`. Launch/monitor is derived from existing fields — zero schema
   change. Launch = link-out only; recovery = copy-don't-execute (suppressed on a remote origin —
   no shell on the phone).
4. **Spike-first** in `spikes/atrium/` and measure before any `integrations/` change (ADR-0005 floor).

All four non-negotiables that bound this surface are **VERIFIED clean**: reversibility (link-out +
copy-don't-execute, zero mutation), don't-reinvent (KRunner is the desktop launcher), copy-don't-
execute (no start/stop buttons), calm/honest mapping (attention-only silence, non-reactive
backdrop, no second warm source). This is why the verdict is ITERATE, not RECONSIDER — the
direction is correct; it needs proof and a moat, not a rethink.

---

## 2. Rating verdict — **ITERATE · 5.0/10** (uncapped design 8.02; two live caps)

Weighted (proposal mode: vision 0.32 · experience 0.26 · craft 0.18 · feasibility 0.14 · market 0.10):

| Dim | Score | Note |
|---|---|---|
| Vision-fit | 9 | Exactly on-vision: calm, reuse-disciplined, refuses the dashboard. |
| Experience | 8 | Carries a **≤5 a11y cap-risk rider** the other 8s do not (see below). |
| Craft | 8 | Two hardest claims (PWA-SW-over-serve; remote-origin suppression) asserted-but-unbuilt. |
| Feasibility | 8 | The "launch = derived from `url`" rule does not survive the serve/no-serve split. |
| Market-fit | 5 | **Hard category ceiling** — me-too homelab launcher as scoped. |
| **Uncapped weighted** | **8.02** | A genuinely strong *design*. |

**Final 5.0**, because two caps are evaluated before reporting and neither is averaged away:
- **Market-fit hard ceiling 5** — Homepage/Homarr/Dashy ship cards+health+PWA+reverse-proxy today;
  the artifact as scoped is cloneable in a weekend. Only **gap #2 (lease-aware door)** lifts it.
- **Experience a11y cap RISK (≤5), not yet fired** — `body.stale main { opacity:.5; filter:saturate(.6) }`
  (`panel.html:159`) is the phone's **default landing** when the tailnet path is down, and its WCAG
  contrast in the *dimmed* state is **unmeasured**. One verification away from firing.

The 8.02-design / 5.0-reported gap is honest and legible: a strong on-vision proposal pinned by a
category ceiling and one unverified a11y gate. Not a non-negotiable violation.

### The one real disagreement (Market 5 vs Vision 9, Δ4)
The hidden axis is **"scored against the AgentOS vision" vs "scored against the homelab-dashboard
market."** Both are correct in their lane: it is *exactly on-vision* AND *exactly a me-too*. The
standing AgentOS tension — **on-vision restraint is not automatically a market moat.** The closing
move is gap #2: the lease-aware door is the one move that makes the door inherit the substrate moat
the vision raters already credit. Until taken, the 9 and the 5 both stand; the cap (5) is reported,
not the blend. No escalation needed — this is a known, structural tension with a clear closing move.

---

## 3. The 10/10 gap plan (prioritized, deduplicated, owned by makers)

**Quick-close track (pure-spike / render logic — no substrate block; do these to clear 9):**

1. **[CAP — do first] Measure the degraded/stale-state contrast.** Capture `main` text + status
   pills at `opacity:.5; saturate(.6)` over the instrument glass; confirm WCAG AA (4.5:1 text,
   3:1 marks) *in the dimmed state*. If it fails: dim background-only or raise to ~.62 — **never**
   the foreground. → **`ui-accessibility-reviewer`** (verify) + **`visual-systems-designer`** (token).
   Lifts: **Experience** (removes the ≤5 cap risk). This is the phone's most-common landing.

2. **Origin-aware "is this a live door."** Gate "is a destination" on `url` present **AND**
   served-on-this-origin: on a remote origin rewrite loopback→tailnet host, suppress un-served
   ports (ComfyUI :8188, Hermes-REST :8642) as monitor-only / "desktop only" — **never a dead
   door.** → **`interaction-designer`** + **`design-technologist`**. Lifts: Feasibility, Experience.

3. **Three-register catalog reconciliation + drift guard.** The rating panel found **three** drift
   points, not the one the prior brief named (all verified):
   - Share hub (:8770) is tailnet-served (`agentosd-remote.sh:16,22`) but **absent from
     `services.json`** — add `share-hub` with `url`/`health`.
   - ComfyUI :8188 has a `url` (`services.json:6`) but is **explicitly not tailnet-served**
     (`agentosd-remote.sh:6,50`) → a dead phone door — mark not-tailnet-served.
   - The status panel's own :9123 is tailnet-served but has **no `services.json` entry** with a
     `url`/`health` — add it.
   Reconcile `services.json` ⊕ `components.conf` ⊕ `agentosd-remote.sh`; add a one-line test: every
   `url`-bearing service on a remote origin maps to a served port. → **`design-technologist`**.
   Lifts: Feasibility, Craft, Vision-fit, Experience.

4. **Server-side loopback suppression of "Copy fix."** Replace the client `location.host` read
   (`panel.html:489` — a reverse proxy rewrites it) with a **server-emitted loopback signal**;
   prove a non-loopback request never receives a shell one-liner. → **`design-technologist`** +
   verify **`reversibility-tx-reviewer`**. Lifts: Craft, Experience (zero-mutation holds on phone).

5. **Land `spikes/atrium/` with the numbers BEFORE any `integrations/` touch; hold ADR-0031 at
   `Proposed — gated`.** Prove: (a) a real `manifest.webmanifest` + minimal SW that installs over
   the actual `tailscale serve` origin (verify SW scope under the reverse proxy — first PWA install
   in-repo); (b) first-paint, idle-RAM, backdrop frame-time vs the wallpaper VRAM/frame budget
   (Open Q1, ties to ADR-0026's unresolved idle-exit). → **`design-technologist`**, consult
   **`resource-safety-reviewer`** (standing cost) + **`wayland-computeruse-reviewer`**
   (`.desktop`→KRunner→`xdg-open` Plasma 6/Wayland). Lifts: Craft, Feasibility, Vision-fit (fixes
   the artifact-order inversion: the ADR + brief are written but `spikes/atrium/` does not exist).

6. **Prove WebGL reduced-motion + name the control gap in the ADR.** Verify `#auroraBg` halts under
   `prefers-reduced-motion` (the block at `panel.html:123` currently only stops `.horizon`); state
   plainly in ADR-0031 Consequences that this surface adds a *go-there* verb and **zero**
   undo/recover capability (the ≤2-step control promise stays with ADR-0005). →
   **`ui-accessibility-reviewer`** + **`content-voice-designer`**. Lifts: Experience, Vision-fit.

**Moat track (v2 — depends on the lease daemon being installed; built-not-installed today):**

7. **[CAP — moat] Make the door lease-aware.** Surface the `org.agentos.Coordinator1` admission
   verdict at hover/focus, *before* the user commits: a quiet single-line cool-toned readout —
   "Dreaming is dormant, not down. Opening now yields ~1.5 GB from your model." → **`interaction-designer`**
   (the click/focus-time affordance, keyboard-reachable) + **`design-technologist`** (lease read).
   Lifts: **Market-fit** — the *only* lever that clears the hard 5 ceiling. The lease daemon
   (`agentosd lease`) exists but is **built-not-installed**; the verdict read is feasible against the
   live `org.agentos.Coordinator1` once installed. **Design the affordance now so the spike measures
   the right thing; build it after the daemon installs.**

*Sequencing:* gaps 1, 4, 5 are pure-spike and unblock immediately. Gaps 2, 3, 6 are catalog +
render logic, no substrate block. Gap 7 is the moat track, gated on daemon install.

---

## 4. Market positioning — refuse the shelf, name a narrow one

**Category decision: do NOT join "self-hosted dashboard" or "agentic-OS command center." Name the
category "the resource-aware front door."**

**Position statement.** For the single-operator running local AI on one prosumer GPU who reaches
their box from their phone, the AgentOS launch pad is **the front door wired to the GPU coordinator
underneath it** — so it tells you the truth your GPU is hiding (is this service down because it
*failed*, or because the VRAM coordinator *evicted* it — and will launching it now OOM your model?),
unlike Homepage or Uptime Kuma which answer only "is the port open," because it reads the same
lease/VRAM/workload contract the broker writes (`keyhole.rs` schema 3) — a contract no port-pinging
dashboard can fabricate without first building the coordinator it has no reason to build.

**Beachhead:** the single-operator local-AI tinkerer on one 24GB-class GPU doing triple duty —
ambient ray-traced desktop + always-on local LLM + on-demand dreaming (ComfyUI) — reaching it from
their phone over Tailscale. The person who *feels the collision* the substrate exists to coordinate.
**Not** the homelab-rack admin (Homepage), **not** the Mac power-user (Raycast), **not** the
fleet-ops team (Uptime Kuma).

**Three pillars (each sourced + maturity-tagged):**
1. **Resource-truth at the front door [PROVEN].** Only Linux front door wired to the resource broker
   (`keyhole.rs:27,75-100` emits `workload` = dominant GPU process by VRAM; `services.json:6` marks
   ComfyUI `on_demand`; `lease.rs` actually SIGKILLs + reclaims). **Structurally uncopyable —
   requires *being* the broker.** This is the headline.
2. **The all-clear is silence [PROVEN].** Calm by contract, not config flag (ADR-0026 §3 rule 3 —
   opens only on `attention>0`; one toast per *new* post-boot failure edge). A posture competitors
   are unwilling to enter.
3. **One honest contract, everywhere — honest when blind [PROVEN, one verify-gate].** `/status.json`
   + `keyhole.json` single sources, rendered to tray/web/phone; `agentosd-remote.sh:16` fronts
   tailnet-only HTTPS with ComfyUI/Hermes-REST deliberately not exposed (`:6,:50`) — a real consent
   line; stale→grey degraded, never confidently-green-while-blind. **Verify-gate:** PWA-over-tailnet
   installability is unconfirmed against the actual cert setup — do NOT promise "installable on your
   phone" until verified (= gap #5).

**Reframe, do not claim.** Reversible recovery is **[DESIGNED], not [PROVEN]** — ADR-0005 is
Accepted but has no runtime tx engine. Today the panel *copies* a fix; the human disposes. Position
as "propose-don't-execute today, reversible-when-the-tx-lands." Do not claim shipped reversible
recovery — it is the *trust* claim, and over-reaching it breaks the whole position first.

**Vocabulary lock (→ `content-voice-designer` verbatim):** *front door / instrument / launch pad.*
NEVER *command center / mission control / dashboard.*

**Differentiation: strong (8.5/10), one structurally-uncopyable axis (E1), PROVEN in shipped code.**
Deltas to a 10/10 position, all "ship the DESIGNED," not "rewrite the message": (1) land ADR-0005 tx
engine → graduates recovery to PROVEN (~+1.0); (2) build the eviction demo — SIGKILL a leased
ComfyUI mid-generation, panel says "evicted, not failed" (~+0.5); (3) verify + ship PWA-over-tailnet
(~+0.3); (4) document the substrate threat model, consult `responsible-ai-privacy-skeptic` (~+0.2).

---

## 5. Signature delight moves (the moat, made felt)

**S1 — The door that knows the GPU. "Open" becomes a small act of honesty, not a hyperlink.**
The one ownable beat and the moat made felt. On an `on_demand` door (ComfyUI), the launch affordance
reads the live `org.agentos.Coordinator1` admission verdict at hover/focus and tells the cost
*before* the user commits — a quiet single-line cool-toned readout, no warning, no confirm dialog,
no color change, just the door briefly *becoming legible about cost.* The whole product compressed
into one hover: "my launcher knows what opening it will cost." **The only move that lifts market-fit
off the hard 5.** Build on the moat track (after the lease daemon installs); design now so the spike
measures the right thing. *Owner:* `interaction-designer` + `design-technologist`.

**S2 — The recovery breath, promoted to the peak-end of the whole surface.** The per-row warm→green
exhale (`panel.html:185-192`) already exists. Make it the surface's *ending*: when the last
attention row crosses healthy and `summary.attention` returns to 0, the summary pill's bead carries
one final slower settle (re-use the `recover` easing, never the reserved warm) as the panel earns
its silence and recedes. Arc: alarm → handled → *the room goes quiet on its own.* CSS-only, no VRAM,
honors the existing `prefers-reduced-motion` guard. *Owner:* `interaction-designer` +
`motion-designer`.

**Earned microdelights (calm, worth adding):**
- **Dormant-door "resting" glyph, not a dead one** — an `on_demand` door at rest reads "ready when
  you are," not "Open ↗"; the on-demand contract felt as hospitality. *Owner:* `content-voice-designer`.
- **Stale-state copy that names which sense went blind** — the phone's most-common landing should
  say *which* layer is blind ("the tailnet path is quiet — the box may be fine"), turning a
  "broken product" into an "honest instrument." Coordinate wording with `content-voice-designer`,
  contrast with `ui-accessibility-reviewer` (= gap #1, the live a11y cap).
- **First-paint that never lies green** — already shipped (`panel.html:280` starts degraded /
  "checking…"); keep it as a stated ADR delight principle.

**Restraint cuts (re-asserted):** no reactive backdrop on the launch view; no launch-pad sound
(defer to the one swaync earcon); no favorites/pinned doors in v1; **no second warm source** — S1
and S2 both use cool/green, never `#ff9957`, so the wallpaper's reserved needs-you dawn-breath stays
the only warm beat in the system. **No flourish that implies the launcher healed a service** —
recovery is [DESIGNED], not [PROVEN]; the delight is the *absence* of a fake heal.

---

## 6. Accepted tradeoffs
- ~10s-stale health on phone (slow poll) — acceptable for a glance surface; `generated_at` stale-gate
  keeps it honest.
- Two renderers (KRunner desktop + PWA phone) could drift — mitigated by one `/status.json` + one
  `services.json` + one token register.
- No personalization in v1.
- Always-installable posture leans against ADR-0026's unresolved idle-exit question — **gated on a
  cost number** (gap #5).
- Narrow self-named category costs more explanation up front ("front door" needs a sentence;
  "dashboard" needs none) — wins every defensibility argument after. Same bet ADR-0001 makes.

---

## 7. Recorded dissent (never erased)
- **visual-systems-designer / brand-identity-designer** — preferred a single "hub mode" of the panel
  (`body[data-mode]` switch on `/`) over a separate route. Held as a legitimate alternative; device
  split chosen on *don't-reinvent* (KRunner already is the desktop launcher; a web launch view earns
  its keep only on the phone).
- **art-director** — preferred a KRunner *runner* (transient summon) as primary over `.desktop`-first.
  Sequencing difference, recorded.
- **Market-fit (5) vs Vision-fit (9)** — not erased into a blend. Both stand; the cap (5) is reported.
  Closing move is gap #7 (lease-aware door). This is the standing AgentOS vision-vs-market tension;
  flag for the human that "calm launcher" is *positioning*, not a moat — the moat is arbitrating the
  single-GPU collision at the door.

---

## 8. Prioritized next actions (to reach 9.0 this round)
1. **Measure the degraded-state contrast** (gap #1) — converts the live ≤5 a11y cap risk into a
   cleared floor. `ui-accessibility-reviewer` + `visual-systems-designer`.
2. **Spike `spikes/atrium/` with the PWA install + the three cost numbers** (gap #5) — removes the
   artifact-order inversion and the two unproven seams. `design-technologist`.
3. **Origin-aware door + three-register catalog** (gaps #2/#3) — kills the dead phone door and the
   drift. `interaction-designer` + `design-technologist`.

*Market ceiling needs gap #7 (lease-aware door, S1) to move beyond 5 — moat track, after the lease
daemon installs.*

---

## 9. Open questions for the human (options + recommendation)
1. **Standing-process cost gate.** ADR-0026 flags idle-exit/socket-activate vs all-session as
   undecided; a PWA leans always-on. Options: (a) measure first-paint/idle-RAM/backdrop-cost in
   `spikes/atrium/` then decide [**recommended** — cheap, ADR-0005 floor]; (b) ship always-on,
   revisit if the GPU budget complains. Owner: design-technologist / rater-feasibility.
2. **Catalog drift — fix now (three points, not one).** Add `share-hub` (:8770); add the status
   panel's own (:9123) `url`/`health`; mark ComfyUI :8188 not-tailnet-served. Low-risk, do it with
   the spike. **Recommended: do it.**
3. **Phone personalization (pinned doors).** Different JTBD from the login panel. Options: (a) ship
   v1 with no personalization [**recommended** — preserves ADR-0026 discipline]; (b) route to
   `personalization-loop-reviewer` as a fast-follow once the PWA exists.
4. **Missing voices to consult before build:** `ux-reviewer` (intent-routing soundness),
   `ui-accessibility-reviewer` (PWA keyboard order + dimmed-state door contrast — the live cap),
   `reversibility-tx-reviewer` (copy-don't-execute reads as zero-mutation; degraded-origin
   suppression via server signal not client host-read), `wayland-computeruse-reviewer`
   (`.desktop`→KRunner→`xdg-open` Plasma 6/Wayland), `ai-product-reviewer` (ratify launch as a
   fourth job), `responsible-ai-privacy-skeptic` (S1 surfaces lease/VRAM — confirm resource-truth,
   not surveillance; certify any privacy claim before it's printed).

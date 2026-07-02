# ADR-0050: The keyhole becomes a tabbed shell — Instrument + an Agent-Orchestrator "Check-ins" view

- Status: **Accepted** (2026-07-01) — the shell-wide pre-Accepted gate ran (ambient-embodiment +
  ui-accessibility + UX, all PASS-WITH-MUST-FIXES; every must-fix applied same day — see the gate
  record below). Built as Phase A of the "Agent Orchestrator — Check-ins" program (the read-only
  floor). Corey's binding steer: *"we'll need many ADRs"*, and *"at the very least"* a buildable
  read-only floor that ships now.
- Date: 2026-06-29
- Deciders: Corey (binding product steer: "we'll need many ADRs"; "at the very least" a buildable
  read-only floor), design synthesis from three Plan agents.
- **Amends ADR-0012 §1** (a single foveal-zoom panel + a `:9119` link-out for the kanban) **and
  ADR-0012 §5** ("v1 is strictly read-only + link-out + the taskbar embodiment") — widening the
  instrument from one view to two co-equal tabs, **read-only still**. **Reaffirms ADR-0012 §6**
  (writes stay deferred — see ADR-0053) **and §7** (calm/honest/accessible, now shell-wide). Follows
  the section-named amend precedent of **ADR-0041** / **ADR-0043** rather than a sixth in-place
  amendment on the already-5×-amended ADR-0012. Relates to ADR-0003 (fail-open).

## Context

ADR-0012 shipped the keyhole as a **single** view: a foveal-zoom panel surfacing the slice agentosd
owns (VRAM pressure, model residency, the led GPU lease/arbitration headline), and sent the Hermes
kanban to a **link-out** at `http://127.0.0.1:9119` (§1). §5 fixed v1 as "strictly read-only +
link-out + the taskbar embodiment." That was right for an arbitration *instrument*. It left no home
for a second, co-equal reading of the fleet: the per-task "Check-ins" view (the playful sidekick
creatures — ADR-0052) that answers *"what is each agent doing, and is it OK?"* rather than *"who
holds the GPU and why?"* The board link-out is the deep destination; it is not an at-a-glance,
in-popup register. A second view needs a place to live **inside** the instrument's own glass.

## Decision

1. **`FullRepresentation.qml` becomes a tabbed shell, not a single panel.** Tab 1 = today's
   arbitration **Instrument, verbatim / byte-for-byte** (the lease-led layout, the aurora VRAM
   horizon gauge, residency, workload, the queue row — unchanged); tab 2 = **Check-ins** (the new
   per-task sidekick view, ADR-0051/0052). The two are co-equal readings, not a master/detail.

2. **The shell keeps the calm contract global, not per-tab.** The signature **HorizonStrip**, the
   **state-glyph + honest UNKNOWN** (ADR-0012 §4), gateway honesty, and — critically — the
   **`CompactRepresentation` tray glyph** stay **shell-global** (shared chrome above/around the
   tabs, never duplicated per tab). The tray contract is untouched: the glyph still vanishes at true
   idle (§5, byte-identical-to-baseline), still surfaces only on a `needs_you` transition. Tabs live
   *inside* the popup; the tray sees one instrument.

3. **The tab switcher is a custom segmented control in the instrument's own minimalist language —
   NOT Kirigami `TabBar` and NOT a QQC2 `StackLayout`.** Two concrete reasons, not taste: a
   `StackLayout` sizes to its **tallest** child, so the shorter tab would render with the taller
   tab's height and the popup would clip (the same `ListView.contentHeight→0` popup-clip class the
   SYSTEM-row amendment already engineered `boardPx` to avoid, and which WCAG 2.3.3 flags). And
   Kirigami chrome carries its own Breeze-flavored padding/imagery that **fights the deep-navy glass
   register** the keyhole is built in (its own `InstrumentPalette` tokens, not the system palette).
   The switcher is two flat segments in the existing token vocabulary.

4. **Per-tab `implicitHeight`; snap the popup height on switch — no tween.** Each tab reports its own
   `implicitHeight`; the shell binds the active tab's height so the hosted Dialog **re-measures** to
   the visible tab. The switch is an **instant snap** (a `visible` toggle on each tab's content),
   never a height animation — a height tween toward a collapsing child re-courts the popup-clip bug
   and the WCAG 2.3.3 sliding-panel flag the SYSTEM board already learned to avoid. Off-tab content
   is `visible: false` (and so not laid out / not animated — see ADR-0052's shared-tick gating).

5. **§7's non-negotiables bind shell-wide.** The tab chrome is calm (no new color; the segmented
   control uses the existing dim/active tokens), **keyboard-operable** (Left/Right or Tab to move
   between segments, Return/Space to activate, a shape focus ring), and **reduced-motion-aware** (the
   snap is already instant; no decorative motion is added by the shell). State remains carried by
   shape+text, never color-only. The second visual register the Check-ins tab introduces is bounded
   and lives entirely *inside* tab 2 — see ADR-0052; the shell itself adds no new register.

## Consequences

- ADR-0012's "the instrument owns only agentosd's own slice + a board link-out" **widens to** "+ a
  read-only Hermes-task tab." The `:9119` link-out **stays** as the deep board (Check-ins is the
  in-popup glance, not a board rebuild — the ADR-0001 anti-pattern is still respected: we render
  Hermes' *existing* task rows, we don't re-derive the kanban).
- Net-new QML is the tab container + segmented switcher. **Tab-1 content is unchanged** — only its
  top anchor (now under the shared chrome + switcher) and a `visible` binding change; the verbatim
  guarantee keeps the arbitration instrument exactly as ADR-0012 left it.
- Both UI trees carry the change: `integrations/keyhole/package/contents/ui/` (canonical, the
  installed `org.agentos.keyhole` package) and `spikes/keyhole/contents/ui/` (the spike mirror).
- The popup grows taller on the Check-ins tab; the tray footprint and idle-vanish are unaffected
  (CompactRepresentation is untouched). No producer or Rust change is required by *this* ADR (the
  data contract is ADR-0051).

## Status / next

Proposed; Phase A is the read-only tabbed shell + Tab-1 verbatim. Companions deferred to their own
ADRs: **ADR-0051** (keyhole.json schema 5 — the Check-ins data contract), **ADR-0052** (the
sidekick-creature embodiment grammar, confined to tab 2), **ADR-0053** (the read-only-first charter
+ the deferred write-action seam). Further out: **ADR-0054** (the agentosd→Hermes write client,
Phase B — discharges the ADR-0012 item-3 gate so the action controls this shell renders disabled can
light up), **ADR-0055** (tok/s graduation + the cost/$budget tile when the ADR-0002 proxy lands,
Phase C). Gate before `Accepted`: the design-council / ambient-embodiment / ui-accessibility pass the
keyhole's prior visible surfaces went through, run shell-wide (tab keyboard model + the snap height).

## Amendment (2026-07-01) — Check-ins becomes the primary face; the divergence record

A 5-lens fidelity review against the design source (`Agent Orchestrator - Check-ins.dc.html`)
confirmed the translation and surfaced that several deliberate adaptations were undocumented. This
amendment is their record; each keeps §7's calm/honest contract and the popup form factor
(≈360–520px) that the 1280×820 design canvas does not share.

1. **Check-ins is the PRIMARY face (tab 0); the arbitration Instrument is the secondary tab.** The
   popup opens on the per-task view (`segments: ["Check-ins", "Instrument"]`); §Decision 1's "two
   co-equal readings" stands — only the opening order changed. The creature-tick `active` gate binds
   to the Check-ins view's own `visible` (never a literal tab index) so a future reorder cannot
   silently freeze the visible tab again.
2. **The metrics rail is fronted by the live AuroraRing porthole** (the ADR-0012 §7 shader, shared
   mood-driven with the wallpaper) instead of the flat `RingGauge` % arc; the honest GPU-LOAD % is
   plain text beside it and goes **amber above 90%** (the design's hot cue, kept as a text tint —
   shape+number stay the signal). A present-only **use-case line** (dominant WORKLOAD, else the held
   lease) folds the Instrument's "what is the GPU spent on" answer into the rail. `RingGauge.qml` is
   retired (unreferenced; delete or repurpose when ADR-0055 lands its tiles).
3. **No rail collapse-on-scroll.** The design collapses a 96px rail to a 16px mini-strip; the popup
   rail is a permanent 66px — three of the four mini-strip datums (tok/s, API, $budget) have no
   honest source until ADR-0055, and 66px in a popup is not worth a scroll-linked animation
   (WCAG 2.3.3-adjacent).
4. **List is the default view; the toggle reads `["List", "Board"]`** (design defaults to Board).
   List is the only view showing RECURRING, so it is the no-data-hidden default. The choice is
   session-only (matching the prototype); a plasmoid config key can persist it later if wanted.
5. **The Board renders the design's ≤560px stacked reflow permanently**, columns reordered
   **attention-first** (NEEDS YOU · RUNNING · SCHEDULED · DONE — the same priority the design's
   ≤900px mode expresses by dropping sched+done). Empty columns are hidden rather than dashed
   "nothing here" placeholders (four dashed boxes are noise at popup width); recurring stay
   List-only and the Board empty-state points to them.
6. **Section label "TASKS", not "LONG-RUNNING"** — the kanban does not guarantee run-to-completion
   semantics; the header's note slot carries the ADR-0051 §4 truncation honesty ("showing N of M")
   instead of the design's editorial copy.
7. **"Open in board" is live** (gateway-gated `Qt.openUrlExternally` → `:9119`, the Instrument
   footer's exact idiom) on task and recurring cards: it is a read-only link-out — ADR-0053 defers
   *writes* only. Pause / run-now / Auto-pilot remain the disabled-with-reason seam.
8. **"N LIVE" counts `working` only** (the design's runN): a stalled task reads amber on its card
   and in the RUNNING column, but never inflates liveness.
9. **Accepted creature-register adaptations** (ADR-0052's token/calm ethos over pixel parity):
   fixed creature sizes for the popup (no full/compact split), the working spinner riding the mouth
   slot, softened sway/bob amplitudes, outline instead of glow on letterforms, and the shared sine
   bob on the pixel sprite. The ~10-creature animation cap is a **whole-tab budget** (recurring and
   board columns spend from one allowance), per ADR-0052 §4's aggregate reading.

## Gate record (2026-07-01) — shell-wide reviewer gate run; must-fixes applied; → Accepted

The §Status/next gate ran as three parallel lenses over the deployed shell: **ambient-embodiment**,
**ui-accessibility**, and **UX** (the design-council seat). All three returned
**PASS-WITH-MUST-FIXES**; every must-fix landed the same day:

- **Keyboard operability of live actions** (a11y BLOCKER): `ActionButton` gained
  `activeFocusOnTab`/Return/Space/`Accessible.onPressAction` + a shape focus ring when live; the
  Instrument footer link-outs got the same treatment (pre-existing gap, fixed shell-wide); focus
  landing inside the scrolling body now scrolls into view.
- **Delegate churn** (a11y HIGH): `applyContract` dedups `check_ins`/`recurring` by content before
  reassigning, so the 2s poll (and the schema-6 heartbeat rewrite) no longer rebuilds every card —
  keyboard focus, AT browse position, and a mid-poke squash all survive a tick. Relative-time
  strings now read the reactive `nowSec` clock so they keep refreshing without rebuilds.
- **Focus visibility** (a11y HIGH): the selected filter chip's ring is ink-on-blue (was invisible
  blue-on-blue); the body Flickable wears a shape ring when focused.
- **Sticky-filter blank body** (UX HIGH): the filter auto-resets to "all" when tasks drain to
  zero, and the chips stay visible while a non-"all" filter is active — the control that emptied
  the body can never be invisible.
- **Stranded failure states** (UX HIGH): the UNKNOWN / unavailable empty states now carry a live
  "Full status ↗" link-out (`:9123`, the footer idiom) — the recovery surface is on the face that
  shows the failure.
- **Live vs seam legibility** (UX+a11y MEDIUM): a live action paints link-blue (the shell's
  clickable idiom), never opacity-only; a click on a *disabled* seam control flashes the "why"
  tooltip instead of being swallowed.
- **Attention-first List** (ambient MEDIUM): `filteredTasks` stable-partitions `needsyou` cards
  first, matching the Board's column order — the warm tray click lands ON the thing that asked.
- **Count coherence** (UX MEDIUM): the subline headlines the pre-cap total when truncated; the
  rail cell is renamed **LIVE** (one liveness word shell-wide); the truncation note shows only
  under the "All" chip; RecurringCard's stalled text routes through `creatureStalled`; the rail
  fold-in now carries **preempt + queue** (the agency-stakes arbitration datums) alongside the
  workload.
- **Docs honesty** (ambient MEDIUM): §Decision 2's "glyph still vanishes at true idle" described a
  tray-hosting posture the shipped panel-widget placement never had — corrected here and in the
  ADR-0012 porthole amendment: idle stays **visible-but-dim** (calm ○, 0.20 resting glow);
  `needs_you` elevates to `NeedsAttentionStatus`. ADR-0052 §2's "exclusively inside tab 2" reads as
  *confined to the Check-ins view, whichever tab position it occupies* (the tick gate binds the
  view's own `visible`, never an index); the popup-open click is the navigation consent.

**Considered and declined:** renaming the "Instrument" segment (proposed "GPU"/"System" — both
narrower than what the tab shows; the name stays, revisit on real confusion). **Deferred:** a
visible "AUTO" caption on the Auto-pilot glyph (the control earns a label when ADR-0054 makes it
live); porthole-glyph scrim deepening under light-crest wallpaper palettes (verify visually first).
**Recorded residuals:** `currentTab` persists per plasmoid session, so a `needs_you` tray surfacing
can open onto the Instrument tab (the StateToken still says "Needs your OK"); strings are not
i18n-wrapped; RTL untested.

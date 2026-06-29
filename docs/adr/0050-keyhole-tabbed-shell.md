# ADR-0050: The keyhole becomes a tabbed shell — Instrument + an Agent-Orchestrator "Check-ins" view

- Status: **Proposed** — built this session (Phase A of the "Agent Orchestrator — Check-ins"
  program; the read-only floor). Corey's binding steer: *"we'll need many ADRs"*, and *"at the very
  least"* a buildable read-only floor that ships now.
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

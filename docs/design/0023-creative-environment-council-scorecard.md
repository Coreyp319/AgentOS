# Design 0023 — creative-environment pipeline · design-council scorecard

Full design-council pass (2026-06-18) over ADR-0023 + design-0023 (pipeline) + design-0023 (window-drag
producer) + three verified spikes (`spikes/creative-env/`, `spikes/windable-grass/` — since removed, ADR-0046, `spikes/window-drag-wind/`).
Run as a fan-out of 11 read-only rater/reviewer agents (the Workflow tool is unreliable in this env), then
synthesized here. Target: 9/10.

## Aggregate verdict

**~7.5/10 weighted — SHIP the Phase-0 render pipeline; ITERATE the live wind mode + painterly craft.**
Not at the 9 bar yet; the gap plan below is the path. Strong, unanimous praise for the *architecture*
(model-proposes/code-disposes is "gold-standard," idle-byte-identical proven, reversible-by-construction);
the misses are all about **what is wired vs. asserted** and **painterly finish**, not the design.

| Lens | Score | Lens | Score |
|---|---|---|---|
| privacy-skeptic | 8.5 | determinism-safety | 8 |
| vision-fit | 8 | resource-safety | 7 |
| feasibility | 8 | experience | 7 |
| ambient-embodiment | 8 | market-fit | 7 |
| craft | 8 | **art-director** | **4** |
| ai-product | 8 (SHIP/ITERATE) | | |

**Dispersion — the art-director's 4 is the one outlier, and half of it was a fixable defect:** its #1 reason
was that the painterly artifact (`out/move.mp4` + the graded frames) **was not on disk** — so the "oil
painting" claim was unreviewable. That has been **regenerated this pass** (168 graded frames + a verified
14 s h264 `move.mp4`). The *other* half — "painterly reads as a posterize filter, not brushwork" — is a
**real, kept craft finding** (see P2). Post-regeneration the art lens is ~6; the panel's true center is ~7.5.

## Decision

- **SHIP** the Phase-0 deterministic render pipeline (prompt → validated brief → `bpy` blockout →
  palette-clamp → dual-purpose spline + raycast validator → EEVEE move → reversible artifact). It is
  verified, on-vision, reuses Blender/ComfyUI/lucid/lease/`agent.json` correctly, reinvents nothing.
- **ITERATE** the live window-drag→wind mode behind **its own gate and a falsifiable kill-metric** — it is
  the differentiation wedge but also the feature-creep risk: it opens a two-repo, two-unbuilt-producer
  chain (KWin → D-Bus sink → shader uniform) that is *not* yet proven end-to-end. Do not let the
  "parallel track" framing smuggle it into the otherwise-clean Phase-0.
- **CUT** nothing.

## Remediated in this pass

- **Citation drift (flagged by 5 lenses):** the docs claimed a "`feed.rs:88-97` spring." `feed.rs` is
  edge-driven and stateless (`ramp` = count→intensity); the easing has always lived in the **consumer**
  (QML) / the **wind sink** (ω≈1.5). Corrected in ADR-0023, design-0023, and the producer spec.
- **Missing artifact:** re-ran the Phase-0 pipeline → `spikes/creative-env/out/move.mp4` (640×360, 168 f,
  14.0 s) + 168 graded frames restored and verified.

## Remediation — gap-plan follow-through (landed 2026-06-18)

Executed as three parallel implementation tracks + doc edits. ✓ = done; ◑ = needs a live session/measurement.

- **P0.2 neutral-vector** — consumer mapping fixed to `windDir = dir·gust` (rest→(0,0)); honored in the
  built sink and `live.qml`. ✓
- **P0.3 validator now GATES** — on an unrecoverable path it deterministically degrades to a safe camera
  and re-validates, else stamps a magenta `validator_failed` band; both flagged in `render.json`. ✓
- **P0.4 honesty** — live-loop framing downgraded to "render-verified; live producer/sink
  built-but-unverified" in ADR-0023 + the producer spec. ✓
- **P1.5 / P1.8 Wind1 sink** — built as `crates/agentosd/src/wind.rs`, served on the lease daemon's
  existing connection; spring + writer run as a separate task with a **structurally guaranteed**
  no-arbitration-lock property (a test that cannot be written to touch `Inner`); 98 crate tests green,
  clippy clean. ✓ build · ◑ live KWin→D-Bus round-trip (no interactive harness this pass).
- **P1.7 `eevee-render` profile** — added, distinct from the 8000-MiB Cycles ceiling (est. 3000 MiB +
  a `TODO: measured NVML delta`). ✓ profile · ◑ measurement.
- **P2.9 / P2.10 painterly structural + real `dreamTex`** — colour in flattened regions/clumps, scumbled
  2-tone sky, wind-aligned brush grain; the real EEVEE frame wired as `dreamTex` (reads painterly, not a
  filter). ✓ structure · ◑ art-director's final ship call on the procedural path.
- **P2.11 per-kind validator + pure-geometry tests** — `clip_verdict.py` (no `bpy`), per-kind thresholds,
  16 tests + a checked-in geometry-hash golden. ✓
- **P2.12 a11y** — `prefers-reduced-motion` uniform damp + a distinct stale/producer-dead look (cool,
  desaturated — never masquerades as calm idle); both idle-identity-safe (maxΔ=0). ✓
- **P3.13 brief schema versioning** — required SemVer `schema` field + validation (MAJOR-incompatible
  rejected); 15 tests. ✓
- **P3.14 / P3.15** — Wind1-into-lease-daemon ratified + the versioned signal allowlist + a per-mode
  success/kill metric, folded into ADR-0023's "Council review — amendments." ✓

**Still genuinely open** (larger or need a live session): the live KWin→D-Bus→sink→uniform round-trip on
Wayland; a measured EEVEE VRAM/frame-time number under the lease; the painterly final sign-off + a
non-grass multi-element brief proving the validator; the WCAG composited-`snag` contrast test; binding
more desktop signals (the market "your desktop is its weather" expansion).

## Consolidated 10/10 gap plan (de-duplicated across the panel)

**P0 — correctness/honesty (cheap):**
1. *(done)* feed.rs spring misattribution; *(done)* regenerate the artifact.
2. **Neutral-vector contract mismatch** (ambient #1 + determinism): the sink writes idle `dir=[0,-1]` but
   the shader treats neutral as `(0,0)` — a raw `[0,-1]` is a full upward bow, not rest. Define the
   consumer mapping as **`windDir = dir · gust`** (rest → `(0,0)`) and pin it against the idle capture, not
   a hand-set `(0,0)`.
3. **Make the validator's verdict ACT** (determinism #1): `render_move.py` renders unconditionally and only
   records `valid` — a gate that never declines is a report. On `valid==false` after `max_regen`,
   deterministically degrade (simpler blockout, ADR-0003) or flag the proposal.
4. Downgrade any "verified end-to-end" framing of the *live loop* → "render-verified; live producer/sink
   unverified."

**P1 — prove the headline (the biggest honest gap):**
5. **Wire the live loop end-to-end:** build the `org.agentos.Wind1` sink (fold into the `lease` daemon,
   **off the lease lock**), the QML consumer poller (Timer-poll, not XHR), live-load the KWin script; then
   capture a **moving A/B** (drag → eased wind → ease-back) to *demonstrate* the sub-threshold cadence
   instead of asserting it.
6. **Live-KWin unknowns:** confirm `interactiveMoveResizeStepped` fires per-step on Wayland (degrades to a
   single end-of-drag gust if not) and `callDBus` reaches a custom name without a policy `.conf`.
7. **Measure under the lease:** a leashed EEVEE VRAM/power number + a **dedicated `eevee-render` profile**
   (the current path would reuse the 8000-MiB *Cycles* ceiling → always-deny against ComfyUI's ~5.8 GB
   headroom); plus a steady-state GPU cost for the always-on live shader layer.
8. **Preemption safety:** assert + test that the 60 Hz wind tick holds no lease lock and cannot delay a
   preempt SIGKILL (p99 with tick on vs off).

**P2 — craft / art direction (to clear the painterly bar):**
9. **Make "painterly" structural, not a filter** (art-director, kept dissent): stroke-in-geometry
   (tip-weighted blade clumping into flattened color *patches*), wind-aligned brush-grain, a scumbled
   2-tone sky (the flat gradient is the most generic element), and use the green note `#9bb04a` for region
   contrast, not a tint. Kuwahara-over-PNG reads as Instagram, not oil.
10. Wire a **real EEVEE render into the shader's `dreamTex`** (today a flat-gradient stand-in — the
    dream-as-texture path currently "carries the look" but carries nothing).
11. **Per-kind validator thresholds** (`kind`→threshold table, unit-tested without `bpy`) so a non-grass
    brief (an avenue of columns) isn't silently mis-judged; extract the clip/occlusion verdict into a pure
    geometry fn + a checked-in geometry-hash gate.
12. **a11y:** a `prefers-reduced-motion` clamp at the uniform stage; WCAG-test the composited `snag` + peak
    `gust` against foreground desktop UI; a ≤2-step legible control for the live binding ("the desktop is
    steering the wind" + mute/revert) and a distinct "producer dead" state vs calm idle.

**P3 — decision hygiene (owed records):**
13. **Brief-schema versioning** as a Phase-0 *exit* gate (before UE becomes a 2nd consumer of the contract).
14. Ratify **`org.agentos.Wind1` folding into the `lease` daemon** as an ADR amendment (behavior changes on
    the safety daemon → an ADR must move).
15. The **bindable-signal allowlist** as a versioned contract; one observable **success/kill metric per mode**.

## Market positioning (triangulated)

The wedge is **the embodiment binding — "your desktop's own motion becomes the scene's weather"** — which
has **no comparator**: OpenArt Worlds, NVIDIA/Purdue Scenethesis (ICLR 2026), and Meshy all generate
navigable scenes but as cloud/standalone artifacts; none is wired to live OS signal. The moat is the
`feed.rs → agent.json → uniform` closed loop + the KWin producer + coherence-by-construction, **not the
frames**. **Position as embodiment, not text-to-3D** — Phase-0 ships procedural blockout, so competing on
"anything from a prompt" loses to cloud; competing on "the only environment that lives in your desktop"
is uncontested. Strengthen by binding **more** desktop signals (focus, workspace switch, fleet busy/snag)
and proving the validator on a non-trivial multi-element brief (the drowned-cathedral case), where the
"code disposes" claim either holds or collapses.

## Recorded dissent

`art-director` holds that the painterly *direction* is not yet delivered — the stylization is a
post-filter, not brushwork-in-geometry. Recorded as the **craft tripwire**: Phase-0 ships the calm,
legible, procedural amber look; the painterly artifact iterates (P2) before this is called done on the
visual axis.

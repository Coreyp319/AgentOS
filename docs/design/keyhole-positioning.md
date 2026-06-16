# Keyhole — Positioning Brief (Phase 4, market triangulation)

Synthesizer note: facts from `market-landscape-analyst`, edges from
`market-differentiation-strategist`, rating panel scored market-fit **7/10**. I triangulate;
expression goes to `content-voice-designer` / `brand-identity-designer`.

## 1. Position statement
For the **local-AI tinkerer on a single prosumer GPU** (4090-class, 24 GB) who can't see *who holds
the GPU and why* when their reactive desktop and their always-on agents collide, the **keyhole** is
the **arbitration window for local AI** — a calm, tray-reachable, read-only instrument that shows the
live **GPU lease**: which tier holds the card (interactive inference vs background batch) and the
preempt that resolved the fight — unlike system monitors (agent-blind) or LLM app windows
(arbitration-blind), because it reads a **real D-Bus VRAM coordinator that owns batch PIDs and
SIGKILL-evicts on preemption** (`org.agentos.Coordinator1`, ADR-0010, shipped + GPU-validated).

## 2. Category to own
**The arbitration window** ("see who holds your GPU and why"). Refuse the crowded "GPU monitor" and
"LLM dashboard" shelves — joining either re-frames the keyhole as a *gauge* and surrenders the moat.
Tradeoff: more explanation up front; uncontested defensibility after. The lease half is the category;
the gauges are table stakes that earn it the right to be on screen.

## 3. Target user & beachhead
The **single-GPU local-AI ricer/tinkerer** running an always-on RT wallpaper *and* serving 17–21 GB
models to Hermes on one 24 GB card — the exact collision in `README.md:15-20`. They feel the pain most
and can verify the moat. (Coordinate the lock with `market-landscape-analyst`.)

## 4. Moat vs copyable surface — the strategic move
- **MOAT (hard to copy, PROVEN):** the lease **tier + holder + preempt event**. No comparator has it,
  because none owns a coordinator that arbitrates the GPU — nvtop/nvidia-smi can *kill* a PID but
  can't say it was a *batch holder preempted by interactive inference*
  ([nvtop](https://github.com/Syllo/nvtop)); LM Studio's VRAM monitor sees only its own models in its
  own window ([sitepoint](https://www.sitepoint.com/lm-studio-vs-ollama/)).
- **COPYABLE (DESIGNED/skin):** the VRAM/residency/tok-s readout + kanban iframe — a skin over nvtop +
  `/api/ps`, cloneable in a sprint.
- **IMPLICATION (the v1 fix):** **foreground arbitration, demote the gauges.** The lease holder/tier
  must be the keyhole's headline datum and the taskbar embodiment's whole reason to "zoom" — not one
  row among commodity bars. Make the preempt the moment the surface narrates. v1 currently buries the
  only datum competitors can't fake; promote it.

## 5. Three pillars
1. **Arbitration-aware** (PROVEN) — surfaces `Acquire`/`Spawn`/preempt/`Release` from shipped code
   (`crates/agentosd/src/lease.rs`, 33 tests green, live `busctl`-verified, ADR-0010).
2. **Ambient & desktop-native** (PROVEN grammar) — the agent *is* the environment; the tray icon is
   the foveal zoom from the reactive-wallpaper mood (`crates/agentosd/src/feed.rs:54-60`), not an app
   window. Distinguishes it from every LLM GUI.
3. **Read-only & local** (PROVEN/DESIGNED) — calm instrument, never an actuator; all-local, MIT;
   kanban is a *link-out* to Hermes, never a re-implemented dashboard (ADR-0001, "don't reinvent").

## 6. Messaging hook
**"See who holds your GPU — and why."** (Anti-persona: not for cloud-agent dev teams who want
execution-graph debugging — that is LangSmith/Langfuse territory
([digitalapplied](https://www.digitalapplied.com/blog/agent-observability-platforms-langsmith-langfuse-arize-2026)).)

## 7. Biggest market risk + how design answers it
**The "so it's just a fancy nvtop" trap.** The instrument panel is copyable, so a reviewer anchors on
the gauges and the moat reads as a feature. **Answer (design mandate):** make the lease/preempt the
*primary visual* — the holder-tier badge is the keyhole's identity and the taskbar's zoom payload; the
preempt is the one event the surface animates. If a stranger's first read is "it shows who's holding
the GPU," the position holds; if it's "it shows VRAM," we've lost. Honesty gate: the Hermes plugin that
*tags* interactive inference is unbuilt (ADR-0006) — say "lease state" (PROVEN), not "watches every
agent" (VISION), or `ai-product-reviewer` docks for vaporware.

## 8. Market-fit feedback (for `rater-market-fit` / `rating-aggregator`)
Differentiation: **strong and code-backed** on the wedge, **weak** on the surface — current **7/10** is
fair while the moat sits demoted. **Delta to 10:** (a) foreground arbitration in the UI per §4; (b) ship
the Hermes plugin (ADR-0006) so "who holds it" spans *agents*, not just lease tiers; (c) make the
preempt the demo's headline verb. Markers: wedge=PROVEN, plugin=DESIGNED, cross-agent attribution=VISION.

## 9. Hand-offs
- `content-voice-designer` — own the verbatim line "See who holds your GPU — and why" and the
  preempt-moment microcopy.
- `brand-identity-designer` — the holder-tier badge as the keyhole's identity / taskbar zoom.
- `ai-product-reviewer` (advisory) — sanity-check the maturity markers before publish.
- Unresolved beachhead/category tension → `design-discourse-mediator`.

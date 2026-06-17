# Design-council brief — ADR-0020: Agent-facing GPU surface

- Status: **FINAL council brief** (the room's decided direction; code + human dispose)
- Date: 2026-06-16
- Facilitator: design-discourse-mediator (neutral; reconciles, does not author design)
- Subject ADR: `docs/adr/0020-agent-facing-gpu-mcp-and-admission-feedback.md` (Proposed)
- Verdict: **ITERATE** — perceive-v1 ships the design (~9); act + CONCUR held behind named gates
- Combines: decided design direction · rating verdict + 10/10 gap plan · market positioning ·
  delight & differentiation
- Mode: **PROPOSAL rating, not code rating.** `mcp.rs` (perceive) ships and is tested; the act
  surface is honestly absent and pinned-absent by test (`mcp.rs:215-225`). A SHIP here means
  "promote the design toward Accepted," never "the act code exists."

---

## 0. One-paragraph decision

Promote ADR-0020 toward Accepted **for the perceive phase only**, sharpened by seven prose
additions, and hold the act verbs and the CONCUR controller behind their named GO/NO-GO gates. The
architecture is unanimously sound and faithful to *don't-reinvent* (no GPU crate, no forked Python,
intent-wrappers not raw verbs). The one structural flaw is that the ADR's central safety claim — the
tier ceiling that stops an agent preempting the desktop — is phrased as an **MCP-shell property**
(ADR line 84) while the code that would enforce it accepts `interactive` from *any* caller
(`coord.rs:53-60`, no clamp). The fix is not taste: the clamp MUST be a core transform in
`coord`/`lease`, because a second D-Bus client bypasses an MCP-layer check. With that and an
identity-bound act token (reusing the shipped `holder_peer` pattern, `lease.rs:227-230`), the act
phase moves from a ~6 to a ~9. The signature move that ships *today* is the inverse of the headline
everyone reaches for: **an agent that can see the whole GPU and is provably forbidden from seizing
it** — the empty `act` slot, pinned by test, is the feature.

---

## 1. Participants (input reconciled, by exact name)

**Design team:** art-director, motion-designer, visual-systems-designer, interaction-designer,
design-technologist, generative-artist, sound-designer, brand-identity-designer,
content-voice-designer, design-researcher.

**Reviewers / deciders named by the ADR + routed to below:** determinism-safety-reviewer,
resource-safety-reviewer, responsible-ai-privacy-skeptic, ui-accessibility-reviewer,
ambient-embodiment-reviewer, rust-performance-reviewer, personalization-loop-reviewer,
wayland-computeruse-reviewer.

**Market + aggregation:** market-landscape-analyst, market-differentiation-strategist
(triangulated by market-positioning-synthesizer), rating-aggregator.

Scores entered: vision-fit 9.0 · experience 7.0 · craft 8.0 · feasibility 7.5 · market-fit 7.0.
Weighted overall **7.875/10**, no cap. Decomposed: **perceive-v1 ~9, act-as-written ~6.**

---

## 2. Agreements (the room converges)

- **Architecture is sound and faithful to don't-reinvent.** Perceive-before-act, tier-ceiling-to-
  Batch, intent-wrappers-not-raw-verbs, no new GPU crate, no forked Python. Unanimous.
- **The tier ceiling, *once in core*, is a real code-enforced guard.** `arbitrate` (coord.rs:129-135)
  preempts only on strictly-higher tier — a Batch-clamped agent literally cannot reach the
  `Interactive` verb that preempts the desktop. Model-proposes/code-disposes done right.
- **The CONCUR count-vs-order line holds** against ADR-0019's no-prioritization invariant (§G6,
  `_FORBIDDEN_ORDER_KEYS` / FIFO-by-`seq`). Count regulation is orthogonal to ordering. Verified.
- **`gpu_why` is the single highest-risk surface** — must be telemetry-sourced, never generated.
  Named independently by seven lanes. Already shipped sourced-not-generated (`mcp.rs:177-199`).
- **Phase-2 CONCUR's signal-gate is correct discipline** — no live KV-pressure signal, no controller.
  Unanimous; prove the signal in the telemetry log first, don't build the input path twice.
- **Reversibility is clean** — additive subcommand + config line; act's only effect (a grant) has
  its inverse (`Release`). Unanimous, no ADR-0005 break.

---

## 3. Tensions and how each resolves (owner lane · tie-break if used)

| # | Conflict | Owner lane | Resolution |
|---|---|---|---|
| T1 | Where the tier clamp lives — MCP shell vs. core | design-technologist + determinism-safety-reviewer | **Adopt clamp-in-core.** `Tier::from_arg` (coord.rs:53-60) accepts `interactive` from any caller; an MCP-shell check is bypassed by a second D-Bus client. A guard in front of the trusted boundary is not a guard. Tie-break: *model-proposes/code-disposes*. Pin with a unit test. |
| T2 | `gpu_why`: free string vs. typed closed-cause contract | content-voice-designer (strings) + determinism-safety-reviewer (sourced-not-generated) | **Adopt typed `{decision, reason_code, params, human}`**, only `human` is a string, typed-UNKNOWN when no correlated event. The closed enum is *how* you enforce "never plausibly-generated." Tie-break: *calm & honest legibility*. |
| T3 | Does an agent lease get an ambient tell? | ambient-embodiment-reviewer (the tell); motion/generative (value/cadence); content-voice (words) | **Reconcilable, not opposed.** The agent-initiated *preempt event* rides the **existing** ADR-0018 grammar (the `holder` field names the agent; the preempt line + one earned toast already fire). The routine `queued`/`denied` *state* stays calm/cool/silent, byte-identical to idle. An agent lease is just another Batch holder — never coin a new channel. Tie-break: *calm & honest mapping* (both honesty AND calm satisfied only by reusing ADR-0018). |
| T4 | Warm-hue invariant must be inherited explicitly | visual-systems-designer (token reservation) + ambient-embodiment-reviewer | **Adopt by reference.** Five lanes collapse to one claim: an agent waiting/denied is calm weather, NEVER warm — warm is reserved for `needs_you` (a *human's* move). ADR-0020 inherits ADR-0019 §3 as an explicit tripwire + acceptance test (`gpu_request → queued/denied` increments no warm; idle stays `iTime`-diff==0). Tie-break: *calm & honest mapping*. |
| T5 | `acting` (state 3): claim now vs. carve out | ambient-embodiment-reviewer + responsible-ai-privacy-skeptic; felt-state dir art-director + interaction-designer | **Reserve, do not wire — not in this ADR.** `state 3 'acting'` is defined-but-never-emitted; `derive_feed` has no producer. Wiring it as a side effect of a resource ADR is the "ships as working-but-faster" pitfall. Flag it honestly-deferred (like ADR-0019 G8). **Genuine tension — NOT flattened — escalated to human (Q1).** |
| T6 | Identity model: open question vs. GO/NO-GO blocker for act | resource-safety-reviewer + determinism-safety-reviewer | **Promote Open-Q2 to a GO condition on act.** Sequential tokens (lease.rs:193-194) are guessable → `gpu_release(token)` is a cross-agent DoS primitive unless bound to caller. Mechanism exists (`holder_peer`, lease.rs:227-230, ADR-0013 B4); the unresolved part is the Claude-Code↔Hermes↔MCP-session granularity. |
| T7 | `queued` is a lying word | content-voice-designer | **Adopt rename.** `lease.rs` has no wait-queue; a loser is told `queued` and must *retry* (AcquireResult). Elsewhere "queued" = "you hold a place." Rename to `busy_retry` (+ a `human` leaf: "not holding a place in line; ask again shortly"). Honest legibility at the word level; feeds T2. |
| T8 | `gpu_why` per-caller vs. system-level | determinism-safety-reviewer | **Scope v1 to system-level last-event.** No per-request correlation id exists, so honest `gpu_why` can say "the last preempt *on the system*" but not "*your* request." Gate per-caller phrasing on the correlation-id work. Several lanes recommend splitting `gpu_why` out of perceive-v1 entirely (see §4). |
| T9 | Don't-reinvent the MCP protocol either | design-technologist + resource-safety-reviewer | **Adopt `rmcp` by name** (official Rust SDK, MIT, tokio-native, stdio). The crate already carries tokio+zbus, so no new runtime cost; stdio satisfies "local-only, no network listener" for free. Pin in Cargo.lock. *(Note: the shipped `mcp.rs` is a hand-rolled minimal JSON-RPC server — `rmcp` is the recommended convergence target, not a claim it's already used.)* |

---

## 4. Decided design direction (concrete, phased)

**Smallest shippable version (perceive-v1):** `gpu_status` + `gpu_residency` only, as a Rust
`agentosd mcp` stdio server (converging on `rmcp`), reading `keyhole.json` + the `coexist` plan —
**no NVML in the MCP layer, no live `Status` on the read path** (read free-VRAM from `keyhole.json`'s
`vram` block). **Split `gpu_why` out of v1** — it is the one perceive verb that is a *synthesis*, not
a read, and carries the fabrication risk the keyhole honesty doctrine exists to kill.

> Reality check: the shipped `mcp.rs` already ships all three verbs including `gpu_why`. The council
> direction is to (a) keep `gpu_why` but **harden** it into the typed contract below before promoting,
> and (b) add the agent-facing UNKNOWN posture. The "split out" framing is the safety stance; in
> practice `gpu_why` is retained but reclassified v1.1-grade until the typed contract + UNKNOWN land.

**v1.1 — `gpu_why` hardening:** typed `{decision, reason_code, params, human}`, system-level last-event
scope (T8), typed-UNKNOWN fallback (today's `"none recorded"` becomes a first-class honest calm line:
"the card was clear; nothing waited on your behalf"), exact-string test mirroring the keyhole's
`pins_the_exact_contract`. Words owned by content-voice-designer; "sourced-not-generated" ratified as
a testable invariant by determinism-safety-reviewer.

**v2 — act** (`gpu_request`/`gpu_release`), gated on TWO GO conditions before any act code lands:
1. **Tier clamp in core** (`Tier::clamp_agent` or a caller-class param on `do_acquire`) with its
   pinned test (T1): agent-identity `Interactive` request installs as `Batch`, `arbitrate` returns
   `Queue` not `Preempt`.
2. **Identity model** binding each token to its MCP session, reusing the `holder_peer` B4
   peer-binding (T6): agent A cannot `gpu_release` agent B's token.
   Outcome words honest: `granted` / `busy_retry` (not `queued`, T7) / `denied(short_mib)`. Agent
   lease surfaces as a `working` Batch holder that names its `holder`, rides the ADR-0018 event-vs-
   state grammar, and never spends warm or an earcon (T3, T4).

**v3 — CONCUR**, gated on a confirmed live KV-pressure signal *proven in the telemetry log first*,
with the FIFO-fill rule in prose (window-down + arrival-order retry, never controller-chosen) and
`_assert_no_priority` extended to the admission path.

**Seven prose additions to ADR-0020 before promotion** (all additive, none gate perceive-v1):
(1) inherit ADR-0019 §3 warm-hue invariant by reference as a tripwire (T4); (2) inherit ADR-0018
event-vs-state grammar for agent-lease surfacing (T3); (3) ratified-silence clause (MCP outcomes are
working/idle weather, no audio); (4) `gpu_why` is a closed-cause typed contract, never prose (T2);
(5) strike the lever-specific `ollama stop` example (line 71) so it can't become the spec; (6) name
`rmcp` (T9); (7) flag `acting` as honestly-deferred (T5). Plus rewrite lines 84-87 so the clamp reads
as a *core transform*, and line 75 `queued` → `busy_retry`.

---

## 5. Rating verdict + 10/10 gap plan

**Overall 7.875/10, no cap, ITERATE** (target 9.0). Split honestly: **perceive-v1 ~9 (genuinely
shippable); act-as-written ~6** (central safety claim in the wrong layer). The headline number is
honest only with that decomposition attached.

Per-dimension — score + the one thing each needs:
- **Vision-fit 9.0** — move the tier clamp from prose-in-shell to a core transform.
- **Experience 7.0** — add an agent-facing UNKNOWN: distinguish "coordinator unreachable" from
  "GPU free," or an agent reads a dead substrate as a free card (keyhole already does this for the
  human at keyhole.rs:281-283; the agent surface returns a bare `"error"` string today, `mcp.rs:113`).
- **Craft 8.0** — commit an exact-string contract test per perceive verb *in the ADR* before any
  further perceive code; there is no versioned MCP↔consumer contract today.
- **Feasibility 7.5** — prove the `gpu_why` cause-source is reconstructible from the telemetry log
  before hardening the verb; don't build the input path twice.
- **Market-fit 7.0** — the differentiation is back-loaded into the gated act/CONCUR phases; the
  moat is the act trust boundary — land clamp-in-core + identity-binding to realize it.

**Prioritized 10/10 gap plan (owners are makers, never raters):**

1. **Move the tier clamp into core + pin it. [GATES ACT]** `Tier::clamp_agent` (or caller-class
   param on `do_acquire`) so an agent-class `Interactive` deterministically installs as `Batch`
   *before* `arbitrate` sees it. Rewrite ADR lines 84-87 to state it is a *core transform*. —
   Owner: **design-technologist** + **determinism-safety-reviewer** (ratify). Closes: feasibility
   (the act cap), vision-fit, craft, market-fit.
2. **Bind each act token to its MCP-session identity. [GATES ACT]** Reuse `holder_peer`
   (lease.rs:227-230). Pin: "agent A cannot `gpu_release` agent B's token." — Owner:
   **design-technologist** + **resource-safety-reviewer** + **wayland-computeruse-reviewer** (the
   off-substrate session hop). Closes: feasibility, vision-fit, market-fit, experience.
3. **Add an agent-facing UNKNOWN to the perceive grammar.** `gpu_status`/`gpu_residency` return a
   typed `unavailable`/`unknown` *posture* (not a bare error string) when the coordinator is
   unreachable, distinct from "GPU idle/free" — mirroring keyhole.rs:281-283. A latent fail-open
   inversion otherwise (ADR-0003). — Owner: **interaction-designer** + **design-technologist**.
   Closes: experience (hard honesty constraint), vision-fit, feasibility.
4. **Commit an exact-string contract test per perceive verb, in the ADR.** Mirror the keyhole's
   `pins_the_exact_contract` + the agent.json serde round-trip — no perceive code lands without a
   versioned pinned JSON shape across the Rust-producer↔MCP-consumer boundary. — Owner:
   **design-technologist**. Closes: craft, feasibility.
5. **Land the seven prose additions into ADR-0020 (behavior change ⇒ ADR moves).** Typed `gpu_why`;
   `busy_retry` not `queued`; system-level `gpu_why` scope + strike per-caller (line 70) and
   `ollama stop` (line 71) examples; warm-hue tripwire by reference; ADR-0018 event-vs-state +
   reduced-motion line; `rmcp` pinned; `acting` honestly-deferred. — Owner: **content-voice-designer**
   (words) + **visual-systems-designer** (warm-hue/token tripwire) + **design-technologist** (ADR
   mechanics + `rmcp`). Closes: all five dimensions.
6. **Add a `gpu_residency` maturity/confidence clause.** Report the ADR-0018 learned footprint *with
   its coexist confidence* (`plan.confident`/`undercount`, already in `mcp.rs:159-161`) or honest-
   UNKNOWN for un-learned models — Phases 2-4 unlanded, `size_vram` undercounts ~1.45×. — Owner:
   **design-technologist** + **visual-systems-designer**. Closes: vision-fit, feasibility.
7. **Pin `gpu_why`'s `human` leaf with a readability acceptance test** (separate from the struct's
   sourced-not-generated invariant). Closed enum kills fabrication; a plain-language pin kills
   typed-but-jargon. — Owner: **content-voice-designer**. Closes: experience, craft.
8. **Add a one-paragraph `acting`-deferral to the ADR (like ADR-0019 G8). [SUBSTRATE-BLOCKED —
   `acting` has no producer; `derive_feed` never emits state 3; computer-use backend unbuilt]** —
   Owner: **content-voice-designer** (flag) + **ambient-embodiment-reviewer** (disposition);
   escalated to human as Q1. Closes: experience, vision-fit, craft.

**Top 3 to close next:** (1) clamp-in-core + pinned test — alone lifts act 6 → ~8; (2)
identity-bound act token — closes the cross-agent DoS primitive; (3) agent-facing UNKNOWN + the
seven prose additions — the honesty constraints that make perceive-v1 *true* not asserted. These
move the blended overall past the 9.0 SHIP target.

---

## 6. Market positioning (significantly-better-than-market)

**Position statement.** For the technical solo builder running a local-AI stack on a single 24GB
GPU — who hits the collision no one else has had to solve, a ray-traced desktop and 17–21GB models
fighting for one card — AgentOS is the **local-AI resource-and-safety substrate** that coordinates
the GPU so the agent never OOMs the desktop and never seizes control. Unlike Ollama (arbitrates only
its own models), cloud agent stacks (can't go local or offer real revert), and datacenter GPU
schedulers (assume MIG a 4090 doesn't have), it ships a tested, fail-open, model-proposes/code-
disposes VRAM lease — and now lets agents *see* that arbitration through a read-only MCP surface,
with action gated behind the same code that disposes.

**Public one-liner:** *"The only local-AI desktop that won't OOM your GPU when the agent thinks."*

**Category — create, don't join.** Create "local-AI resource substrate"; do NOT join "AI agent
platform / agentic OS" (AgentOS has no orchestrator — Hermes is the brain, ADR-0001; "so it's a
worse OpenClaw?" lands in meeting two). The substrate shelf is genuinely uncontested: every GPU-
sharing result is datacenter multi-LLM; `mcp-system-monitor` is read-only Python (~2 stars); nobody
coordinates *graphics vs inference on one consumer card*. Narrow scope IS the position. Hard guard
for content-voice-designer + brand-identity-designer: any one-liner implying "new agentic OS" is a
category lie ADR-0001 forbids — lead with **the floor under the AI, never the AI**.

**Three pillars (trust triad), each maturity-tagged:**
- **Coordinated [PROVEN].** Cross-tenant VRAM arbitration on one consumer card: predict-before-load
  admission with fail-open-per-tier (interactive grants on unreadable NVML, batch fails closed —
  `lease.rs:452-455`), strict-tier SIGKILL preemption, `ollama stop` graceful reclaim. The moat.
- **Legible [PROVEN perceive; DESIGNED act].** Agent and human both *see* the arbitration:
  `mcp.rs` ships three read-only tools sourced-not-generated; act verbs deliberately absent and
  pinned-absent by test. Keyhole tray + reactive wallpaper are the human-facing half.
- **In control [DESIGNED tx; PROVEN arbitration-rollback].** Model proposes, code disposes,
  reversibly. The tier ceiling clamps an agent to Batch; arbitration rollback shipped; the config
  apply-rollback tx (ADR-0005) and MCP act verbs are roadmap.

**Differentiation 9/10 · Defensibility 8/10.** The wedge (graphics-vs-LLM coordination on one
consumer card) is uncontested and rides shipped tested code. The moat is the hard 10% of correctness
(stale-token guard, fits-after-evict re-check, cooperative-vs-owned victim distinction, fail-open-
per-tier) a weekend clone gets wrong. Docked for: the most evocative differentiators (undo-the-
agent's-day, agent-controllable GPU) are the *unbuilt* ones; the wallpaper is a weekend clone
(demote it, never lead); the act identity-scoping threat model is unspecified.

**Positioning risks (the trust story breaks here if violated):** never put a DESIGNED edge (config-
tx, act verbs, CONCUR) in present tense; the `state:3 acting` verb is declared-not-emitted (position
what the binary does); don't overclaim the GPU win (wallpaper yield ~1.5GB, apps ~2.5GB, vs a
19.5–21GB model — the win is coordinated reversible pressure management + graceful reclaim, NOT magic
free VRAM; live VRAM-partitioning is out of scope, no MIG on a 4090).

**Deltas to a 10/10 position (ship the DESIGNED, don't rewrite the claim):** (1) ship act verbs with
tier-ceiling + identity-scoping → turns "agent-controllable GPU" VISION → PROVEN (highest leverage);
(2) ship the ADR-0005 config-tx → turns "complete control / one undo" into a present-tense category
claim; (3) resolve CONCUR open-Q1 (confirm/deny the Ollama KV signal); (4) specify the daemon threat
model → unlocks the privacy/safety *category* claim.

---

## 7. Signature delight moves to land

The differentiator made felt: **a substrate an agent can perceive but is fenced from controlling —
and that fence is honest, code-enforced, and demonstrable.** Every move below rides surfaces that
already exist or are one honest line of telemetry away. None spends warm, coins a channel, or breaks
the act gates.

**S1 — "Ask the desk why it waited." `gpu_why` as a sourced, human-readable confession.** Already
wired (`mcp.rs:177-199`, sourced from `keyhole.json`'s `lease.preempt` + telemetry, never generated).
An agent (or a human reading the transcript) asks *why my run was slow* and gets a true sentence —
"a Batch dream held the heavy lane; it was reclaimed via `ollama stop`, then your model loaded" — not
a plausible guess. Nobody else does this: `mcp-system-monitor` returns numbers, cloud stacks return
nothing local. *Elevation (small):* make the calm case a first-class honest line, not a null — today
`"none recorded"` (mcp.rs:194) should read "the card was clear; nothing waited on your behalf." The
peak-end "nothing was lost" feeling applied to legibility: even *no news* comes back as calm good
news.

**S2 — The locked door you can see through. The act verbs provably, demonstrably absent.** Make the
fence *felt, not just true*: `tools/list` returns exactly three perceive verbs, and the test
`tools_list_has_the_three_perceive_tools_and_no_act_verbs` (`mcp.rs:215-225`) pins their absence as a
contract. "Show me what the agent could do to my GPU" → `gpu_status`, `gpu_residency`, `gpu_why`.
Read, read, read. A 10-second demo of the entire trust thesis. The door is glass: the agent sees
everything, touches nothing. Remove the pinned-absence test and "complete control" becomes an
assertion instead of a checkable fact — **the absence itself is the product.**

**Earned microdelights:** `source` as a quiet self-citation leaf, standardized across all three
tools (the substrate that cites itself, never bluffs); honest-UNKNOWN as a felt *posture* not an
error ("I can't see right now" with the same calm as "the card is clear" — the substrate that knows
the difference between *quiet* and *blind*, gap item 3); `gpu_residency`'s `confident`/`undercount`
flags surfaced as candor ("this estimate is still learning") — personalization as a quiet gift, never
a creepy reveal.

**Differentiation made felt:**
- *Coordinated* → the human watches the keyhole: an agent's job appears as **another Batch holder
  that names itself**, rides the existing ADR-0018 event line, and *never turns the weather warm*.
  The collision that crashes everyone else's stack is, here, a named line in a calm instrument.
- *Legible* → S1: two surfaces, one truth, sourced — the agent's `gpu_why` matches what the keyhole
  showed the human.
- *In control* → S2 + the act-phase peak (deferred, flagged): an agent *requests* `Interactive`, the
  desk quietly installs it as `Batch`, and the desktop never flickers. The agent proposed; the code
  disposed; the user felt nothing. **That is the peak** — contingent on clamp-in-core (gap item 1).

**The "one more thing":** make the absence of power the demo. Lead the 10-second clip with
`tools/list` returning three read verbs and a passing absence-test; close it with `gpu_why` returning
one true sentence. **Restraint IS the delight — the empty `act` slot is the feature.** "I gave the AI
eyes on my GPU and *no hands*, and it can explain itself."

**Cut from temptation (anti-delight):** any task-complete celebration on an agent grant, any "agent
is thinking" glow, any wiring of `acting`/state-3 here. No confetti on a calm desk. Sound stays
ratified-silent — no earcon on any MCP outcome.

---

## 8. Accepted tradeoffs

- `gpu_why` is hardened/narrowed (system-level, typed, no `ollama stop` victim-naming until a
  correlation id exists) — we give up the evocative example sentence to avoid confident-but-wrong
  legibility.
- An agent's GPU wait is *less* visible to the human (a count/holder line, no glow) — correct: warm
  attention is reserved for needs-you, not for the substrate doing its job.
- The act phase is gated behind two hard conditions (clamp-in-core, identity model) — slower to ship,
  but the central safety claim is otherwise enforced in the wrong layer.
- CONCUR may never ship if no live signal exists — accepted; static learned-footprint admission
  already covers the common case.
- `acting` stays a phantom for now — the first plausible real driver exists in this ADR, but the
  felt-state design is deferred to its own ADR rather than bolted on.
- The market headline differentiators (agent-controllable GPU, undo-the-agent's-day) stay roadmap,
  not present-tense — the perceive-only v1 is parity with `mcp-system-monitor` until act lands.

---

## 9. Recorded dissent (never erased)

- **motion-designer** — *standing dissent* (from ADR-0019): an agent-controllable GPU earns NO new
  animated affordance; reuse busy/snag only. **Honored, not overridden** — the Decision adds no new
  affordance. Recorded so a future dedicated agent-GPU surface starts from this position.
- **art-director vs. visual-systems-designer / interaction-designer / brand-identity-designer on
  `acting` (T5):** art-director holds agent GPU activity stays `working` and must not resurrect
  enum 3; three lanes want it reserved/wired. Not resolved by ownership — escalated to human (Q1).
  Neither side erased.
- **design-technologist's precision dissent on "perceive is zero-risk, ships alone":** not fully
  identity-free — `gpu_status` exposing `holder` needs the same identity plumbing the act phase does
  (echoed by art-director). Recorded; the Decision scopes v1 `gpu_status` to tier-without-holder-
  identity to keep the zero-risk claim honest, deferring holder-naming to v2.

---

## 10. Prioritized next actions (the path to 10/10)

1. **[GATES ACT]** Clamp-in-core (`Tier::clamp_agent`) + pinned test — design-technologist +
   determinism-safety-reviewer. *Highest leverage: act 6 → ~8.*
2. **[GATES ACT]** Identity-bound act token (reuse `holder_peer`) + pinned "A can't release B" test
   — design-technologist + resource-safety-reviewer + wayland-computeruse-reviewer.
3. Agent-facing UNKNOWN posture in `gpu_status`/`gpu_residency` — interaction-designer +
   design-technologist.
4. Exact-string contract test per perceive verb, in the ADR — design-technologist.
5. Land the seven prose additions into ADR-0020 (incl. `busy_retry`, typed `gpu_why`, `rmcp`,
   warm-hue tripwire, `acting` deferral) — content-voice-designer + visual-systems-designer +
   design-technologist.
6. `gpu_residency` confidence/maturity clause + `gpu_why` `human`-leaf readability test —
   design-technologist + content-voice-designer.
7. **[GATES PHASE 2]** Prove (or deny) the live Ollama/llama.cpp KV-pressure signal in the telemetry
   log — design-researcher + rust-performance-reviewer.
8. Specify the daemon threat model (SIGKILL + `ollama stop` + token identity scope) —
   responsible-ai-privacy-skeptic + security-reviewer.

---

## 11. Open questions for the human (options · cost · recommendation)

**Q1 — Does the agent-act path claim the `acting` state (enum 3), or stay `working`?** The one
tension ownership + non-negotiables do not settle (T5).
- *Option A (stay `working`, art-director):* an agent is just another Batch GPU consumer; no new
  felt state. Cost: "an autonomous agent is acting on your machine" never gets a distinct ambient
  signature — arguably an honesty-of-mapping gap (brand-identity). Cheapest, safest.
- *Option B (wire `acting` here):* gives autonomous agent action a distinct cool-blue signature now.
  Cost: resurrects a phantom state with no felt-state moodboard, under deadline, as a side effect of
  a resource ADR — the "ships as working-but-faster" pitfall.
- *Option C (reserve + defer — recommended):* flag in ADR-0020 that the act phase is the first
  candidate producer of `acting`, honestly deferred (like ADR-0019 G8); spin a dedicated ADR +
  moodboard when act lands. **Recommendation: C** — neither erases the honesty gap nor bolts on an
  undesigned state; the reversible, ADR-per-behavior-change path.

**Q2 — Does `gpu_request` (act) require the per-request correlation id, or only the identity model?**
- *Option A (recommended):* gate act on the identity model alone; ship `gpu_why` system-level
  indefinitely. Cost: agents can act but can never learn "why *my* request waited."
- *Option B:* gate act on both. Cost: more schema work before any act ships.
- **Recommendation: A** — the identity model is the *safety* gate (blocks DoS); the correlation id
  is a *legibility* refinement that should not block the safety-complete act surface. Ship act with
  system-level `gpu_why`, add per-caller `gpu_why` when the correlation id lands.

---

## 12. Artifacts

- This brief: `docs/design/0020-agent-facing-gpu-council-brief.md`
- ADR stub for the implied behavior changes: `docs/adr/0021-agent-act-tier-clamp-and-identity-binding.md`
  (the two GO-conditions T1 + T6, drafted as a stub — code and the human dispose).
- Recommended edits to the live ADR `docs/adr/0020-agent-facing-gpu-mcp-and-admission-feedback.md`
  (the seven prose additions in §4 + lines 84-87 clamp-as-core-transform + line 75 `queued` →
  `busy_retry`). Not yet written — they are the disposition recommendation.

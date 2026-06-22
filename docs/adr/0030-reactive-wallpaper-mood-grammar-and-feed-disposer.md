# ADR-0030: Reactive-wallpaper mood grammar — file-feed mood vs. RC throttle, one fail-to-calm disposer (extends ADR-0029)

- Status: Proposed (PAUSED — gated on ADR-0029 Open §A, the unbuilt UE wallpaper LAYER; the
  procedural shader is the live wallpaper today and already carries this reactivity)
- Date: 2026-06-20
- Extends: [ADR-0029](0029-ue-wallpaper-primary-shader-fallback-floor.md) — UE-as-wallpaper
  primary / procedural shader as the fallback floor. ADR-0029 §3 left "how the continuous
  `{busy,warm,snag}` floats map onto a UE real-time stage" as an explicitly OPEN design point
  routed to `art-director` + `motion-designer` + `design-technologist`. This ADR ratifies the
  **mechanism and grammar** of that mapping (how the signal reaches UE, how it is disposed, which
  visual axes mood vs. throttle own, how idle/stale/crossfade behave). It does **not** re-open
  ADR-0029's surface-fork governance (Option A held; Option C the documented fallback).
- Carries forward / constrains:
  - [ADR-0009](0009-dreaming-shader-primary-video-as-texture.md) **§2 ambient contract** — the
    `{state,busy,warm,snag}` grammar, the signal allowlist (idle byte-identical, warm reserved for
    `needs_you`, no surface keys on the unemitted `acting` state), and the system-owned parametric
    grade. **§2 stands and constrains this decision** (§1 is the part ADR-0029 inverts for this
    surface).
  - [ADR-0023](0023-creative-environment-pipeline.md) — the locked palette, the SemVer schema, and
    the `window-drag → wind.json` producer→sink (kept; UE consumes the same seam, lock-free vs.
    arbitration — `crates/agentosd/src/wind.rs`).
- Relates to: ADR-0001 (reuse the producer→consumer grammar, do not reinvent it), ADR-0003
  (fail-open supervised — every reactive failure mode resolves to calm), ADR-0004 (graphics yield —
  the throttle ladder this ADR keeps disjoint from mood), ADR-0005 (apply/rollback tx — any UE↔shader
  *source swap* during a crossfade routes it), ADR-0010/0013 (lease + `Tier::Yielding` — the throttle
  channel this ADR fences off from the mood channel), ADR-0019 (the warm bloom already folds the local
  lucid review into `feed.rs`).
- Evidence (spike, throwaway): [`spikes/ue-probe/cvar_ladder.md`](../../spikes/ue-probe/cvar_ladder.md)
  (the throttle ladder + the VRAM-vs-GPU-time lever table), [`spikes/ue-probe/remote_control_setup.md`]
  (RC `:30010` loopback bind, `ExecuteConsoleCommand`, the standalone-`-game` caveat),
  [`spikes/ue-probe/indigo_channel_setup.py`](../../spikes/ue-probe/indigo_channel_setup.py) (the
  landed "Indigo Channel" tableau, the `INDIGO_MOTION_SPEED=0` reduce-motion/freeze seam, the
  warm-chroma-reserved lighting). Precedent disposers in the crate:
  [`crates/agentosd/src/feed.rs`](../../crates/agentosd/src/feed.rs) (bounded code-disposed signal,
  atomic write, schema-drift probe) and [`crates/agentosd/src/wind.rs`](../../crates/agentosd/src/wind.rs)
  (the fast spring, REST_GUST snap-to-zero, lock-isolation, idle byte-stable frame).

## Context

ADR-0029 made a live UE 5.8 (Lumen) environment the *primary* ambient wallpaper, with the procedural
aurora shader as the mandatory fallback floor, and **deferred the reactive mapping** ("how the
continuous floats map onto a UE real-time stage") to a later design pass. That pass — a five-reviewer
panel (mechanism / calm-grammar / privacy / determinism / security), reconciled by the mediator — is
the input this ADR ratifies.

The producer side already exists and is honest: `feed.rs::derive_feed` is a **pure mapping over
read-only SQL counts**, not raw model text — it emits `{state, busy, warm, snag}` as bounded numeric
scalars with a fixed precedence (`needs_you > snag > working > idle`), warm reserved for `needs_you`,
nothing keyed on the declared-but-unemitted `acting` state (3). The window-drag sink (`wind.rs`)
already runs a fast critically-damped spring, snaps to an exact neutral at rest (`REST_GUST`), and is
*structurally* fenced off the lease's arbitration lock. The music bridge already exists outside the
crate (`aurora-audio-bridge.py` + `nimbus-aurora-audio.service`) capturing the default-sink **monitor**
(not a mic) → an FFT → five scalars in `audio.json`, which the shader already consumes.

What was **not** decided before this ADR: which transport carries reactive scene params to UE; whether
the same channel that throttles UE for VRAM also paints its mood; how a UE stage decays to its resting
look; what a stale/blind feed should read as; how the UE↔shader crossfade preserves mood; and how the
existing (unsandboxed) music bridge relates to the agent grammar. Those are D1–D10 below.

## Decision

**D1 — Mood flows over the FILE-FEED; throttle flows over Remote Control; the two are separated by
construction.** Reactive scene params (`agent.json` / `wind.json` / `audio.json`) reach UE through a
**UE-side VALIDATING poller** reading `$XDG_RUNTIME_DIR/nimbus-aurora/*.json` — the same seam the
shader, `feed`, `keyhole`, and `wind` already use — **not** UE Remote Control. RC (`:30010`) is
reserved for the **daemon-owned `Tier::Yielding` THROTTLE ladder ONLY** (ADR-0029 §3) and must be
locked down: loopback bind **asserted at launch** (not merely configured — `127.0.0.1` per
`remote_control_setup.md` §2a, re-checked live), a **fixed cvar-ENUM allowlist** (only the
`cvar_ladder.md` rungs — `r.ScreenPercentage`, `sg.*`, `t.MaxFPS`, `r.Streaming.*`), the **generic
`ExecuteConsoleCommand` endpoint disabled** (it is arbitrary local code-exec — `remote_control_setup.md`
§3), and **browser/DNS-rebinding treated as in-scope** (a loopback HTTP server is reachable from any
page the user opens). This separates MOOD (file-feed, untrusted-but-pure) from THROTTLE (RC, daemon-only,
allowlisted) **by construction**, not by discipline. Routed to `security-reviewer`.

**D1 reconciliation (the panel's one real disagreement, recorded).** The mechanism panelist argued
the *opposite* — push mood from agentosd over RC — on a real feasibility point: a UE-side poller is
**net-new UE code** (a Blueprint with a file-read node, or a small native `UFUNCTION`), because the
`PythonScriptPlugin` is **editor-only and does not tick in a cooked `-game` build**. This does not
move the disposer into UE: per D2, `derive_scene` stays in agentosd and writes a single *pre-disposed*
`scene-params.json` (already eased / clamped / slewed), so the UE side is a **dumb applier**, never a
second home for the grammar. The RC-unified alternative (one hardened channel for both mood and
throttle, no UE-side reader) is the **documented fallback iff** the RC params-only lockdown (generic
`ExecuteConsoleCommand` disabled, only MPC setters + the `cvar_ladder.md` throttle cvars exposed)
proves clean on UE 5.8 — a `[VERIFY-LIVE]` question, since if it does *not*, RC stays an arbitrary
code-exec surface and the file-feed split is mandatory regardless. **Recommendation: design for the
file-feed split** — mood never rides the safety channel, and the disposer + its tests stay in Rust;
collapse to RC-unified only if the file-poller proves costly *and* the params-only lockdown verifies.
Routed to `design-technologist` (the reader) + `security-reviewer` (the lockdown).

**D1 reconciliation — UPDATE 2026-06-21 (the security half of the fork's gate is now design-cleared).**
The `[VERIFY-LIVE]` condition above ("the RC params-only lockdown proves clean on UE 5.8") is
**resolved at the design level, source-grounded** by this session's security review (full spec:
ADR-0029 §B "RE-GROUNDED 2026-06-21"): UE 5.8 already ships a **default-deny function allowlist**
(`bAllowAnyRemoteFunctionCall=false` + `bAllowConsoleCommandRemoteExecution=false`, both default), so
`ExecuteConsoleCommand` is absent by COOKED CONFIG and an MPC mood setter (`SetScalarParameterValue`)
can be the *only* reactive verb allowlisted — RC **can** carry mood without being an arbitrary
code-exec surface. So the gate's **security** condition no longer blocks RC-unified. What still favors
the **file-feed split (D1's standing recommendation, UNCHANGED)** is the *other* half of the condition —
whether the UE-side file-poller is genuinely costly/feasible — which is the **`design-technologist`**
question and remains open (a cooked `-game` build cannot read the file without a net-new Blueprint/native
reader; the SPEC's Candidate-B reader-actor is the only file-side path, and `PythonScriptPlugin` is
editor-only). **Net: choose mood transport on the *reader-cost* axis now, not the security axis.** Two
cautions the review adds for *either* path: (a) even the MOOD sink must use the narrow allowlisted
setter — **never** generic `ExecuteConsoleCommand`, and **never** the `/remote/object/property`
exposed-preset route (it bypasses the function allowlist → wider/weaker surface); (b) per the
resource-safety ruling (ADR-0029 §B), IF mood rides RC the pusher is INDEPENDENT of the throttle
lane/coexistence (safe to build ahead of it) but MUST ship the lock-free, time-bounded, single-in-flight,
relaunch-resetting contract recorded there, as a **second sink** that never shares the throttle's
send-path. `MotionSpeed` stays governor-driven only.

**D2 — The reactive consumer is ONE pure, clamped, validated, FAIL-TO-CALM disposer (`derive_scene`),
modeled on `wind.rs`/`feed.rs`.** It: clamps every input to its declared domain; maps `!isfinite →
neutral` (the `wind.rs::on_gust` discipline — a NaN reads as calm, never a hold); **slew-rate-limits**
all outputs (audio is spiky — an un-slewed level/beat term is a **strobe risk**, an accessibility and
calm defect); bounds outputs with **compile-time min/max**; and decays to the **EXACT baseline**,
snapping below a rest band rather than asymptoting (the `wind.rs::REST_GUST` snap-to-zero, so idle is
reached, not merely approached). It is **unit-testable without a live engine** (pure value in, pure
params out — the `feed.rs`/`wind.rs` test shape). The feed is treated as **UNTRUSTED input**: a max
file size, `O_NOFOLLOW` open, an mtime/staleness gate, a schema-version check, and **parse-fail →
neutral** (the `feed.rs` "fold an error into all-zeros" posture, ADR-0003). Routed to
`determinism-safety-reviewer` (purity/clamp/slew) + `design-technologist` (the poller).

**D3 — Throttle and mood own DISJOINT visual axes.** Mood owns **motion-rate / fog-density-delta /
warm-inscatter**. Throttle owns **screen-percentage / MaxFPS-ceiling / Lumen-fidelity / streaming-pool**
(the `cvar_ladder.md` rungs). Throttle is **"same mood, cheaper" — never a different mood**: a yielded
FLOOR rung must read as the *same room, dimmer-rendered*, not as a state change. Where the two collide
on a shared lever (frame rate), **throttle is a CEILING and mood is a position under it** (mood may
quicken motion only up to the throttle's `t.MaxFPS` cap). Otherwise VRAM pressure would read as a mood
change — a calm/honest-mapping violation (ADR-0009 §2). Routed to `motion-designer` (mood axes) +
`resource-safety-reviewer` (the throttle ladder is also the compute budget, ADR-0029 §5).

**D4 — Idle for a LIVE stage = PARAMETER-identical to the authored resting tableau, all agent deltas
= 0.** This is the *correct restatement* of ADR-0009's idle-byte-identical invariant for a live
renderer: a UE stage has an **autonomous parallax drift that is the canvas** (the `indigo_channel`
`LevelSequence` loop) — agent state is the **paint** laid over it. So idle is not a frozen frame; it is
the authored resting tableau with **every agent term at 0** (`busy=warm=snag=0`, wind at neutral, music
ducked/off). The disposer drives params back to the **exact** baseline (D2's snap-to-rest); **any sticky
cvar used for a mood effect must be actively RESET to baseline, not merely un-pushed** (a held cvar
would leave a residue idle never reaches — the inverse of the `wind.rs` neutral-vector guarantee). The
shader floor keeps the strict byte-identical idle unchanged. Routed to `ambient-embodiment-reviewer`.

**D5 — Window-drag (`wind.json`): SHIP.** Already built and privacy-correct: geometry-only
(`Gust(dirX,dirY,speed,active)` — three floats and a flag is the *entire* payload, no window
content/title/identity, `wind.rs:107`), off-by-default, ephemeral (no persistence, no ledger),
reversible. Route it to the **AIR / fog-impulse axis** — a gust through the volumetric medium —
**DISTINCT** from the agent-mood axes, on its own **fast spring (~1.5 rad/s, `OMEGA` in `wind.rs:58`)**
so direct manipulation feels responsive. **Two springs, two timescales:** wind is fast
(direct-manipulation), agent mood is slow (the ambient 2–20 s family, ADR-0009 §2). They never share a
spring and never share an axis. Routed to `interaction-designer` (the manipulation feel) +
`reversibility-tx-reviewer` (confirm wind is **never** ledgered — it is fail-open ambient, not a desktop
mutation, the `wind.rs` lock-isolation guarantee).

**D6 — `agent.json` is ALREADY a code-disposed bounded signal — lock the invariant.** `derive_feed`
runs over SQL counts, not raw model text (`feed.rs:85-112`). The invariant this ADR locks: the
wallpaper consumes **only bounded numeric scalars, NEVER free-form or model-authored strings**; the
**palette stays locked** (ADR-0023; warm reserved for `needs_you`, D8); reactivity **modulates intensity
WITHIN bounds**, never injects content. A future producer that wanted to pass a string (a label, a
prompt echo) is **out of bounds** and would need its own ADR + privacy review. Routed to
`responsible-ai-privacy-skeptic` (the "no model-authored content on the ambient surface" red-line) +
`ai-product-reviewer`.

**D7 — MUSIC reactivity is ENTERTAINMENT, not the agent's voice — and is SCOPED OUT of this ADR to its
own future ADR.** The pipeline **already exists** (`aurora-audio-bridge.py`: default-sink **MONITOR /
loopback — NOT a mic** — FFT → five 0..1 scalars in `audio.json`, AGC-normalised, ~30 Hz; the aurora
shader already consumes it). It is **entertainment**: off-by-default, **never focal**, **NEVER touches
the warm / `needs_you` channel or the focal backlight or the parallax**, decays-to-calm, and **the
agent grammar always WINS — music DUCKS under any active agent state**. It is named as *entertainment*,
not the agent's voice (the ADR-0009 §4 naming discipline). Because it is its own behavior with its own
consent + sandboxing surface, **this ADR references it but does not fold it in** — a future ADR owns it.
That future ADR must **HARDEN the existing service**, which today has **ZERO systemd sandboxing**
(`nimbus-aurora-audio.service` — only `Nice=8`): add `NoNewPrivileges`, `ProtectSystem=strict`,
`ProtectHome` (read-only home), `PrivateTmp`, `RestrictAddressFamilies=AF_UNIX` (no network),
`MemoryDenyWriteExecute`, a `SystemCallFilter`; **ASSERT monitor-only by construction** (the captured
device name must end in `.monitor`; **reject any non-monitor source** so it can never be pointed at a
real input); **pin it as an owned component** (not a loose `~/.local/share` script the unit `ExecStart`s
today); add a **visible "listening" indicator + a hard-off**. Routed to `security-reviewer` (sandbox +
monitor assertion) + `responsible-ai-privacy-skeptic` (any-audio-capture consent) + `sound-designer`
(the named "this is entertainment, not the agent" framing) — **all owed in the music ADR, not here.**

**D8 — Warm monopoly: a PER-TABLEAU warm-budget gate.** Extend the locked-palette validator (ADR-0023)
so any authored tableau's **resting (agent-zero) frame must have NO chroma in the reserved warm band**
— a tableau that already glows warm at rest would make the rare `needs_you` warmth meaningless (the
`indigo_channel_setup.py` lighting already obeys this by hand: "Nothing here carries warm chroma —
warmth stays reserved for the needs-you signal," and it actively sweeps the leftover golden-hour sun).
`needs_you` is the **ONLY** warm injector: the **Indigo Channel** realization is a *slow warm shift in
the far-end backlight inscatter* (the cyan rake leans warm-amber at the channel's far end, breathing on
the agent timescale). Routed to `visual-systems-designer` (the validator) + `art-director` (the
per-tableau authoring) + `ui-accessibility-reviewer` (warm must not be the *only* `needs_you` cue — see
Accessibility).

**D9 — Stale ≠ serene: a freshness signal with a DISTINCT quieter-than-idle look.** A stale/blind feed
(no producer, an mtime past the staleness gate, a missed heartbeat) must **not** read as the calm `idle`
the wallpaper feed folds an unreachable Hermes into today (the `keyhole` "honest UNKNOWN" lesson — an
unreachable producer should read `unknown`, not `idle`). Add a freshness signal (mtime / heartbeat); a
stale feed gets a **distinct quieter-than-idle** look — drift slows *further* than idle, the backlight
dims one step — reading as **"I can't see"** (calmer than idle, **NOT** snag, never alarming). A blind
feed **also triggers a throttle toward FLOOR**: a wallpaper that can't see the fleet shouldn't burn full
Lumen. Routed to `determinism-safety-reviewer` (the staleness/heartbeat gate) + `ambient-embodiment-reviewer`
(the "I can't see" reading) + `resource-safety-reviewer` (blind → FLOOR).

**D10 — UE↔shader crossfade = MOOD-continuity, not a pixel crossfade.** When UE is killed and the shader
floor comes up (ADR-0029 §1, ADR-0004), the eased `{busy,warm,snag}` state must live in the
**producer-adjacent CONSUMER layer — NOT inside the UE process being killed** — so the shader floor comes
up *already at the current mood* (a dying process can't hand off its own eased state). The shader floor
**matches the resting palette** (it is a quieter version of the same indigo room, ADR-0029's "shader
floor as a quieter same-mood render") and **eases on the agent timescale**, not a hard cut. The
crossfade is mood-continuity; if the *wallpaper source itself* is swapped as a desktop-state mutation,
that swap routes the ADR-0005 apply/rollback tx (ADR-0029 §7 / ADR-0009 §5) — a degraded-render
fall-to-floor is fail-open (ADR-0003), not a tx. Routed to `motion-designer` + `design-technologist`.

## Consequences

- **2026-06-21 — a fourth producer feed: `drift.json` (ADR-0034 Tier-2).** The Style Charter's
  drift-from-kept-identity signal joins `agent.json`/`wind.json`/`audio.json` as a bounded, untrusted,
  schema-gated producer feed read by the `scene.rs` disposer. It folds a gentle `DRIFT_DESAT_MAX=0.25`
  floor into the `Desat` mood axis (via MAX with the snag-desat, never warm — the D8 monopoly holds),
  so a desktop that has drifted from its kept aesthetic reads as a faint "quietly not-quite-itself"
  haze. Same disposer, same grammar (D1): drift is a LOCAL signal (read even when the fleet feed is
  stale/blind, like wind), drift=0 ≡ the byte-identical idle anchor (D4), eased on the slow spring.
  Producer = `ui-audit-style.py emit-drift` + `nimbus-aurora-drift.timer`. Still PAUSED with the rest of
  the stack (actuation needs the gated `scene`/`rc` services running). See ADR-0034.

- **Honest record.** This ADR is *Proposed* and **PAUSED**, marked like `Tier::Yielding` itself:
  **all live-on-the-wallpaper reactivity is GATED on ADR-0029 Open §A** — the UE wallpaper *LAYER* is
  unbuilt (no native-Wayland wallpaper host exists; live-windowed proves UE-runs-on-Wayland, not the
  layer). **The procedural shader is the live wallpaper today and already performs this reactivity**
  (the `feed`/`wind` producers ship; the shader consumes them). So this ADR ratifies the **grammar and
  mechanism the UE consumer will obey when there is one** — a proposal-of-a-proposal on the same
  long time-horizon as ADR-0029. **The disposer half (D1/D2) was the one piece buildable ahead of §A
  and is now BUILT** — `crates/agentosd/src/scene.rs` (`agentosd scene`), the pure clamped/slewed
  fail-to-calm `derive_scene` writing `scene-params.json` (see the implementation-status bullet below).
  The matching UE-side validating poller (which needs only a running `-game` proc reading the file) is
  the remaining buildable-ahead-of-§A piece.

- **DECIDED vs spike-proven vs OPEN — read precisely:**
  - **DECIDED (subject to ratification):** the file-feed/RC mood/throttle split (D1); the single
    fail-to-calm `derive_scene` disposer contract (D2); the disjoint mood/throttle visual axes with
    throttle-as-ceiling (D3); parameter-identical-idle for a live stage (D4); ship window-drag on its
    own fast spring/axis (D5); the bounded-scalar / no-model-strings invariant (D6); music-as-entertainment
    + scoped-to-its-own-ADR (D7); the per-tableau warm-budget gate (D8); stale≠serene + blind→floor (D9);
    consumer-layer mood-continuity crossfade (D10).
  - **PROVEN in spike (risk retired, code NOT in the crate):** the throttle ladder + the VRAM-vs-GPU-time
    lever table (`cvar_ladder.md`); RC loopback bind + `ExecuteConsoleCommand` shape + the
    standalone-`-game` auto-start caveat (`remote_control_setup.md`); a warm-reserved tableau that runs
    live and was approved on look, with a working `INDIGO_MOTION_SPEED=0` freeze seam (`indigo_channel_setup.py`).
  - **BUILT + REVIEWED + TESTED 2026-06-20 — the `derive_scene` disposer (D2), the agentosd half.**
    `crates/agentosd/src/scene.rs` (the `agentosd scene [--once]` mode) implements the single
    fail-to-calm disposer modeled on `wind.rs`/`feed.rs`: clamp-to-domain + non-finite→calm (D2),
    explicit slew caps on every output (anti-strobe, D2), snap-to-target so idle reaches the EXACT
    resting baseline AND a held mood/stale look is byte-stable (D4), the disjoint MOOD-only axes
    mapped onto the Indigo Channel levers — motion/fog/backlight/warm/desat/air — with NO throttle
    cvar by construction (D3), warm injected ONLY by `needs_you` (D8), stale≠serene + a published
    `fresh` signal for the governor's blind→FLOOR read (D9), the reduce-motion static-tone fallback
    (accessibility), and untrusted-input readers (`O_NOFOLLOW`, max-size, schema gate, parse-fail→
    neutral) reusing the hardened 0700 `feed::feed_dir`. 24 unit tests + 137-test suite green, clippy
    clean, verified live through the binary (busy/needs_you/snag/blind/reduce-motion). A four-reviewer
    adversarial panel (determinism-safety / ambient-embodiment / resource-safety / staff-Rust) returned
    **SHIP-AFTER-FIX, no BLOCKER/MAJOR**; all MINOR findings fixed (snap-to-target, `-0.0` scrub,
    softened `MOTION_STALE`, missing transition/cap/`-0` tests). *Still UNBUILT* (and gated on §A or its
    own surface): the **UE-side validating poller / dumb applier** (the other half of D1/D2), the RC
    allowlist + `ExecuteConsoleCommand` disable (D1, `security-reviewer`), the **producer heartbeat**
    that activates the `Stale` middle state (a one-line `feed.rs` follow-up — the consumer already
    handles it), the adaptive idle poll cadence (before 24/7), and the consumer-layer crossfade (D10).
  - **OPEN / NOT ratified:** the **UE wallpaper LAYER itself** (ADR-0029 Open §A — the gate this ADR
    sits behind); the exact float→tableau mapping *per tableau* beyond the Indigo Channel (each new
    tableau needs its own warm-budget pass + axis tuning, like the shader's per-style table in
    `docs/vision.md`); the music ADR (D7) in full.

- **MUST-FIX-BEFORE-SHIP (the panel's hard gates):**
  - **RC `:30010` lockdown** (D1): loopback bind asserted at launch, fixed cvar-enum allowlist, generic
    `ExecuteConsoleCommand` disabled, DNS-rebinding in scope. `security-reviewer`.
  - **Runtime-dir hardening.** `$XDG_RUNTIME_DIR/nimbus-aurora` (`/run/user/<uid>/nimbus-aurora`) is
    currently created mode **0755 (world-readable)** by `feed.rs::feed_dir` — every reactive feed file
    is readable by other local uids. Fix: **0700 + assert-owned**, and harden the uid fallback to use a
    **real `getuid()`** rather than `feed.rs::current_uid`'s `/proc/self`-then-**default-1000** path
    (a wrong uid points the fallback at *another user's* runtime dir). `security-reviewer` +
    `reversibility-tx-reviewer`.
  - **Audio service sandboxing + monitor-assertion** (D7) — owed in the music ADR, recorded here so it
    is not lost: the existing unit has zero sandboxing today.

- **ACCESSIBILITY (owed to `ui-accessibility-reviewer`):**
  - **Reduce-motion needs a NON-motion agent-state fallback.** The `INDIGO_MOTION_SPEED=0` seam freezes
    *all* motion (the canvas drift included), so with motion off the mood must be carried by **static
    tone**, mirroring the shader's documented reduce-motion fallback (`docs/vision.md`): **`busy` = a
    static brightness step held under the bloom threshold**; **`snag` = a static fog-thicken +
    desaturate**; **`needs_you` = a held (non-breathing) warm lobe**. State must survive with zero motion.
  - **Non-color-redundant cues.** No agent state may be carried by hue alone — pair every state with a
    **non-color** channel (fog density, parallax pace, depth-of-field), so `needs_you`-warm and
    `snag`-cool are distinguishable without color discrimination (D8's warm monopoly must not become a
    color-only signal).
  - **`acting` / state-3 stays OVERLAY-owned, never in the dark-ride** (ADR-0009 §1 + `docs/vision.md`:
    the spatial-attention overlay owns `acting`; the wallpaper offers at most a faint cool cue). The
    dark-ride keys no surface on state 3 (it is still unemitted by `derive_feed`).

- **Routing hand-offs preserved (the panel's bidirectional edges):** `wayland-computeruse-reviewer` —
  the wallpaper LAYER (ADR-0029 §A, the gate); `security-reviewer` — RC `:30010` + runtime-dir mode +
  audio sandbox; `reversibility-tx-reviewer` — **never ledger wind or music** (ambient, fail-open, not
  desktop mutations) and the runtime-dir ownership; `ui-accessibility-reviewer` — contrast, redundancy,
  reduce-motion; `responsible-ai-privacy-skeptic` — the no-model-strings red-line (D6) + any
  audio-capture consent (D7); `determinism-safety-reviewer` — the pure clamped slew-limited disposer
  (D2) + the freshness gate (D9).

- **Reuse, do not rebuild (ADR-0001 carry-forward).** `derive_scene` is `derive_feed`/`wind.rs` with a
  richer output struct — the **same** atomic-write, the **same** edge-driven poll, the **same** snap-to-rest,
  the **same** lock-isolation, the **same** parse-fail-to-neutral. UE consumes the **same** `agent.json` /
  `wind.json` / `audio.json` seam the shader does. We add a renderer and a disposer, **not** a new
  producer grammar.

## Open questions for the human (framed)

1. **Prototype the UE file-poller now, or wait for the layer (§A)?** Recommendation: **prototype
   `derive_scene` + the validating poller in the windowed `-game` spike now** — it is the one piece
   buildable ahead of ADR-0029 §A (it needs only a running proc reading `*.json`), and it de-risks the
   slew/clamp/idle behavior on a real engine before the layer effort. *Cost of waiting:* the disposer
   stays paper until the layer lands, and the layer is the larger, later effort.

2. **Split the music ADR out now, or let it ride this one?** Recommendation: **split it (D7)** — music
   has its own consent surface, its own (currently unsandboxed) service, and a different owner
   (`sound-designer` + `security-reviewer`), and folding it in would let the unsandboxed-service risk
   hide inside a paused wallpaper ADR. Reference it here; write it as its own Proposed ADR with the
   sandbox + monitor-assertion as its must-fix gates. *Cost of folding:* the zero-sandboxing audio
   service stays an untracked liability behind a paused decision.

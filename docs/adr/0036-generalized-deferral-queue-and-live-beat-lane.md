# ADR-0036: Generalized deferral queue — a reusable lane library, the live-beat lane, and the private-path completion

- Status: **Proposed — council-ratified, ship-after-edits** (model proposes, code disposes; the
  human disposes on this). Resolves the design forks for generalizing ADR-0019 beyond its single job
  type. No code shipped yet; this records the decided architecture so implementation does not drift.
  Design-council 2026-06-21: scope debate decided (keep 2 lanes wired; reframe D2 to a noun-vs-verb
  gate — see D2 council note); vision-fit 9/10, feasibility 7/10. Verdict: sound, ship after the
  edits folded in below (the GO/NO-GO gate in the Phased plan, the D4 freshness-predicate spike, and
  D5-before-Phase-2 sequencing). No non-negotiable violated.
  **Implementation: Phase 3 (D9) SHIPPED + verified 2026-06-21 (privacy seal HOLDS, determinism SOUND).
  Phase 1 (D5 + I9 + D3) SHIPPED + verified 2026-06-21** — `freeze_intent` captures the intent as
  values; the durable drainer ENFORCES the frozen prompt/seed/engine-family (engine pinned via
  `set_engine`, oneshot-safe), the private in-process drainer enforces prompt/seed but NOT engine (no
  global mutation beside a live dream); `workflow`/`est_mib` are recorded-for-audit but live-resolved
  (workflow process-immutable; est reconciled by the engine pin); I9 clock seam threaded through the
  leaves AND the drain orchestrators (replayable tick); legacy records (no `frozen`) drain byte-identical.
  Determinism+regression review: SOUND/SAFE, 2 residuals closed (honest docstring + full I9 seam).
  **D7 (idempotent compute-commit lock) DEFERRED — re-scoped to Phase 2 after a code trace:** the durable
  create-from-image drainer calls `L.start` (which RESETS the chain to `{opening}`) before `L.step`, so a
  post-commit crash + re-run resets-then-appends one beat → a single clean node, NOT a duplicate (the only
  residue is a wasted clip file, a minor disk leak). The duplicate-NODE hazard D7 targets materializes only
  for an APPENDING lane — the live beat — so D7 is naturally designed WITH Phase 2's append + the I10
  freshness predicate, not as a standalone create-from-image fix. Phases 0-spike/2 still open.
- Date: 2026-06-21
- Deciders: design synthesis from four expert passes (resource-safety, ai-product, ux,
  determinism-safety), pending human + a follow-on security/privacy review of the live-beat
  needs-review path.
- Extends: **ADR-0019** (reviewable request queue — widens its one-lane scope to a multi-lane
  library and adds a second lane; ADR-0019's create-from-image behavior is unchanged and stays
  operational).
- Re-affirms (does NOT supersede): **ADR-0010** (VRAM coordinator / `lease.Queued`) — the daemon
  stays a memoryless accept-or-reject lease + supervisor; this ADR adds **no** wait-queue to the
  daemon. **ADR-0001** (substrate not orchestrator) — the deferral buffer is a per-lane userspace
  add-on, not a scheduler; Hermes still owns scheduling/ordering.
- Relates to: ADR-0003 (fail-open supervised), ADR-0005 (apply/rollback — frozen-intent record is
  the replay record), ADR-0006 (Hermes plugin — inference backpressure stays "gateway holds the
  response", never the spool), ADR-0012 (keyhole legibility — `lease.json` off-lock mirror is the
  closed-loop drain trigger), ADR-0016 (lucid private ephemeral mode), ADR-0017 (B2 seed-likeness
  guard), ADR-0020 (agent-facing GPU MCP — "any move toward ordering must supersede ADR-0019
  explicitly, not smuggle it in"; this ADR is that explicit move), ADR-0022 (creative-app MCP —
  Blender launches stay admit-before-launch, NOT queued), ADR-0029 (UE wallpaper — degrades/yields,
  never queued), ADR-0035 (voice — CPU-only, never queued).

## Context

ADR-0019 promoted Lucid "Create Video from Image" from a tombstone board to a durable deferral
buffer: a couldn't-run-now request is held on disk, auto-retried by a best-effort drainer, and
surfaced as a reviewable row instead of a dropped notification. It is **built and verified for one
job type** and is, by contract, locked to that one job type (an anti-scheduler test raises
`SystemExit` on any priority key).

The user asked to generalize this so that "we don't just send a notification that your request was
denied" across **multiple job types**, and specifically flagged the one remaining bare dead-end: the
**live interactive dream beat**, which on a VRAM denial discards the chosen turn and shows a soft
"that beat was skipped — choose again" banner (`lucid_web.py:194` sets `phase="skipped"`; banner at
`Chain.tsx:980`). That banner is the exact "just a denied notification" being complained about —
the user already committed a narrative choice and the system throws it away.

Verified current state (code trace):
- **Durable create-from-image lane: works.** Disk-backed fsync'd spool, suffix-state machine
  (`.held`/`.running`/`.review.json`), atomic-rename single-flight claim, FIFO-by-`seq`,
  crash-recovery-from-file, snapshot-copied-into-spool at intake, best-effort `systemd --user`
  drain timer (`lucid_queue.py`, `lucid_drain.py`). Anti-scheduler enforced in code
  (`lucid_queue.py:77-92`, `SystemExit` on `priority|weight|rank|urgency|boost|class`).
- **The spool is already lane-parameterized.** Every function takes an explicit `spool=` dir; only
  `durable_dir()` hardcodes the Lucid default. Extraction to a multi-lane library is mechanical.
- **The daemon never queues, by design.** `lease.rs` says in three places "there is no wait-queue;
  the loser retries, holds no place" (`:29-32`, `:148-152`, `:917-933`). It already writes a
  `lease.json` mirror **off-lock** on every transition (`lease.rs` `write_lease_mirror`), so a
  lease-going-idle is already a file event a drainer can watch.
- **The private path is built but not wired live.** `lucid_priv_queue.py` / `lucid_priv_drain.py`
  exist and are unit-tested, but `create_from_image.py` never calls `hold()` (both deferral sites
  are `if not private`) and `lucid_web.py main()` never starts the `run_in_session` drainer thread.
  A deferred **private** request is therefore still silently dropped today.
- **Intent is only half-frozen.** At enqueue the *image bytes* are frozen (copied into the spool,
  EXIF-stripped, never re-fetched). But `seed`, `prompt`, `workflow`, `engine`, `quality`, and
  `est_mib` are **re-decided at drain** from a mutable model registry pointer
  (`lucid_engine.py` `current_engine()`/`WORKFLOW`/`MODEL`; `lucid_linear.py:179` mints a fresh
  `seed` per `start`). Tolerable for create-from-image (the re-decided values are constants or
  don't-cares); load-bearing the moment a lane carries a meaningful prompt or quality choice.

## Decision

Adopt a **narrow generalization**: extract the proven ADR-0019 machinery into a reusable,
multi-lane **userspace deferral library**, then wire exactly **one** new lane — the live dream beat,
**human-gated, not headless** — and finish the dormant private path. The substrate gets no
wait-queue; Hermes keeps scheduling; the FIFO-per-lane / no-cross-lane-order contract is preserved
and re-asserted.

### D1 — Where the queue lives: a userspace library, never the daemon (resolves the central fork)

The cross-job-type queue is a **shared userspace library** extracted from `lucid_queue.py` /
`lucid_drain.py` / the state machine. Each lane owns its **own spool directory**; the library never
enters `agentosd`. This is the only option that honors both load-bearing invariants at once:
arbitration (who runs now) stays memoryless in the daemon; durable deferral (this intent survives a
busy GPU / a reboot) stays in userspace — exactly "the wait-queue `lease.rs` deliberately declined
to be."

Rejected alternatives: a daemon-side waitlist (contradicts ADR-0010's core scope and couples
durable disk state to the process that holds the GPU lever — a queue bug could wedge the safety
floor); per-feature copy-paste (re-implements subtle fsync/atomic-rename/crash-recovery N times, each
a fresh chance to silently lose a held intent); pushing deferral up into Hermes kanban (right
long-term home for *inference* work, but cannot hold a desktop-local intent while Hermes is down —
which is the exact silent-drop being fixed — and is gated on a confirmed Hermes write-API).

### D2 — The gate: queue durable nouns, not live verbs (classify by noun, not by subsystem)

The deferral gate is a single test, applied per *request shape*, not per subsystem: **a deferrable
request is a durable noun ("make me this") whose originating context can vanish (the tab closes,
Hermes is down) and which no existing system already holds durably. A live verb — a turn, a session,
a launch happening now, whose value collapses with latency — is not deferrable; it REJECTS or
DEGRADES (the voice prior disciplines this).**

v1 wires exactly the two lanes whose noun is desktop-local and unheld elsewhere:

| Lane | Policy | Why it passes the gate |
|------|--------|------------------------|
| Lucid create-from-image | **DURABLY QUEUE** (headless auto-drain) | Durable noun; zero staleness; desktop-local (a Dolphin/Firefox right-click Hermes never saw). |
| Lucid **live dream beat** | **DEFER, human-gated** (D4) | A committed narrative choice — a noun *while context holds*; not headless-replayable, hence human-gated. |

Everything else is **not wired in v1**, recorded as a *gate result*, not a permanent per-subsystem
"NO" — so a correctly-classified future lane is not forced to relitigate against a written-down
exclusion:

| Request | v1 result | Classification |
|---------|-----------|----------------|
| Interactive LLM turn | not deferred | **Verb** — high staleness (a late turn is garbage); its preemption need *requires* a priority key the buffer forbids; served by the daemon's memoryless tier arbitration, not a queue. |
| Batch / overnight inference | not in this library | **Noun, but already held** — owned by ADR-0010's Batch tier + Hermes kanban (durable, ordered, survives a crash, has dependency graphs the buffer cannot). A *desktop-local* batch-inference artifact submitted while Hermes is down is the only future candidate for this library; no producer demands it today. |
| Interactive Blender launch | not deferred | **Verb** — a present, context-bound session (ADR-0022 §2 admit-before-launch). |
| Headless Blender render job | not in this library | **Noun, but already held** — a lease-owned `Spawn` job the daemon supervises (ADR-0022 §6); its "defer until VRAM frees" is `busy_retry` + caller/cron re-submit. Revisit only if a render gains a fire-and-forget producer with no cron home. |
| UE live wallpaper | not deferred | **Verb** — it is the thing that *yields* (ADR-0029); "resume the wallpaper" is undefined. |
| Voice utterance | not deferred | **Verb** — denied exactly when it must speak (ADR-0035); near-zero staleness tolerance. |
| Reactive mood pushers | out of scope | CPU-side; never GPU-lease-bound. |

Council note (why this is a *gate*, not a permanent exclusion list): the live Hermes
`gpu-coordinator` plugin currently hardcodes `tier="interactive"` for **every** inference call
(`integrations/hermes/gpu-coordinator/__init__.py`), so "inference = never deferred" is true today
only *by accident* — there is no batch-inference path at all. When that tiering is corrected to the
`batch`/`overnight` tiers the lease client already defines, "a late turn is garbage" becomes provably
false for the batch case. Writing the carve-out as a noun-vs-verb gate keeps the architecture honest
when that day comes. Because the library is lane-parameterized, a correctly-classified third lane is
a producer change, not a rebuild — so the right move is to **state the gate and decline to wire**,
never to bake in a permanent NO against a noun. (Recorded dissent: the narrow-scope defender holds
that even the *framing* is unnecessary — the existing homes for batch inference (kanban) and render
jobs (`Spawn`) make any future library lane redundant, not merely unbuilt. The gate framing is
adopted because it is near-free and avoids a justification the codebase is built to contradict.)

### D3 — FIFO-per-lane, no cross-lane scheduler (the line that keeps D1 legitimate)

- Each lane sorts strictly by its **own** per-spool monotonic `seq`. There is no global `seq`, so two
  lanes cannot be interleaved by arrival.
- The library MUST NOT provide a "pick the next lane to drain" function. When two lanes' drainers
  both want the single GPU lease, the **daemon** arbitrates by tier (memoryless, no priority); the
  loser gets `busy_retry` and re-polls. The daemon's tier arbitration is the *only* cross-lane
  ordering authority.
- The `_assert_no_priority` `SystemExit` guard is inherited by the library **and** extended with a
  cross-lane twin test: *no userspace module reads two spool dirs and emits a merged order.* This is
  the explicit, non-smuggled supersession ADR-0020 demands.

### D4 — The live-beat lane: in-session hold, then human-gated review (NOT headless auto-drain)

A live beat's intent is its **narrative closure** (parent frame, premise, story-so-far spine, rating
floor, seed, subject anchor, the post-decompose `prompt_final`) — almost none of which the current
spool captures, and all of which the session can move past. UX and determinism converge: a live beat
**must not be silently auto-drained later**. The behavior:

1. **Tab open / context intact** → an **in-session bounded retry** against the same tip (reuse the
   existing warm-keep `_ensure_lease` retry). The user is watching; the narrative head has not moved;
   so painting the beat the moment VRAM frees is honest and requires **zero taps**. This is an
   in-session hold, **not** the durable auto-drain lane. The un-taken sibling beats stay pickable
   (existing ghosts at `Chain.tsx:348`); choosing a different beat supersedes the held one via the
   existing epoch bump. Calm banner (replaces the `skipped` banner), never the warm `needs_you`
   signal:
   > "Painting this beat as soon as the graphics card frees — it's busy with your live work right
   > now. Stay here and it'll appear on its own; you don't have to pick again."
2. **Tab closes / in-session retry budget exhausts with the beat unrun** → the intent may now be
   stale, so **freeze the full beat-intent to values** (`parent_id` + a content hash of the parent
   node's `out_frame`, the gated `prompt_final`, `seed`, `anchor` bytes, `rating`, `quality`,
   `length`, premise/subject as captured values) and write a durable **needs-review** row
   (human-disposes), **not** a `held` auto-drain row. Exit toast + panel row:
   > Toast: "Want this beat saved to make later? You left a dream beat waiting for the graphics card.
   > It's set aside in your Lucid queue — open it whenever to make it, or it'll quietly expire.
   > Nothing runs on its own."
   > Panel: "A dream beat you started — pick it up to paint it, or let it go." `[Make it] [Dismiss]`
3. **On "Make it"** → replay validates a **freshness predicate**: if the parent-frame hash or rating
   floor has changed, decline deterministically with a new cause `context-moved` (a `needs-review`
   terminal that explains the spine moved) — **never** a silent stale append.
4. **Genuine fault vs busy** → bound the in-session hold; on exhaustion *with the coordinator
   reachable* escalate to step 2 (staleness path); on exhaustion *with ComfyUI/coordinator down*
   show the honest `error` banner. Two honest terminal states, never one calm banner papering over a
   crash.

This keeps the **only headless auto-draining lane** as create-from-image (where the seed fully
captures intent). Live beats are either in-session (context intact) or human-confirmed
(freshness-guarded) — which is precisely why the determinism Blocker (I10 below) does not bite.

### D5 — Freeze the full intent to values (close the latent non-determinism for every lane)

The library's spool record carries the **complete frozen intent as values**, never registry
pointers: every binary input copied into the spool (inherit), and `seed` / gated `prompt` /
resolved `workflow` (path+hash) / `engine` id / `quality` / `est_mib` captured at enqueue. The drain
runner reads **only** the record; it calls zero `current_engine()` / `lucid_models.get(...)` /
env-resolving accessor. This fixes the "latest pointer" race (registry flips Wan→10Eros between
enqueue and a 6-hour-later drain) and is also the record a rollback would replay (ADR-0005).

### D6 — Gates run at enqueue on the frozen value; drain re-runs only resource + defensive red-line

The model/consent gate (B2, ADR-0017) runs at **enqueue** against the frozen bytes — only a cleared
seed enters the spool; possible-minor is refused and **never** enqueued — which is why the drain may
run `_trusted_seed=True` without re-judging. The generalization preserves this for every new
model-judged input (a frozen `prompt_final` is gated at enqueue; the drain re-gate is a cheap
defensive double-check, not the primary authority). Two gates legitimately re-run at drain: **lease
admission** (GPU state is meant to be fresh) and a **stale-consent re-confirm** past a TTL — a
deferred *creation* older than the freshness TTL re-confirms or downgrades to `needs-review` (closes
ADR-0019 Open Question #2, which auto-drain made sharper); a deferred *check* runs silently.

### D7 — Idempotent at the compute-commit boundary (not just the claim boundary)

ADR-0019 promised an `O_EXCL` claim-lock at the compute-commit boundary but the spool implements only
the rename-claim, which guards *concurrent* drains, not *sequential* re-run after a post-commit
crash (a clip committed to the chain, then a crash before `writeback("done")`, re-runs and duplicates
the artifact). The library closes this: either content-address the output by the **frozen**
`job_id`+`seed` (a re-run overwrites rather than duplicates — requires D5), or take the promised
`O_EXCL` lock before the compute-commit boundary and release it in `finally`. Pin ADR-0019's
**T-idem-4 (crash-both-polarities)** test, generalized, against the real spool, and route it to
`reversibility-tx-reviewer` so the I6 close is *verified, not asserted* (ADR-0019 described this lock
but only the rename-claim is actually implemented — confirmed in `lucid_queue.py` `claim()`).

### D8 — Closed-loop drain trigger via the existing `lease.json` mirror

v1 keeps the polled best-effort drainer (`OnUnitInactiveSec` ~20s worst case) as the fail-open floor.
v2 adds an inotify watch on `$XDG_RUNTIME_DIR/nimbus-aurora/lease.json` (the off-lock mirror the
daemon **already** writes) so a drainer wakes on the lease-free transition instead of polling — a
consumer change only, no new daemon write path, the daemon's no-queue stance untouched. If the watch
breaks, the timer still fires.

### D9 — Finish the private path (close the silent-drop), and the private live beat

Wire the two dormant calls: `create_from_image.py`'s private branch calls
`lucid_priv_queue.hold()`; `lucid_web.py main()` starts the `run_in_session` private drainer thread.
A private deferral retries in-session on tmpfs and **burns on logout** — it never reaches the durable
`lucid-queue/` path (the two-module physical separation, no shared base-path constant, survives
verbatim). A private **live** beat uses the in-session ephemeral hold only and an **action-less,
transient** notice — never the save-as-proposal exit (offering to persist a private intent is the
exact boundary the private seal exists to hold):
> "Private — held in memory only. The graphics card is busy. This will paint on its own while you're
> logged in, then it's gone — never saved, never shown on the desktop. Log out and it's wiped."

A private beat that exhausts its in-session budget or ages past the freshness TTL **burns silently**:
D6's stale-consent re-confirm is **non-private only**; a private beat never re-confirms and never
produces a needs-review row (it has no durable surface to ask "still want this?" without breaching the
seal). This inherits the ADR-0019 §5 BURNED-SILENT terminal.

## Determinism invariants (the generalized library MUST hold)

| # | Invariant | ADR-0019 today | This ADR |
|---|---|---|---|
| I1 | Every binary input frozen to owned bytes at enqueue; no re-fetch at drain | satisfied | inherit |
| I2 | Every value input (seed/prompt/workflow/engine/quality/est) frozen at enqueue; drain resolves nothing from a mutable pointer | **not satisfied** | **D5 — build** |
| I3 | Model/consent gates run at enqueue on the frozen value; drain re-runs only resource + defensive red-line | satisfied (image) | **D6 — extend to prompt** |
| I4 | Stale-consent re-confirm defined deterministically (TTL → re-confirm or escalate) | undefined | **D6 — decide** |
| I5 | Single-flight claim atomic; double-execution impossible | satisfied | inherit |
| I6 | Idempotent at the compute-commit boundary (post-commit crash does not re-produce) | partial (lock promised, not implemented) | **D7 — close** |
| I7 | Crash recovery decides from file, never PID | satisfied | inherit |
| I8 | Drain order FIFO-by-`seq`, fail-closed against priority keys | satisfied | **D3 — inherit + cross-lane twin** |
| I9 | Decided state machine pure + clock-injectable + replayable | partial (clock read inline) | thread a clock seam |
| I10 | A live beat's narrative closure frozen to values AND a freshness predicate refuses replay against a moved spine | absent | **D4 — build (the hard one)** |

## Consequences

- The user's complaint is answered where it is real (the live-beat dead-end) and explicitly **not**
  "solved" where queueing would harm (inference/launch/voice) — those keep their better non-queue
  behavior, documented so the carve-out is not relitigated.
- The substrate gains no scheduler and no daemon wait-queue; the ADR-0001 / ADR-0010 boundaries hold.
- The reusable library makes a *third* artifact lane (if one is ever justified) a producer change,
  not a rebuild — but no third lane is wired now.
- New risk surface: the live-beat needs-review row crosses a live in-session intent into the durable
  human-review lane; the freshness predicate and the resume-once idempotency (I6/I10) are the load
  bearing new code and need the security/privacy + reversibility follow-on review.
- Kill metric: instrument resume-vs-expire for deferred live beats. If most expire unrendered (the
  user moved on), staleness was higher than assumed → fall back to the one-tap re-pick.

## Phased implementation (maps to the three asks)

Recommended order is **Phase 3 → Phase 1 → Phase 0-spike → Phase 2** (lowest-risk correctness win
first; the live-beat lane last, behind its spike and gate). The three asks all land; only the
sequencing is opinionated.

- **Phase 3 — the private completion (D9).** *Lowest risk, ship first.* Wire `lucid_priv_queue.hold()`
  into `create_from_image.py`'s private branches (`:296`, `:346`) + start the `run_in_session` drainer
  thread in `lucid_web.py main()` (before `serve_forever()` at `:1629`, beside the existing
  `_lease_reaper` thread); private live-beat action-less ephemeral notice. The modules are built +
  unit-tested; feasibility rated this genuinely two wiring calls. Closes the silent-drop of private
  deferrals today.
- **Phase 1 — the library (the actual generalization).** Extract `lucid_queue`/`lucid_drain`/state
  machine into a lane-parameterized library; fold in **D5 (freeze values)** — this is a Phase-1
  deliverable that Phase 2 *depends on*, not a parallel task; D7 (idempotent compute-commit, with the
  T-idem-4 test); D6 (gate-at-enqueue rule); I9 (clock seam, with a `next_state`-equivalent
  purity + clock-injection test inheriting ADR-0019 G3/G4); write the D3 cross-lane anti-scheduler
  test. create-from-image migrates onto it with byte-identical drain behavior (regression-pinned: the
  re-decided constants must equal the frozen values). No new headless lane.
- **Phase 0-spike (before Phase 2) — the D4 freshness predicate.** The single score-capping unknown
  (feasibility 7/10). Prove end-to-end: freeze a beat-intent record (`parent_id` + blake2b of the
  parent node's `out_frame` bytes via `ST.frame_abs(...)` + `prompt_final` + `rating` floor + `seed` +
  `anchor`), then on "Make it" re-hash the live parent and assert `context-moved` fires when the spine
  moved and a clean replay fires when it didn't. Owner: `design-technologist`.
- **Phase 2 — the live-beat lane (D4).** In-session bounded hold + new calm banner; save-as-
  needs-review on tab close with full frozen intent + parent-frame hash; freshness predicate +
  `context-moved` decline on "Make it"; fault-vs-busy split. **Banner-honesty caveat:** the in-session
  hold reuses `_ensure_lease`'s *bounded* retry (`ADMIT_RETRIES=6 × ADMIT_BACKOFF=2.0s ≈ 14s`,
  `lucid_web.py:160-161`) — a spawn-admission retry, not a hold-until-free loop. The calm banner
  ("painting this beat as soon as the graphics card frees") over-promises on a 14s budget; so for the
  live-beat lane, **D8's `lease.json` lease-free wake is a Phase-2 dependency** (it can stay v2 for
  create-from-image, where the 20s poll is fine). Closes the "live-dream path → queue" ask the safe
  (human-gated) way.

### GO/NO-GO gate (binding, mirrors the ADR-0019 precedent)

**Phase 2 (D4 live-beat) does NOT land until both follow-on reviews return GO:** the
security/privacy review of the live-intent → durable-needs-review crossing, and the
`reversibility-tx-reviewer` audit of the I6/I10 load-bearing new code (the freshness predicate +
resume-once idempotency). Phase 3 and Phase 1 are not gated on these (no new irreversible
human-review surface). This converts the named-but-ungated reviews into a binding condition.

## Open questions

- OQ1 — *Decided lean:* the live-beat needs-review row gets a **distinct, shorter TTL** than
  create-from-image's `DEFER_TTL` (6h) — a narrative beat rots faster than a standing artifact —
  confirmed empirically via the resume-vs-expire kill-metric. Exact value pending the first live data.
  (ai-product + interaction.)
- OQ2 — Should the in-session retry budget *adapt* to a user whose UE wallpaper sits permanently at
  the VRAM knife-edge? (personalization-loop.)
- OQ3 — Phase 1's library extraction: same-process import vs a tiny shared package under
  `apps/dreaming/lucid/` first, promoted later — sequencing only, not architecture.

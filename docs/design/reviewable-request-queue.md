# Design Brief — Reviewable Request Queue

Status: Decision-ready (mediator synthesis), **iterated round 2 to the 9.0 bar**. Pairs with draft
ADR-0019 (Status: Proposed). Mediator: design-discourse-mediator. Date: 2026-06-16.

## Iterate round 2 — changes (one line per gap, what it closed)
- **G9-private** — RATIFIED the human's binding override: private mode is now an EPHEMERAL
  IN-SESSION tmpfs queue (auto-retry, burned on logout, no review surface), NOT ineligible.
  `responsible-ai-privacy-skeptic` ruled GO-with-conditions; the airtight boundary (H1-H5) and
  GO/NO-GO conditions are embedded verbatim; design-researcher + content-voice-designer dissent
  preserved and marked overridden-with-conditions. Closed the values fork (was Open Q1).
- **G1** — the cap-lifter: lucid review items reach the warm bloom via a SEPARATE `review.json`
  sidecar `feed.rs` folds additively (own liveness gate, no collision with the needs-you plugin's
  `os.replace`). Made "reuse, coin nothing" TRUE. Feasibility 7→9.
- **G3** — drainer loop turned from slogan to testable spec: source-of-truth inversion via filename
  suffix, atomic `claim()` single-flight, `Tier::BestEffort` fail-open BY CONSTRUCTION, file-over-PID
  crash recovery, two-spool capability-table seal for the private path.
- **G5** — recovery-toast a11y + persistence-on-dismiss contract: persist-first/toast-second
  ordering invariant (timeout/dismiss = no-op on the row), WCAG 2.2 a11y slots, dismiss≠Cancel,
  no-countdown anti-dark-pattern, private zero-action carve-out. HARD GATE before Phase 2.
- **G2** — schema-2 `keyhole.json` literal BUILT + VERIFIED: `pending_requests = {"held":N,
  "needs_review":M}` (two counts → two tray lines), re-pinned round-trip test, 61 tests pass.
- **G4+G6** — retry as a pure function `next_state(attempts,last_error,age)` + backoff as a
  SEPARATE pure function (eligibility, never an ordering key); anti-scheduler invariant locked IN
  THE SCHEMA (`_FORBIDDEN_ORDER_KEYS`, strict-arrival FIFO, `SystemExit` on any priority key).
- **G7** — warm-state WCAG contrast gate: measured numbers against the warm-breath-peak on Hills +
  Flow; tray `#ff9957` warm-on-warm FAILS AA (3.07-3.92:1) → fallback ladder; web-panel `--inst-label`
  fix; `g7_contrast.py` CI artifact. HARD GATE before Phase 2.
- **idem (idempotent re-run)** — dedup keyed on the held row's stable `job_id` via an `O_EXCL` claim
  before `lease_spawn`, released in `finally`; crash recovery gated on claim liveness not the status
  string; a 5×-deferred job yields ONE clip. Clears the double-generate market risk.

---

## Problem
The "Create Video from Image" browser flow is fire-and-forget: right-click →
native-messaging host → detached `create_from_image.py` that tries to generate
immediately. When it cannot run it writes a terminal `skipped`/`blocked`/`failed`
tombstone + a toast, and the intent is lost — no retry, no held queue, no surface
a human can act on. The user: "it looks like we drop the request altogether. That
should go into the reviewable queue."

## What the code actually is today (verified, not assumed)
- `create_from_image.py` writes `skipped` and exits at the two deferral points
  (`:264` coordinator down, `:309` GPU busy / preempted) and `blocked` at the gates
  (`:228` consent declined, `:232` possible-minor, `:237` B2 can't-verify).
- `lucid_jobs.py` is a **tmpfs** board (`$XDG_RUNTIME_DIR/agentos/lucid-jobs/`),
  atomic-write + 24h/24-entry prune + a path-traversal `_valid_id` guard. States
  `queued→checking→generating→ready/skipped/blocked/failed`. **No durability, no
  retry edge, no `held`/`needs-review` state.**
- Private requests pass `job_id=None` (`:255`); every `_job()` call no-ops on a
  falsy id (`:202`). The private seal is **"the launcher never calls the board."**
- `lease.rs` exposes `AcquireResult::Queued` (`:125`) but the module header is
  explicit: **"There is no wait-queue yet — a losing acquirer is told `queued` and
  retries"** (`:29-32`). The lease tells you to come back; it does not hold intent.
- `feed.rs` precedence is `needs_you > snag > working > idle` (`:88-97`); the
  single warm bloom is `needs_you` (`:88-90`), gated on `gateway_alive` (`:83-86`).
- `keyhole.rs` serializes `Fleet{running,queued,snagged}` (`:84-88`) read-only;
  v2 write-actions are gated on a confirmed Hermes write-API (ADR-0012 §6).

## The agreements (the room converged here, by evidence)
1. **Reuse, don't coin.** Deferral rides `lease.Queued`; human-approval rides the
   existing `needs_you.json` → warm bloom; the count lives in `keyhole.fleet`.
   No new wallpaper float, no fifth ambient mood. (all nine designers)
2. **`skipped`/`blocked` are tombstone words that now lie.** A held request must
   read as *waiting / your move is one click*, never *over*. (content-voice-designer,
   art-director, brand-identity-designer)
3. **Possible-minor never enters the queue.** It terminates at `gate_seed` (`:231`),
   gets no review row, no warm bloom, no retry-implying chrome — terminal and calm,
   never red. The carve-out is a property of the state machine (no transition is
   *defined* out of REFUSED), not a policy someone remembers. (unanimous)
4. **Two kinds of "review" are two different things in two different owners.**
   Deferral (couldn't-run-now → auto-run on lease-free) is a *resource* concern that
   stays local and fail-open. Human-approval (B2 can't-verify, consent-borderline) is
   a *human-decision* concern that rides the `needs_you` channel. They share storage,
   never chrome, never the same ambient channel. (interaction-designer, design-researcher,
   brand-identity-designer, generative-artist, visual-systems-designer)
5. **The local queue holds intent; it never schedules.** Retry is triggered by a
   lease-free signal, not by a scheduler that orders/prioritizes across requests.
   That line is what keeps it a substrate and not a second orchestrator. (unanimous)
6. **The earcon budget is zero new sounds.** Needs-review folds into the existing
   `needs_you` chime via `state==2`; a stalled generation folds into `snag` via
   `state==4`; deferral/auto-run/expiry are silent. (sound-designer)

## The tensions and how they resolve

### T1 — Where the queue lives (Fork 1): A (Hermes kanban) vs B (local durable) vs C (hybrid)
- **Owner lane:** substrate scope / don't-reinvent — adjudicated by the mediator on
  the non-negotiables, with feasibility from design-technologist.
- **Resolution: B-for-deferral + thin reuse of `needs_you` for approval; A deferred.**
  fail-open is the tie-breaker (non-negotiable #5): a Hermes-owned queue cannot hold a
  local desktop intent while Hermes is down, which is the exact silent-drop being fixed.
  Option A-now also requires a **Hermes write-API that does not exist** — ADR-0012 already
  gates keyhole-v2 on it; building enqueue on an unconfirmed API is drift, not a decision.
  design-technologist confirmed v1 is a *promotion* of `lucid_jobs.py`, not a build.
- **The don't-reinvent guard (non-negotiable #3) survives because** the deferral buffer
  is exactly the userspace wait-queue `lease.rs` *deliberately declined* to be (`:29-32`).
  It is allowed **two exit transitions only**: `→ running` on lease-free, and
  `→ needs-review` on retry-exhaustion. The moment any transition needs to decide *which*
  request runs *first* (ordering/priority/dependency), it has become a scheduler — that
  belongs to Hermes; stop and escalate.

### T2 — Deferral vs approval as one surface or two (Fork 2)
- **Owner lane:** interaction-designer (control model).
- **Resolution: two terminal-distinct held states, shared storage, distinct chrome and
  distinct ambient channel.** `held(deferred)` is *weather* → keyhole `fleet.queued`,
  calm/idle, silent, **never touches warm**. `needs-review(human)` is a *consent prompt*
  → the one warm bloom via `needs_you.json`. Conflating them reproduces the exact
  nag-fatigue / silent-strand dark pattern the calm+honest non-negotiable forbids.
- **Tie-break applied:** honest mapping (non-negotiable #6). visual-systems-designer's
  invariant is load-bearing and adopted verbatim into the ADR: *only `needs-review`
  increments `needs_you.json` / drives warm; `held` never does.* This is the single most
  likely implementation mistake and it silently destroys the honest mapping.

### T3 — Private-mode coexistence (Fork 3): RESOLVED by the human's binding decision
- **Owner lane:** responsible-ai-privacy-skeptic (ratified GO-with-conditions; the values call was
  the human's), with the data seal owned by whoever owns `lucid_store.py`'s `is_private` gate.
- **Resolution (human override, round 2): private is an EPHEMERAL IN-SESSION tmpfs queue** — a
  tmpfs-only hold that auto-retries within the session and is **burned on logout**, with **NO
  persisted review surface**. This is MORE permissive than the round-1 recommendation
  (ineligible-for-defer) and widens the ADR-0016 seam. `design-researcher` and
  `content-voice-designer` dissented against exactly this; the human overrode them WITH the
  skeptic's constraints attached (dissent preserved below). The choice is MADE — not relitigated —
  and made airtight by treating the private drainer/store as a **physically separate subsystem**.
- **Airtight boundary (`responsible-ai-privacy-skeptic`, embedded verbatim):**
  - **H1 — never disk-backed.** No `~/.local/share/agentos/lucid-queue/` codepath in the private
    module; private lives ONLY under `$XDG_RUNTIME_DIR/agentos/lucid-priv-queue/` (0700, both dir
    and parent `chmod`'d, atomic temp+`os.replace`, `_valid_id` guard). NO shared base-path
    constant, NO `if private: base=X else=Y` inside one writer — two modules.
  - **H2 — no persisted ambient surface.** A private held item NEVER increments `needs_you.json`,
    NEVER appears in `keyhole.fleet.queued`/`snagged`, NEVER appears in `pending_requests`. Any of
    those four is a Blocker-class leak.
  - **H3 — no review surface.** Structurally ineligible for `needs-review`; a private yes/no is
    refused in-session via the existing synchronous consent dialog, never queued for later review.
    The one place the override does NOT widen.
  - **H4 — no earcon.** Never reaches `state==2`/`state==4`, so the chimes cannot fire (for free).
  - **H5 — burned on logout, completely** (see Condition 1, the load-bearing fix).
- **The chokepoint (two modules, no shared writer):** `lucid_jobs.enqueue(job_id)` (durable) hard-
  refuses `job_id is None` AND `private=True`, physically only knows `~/.local/share/...`;
  `lucid_priv_queue.hold(session)` (ephemeral) asserts `ST.is_private(session)` and raises
  otherwise, physically only knows the tmpfs queue dir. A future "unify" PR must delete a module
  and merge two refusal asserts — loud, reviewable, ADR-requiring. **`grep` acceptance test:** no
  source file references both `lucid-priv-queue` and `lucid-queue`.
- **The drainer is doubled:** the private drainer is a separate in-session loop INSIDE the lucid web
  service process (NOT a systemd timer — a timer outlives the session and would resurrect a private
  retry post-logout, an H5 violation), acquires the SAME `Tier::BestEffort` lease, and runs through
  the governed `create_from_image.run(arg, private=True)` path so B2 + EXIF-strip + the per-lease
  SIGKILL ComfyUI-history seal all still apply.
- **Surfacing ceiling:** at most an in-session-only ephemeral count, computed on-read from the tmpfs
  dir listing, served only on the loopback CSRF-gated `:8765` panel (the surface ADR-0016 already
  trusts). NEVER written to `agent.json`/`keyhole.json`/`pending_requests`/`needs_you.json`. If it
  can't be shown without writing a persisted sink, it isn't shown.
- **GO/NO-GO conditions** (`responsible-ai-privacy-skeptic`, each an acceptance test): see ADR-0019
  §5. The load-bearing one (**Condition 1, Blocker**): the existing ExecStop burn hook
  `_burn_private_on_stop()` (`lucid_web.py:681-693`) burns only the single hardcoded
  `SESSION="web"` (`:40,688`) — a queue holds N sessions, so on a CLEAN logout N−1 private held
  items' on-disk sealed anchor frames (`input/.lucid-priv-<session>/`) survive ExecStop until the
  next `reap_orphans()`. "Burned on logout" is FALSE as-written. Fix: iterate
  `ST.list_priv_queue() ∪ ST.list_private()`, `L.burn(s)` each, then clear the tmpfs queue dir.
  **No code lands on the private path until Conditions 1-3 pass.**
- **Audio is an ambient surface too** (sound-designer): because private never reaches `state==2`,
  the earcon never fires — for free. A written residual, not an accident.
- **Recorded dissent on T3:** see below — preserved, marked overridden-with-conditions.

## DECISION — the single recommended direction

A **thin local durable deferral buffer** (promote `lucid_jobs.py`), with human-approval
items routed through the **existing `needs_you` channel**, surfaced **read-only in the
keyhole tray** and **actionable in the lucid web panel**. Private requests get an **EPHEMERAL
IN-SESSION tmpfs queue** (human override, round 2) — a tmpfs-only hold that auto-retries within
the session and is burned on logout, with NO persisted review surface, ratified GO-with-conditions
by `responsible-ai-privacy-skeptic` and physically separated from the durable store (T3, ADR-0019
§5). Possible-minor stays a hard terminal block, never enqueued, never warm.

### Request state machine (deterministic safety gates; model proposes, code disposes)
```
requested
  ├─ possible-minor ───────────────▶ REFUSED  (terminal; no transition out; never queued;
  │                                            never warm; critical notify only)
  ├─ private & can't-run-now ──────▶ priv-held (EPHEMERAL tmpfs hold; in-session auto-retry;
  │                                    │          burned on logout; H1-H5: no review surface,
  │                                    │          no warm, no earcon, no persisted count)
  │                                    ├─(lease frees, same session)──▶ running (private)
  │                                    └─(TTL / max-attempts)─────────▶ BURNED-SILENT (no review row)
  ├─ runnable now ─────────────────▶ running ─▶ done | failed(terminal)
  ├─ GPU busy / ComfyUI cold /
  │  coordinator down (non-private) ▶ held:deferred ──(lease frees)──▶ running
  │                                    │  └─(retries exhausted / TTL)─▶ needs-review or expired
  │                                    └─ ambient: fleet.queued (calm), NEVER warm
  ├─ preempted mid-generation ─────▶ held:deferred  (NOT failed — intent intact, compute
  │                                                   reclaimed; re-acquire later)
  └─ B2 can't-verify / consent
     borderline (non-private) ──────▶ needs-review ─▶ approved→running | rejected | expired(TTL)
                                       └─ ambient: warm bloom via review.json sidecar (G1) → feed.rs
```
Edge ownership:
- `→ REFUSED` (possible-minor): deterministic code gate, terminal, no human edge.
- `→ priv-held` (private no-run): EPHEMERAL tmpfs subsystem, physically separate from the durable
  store; auto-retries in-session, burned on logout; reaches no persisted/ambient surface and no
  review channel (H2/H3). A private yes/no is refused in-session, not queued.
- `held:deferred → running` and `priv-held → running`: code disposes on a lease-free event (no
  second consent — the right-click was the consent). The private retry runs the governed
  `create_from_image(private=True)` path on the same `Tier::BestEffort` lease.
- `needs-review → approved/rejected`: **human disposes**; the model proposed nothing (B2's verdict
  is a deterministic flag). A private item has NO needs-review edge — the ephemeral branch returns
  before the fork.
- `held → expired`, `needs-review → expired`: TTL; ages out **visibly and honestly**.
  `priv-held → BURNED-SILENT`: TTL/max-attempts on a private item burns silently — no `expired`
  review row, no persisted artifact.
- `preempted → held:deferred`: the load-bearing reversibility edge (design-researcher).

### Labels (content-voice-designer; the rename is mandatory in the same PR)
- `skipped`(GPU busy) → **"Waiting for the graphics card"** / *"Held — it'll start on its
  own when the GPU is free."*
- `skipped`(ComfyUI cold) → **"Waiting to start"** — the `(requeue)` comment finally true.
- `skipped`(coordinator down) → **"Waiting for graphics turn-taking"** / *"...Live work is
  never interrupted."*
- B2 can't-verify → **"Needs your OK"** / *"...held for you to allow or cancel."*
- Verbs: **Retry** (re-defers honestly, never over-claims "Retrying…"), **Allow**/**Cancel**
  (never "Approve"), **Dismiss** (never "Delete" — nothing existed to delete).
- A *preempted* clip is **lost**; copy promises a fresh retry ("Held to try again"), never
  "Resuming" (the partial bytes are gone).
- **Private intake line (REWRITTEN — the old "runs now or not at all" copy is now FALSE and
  removed).** The human's override makes private auto-retry within the session, so intake must
  state the new capability AND its cost in one calm line:
  *"Private: held in RAM only, retries while you're logged in, gone the moment you log out — never
  saved, never shown on the desktop."* Shipping the old copy alongside an auto-retrying queue is a
  dishonest-intake Blocker (content-voice-designer's GO condition). The inherited ADR-0016 swap
  residual (tmpfs may swap to disk unless swap is encrypted) is carried forward verbatim — do NOT
  upgrade the claim to "never on disk."

### The UX surface (smallest that honors complete control + calm)
- **Keyhole tray = glance, read-only v1.** Add `pending_requests` next to
  `fleet.{running,queued,snagged}` — **schema-2 additive, BUILT + VERIFIED (G2, 61 tests pass)**.
  The field is **two counts**: `{"held":2,"needs_review":1}` → two tray lines, "2 held — GPU busy"
  (idle/calm), "1 needs your OK" (warm cohort, but warmth is `feed.rs`'s job — the keyhole only
  displays the count). Link-out to the web panel.
  **Approve/retry/dismiss buttons are NOT in the tray in v1** — that collides with the
  ADR-0012 §6 write-API gate. (visual-systems-designer, generative-artist, design-technologist)
- **Lucid web panel = act.** The held / needs-review list with the seed-frame thumbnail
  and Retry / Allow / Cancel / Dismiss — because review needs to *see the image*, and the
  panel already streams clips with CSRF tokens.
- **The recovery toast is the primary control for the common case** (interaction-designer):
  the swaync action button on the deferral toast (`[Run when free ✓] [Cancel]`) is where
  the drop is felt and where recovery lives, ≤2 steps. The board is the audit fallback.
- **Ambient:** deferral earns **no** ambient cue beyond the keyhole count (art-director's
  calm-budget non-negotiable: *deferred ≠ a notification; only "needs you" earns warmth*).
  The empty queue must return the field **byte-identical to idle** (generative-artist's
  fixed-`iTime` diff as an ADR acceptance test).

### Motion (motion-designer)
- The keyhole queued-count eases ~3–6s (over the 2s producer poll), gated on
  `model.reducedMotion` (instant under reduce-motion). Producer snaps the integer; the
  consumer eases it — never both (double-damping makes the count soupy).
- `held → running` that coincides with a VRAM yield is a **settle-in fade-up from black on
  flux restore**, never a crossfade that dies mid-kill (ADR-0004). One sentence in the ADR.

## Round-2 build specs (the iterate deliverables, decision-ready)

### G1 — the cap-lifter: lucid review-item → warm bloom (feasibility 7→9)
The warm bloom for a non-Hermes lucid review item is driven by a **SEPARATE lucid-owned sidecar**,
never a second writer to `needs_you.json`. This is what makes "reuse, coin nothing" TRUE by
construction.

- **File:** `~/.local/share/agentos/lucid-queue/review.json`, lucid is the SOLE writer, `feed.rs`
  is a read-only consumer:
  ```json
  {"schema":1,"pending_review":2,"updated_at":1781635530.88,
   "items":[{"id":"shot_a1b2c3d4","title":"Create from image","since":1781635400.0}]}
  ```
  `pending_review` counts `needs-review` items ONLY — **NOT `held:deferred`** (held is calm/idle and
  never warm; the field name enforces it). `updated_at` is the liveness heartbeat, rewritten EVERY
  drainer tick even when unchanged. Atomic temp+`os.replace` (`.review.*.tmp`, the `lucid_jobs.py:58-70`
  idiom). The Hermes plugin writes `~/.hermes/needs_you.json`; lucid writes `review.json` — **the two
  producers never name the same path**, so the "collision with the plugin's whole-set `os.replace`"
  cannot occur.
- **`feed.rs` additive fold (exact, deterministic):** add a reader mirroring `read_needs_you`
  (`feed.rs:146-152`) with its OWN liveness gate decoupled from `gateway_alive`:
  ```rust
  #[derive(Deserialize, Default)]
  struct LucidReviewFile { #[serde(default)] pending_review: u32, #[serde(default)] updated_at: f64 }

  pub(crate) fn read_lucid_review(path: &Path, now: f64) -> u32 {
      const STALE_SECS: f64 = 12.0; // > drainer tick (≤4s)
      fs::read_to_string(path).ok()
          .and_then(|s| serde_json::from_str::<LucidReviewFile>(&s).ok())
          .filter(|r| now - r.updated_at <= STALE_SECS) // OWN liveness gate
          .map(|r| r.pending_review).unwrap_or(0)
  }
  ```
  `derive_feed` gains one param and folds **at the count level, ramping ONCE on the sum**:
  ```rust
  let hermes_pending = if gateway_alive { needs_you } else { 0 };
  let pending = hermes_pending + lucid_review;  // two ORIGINS, one count, one warm scalar
  if pending > 0 { AgentFeed { state: 2, busy: 0.0, warm: ramp(pending, 0.75, 0.9), snag: 0.0 } }
  ```
  Folding at the count (not `max(hermes_warm, lucid_warm)`) keeps `warm ∈ [0.75, 0.9]` regardless of
  the split and keeps the 11 existing `derive_feed` tests passing with `lucid_review=0`.
- **No-double-count = disjoint-set summation:** a lucid video-review and a Hermes command-approval
  are disjoint intent sets owned by exactly one origin for life; summed once. v1 does NOT mirror
  lucid items into `needs_you.json` (Phase 3, gated on the Hermes write-API, ADR-0012 §6). The
  drainer recomputes `pending_review` from the authoritative spool every tick (never `+=`/`-=` a
  cached scalar).
- **Own liveness, NOT `gateway_alive`:** lucid review items must survive a Hermes outage (fail-open,
  §1). Lucid alive + Hermes dead → lucid bloom still shows; lucid dead/logged-out → its bloom
  retracts within 12s even though its file lingers. New tests: lucid-only bloom, lucid+Hermes
  additive (sum-ramps-once, warm ≤0.9), stale-lucid-suppressed, lucid-survives-dead-gateway,
  empty→byte-identical-idle.
- *Open risk:* `STALE_SECS=12` assumes the drainer tick is ≤4s — re-tune to >3× the real cadence
  before pinning. The keyhole `pending_requests` (G2) shares this `review.json` source: re-pin the
  keyhole round-trip against `review.json` schema:1 so both consumers read the same authoritative
  file and cannot diverge.

### G3 — the drainer loop (slogan → testable spec)
`spikes/dreaming/lucid/lucid_drain.py` (new) + `lucid_jobs.py` promotion + `dist/lucid-drain.{service,timer}`.

- **Source-of-truth inversion:** the spool FILE is authoritative, the detached PID is not. State is
  encoded in the **filename suffix** (`.held.json` / `.running.json`), so the atomic `os.rename` in
  `claim()` IS the `held→running` transition (single-flight without a DB; `read_held()` globs
  `*.held.json` so a running record is invisible to the scan). The launcher inverts to
  write-then-exit: `create_from_image.py:262-268`/`:307-312` write a `held` record instead of
  `skipped`, snapshotting the already-sanitized PNG (`:274`) into the spool BEFORE the `:331-336`
  finally unlinks it — drain-time re-fetch of a remote URL is forbidden.
- **Two spools, one loop body** (binding decision): `DURABLE = ~/.local/share/agentos/lucid-queue/`
  (survives logout) and `EPHEMERAL = $XDG_RUNTIME_DIR/agentos/lucid-queue-priv/` (tmpfs 0700, burned
  on logout). The private seal is a per-spool **capability table** enforced by construction: the
  ephemeral branch of `expire()` `return`s BEFORE the needs-review fork, and `run_one` never calls a
  hub/keyhole/web producer for it. There is no `if private` a refactor can invert — the leak path
  does not exist (same way `Tier::BestEffort` makes fail-open structural).
- **The loop (one-shot per timer fire; the timer is the clock):**
  ```python
  def main():
      lock = open(LOCKFILE, "w")
      try: fcntl.flock(lock, LOCK_EX | LOCK_NB)
      except BlockingIOError: return 0          # another fire is mid-drain — never two at once
      recover_crashed()                          # running orphans → held BEFORE we drain
      for spool in (DURABLE_SPOOL, EPHEMERAL_SPOOL):
          for rec in sorted(read_held(spool), key=lambda r: r["created"]):  # arrival FIFO (G6)
              now = time.time()
              if rec["next_retry_after"] > now: continue   # backoff floor — eligibility, not order
              if rec["attempts"] >= MAX_ATTEMPTS: expire(spool, rec); continue
              claimed = claim(spool, rec["id"])  # atomic rename held→running
              if claimed is None: continue       # lost the race
              run_one(spool, claimed); break      # one job per fire; the timer re-fires for the rest
      return 0
  ```
  `claim()` reads `*.held.json`, writes `*.running.json` atomically, then unlinks the held marker;
  on `FileNotFoundError` (another drainer won) it rolls back its stray running file and returns
  `None`.
- **Crash recovery decides from the FILE, not the PID:** with the flock held, any `*.running.json`
  present at fire start is provably an orphan → `held`, `attempts++`, cause `preempted`,
  `owner_pid=None`. We never `kill -0 owner_pid` (a recycled PID could lie; a live PID under a
  different drainer can't exist under the flock). `owner_pid` is advisory only.
- **`Tier::BestEffort` is the load-bearing 1-line change:** `lucid_linear.py:66` hardcodes `"batch"`;
  the drainer path MUST pass `"best-effort"` so `arbitrate()` (`coord.rs:129-135`) structurally
  Queues it behind ANY holder and lets `Tier::Interactive` preempt it (`lease.rs:583-592`). Fail-open
  BY CONSTRUCTION, not by measurement. Recommend a `lease_spawn(tier=...)` param over an env knob (an
  env knob could silently downgrade the interactive Lucid loop). Without it the drainer acquires
  `batch` and could contend with overnight Hermes batch.
- **G3 produces the `needs-review` spool record + the separate `review.json` sidecar (G1's lane); it
  MUST NOT write `needs_you.json` directly** (single-writer plugin race). The `feed.rs` fold is G1.
- **Systemd `--user` TIMER, not a daemon:** `dist/lucid-drain.{service,timer}`, `Type=oneshot`,
  `OnUnitInactiveSec=20s`, `Persistent=false` (a private fire missed while logged out is never
  replayed — part of the seal). Honest scoping: this is a **polling** drainer (~20s worst-case
  latency); the lease-free EVENT (closed loop) is G10/Phase-2, substrate-blocked.

### G4 + G6 — the two deterministic gates (root-agnostic: one function across BOTH spools)
**G6 — anti-scheduler invariant, locked in the schema (do first):** the promoted job record carries
`seq` (a persisted monotonic arrival ordinal — the ONLY drain-order key) and `created` (wall-clock,
DISPLAY ONLY, never a sort key — clock skew makes it non-monotonic). **No `priority`/`weight`/`rank`/
`urgency`/`boost`/`class`/`tier` field on the record** (lease `Tier` lives on the lease CALL, never
the record).
```python
_FORBIDDEN_ORDER_KEYS = ("priority","weight","rank","urgency","boost","class")
def drain_order(jobs):
    eligible = [j for j in jobs if j["state"] == "held"]
    _assert_no_priority(eligible)              # raises SystemExit on any forbidden key — fail-closed
    return sorted(eligible, key=lambda j: j["seq"])   # ASC = strict arrival FIFO
```
`_assert_no_priority` raises `SystemExit("ANTI-SCHEDULER INVARIANT VIOLATED…")` — a HALT, not a log
— the moment a record carries a forbidden key. `seq` allocated from a persisted `.seq` counter via
atomic-rename. **Tests:** two held drain in arrival order; `created` is not the drain key (clock-skew
defense); a `priority` field halts; `next_retry_after` is eligibility, not order.

**G4 — retry policy as a pure function (`derive_feed` treatment, no clock/fs/model):**
```python
def next_state(attempts, last_error, age_s):
    """PURE. Precedence: expired > needs-review(human cause) > needs-review(retries exhausted) > held."""
    is_human = last_error in HUMAN_ERRORS            # {"consent-borderline","b2-cant-verify"}
    ttl = REVIEW_TTL_S if is_human else DEFER_TTL_S
    if age_s >= ttl: return "expired"
    if is_human: return "needs-review"
    if attempts >= MAX_ATTEMPTS: return "needs-review"   # exhausted resource → human, NEVER dropped
    return "held"

def retry_backoff_s(attempts):
    """PURE. Eligibility only — NEVER an ordering key (G6)."""
    return min(BACKOFF_BASE_S * (2 ** attempts), BACKOFF_CAP_S)
```
Backoff is a SEPARATE function feeding ONLY the eligibility filter (`next_retry_after <= now`), never
the sort key — this orthogonality prevents a backoff timer becoming a covert priority (pinned by a
dedicated test). Constants (`MAX_ATTEMPTS=5`, `DEFER_TTL_S=6h`, `REVIEW_TTL_S=24h`, backoff
30s..1800s) are tunable knobs, not calibrated — *open risk:* a Wan-14B dream holds the lease ~1hr, so
derive `DEFER_TTL_S` from the lease TTL (≥ a few lease cycles) rather than a guessed 6h, or a
legitimately-waiting deferral expires before the GPU frees.

**Binding-decision airtightness:** the private tmpfs spool and the durable spool call the IDENTICAL
`next_state`/`drain_order`/anti-scheduler — there is no second, looser private drainer. Privacy forks
ONLY at (a) which directory and (b) a `not job["private"]` warm-gate so a private `needs-review`
never reaches the `review.json` sidecar / earcon layer (ADR-0016's "never `state==2`" preserved for
free). *Open risk:* the two roots drain INDEPENDENTLY (separate `drain_order` calls); never merge
them into one sorted list (the tmpfs `.seq` resets to 0 each session, so a merged list would mis-order
across roots).

### Idempotent re-run (idem — market table-stake, not polish)
A retry must yield exactly ONE artifact. The two "obvious" dedup keys are non-functional as written:
`job_id`/`session` is **re-minted per process** (`create_from_image.py:254` `secrets.token_hex(4)`),
and `seed+prompt+seed-int` is **non-reconstructible** (the seed is `random.randint` at generate-time,
never persisted — `lucid_linear.py:226-227` stores `seed:None`).

- **Dedup key = the held row's stable `job_id`, threaded INTO re-run** (never re-minted at entry). Add
  an optional `job_id` param to `create_from_image.run()`; the drainer/Retry call
  `run(arg, private=False, job_id=row.job_id)` and use it as both `session` and the claim key.
- **Exactly-once = an `O_EXCL` atomic claim**, taken before the compute-commit boundary
  (`lucid_linear.generate_video` before `lease_spawn`, `:173`) and released in a `finally` — NOT a
  status flag (`lucid_jobs.update:88-89` silently resurrects a deleted row, so status-as-lock is
  unsafe):
  ```python
  def claim(job_id, owner):
      fd = os.open(_claim_path(job_id), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)  # FileExistsError if held
      # ... write {owner,pid,at}, fsync. On FileExistsError: steal IFF _claim_is_stale (dead pid OR > 2h TTL).
  ```
  `_claim_is_stale` checks `os.kill(pid, 0)` liveness AND a TTL > the 30-min generation ceiling; a
  live pid holding a fresh claim is NEVER stolen.
- **Crash recovery is gated on claim LIVENESS, not the status string** — `generating → held` only when
  `not claim_alive(job_id)`. This closes race R3 (a recovered row double-generating against a
  still-alive ComfyUI). The drainer and the manual web-panel Retry call the IDENTICAL
  `run(..., job_id=row.job_id)` so they contend on the same claim — no side path bypasses it.
- **Private:** `claim(None, …)` hard-refuses (private never persists a durable claim); private claims
  live in tmpfs under the private-queue root via a DIFFERENT code path, and `burn(session)` removes
  the private claim too.
- *Open risk (Finding D):* `lucid_linear._newest_clip:196-208` selects by mtime over the SHARED
  `output/lucid/` dir — two concurrent DIFFERENT-job generations cross-contaminate. Single-flight
  per `job_id` prevents two runs of the SAME job, but the drainer must NOT run two different jobs
  concurrently into the shared dir without per-job output isolation (`output/lucid/<job_id>/`). Hand
  to `determinism-safety-reviewer` for the parallelism bound. Tests T-idem-1/2/4 are the gate.

### G5 — recovery-toast a11y + persistence-on-dismiss (HARD GATE before Phase 2)
The recovery toast is the primary control for the common case (GPU-busy deferral). Today `notify()`
(`create_from_image.py:56-61`) is a bare fire-and-forget `notify-send` and the launcher EXITS before
any click — so the action cannot be handled by the launcher.

- **Ordering invariant (the spine, non-negotiable):** at each deferral site, **persist the held row
  FIRST** (durable spool, fsync), **then** show the toast. The toast is a pure accelerator over an
  already-safe state. Consequence: "toast expired / dismissed / never seen" are all **no-ops on the
  row** — the drainer auto-runs it regardless. If the `enqueue` raises, fall back to a no-action
  `critical` toast telling the user to re-trigger; NEVER show an action button backed by a row that
  does not exist (fail-open honesty, mirroring `feed.rs:83-86`).
- **Action mechanism (decoupled from the dead launcher):** a tiny persistent broker folded into the
  **G3 drainer service** (NOT a second daemon) subscribes to `ActionInvoked(id, action_key)`. Action
  keys carry the job_id (`run:<job_id>` / `cancel:<job_id>`) so a restarted broker resolves the job
  from the key alone. Re-deferral of the same job uses a stable
  `x-canonical-private-synchronous:lucid-<job_id>` hint so the toast REPLACES rather than stacks.
- **Persistence-on-dismiss state table (the airtight part):** `Run when free` → stays `held`,
  `intent_confirmed=true`; `Cancel` → `held→expired` (visible retraction, count decrements); toast
  timeout / swipe / Esc / never-seen / swaync-down → **NO-OP on row** (drainer still auto-runs).
  Dismiss ≠ Cancel: the SR label for close is "Dismiss this notice (the request stays held)". The
  single forbidden cell — any path where the row is dropped because of a toast lifecycle event —
  does not exist. **No visible countdown** (a deadline that does not exist = a dark pattern; SC 2.2.1
  satisfied by the backstop, not a timer).
- **WCAG 2.2 a11y slots (ui-accessibility-reviewer signs T8-T13):** keyboard-operable actions (SC
  2.1.1, mouse-unplugged test); focus order `Run when free → Cancel` with `Run` initial focus (SC
  2.4.3); visible non-color focus ring ≥3:1 not clipped (SC 2.4.7/2.4.11); self-sufficient SR labels
  ("Run this held video when the graphics card is free" / "Cancel this held video request") (SC
  4.1.2); polite-not-assertive announce (a deferral is calm weather); ≥24×24px targets (SC 2.5.8);
  `prefers-reduced-motion` honored (SC 2.3.3). The toast surface is an opaque swaync card, so its
  contrast is **style-independent** (resolves the per-wallpaper legibility risk for the toast); the
  held toast never borrows the warm palette (warm-hue invariant).
- **Private carve-out (binding decision):** a private deferral toast carries **ZERO action buttons**
  and no link-out (there is no persisted review surface to point at) — it is informational only:
  "Private: held in memory, retrying on its own this session; gone when you log out." A private hold
  goes to `enqueue_ephemeral()` into the separate tmpfs root, never the durable spool; it produces NO
  `needs_you.json` increment, NO `state==2`, NO chime, and is NOT counted in public
  `keyhole.fleet.queued`/`pending_requests`. The more-permissive in-session auto-retry buys
  capability WITHOUT buying any observable surface.

### G7 — warm-state WCAG contrast acceptance gate (HARD GATE before Phase 2)
Measured against the warm-bloomed field, NOT the calm idle baseline. The warm bloom rides the
brightest crests and breathes ~10s, so the "act now" frame is the worst-contrast frame.

- **Split by surface exposure (verified against code):** the lucid web panel does NOT composite the
  live wallpaper (its `backdrop-filter` blurs its OWN opaque navy body gradient,
  `theme.css`/`lucid_web.py:202-248`), so Allow (#7aa2ff/6.06:1), Cancel (#ec7676/5.32:1), and the
  body label (#e6e9f0/12.4:1) already pass — only `--inst-label` (#7a8090, **3.82:1 on the 46%-glass
  card**) fails (the `instrument-tokens.md:26` "clears 4.5:1 on the glass" claim is false against the
  glass card). The keyhole tray genuinely floats over the warm-bloomed wallpaper: tray warm token
  `#ff9957` over the bloom is **3.07:1 (Flow) to 3.92:1 (Hills)** — FAILS AA 4.5:1 text and at the
  edge of the 3:1 floor.
- **Gate conditions:** render `uAgentState=2, uAgentWarm=0.9, uDark=1`; sample at the warm BREATH
  PEAK (`sin(t*0.62)=+1`) and take the min ratio across the cycle; at 20%/low brightness; against the
  **worst (highest-luminance) pixel in each element's bbox**. Thresholds: 4.5:1 for the bold-but-not-
  large tray label and button text (SC 1.4.3), 3:1 for glyph/halo/border/focus (SC 1.4.11); thumbnail
  image exempt but its caption/selection-border/adjacent controls are not.
- **Fallback ladder (visual-systems-designer owns values; this gate owns ordering, all revertible):**
  F1 — lighten web `--inst-label` to clear 4.5:1 on the glass card (or demote info usages to
  `--inst-muted`, already 4.72:1). F2 (the tray Blocker) — **de-couple warm from the text FILL**:
  keep the label+glyph at `skin.text` (#e6e9f0, 5.6-6.8:1 even over the warm patch) and express "needs
  you" via the aurora ring/halo warmth + bold weight, NOT the letterforms; OR guarantee an opaque
  instrument-register chip behind tray text so it never composites against the wallpaper. F3 — a
  shader-side bloom clamp under a registered bbox, escalated to `determinism-safety-reviewer` (must
  not re-introduce a strobe), last resort only.
- **Reduced-transparency** (`prefers-reduced-transparency:reduce` → opaque glass) composites every
  token to 5.3-13.5:1 — a guaranteed-AA escape hatch, tested as its own matrix row, and must also
  force the opaque tray chip. Ship `g7_contrast.py` (renders the two `needs_you` frames at gate
  conditions, asserts thresholds) paired with the idle `iTime`-diff==0 test — both ends of the warm
  channel proven (byte-identical at idle AND AA-legible at the warm peak).
- *Open risk:* only Hills + Flow measured (the brief scope); styles 2-7 (uStyle) under the warm bloom
  are an unmeasured contrast surface if ever shippable. Tray host ambiguity: in a panel/desktop-widget
  placement the compact glyph CAN composite against the wallpaper; if product guarantees an always-
  opaque panel host, F2.2 is satisfied for free and the Blocker downgrades to verification-only —
  confirm the deployment placement.

## Accepted tradeoffs (what we knowingly give up)
- **Private mode widens the ADR-0016 seam (the human's knowing choice).** Private creations now DO
  auto-retry within the session — but at the cost of a real in-session observable: a `priv-held` row
  + claim exists in `$XDG_RUNTIME_DIR` (already 0700) for the session lifetime, so an attacker with
  live read access can observe that *a* private hold exists. This does NOT widen the durable surface
  (nothing survives logout, nothing on the board, nothing in the warm signal), but it is a residual
  "run-now-or-burn" did not have. Recorded honestly per the AIRTIGHT mandate; the
  design-researcher/content-voice-designer dissent stands.
- **Hermes enqueue (Option A) is deferred to Phase 3**, behind the write-API it actually
  needs. v1 ships without it; the desktop's single approval surface for *Hermes* approvals
  stays `needs_you`, and lucid review items mirror into it only when the write-API lands.
- **The deferral runner inverts source-of-truth** (the spool FILE, not the detached process, is
  authoritative; state in the filename suffix, atomic-rename = the transition). This is the real
  engineering work and the thing that fixes preemption-loss; it is more than a "promotion."
- **Idempotent re-run is mandatory before any "retriable" claim** — dedup keyed on the held row's
  stable `job_id` via an `O_EXCL` claim before `lease_spawn`, recovery gated on claim liveness not
  the status string. A 5×-deferred job yields ONE clip. The durable store must adopt fsync
  (`lucid_jobs._write:58-70` currently omits it — a torn-backup hazard once disk-backed).
- **A drainer re-fetch can find the URL gone.** Mitigation: snapshot the sanitized PNG at intake
  (from `_clean_png` at `:274`, before the `:331-336` unlink) and retry from stored bytes;
  re-fetch of a remote URL at drain time is forbidden. Hard v1 requirement.
- **One warm signal cannot distinguish "a video needs review" from "a Hermes approval."**
  Accepted: the wallpaper is mood (4 scalars), the keyhole is the legible breakdown (two counts).

## Recorded dissent (never erased)
- **design-researcher dissents** from any "ephemeral in-session private queue" (Fork-3's first
  option), on evidence: ADR-0016's documented residual seams + the incognito precedent. Records
  that choosing it *knowingly widens a private-mode seam*. **OVERRIDDEN by the human's binding
  decision** — but `responsible-ai-privacy-skeptic` adopted design-researcher's
  **physical-separation requirement** as a GO condition (two modules, no shared path constant,
  Condition 2). The dissent is NOT erased; this is a knowing seam-widening ratified with conditions.
- **motion-designer dissents** from any v1 that gives held requests a **new, separate UI
  affordance with its own animation**. Held = a queued thing; the keyhole already renders
  queued things. A dedicated pane is justified *only* for needs-review items.
- **content-voice-designer dissents** (conditional) against an in-session private hold even if
  chosen: an in-session queue is a surface, and a surface is the leak the no-board doctrine exists
  to prevent. **OVERRIDDEN by the human's binding decision** — but `responsible-ai-privacy-skeptic`
  adopted content-voice-designer's **intake-honesty condition** as a GO condition (the old "runs now
  or not at all" line removed; intake states auto-retry AND ephemerality, Condition 5). The dissent
  is NOT erased; the only permitted surface is the loopback-only, RAM-derived, request-time count on
  the already-trusted `:8765` panel.
- **interaction-designer anticipates ux-reviewer dissent** that a single unified "Pending"
  list is simpler than two held-states. The mediator holds with interaction-designer:
  simplicity-of-list costs honesty-of-signal, and honesty is the non-negotiable.

## Smallest shippable v1 vs phases
- **v1 (all in `spikes/`, no Rust, fully revertible via `dist/{apply,restore}.sh`):**
  promote `lucid_jobs.py` to a durable store under `~/.local/share/agentos/lucid-queue/` with states
  `held`/`needs-review`/`expired` + `attempts`/`next_retry_after`/`seq` + a snapshotted-PNG path +
  the pure `next_state`/`drain_order`/`retry_backoff_s` gates (G4/G6) and the `O_EXCL` claim (idem);
  the durable `enqueue(job_id)` chokepoint refusing `None` AND `private=True`; the SEPARATE
  `lucid_priv_queue.hold(session)` ephemeral tmpfs lane (`$XDG_RUNTIME_DIR/agentos/lucid-priv-queue/`)
  with its own in-session drainer (NOT a timer); launcher writes `held` instead of `skipped` at
  `:264`/`:309` (and `lucid_linear.py:176`), fsync the row BEFORE the recovery toast (G5 ordering);
  the `--user`-timer durable **drainer** (`lucid_drain.py`, single-flight flock, `Tier::BestEffort`,
  backoff, max-attempts → `expired`, file-over-PID crash recovery); the label rename; the REWRITTEN
  private intake line. **Private-path code is gated on Conditions 1-3 (§5) — including the ExecStop
  multi-session burn fix — landing first.**
- **Phase 2:** keyhole `pending_requests` read-only field (G2, two counts — BUILT) + link-out;
  web-panel Held strip via existing `J.recent()`; the warm-bloom bridge via G1's `review.json`
  sidecar (NOT a second `needs_you.json` writer). **Hard gates G5 (recovery-toast a11y/persistence)
  and G7 (warm-state WCAG contrast on Hills + Flow) must pass before this ships.**
- **Phase 3 (gated on a confirmed Hermes write-API, ADR-0012):** mirror needs-review items
  into Hermes kanban; tray approve/retry/dismiss; Option A becomes feasible.
- **Phase 4 (reserve):** when computer-use lands, the `acting` ambient state and an
  in-flight veto attach here (consult design-technologist + wayland-computeruse-reviewer —
  note `state 3 'acting'` is defined but never emitted today; that is an open dependency,
  not a resolved design point). Also reserved: **G10** — the closed-loop lease-free EVENT
  (substrate-blocked on the daemon push) that replaces the polling drainer's ~20s latency.

## Open questions for the human (framed; not open-ended)
- **Private-mode handling — RESOLVED.** The human chose the ephemeral in-session tmpfs queue;
  `responsible-ai-privacy-skeptic` ratified GO-with-conditions (ADR-0019 §5). The dissent stands and
  is recorded. No longer open — the load-bearing prerequisite is Condition 1 (the ExecStop
  multi-session burn fix) before any private-path code lands.
- **Re-confirm on stale auto-run?** Still open (ADR-0019 Open Q2): re-confirm a deferred *creation*
  past a TTL vs run silently. For a PRIVATE item the artifact is RAM-only and burned at logout, so
  the stale-surprise cost is bounded to the session; a private retry past TTL/max-attempts burns
  silently (no review row). Owner: interaction-designer.
- **Phase-3 Hermes mirror** waits on a confirmed Hermes write-API (ADR-0012 §6). Recommend not
  building on an unconfirmed API — hold Phase 3.

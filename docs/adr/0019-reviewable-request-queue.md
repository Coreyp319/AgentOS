# ADR-0019: Reviewable Request Queue for Local Creation Intents

Status: **Proposed** (draft — model proposes, code disposes; the human disposes on this)
Date: 2026-06-16
Deciders: design-discourse-mediator (synthesis), pending human + responsible-ai-privacy-skeptic
Relates to: ADR-0001 (substrate not orchestrator), ADR-0003 (fail-open supervised),
ADR-0004 (graphics yield kill/relaunch), ADR-0005 (apply/rollback), ADR-0010 (VRAM
coordinator / `lease.Queued`), ADR-0012 (keyhole legibility instrument), ADR-0016 (lucid
private ephemeral mode), ADR-0017 (B2 seed-likeness guard). Supersedes nothing.

## Context
The "Create Video from Image" browser flow is fire-and-forget and synchronous. When it
cannot run, `create_from_image.py` writes a terminal `skipped`/`blocked`/`failed` tombstone
+ a toast and exits — the intent is lost. There is no retry, no held queue, no human-review
surface. The user named the failure: "it looks like we drop the request altogether. That
should go into the reviewable queue."

Verified current behavior (code trace, not ADR):
- Deferral points write `skipped`+exit: `create_from_image.py:264` (coordinator down),
  `:309` (GPU busy / preempted), `lucid_linear.py:176` (ComfyUI cold — comment says
  "(requeue)"). Approval gates write `blocked`: `:228` consent declined, `:232`
  possible-minor, `:237` B2 can't-verify.
- `lucid_jobs.py` is a tmpfs board (atomic write, 24h/24-entry prune, `_valid_id`
  path-traversal guard); no durability, no retry, no `held`/`needs-review` state.
- Private requests carry `job_id=None` (`:255`); `_job()` no-ops on falsy id (`:202`) — the
  private seal is "the launcher never calls the board."
- `lease.rs` exposes `AcquireResult::Queued` (`:125`) but **declines a wait-queue** by
  design (`:29-32`): a loser is told `queued` and retries.
- `feed.rs` precedence `needs_you > snag > working > idle` (`:88-97`); the single warm bloom
  is `needs_you`, gated on `gateway_alive` (`:83-90`).
- `keyhole.rs` serializes `Fleet{running,queued,snagged}` read-only (`:84-88`); v2 writes are
  gated on a confirmed Hermes write-API (ADR-0012 §6).

## Decision
Promote `lucid_jobs.py` from a tmpfs tombstone board to a **thin local durable deferral
buffer**, route human-approval items through the **existing `needs_you` channel**, surface
the queue **read-only in the keyhole tray** and **actionable in the lucid web panel**, and
make private requests **ineligible for deferral and review** (run-now-or-burn). Possible-minor
stays a hard terminal block that is never enqueued and never warm.

### 1. Where the queue lives
Local durable buffer (Option B) for deferral; reuse `needs_you.json` for approval; Hermes
enqueue (Option A) deferred to a later phase. **fail-open is the tie-breaker** (ADR-0003): a
Hermes-owned queue cannot hold a local intent while Hermes is down — the exact silent-drop
being fixed. Option A-now also requires a Hermes write-API that does not exist (ADR-0012 §6).

**Don't-reinvent guard (ADR-0001):** the deferral buffer is the userspace wait-queue
`lease.rs` deliberately declined to be. It has **exactly two exit transitions**:
`held → running` (on a lease-free event) and `held → needs-review/expired` (on retry-exhaustion
/ TTL). It MUST NOT order, prioritize, or express dependencies across requests. Any transition
that decides *which* request runs *first* makes it a scheduler — that is Hermes' job; stop and
escalate to the human.

### 2. State machine (deterministic safety gates)
```
requested
  ├─ possible-minor ──────────────▶ REFUSED  (terminal; NO transition defined out; never queued,
  │                                            never review, never warm; critical notify only)
  ├─ private & can't-run-now ─────▶ priv-held (EPHEMERAL tmpfs hold; in-session auto-retry;
  │                                   │          burned on logout; NO review surface, NO warm,
  │                                   │          NO earcon, NO persisted count — H1-H5, §5)
  │                                   ├─(lease frees, same session)──▶ running (private)
  │                                   └─(TTL / max-attempts)─────────▶ BURNED-SILENT (no review row)
  ├─ runnable now ────────────────▶ running ─▶ done | failed(terminal)
  ├─ deferral cause (non-private) ─▶ held:deferred ──(lease frees)──▶ running
  │                                   └─(retries exhausted / TTL)──▶ needs-review | expired
  ├─ preempted mid-generation ────▶ held:deferred  (NOT failed — intent intact)
  └─ B2 can't-verify / consent
     borderline (non-private) ─────▶ needs-review ─▶ approved→running | rejected | expired
```
Edge ownership:
- `→ REFUSED` (possible-minor): deterministic code gate, terminal, **no human edge in or out**.
- `→ priv-held` (private no-run): EPHEMERAL tmpfs subsystem, physically separate from the durable
  store (§5 chokepoint); auto-retries in-session, burned on logout; reaches no persisted/ambient
  surface and no review channel (H2/H3). A private item needing a yes/no is refused in-session, not
  queued.
- `held:deferred → running` and `priv-held → running`: code disposes on a lease event (the
  right-click was the consent; no second consent). The private retry runs through the governed
  `create_from_image(private=True)` path on the same `Tier::BestEffort` lease.
- `needs-review → approved/rejected`: **human disposes**; the model proposed nothing. A private item
  has NO needs-review edge (H3) — the ephemeral branch returns before the fork.
- `held/needs-review → expired`: TTL; ages out visibly (the retraction motion), never rots.
  `priv-held → BURNED-SILENT`: TTL/max-attempts on a private item burns silently — no `expired`
  review row, no persisted artifact (a forgotten private retry dies quietly inside the session).
- `preempted → held:deferred` (not `failed`): the load-bearing reversibility edge.

### 3. The warm-hue invariant (load-bearing; do not violate)
> Only `needs-review` increments `needs_you.json` / drives the wallpaper warm bloom.
> `held(deferred)` is visually `--st-idle` (calm), surfaces only as the keyhole
> `fleet.queued` / `pending_requests` count, and NEVER touches the warm signal.

This is the single most likely implementation mistake and it silently destroys the honest
mapping (the warm hue spent on "the GPU is busy" — a non-event). The `needs_you` source MUST
stay gated on `gateway_alive` (`feed.rs:83-86`) so a dead coordinator's stale review can't
keep shouting "needs you."

### 4. Possible-minor carve-out (non-negotiable)
The possible-minor refusal at `gate_seed` (`create_from_image.py:231-236`) is terminal and
**cannot acquire a review affordance at the component level** — no "Allow", no "Retry", no
"Review" button may render on it, and not merely disabled (a disabled "Allow" still implies
allowing is conceivable). It is logged for audit, never enqueued, never tokenized, never warm.
The refusal string stays exactly as written.

### 5. Private mode (ADR-0016) — EPHEMERAL IN-SESSION tmpfs queue (human override, ratified with conditions)

**Binding human decision (override of the round-1 recommendation).** The human chose an
**ephemeral in-session tmpfs queue** for private creation requests, NOT the round-1
"ineligible / run-now-or-burn." Private requests get a **tmpfs-only hold that CAN auto-retry
within the session and is BURNED on logout**, with **NO persisted review surface**. This is
MORE permissive than the round-1 brief and widens the ADR-0016 private seam. `design-researcher`
and `content-voice-designer` dissented against exactly this (dissent recorded below, never
erased); the human overrode them WITH `responsible-ai-privacy-skeptic`'s constraints attached.
The choice is MADE; the constraints below make it airtight.

**Privacy ruling (`responsible-ai-privacy-skeptic`): GO — conditional ratification.** The
ephemeral in-session tmpfs private queue is privacy-acceptable IF the private store/drainer is a
**physically separate subsystem** from the durable one. The decision rests on one architectural
fact: ADR-0016's private seal is **"the launcher never calls the durable board"**
(`lucid_jobs.py:6-8`, `create_from_image.py:200-207,253-255` — a private item carries
`job_id=None` and `_job()` no-ops on it at `:202`). This is NOT loosened. A **second, parallel,
RAM-only store** is added that the durable code path can never reach.

**Hard invariants (the boundary it must never cross):**
- **H1 — never disk-backed.** The private queue store has NO `~/.local/share/agentos/lucid-queue/`
  codepath. The durable queue lives under `~/.local/share/`; the private queue lives **only**
  under `$XDG_RUNTIME_DIR/agentos/lucid-priv-queue/` (mode 0700, both dir and parent `chmod`'d,
  atomic temp+`os.replace`, `_valid_id` traversal guard — mirroring `lucid_store.py:88-94` and
  `lucid_jobs.py:47-70`). There is **no shared base-path constant** and **no `if private: base=X
  else base=Y` inside one writer function** — the two stores are two modules (see chokepoint).
- **H2 — no persisted ambient surface.** A private held item NEVER increments
  `~/.hermes/needs_you.json` (`feed.rs:86,88-90`), NEVER appears in
  `keyhole.fleet.queued`/`snagged` (`keyhole.rs:84-88`, sourced from Hermes `kanban.db`,
  `feed.rs:106` — which a lucid item never touches anyway), and NEVER appears in the
  `pending_requests` field (§6). A private hold reaching any of those four persisted /
  over-the-shoulder-readable surfaces is a Blocker-class leak.
- **H3 — no review surface.** A private held item is structurally ineligible for `needs-review` —
  it cannot enter the human-approval channel. A private item that needs a yes/no is refused
  in-session via the existing synchronous consent dialog (`create_from_image.py:178-196`), never
  queued for later review. This is the one place the override does NOT widen.
- **H4 — no earcon.** Because a private item never reaches `state==2` (warm) or `state==4` (snag),
  the existing chimes cannot fire for it (holds for free). The private drainer MUST NOT emit any
  new sound.
- **H5 — burned on logout, completely.** Every private held item is wiped on logout (see the
  ExecStop fix below — the load-bearing condition).

**The chokepoint (two modules, no shared writer):**
- `lucid_jobs.enqueue(job_id)` (durable) — hard-refuses `job_id is None` AND hard-refuses any
  caller passing `private=True`; physically only knows `~/.local/share/...`.
- `lucid_priv_queue.hold(session)` (ephemeral, new) — the ONLY writer to the tmpfs queue dir;
  **asserts `ST.is_private(session)` is True** (`lucid_store.py:97-105`) and **raises** otherwise;
  physically only knows `$XDG_RUNTIME_DIR/...lucid-priv-queue/`.
- **Structural guarantee:** neither module imports the other's path constant; neither branches on
  `private` to pick a base dir. A future "unify the two queues" PR would have to *delete one module
  and merge two refusal asserts* — a loud, reviewable, ADR-requiring act. **Acceptance test:**
  `grep` proves no source file references both `lucid-priv-queue` and `lucid-queue`.

**The drainer is doubled.** The durable drainer reads only `~/.local/share/...`. The private
drainer is a **separate in-session loop inside the lucid web service process** (NOT a `--user`
systemd timer — a timer outlives the session and would resurrect a private retry after logout, an
H5 violation). It reads only the tmpfs queue, acquires the **same `Tier::BestEffort`** lease so it
structurally cannot block `Tier::Interactive`, and runs through the governed
`create_from_image.run(arg, private=True)` path — so B2, EXIF-strip, the lease, and the per-lease
SIGKILL ComfyUI-history seal (ADR-0016 honest-residual) all still apply to a retried private item.

**Surfacing ceiling (at most an in-session ephemeral count):** the lucid web panel (`:8765`,
loopback-only, CSRF-gated, already the private surface per ADR-0016 §4) MAY show a live count
"N private held — will run when the GPU frees, gone at logout" computed **on read** from a
directory listing of the tmpfs queue dir (the `list_private()` idiom, `lucid_store.py:265-270`).
RAM-derived, request-time, never serialized to a feed file, never off loopback. **Forbidden:**
writing that count to `agent.json`, `keyhole.json`, `pending_requests`, or `needs_you.json`. If the
count cannot be shown without writing a persisted sink, it is **not shown**.

**GO/NO-GO conditions (each an acceptance test, not a sentiment).** ADR-0019 may move from
Proposed toward Accepted on the private path ONLY when ALL land:
1. **(Blocker, load-bearing) ExecStop iterates EVERY live private session.** The existing burn hook
   `_burn_private_on_stop()` (`lucid_web.py:681-693`) calls `L.burn(SESSION)` with a single
   hardcoded `SESSION="web"` (`lucid_web.py:40,688`) — a private *queue* holds N sessions, so on a
   clean logout N−1 held items' on-disk sealed anchor frames (`input/.lucid-priv-<session>/`,
   ADR-0016 honest-residual lines 44-47) survive ExecStop until the next startup's `reap_orphans()`
   (`lucid_web.py:700-705`). "Burned on logout" is FALSE as-written. **Required fix:**
   ```
   for s in ST.list_priv_queue() ∪ ST.list_private():   # every live private session, queue + dream
       L.burn(s)                                          # symlink-aware, verifies removal (lucid_store.py:176-200)
   clear the tmpfs lucid-priv-queue dir entirely         # the queue records themselves
   ```
   Test: three live private held sessions → after ExecStop, zero `input/.lucid-priv-*` remain.
2. **(Blocker) Physical separation:** two modules, no source file references both `lucid-queue` and
   `lucid-priv-queue`; durable `enqueue(private=True)` raises; private `hold(non_private_session)`
   raises; no `if private` base-dir branch inside any one writer.
3. **(Blocker) No private item ever writes** `needs_you.json` / `agent.json` / `keyhole.json` /
   `pending_requests` (H2). Pin a test asserting the private path touches none.
4. **(High) Private drainer is in-session** (process-scoped, dies with the session), not a systemd
   timer; uses `Tier::BestEffort`; reuses the governed `create_from_image(private=True)` path and
   the per-lease SIGKILL ComfyUI (`lucid_linear.py:13-16,189-193`).
5. **(High) Intake copy rewritten** to state auto-retry AND ephemerality honestly; the old "runs
   now or not at all" line removed (see §"intake honesty").
6. **(Medium) TTL/max-attempts exhaustion burns silently** — no `expired` review row, no persisted
   artifact (a forgotten private retry dies quietly inside the session).

**NO-GO trigger (any one):** a shared base-path constant or a single writer that branches on
`private` to choose disk-vs-tmpfs; a systemd-timer private drainer; a private count emitted to any
producer feed file; or shipping the queue without fixing the single-session ExecStop burn
(Condition 1). Until Conditions 1-3 are met, **NO code lands on the private path**; the non-private
durable queue (G1-G8) is unaffected and may proceed.

**Intake honesty (mandatory, replaces the old line):** the old "runs now or not at all" copy
(brief line 209) is now FALSE and must be removed. At private intake, one calm line states the new
capability AND its cost: *"Private: held in RAM only, retries while you're logged in, gone the
moment you log out — never saved, never shown on the desktop."* Shipping the old copy alongside an
auto-retrying queue is a dishonest-intake Blocker. The inherited ADR-0016 swap residual (tmpfs may
swap to disk unless swap is encrypted) is carried forward verbatim — do NOT upgrade the claim to
"never on disk."

**Audio residual (unchanged, holds for free):** a private request emits no private-specific sound
and never reaches `state==2`/`state==4`, so the earcon layer cannot observe it.

### 6. Reuse map (no new ambient channel, no new earcon)
- Deferral count → `keyhole.fleet.queued` + a new additive `pending_requests` (schema 1→2; the
  round-trip test re-pinned — BUILT + VERIFIED, all 61 `agentosd` tests pass, G2). The field is
  **two honest counts**, not a singular int:
  ```json
  "pending_requests": {"held": 2, "needs_review": 1}
  ```
  Two counts → two tray lines; the split renders the §3 held(deferred)-vs-needs-review invariant in
  the schema, not prose. Both counts default to `0` and ALWAYS serialize (an empty queue is a REAL
  datum, NOT the `-1`/UNKNOWN sentinel the fleet/vram fields use); a missing/unreadable lucid
  sidecar collapses to `{0,0}` = "nothing waiting" (calm, fail-open). Appended **trailing** (after
  `tokens_per_sec`) so a schema-1 consumer ignores it. Negatives clamp to `0` in `read_pending` (a
  count is a cardinality; `-1` is reserved for UNKNOWN numerics and must never leak in). Read-only:
  the keyhole READS the optional lucid sidecar `$XDG_RUNTIME_DIR/nimbus-aurora/pending.json` but
  NEVER writes `needs_you.json`.
- **Warm-bloom producer (G1, the cap-lifter):** the warm bloom for a non-Hermes lucid review item
  is driven by a SEPARATE lucid-owned sidecar `~/.local/share/agentos/lucid-queue/review.json`
  (`{schema, pending_review, updated_at, items}`), which `feed.rs` folds **additively** alongside
  the Hermes `needs_you` count — NOT a second writer to `needs_you.json`. The two producers never
  name the same path (the Hermes `needs-you-signal` plugin writes `~/.hermes/needs_you.json` via
  its own `os.replace`, `feed.rs` reads it at `feed.rs:146-152`; lucid writes `review.json`, read
  independently), so the "collision with the plugin's whole-set `os.replace`" cannot occur by
  construction. `feed.rs` sums the two integer counts and ramps ONCE on the sum
  (`pending = hermes_pending + lucid_review`), keeping `warm ∈ [0.75, 0.9]` regardless of how the
  two origins split, and keeping the 11 existing `derive_feed` tests passing with `lucid_review=0`.
  The lucid sidecar carries its OWN liveness gate (`updated_at` heartbeat every drainer tick,
  suppressed at `>12s` stale), DECOUPLED from `gateway_alive` — so lucid review items survive a
  Hermes outage (fail-open, §1), unlike Hermes approvals which retract when the gateway dies.
  No-double-count is a disjoint-set proof: a lucid video-review and a Hermes command-approval are
  disjoint intent sets, summed once; v1 does NOT mirror lucid items into `needs_you.json` (that is
  Phase 3, gated on the Hermes write-API, ADR-0012 §6).
  > **Cross-producer drift pin (G2↔G1):** the keyhole's `needs_review` count (`pending.json`) and
  > G1's warm-bloom signal (`review.json`) MUST describe the SAME set, or the tray says "1 needs
  > your OK" while the wallpaper shows no warmth (or vice-versa). Lucid is the single source of
  > truth and must write both consistently from the same authoritative recompute. `needs_review`
  > in `pending.json` is a DISPLAY mirror only — it must NEVER drive warmth; the warm-hue invariant
  > (§3) silently dies if an implementer wires the keyhole count to bump the wallpaper warm float.
- Earcons → needs-review via `state==2` (existing `needs_you` chime); stall/preempt via
  `state==4` (existing `snag` chime); retry backoff is the snag-debounce authority so a
  flapping retry can't weaponize the chime. Held/auto-run/expiry are silent.
  > **G8 honesty flag (substrate-blocked):** `feed.rs:106` sources `fleet.snagged` *exclusively*
  > from the Hermes `kanban.db` SQL — a local lucid stall has no row there and can never reach
  > `state==4` today. The `state==4` snag reuse for a local stall is **not-yet-wired**: either route
  > a local stall through G1's sidecar, or mark this Phase-2 wiring honestly (the way `state 3
  > 'acting'` is flagged as defined-but-never-emitted). This is the one "reuse, coin nothing"
  > claim that is unspiked new work, stated honestly.

### 7. UX surface (smallest reviewable surface)
- **Keyhole tray** = glance, **read-only v1**: "2 held — GPU busy" (calm), "1 needs your OK"
  (warm), link-out to the web panel. No tray write-actions in v1 (ADR-0012 §6 gate).
- **Lucid web panel** = act: held / needs-review list with seed-frame thumbnail and
  Retry / Allow / Cancel / Dismiss (review must see the image).
- **Recovery toast** = primary control for the common case: swaync action button
  `[Run when free ✓] [Cancel]` where the drop is felt, ≤2 steps; the board is audit fallback.
- Deferral earns no ambient cue beyond the count; the empty queue returns the field
  **byte-identical to idle** (an ADR acceptance test: fixed-`iTime` capture diff == 0).

### 8. Motion & yield
- The keyhole count eases ~3–6s, gated on `model.reducedMotion` (instant under reduce-motion);
  producer snaps the integer, consumer eases it (never both).
- `held → running` coinciding with a VRAM yield (ADR-0004) is a settle-in fade-up from black on
  flux restore, never a crossfade across the kill window.

## Consequences
Positive: intents are never silently dropped; private creations now DO auto-retry within the
session (the human's override — more capable than the round-1 recommendation) while remaining
ephemeral and unobservable; the desktop reads "waiting" honestly instead of a fake idle;
fail-open is preserved (a down drainer never blocks interactive inference); v1 ships without
Hermes, in `spikes/`, fully revertible.

Costs / accepted tradeoffs:
- Private creations gain in-session auto-retry but at the cost of a **widened ADR-0016 seam**: a
  private request now has an in-RAM `priv-held` row + claim that exists for the session lifetime.
  An attacker with live read access to `$XDG_RUNTIME_DIR` (already 0700, already where private
  *clips* live) can observe that *a* private hold exists. This does NOT widen the durable surface
  (nothing on disk survives logout, nothing on the board, nothing in the warm signal), but it is a
  real in-session observable that "run-now-or-burn" did not have. Recorded honestly per the
  AIRTIGHT mandate; `design-researcher`/`content-voice-designer` dissent stands (below).
- The deferral runner must **invert source-of-truth** (the spool FILE, not the detached process,
  is authoritative). A job exists because a spool record exists in state `held`, independent of any
  running process. State is encoded in the filename suffix (`.held.json` / `.running.json`) so the
  atomic `os.rename` in `claim()` IS the `held → running` transition — single-flight without a DB,
  `read_held()` globs `*.held.json` so a `running` record is invisible to the scan. This fixes
  preemption-loss and is more than a "promotion."
- **Crash recovery decides from the FILE, not the PID.** With a process-level `flock`, any
  `*.running.json` present at the START of a drainer fire is provably an orphan (a live drainer
  finishes within its own fire) → recovered to `held`, `attempts++`, cause `preempted`, WITHOUT
  ever probing `owner_pid` (`kill -0` would lie on a recycled PID). `owner_pid` is advisory only.
- **Idempotent re-run is a hard table-stake (not polish).** The dedup key is the held row's stable
  `job_id`, threaded into re-run — NOT a freshly-minted `session` (`create_from_image.py:254`
  regenerates one per process, so "dedup on job_id" of a fresh id is a no-op) and NOT a content
  hash of `seed+prompt+seed-int` (the seed is `random.randint` per call at generate-time, never
  persisted — `lucid_linear.py:226-227` stores `seed:None` — so that hash is non-reconstructible at
  re-entry). Exactly-once is an `O_EXCL`/atomic claim-lock keyed on `job_id`, taken before the
  compute-commit boundary (`lucid_linear.generate_video` before `lease_spawn`, `:173`) and released
  in a `finally`. The drainer and the manual web-panel Retry must call the IDENTICAL
  `run(..., job_id=row.job_id)` entry so they contend on the same claim. A 5×-deferred job produces
  ONE clip, not five. Pin with `reversibility-tx-reviewer` (tests T-idem-1/2/4).
- A drainer re-fetch can find the source URL gone → snapshot the sanitized PNG at intake (from the
  already-sanitized `_clean_png` at `create_from_image.py:274`, into the spool BEFORE the
  `:331-336` finally unlinks it) and retry from stored bytes (hard v1 requirement; re-fetch of a
  remote URL at drain time is forbidden).
- **Durable-store fsync prerequisite (Blocker before calling the store "durable"):**
  `lucid_jobs._write` (`:58-70`) currently does `os.replace` WITHOUT `fsync` — once promoted to the
  disk-backed durable store this is a torn-backup-after-hard-reboot hazard. It must adopt
  `lucid_store.save_chain`'s fsync discipline (`:112-120`). Record order is invariant: spool row
  durable+fsync → claim → mutate/generate → writeback → release; `attempts++` only on a completed
  run's writeback, never on recovery (so revert/recover-twice is idempotent).
- One warm signal cannot distinguish a video review from a Hermes approval; the keyhole is the
  legible breakdown (two counts in `pending_requests`, §6).

## Recorded dissent (never erased)
- **design-researcher** dissents against any "ephemeral in-session private queue" on evidence
  (ADR-0016 residual seams + incognito precedent); choosing it knowingly widens a sealed seam.
  **Overridden by the human's binding decision** — but `responsible-ai-privacy-skeptic` adopted
  design-researcher's **physical-separation requirement** as a GO condition (§5 chokepoint: two
  modules, no shared path constant). The dissent stands; this is a knowing seam-widening ratified
  with conditions, not a clean win.
- **content-voice-designer** (conditional) concurs: an in-session private hold is a surface, and a
  surface is the leak the no-board doctrine exists to prevent. **Overridden by the human's binding
  decision** — but `responsible-ai-privacy-skeptic` adopted content-voice-designer's
  **intake-honesty condition** as a GO condition (§5: the old "runs now or not at all" line is
  removed; intake states auto-retry AND ephemerality). The dissent stands; the only permitted
  surface is the loopback-only, RAM-derived, request-time count on the already-trusted `:8765`
  panel.
- **motion-designer** dissents against giving held requests a new separate animated UI
  affordance; held = a queued thing the keyhole already renders. (S1 row-continuity is compatible —
  it reuses the existing card and `develop` keyframe.)
- **interaction-designer** anticipates **ux-reviewer** preferring one unified "Pending" list;
  the synthesis holds two held-states because list-simplicity costs signal-honesty.
- **rater-feasibility / rater-market-fit** (held, not averaged away): the headline behaviors ride
  `lease.Queued`, which is told-to-retry, NOT a wait-queue — so v1's differentiated behavior is a
  *polling drainer* (~20s worst-case latency), shallower than the prose. Routed to G10 (closed-loop
  lease-free event) as a Phase-2 item, not a v1 blocker.

## Open questions (for the human; framed)
1. **Private-mode handling (values fork) — RESOLVED by the human's binding decision.** The human
   chose the **ephemeral in-session tmpfs queue** (more capable — private creations auto-retry
   within the session). `responsible-ai-privacy-skeptic` ratified it **GO with conditions** (§5
   GO/NO-GO). `design-researcher` and `content-voice-designer` dissent is recorded above and stands;
   it was overridden by the human WITH the skeptic's physical-separation + intake-honesty conditions
   attached. No longer an open question — Condition 1 (the ExecStop multi-session burn fix) is the
   load-bearing prerequisite before any private-path code lands.
2. **Re-confirm on stale auto-run?** When a deferred creation auto-runs 40 minutes later it
   produces an artifact the user may have forgotten requesting. Options: run silently (calm,
   but surprising) vs re-confirm if older than a TTL ("Still want this?"). Recommendation:
   re-confirm a deferred *creation* past a TTL; run a deferred *check* silently. Owner:
   interaction-designer.
3. **Phase-3 Hermes mirror** depends on a confirmed Hermes write-API (ADR-0012 §6). Until it
   exists, lucid review items do not enqueue into kanban. Recommendation: hold Phase 3 until
   the write-API is confirmed; do not build on an unconfirmed API.

## Validation (acceptance tests this ADR asserts)
**Idle / warm-hue invariant:**
- Empty queue → wallpaper field byte-identical to unmodified idle (fixed-`iTime` diff == 0;
  `derive_feed` with `lucid_review=0` is bit-identical to today's two-arg call).
- `held` never increments `needs_you.json` / `review.json`; only `needs-review` does.
- Lucid-only bloom; lucid+Hermes additive (sum-ramps-once, `warm ≤ 0.9`); stale-lucid suppressed
  (>12s); lucid survives a dead gateway; empty → byte-identical idle (G1).

**Schema / contract (G2 — BUILT + VERIFIED, 61 `agentosd` tests pass):**
- `pending_requests` serializes as `{"held":N,"needs_review":M}` — two counts, never a singular
  int; `pending_defaults_to_empty_not_unknown` (default `{0,0}`; absent file `{0,0}`; schema-1-era
  `{}` → `{0,0}`; negative junk clamps to 0; independent counts).
- The re-pinned exact-string round-trip pins `schema:2` + the trailing `pending_requests` object;
  the all-UNKNOWN frame keeps pending `{0,0}` (NOT -1) — an unreachable Hermes says nothing about a
  LOCAL lucid queue.

**Drainer / determinism (G3, G4, G6):**
- `next_state(attempts, last_error, age_s)` is pure (no clock/fs/model) with total precedence
  `expired > needs-review(human cause) > needs-review(retries exhausted) > held`; resource retries
  that exhaust go to `needs-review`, never dropped. `retry_backoff_s` monotonic + capped.
- Anti-scheduler (G6): no job record carries a `_FORBIDDEN_ORDER_KEYS` member
  (`priority|weight|rank|urgency|boost|class`); `drain_order` sorts by `seq` ASC (arrival FIFO),
  calls `_assert_no_priority` which raises `SystemExit` (fail-closed, not a log) on any priority
  key; `next_retry_after` is eligibility-only, never an ordering key; `created` is display-only.
- Crash recovery (G3): a `*.running.json` present at fire start → `held`, `attempts++`, cause
  `preempted`; `owner_pid` is never probed.
- Single-flight: two `claim()` on one held file → exactly one returns a record, the other `None`.

**Idempotent re-run (idem, market table-stake):**
- T-idem-1: two concurrent `run(arg, job_id=jid)` → exactly one generation, one artifact.
- T-idem-2/3 (drainer × manual Retry; double drainer tick): one claim wins, `GEN_CALLS==1`.
- T-idem-4 (crash recovery both polarities): dead-pid claim → steal + run once; live-pid claim →
  do NOT steal, drainer skips.
- T-idem-6: recovery-to-`held` does NOT increment `attempts`; only a completed run's writeback does.
- Durable store: `lucid_jobs._write` adopts fsync (torn-write durability T-idem-7).

**Safety carve-outs:**
- `enqueue(None)` and durable `enqueue(private=True)` both refuse (private cannot persist to disk).
- A possible-minor request renders no action affordance at the component level.

**Fail-open / a11y gates:**
- A down drainer never blocks `Tier::Interactive` admission; stronger with `Tier::BestEffort` — an
  *up* drainer mid-generation also never blocks interactive (interactive preempts it,
  `coord.rs:132`, `lease.rs:583-592`). The drainer path must pass `"best-effort"`, not the
  hardcoded `"batch"` at `lucid_linear.py:66` (the single load-bearing line).
- **G5 (HARD GATE before Phase 2):** held row persisted BEFORE the recovery toast at all three
  deferral sites; toast timeout/dismiss/never-seen are no-ops on the row; `Cancel` (not dismiss)
  retracts; WCAG T8-T13 (keyboard-operable, focus order, polite announce, no countdown, ≥24px
  targets, AA contrast on the opaque card); broker resolves `action_key=run:<job_id>` after restart.
- **G7 (HARD GATE before Phase 2):** warm-state contrast measured against the warm-BREATH-PEAK,
  worst-pixel-in-bbox, low-brightness, on Hills AND Flow. The tray `#ff9957` warm-on-warm FAILS AA
  as authored (3.07–3.92:1) and must apply F2 (warm de-coupled from text fill / opaque instrument
  chip); web-panel `--inst-label` (3.82:1 on glass) fixed via F1; `g7_contrast.py` CI asserts
  thresholds paired with the idle `iTime`-diff==0 test (both ends of the warm channel proven).

**Private-path conditions (§5 GO/NO-GO — NO code lands until 1-3 pass):**
- ExecStop with three live private sessions burns ALL three sealed input subdirs (not just
  `"web"`); after logout no `input/.lucid-priv-*` remains (Condition 1, the load-bearing fix).
- No source file references both `lucid-queue` and `lucid-priv-queue`; durable `enqueue(private=True)`
  raises; private `hold(non_private_session)` raises (Condition 2, physical separation).
- A private item never writes `needs_you.json` / `agent.json` / `keyhole.json` / `pending_requests`
  (Condition 3, H2).
- Private retry past TTL/max-attempts burns silently — no review row, no on-disk trace (Condition 6).
- Private intake line states auto-retry AND ephemerality (Condition 5).

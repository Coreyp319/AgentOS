# ADR-0034: The overnight UI-audit goes deterministic (no_agent) + persistence honesty + before/after screenshot auditability

- Status: **Accepted — Phase 1 + Phase 2 SHIPPED** in the `kde-plasma-customization` Hermes skill
  (lives in `~/.hermes/skills/devops/kde-plasma-customization/`, not this repo — skills are stored
  under `~/.hermes` per ADR-0001). Phase 1: the daily audit (cron `9d804e712259`) is a `no_agent`
  deterministic job; the persistence/governance honesty bugs are fixed; the job is left **paused** for
  the user to resume. Phase 2: before/after screenshot cards are built and verified end-to-end —
  consent-gated, network-isolated full-desktop capture via spectacle, deferred next-login AFTER frame
  (a KDE autostart hook) that doubles as the persistence proof, stitched into a labeled card. Remaining
  is verification-not-build: a real approve→relogin card on the box, and the live daily run after
  resume.
- Date: 2026-06-21
- Relates to: ADR-0001 (don't reinvent — Hermes schedules/runs cron, this is a Hermes *skill*, not new
  agentosd code; the only AgentOS-side hook is the deferred lease integration for the explore pass),
  ADR-0003 (fail-open-supervised — the audit must never wedge the desktop or emit false signal),
  ADR-0010 (VRAM lease — the basis for the *deferred* attended "explore" pass), ADR-0011 (autonomous
  overnight pipeline — same "model proposes, code disposes, human accepts" spine, here taken to its
  conclusion: when code disposes *everything*, the model is removable).
- Review input: 6-lens review 2026-06-21 (ux, wayland-computeruse, ai-product, responsible-ai-privacy,
  + empirical capture/persistence verification on the live box).

## Context
The "overnight UI-improvement agent" is the Hermes cron job **Daily UI audit** — a local model
(qwen3.6-27b) driven by `kde-plasma-customization` that audits and applies KDE theming keys
(contrast, font weights, KWin effects). It was built on a sound "model proposes, code disposes"
spine: a deterministic collector emits `state.json`, the model authors `ops.json`, and
`ui-audit-apply.py` enforces an allowlist, state-binding assertions, canonical targets, earned-auto,
backup/verify, and a deterministic report. The user asked to make it "fully functional by itself,
silently working away, very auditable via before/after screenshots." Review surfaced three problems:

1. **It was lying.** `kdeglobals:KDE:contrast` is owned by the active color scheme
   (`CoreyLavender.colors` ships `[KDE]contrast=4`) and a `LookAndFeelPackage`, which re-stamp
   `kdeglobals` at login. The audit wrote the `kdeglobals` *shadow*, its same-process read-back saw
   the new value, logged `accepted`, and the change reverted at the next login. Live `contrast` was
   `4` while the ledger said `7`; one report said both "4→7 changed" and "already at 7". A weeklong
   false-success loop. An autonomous agent that silently un-fixes itself and falsifies its own audit
   trail is worse than nothing.
2. **The model was vestigial.** `ui-audit-apply.py` already disposed every canonical target
   (`contrast→7`, font weight→500). The model's only job — "emit an op for each allowlisted key below
   target" — is a map over a 10-entry allowlist on a deterministic snapshot, zero decision degrees of
   freedom. Running a 27B model for it caused the 09:00 VRAM collision that **paused the job**,
   required a reaper cron to evict it, and reintroduced the confabulation the skill exists to prevent.
3. **The desired "simple before/after screenshot" collides with three constraints** (privacy,
   relogin, legibility) — see Phase 2.

## Decision

### Phase 1 — deterministic + honest (SHIPPED)
1. **Kill the model; the daily job is `no_agent`.** New `ui-audit-decide.py` deterministically emits
   `ops.json` from the snapshot. The cron job runs `~/.hermes/scripts/ui-audit-run.sh`
   (collect → decide → apply) with `no_agent: true`, rescheduled to `0 4 * * *` (off the VRAM-heavy
   09:00 window). The orphaned model-reaper cron (`d43efcc8d69a`) is paused. This eliminates the
   collision, the reaper, the wedge risk, and the confabulation surface in one move.
2. **Governance — don't fight a load-bearing source.** The collector resolves the effective source of
   each shadow key and emits a `governance` block. A key re-asserted on login by the scheme/
   LookAndFeel (`durable:false`) is **never** auto-applied or staged as a doomed `kdeglobals` write;
   it becomes an honest **manual recommendation** ("a durable change means editing `<scheme.colors>` —
   load-bearing, hand-maintained, not auto-touched"). The source file stays DENY-listed. Governance is
   checked *before* the soft-veto so a scheme-driven login revert is never misread as a manual undo.
3. **Persistence net — the cross-login honesty check.** Every run, the applier re-reads each key it
   previously `applied`/`accepted` and compares to live; a mismatch is a **regression**, surfaced
   loudest (top of `report.md`) and written to `regressions.json`. Non-durable keys are excluded (they
   are recommendations, not alarms). This is the mechanism that would have caught the contrast lie on
   day two.
4. **Idempotent unattended runs.** Re-staging the same finding is deduped (no duplicate `pending/*.json`,
   no ledger bloat). Repeated runs at steady state change nothing. Verified: zero growth across runs.
5. **A residual model role exists but is NOT this job.** Discovering findings *beyond* the canonical
   allowlist is open-ended judgment. If built, it is a *separate, rare, attended* "explore" pass,
   proposals-only, run under the agentosd VRAM lease (ADR-0010) — never folded into the daily sweep.

### Phase 2 — before/after screenshot auditability (SHIPPED)
Implemented as three scripts + a login hook: `ui-audit-shot.py` (consent/capture/stitch/forget/prune),
`ui-audit-after.py` (deferred next-login AFTER + persistence-stamped card), `~/.config/autostart/
ui-audit-after.desktop` (the login trigger), wired into `ui-audit-apply.py`'s auto-apply and `--approve`
paths via `record_change_shot()`. A change captures BEFORE at apply time, queues the AFTER for next
login (kdeglobals only renders after a relogin — capturing AFTER now would just duplicate BEFORE), then
at login the AFTER is captured, the live value is checked against what was applied, and a labeled card
(header = key + delta + run, BEFORE/AFTER frames, footer = why + effect + **persisted/REVERTED status**
+ revert command) is stitched into `~/.hermes/ui-audit/cards/`. Capture is best-effort: a failure never
blocks or reverts the change (fail-open, ADR-0003). The persistence stamp on the card is the same
honesty net as Phase 1, made visible.

Empirically proven on the box: silent capture works **only** via `spectacle -b -n -f` (KWin rejects
arbitrary DBus screenshot clients — `NoAuthorized`; spectacle is the authorized binary, gated on the
binary not the launch path). The cron context is headless but recoverable by exporting
`WAYLAND_DISPLAY` (XDG_RUNTIME_DIR + the session bus are already inherited). Three hard constraints
shape the design:
- **Privacy:** a full-desktop grab photographs whatever is open (it captured the user's private
  browser content in testing). Per the existing usage-signal contract, audit images must stay
  on-box (0600, 30-day retention, `--forget`), run under `run-sandboxed.sh`, never be delivered/
  attached, and be gated by a one-time consent marker.
- **Relogin:** kdeglobals keys take effect next login, so an honest "after" frame for those keys must
  be captured at next login (which doubles as the persistence check); only KWin effects live-reload.
- **Legibility:** a contrast 4→7 amplifier shift is nearly invisible in a full-desktop diff.
- **Chosen artifact (user decision 2026-06-21):** full-desktop `spectacle` before/after, stitched with
  `magick montage`, **with** the on-box-only storage + consent gate + deferred next-login "after"
  capture above. (The off-screen reference render via `kwin_wayland --virtual` against a throwaway
  `XDG_CONFIG_HOME` was evaluated as the privacy-and-honesty-optimal alternative; deferred unless the
  full-desktop privacy posture proves unacceptable in practice.)

### Tier-2 — Style Charter + genuine design critique (Charter SHIPPED; critique attended-only)
Evolves the audit from mechanical compliance into a design critic with a persistent, user-owned
aesthetic identity. Two cleanly-separated parts (a 2-lens design review — product + privacy — insisted
on the split):
1. **Style Charter (`ui-audit-style.py`, SHIPPED) — deterministic, model-free.** A versioned aesthetic
   anchor in `~/.hermes/ui-audit/style/`: NL **intent** + **principles** (the rubric a critique scores
   against) + a **tokens** snapshot of the live look (scheme/palette/fonts/contrast/blur/…) + version
   history. Verbs: `keep` (checkpoint the current look as the active version — the user can keep at any
   time), `evolve "toward X"` (open a *draft* targeting a new direction; changes nothing on the desktop;
   critique then guides toward it until `keep` finalizes it), `show`/`tokens`/`history`. This alone
   satisfies "keep the theme over sessions / stable by default / evolve only on intent." **Hard rule:**
   it NEVER writes the scheme/LookAndFeel (the contrast lesson) — it describes and *defends* the
   hand-maintained look; the model proposes directions, the user's `keep` disposes. v1 seeded from the
   live CoreyLavender desktop.
2. **Genuine critique — attended/on-demand ONLY, never cron.** Genuine *style* critique (color/type/
   contrast) needs no screenshot (derivable from the scheme) — privacy-free. Genuine *layout/info-
   hierarchy* critique needs a rendered frame, which is the privacy crux: it is produced by an
   **on-demand, attended** model pass (default: Claude in the user-invoked session — a 3B local VL
   yields generic slop; pulling a 32B pays the ADR-0010 lease tax for still-sub-Claude taste), reading
   the Charter + a frame, output as critique cards in the existing `cards/` system with an acceptance/
   action metric and a specificity gate (no generic item that doesn't cite a concrete token/role). It
   is **not** governed by ADR-0011 (that frame is unattended-overnight; capture must be attended here).
   **Frame privacy ruling:** the green-lit frame for layout critique is a **proven-bare desktop** (empty
   virtual desktop + neutral wallpaper — never the live UE/dream wallpaper — + DND + a KWin window-list
   assert that zero app windows are present, reversibly), which shows real shell layout with no private
   content and is the only frame safe for a cloud-tier model. Full-desktop→local-VL is a power-user
   opt-in (image never leaves box but a model now ingests private content); full-desktop→cloud is a
   hard NO. A distinct **critique-consent** layer is required (capture-consent ≠ "a model may analyze
   your desktop"); `--forget` must reach critique *text*, not just PNGs; critique prompts treat
   on-screen text as lorem-ipsum (geometry/hierarchy only).

   **SHIPPED (`ui-audit-critique.py`):** the user chose full-desktop → local model, wired **through the
   VRAM lease**. Flow: `Acquire(interactive, est)` on the agentosd coordinator → capture (no-net) →
   local qwen2.5-VL → critique vs the Charter → specificity gate + quoted-span redaction → 0600 brief
   in `style/critique/` (frame discarded) → `Release`; `--accept`/`--dismiss` feed an acceptance metric.
   The lease offers a model only ~7.7 GB (it reserves gfx headroom — ADR-0004), so **7b fits and 32b is
   correctly denied**; a denied lease aborts rather than forcing an OOM; an unreachable coordinator
   fails open. Two honest limits found on the box: (a) **qwen2.5vl:32b's vision encoder fails to load in
   the installed Ollama** (model/runtime incompat, independent of VRAM) → default is **7b**, which loads
   and fits the lease budget; (b) the 7b is useful-but-below-Claude-tier and **still names unquoted UI
   labels** despite the lorem-ipsum prompt (redaction only strips quoted spans) — the residual
   full-desktop+local leak; the bare-frame option (declined) is the clean fix, and briefs are 0600 +
   `--forget`-able + the frame is never retained. The frame/model fork is RESOLVED; end-to-end working.

   **Tier-2 design-council review + iteration (2026-06-21).** A full panel (3 design lenses + 5 raters +
   aggregator) rated the first Tier-2 cut ITERATE 5.9/10 — Charter SHIP (~9), critique engine ITERATE
   (~5). The gap plan was then implemented and re-rated (vision 8, experience 8, craft 8.5, feasibility 8;
   composite ~8.1). What changed: (a) **honesty override** — the deterministic style assessment now
   overrides the model's `honors_intent` self-certification (the brief can't claim "honors intent"
   against measured AA failures); (b) **deterministic style critique + drift** codified
   (`ui-audit-style.py assess`/`drift`) and surfaced as the leak-free TRUSTWORTHY layer, with the local-VL
   layout pass labelled "directional"; (c) **charter-contradiction / self-nullify / tightened-generic
   filters** (a "add panels" suggestion that contradicts a recede principle is dropped); (d) **evolve→
   critique wired** (`target_intent` steers a draft); (e) **dismiss now sticks** (fingerprint suppression);
   (f) **consent integrity** (run-time verify + re-stamp; the stale 32b record fixed); (g) **B4 fix** — the
   lease is held over a persistent in-process D-Bus connection across the inference (verified against
   `lease.rs:1476-1500`: the supervisor keys on the bus sender the connection holds, so the protective
   hold survives the ~210s critique); (h) **bare-frame default** capture — minimises app windows via a KWin script (the `showDesktop`
   D-Bus method is a NO-OP on this Plasma 6 Wayland — verified; the first live run leaked VS Code panel
   names because of it), tracking exact `internalId`s so restore is precise, leaving the keep-below UE
   wallpaper untouched + re-asserting it, restored in `finally`+atexit+SIGTERM — closes the app-content
   leak at the source (verified live: the bare frame is window-free, showing only the wallpaper/shell); (i) Charter `revert`/
   `discard` verbs + `wallpaper_plugin` token; (j) a filter unit-test suite (`test_critique_filters.py`,
   16 cases). Scorecard: `docs/design/0034-tier2-style-charter-critique-panel-scorecard.md`.

   **Ambient drift cue — BUILT (2026-06-21).** The "your desktop has drifted from its kept identity"
   signal is no longer substrate-blocked: it folds the Charter's `drift` amount into the reactive
   wallpaper's **`Desat`** lever (ADR-0030). Producer: `ui-audit-style.py emit-drift` writes
   `$XDG_RUNTIME_DIR/nimbus-aurora/drift.json` `{schema,amount,updated_at}` (on every charter mutation
   + a `nimbus-aurora-drift.timer` every 3 min for desktop-config drift). Consumer: the `scene.rs`
   disposer reads it (untrusted, schema-gated) and folds a gentle `DRIFT_DESAT_MAX=0.25` floor into
   `t_desat` via MAX — never warm (D8 monopoly), drift=0 ≡ byte-identical idle (D4), eased on the slow
   spring, applied even when the fleet feed is stale/blind (drift is a local signal, like wind). A real
   snag (desat 0.6 + fog) always reads stronger than a drift haze (desat-only, ≤0.25). 6 new scene
   tests (31 total pass). Verified producer→disposer end-to-end: drift 0.12 → `scene-params.json`
   `desat:0.03`. **Actuation on the live wallpaper is gated**, not blocked: it needs the `agentosd
   scene` + `rc` services running, which are the ADR-0030/ADR-0029 §B gated-prototype reactive stack
   (no service units yet) — a separate GO/NO-GO.

   **Post-review hardening (2-lens substrate review — rust-performance SHIP + resource-safety APPROVE).**
   Both flagged the same Medium — drift had no staleness gate (a dead producer would freeze a permanent
   haze, unlike the heartbeat-gated agent feed). Fixed: `drift.json` carries `updated_at`; `read_world`
   folds drift to 0 past `DRIFT_STALE_SECS=600` (> the 3-min cadence) so the haze FADES to calm.
   rust-perf's Medium — the adjacent `gust: f64, drift: f64` transposition footgun — fixed with a
   `LocalSignals {gust,drift}` struct returned by `read_world` by name. The producer units
   (`nimbus-aurora-drift.{service,timer}`) are now tracked in `crates/agentosd/dist/` + bounded
   (`TimeoutStartSec`/`Nice`/`NoNewPrivileges`; heavier namespace sandboxing hangs `kreadconfig6`, so
   it's skipped — the can't-touch-the-lease property holds by process isolation regardless). 32 scene
   tests pass (+ a staleness-gate test), clippy clean.

   Other fast-follows: one live granted-path model run on an idle GPU (deny path + full pipeline
   verified, the latter via mock); SIGKILL (uncatchable) can still strand show-desktop briefly (lease
   TTL + 1-click undo bound it); `--undismiss` + louder dbus-absent signal are nice-to-haves.

## Consequences
- The audit is now honest: it reports manual recommendations and regressions instead of false
  successes. Day-one it surfaced three real findings the model had ignored for seven runs (menuFont/
  toolBarFont/smallestReadableFont at weight 400, below the 500 target).
- No GPU contention, no reaper, no confabulation. "Fully functional by itself, silently working away"
  is met for the config audit; the job is paused pending the user's resume
  (`cronjob(action='resume', job_id='9d804e712259')`).
- Success metric: `open_findings` that **stay 0 across logins**; a regression must surface, never be
  silently re-applied.
- Contrast will keep appearing as a manual recommendation until the user either edits the scheme by
  hand or marks it `wontfix`. This is correct: the audit must not auto-edit a hand-maintained,
  load-bearing scheme/LookAndFeel.

## Open / deferred
- Phase 2 verification on the box: a real approve→relogin card (the synthetic-record test passed; the
  live approve+relogin path is unverified until the user approves a font and logs in).
- The off-screen reference render (`kwin_wayland --virtual`) remains the privacy-and-legibility-optimal
  alternative to full-desktop capture if the full-desktop posture proves unwanted in practice.
- Escalation channel: regressions currently surface via `report.md` + `regressions.json` + local cron
  delivery. `~/.hermes/needs_you.json` exists but has no discoverable writer/schema in the Hermes code,
  so it is not written to yet — wire it once the contract is known.
- The attended "explore beyond the allowlist" pass under the lease (named here, intentionally not built).

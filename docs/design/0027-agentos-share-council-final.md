# Design-Council Final Brief — AgentOS Share (phone → desktop ingest hub)

Status: **PROPOSAL (design-only).** ADR-0027 is a verified stub — none of the surfaces
(hub, `/api/share`, Shortcut, PWA receipt, kanban bridge, Claude inbox) exist in the repo
yet. The only built, exposed photo-from-phone surface is the Lucid spike
(`apps/dreaming/lucid/lucid_web.py`). "SHIP" in this document can only ever mean
"ship the design," never "the code exists." Code disposes on the human's timeline.

Mediator: design-discourse-mediator · Date: 2026-06-19 · Verdict: **ITERATE (7.55/10, target 9).**

Companion artifacts:
- ADR stub: `docs/adr/0027-agentos-share-phone-ingest-hub.md`
- Full discourse brief: `docs/design/0027-agentos-share-ingest-hub-brief.md`

---

## 0. What this is, in one sentence

Share a photo from your iPhone into your *own* box — Lucid dream, Hermes (chat or task),
or Claude Code — and the moment a destination could *act*, every action is a reversible
proposal you confirm on the box, processed provably on-machine, nothing ever leaving it.

---

## 1. The decided design direction (locked)

### The spine: three trust classes, not three peers
The destinations ship in **risk order**, never as equals:
- **Data → gated renderer** (Lucid) — safe; B2 likeness gate already live in `start()`. Ships first.
- **Data → trusted orchestrator** (Hermes chat/task) — chat is irreversible (disclosed), task is deletable. Ships second.
- **Instructions → an actor with tools** (Claude) — the lethal trifecta (tools + untrusted input + sensitive scope). Gated last, behind its own review.

### The eight decisions that are settled
1. **Shortcut-primary, PWA-secondary** (unanimous; iOS Safari has no Web Share Target — locked truth). Intent in the sheet, consequence in the PWA receipt.
2. **Hermes `:8642` stays loopback; the hub proxies** (verified against `agentosd-remote.sh:49`, 8642/8188 deliberately excluded). The Hermes `API_SERVER_KEY` never reaches the phone.
3. **Reuse the Lucid harness** (CSRF + Origin allowlist + EXIF-strip + decode-bomb guards + B2 gate) — don't reinvent the pattern.
4. **Staged hub placement with a named graduation trigger** — extend `lucid_web.py` for the Lucid + Hermes-chat MVP; move to a dedicated `share_web.py` on its own `tailscale serve` port the moment the Hermes-task *write* or the Claude *execution* path ships. Code-execution must not share the NSFW-capable dream loop's CSRF/lifecycle boundary.
5. **Auth model:** tailnet membership = primary auth; per-process CSRF guards the PWA; a file-backed `X-Share-Key` is defense-in-depth, **explicitly not a security boundary** (the Shortcut can't hold a real secret).
6. **EXIF-strip every path** (B2 stays generation-only on Lucid). Per-destination retention disclosed honestly in copy.
7. **Claude inbox = inert file → plan-only `claude -p` in a scoped cwd → human approves on the desktop → execution inside the ADR-0005 tx.** Auto-pickup refused by design.
8. **Honest-mapping locks:** no wallpaper drive from shares (only `derive_feed`); routes are icon+label on a single `--inst-blue` (`--inst-warm` never spent on a route); acks reflect comprehension not transport; outages are honest-open, never "Sent ✓".

### Phased MVP (smallest correct first slice)
- **Phase 0** — resting capture surface + three-door chooser, wired to the Lucid door only (`/api/start`, B2 live). Zero execution risk.
- **Phase 1** — PWA enablement (`.webmanifest` MIME + apple-touch-icon + shell `sw.js`) + the iOS Shortcut deep-linking the Dream door + Hermes-chat over loopback proxy + one `X-Share-Key` + EXIF-strip every path + held-spool degrade.
- **Phase 2** — Hermes-task bridge (deterministic, `status='triage'`, deletable) — gated on the task-write-mechanism question.
- **Phase 3 (separate review gate)** — Claude inbox as a human-approved proposal queue.

---

## 2. Rating verdict + the 10/10 gap plan

**Overall: 7.55/10 (weighted) → ITERATE.** No cap fires — the design pre-empted every
non-negotiable violation (Hermes proxied not reinvented; the dangerous path held in
`proposed` with a deterministic inert-inbox gate + human approve + ADR-0005 tx; no a11y
cap, though a *latent* cap-at-5 lurks in the unstated reduced-motion proposed-vs-executed
contract). Uncapped = capped = 7.55.

| Dimension | Weight | Score | The one thing it needs |
|---|---|---|---|
| Vision-fit | 0.30 | 9 | Mechanize reversibility *per path* in the ADR |
| Experience | 0.25 | 8 | Define the `acting` ambient look as a hard Phase-3 prerequisite |
| Craft | 0.20 | 8 | Pin the kanban write contract (fail-closed schema probe) |
| Feasibility | 0.15 | 8 | Spike the two unspiked load-bearing contracts |
| Market-fit | 0.10 | 6 | Pull the revertible-Claude-proposal slice forward (shown, not promised) |

### 10/10 gap plan (prioritized, owned, deduplicated)
Owners are *makers* (raters score, they don't own fixes). `[BLOCKED]` = phase-gated.

1. **Pin the kanban write contract** — adopt the `feed.rs`/`FLEET_COLUMNS` fail-closed schema-probe as the Phase-2 acceptance gate; pin the exact `kanban.py` create argv (or INSERT column tuple); direct-INSERT is the schema-probed fallback with a sunset. Closes **Craft + Feasibility + Market** (deduped — one gap, one owner). Owner: **design-technologist**.
2. **Decide the image-on-task path explicitly** — wire `task_attachments` or document the photo is dropped for tasks; no silent omission. Owner: **design-technologist** (mechanism) + **interaction-designer** (does the receipt say "photo not kept").
3. **Mechanize reversibility per path in the ADR** — Lucid = idempotent re-render; Hermes-task = register DELETE-triage-row as the tx inverse; Hermes-chat = honest inverse "none — disclosed in copy"; Claude = already correct. Owner: **interaction-designer**; confirm with **reversibility-tx-reviewer** (blocking Phase 2).
4. **Design the Shortcut stale-key recovery concretely** — a Shortcut can't self-reload like `staleReload()`; a 403 must deep-link the PWA to re-provision the key, with named plain-language copy. Owner: **interaction-designer** (flow) + **content-voice-designer** (copy).
5. **State the reduced-motion proposed-vs-executed contract** — reserved-warm + label is the primary differentiator; motion is enhancement only. One ADR sentence disarms the latent a11y cap. Owner: **interaction-designer** + **ui-accessibility-reviewer** (consult).
6. **Pull the revertible-Claude-proposal slice forward as shown-not-promised** — inert-file → `plan.json` → desktop-approve, execution stubbed. The only move that lifts market-fit 6→8+ and makes the doctrine *felt*. Owner: **interaction-designer** + **design-technologist**.
7. **[BLOCKED — Phase 3]** Define the `acting` ambient look — redundant-channel (luminance+spatial, never hue), reduced-motion clamp to still+word. *Blocker:* `derive_feed` never emits state 3 (verified — declared in `state_word`, no producer, no test) and the execution backend is unbuilt. Owner: **motion-designer** + **interaction-designer**, with **ambient-embodiment-reviewer**.
8. **[BLOCKED — Phase 3]** Spike the `claude -p` plan-only loop with a hostile caption — prove `--disallowedTools` + scoped cwd holds plan-only and the quoted-untrusted-data wrapping isn't escapable. Owner: **design-technologist**; gate with **security-reviewer** + **determinism-safety-reviewer**.
9. **Get the named-absent reviewers on record before Phase 3** — make this a **blocking ADR clause**, not a follow-up: responsible-ai-privacy-skeptic, security-reviewer, reversibility-tx-reviewer, resource-safety-reviewer, ux-reviewer.
10. **Ship Phase-0 prototype + record micro-decisions** — three-door chooser + resting frame + one receipt on the served bundle (proves calm restraint); record the sound-designer "disable iOS default tone" decision and the "`X-Share-Key` compromise ⊄ access without tailnet" sentence. Owner: **design-technologist** (build) + **content-voice-designer** (micro-copy) + **visual-systems-designer** (token/icon read).

**Top 3 to close next:** (1) pin the kanban contract — collapses the highest variance-masked
double-flag; (3) mechanize reversibility per path — the last point on the heaviest dimension;
(6) pull the Claude proposal-loop forward — the only lever on the lone market-fit outlier.

**Projected lift:** Craft/Feasibility 8→9 (#1), Vision-fit 9→10 (#3), Market-fit 6→8 (#6)
⇒ ~8.7 weighted, still short of 9 until the Phase-0 prototype (#10) proves the calm feel.

---

## 3. Market positioning (the chosen position)

**Category — refuse "AI share app," define "private phone→self-hosted-box ingest hub with
your own personal sinks."** On the AI-share shelf, "so it's just Lens but local?" loses.
The narrow category does the one thing Lens *structurally cannot* — route to *your* box,
*your* agents, reversibly. Costs explanation up front, wins the second-meeting question.

**Position statement:**
> For the technical self-hoster who already runs their own box (a 4090, a tailnet, Hermes
> installed) and wants their phone to feed *that* box instead of someone's cloud, AgentOS
> Share is a private phone→your-box ingest hub — and the moment a destination could *act*,
> every action is a reversible proposal you confirm on the box, processed provably on-machine,
> nothing ever leaving it. Unlike Apple Visual Intelligence / Google Lens (their cloud, their
> sinks) or transfer tools like Orange Share (bytes, no safety substrate), AgentOS treats
> untrusted phone input as **data, not instructions**, with the safety gate in *your harness*.

**The tension resolved (not split):** the *fan-out to your own sinks* is the **visible hook
(headline, legibility)** — a user understands it in one sentence; the **substrate underneath
is the moat (defensibility)** — reversible, human-approved, harness-gated agent execution.
A chooser + three POSTs is a weekend clone; the durable edge is one layer down. This mirrors
AgentOS's own proven arc (reactive wallpaper = visible hook; VRAM coordinator = moat).

**Three pillars (each maturity-tagged):**
1. **Provably on-box, not just private** `[PROVEN]` — verifiable at the loopback proxy; `agentosd-remote.sh` is the receipt (8642/8188 never exposed).
2. **Your destinations, chosen at share time** `[PROVEN` Lucid · `DESIGNED` Hermes-task/Claude`]` — the visible hook; honest maturity per sink.
3. **When it could act, you stay in control** `[DESIGNED`, lands on `PROVEN` substrate`]` — the moat; untrusted input as data, gate in your harness, every action a reversible proposal.

**Beachhead / first slice:** the technical self-hoster on their daily-driver; **phone photo →
Lucid dream** is the only fully-shipped sink and the winnable wedge. Ship the verb you can demo.

**Market-fit feedback:** Differentiation **8/10** (the wedge is genuinely empty; capped <9
because the *visible* differentiator is clonable and the durable part isn't yet legible to a
stranger). Defensibility **6.5/10 today → 9/10 at the proof gate** (the moat rests on
`DESIGNED`, not `PROVEN`, code). Honesty constraints to hold the message to: tailnet is the
auth (Shortcut bearer = second factor, never a vault); "coordinated, leased multimodal," never
"free local AI"; the act verb is roadmap-with-a-gate, never present-tense, until the inbox +
revert ship.

---

## 4. Signature delight moves (the beats to land)

**SM-1 — "The receipt that confirms it understood, not that it arrived."** *(highest conviction, the "one more thing")*
The field treats a share as transport ("Sent ✓"). AgentOS's ownable beat is the opposite:
the Shortcut fires silently; the only moment that lands is the PWA receipt at `/r/<id>`,
which **plays back the photo developing into its destination** using the shipped
`.aurora`/`develop` "the clip developing, not a spinner" hero (`theme.css:112-135`). For a
Dream, the receipt *is* the opening frame blooming in. For Hermes-chat, it mirrors back the
one sentence the vision model read ("a calm aurora over dark hills"). Felt: *"my box saw this,
it didn't just receive a file."* Zero new tokens — reuses shipped keyframes; impossible
without an on-box model, so structurally ownable, not clonable. **Make this the single
demoable signature of AgentOS Share.**

**SM-2 — "Time-travel on the Claude door" (the held proposal as the delight, not a risk dialog).**
The security design (phone POST → inert file → `plan.json` → desktop approve) is also the most
delightful interaction *if framed right*. The desktop approval shows the plan as the existing
**S2 "paths not taken"** ghost grammar (`theme.css:462-477`): the proposed action sits faint
and dashed; approving blooms it solid; declining leaves a revivable ghost. Felt: *"the agent
drafted something for me, and saying yes/no costs me nothing."* This makes the scariest path
feel safe and a little magical — ADR-0005 reversibility made tangible at the exact moment fear
would spike. Design it now (so security gate and delight are the same object); ships Phase 3.

**Earned microdelights:** the three-door resting frame breathes once on cold-launch then rests
(`gbloom`, transform-only, opacity-safe); the optional caption field rests with the serif
placeholder voice (skipping feels like the intended path); held-spool outage reuses the
`.qitem` queue grammar so an outage *looks like patience, not failure*; door icons carry the
destination's own resting texture (Dream = dashed `.gthumb`, Hermes = glass card, Claude =
dashed-future `.fcell`) — differentiation by texture+icon+label, never a second accent.

**Cut from this pass (failed restraint):** a desktop acknowledgement earcon (warm channel is
scarce by law; the device-local iOS receipt tone is the only recorded sound); any wallpaper
reaction to a share (forbidden — the field reacts only through `derive_feed`).

---

## 5. Recorded dissent (never erased)

- **design-technologist** — dissents from any MVP including the Claude-execution path; refuses an auto-pickup design (phone POST → `claude -p` executes without human approval). A trust-class boundary, not a feature toggle.
- **interaction-designer** — Phase 2 and Phase 3 should each carry their own human sign-off gate even after this ADR lands.
- **art-director** — will break from the brief if the three doors are ever color-coded (would break the one-warmth signal).
- **sound-designer** — "no acknowledgement sound" is acceptable but must be a *recorded* decision that disables the iOS default success tone, not silence-by-omission.

---

## 6. Prioritized next actions (the path to 10/10, in order)

1. **Pin the kanban write contract** (gap #1) — design-technologist. Unblocks Phase 2; lifts Craft + Feasibility.
2. **Mechanize reversibility per path in the ADR** (gap #3) — interaction-designer + reversibility-tx-reviewer. The last point on vision-fit.
3. **Pull the Claude proposal-loop slice forward, shown-not-promised** (gap #6) — interaction-designer + design-technologist. The only market-fit lever.
4. **Ship Phase-0 prototype** (gap #10) — three-door chooser + resting frame + SM-1 receipt-develop on the served bundle. Proves the calm feel; the score can't reach 9 without it.
5. **State the reduced-motion proposed-vs-executed contract** (gap #5) — disarms the latent a11y cap before it can arm at build time.
6. **Get the named-absent reviewers on record** (gap #9) as a blocking ADR clause before Phase 3.

---

## 7. Open questions for the human (options + recommendation)

1. **Hermes-task write mechanism.** Verified: no task-creation REST endpoint exists in `api_server.py`. Options: (a) parameterized `kanban.db` INSERT + fail-closed schema probe; (b) shell Hermes' own `kanban.py` CLI; (c) request a Hermes endpoint. **Recommendation: (b)** — honors don't-reinvent (call Hermes' own tool, not its file), with (a) as schema-guarded fallback; record as a temporary bridge with a sunset. Consult: ai-product-reviewer.
2. **Claude path in MVP, or its own ADR review gate?** **Recommendation: defer to Phase 3 behind its own review gate** (3 agents converge; design-technologist dissents against any earlier inclusion).
3. **Dedicated port now vs at graduation?** **Recommendation: ride 8765 for Phase 0–1; stand up `share_web.py` on its own port at the graduation trigger** (first write or first execution path).

**Required absent voices before Phase 3** (mediator does not fill these lanes):
responsible-ai-privacy-skeptic (named by 4 agents, did not weigh in), security-reviewer,
reversibility-tx-reviewer, resource-safety-reviewer, ux-reviewer.

---

## 8. ADR action

ADR-0027 already exists as a stub (`docs/adr/0027-agentos-share-phone-ingest-hub.md`,
Status: Proposed). This brief does **not** imply a *new* behavior change beyond it — it
refines the existing stub. The required ADR edits before ratification, all captured in the
gap plan above: (i) mechanize the reversibility inverse per path (gap #3); (ii) state the
reduced-motion proposed-vs-executed contract as a blocking clause (gap #5); (iii) make the
five named-absent reviews a blocking pre-Phase-3 clause (gap #9); (iv) pin the kanban write
mechanism once the human chooses (open question #1). No second ADR is needed; Phase 3 ships
behind its own review gate *within* ADR-0027.

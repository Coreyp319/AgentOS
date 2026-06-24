# ADR-0027 — AgentOS Share: phone → desktop ingest hub

Status: **Proposed** (Phase 0/1 built, live, and e2e-verified 2026-06-20 — the Lucid + Hermes-chat
doors and the Claude inert-proposal inbox — committed `85830b4`/`5bac7a6`/`6c91f20`; Phase 2
(Hermes-task) and Phase 3 (Claude execution) are sequenced behind the named blocking gates below).
Supersedes nothing; net-new. Companion design artifacts: the discourse brief
`docs/design/0027-agentos-share-ingest-hub-brief.md` and the council final (10/10 gap plan + market
position) `docs/design/0027-agentos-share-council-final.md`.

Maturity: Phase 0/1 shipped. The hub is a dedicated `apps/dreaming/lucid/lucid_share.py` (stdlib +
PIL on :8770, tailnet-only via `tailscale serve`; `test_lucid_share.py`, 21/21), with install units
in `integrations/share/` and the `:8770` exposure added to `agentosd-remote.sh`. The only edit to the
concurrently-rewritten `lucid_web.py` was the ~15-line `X-Share-Key` acceptance on `/api/start`.
Phase 2/3 remain the contract to honor before that code lands — this ADR is now the record of the
shipped slice plus the gates on the rest.

## Context
The user wants to share a photo (+ optional caption) from their iPhone into the agentic desktop,
choosing at share time among: (1) start a Lucid dream, (2) send to Hermes (chat OR new kanban
task), (3) hand to Claude Code (local watched inbox + headless `claude -p`). iOS Safari has no Web
Share Target API, so the share-sheet entry must be an iOS Shortcut; the in-app surface is an
installable PWA.

## Decision (to ratify)
1. **Three trust classes, not three peers.** Data→gated-renderer (Lucid), data→trusted-orchestrator
   (Hermes chat/task), instructions→actor-with-tools (Claude). Shipped in risk order.
2. **Hub placement: dedicated service from v0** (amended 2026-06-19, post-council). The council
   recommended extending `lucid_web.py` for Phase 0–1 and graduating to a dedicated
   `share_web.py` only at the first write/execution path. **This is overridden by an
   implementation reality the council did not have:** `lucid_web.py` (and the whole Lucid web
   subsystem) is under *active concurrent rewrite* by the ADR-0028 build (dream library +
   encrypted private stash). Hot-patching a file another agent is mid-rewrite on would clobber
   work. So the dedicated graduation is **pulled forward to v0**: the hub is a new
   `apps/dreaming/lucid/lucid_share.py` on its own port (**8770**), zero collision. This also
   happens to be the *safer* end-state the council wanted (code-execution must not share the
   NSFW-capable dream loop's CSRF/lifecycle boundary) — we just arrive there immediately. The
   only edit to the contended `lucid_web.py` is a ~15-line `X-Share-Key` acceptance on
   `/api/start` (the Lucid door), applied during a quiet window. Rust/agentosd remains the
   eventual home for the kanban bridge, not v0.
3. **iOS = Shortcut-primary, PWA-secondary.** Intent chosen in the share sheet; consequence and any
   approve/revert surfaces live in the PWA (deep-linked from the Shortcut's success notification).
   Shortcut is silent-fast (one native notification + device-local haptic/earcon).
4. **Hermes stays loopback; the hub proxies.** `:8642` never on the tailnet. Tailnet membership is
   the primary auth; per-process CSRF guards the PWA. A file-backed `X-Share-Key` is
   defense-in-depth on the Shortcut path, **explicitly not a security boundary**. Hermes'
   `API_SERVER_KEY` never leaves the box.
5. **Safety gates per class.** EXIF-strip every path (B2 is generation-only, stays on Lucid). Claude
   path: inert inbox file → plan-only `claude -p` in a scoped cwd, no network unless allowlisted,
   caption wrapped as quoted untrusted data → human approves on the desktop (verbatim prompt shown
   and labeled "from your phone") → execution inside the ADR-0005 tx. Auto-pickup refused by design.
6. **Honest-mapping locks.** Share events never drive the wallpaper directly (only `derive_feed`
   does). Routes are icon+label, single `--inst-blue` accent; `--inst-warm` is never spent on a
   route. Acks reflect comprehension, not transport; outages are honest-open, never "Sent ✓".

## Reversibility per path (ratification requirement — council gap #3)
Every destination declares its inverse explicitly; "reversible by default" is mechanized, not assumed:
- **Lucid (data → renderer):** inverse = idempotent re-render / delete the session via the existing
  `/api/delete` + burn path. Starting a dream mutates only a session sink the user already owns.
- **Hermes-chat (data → orchestrator):** **irreversible by nature** (a message is read). Honest
  inverse = "none — disclosed in the receipt copy." The receipt must say so plainly, not imply undo.
- **Hermes-task (data → orchestrator):** inverse = DELETE the just-created `status='triage'` row.
  The hub returns the task id; the receipt offers a one-tap "remove this task." Registered as the
  tx inverse before Phase 2 ships (confirm with reversibility-tx-reviewer — blocking Phase 2).
- **Claude (instructions → actor):** the *proposal itself* is the reversible object — an inert
  `plan.json` the human approves/declines on the desktop; declining leaves a revivable ghost;
  execution (if approved) runs inside the ADR-0005 tx. Nothing executes without desktop approval.

## Accessibility: reduced-motion proposed-vs-executed contract (ratification requirement — council gap #5)
The proposed-vs-executed distinction is carried by a **redundant, non-motion channel first**: a
**server-authored state word** ("Developing" / "Proposed") plus **luminance** — a cool-solid
*developed* hero versus a dashed-dim *held* "ghost" — **cool only**. Motion (the `develop`/bloom
hero, the S2 ghost) is *enhancement only*. Under `prefers-reduced-motion`, every state remains
legible as still-frame + word — no information lives in animation alone. The state word is full-ink
(it never inherits the held hero's dimming) and the disclosure copy clears WCAG 1.4.3 AA. This one
contract disarms the latent a11y cap the rating panel flagged.

> **Amended 2026-06-20 (post-build):** an earlier draft named "reserved-warm luminance" as the
> non-motion channel. That is **superseded** — §6 forbids spending `--inst-warm` on a route, and a
> green-light receipt is not a needs-you cue, so the channel is the **cool** state word + dashed-vs-
> solid luminance, never a warm hue. Implemented in `apps/dreaming/lucid/lucid_share.py`'s receipt
> and pinned by `test_lucid_share.py` (no `--warm` reaches any receipt route).

## Phased rollout
- Phase 0: capture surface + chooser → Lucid only.
- Phase 1: PWA enablement + Shortcut + Hermes-chat + `X-Share-Key` + EXIF-strip + held-spool degrade.
- Phase 2: Hermes-task bridge (deterministic, `status='triage'`, deletable) — gated on the
  task-write-mechanism question.
- Phase 3 (separate review gate): Claude inbox as a human-approved proposal queue.

## Consequences
- New inbound surface = new attack surface; mitigated by cloning the whole Lucid guard stack and by
  the trust-class staging. First phone→execution path in the project (gated, last, plan-first).

## Phase 0/1 security + privacy review (2026-06-20, advisory)
The shipped surface (`lucid_share.py`) was reviewed by `security-reviewer` + `responsible-ai-privacy-
skeptic` — the project's first inbound phone→box surface. **No CRITICAL findings**; the scary classes
are closed (Claude door provably inert — no `subprocess`/`exec`; no SSRF in the proxies; key never
logged or sent to the phone; receipt is a zero-photo/zero-caption capability page; EXIF/GPS stripped
on every path; B2 gate fails closed; `hmac.compare_digest`; strict CSP; no off-box egress). One
**material, convergent** finding and a cheap hardening cluster were **fixed in this slice** (tests
21/21):
- **Retention (HIGH):** the Claude door persisted the photo + caption with no expiry. **Fixed** — the
  inbox now self-expires (`INBOX_TTL`, default ~24h, swept on each write); a held share is no longer
  permanent on-disk PII.
- **Honest framing (MED):** the "held"/"never executes" copy read as "stores nothing." **Fixed** —
  the door + README now say the photo *is* stored on-box (auto-expiring) for later approval.
- **Hardening:** Origin **fail-closed** on the PWA token branch (verified against the real PWA flow —
  the iOS Shortcut key-path is unaffected); private `0700` dirs (mode-corrected); `O_EXCL` key
  creation (no cross-process key divergence with `lucid_web`); 30s socket timeout (Slowloris);
  systemd `IPAddressDeny=any`/`IPAddressAllow=localhost` (SSRF blast-radius cap) + `ProtectSystem`/
  `UMask=0077`/syscall-filter sandboxing.
- **Deferred (tracked):** the identical Origin-missing pass exists in the `lucid_web.py`
  `X-Share-Key` hook (`_authed`/`/api/start`) — **not fixed here** because that file is the
  concurrently-rewritten ADR-0028 tree; apply the same fail-closed fix in a quiet window so the Dream
  door isn't left with the weaker check. Also deferred (privacy follow-ups, not Phase-0/1 blockers):
  an inbox list/delete affordance + a first-run consent/retention line.

## Open / to resolve before ratification
- **`lucid_web.py` Origin twin-fix** (from the review above): make `_authed` on the Dream door's
  `X-Share-Key`/`/api/start` hook fail closed on a missing Origin, matching `lucid_share.py`. Held
  only to avoid clobbering the in-flight ADR-0028 rewrite of that file.
- **Hermes-task write mechanism** (pinned default, council gap #1): verified there is NO
  task-creation REST endpoint in `api_server.py`. **Chosen default: (b) shell Hermes' own
  `kanban` CLI** — honors don't-reinvent (call Hermes' own tool, not its sqlite file). Fallback:
  (a) parameterized `kanban.db` INSERT guarded by a fail-closed schema probe, marked a temporary
  bridge with a sunset. (c) requesting a first-party Hermes endpoint is the durable end-state.
  Phase 2 only; confirm the exact argv with the human + ai-product-reviewer before building.
- **BLOCKING pre-Phase-3 review gate** (council gap #9 — a ratification clause, not a follow-up):
  Phase 3 (the Claude execution path) MUST NOT ship until all five are on record:
  responsible-ai-privacy-skeptic, security-reviewer, reversibility-tx-reviewer,
  resource-safety-reviewer, ux-reviewer. The mediator does not fill these lanes.
- **Phase-3 security spike (blocking):** prove `claude -p --disallowedTools` + scoped cwd holds
  plan-only and the quoted-untrusted-caption wrapping is not escapable, with a hostile caption.

## Dissent (carried from the brief)
- design-technologist: dissents from any MVP including Claude-execution; refuses auto-pickup design.
- interaction-designer: each of Phase 2/3 should carry its own human sign-off gate.
- sound-designer: "no acknowledgement sound" is acceptable but must be a recorded decision (disable
  the iOS default tone), not silence-by-omission.

# Design brief — AgentOS Share (phone → desktop ingest hub)

Status: proposal-of-a-proposal. The hub, the `/api/share` endpoint, the iOS Shortcut, the
Hermes-task bridge, and the Claude inbox **do not exist in the repo yet**. The only built,
exposed, photo-from-phone surface today is the Lucid spike (`spikes/dreaming/lucid/lucid_web.py`,
stdlib `http.server` on `127.0.0.1:8765` behind `tailscale serve --https=8765`). This brief is
decision-ready toward ADR-0027; code disposes on the human's timeline.

Mediator: design-discourse-mediator. Date: 2026-06-19.

## Question
Design "AgentOS Share": share a photo (+ optional caption) from the user's iPhone to one of three
destinations chosen at share time — (1) start a Lucid dream, (2) send to Hermes (chat OR new
kanban task), (3) hand to Claude Code via a local watched inbox a headless `claude -p` picks up.
Resolve: hub placement, the iOS Shortcut + PWA flows, the three routing paths, auth/exposure,
safety/consent gates, the Claude-inbox sandbox, the phased MVP.

## Locked constraints (set by the user; not relitigated)
- iOS Safari has no Web Share Target API → the share-sheet entry is an iOS **Shortcut**; the
  in-app experience is an installable **PWA** (Add to Home Screen) + camera/photo picker.
- Hermes destination: chat-vs-task chosen **at share time**; a kanban bridge is needed (no
  task-creation REST endpoint exists today — verified, see "Contract drift").
- Claude destination: **local** inbox + headless pickup; no cloud.

## Participants (reconciled, by exact name)
art-director, motion-designer, visual-systems-designer, interaction-designer,
design-technologist, generative-artist, sound-designer, brand-identity-designer,
content-voice-designer, design-researcher.

## Decision (recommended direction)

### Trust-class framing (the spine — adopted from interaction-designer + design-technologist)
The three destinations are **NOT peers**. They are three trust classes and ship in risk order:
- **Data → gated renderer** (Lucid): safe. B2 likeness gate already live in `start()`.
- **Data → trusted orchestrator** (Hermes chat / task): safe-ish; Hermes owns its own auth.
  Chat is irreversible (can't unsend); task is deletable.
- **Instructions → an actor with tools** (Claude): dangerous. This is the lethal trifecta
  (tool access + untrusted input + sensitive scope). It is gated last, behind its own review.

### Hub placement (Q1) — staged, with a named graduation trigger
- **MVP slice (Phase 0–1): extend `lucid_web.py`** for Lucid + Hermes-chat ONLY. It is the only
  surface already on the tailnet, already minting per-process CSRF, already EXIF-stripping and
  bomb-guarding image decode, and already owning the photo safety gate. PWA enablement is ~3 lines
  (register `.webmanifest` in `_MIME`; `_serve_static` already serves `web/dist` traversal-safe;
  `vite base:'./'` already path-relative).
- **Graduation trigger (write into the ADR): the hub moves OUT of `lucid_web.py` into a dedicated
  `share_web.py` on its own `tailscale serve` port the moment EITHER the Hermes-task write path OR
  the Claude execution path ships.** Cloning Lucid's hardened harness verbatim (CSRF + Origin
  allowlist + `MAX_BODY`/decode-bomb guards + `_bind_server` single-owner takeover), never living
  inside the NSFW-capable dream process. This honors don't-reinvent (reuse the *pattern*, not the
  spike) and keeps the code-execution blast radius off the dream loop's CSRF token and lifecycle.
- agentosd (Rust) is the documented end-state for the kanban bridge, not v0 (an inbound HTTP
  server on the deliberately-tiny synchronous crate is a structural shift, not an increment).

### iOS flow (Q2) — Shortcut-primary, PWA-secondary
- ONE "AgentOS Share" Shortcut registered as a Share-sheet action (Accepts: Images). A native
  `Choose from Menu` is the entire informed-consent moment. Rows named by **outcome/intent**, not
  backend (content-voice-designer / brand-identity-designer own final wording):
  `Dream from this photo · Ask Hermes (chat) · New Hermes task · Hand to Claude Code`.
  Then an "Ask for Text" caption step (skippable) → one `Get Contents of URL` POST of
  `{dest, image_b64, caption}` to the tailnet HTTPS hub URL.
- The Shortcut is **silent-fast**: it fires and shows one native success/fail notification +
  haptic (sound-designer: device-local receipt earcon, NOT a new desktop sound). It does NOT
  render destination UX. For `dest:lucid` (and any consequential receipt) the success
  notification **deep-links into the PWA receipt** (`/r/<id>`), where the legible consequence and
  any revert/approve surfaces live. Intent in the sheet; consequence in the app.
- PWA = camera/photo picker for in-app capture + the home of all receipts and the Claude
  approve/revert surfaces. First paint must be `--inst-base`, never white (color-scheme:dark +
  base bg) so Add-to-Home-Screen launch is calm.

### Routing paths
- **Lucid:** reuse `/api/start` 1:1; B2 gate runs inside `start()`. On `blocked && requires_consent`
  the hub returns a `needs_consent` receipt; the in-surface consent card (the `Start.tsx` pattern,
  never a native `confirm()`) renders in the PWA. Hard block (possible minor) = non-overridable
  plain refusal. The firewall (a real-person/consent-gated dream is never set as wallpaper) must be
  enforced at the **promotion boundary**, not only at ingest (generative-artist).
- **Hermes chat:** hub creates session + posts the multimodal `image_url` data-URL message over
  loopback to `:8642`. Receipt deep-links a read-only thread mirror. Revert = "burn this thread."
- **Hermes task:** **deterministic, validated, parameterized bridge** — fixed columns
  (`title, body, status='triage', priority, assignee`), the model never composes SQL. Lands in the
  human-review column, deletable. See "Contract drift" for the sqlite-vs-CLI-vs-REST open question.
- **Claude:** writes an **inert** `request.json` + image to a watched inbox, state `proposed`.
  Nothing executes on arrival. `claude -p` runs read-only/plan-mode in a **scoped cwd** (never
  $HOME), no network unless allowlisted, the caption wrapped as quoted untrusted data, and produces
  a `plan.json` PROPOSAL. The human **approves on the desktop** (real Plasma/swaync dialog, not a
  phone toast — verbatim untrusted prompt shown and labeled "from your phone"). Approval runs the
  action inside the ADR-0005 tx (begin → ops → commit|rollback). "Allow once," never "Always allow."

### Auth & exposure (Q3)
- Hermes `:8642` stays **loopback**; the hub proxies it. Never added to `agentosd-remote.sh`
  `PORTS` (verified: 8642/8188 deliberately excluded). The MVP rides 8765; on graduation the new
  port is added to `PORTS`.
- Tailnet membership is the **primary** auth (device-is-identity); per-process CSRF guards the PWA.
- The Shortcut **cannot hold a real secret** (a baked-in bearer is readable/exportable). So a
  file-backed stable `X-Share-Key` (`$XDG_RUNTIME_DIR/nimbus-aurora/share.key`, `0600`, generated
  once, accepted only on `/api/share` in addition to CSRF) is **defense-in-depth on top of the
  tailnet, NOT a security boundary** — the ADR must say so explicitly, and must note the stale-key
  recovery UX (a rotated key 403s a Shortcut that "appears dead", mirroring the Lucid stale-CSRF
  trap). Hermes' `API_SERVER_KEY` stays server-side only; never on the phone.

### Safety / consent (Q4)
- B2 is a **generation** gate, not an ingest gate. Don't bolt it onto chat/Claude; bolt the
  **EXIF-strip** onto every path (GPS/identity metadata never leaves the box). Reuse `_decode_seed`.
- Per-destination retention/deletion stated honestly in copy (content-voice-designer): photo not
  stored by the hub after send; chat lands in Hermes memory (disclose); "Pause the Claude inbox"
  shipped beside the enable. Consult responsible-ai-privacy-skeptic on the retention/echo claims.

### Honest mapping (cross-lane locks)
- A share/ingest event MUST NOT drive the reactive wallpaper directly (generative-artist). The
  field reacts only to `agent.json` from `derive_feed`. A share becomes visible in the field only
  once it is real agent work (task → working/busy; needs-you → warm) and flows through the existing
  feed. Acknowledgement of receipt belongs to the share UI and/or the keyhole, never the wallpaper.
- Routes are differentiated by **icon + label only**; the single accent stays `--inst-blue`. The
  Claude execute-route confirmation reuses the consent-card grammar with `--brand-warm`;
  `--inst-warm` (`#ff9957`, the reserved wallpaper "needs you" hue) is never spent on any route
  (visual-systems-designer + brand-identity-designer). Doors are never color-only.
- Acks reflect **comprehension, not transport** (brand-identity-designer). "Sent ✓" on a dropped
  share (Hermes unreachable) is the outage-wearing-serenity lie — fail-open must be honest-open
  (degrade to a held spool + "saved, will send when reachable", reusing ADR-0019's pattern).
- The Claude action must look unmistakably different from chat: proposed-vs-executed is a motion +
  reserved-warm contract (motion-designer), and undo must never show a "rewind" the substrate
  can't perform — which is why the action is held in the proposed state until confirmed.

## Phased MVP (smallest correct first slice)
- **Phase 0 (taste + spine, zero execution risk):** resting capture surface + three-door chooser
  as static React on the served bundle, wired to the **Lucid door only** (`/api/start`, B2 live).
- **Phase 1:** PWA enablement (manifest + apple-touch-icon + shell `sw.js`) + the iOS Shortcut
  deep-linking the Dream door + Hermes-chat (loopback proxy). One file-backed `X-Share-Key`.
  EXIF-strip every path. Held-spool degrade for Hermes-unreachable.
- **Phase 2:** Hermes-task bridge (deterministic insert, `status='triage'`, Undo/delete) — gated
  on resolving the sqlite-vs-CLI-vs-REST question below.
- **Phase 3 (separate ADR review gate):** Claude inbox as a **human-approved proposal queue**
  (ADR-0019 board pattern), scoped-cwd `claude -p`, plan-only first, then approved execution in the
  ADR-0005 tx. Auto-pickup of a phone-POSTed prompt is refused by design.

## Accepted tradeoffs
- The Shortcut UI is system chrome we can't art-direct; taste lives in the hub receipt + resting
  capture frame. The Shortcut secret is not a boundary (tailnet + 2FA carry it).
- Routes share one accent (no per-destination color) — loses instant brand-color recognition,
  keeps the one-warmth signal honest and accessibility intact.
- Chat is irreversible and the copy says so plainly (honesty over warmth).
- MVP intentionally ships fewer destinations (no task, no Claude) to ship the calm photo-sharing
  win without standing up a write path or an execution surface on day one.

## Recorded dissent
- **design-technologist** dissents from any MVP that includes the Claude-execution path, and from
  any Claude design that auto-executes a phone-POSTed prompt without a human-approval step. Escalate
  if the panel ships Claude-exec in slice 1 — a trust-class boundary, not a feature toggle.
- **interaction-designer** dissents toward stronger gating: Phase 2/3 should each carry their own
  ADR section with an explicit human sign-off even after ADR-0027 lands.
- **art-director** would split from the brief if the three doors get color-coded (would break the
  one-warmth signal); records this as a line to hold, not a present conflict.
- **sound-designer** one-line dissent: if the panel wants NO acknowledgement sound, that is also
  calm and defensible — but it must be a recorded decision that disables the iOS Shortcut's
  built-in success tone, not silence-by-omission that lets the default chime in.

## Open questions for the human (options + recommendation)
1. **Hermes-task write mechanism.** Verified: `api_server.py` has NO task-creation REST endpoint
   (only background-task asyncio plumbing). Three options: (a) direct parameterized INSERT into
   `kanban.db` with a startup schema-probe that fails closed on drift (precedent: `feed.rs`
   read-only fleet read + `FLEET_COLUMNS` probe); (b) shell the Hermes-owned `kanban.py`/
   `kanban_db.py` CLI; (c) wait for / request a Hermes task endpoint. Recommendation: **(b) the
   Hermes-owned CLI** — honors don't-reinvent (call Hermes' own tool, not its file), with (a) as a
   schema-guarded fallback only if the CLI proves unsuitable. Defer the final call to whoever can
   confirm the CLI's contract; record it as a deliberate, temporary bridge with a sunset to a real
   endpoint. Owner to consult: ai-product-reviewer (is sqlite-direct acceptable under ADR-0001).
2. **Claude path in scope now, or deferred to its own ADR?** Recommendation: **defer to Phase 3
   behind its own review gate** (design-technologist + interaction-designer + design-researcher all
   converge here, with recorded dissent against including it in MVP). The MVP is Lucid + Hermes.
3. **Dedicated port now vs at graduation?** Recommendation: ride 8765 for Phase 0–1; stand up
   `share_web.py` on its own port at the graduation trigger (first write or first execution path).

## Voices to consult (not present; mediator does not fill these lanes)
- **responsible-ai-privacy-skeptic** — REQUIRED before Phase 3 and for the retention/echo/
  injection-legibility claims (was named by 4 agents; did not weigh in here).
- **ux-reviewer** — heuristic pass on the Claude proposal-review surface and the
  Shortcut→PWA deep-link reliability (does the success notification land on the right receipt or
  cold-open the home tab on iOS).
- **reversibility-tx-reviewer** — confirm "undo a share" is a tx op and the Claude action is one
  revertible tx unit; co-owns the Claude tool-allowlist scope.
- **resource-safety-reviewer** — confirm the share path adds no new VRAM holder and the Claude run
  goes through the lease if it touches the GPU.
- **security-reviewer** — the new exposed behavior, the shared-secret, and the execution path.

## Relevant paths
- `/home/corey/Documents/AgentOS/spikes/dreaming/lucid/lucid_web.py` (hub host: CSRF :63, Origin
  allowlist :52, `_MIME` :56-60, `MAX_BODY`/bomb guards :67-69)
- `/home/corey/Documents/AgentOS/spikes/dreaming/lucid/web/src/Start.tsx` (consent-card + B2_NOTE)
- `/home/corey/Documents/AgentOS/spikes/dreaming/lucid/web/src/theme.css` (the register to inherit)
- `/home/corey/Documents/AgentOS/integrations/design/instrument-tokens.md` (canonical tokens)
- `/home/corey/Documents/AgentOS/crates/agentosd/src/feed.rs:124-145` (read-only kanban + schema
  probe — the bridge precedent)
- `/home/corey/Documents/AgentOS/integrations/agentosd-remote.sh:16,49` (PORTS; 8642 excluded)
- `/home/corey/Documents/AgentOS/docs/REMOTE-ACCESS.md:141-168` (tailnet-as-auth, ACL)
- `/home/corey/Documents/AgentOS/docs/adr/0019-reviewable-request-queue.md` (held/human-disposes)

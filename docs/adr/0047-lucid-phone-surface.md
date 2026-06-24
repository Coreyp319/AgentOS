# ADR-0047 — Lucid on a phone: a mobile-first PWA thin client over Tailscale

- Status: **Proposed** (2026-06-23). No build beyond the de-risking spike yet
  (`spikes/lucid-notify/`, 17/17 — the async notify leg). This ADR records
  the decision a 7-lens persona panel converged on: an "app version of Lucid" is a
  **mobile-first PWA thin client to the user's own box**, not on-device generation,
  not a native binary, not a cloud SaaS.
- Date: 2026-06-23
- Relates to: **ADR-0001** (substrate, not a product — this is a *consumer* of the
  substrate, the cap basis for rejecting Option D); **ADR-0046** (graduate live
  products from `spikes/` into `apps/` — this phone surface lands in the Lucid
  product home, never in `crates/agentosd`); **ADR-0031** (launch-surface PWA-over-
  Tailscale — inherits its `serve`-never-`funnel` constraint, origin-aware doors,
  loopback-bind guard); **ADR-0027** (phone ingest hub — inherits tailnet-as-auth,
  per-process CSRF, EXIF-strip, per-path inverses); **ADR-0016** (private ephemeral
  mode — the "local ≠ private" honest-storage caveat); **ADR-0028** (encrypted
  stash); **ADR-0045** (anticipatory pre-warm — extended with a phone-open trigger);
  **ADR-0014/0023/0025/0032** (the dream loop + choice/annotation interaction being
  re-housed for touch). Extends ADR-0027/0031; supersedes nothing.
- Design input: 7-lens panel (ai-product, market-differentiation, interaction,
  responsible-ai-privacy-skeptic, rater-vision-fit 8/10, rater-feasibility 8/10,
  ai-generation-reviewer) over a research pass on Apple/Android mid-2026 on-device AI.

## Context
The question was "can we make an app version of Lucid that uses the local hardware
Apple/Google just opened up?" The research settles the premise:
- **A phone cannot render Lucid's video locally — categorically, not waitable-out.**
  Lucid's i2v is a 14–19 GB Wan/LTX model (4.5–12 min/clip on the 4090). What Apple
  (Foundation Models, WWDC 2026) and Google (Gemini Nano / LiteRT, I/O 2026) "opened
  up" is on-device **text LLMs** and **still-image** generation — **no video**.
  On-device phone video is a ~2 s, low-res, ~3.5 GB *research demo*, not a product.
- A **thin client already half-exists**: the Lucid React SPA already ships a PWA
  manifest + icons + portrait/mobile CSS, reachable over `tailscale serve`
  (`agentosd-remote.sh`, `LUCID_EXTRA_ORIGINS`); `lucid_share.py` ingests phone photos.
- Lucid has a **Mature 18+ tier**, which Apple/Google ban store-wide regardless of
  where compute runs — but a **PWA served from your own box over your own tailnet
  never enters a store**, so it is policy-immune by construction.

Four options were on the table: **A** PWA thin client to your own box · **B** hybrid
on-device-LLM narrative + streamed video · **C** fully on-device degraded (no video)
· **D** cloud-GPU SaaS.

## Decision
1. **Build Option A. It is the smallest, most buildable, most vision-aligned, and
   most defensible option simultaneously.** The deliverable is **PWA-ifying the
   existing Lucid SPA** (service worker + mobile ergonomics + the async notify leg),
   *not* a net-new client. The phone **directs** (pick branch, type/refine a beat,
   annotate) and **reviews** (watch clips, navigate the tree); every pixel still
   renders on the user's 4090 over the tailnet.
2. **Reject Option D outright (hard cap).** A hosted GPU SaaS is "build a product" —
   the exact thing ADR-0001 rejects — abandons local-first, re-acquires the store
   NSFW ban it was the point of dodging, and is a different company. Not a Lucid
   roadmap item.
3. **Reject Option C as a product.** Stripping the video ships a branching-text-with-
   stills app — the commoditized lane Lucid is weakest in — and abandons the white
   space (private, your-hardware, branching *video*). Keep only its graceful-
   degradation instinct: when the box is unreachable, the PWA opens to the **library
   of already-rendered dreams** + an honest "box offline" state, never a fake local
   generator.
4. **Defer Option B's on-device narrative; it does not earn its complexity, and its
   cloud-fallback rider is a non-negotiable violation.** The box's narrator (MN-12B,
   tuned + mature-directive + JSON-reliable) is *better* than any phone's 3–4 B
   on-device model and is reachable at LAN latency over the tailnet. On-device
   authoring would **break the Mature tier** (phone models' own safety alignment
   softens/refuses explicit beats), **fragment the narrator's voice** mid-dream,
   need a second per-platform JSON contract, and — under model-proposes/code-disposes
   — produce a draft the box must re-author anyway. **All narrative authoring is
   box-only.** When the box is unreachable, **queue intent** (a picked card is an id;
   a typed idea is a string — no model needed); the box authors + gates + renders on
   reconnect. On-device models get exactly one non-generative job: rendering cached
   menus and echoing the user's typed draft so the *wait* feels alive.
5. **The "instant feel" comes from the box, not the phone.** Extend ADR-0045's
   anticipatory pre-warm with a **phone-open trigger** (the foreground event is a
   strong predictive signal) + optimistic/skeleton menu UI, so a warm MN-12B over the
   tunnel paints choices fast — without a second model, voice fragmentation, or a
   crypto/native-shell tax.
6. **The async notify leg (the one net-new thing) is Telegram-first.** A render takes
   minutes and the phone is backgrounded/asleep; the loop closes only by reaching a
   non-foreground phone. **v1 = Telegram-via-Hermes** (already live, reaches a closed
   app, zero new dependency, creds stay on the box). **v2 = web push** only behind an
   explicit decision to adopt the project's first crypto dependency
   (`cryptography`/`pywebpush` for RFC 8291 + VAPID) and ship a service worker. See
   the spike.
7. **The Mature tier selects the architecture: PWA-over-your-own-tailnet only, no
   app-store binary, ever.** A native/store build silently forfeits the Mature tier
   (and re-acquires the store policy war). Recorded as an invariant, not a footnote.

## Non-goals (re-asserted)
- No on-device video/image/narrative **generation** (impossible / a downgrade / breaks
  the Mature tier — §1, §4).
- No second/parallel phone-specific front-end codebase — PWA-ify the existing SPA (§1).
- No app-store or TestFlight binary while the Mature tier exists (§7).
- No cloud render path, ever — not even an "asleep-box" convenience fallback
  (privacy blocker, below).
- No caching of dream content in the service worker / Cache API (app-shell only).
- No new daemon or port for the notifier — it rides the existing `TURN` lifecycle and
  the existing `tailscale serve` exposure.

## Reversibility per path (ADR-0027 discipline, restated — not inherited by vibe)
- **Start/continue a dream (data → renderer):** inverse = the existing `/api/delete`
  + burn path; mutates only a session sink the user owns.
- **Refine / queue a beat:** inverse = revert the beat (append-only fork boundary,
  ADR-0014/0025 — a queued, not-yet-rendered node is a clean append-removal; never a
  sibling mutation). Cancelling a queued branch before render must be append-removal.
- **Set-as-wallpaper from the phone:** stays **box-only and disabled in private**
  (ADR-0016:38), and still routes the ADR-0005 tx; the phone proposes, the box disposes.
- **"Burn on this device":** a new phone-side inverse — clears the PWA's own caches/
  IndexedDB, wired to the box burn so one action clears both ends (see blocker #3).
  Confirm with `reversibility-tx-reviewer` before crediting.

## Privacy non-negotiables (responsible-ai-privacy-skeptic — must ship or do not ship)
A phone is glanced-at and lock-screened in ways a desktop is not. Four mitigations are
non-negotiable for the private/mature tier; the first three are **mechanized in the
notify spike** (`notify.py`, 17/17):
1. **Content-free notifications.** No dream title, beat text, or thumbnail in any push
   payload (it routes through APNs/FCM/Telegram servers *and* renders on a lock
   screen). Payload = app name + a fixed generic line + a deep link carrying only an
   opaque node id; the body is fetched after unlock. Telegram sends with
   `disable_web_page_preview`. Enforced by `assert_content_free()` / `_safe_payload()`.
2. **No off-box egress for private/mature.** Never PCC/AICore/cloud-render. Narrative
   pins to the box's Ollama; the asleep-box path **wakes the box, it does not reroute
   to a cloud**. (This is the other reason B and D die.)
3. **No phone caching of private/mature clips.** Stream-only, `no-store`, non-backup-
   eligible; the service worker caches the app shell only, never dream content. Plus
   a "burn on this device + box" that clears both ends — otherwise "delete on box"
   leaves residue in the phone's media cache and iCloud/Google backups.
4. **Private/mature push is default-deny.** `should_notify()` refuses private outright
   and mature unless explicitly opted in (still content-free).
- **Pre-existing, sharpened on a shared phone (tracked, not blockers for the PWA
  shell):** the Mature tier has no real age gate (ADR-0017 owes the CV face detector;
  the red-line is a thin regex deny-list); `/api/state` ships `name`/`rating`/`private`/
  `stash.exists` to the tailnet — gate behind an unlocked, foregrounded session.

## The async notify leg — spike results (`spikes/lucid-notify/`)
- **Fires on the `TURN.phase -> "done"` edge** (`lucid_web.py` ≈228/262/342) — one call
  site, no second producer.
- **Web push is dep-walled here:** RFC 8291 needs P-256 ECDH + ES256 (i.e.
  `cryptography`/`pywebpush`), neither installed; the project is stdlib-only. The
  transport reports this honestly rather than no-op'ing.
- **Telegram-via-Hermes is the recommended v1 primary:** reaches a fully-closed app,
  cross-platform, zero new dependency, creds stay on the box. Dry-run by default.
- **17/17 tests** cover the content-free invariant, private/mature suppression,
  transport selection/fallback, and the dep-wall.

## Box-wake policy (decision D1)
`tailscale serve` is dead if the box suspends. v1 declares an **always-on-while-dreaming
prerequisite** (`systemd-inhibit` around an active session, or a "keep awake while
dreaming" toggle) — cheap and honest. Wake-on-LAN over the tailnet is a real v2; it must
not gate v1. A notification only ever fires for a clip that landed (box was awake).

## Phased rollout
- **Phase 1 (the MVP):** service-worker install + mobile ergonomics (choice gutter →
  bottom sheet; git-graph tree → vertical filmstrip spine, swipe-between-takes) +
  opens-to-library-when-offline + the Telegram notify leg + the phone-open pre-warm
  trigger. Lands in `apps/dreaming` once ADR-0046 executes the `spikes/ → apps/` move.
- **Deferred:** web-push (v2, behind the crypto-dep decision); WoL (v2); on-device
  narrative (only if a named "plan while box is off" request appears, and only after
  ai-generation-reviewer re-confirms the juncture needs a model — current answer: no).

## Consequences
- A real, differentiated product — "the private remote control for the generative rig
  you already own; made on your machine, watched on your phone" — for roughly a UX-
  polish pass on assets that already exist. Honest TAM: a niche-of-a-niche (self-hosters
  with a 24 GB+ GPU), high willingness-to-pay, zero per-gen cost; a viable indie scope,
  not venture-scale. The moat (private-by-construction + un-metered desktop-grade video)
  is one a cloud incumbent cannot copy without cannibalizing its own unit economics.
- The honest message — **"made on your machine, watched on your phone"** — must ship
  *with* the storage truth ("rendered and stored on your box; private dreams are
  ephemeral", ADR-0016) so it can't be misheard as a blanket privacy claim.

## Open / to resolve before ratification
- **iOS PWA verification (only the phone can settle it):** run `serve_demo.py` over
  `tailscale serve`, install to home screen, confirm SW + permission + `showNotification`
  on the real device. Decide web-push-vs-Telegram for the "no Telegram account" user.
- **Onboarding:** fold tailnet-name discovery + the `LUCID_EXTRA_ORIGINS` systemd
  drop-in into the onboarding wizard (ADR-0044) so a non-technical user never hand-edits
  a unit; design the QR/six-word pairing + "the box answers back" liveness handshake.
- **Phone-app lock:** today the surface is tailnet-auth only (no per-user identity); add
  an optional biometric/passphrase app-lock for the lost-phone case (an NSFW-capable
  surface on an unlocked phone).
- **Confirm with:** `reversibility-tx-reviewer` (phone inverses incl. burn-on-device),
  `resource-safety-reviewer` (phone-open pre-warm must stay lowest-priority/instantly-
  reclaimable under the i2v lease; queued-intent reconcile must be append-only),
  `security-reviewer` (push transport + SW cache-control mechanics).

## Recorded panel verdict
- **rater-vision-fit: A 8/10** (no hard cap; gating delta = this ADR + reuse-the-SPA +
  fence on-device additive-only). **B 4/10** (soft-capped: cloud fallback). **D 2/10**
  (hard cap: ADR-0001 + local-first).
- **rater-feasibility: A 8/10** (smallest = highest-scoring; the only load-bearing
  unknown is the async-notify leg, de-risked by the spike + the Telegram fallback).
- **ai-generation-reviewer:** on-device narrative does not earn its complexity; route
  all authoring to the box; on-device is a non-generative responsiveness shell only.

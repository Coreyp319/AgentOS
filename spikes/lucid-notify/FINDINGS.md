# ADR-0047 spike — the async "your dream is ready" notify leg

**Date:** 2026-06-23 · **Status:** spike (throwaway; `spikes/` is excluded from the build)

The one genuinely net-new, unspiked leg of a Lucid phone app is **how a finished
render reaches a phone that is backgrounded or asleep**. A clip takes 4.5–12 min
on the box; nobody stares at a spinner for that. This spike de-risks that leg and
returns a decision.

## What this spike contains

| File | What it proves |
|---|---|
| `notify.py` | The transport-agnostic notifier fired on the `TURN.phase -> "done"` edge. Mechanizes the two privacy blockers. Runnable: `python3 notify.py --demo`. |
| `test_notify.py` | 17/17 — content-free invariant, private/mature suppression, transport selection, dep-wall honesty. |
| `sw.js` | The minimal service worker the web-push path needs (Lucid has none today). Content-free; caches no dream content. |
| `serve_demo.py` | The on-phone test harness — the ONLY part that must run on Corey's real device (iOS PWA push reliability can't be settled from docs). |

## Finding 1 — Web push cannot run on this box without a dependency the project rejects

Web Push (RFC 8291) requires **P-256 ECDH + HKDF + AES128GCM** payload encryption
and an **ES256 VAPID JWT**. That is `cryptography` / `pywebpush` — **neither is
installed**, and the project is deliberately stdlib-only (the private stash uses
stdlib scrypt+blake2b rather than pull in a crypto lib, ADR-0028). `notify.py`'s
`WebPushTransport.available()` reports this honestly rather than no-op'ing:

```
webpush  unavailable — requires `pywebpush` (+`cryptography`) — NOT installed; project is stdlib-only
```

So web push is not a "wire it up" job — it's "adopt the first crypto dependency in
the project, generate + persist a VAPID keypair, ship a service worker, and accept
flaky iOS delivery." That's a real cost, not a footnote.

## Finding 2 — Telegram-via-Hermes is the pragmatic primary, and it's already live

Hermes' Telegram stack is configured on this box (`display.platforms.telegram.streaming
= True`). The Telegram path:

- **reaches a fully-closed app** (the thing iOS polling cannot do), cross-platform, identically;
- needs **zero new dependency** (stdlib `urllib`), **no service worker, no VAPID, no
  install-to-home-screen**;
- carries the bot token + chat id **on the box only** — never sent to the phone.

`notify.py`'s `TelegramTransport` is dry-run by default (it prints the API call it
*would* make) so the spike never emits a real message without an explicit `send=True`
+ creds. **Recommendation: Telegram is the v1 primary; web push is a v2 "native feel"
upgrade gated on accepting the crypto dep.**

## Finding 3 — the privacy blockers are mechanized, not documented

Both of the responsible-ai-privacy-skeptic's load-bearing blockers are enforced in code:

- **Content-free payloads (blocker #1).** Every payload passes `_safe_payload()` /
  `assert_content_free()`, which raise `ContentLeak` on any key that could carry a
  dream title, beat text, prompt, caption, frame, or thumbnail. The payload is
  literally `{title:"Lucid", body:<fixed string>, url:<base>/?node=<opaque id>}`.
  Telegram sends with `disable_web_page_preview` so the deep link never expands into
  a frame preview. `sw.js` renders only the constant app name and sets no `image`.
- **Private/mature do not push by default (blockers #2/#3).** `should_notify()` is
  default-deny: private → never; mature → only with an explicit opt-in (still
  content-free); asleep-box → nothing landed to notify about.

Demo output (private and mature both correctly suppressed):

```
=== a private dream clip landed (must NOT push) ===
  -> none: ok=False suppressed: private dreams never push (ephemeral, blocker #3)
=== a mature dream, no opt-in (must NOT push) ===
  -> none: ok=False suppressed: mature dreams do not push unless explicitly opted in (blocker #2)
```

## Finding 4 — the box-asleep problem is a policy decision, not a feature to build (D1)

`tailscale serve` is dead if the box suspends, so a queued beat never renders and no
notification can fire. There is no wake/inhibit handling in the repo today. The honest
v1 answer is a **declared prerequisite, not a wake feature**: the box stays awake while
a dream is in flight (`systemd-inhibit` around an active session, or a "keep awake while
dreaming" toggle). Wake-on-LAN over the tailnet is a real v2, but it's finicky and
should not gate v1. Either way: **a notification only ever fires for a clip that landed,
i.e. while the box was awake** — `should_notify()` encodes exactly that.

## The integration point (when this graduates out of the spike)

In `lucid_web.py`, the worker functions set `TURN.update(phase="done", ...)` (≈ lines
228 / 262 / 342) on a completed render. That edge — idle/dreaming → `done` — is the
single call site:

```python
# on the TURN.phase -> "done" transition, for the just-finished node:
notify_on_done(
    DreamMeta(dream_id=chain.id, private=chain.private, rating=chain.rating, box_awake=True),
    dream_grew(chain.id, node.id),
    transports=[TelegramTransport(*hermes_telegram_creds()), WebPushTransport(...), LogTransport()],
    base_url=tailnet_base_url(),
)
```

No second producer, no new daemon — it rides the existing turn lifecycle.

## The one thing only Corey's phone can settle

Run `serve_demo.py` on the box, expose `:8791` via `tailscale serve`, open the tailnet
HTTPS URL on the iPhone, **Add to Home Screen**, launch, grant notification permission,
tap "Fire test". That proves SW registration + permission + `showNotification` on the
real device. The harness deliberately **cannot** notify a *fully-closed* iOS PWA (that
needs real web push / APNs / the crypto dep) — so you *feel* the boundary that makes
Telegram the right v1 primary.

## Recommendation (feeds ADR-0047 §Decision)

1. **v1 notify = Telegram-via-Hermes** (zero new dep, reaches a closed app, already live).
2. **v2 = web push** behind an explicit decision to adopt `cryptography`/`pywebpush` +
   ship `sw.js` + persist a VAPID keypair, only if "native notification without a
   Telegram account" proves worth it.
3. **Box-asleep = declared always-on-while-dreaming prerequisite** (`systemd-inhibit`),
   WoL deferred to v2.
4. **The content-free + private/mature-suppressed invariants are non-negotiable** and
   ship mechanized (this spike) wherever the notifier lands.

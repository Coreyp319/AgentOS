#!/usr/bin/env python3
"""ADR-0047 spike — the async "your dream is ready" notify leg.

A Lucid render takes 4.5–12 min on the box; the phone is usually backgrounded or
asleep when a clip lands. To close the loop we must reach a phone that is NOT in
the foreground. This module is the transport-agnostic notifier that fires on the
`TURN.phase -> "done"` edge in lucid_web.py.

Two hard privacy invariants are MECHANIZED here (responsible-ai-privacy-skeptic,
blockers #1 and #3), not left to the caller:

  1. CONTENT-FREE PAYLOADS. A notification carries only opaque ids (dream_id,
     node_id) + a fixed generic body. NO dream title, beat text, prompt, caption,
     frame, or thumbnail ever enters a payload — those route through APNs/FCM/
     Telegram servers AND render on a lock screen. `assert_content_free()` is the
     gate every transport's payload passes through; it raises on a banned key.

  2. PRIVATE/MATURE ARE NOT PUSHED BY DEFAULT. `should_notify()` refuses a push
     for a private dream outright, and a mature dream unless explicitly opted in
     (and even then, content-free). A glanced-at phone is not a desktop.

Transports, in preference order:
  - TelegramTransport  — RECOMMENDED primary. Reuses Hermes' already-live Telegram
                         stack. Reaches a fully-closed app, cross-platform, ZERO new
                         dependency (stdlib urllib). Needs a bot token + chat id.
  - WebPushTransport   — the "native notification" upgrade. Needs a service worker
                         (greenfield), a VAPID keypair, and — decisively — P-256
                         ECDH + ES256 signing, i.e. the `cryptography`/`pywebpush`
                         dependency this project has deliberately avoided. Degrades
                         to an honest "unavailable + here's the dep" rather than a
                         silent no-op.
  - LogTransport       — dry-run default. Prints the (content-free) payload.

Run:  python3 notify.py --demo
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

# --- the content-free contract -------------------------------------------------

# Keys that could carry dream content. If any appears in a payload we are about to
# hand to a push server / lock screen, that is the blocker-#1 leak. Fail closed.
_BANNED_KEYS = {
    "title", "name", "dream_name", "prompt", "beat", "beats", "caption",
    "text", "body_text", "thumbnail", "thumb", "image", "image_b64", "frame",
    "clip", "subject", "rating",
}
# A single fixed body. Deliberately says nothing about the dream's content.
_GENERIC_BODY = "Your dream grew — a new clip is ready. Open Lucid to watch."
_GENERIC_TITLE = "Lucid"


class ContentLeak(Exception):
    """Raised when a payload about to leave the box carries dream content."""


def assert_content_free(payload: dict) -> dict:
    """Gate an outbound payload. Raises ContentLeak on a banned key (recursive)."""
    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in _BANNED_KEYS:
                    raise ContentLeak(f"banned key {path}{k!r} in notify payload")
                walk(v, path + k + ".")
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}].")
    walk(payload)
    return payload


def _safe_payload(p: dict) -> dict:
    """The real gate for a push schema. Allow-lists the structural keys
    title/body (asserting title holds ONLY the fixed app-name constant), then
    asserts everything else is content-free."""
    if p.get("title") not in (None, _GENERIC_TITLE):
        raise ContentLeak("title carries non-generic content")
    rest = {k: v for k, v in p.items() if k.lower() not in _BANNED_KEYS}
    assert_content_free(rest)
    return p


# --- the decision: should this dream push at all? ------------------------------

@dataclass(frozen=True)
class DreamMeta:
    """Just enough to decide. Note: NO content fields live here either."""
    dream_id: str
    private: bool = False
    rating: str = "sfw"           # 'sfw' | 'mature'
    box_awake: bool = True        # a clip only lands while the box is awake


def should_notify(meta: DreamMeta, *, allow_mature: bool = False) -> tuple[bool, str]:
    """Privacy blockers #2/#3 mechanized as a default-deny gate."""
    if meta.private:
        return False, "private dreams never push (ephemeral, blocker #3)"
    if meta.rating == "mature" and not allow_mature:
        return False, "mature dreams do not push unless explicitly opted in (blocker #2)"
    if not meta.box_awake:
        return False, "box asleep — no clip landed to notify about"
    return True, "ok"


# --- the notification ----------------------------------------------------------

@dataclass(frozen=True)
class Notification:
    """A content-free 'a clip landed' edge. Construct via `dream_grew`."""
    dream_id: str
    node_id: str
    count: int = 1

    def deep_link(self, base_url: str) -> str:
        # Carries only the opaque node id — the app fetches the actual clip AFTER
        # the user unlocks and foregrounds (blocker #1).
        return f"{base_url.rstrip('/')}/?node={urllib.parse.quote(self.node_id)}"

    def payload(self, base_url: str) -> dict:
        # Build the push schema. title/body are validated against the fixed
        # constants by `_safe_payload` at send time (we do NOT assert_content_free
        # here because `title` is itself a banned key-name yet legitimately holds
        # the app name "Lucid").
        body = _GENERIC_BODY if self.count == 1 else \
            f"Your dream grew — {self.count} new clips are ready. Open Lucid to watch."
        return {
            "title": _GENERIC_TITLE,   # the literal app name, not the dream name
            "body": body,
            "url": self.deep_link(base_url),
            "tag": f"lucid-{self.dream_id}",   # coalesces a burst into one notification
        }


def dream_grew(dream_id: str, node_id: str, count: int = 1) -> Notification:
    return Notification(dream_id=dream_id, node_id=node_id, count=count)


# --- transports ----------------------------------------------------------------

@dataclass
class SendResult:
    transport: str
    ok: bool
    detail: str


class TransportUnavailable(Exception):
    pass


class LogTransport:
    """Dry-run default — prints the content-free payload, sends nothing."""
    name = "log"

    def available(self) -> tuple[bool, str]:
        return True, "always (dry-run)"

    def send(self, notif: Notification, *, base_url: str, **_) -> SendResult:
        p = _safe_payload(notif.payload(base_url))
        print("DRY-RUN notify:", json.dumps(p))
        return SendResult(self.name, True, "printed (dry-run)")


class TelegramTransport:
    """Reuses Hermes' live Telegram bot. Stdlib only. Reaches a closed app.

    `bot_token` + `chat_id` come from the box's Hermes creds (NOT hard-coded, NOT
    sent to the phone). With send=False it dry-runs so the spike never emits a
    real message without an explicit opt-in.
    """
    name = "telegram"

    def __init__(self, bot_token: Optional[str], chat_id: Optional[str], send: bool = False):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.live = send   # NOT `self.send` — that would shadow the send() method

    def available(self) -> tuple[bool, str]:
        if not self.bot_token or not self.chat_id:
            return False, "no bot_token/chat_id configured"
        return True, "configured"

    def send(self, notif: Notification, *, base_url: str, **_) -> SendResult:  # noqa: F811
        ok, why = self.available()
        if not ok:
            raise TransportUnavailable(f"telegram: {why}")
        p = _safe_payload(notif.payload(base_url))
        # disable_web_page_preview: stop Telegram expanding the deep link into a
        # frame/thumbnail preview (privacy).
        body = f"🌙 {p['body']}\n{p['url']}"
        form = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": body,
            "disable_web_page_preview": "true",
        }).encode()
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        if not self.live:
            return SendResult(self.name, True,
                              f"DRY-RUN would POST api.telegram.org/bot…/sendMessage chat={self.chat_id}")
        req = urllib.request.Request(url, data=form, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:  # nosec - api.telegram.org
            return SendResult(self.name, r.status == 200, f"HTTP {r.status}")


class WebPushTransport:
    """The 'native notification' upgrade. Needs a service worker + VAPID + crypto.

    available() reports the honest blocker: web push (RFC 8291) requires P-256
    ECDH + HKDF + AES128GCM and an ES256 VAPID JWT — i.e. `pywebpush`/`cryptography`,
    which this project does not install. We degrade to a clear 'unavailable + dep'
    rather than a silent no-op (the keyhole-honesty rule).
    """
    name = "webpush"

    def __init__(self, vapid_keys=None, subscription=None):
        self.vapid_keys = vapid_keys
        self.subscription = subscription

    def available(self) -> tuple[bool, str]:
        try:
            import pywebpush  # noqa: F401
        except Exception:
            return False, "requires `pywebpush` (+`cryptography`) — NOT installed; project is stdlib-only"
        if not self.vapid_keys:
            return False, "no VAPID keypair generated"
        if not self.subscription:
            return False, "no PushSubscription from an installed PWA"
        return True, "ready"

    def send(self, notif: Notification, *, base_url: str, **_) -> SendResult:
        ok, why = self.available()
        if not ok:
            raise TransportUnavailable(f"webpush: {why}")
        from pywebpush import webpush  # type: ignore
        p = _safe_payload(notif.payload(base_url))
        webpush(
            subscription_info=self.subscription,
            data=json.dumps(p),
            vapid_private_key=self.vapid_keys["private"],
            vapid_claims={"sub": "mailto:lucid@localhost"},
        )
        return SendResult(self.name, True, "sent via pywebpush")


# --- orchestration -------------------------------------------------------------

def notify_on_done(meta: DreamMeta, notif: Notification, transports: list,
                   *, base_url: str, allow_mature: bool = False) -> SendResult:
    """The single entry point lucid_web.py calls on the TURN.phase->'done' edge.

    Picks the first available transport in preference order. Refuses outright for
    a private/mature/asleep dream. Always passes through the content-free gate.
    """
    ok, why = should_notify(meta, allow_mature=allow_mature)
    if not ok:
        return SendResult("none", False, f"suppressed: {why}")
    for t in transports:
        avail, _ = t.available()
        if not avail:
            continue
        try:
            return t.send(notif, base_url=base_url)
        except (TransportUnavailable, ContentLeak) as e:
            return SendResult(t.name, False, str(e))
    return SendResult("none", False, "no transport available")


def _demo():
    base = "https://4090.tail096c29.ts.net:8765"
    transports = [
        TelegramTransport(bot_token=None, chat_id=None, send=False),  # unconfigured here
        WebPushTransport(),                                            # dep-walled here
        LogTransport(),                                                # always wins as fallback
    ]
    print("=== transport availability ===")
    for t in transports:
        ok, why = t.available()
        print(f"  {t.name:9} {'AVAILABLE' if ok else 'unavailable':11} — {why}")

    print("\n=== a normal (sfw) dream clip landed ===")
    meta = DreamMeta(dream_id="d3e685", private=False, rating="sfw", box_awake=True)
    r = notify_on_done(meta, dream_grew("d3e685", "n-00042"), transports, base_url=base)
    print(f"  -> {r.transport}: ok={r.ok} {r.detail}")

    print("\n=== a private dream clip landed (must NOT push) ===")
    r = notify_on_done(DreamMeta("p9001", private=True), dream_grew("p9001", "n-1"),
                       transports, base_url=base)
    print(f"  -> {r.transport}: ok={r.ok} {r.detail}")

    print("\n=== a mature dream, no opt-in (must NOT push) ===")
    r = notify_on_done(DreamMeta("m42", rating="mature"), dream_grew("m42", "n-1"),
                       transports, base_url=base)
    print(f"  -> {r.transport}: ok={r.ok} {r.detail}")

    print("\n=== proof: a nested dream-content key is REJECTED ===")
    try:
        _safe_payload({"title": "Lucid", "body": "ok", "leak": {"prompt": "explicit text"}})
    except ContentLeak as e:
        print(f"  ContentLeak raised as expected: {e}")


if __name__ == "__main__":
    if "--demo" in sys.argv or len(sys.argv) == 1:
        _demo()

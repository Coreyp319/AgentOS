#!/usr/bin/env python3
"""ADR-0047 — the async "your dream is ready" notifier (productionized from the
spikes/lucid-notify spike). Fires on lucid_web's TURN.phase->'done' edge after a
NEW beat renders (heroes don't grow the tree, so they don't notify).

Two privacy invariants are MECHANIZED (responsible-ai-privacy-skeptic):
  1. CONTENT-FREE payloads — app name + a fixed generic line + a deep link carrying
     only an opaque node id. No dream title / beat / prompt / frame / thumbnail ever
     enters a payload (it would route through APNs/FCM/Telegram AND a lock screen).
  2. PRIVATE/MATURE never push by default, and resolution is FAIL-SAFE: if privacy
     can't be determined, we suppress.

Creds come from an EXPLICIT, user-owned config — Lucid never reads Hermes' secrets.
Config (any one of):
  - ~/.config/agentos/notify.json   {"telegram":{"bot_token":"…","chat_id":"…"},
                                      "send":true, "base_url":"https://…ts.net:8765",
                                      "allow_mature":false}
  - env: LUCID_NOTIFY_TELEGRAM_TOKEN, LUCID_NOTIFY_TELEGRAM_CHAT, LUCID_NOTIFY_SEND=1,
         LUCID_NOTIFY_BASE_URL, LUCID_NOTIFY_ALLOW_MATURE=1, LUCID_NOTIFY_DEBUG=1
DORMANT until configured: with no telegram creds it is a silent no-op. `send` defaults
to FALSE (dry-run) so the FIRST real message is an explicit opt-in, never a surprise.
on_done() NEVER raises into the caller — a notify failure can't break a render.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

# --- content-free contract -----------------------------------------------------

_BANNED_KEYS = {
    "name", "dream_name", "prompt", "beat", "beats", "caption", "text", "body_text",
    "thumbnail", "thumb", "image", "image_b64", "frame", "clip", "subject", "rating",
}
_GENERIC_BODY = "Your dream grew — a new clip is ready. Open Lucid to watch."
_GENERIC_TITLE = "Lucid"


class ContentLeak(Exception):
    pass


def assert_content_free(payload: dict) -> dict:
    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in _BANNED_KEYS or k.lower() == "title":
                    raise ContentLeak(f"banned key {path}{k!r} in notify payload")
                walk(v, path + k + ".")
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}].")
    walk(payload)
    return payload


def _safe_payload(p: dict) -> dict:
    """Gate a push schema: title must be ONLY the fixed app-name constant; the rest
    must be content-free."""
    if p.get("title") not in (None, _GENERIC_TITLE):
        raise ContentLeak("title carries non-generic content")
    assert_content_free({k: v for k, v in p.items() if k.lower() != "title"})
    return p


# --- the decision --------------------------------------------------------------

@dataclass(frozen=True)
class DreamMeta:
    dream_id: str
    private: bool = False
    rating: str = "sfw"       # 'sfw' | 'mature'
    box_awake: bool = True


def should_notify(meta: DreamMeta, *, allow_mature: bool = False) -> tuple[bool, str]:
    if meta.private:
        return False, "private dreams never push"
    if meta.rating == "mature" and not allow_mature:
        return False, "mature dreams do not push unless explicitly opted in"
    if not meta.box_awake:
        return False, "box asleep — no clip landed"
    return True, "ok"


def meta_from_fields(session, private, rating_floor, node, nodes=None) -> DreamMeta:
    """Pure: derive the notify metadata. Mature if the user-declared floor is mature
    OR this node OR any node in the chain grounded mature (monotone, like the engine)."""
    node_rating = (node or {}).get("rating")
    any_mature = any((n or {}).get("rating") == "mature" for n in (nodes or []))
    mature = rating_floor == "mature" or node_rating == "mature" or any_mature
    return DreamMeta(dream_id=str(session), private=bool(private),
                     rating="mature" if mature else "sfw", box_awake=True)


def resolve_dream_meta(session, node) -> DreamMeta:
    """Load privacy/rating for `session`. FAIL-SAFE: any uncertainty → private (suppress)."""
    try:
        import lucid_store as ST  # type: ignore
        import lucid_linear as L  # type: ignore
        private = ST.is_private(session)
        chain = L.load_chain(session)
        return meta_from_fields(session, private, chain.get("rating_floor"),
                                node, chain.get("nodes"))
    except Exception:
        return DreamMeta(dream_id=str(session), private=True, rating="mature", box_awake=True)


# --- the notification ----------------------------------------------------------

@dataclass(frozen=True)
class Notification:
    dream_id: str
    node_id: str
    count: int = 1

    def deep_link(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/?node={urllib.parse.quote(self.node_id)}"

    def payload(self, base_url: str) -> dict:
        body = _GENERIC_BODY if self.count == 1 else \
            f"Your dream grew — {self.count} new clips are ready. Open Lucid to watch."
        return {"title": _GENERIC_TITLE, "body": body,
                "url": self.deep_link(base_url), "tag": f"lucid-{self.dream_id}"}


def dream_grew(dream_id: str, node_id: str, count: int = 1) -> Notification:
    return Notification(dream_id=str(dream_id), node_id=str(node_id), count=count)


# --- config --------------------------------------------------------------------

def _config_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "agentos", "notify.json")


def load_config(path: Optional[str] = None, env: Optional[dict] = None) -> dict:
    env = os.environ if env is None else env
    cfg: dict = {}
    p = path or _config_path()
    try:
        with open(p) as f:
            cfg = json.load(f) or {}
    except Exception:
        cfg = {}
    tg = dict(cfg.get("telegram") or {})
    # env overrides (a deployment can configure without writing the file)
    if env.get("LUCID_NOTIFY_TELEGRAM_TOKEN"):
        tg["bot_token"] = env["LUCID_NOTIFY_TELEGRAM_TOKEN"]
    if env.get("LUCID_NOTIFY_TELEGRAM_CHAT"):
        tg["chat_id"] = env["LUCID_NOTIFY_TELEGRAM_CHAT"]
    cfg["telegram"] = tg
    if "LUCID_NOTIFY_SEND" in env:
        cfg["send"] = env["LUCID_NOTIFY_SEND"] not in ("", "0", "false", "False")
    if env.get("LUCID_NOTIFY_BASE_URL"):
        cfg["base_url"] = env["LUCID_NOTIFY_BASE_URL"]
    if env.get("LUCID_NOTIFY_ALLOW_MATURE"):
        cfg["allow_mature"] = env["LUCID_NOTIFY_ALLOW_MATURE"] not in ("", "0", "false", "False")
    if env.get("LUCID_NOTIFY_DEBUG"):
        cfg["debug"] = True
    return cfg


# --- transports ----------------------------------------------------------------

@dataclass
class SendResult:
    transport: str
    ok: bool
    detail: str


class TransportUnavailable(Exception):
    pass


class LogTransport:
    name = "log"

    def available(self) -> tuple[bool, str]:
        return True, "always (dry-run)"

    def send(self, notif: Notification, *, base_url: str) -> SendResult:
        print("lucid_notify DRY-RUN:", json.dumps(_safe_payload(notif.payload(base_url))))
        return SendResult(self.name, True, "printed")


class TelegramTransport:
    name = "telegram"

    def __init__(self, bot_token, chat_id, send=False):
        self.bot_token, self.chat_id, self.live = bot_token, chat_id, bool(send)

    def available(self) -> tuple[bool, str]:
        return (bool(self.bot_token) and bool(self.chat_id)), "configured"

    def send(self, notif: Notification, *, base_url: str) -> SendResult:
        ok, _ = self.available()
        if not ok:
            raise TransportUnavailable("telegram: no bot_token/chat_id")
        p = _safe_payload(notif.payload(base_url))
        form = urllib.parse.urlencode({
            "chat_id": self.chat_id, "text": f"🌙 {p['body']}\n{p['url']}",
            "disable_web_page_preview": "true",   # don't expand the link into a frame preview
        }).encode()
        if not self.live:
            return SendResult(self.name, True, "DRY-RUN (set send:true to deliver)")
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        with urllib.request.urlopen(urllib.request.Request(url, data=form, method="POST"),
                                    timeout=10) as r:   # nosec - api.telegram.org
            return SendResult(self.name, r.status == 200, f"HTTP {r.status}")


def build_transports(cfg: dict) -> list:
    tg = cfg.get("telegram") or {}
    ts: list = []
    if tg.get("bot_token") and tg.get("chat_id"):
        ts.append(TelegramTransport(tg["bot_token"], tg["chat_id"], send=cfg.get("send", False)))
    if cfg.get("debug"):
        ts.append(LogTransport())
    return ts


# --- the entry point lucid_web calls -------------------------------------------

def on_done(session, node, base_url=None, *, _cfg=None, _meta=None, _transports=None) -> SendResult:
    """Fired on the TURN.phase->'done' edge for a new beat. Never raises."""
    try:
        cfg = _cfg if _cfg is not None else load_config()
        meta = _meta if _meta is not None else resolve_dream_meta(session, node)
        ok, why = should_notify(meta, allow_mature=cfg.get("allow_mature", False))
        if not ok:
            return SendResult("none", False, f"suppressed: {why}")
        notif = dream_grew(meta.dream_id, str((node or {}).get("id", "")))
        base = base_url or cfg.get("base_url") or "http://localhost:8765"
        transports = _transports if _transports is not None else build_transports(cfg)
        for t in transports:
            avail, _ = t.available()
            if not avail:
                continue
            try:
                return t.send(notif, base_url=base)
            except (TransportUnavailable, ContentLeak):
                continue
        return SendResult("none", False, "no transport configured (dormant)")
    except Exception as e:   # noqa: BLE001 — a notify failure must never break a render
        return SendResult("none", False, f"error (swallowed): {e}")

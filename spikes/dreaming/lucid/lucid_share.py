#!/usr/bin/env python3
"""AgentOS Share — phone -> your-box ingest hub (ADR-0027).

A small, SELF-CONTAINED http.server (stdlib + PIL only) that lets an iPhone share a photo
(+ optional caption) into ONE of the box's own sinks, chosen at share time:

  - Dream        -> start a Lucid dream from the photo  (proxies lucid_web /api/start)
  - Ask Hermes   -> a multimodal chat message to Hermes (proxies Hermes :8642, on-box key)
  - Hermes task  -> a kanban task                        (Phase 2 — honest "not yet" until wired)
  - Claude       -> an INERT proposal file for desktop approval (Phase 3 — NEVER executes here)

WHY ITS OWN SERVICE (and not folded into lucid_web.py, as the council first suggested):
the whole Lucid web subsystem is under active concurrent rewrite (ADR-0028 stash/library), and
code-execution must not share the NSFW-capable dream loop's CSRF/lifecycle boundary anyway. So
the council's "dedicated share_web.py at graduation" is pulled forward to v0 (see ADR-0027 §2).
The ONLY edit to the contended lucid_web.py is a ~15-line X-Share-Key acceptance on /api/start.

TRUST CLASSES (ADR-0027): data->renderer (Dream) ships; data->orchestrator (Hermes) ships
behind on-box auth; instructions->actor (Claude) is held INERT — no `claude -p` is spawned by
this file. The execution path is Phase 3, behind its own blocking review gate.

Auth: tailnet membership is the real boundary (tailscale serve, tailnet-only). Defense-in-depth:
the PWA (a browser, same-origin) uses a per-process CSRF token; the iOS Shortcut (not a browser,
can't read the token) uses a file-backed X-Share-Key. Neither is claimed to be a security
boundary on its own. Hermes' own API key never leaves the box and is never logged.

Run:  LUCID_SHARE_PORT=8770 python3 lucid_share.py
Expose (tailnet-only): add 8770 to integrations/agentosd-remote.sh.
"""
from __future__ import annotations

import base64
import hmac
import io
import json
import os
import re
import secrets
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---- config -----------------------------------------------------------------
HOST = os.environ.get("LUCID_SHARE_HOST", "127.0.0.1")
PORT = int(os.environ.get("LUCID_SHARE_PORT", "8770"))

LUCID_BASE = os.environ.get("LUCID_BASE", "http://127.0.0.1:8765")     # the Dream door target
LUCID_PORT = urllib.parse.urlsplit(LUCID_BASE).port or 8765            # so the phone can deep-link the dream UI
HERMES_BASE = os.environ.get("HERMES_BASE", "http://127.0.0.1:8642")   # the Hermes door target
HERMES_API_KEY = os.environ.get("HERMES_API_KEY", "")                  # on-box only; never logged
HERMES_MODEL = os.environ.get("HERMES_MODEL", "hermes-agent")

# Brand-new files only; nothing here is on the contended Lucid web tree.
STATE_HOME = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
CLAUDE_INBOX = os.environ.get("SHARE_CLAUDE_INBOX",
                              os.path.join(STATE_HOME, "agentos", "share-inbox"))
CONF_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
SHARE_KEY_FILE = os.environ.get("SHARE_KEY_FILE",
                                os.path.join(CONF_HOME, "agentos", "share.key"))

# The tailnet HTTPS origin the PWA is served from (set in the systemd unit, like LUCID_EXTRA_ORIGINS).
ORIGIN_OK = {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}
ORIGIN_OK |= {o.strip() for o in os.environ.get("SHARE_EXTRA_ORIGINS", "").split(",") if o.strip()}

MAX_IMG = 20 * 1024 * 1024          # base64-decoded image ceiling (matches lucid_web)
MAX_BODY = 30 * 1024 * 1024         # reject oversized bodies before reading
MAX_PIXELS = 24_000_000             # decompression-bomb guard (matches lucid_web _decode_seed)
MAX_CAPTION = 2000                  # untrusted caption length cap

CSRF = secrets.token_hex(16)        # per-process; embedded in the served PWA, sent as X-Share-Token
_DECODE_SEM = threading.BoundedSemaphore(2)   # bound concurrent PIL decodes (memory/GPU-adjacent)

DESTS = ("lucid", "hermes-chat", "hermes-task", "claude")

# in-memory receipts: id -> dict (small ring; restarts lose them, which is fine — they're transient)
_RECEIPTS: "dict[str, dict]" = {}
_RECEIPT_ORDER: "list[str]" = []
_RECEIPT_LOCK = threading.Lock()
RECEIPT_TTL = 600       # /r/<id> 404s ~10 min after the share (the ring-of-64 bounds count, not time)
# The Claude door's on-disk proposal self-expires so a held photo + caption is never permanent PII
# (ADR-0027 retention). Long enough to approve on the desktop, short enough to forget on its own.
INBOX_TTL = int(os.environ.get("SHARE_INBOX_TTL", str(24 * 3600)))


def _ensure_private_dir(path: str) -> None:
    """0700 dir, mode-corrected even when it already exists (os.makedirs(exist_ok=True) skips the
    mode on an existing dir, so a parent left world-traversable would expose filenames+mtimes)."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _load_share_key() -> str:
    """Read the X-Share-Key; create one (0600) on first run. This is OUR key — generated here,
    shared with lucid_web via the same file — never a third party's secret."""
    try:
        with open(SHARE_KEY_FILE, "r") as f:
            k = f.read().strip()
            if k:
                return k
    except FileNotFoundError:
        pass
    _ensure_private_dir(os.path.dirname(SHARE_KEY_FILE))
    # O_EXCL: exactly one creator wins on a cold box; a peer racing the same first-run start (e.g.
    # lucid_web reading on demand) loses the create and re-reads the winner's key, so the two
    # services never diverge onto different keys. 0600 from the first byte.
    try:
        fd = os.open(SHARE_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(SHARE_KEY_FILE) as f:
            return f.read().strip()
    k = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w") as f:
        f.write(k + "\n")
    return k


SHARE_KEY = _load_share_key()


# ---- image hygiene (mirrors lucid_web._decode_seed) -------------------------
def _clean_image(raw: bytes) -> bytes:
    """Validate real image, fix orientation, strip EXIF (GPS/identity), re-encode JPEG. Guards
    decompression bombs. Returns clean JPEG bytes. Raises ValueError on anything suspicious."""
    from PIL import Image, ImageOps
    import warnings
    if len(raw) > MAX_IMG:
        raise ValueError("image too large (max 20 MB)")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        Image.open(io.BytesIO(raw)).verify()              # raises if not a valid image
        img = Image.open(io.BytesIO(raw))                 # re-open (verify leaves it unusable)
        w, h = img.size
        if w > 8192 or h > 8192 or w * h > MAX_PIXELS:
            raise ValueError(f"image dimensions too large ({w}x{h})")
        img = ImageOps.exif_transpose(img)                # honor phone rotation BEFORE dropping EXIF
        img = img.convert("RGB")                          # bounded allocation; drops alpha/EXIF
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)                     # no EXIF carried into the JPEG
    return out.getvalue()


# ---- doors ------------------------------------------------------------------
def _post_json(url: str, body: dict, headers: dict, timeout: float = 30.0) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw or b"{}")
            except Exception:
                return r.status, {"raw": raw.decode("utf-8", "replace")[:500]}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {"error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return 0, {"error": f"unreachable: {e.reason}"}


def door_lucid(jpeg: bytes, caption: str) -> dict:
    """Start a Lucid dream from the photo by proxying to lucid_web /api/start with X-Share-Key.
    lucid_web re-runs its own EXIF strip + B2 likeness gate inside start() — we don't bypass it."""
    b64 = base64.b64encode(jpeg).decode()
    name = (caption or "").strip()[:80] or None
    code, j = _post_json(f"{LUCID_BASE}/api/start",
                         {"image_b64": b64, "name": name, "private": False},
                         {"X-Share-Key": SHARE_KEY})
    if code == 0:
        return {"ok": False, "reason": "the dream service isn't reachable right now"}
    if j.get("blocked"):     # B2 likeness gate fired (real person / minor, no consent)
        return {"ok": False, "blocked": True,
                "reason": j.get("reason") or "that image can't seed a dream (a real person was detected)"}
    if j.get("error"):
        return {"ok": False, "reason": j["error"]}
    if j.get("ok"):
        return {"ok": True, "dest": "lucid", "session": j.get("session"),
                "open": f"{LUCID_BASE}/"}   # deep-link the dream UI (rewritten to tailnet origin client-side)
    return {"ok": False, "reason": "the dream service returned an unexpected response"}


def door_hermes_chat(jpeg: bytes, caption: str) -> dict:
    """Send the photo + caption to Hermes as a multimodal chat message (data->orchestrator).
    Irreversible by nature; the receipt discloses that. Hermes' key stays on-box (env, never logged)."""
    if not HERMES_API_KEY:
        return {"ok": False, "reason": "Hermes chat isn't configured (no on-box key set)"}
    auth = {"Authorization": f"Bearer {HERMES_API_KEY}"}
    code, sess = _post_json(f"{HERMES_BASE}/api/sessions", {"title": "shared from phone"}, auth)
    sid = (sess.get("session") or {}).get("id") if isinstance(sess.get("session"), dict) else sess.get("id")
    if code == 0 or not sid:
        return {"ok": False, "reason": "Hermes isn't reachable right now"}
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    msg = [{"type": "text", "text": (caption or "Shared from my phone.")[:MAX_CAPTION]},
           {"type": "image_url", "image_url": {"url": data_url}}]
    code, j = _post_json(f"{HERMES_BASE}/api/sessions/{sid}/chat", {"message": msg}, auth, timeout=120.0)
    if code == 0:
        return {"ok": False, "reason": "Hermes isn't reachable right now"}
    if code >= 400:
        return {"ok": False, "reason": j.get("error") or f"Hermes refused (HTTP {code})"}
    return {"ok": True, "dest": "hermes-chat", "session": sid, "irreversible": True}


def door_hermes_task(jpeg: bytes, caption: str) -> dict:
    """Phase 2 (ADR-0027): create a kanban task. The task-write mechanism (shell Hermes' own
    `kanban` CLI, the pinned default) is unconfirmed, so we DO NOT fake success — honest 'not yet'.
    Wiring this is gated on the human confirming the exact argv + the schema probe."""
    return {"ok": False, "phase": 2,
            "reason": "Hermes tasks aren't enabled yet — the task-write bridge is Phase 2 (needs sign-off)"}


def door_claude(jpeg: bytes, caption: str) -> dict:
    """Phase 3 (ADR-0027): write an INERT proposal file for desktop approval. THIS FILE NEVER
    EXECUTES claude -p. The caption is stored verbatim, clearly labeled untrusted phone input.
    A separate, human-approved desktop step (behind the blocking review gate) would act on it."""
    _ensure_private_dir(CLAUDE_INBOX)
    _sweep_inbox()                      # forget proposals past INBOX_TTL (ADR-0027 retention)
    rid = secrets.token_hex(8)
    img_path = os.path.join(CLAUDE_INBOX, f"{rid}.jpg")
    with open(os.open(img_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        f.write(jpeg)
    meta = {"id": rid, "status": "proposed", "source": "phone-share",
            "untrusted": True, "caption_from_phone": (caption or "")[:MAX_CAPTION],
            "image": img_path, "ts": int(time.time()),
            "expires_ts": int(time.time()) + INBOX_TTL,
            "note": (f"INERT. Requires explicit human approval on the desktop. Not executed. "
                     f"Auto-expires ~{INBOX_TTL // 3600}h after creation if not approved.")}
    with open(os.open(os.path.join(CLAUDE_INBOX, f"{rid}.json"),
                      os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(meta, f, indent=2)
    return {"ok": True, "dest": "claude", "proposal": rid,
            "held": True, "reason": "saved as a proposal — approve it on the desktop to act on it"}


DOOR_FN = {"lucid": door_lucid, "hermes-chat": door_hermes_chat,
           "hermes-task": door_hermes_task, "claude": door_claude}


def _sweep_inbox() -> None:
    """Drop Claude-inbox proposals (photo + caption) past INBOX_TTL so a held share forgets itself
    the way the receipt ring does — no permanent on-disk PII (ADR-0027 retention). Best-effort and
    quiet; run on each new write so the directory stays bounded without a separate timer."""
    now = time.time()
    try:
        names = os.listdir(CLAUDE_INBOX)
    except FileNotFoundError:
        return
    stems = {n.rsplit(".", 1)[0] for n in names if n.endswith((".json", ".jpg"))}
    for stem in stems:
        paths = [os.path.join(CLAUDE_INBOX, f"{stem}.json"),
                 os.path.join(CLAUDE_INBOX, f"{stem}.jpg")]
        mtimes = [os.path.getmtime(p) for p in paths if os.path.exists(p)]
        if mtimes and now - max(mtimes) > INBOX_TTL:
            for p in paths:
                try:
                    os.remove(p)
                except OSError:
                    pass


def _remember(receipt: dict) -> str:
    rid = secrets.token_hex(8)
    now = int(time.time())
    receipt = {**receipt, "id": rid, "ts": now}
    with _RECEIPT_LOCK:
        # time-sweep on insert: drop anything past its TTL so "forgets itself" is literally true in
        # memory (not merely 404-masked at read time). The ring-of-64 still bounds the count.
        for old in [k for k, v in _RECEIPTS.items() if now - v.get("ts", 0) > RECEIPT_TTL]:
            _RECEIPTS.pop(old, None)
            if old in _RECEIPT_ORDER:
                _RECEIPT_ORDER.remove(old)
        _RECEIPTS[rid] = receipt
        _RECEIPT_ORDER.append(rid)
        while len(_RECEIPT_ORDER) > 64:
            _RECEIPTS.pop(_RECEIPT_ORDER.pop(0), None)
    return rid


# ---- served PWA (inline; no build step, no contended web/ tree) -------------
MANIFEST = json.dumps({
    "name": "AgentOS Share", "short_name": "Share", "start_url": "/", "scope": "/",
    "display": "standalone", "background_color": "#06070d", "theme_color": "#06070d",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
})

SW_JS = """
const SHELL='agentos-share-v1';
self.addEventListener('install',e=>{self.skipWaiting()});
self.addEventListener('activate',e=>{e.waitUntil(self.clients.claim())});
// network-first for the shell; the hub is tailnet-local so offline is just an honest failure.
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET')return;
  e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));
});
"""

PAGE = r"""<!doctype html><html lang=en><head>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name=color-scheme content=dark><meta name=theme-color content="#06070d">
<meta name=csrf content="__CSRF__"><meta name=lucid-port content="__LUCIDPORT__">
<meta name=apple-mobile-web-app-capable content=yes>
<meta name=apple-mobile-web-app-status-bar-style content=black-translucent>
<meta name=apple-mobile-web-app-title content="Share">
<link rel=manifest href="/manifest.webmanifest">
<link rel=apple-touch-icon href="/icon-192.png">
<title>AgentOS Share</title>
<style>
:root{
 --ink:#eef1fb;--dim:#9aa1c4;--faint:#646a92;
 --night:#06070d;
 --panel:rgba(255,255,255,.035);--panel2:rgba(255,255,255,.065);
 --line:rgba(255,255,255,.09);--line2:rgba(255,255,255,.16);
 --cool:#8aa9ff;--cool-d:#5d7ae6;
 --warm:#ffb07a;
 --serif:'Fraunces',ui-serif,Georgia,serif;
 --mono:'Spline Sans Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
 --sans:-apple-system,system-ui,'Segoe UI',Roboto,sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0}
body{min-height:100dvh;color:var(--ink);font:15px/1.55 var(--sans);
 -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
 background:
  radial-gradient(135% 85% at 8% -10%,rgba(126,92,210,.20),transparent 52%),
  radial-gradient(120% 70% at 96% -4%,rgba(54,156,180,.18),transparent 48%),
  radial-gradient(150% 120% at 50% 118%,rgba(86,112,255,.14),transparent 60%),
  var(--night);
 padding:max(26px,env(safe-area-inset-top)) 20px calc(26px+env(safe-area-inset-bottom));
 position:relative;overflow-x:hidden}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;opacity:.05;
 background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}
body::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
 background:radial-gradient(130% 100% at 50% 30%,transparent 55%,rgba(0,0,0,.55))}
.wrap{position:relative;z-index:1;max-width:540px;margin:0 auto}
.eyebrow{font:500 11px/1 var(--mono);letter-spacing:.22em;color:var(--cool);
 text-transform:uppercase;opacity:.85;margin:2px 0 14px;display:flex;gap:8px;align-items:center}
.eyebrow .dot{width:5px;height:5px;border-radius:50%;background:var(--cool);box-shadow:0 0 10px var(--cool)}
h1{font:560 clamp(31px,8.6vw,40px)/1.02 var(--serif);letter-spacing:-.015em;margin:0 0 8px}
h1 em{font-style:italic;color:var(--cool)}
.sub{color:var(--dim);margin:0 0 22px;font-size:14.5px;max-width:34ch}

.frame{display:block;position:relative;border:1.5px dashed var(--line2);border-radius:22px;
 background:var(--panel);overflow:hidden;cursor:pointer;min-height:188px;
 transition:border-color .25s,box-shadow .5s,background .25s;-webkit-tap-highlight-color:transparent}
.frame:active{border-color:var(--cool)}
.frame input{display:none}
.frame .c{position:absolute;width:15px;height:15px;border:1.5px solid var(--cool);opacity:.45;transition:opacity .25s}
.frame .tl{top:11px;left:11px;border-right:0;border-bottom:0;border-radius:4px 0 0 0}
.frame .tr{top:11px;right:11px;border-left:0;border-bottom:0;border-radius:0 4px 0 0}
.frame .bl{bottom:11px;left:11px;border-right:0;border-top:0;border-radius:0 0 0 4px}
.frame .br{bottom:11px;right:11px;border-left:0;border-top:0;border-radius:0 0 4px 0}
.ph{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;padding:24px;text-align:center}
.ph .glyph{font-size:30px;color:var(--cool);opacity:.7;line-height:1}
.ph .pht{font:400 16px var(--serif);color:var(--ink)}
.ph .phh{font:400 10.5px/1 var(--mono);letter-spacing:.16em;color:var(--faint);text-transform:uppercase}
#prev{display:block;width:100%;max-height:46dvh;object-fit:cover;
 filter:saturate(.55) brightness(.8) contrast(1.03);transition:filter 1.15s ease}
.frame.developed{border-style:solid;border-color:rgba(138,169,255,.55);
 box-shadow:0 0 0 1px rgba(138,169,255,.28),0 18px 60px -18px rgba(91,122,255,.45)}
.frame.developed #prev{filter:none}
.frame.developed .c{opacity:0}
.sweep{position:absolute;inset:0;pointer-events:none;transform:translateX(-130%);
 background:linear-gradient(115deg,transparent 38%,rgba(138,169,255,.45) 50%,transparent 64%)}
.frame.developed .sweep{animation:sweep 1.15s cubic-bezier(.4,0,.1,1) forwards}
@keyframes sweep{to{transform:translateX(130%)}}

.cap{width:100%;margin:14px 0 2px;background:var(--panel);color:var(--ink);
 border:1px solid var(--line);border-radius:14px;padding:13px 14px;font:400 16px var(--serif);resize:none}
.cap::placeholder{color:var(--faint);font-style:italic}
.cap:focus{outline:none;border-color:var(--line2)}

.lbl{font:500 10.5px/1 var(--mono);letter-spacing:.2em;color:var(--faint);text-transform:uppercase;margin:20px 2px 9px}
.doors{display:grid;grid-template-columns:1fr 1fr;gap:11px}
.door{position:relative;display:flex;flex-direction:column;gap:2px;align-items:flex-start;text-align:left;
 border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:16px;padding:14px 14px 13px;
 cursor:pointer;transition:border-color .18s,background .18s,transform .07s;-webkit-tap-highlight-color:transparent}
.door:active{transform:scale(.985)}
.door.is-future{border-style:dashed;background:transparent}
.door svg{width:22px;height:22px;color:var(--dim);margin-bottom:6px;transition:color .18s}
.door .t{font:560 17px/1.1 var(--serif)}
.door .d{color:var(--dim);font-size:12px;line-height:1.3}
.door .tag{position:absolute;top:12px;right:12px;font:500 9px/1 var(--mono);letter-spacing:.12em;
 color:var(--faint);border:1px solid var(--line);border-radius:999px;padding:3px 6px}
.door[aria-checked=true]{border-color:var(--cool);background:var(--panel2);
 box-shadow:inset 0 0 0 1px var(--cool),0 10px 30px -16px var(--cool)}
.door[aria-checked=true] svg{color:var(--cool)}
.door[aria-checked=true] .tag{color:var(--cool);border-color:var(--cool)}
.door:focus-visible{outline:2px solid var(--cool);outline-offset:2px}

.go{margin-top:18px;width:100%;border:0;border-radius:16px;padding:16px;cursor:pointer;
 font:560 17px var(--serif);color:#0a1024;background:linear-gradient(180deg,var(--cool),var(--cool-d));
 box-shadow:0 12px 34px -14px var(--cool);transition:opacity .2s,transform .07s}
.go:active{transform:scale(.99)}
.go[disabled]{opacity:.32;box-shadow:none;cursor:default}
.go:focus-visible{outline:2px solid var(--ink);outline-offset:2px}

#out{margin-top:15px}
.msg{padding:14px 15px;border-radius:14px;background:var(--panel);border:1px solid var(--line);
 font:400 15.5px/1.45 var(--serif)}
.msg.ok{border-color:rgba(138,169,255,.4);color:#cdd9ff}
.msg.bad{border-color:rgba(255,120,120,.4);color:#ffc4c4}
.msg .open{display:inline-block;margin-top:9px;font:500 12px var(--mono);letter-spacing:.06em;
 color:var(--cool);text-decoration:none;border-bottom:1px solid rgba(138,169,255,.4);padding-bottom:1px}
.foot{margin:22px 2px 0;font:400 10.5px/1.6 var(--mono);letter-spacing:.08em;color:var(--dim);text-transform:uppercase}

@media(min-width:560px){.doors{grid-template-columns:repeat(4,1fr)}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}#prev{filter:none}}
</style></head><body><div class=wrap>

<div class=eyebrow><span class=dot></span>AgentOS · Tailnet-only</div>
<h1>Hand it to your <em>box</em>.</h1>
<p class=sub>A photo from your phone into one of your own sinks. Nothing leaves the machine.</p>

<label class=frame id=frame>
  <input type=file accept="image/*" id=file>
  <span class="c tl"></span><span class="c tr"></span><span class="c bl"></span><span class="c br"></span>
  <div class=ph id=ph><div class=glyph>◇</div><div class=pht>Choose or take a photo</div><div class=phh>HEIC · JPEG · PNG</div></div>
  <img id=prev hidden alt="your selected photo">
  <div class=sweep></div>
</label>

<textarea class=cap id=cap rows=2 placeholder="a calm aurora over dark hills…"></textarea>

<div class=lbl>Where to</div>
<div class=doors id=doors role=radiogroup aria-label="Destination">
  <button class=door data-dest=lucid role=radio aria-checked=false>
    <svg viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.6 stroke-linecap=round stroke-linejoin=round><path d="M20.5 14.5A7.5 7.5 0 1 1 11 5a6 6 0 0 0 9.5 9.5Z"/></svg>
    <span class=t>Dream</span><span class=d>start a Lucid dream</span><span class=tag>live</span></button>
  <button class=door data-dest=hermes-chat role=radio aria-checked=false>
    <svg viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.6 stroke-linecap=round stroke-linejoin=round><path d="M4 5h16v11H9l-4.5 4V16H4Z"/></svg>
    <span class=t>Ask Hermes</span><span class=d>chat with the agent</span><span class=tag>ready</span></button>
  <button class="door is-future" data-dest=hermes-task role=radio aria-checked=false>
    <svg viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.6 stroke-linecap=round stroke-linejoin=round><rect x=4 y=4 width=16 height=16 rx=3/><path d="M8.5 12l2.5 2.5L16 9"/></svg>
    <span class=t>Hermes task</span><span class=d>add to the board</span><span class=tag>soon</span></button>
  <button class="door is-future" data-dest=claude role=radio aria-checked=false>
    <svg viewBox="0 0 24 24" fill=none stroke=currentColor stroke-width=1.6 stroke-linecap=round stroke-linejoin=round><path d="M12 3l2.1 6.9L21 12l-6.9 2.1L12 21l-2.1-6.9L3 12l6.9-2.1Z"/></svg>
    <span class=t>Claude</span><span class=d>save a proposal on your box</span><span class=tag>held</span></button>
</div>

<button class=go id=go disabled>Send to your box</button>
<div id=out></div>
<p class=foot>Nothing leaves the box · acts as a proposal where it could change things</p>

</div><script>
const LP=document.querySelector('meta[name=lucid-port]').content;
const CSRF=document.querySelector('meta[name=csrf]').content;
const file=document.getElementById('file'),prev=document.getElementById('prev'),ph=document.getElementById('ph');
const frame=document.getElementById('frame'),cap=document.getElementById('cap'),go=document.getElementById('go'),out=document.getElementById('out');
const doors=[...document.querySelectorAll('.door')];
let dest=null,b64=null;
function refresh(){go.disabled=!(dest&&b64)}
function dreamUrl(){const p=(LP==='80'||LP==='443')?'':':'+LP;return location.protocol+'//'+location.hostname+p+'/'}
function reset(){b64=null;dest=null;prev.hidden=true;prev.removeAttribute('src');ph.hidden=false;
  frame.classList.remove('developed');cap.value='';out.innerHTML='';file.value='';
  doors.forEach(x=>x.setAttribute('aria-checked',false));
  go.textContent='Send to your box';go.onclick=send;go.disabled=true}
file.onchange=()=>{const f=file.files&&file.files[0];if(!f)return;
  const fr=new FileReader();fr.onload=()=>{b64=String(fr.result).split(',')[1];
    prev.src=fr.result;prev.hidden=false;ph.hidden=true;frame.classList.remove('developed');refresh()};
  fr.readAsDataURL(f)};
doors.forEach(d=>d.onclick=()=>{dest=d.dataset.dest;doors.forEach(x=>x.setAttribute('aria-checked',x===d));refresh()});
async function send(){go.disabled=true;out.innerHTML='<div class=msg>Developing…</div>';
  try{
    const r=await fetch('/share',{method:'POST',headers:{'Content-Type':'application/json','X-Share-Token':CSRF},
      body:JSON.stringify({dest,image_b64:b64,caption:cap.value})});
    const j=await r.json();
    if(j.ok){
      frame.classList.add('developed');                       // the develop, not a spinner
      const open=j.dest==='lucid'?'<a class=open href="'+dreamUrl()+'">open the dream ↗</a>':'';
      // same-origin link to the SM-1 receipt (/r/<id> on this hub) so the signature surface is
      // reachable from the PWA, not only a hand-built URL. The receipt carries its own "back to Share".
      const rcpt=j.receipt?'<a class=open href="/r/'+encodeURIComponent(j.receipt)+'">view your receipt ↗</a>':'';
      const links=[open,rcpt].filter(Boolean).join(' · ');
      out.innerHTML='<div class="msg ok">'+(j.message||'Done.')+(links?'<br>'+links:'')+'</div>';
      go.textContent='Send another';go.onclick=reset;go.disabled=false;
    }else{
      out.innerHTML='<div class="msg bad">'+(j.reason||j.error||'Could not send.')+'</div>';go.disabled=false;
    }
  }catch(e){out.innerHTML='<div class="msg bad">The box didn’t answer. Check Tailscale and try again.</div>';go.disabled=false}
}
go.onclick=send;
</script></body></html>"""


RECEIPT_PAGE = r"""<!doctype html><html lang=en><head>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name=color-scheme content=dark><meta name=theme-color content="#06070d">
<meta name=lucid-port content="__LUCIDPORT__">
<title>AgentOS Share — receipt</title>
<style>
:root{--ink:#eef1fb;--dim:#9aa1c4;--faint:#646a92;--night:#06070d;--cool:#8aa9ff;
 --serif:'Fraunces',ui-serif,Georgia,serif;--mono:'Spline Sans Mono',ui-monospace,Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;min-height:100dvh;color:var(--ink);font:15px var(--serif);text-align:center;
 background:radial-gradient(135% 85% at 8% -10%,rgba(126,92,210,.20),transparent 52%),radial-gradient(150% 120% at 50% 118%,rgba(86,112,255,.14),transparent 60%),var(--night);
 display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;
 padding:max(30px,env(safe-area-inset-top)) 22px calc(30px+env(safe-area-inset-bottom))}
.eyebrow{font:500 11px var(--mono);letter-spacing:.22em;color:var(--cool);text-transform:uppercase}
main{display:contents}    /* a semantic landmark without disturbing the body flex column */
/* the develop hero: the dream's COLOUR blooming out of the dark — never a copy of the photo (no-auth /r/) */
.hero{position:relative;width:min(340px,82vw);aspect-ratio:4/5;border-radius:20px;overflow:hidden;background:#070810}
.hero.developed{border:1px solid rgba(138,169,255,.42);box-shadow:0 18px 60px -18px rgba(91,122,255,.42)}
.hero.held{border:1px dashed rgba(138,144,160,.42);opacity:.7}     /* S2 'paths not taken': proposed, not developed */
.aurora{position:absolute;inset:-22%;filter:blur(34px) saturate(115%);opacity:0;animation:develop 1.3s .12s ease forwards}
.aurora i{position:absolute;border-radius:50%;mix-blend-mode:screen;display:block}
.aurora i:nth-child(1){width:46%;height:60%;left:8%;top:14%;background:radial-gradient(circle,#27306E,transparent 62%)}
.aurora i:nth-child(2){width:54%;height:64%;right:4%;top:6%;background:radial-gradient(circle,#8A6BDC,transparent 62%)}
.aurora i:nth-child(3){width:50%;height:56%;left:24%;bottom:0;background:radial-gradient(circle,#4A5AD2,transparent 62%)}
.hero.held .aurora{animation:none;opacity:.2}     /* held shows a faint static wash, never blooms */
@keyframes develop{to{opacity:.6}}
/* the state WORD is the PRIMARY proposed-vs-executed channel — server-authored DOM text, present with or without motion */
.state{font:500 11px var(--mono);letter-spacing:.18em;text-transform:uppercase;margin:0}
.state.developed{color:var(--cool)}
.state.held{color:var(--ink)}      /* never inherits the hero's .7 dimming — must clear AA */
.msg{font:400 19px/1.4 var(--serif);max-width:25ch;margin:0}
.inverse{font:400 13px/1.4 var(--serif);color:var(--dim);max-width:26ch;margin:0}
.links{display:flex;flex-direction:column;gap:7px;align-items:center}
.link{font:500 12px var(--mono);letter-spacing:.06em;color:var(--cool);text-decoration:none;
 border-bottom:1px solid rgba(138,169,255,.4);padding-bottom:2px}
.link.sub{color:var(--dim);border-bottom-color:rgba(154,161,196,.35)}    /* the inverse: secondary, calm, never red */
.link:focus-visible{outline:2px solid var(--cool);outline-offset:3px;border-radius:2px}
.foot{font:400 10.5px var(--mono);letter-spacing:.08em;color:var(--dim);text-transform:uppercase;margin:0}
.foot.sub{text-transform:none;letter-spacing:.02em}      /* informational disclosure — must clear AA (--dim 7.9:1) */
/* reduced-motion: every state lands on its settled, fully-legible end-state — no info lives in motion */
@media(prefers-reduced-motion:reduce){
  .aurora{animation:none;opacity:.5}
  .aurora i{animation:none}
}
</style></head><body><main>
<div class=eyebrow>Received on your box</div>
<div class="hero __STATECLS__" aria-hidden=true><div class=aurora><i></i><i></i><i></i></div></div>
<p class="state __STATECLS__">__WORD__</p>
<div class=msg>__MSG__</div>
__INVERSE__
__LINKS__
<a class="link sub" href="/">back to Share</a>
<div class=foot>Nothing left the machine</div>
<div class="foot sub">This receipt lives only on your box and forgets itself.</div>
</main>
<script>const LP=document.querySelector('meta[name=lucid-port]').content;
const p=(LP==='80'||LP==='443')?'':':'+LP;const dream=location.protocol+'//'+location.hostname+p+'/';
for(const id of ['open','del']){const a=document.getElementById(id);if(a)a.href=dream}</script>
</body></html>"""


NOTFOUND_PAGE = r"""<!doctype html><html lang=en><head>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<meta name=color-scheme content=dark><meta name=theme-color content="#06070d">
<title>AgentOS Share — receipt</title>
<style>:root{--ink:#eef1fb;--faint:#646a92;--night:#06070d;--cool:#8aa9ff;
 --serif:'Fraunces',ui-serif,Georgia,serif;--mono:'Spline Sans Mono',ui-monospace,Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;min-height:100dvh;color:var(--ink);font:15px var(--serif);text-align:center;
 background:radial-gradient(150% 120% at 50% 118%,rgba(86,112,255,.14),transparent 60%),var(--night);
 display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:30px 22px}
.eyebrow{font:500 11px var(--mono);letter-spacing:.22em;color:var(--cool);text-transform:uppercase}
.msg{font:400 18px/1.4 var(--serif);color:var(--ink);max-width:27ch;margin:0}
.link{font:500 12px var(--mono);letter-spacing:.06em;color:var(--cool);text-decoration:none;
 border-bottom:1px solid rgba(138,169,255,.4);padding-bottom:2px}
.link:focus-visible{outline:2px solid var(--cool);outline-offset:3px;border-radius:2px}
</style></head><body>
<div class=eyebrow>Receipt not found</div>
<p class=msg>No such receipt — it may have expired. Receipts forget themselves after a short while.</p>
<a class=link href="/">Back to Share</a>
</body></html>"""


def _esc(s) -> str:
    """The single escaping chokepoint: every dynamic string reaching the receipt page goes through
    here. Load-bearing the instant any caption-echo / model read-back ever lands — build it now.
    NOTE: this guards HTML text/attribute contexts only. script-src 'unsafe-inline' still stands, so
    no dynamic value may EVER be substituted into a <script> context (use <meta> + a JS read, as now)."""
    import html
    return html.escape(str(s), quote=True)


# dest -> (hero/word CSS class, server-authored state WORD). Unknown dest defaults to the SAFE 'held'.
_RECEIPT_STATE = {
    "lucid": ("developed", "Developing"),
    "hermes-chat": ("developed", "Developing"),
    "claude": ("held", "Proposed — not yet acted"),
}
# the irreversibility disclosure for chat is its OWN structural line so a redesign can't swallow it.
_INVERSE_HERMES = "<p class=inverse>Hermes has read this. A chat can&#8217;t be taken back.</p>"
# the Lucid inverse is a cross-origin DEEP-LINK to the dream view (which owns /api/delete) — never an
# inline delete: the share key authenticates only /api/start, and a share-side delete would be a false
# promise (the dream lives in lucid_web). The server authors a best-effort real href (so the links work
# with JS off); the client JS (RECEIPT_PAGE) then refines it to the exact origin when present.
_LINKS_LUCID = ('<div class=links>'
                '<a id=open class=link href="__DREAM__">open the dream &#8599;</a>'
                '<a id=del class="link sub" href="__DREAM__">open the dream view to delete it</a>'
                '</div>')

# hostname[:port] only — excludes the underscore on purpose (so a crafted Host can neither break out of
# the href attribute nor resurrect a __TOKEN__ during templating; html.escape leaves underscores intact).
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+(?::\d{1,5})?$")


def _dream_origin(host: str, proto: str) -> "str | None":
    """Best-effort dream-view origin for the receipt's NO-JS link, derived from the request's host
    (the exact tailnet host the phone navigated to) + LUCID_PORT. Behind `tailscale serve` the original
    host arrives as X-Forwarded-Host and the scheme as X-Forwarded-Proto, so do_GET passes those with
    Host as the fallback. Returns None for a missing/implausible host so the link falls back to '#'
    (the client JS still rewrites it when on). The host is reflected only to the same requester and is
    validated + escaped, so a crafted header can't reach another user or inject markup."""
    host = (host or "").split(",", 1)[0].strip()      # first hop only, if a proxy chained the header
    if not _HOST_RE.match(host):
        return None
    hostname = host.rsplit(":", 1)[0] if ":" in host else host
    scheme = proto if proto in ("http", "https") else "https"   # the only reachable path is TLS tailnet
    suffix = "" if LUCID_PORT in (80, 443) else f":{LUCID_PORT}"
    return f"{scheme}://{hostname}{suffix}/"


def _render_receipt(r: dict, dream_origin: "str | None" = None) -> str:
    """Render a receipt as the SM-1 'develop' hero — the dream's COLOUR blooming out of the dark,
    NEVER a copy of the photo (/r/<id> is unauthenticated, so it serves no source-image bytes). The
    proposed-vs-executed distinction is carried by a server-authored WORD + luminance, not by motion.
    dream_origin (when known) makes the Lucid open/delete links work with JS off; client JS still refines."""
    dest = r.get("dest")
    cls, word = _RECEIPT_STATE.get(dest, ("held", "Proposed — not yet acted"))
    inverse = _INVERSE_HERMES if dest == "hermes-chat" else ""
    # resolve __DREAM__ inside the links fragment (escaped + charset-validated) BEFORE page assembly,
    # so no __DREAM__ token survives to collide with the message substitution; '#' falls back to the
    # client-JS rewrite when the host is unknown.
    links = _LINKS_LUCID.replace("__DREAM__", _esc(dream_origin) if dream_origin else "#") if dest == "lucid" else ""
    # static, server-authored fragments first; the (escaped) dynamic message LAST, so an escaped value
    # can never resurrect a __TOKEN__ (html.escape does not escape underscores).
    page = (RECEIPT_PAGE
            .replace("__LUCIDPORT__", str(LUCID_PORT))
            .replace("__STATECLS__", cls)
            .replace("__WORD__", word)
            .replace("__INVERSE__", inverse)
            .replace("__LINKS__", links))
    return page.replace("__MSG__", _esc(r.get("message") or "Done."))


# ---- icons (generated once with PIL; cached) --------------------------------
_ICON_CACHE: "dict[int, bytes]" = {}


def _icon(size: int) -> bytes:
    if size in _ICON_CACHE:
        return _ICON_CACHE[size]
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (size, size), (11, 13, 20))
    d = ImageDraw.Draw(img)
    for y in range(size):                                   # soft aurora wash
        t = y / size
        d.line([(0, y), (size, y)],
               fill=(int(15 + 22 * t), int(20 + 30 * t), int(40 + 70 * t)))
    r = size // 5                                           # a calm blue ring (the one accent)
    d.ellipse([size//2 - r, size//2 - r, size//2 + r, size//2 + r],
              outline=(91, 140, 255), width=max(2, size // 32))
    out = io.BytesIO()
    img.save(out, "PNG")
    _ICON_CACHE[size] = out.getvalue()
    return _ICON_CACHE[size]


# ---- HTTP handler -----------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "agentos-share"
    timeout = 30          # reap a stalled/slow-drip connection (Slowloris) — StreamRequestHandler
                          # applies this to the socket; a 20 MB tailnet upload fits comfortably.

    def log_message(self, *a):       # quiet; systemd journal captures what we choose to print
        pass

    def _send(self, code, body, ctype, extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):
            # Strict CSP so "nothing left the machine" is literally true: no remote origin can load.
            # connect-src/manifest-src 'self' are the minimum that keep the PWA's /share POST + the
            # webmanifest working; frame-ancestors 'none' blocks clickjacking the receipt.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; connect-src 'self'; manifest-src 'self'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.replace("__CSRF__", CSRF).replace("__LUCIDPORT__", str(LUCID_PORT)),
                              "text/html; charset=utf-8")
        if path == "/manifest.webmanifest":
            return self._send(200, MANIFEST, "application/manifest+json")
        if path == "/sw.js":
            return self._send(200, SW_JS, "text/javascript")
        if path == "/icon-192.png":
            return self._send(200, _icon(192), "image/png")
        if path == "/icon-512.png":
            return self._send(200, _icon(512), "image/png")
        if path == "/healthz":
            return self._send(200, "ok", "text/plain")
        if path.startswith("/r/"):
            rid = path[3:]
            r = _RECEIPTS.get(rid)
            # honest-open 404: unknown OR expired (TTL) returns the same styled, navigable page — never
            # leaks whether an id ever existed, and a keyboard user keeps a focusable way back to Share.
            if not r or (int(time.time()) - r.get("ts", 0) > RECEIPT_TTL):
                return self._send(404, NOTFOUND_PAGE, "text/html; charset=utf-8")
            # X-Forwarded-* carry the real tailnet host/scheme behind `tailscale serve`; Host is the
            # fallback. Lets the receipt's Lucid links work with JS off (client JS refines when on).
            origin = _dream_origin(
                self.headers.get("X-Forwarded-Host") or self.headers.get("Host", ""),
                self.headers.get("X-Forwarded-Proto", ""))
            return self._send(200, _render_receipt(r, origin), "text/html; charset=utf-8")
        return self._send(404, "not found", "text/plain")

    def _authed(self) -> bool:
        """Either auth suffices (tailnet is the real boundary): the PWA sends the per-process
        CSRF token; the iOS Shortcut sends the file-backed X-Share-Key."""
        if hmac.compare_digest(self.headers.get("X-Share-Key", ""), SHARE_KEY):
            return True
        if hmac.compare_digest(self.headers.get("X-Share-Token", ""), CSRF):
            # The token = the browser PWA (the iOS Shortcut uses X-Share-Key above and never reaches
            # here). Browsers send Origin on every state-changing fetch, so require it to be in the
            # allowlist — fail closed on a missing/foreign Origin rather than letting a leaked token
            # authenticate a cross-origin or non-browser caller.
            return self.headers.get("Origin") in ORIGIN_OK
        return False

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/share":
            return self._send(404, "not found", "text/plain")
        if not self._authed():
            return self._send(403, json.dumps({"ok": False, "error": "unauthorized"}), "application/json")
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n > MAX_BODY:
            return self._send(413, json.dumps({"ok": False, "error": "payload too large"}), "application/json")
        try:
            req = json.loads(self.rfile.read(n) or "{}") if n else {}
        except Exception:
            return self._send(400, json.dumps({"ok": False, "error": "bad request"}), "application/json")

        dest = req.get("dest")
        if dest not in DESTS:
            return self._send(400, json.dumps({"ok": False, "error": "unknown destination"}), "application/json")
        img_b64 = req.get("image_b64")
        caption = (req.get("caption") or "")[:MAX_CAPTION]
        if not img_b64:
            return self._send(400, json.dumps({"ok": False, "error": "no image"}), "application/json")

        if not _DECODE_SEM.acquire(blocking=False):
            return self._send(429, json.dumps({"ok": False, "reason": "busy — try again in a moment"}),
                              "application/json")
        try:
            try:
                raw = base64.b64decode(img_b64, validate=True)
                jpeg = _clean_image(raw)            # EXIF-strip every path (ADR-0027 §safety)
            except Exception as e:
                return self._send(200, json.dumps({"ok": False, "reason": f"invalid image: {e}"}),
                                  "application/json")
            result = DOOR_FN[dest](jpeg, caption)
        finally:
            _DECODE_SEM.release()

        # honest acknowledgements — comprehension, not transport; never a fake "Sent ✓"
        if result.get("ok"):
            msg = {
                "lucid": "Your box read this photo and is opening a dream from it.",
                "hermes-chat": "Hermes read your photo. A message can't be unread — there's no undo for this one.",
                "hermes-task": "Added to the board.",
                "claude": "Saved as a proposal on your box. Nothing runs until you approve it on the desktop.",
            }.get(dest, "Done.")
            # the receipt ring holds ONLY the server-authored fields the receipt renders — never the
            # untrusted caption (retained PII, no purpose) and never a copy of the photo (no-auth /r/<id>).
            result = {**result, "message": msg,
                      "receipt": _remember({"dest": dest, "message": msg})}
        return self._send(200, json.dumps(result), "application/json")


def main():
    _ensure_private_dir(CLAUDE_INBOX)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    hk = "set" if HERMES_API_KEY else "MISSING (Hermes chat disabled)"
    print(f"[lucid_share] listening on http://{HOST}:{PORT}  lucid={LUCID_BASE}  hermes={hk}", flush=True)
    print(f"[lucid_share] X-Share-Key file: {SHARE_KEY_FILE}  claude-inbox: {CLAUDE_INBOX}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

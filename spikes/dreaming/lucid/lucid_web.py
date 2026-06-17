#!/usr/bin/env python3
"""Lucid web surface (ADR-0014/0015) — the dedicated interactive page the AgentOS status
panel (:9123) opens. The status board stays read-only status + links; lucid *renders* here.

It is a thin, loopback-only, stdlib-only surface over the SAFE MVP path (`lucid_linear`):
every prompt passes the deterministic gate (`lucid_safety.gate_prompt`), every video beat goes
through the coordinator lease (Spawn/confirm-evict/Release), and the whole thing FAILS OPEN and
says so honestly when the coordinator/ComfyUI/Ollama isn't there — it never looks ready when it
isn't, and never forces a GPU load. Wears the shared instrument "glass" register
(integrations/design/instrument-tokens.md) so it feels like the keyhole + status panel.

Endpoints:
  GET  /            the page
  GET  /healthz     200 "ok"  (the status panel's reachability probe)
  GET  /api/state   readiness (coordinator/comfyui/ollama) + current chain + validated beats
  POST /api/dream   one gated, leased turn (same-origin guarded) — {prompt|choose, label}

Run: python3 lucid_web.py   (port LUCID_WEB_PORT, default 8765; loopback only)
"""
import base64
import hmac
import io
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_linear as L   # noqa: E402  (safe MVP path: gate -> confirm-evict -> lease -> generate)
import lucid_safety as S   # noqa: E402
import lucid_t2i as T2I    # noqa: E402  (text-to-opening seed source — ADR-0015)
import lucid_hub as H      # noqa: E402  (ADR-0019: the held/needs-review board + retry/dismiss/approve)

HOST = os.environ.get("LUCID_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("LUCID_WEB_PORT", "8765"))
SESSION = os.environ.get("LUCID_WEB_SESSION", "web")
ORIGIN_OK = {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}
# the built React bundle (self-hosted, no CDN); served as the primary surface when present
WEB_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "dist")
HAS_DIST = os.path.isdir(WEB_DIST)   # fixed at startup — avoid a stat() on every (polled) GET
_MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
         ".json": "application/json", ".woff2": "font/woff2", ".woff": "font/woff",
         ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm", ".ico": "image/x-icon",
         ".map": "application/json"}
# Per-process CSRF token: embedded in the page, required as a header on every state-changing POST.
# A cross-origin page can't read it (same-origin policy), so it closes the missing-Origin CSRF gap.
CSRF = secrets.token_hex(16)
# Bound the expensive start path (each upload = an image decode + a ~13s vision model load) so a
# burst of /api/start can't exhaust memory / thrash the GPU the coordinator arbitrates (security).
_START_SEM = threading.BoundedSemaphore(2)
MAX_BODY = 30 * 1024 * 1024     # hard request-body ceiling (before reading)
MAX_IMG = 20 * 1024 * 1024      # decoded-image-bytes ceiling
MAX_PIXELS = 24_000_000         # ~6000x4000 — reject decompression bombs

# ---------------- in-flight turn record (the honest "dreaming" state) ----------------
# A video beat takes MINUTES, so /api/dream starts a worker and returns at once; the page renders
# this. phase: idle | dreaming | done | skipped (fail-open) | refused (gate) | error
# `epoch` is a monotonic turn-generation counter: /api/start, /api/delete, and /api/burn bump it to
# SUPERSEDE any in-flight worker, so a stale beat that finishes after the session was restarted/wiped
# can neither clobber the fresh TURN state nor resurrect a deleted chain (see _supersede_turn).
TURN = {"phase": "idle", "label": None, "error": None, "started": None, "epoch": 0}
TURN_LOCK = threading.Lock()
# Serializes the per-frame beat roll+persist so two concurrent /api/beats reads (e.g. two tabs)
# can't both roll the LLM for the same tip — the first rolls + seals it, the rest re-serve it.
BEATS_LOCK = threading.Lock()

# ---------------- WARM-KEEP: one ComfyUI lease held across a session (ADR-0015) ----------------
# ComfyUI is no longer always-on — the coordinator Spawns+owns it under a BATCH lease, so the lease
# is real (the keyhole reads "batch (comfyui)") and a live-inference preempt can SIGKILL it. To
# avoid a per-beat 17 GB cold-start we hold ONE lease across the session: spawn on the first ComfyUI
# op (the text-opening render OR the first dream beat), reuse it for every later beat, and release on
# session end / idle / shutdown. The lease lives with the coordinator-owned ComfyUI process (not our
# D-Bus connection), so warm-keep is just "don't Release between beats." LEASE_LOCK guards the two
# fields and serializes ensure/release so two requests can't double-spawn.
CURRENT_TOKEN = None           # the held batch-lease token, or None when no lease is held
TOKEN_DEADLINE = None          # time.monotonic() after which an IDLE session's lease is reaped
LEASE_LOCK = threading.Lock()
IDLE_SECS = int(os.environ.get("LUCID_LEASE_IDLE_SECS", "600"))  # release a walked-away session's GPU


def _epoch_current(epoch):
    """True iff `epoch` is still the live turn generation — i.e. no /api/start, /api/delete, or burn
    has superseded this worker since it started. A lock-free read would be GIL-atomic, but we take the
    lock so the check is ordered against _supersede_turn's bump."""
    with TURN_LOCK:
        return TURN["epoch"] == epoch


def _supersede_turn():
    """Invalidate any in-flight beat worker — a /api/start, /api/delete, or burn arrived. Bumping the
    epoch makes the worker's terminal writes no-ops: step() sees `is_current()` go False and skips its
    chain persist (no resurrection of a deleted/restarted chain), and _run_turn skips its TURN update
    (no clobbering the fresh idle state). Also resets the visible turn to idle."""
    with TURN_LOCK:
        TURN["epoch"] += 1
        TURN.update(phase="idle", label=None, error=None, started=None)


def _run_turn(prompt, label, epoch=None):
    """Worker: drive ONE leased turn, then record an honest outcome (never a silent no-op).
    Warm-keep: ensure the session's batch lease (spawn ComfyUI once, reuse after) and hand it to
    step() as external — step neither Spawns nor Releases, so ComfyUI stays warm across beats.
    `epoch` (the turn generation captured at /api/dream) gates every state mutation: if a
    start/delete/burn supersedes this turn mid-beat, both step's chain persist and the terminal TURN
    update are discarded. `epoch=None` (tests / untracked callers) keeps the legacy unguarded path."""
    global TOKEN_DEADLINE
    try:
        if epoch is not None and not _epoch_current(epoch):  # superseded before we even spawned — skip
            return
        if _ensure_lease(epoch) is None:  # coordinator down / GPU busy / ComfyUI cold / superseded — fail open
            phase, err = "skipped", None
        else:
            is_current = (lambda: _epoch_current(epoch)) if epoch is not None else None
            node = L.step(SESSION, prompt, label, external_lease=True, is_current=is_current)
            phase, err = ("done" if node else "skipped"), None
    except SystemExit as e:        # red-line gate refused the prompt (B3)
        phase, err = "refused", str(e)
    except Exception as e:         # noqa: BLE001  — fail open, but SAY SO
        phase, err = "error", str(e)
    with LEASE_LOCK:               # beat done — restart the idle countdown from now, but only while this
        if CURRENT_TOKEN and (epoch is None or TURN["epoch"] == epoch):  # turn still owns the session: a
            TOKEN_DEADLINE = time.monotonic() + IDLE_SECS               # superseded turn must not push a
                                                                        # NEW session's deadline forward
                                                                        # (lock-free epoch read: GIL-atomic)
    with TURN_LOCK:
        if epoch is not None and TURN["epoch"] != epoch:
            return                 # superseded mid-beat — don't clobber the fresh state
        TURN.update(phase=phase, error=err, started=None)


def turn_snapshot():
    with TURN_LOCK:
        t = {"phase": TURN["phase"], "label": TURN["label"], "error": TURN["error"]}
        if TURN["phase"] == "dreaming" and TURN["started"] is not None:
            t["elapsed"] = int(time.monotonic() - TURN["started"])
    return t


# ---------------- readiness probes (honest; never claim ready when blind) ----------------
def _http_ok(url, timeout=1.5):
    try:
        urllib.request.urlopen(url, timeout=timeout).read(1)
        return True
    except Exception:
        return False


def _coordinator_up():
    try:
        r = subprocess.run(
            ["busctl", "--user", "call", "org.agentos.Coordinator1", "/org/agentos/Coordinator1",
             "org.agentos.Coordinator1", "Status"], capture_output=True, text=True, timeout=4)
        return r.returncode == 0
    except Exception:
        return False


def readiness():
    comfy = _http_ok(f"http://{L.COMFY_HOST}/system_stats")
    ollama = _http_ok(f"{L.E.OLLAMA}/api/version")
    coord = _coordinator_up()
    # ComfyUI is now ON-DEMAND (coordinator-spawned per session, ADR-0015 warm-keep), so it is NOT a
    # precondition to dream — the dream is what spawns it. The loop can dream when the coordinator can
    # arbitrate the GPU AND the narrator (Ollama) is up. `comfyui` stays reported (honest: up only
    # during an active dream), but does not gate `can_dream`.
    return {
        "coordinator": coord, "comfyui": comfy, "comfyui_on_demand": True, "ollama": ollama,
        "can_dream": coord and ollama,
        # the user's truth, in their words — not the daemon's component names
        "why": ([] if coord else ["graphics turn-taking isn't running"])
               + ([] if ollama else ["story suggestions aren't responding"]),
    }


def _ensure_lease(epoch=None):
    """Warm-keep: hold ONE batch lease across the session's ComfyUI ops (text-opening + i2v beats).
    Reuse the held lease if ComfyUI still answers; otherwise (none, or stale because a preempt
    SIGKILLed ComfyUI and the daemon auto-released) Spawn+own a fresh ComfyUI and wait until it's
    ready. Returns the token, or None = FAIL OPEN (coordinator down / admission refused / ComfyUI
    never came up) — the caller skips the turn, never forces VRAM. Serialized by LEASE_LOCK so two
    requests can't double-spawn; the cold-start wait_ready is held under the lock on purpose (a
    second caller then reuses the one ComfyUI rather than racing a second Spawn).

    `epoch` (passed by the beat worker) makes the spawn epoch-aware: if a start/burn/delete supersedes
    the turn before we commit a token, we must NOT leave a lease held for a dead session — that was a
    real ~17 GB leak in the bare warm-keep (a worker that cleared the epoch check, then had burn's
    _release_lease run past it, would Spawn a ComfyUI nothing could reclaim until the idle reaper). We
    re-check at every commit point. The epoch is read lock-free (a GIL-atomic int) ON PURPOSE: taking
    TURN_LOCK here would nest it under LEASE_LOCK, which the flat-lock design forbids."""
    global CURRENT_TOKEN, TOKEN_DEADLINE
    def _superseded():
        return epoch is not None and TURN["epoch"] != epoch
    with LEASE_LOCK:
        if _superseded():                                   # dead session — neither reuse nor spawn for it
            return None
        if CURRENT_TOKEN and _http_ok(f"http://{L.COMFY_HOST}/system_stats"):
            TOKEN_DEADLINE = time.monotonic() + IDLE_SECS   # touch: not idle
            return CURRENT_TOKEN
        if CURRENT_TOKEN:                                   # stale token (ComfyUI gone) — clear it
            L.lease_release(CURRENT_TOKEN)                  # harmless no-op if already auto-released
            CURRENT_TOKEN, TOKEN_DEADLINE = None, None
        token = L.lease_spawn("batch")                      # admission: predict-before-load (fail open)
        if token is None:
            return None
        if _superseded() or not L.wait_ready():             # superseded during admission, OR spawned but
            L.lease_release(token)                          # never bound :8188 — release + fail open
            return None
        if _superseded():                                   # superseded DURING the cold-start wait — undo
            L.lease_release(token)
            return None
        CURRENT_TOKEN, TOKEN_DEADLINE = token, time.monotonic() + IDLE_SECS
        return token


def _release_lease():
    """Release any held ComfyUI lease (-> coordinator SIGKILLs ComfyUI, VRAM reclaimed). Idempotent;
    called on a fresh /api/start, burn/delete, idle-reap, and shutdown. A no-op if nothing is held."""
    global CURRENT_TOKEN, TOKEN_DEADLINE
    with LEASE_LOCK:
        if CURRENT_TOKEN:
            L.lease_release(CURRENT_TOKEN)
            CURRENT_TOKEN, TOKEN_DEADLINE = None, None


def _end_session():
    """Tear down the current session: invalidate any in-flight beat (epoch bump) BEFORE reclaiming the
    GPU, so a stale worker can neither resurrect the chain nor (now that _ensure_lease is epoch-aware)
    re-spawn a lease for a dead session. One home for the burn/delete teardown invariant — the order
    (supersede THEN release) must not be reordered: the worker reads the epoch to decide both."""
    _supersede_turn()
    _release_lease()


def _lease_reaper():
    """Release a walked-away session's lease so an idle dream never pins ~17 GB. NEVER reaps while a
    beat is in flight (phase 'dreaming' — a long beat refreshes the deadline only on completion, so
    reaping mid-beat would SIGKILL ComfyUI under the generation)."""
    global CURRENT_TOKEN, TOKEN_DEADLINE
    while True:
        time.sleep(30)
        with TURN_LOCK:
            dreaming = TURN["phase"] == "dreaming"
        with LEASE_LOCK:
            if (CURRENT_TOKEN and not dreaming and TOKEN_DEADLINE
                    and time.monotonic() > TOKEN_DEADLINE):
                print(f"[lucid] idle {IDLE_SECS}s — releasing ComfyUI lease {CURRENT_TOKEN}", flush=True)
                L.lease_release(CURRENT_TOKEN)
                CURRENT_TOKEN, TOKEN_DEADLINE = None, None


def chain_or_none():
    try:
        return L.load_chain(SESSION)
    except Exception:
        return None


def _synthetic_opening():
    """A server-generated abstract opening frame — no user upload, so no real-person B2 concern.
    A placeholder seed until upload + the face/likeness guard (or text-to-opening) land."""
    import tempfile
    from PIL import Image, ImageDraw
    fd, p = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img = Image.new("RGB", (720, 1280), (18, 22, 38))
    d = ImageDraw.Draw(img)
    for y in range(0, 1280, 5):
        d.line([(0, y), (720, y)],
               fill=(min(255, 18 + y // 12), min(255, 28 + y // 20), min(255, 60 + y // 9)))
    img.save(p)
    return p


def _decode_seed(raw):
    """Validate uploaded bytes are a real image and re-encode to a clean PNG — strips EXIF
    (GPS / camera / identity metadata, a privacy win) and normalizes the format for ComfyUI.
    Guards against decompression bombs: a tiny PNG can claim gigapixels (security review)."""
    from PIL import Image
    import tempfile
    import warnings
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)  # warn-band -> hard reject
        Image.open(io.BytesIO(raw)).verify()             # raises if not a valid image
        img = Image.open(io.BytesIO(raw))                # re-open (verify() leaves it unusable)
        w, h = img.size
        if w > 8192 or h > 8192 or w * h > MAX_PIXELS:
            raise ValueError(f"image dimensions too large ({w}x{h})")
        img = img.convert("RGB")                         # allocation happens here — now bounded
    fd, p = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(p, "PNG")                                   # no EXIF carried into the PNG
    return p


def state():
    """Fast — readiness + chain + private. The slow beat proposal (Ollama) is a SEPARATE endpoint
    (/api/beats) so the page renders instantly and never blocks on a model load."""
    chain = chain_or_none()
    return {"session": SESSION, "readiness": readiness(), "chain": chain,
            "private": L.ST.is_private(SESSION) or bool(chain and chain.get("private")),
            "turn": turn_snapshot()}


def beats():
    """The HELD per-frame menu (ADR-0015 §1: "no reroll"). The model proposes once per chain tip and
    the proposal is persisted on the node (lucid_linear.beats_for_tip); every later read re-serves it
    verbatim, so the suggestions can't change under the user on a reload / second tab / a skipped
    fail-open turn. Frozen while a beat is in flight — the tip can't change mid-turn, so the held menu
    stands and we never roll a fresh one against a frame the user already picked from."""
    if chain_or_none() is None:
        return []
    with TURN_LOCK:
        rolling = TURN["phase"] != "dreaming"   # in-flight: serve what's held, never roll a new menu
    try:
        with BEATS_LOCK:                        # one roll per tip even under concurrent reads
            return L.beats_for_tip(SESSION, roll=rolling)
    except Exception:
        return []


# ---------------- page (instrument glass; status panel / keyhole register) ----------------
PAGE = """<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=color-scheme content=dark><meta name=csrf content="__CSRF__">
<title>Lucid · AgentOS</title><style>
/* shared instrument register — mirrors integrations/design/instrument-tokens.md (canonical values,
   DERIVED status family, the scales, and the mandated reduced-transparency / reduced-motion fallbacks) */
:root{
 --inst-base:#12141c;--inst-deep:#161a28;--inst-horizon:#1a2238;
 --inst-text:#e6e9f0;--inst-muted:#8a90a0;--inst-label:#7a8090;
 --inst-blue:#7aa2ff;--inst-warm:#ff9957;--brand-warm:#e0884f;
 --glass:rgba(38,42,54,.46);--hairline:rgba(255,255,255,.07);
 --st-up:#74d39a;--st-idle:#757c8e;--st-unknown:#6f7894;--st-red:#ec7676;--st-amber:#f2c879;
 --st-red-line:color-mix(in srgb,var(--st-red) 40%,transparent);
 --blue-wash:color-mix(in srgb,var(--inst-blue) 11%,transparent);
 --fs-display:1.1875rem;--fs-md:.8125rem;--fs-sm:.78125rem;--fs-xs:.75rem;
 --sp-3:12px;--sp-4:16px;--sp-5:20px;--sp-6:24px;--sp-7:32px;
 --radius-sm:9px;--radius-md:16px;--blur-raised:14px}
@media (prefers-reduced-transparency:reduce){:root{--glass:rgba(24,28,40,.94)} .card{backdrop-filter:none}}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 system-ui,sans-serif;color:var(--inst-text);min-height:100vh;
 background:radial-gradient(1000px 600px at 80% -10%,color-mix(in srgb,var(--inst-blue) 8%,transparent),transparent 60%),
  linear-gradient(160deg,var(--inst-base),var(--inst-deep) 45%,var(--inst-horizon))}
.wrap{max-width:760px;margin:0 auto;padding:var(--sp-7) var(--sp-5)}
h1{font-size:1.4rem;font-weight:600;margin:0 0 2px}
.subtle{color:var(--inst-label);font-weight:400;font-size:1rem}
.sub{color:var(--inst-muted);font-size:var(--fs-sm);margin-bottom:var(--sp-6);max-width:60ch}
.card{background:var(--glass);border:1px solid var(--hairline);border-radius:var(--radius-md);
 padding:18px 20px;margin:14px 0;backdrop-filter:blur(var(--blur-raised))}
.ready{display:flex;gap:18px;flex-wrap:wrap;font-size:var(--fs-sm);color:var(--inst-muted)}
.item{display:inline-flex;align-items:center}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px;vertical-align:middle}
.on{background:var(--st-up)}.off{background:transparent;border:1.5px solid var(--st-idle)}
.banner{margin-top:10px;color:var(--inst-text);font-size:var(--fs-sm)}
.banner.bad{color:var(--st-red)}.banner.good{color:var(--st-up)}
.beat{display:block;width:100%;text-align:left;background:var(--blue-wash);border:1px solid var(--hairline);
 color:var(--inst-text);border-radius:var(--radius-sm);padding:12px 14px;margin:8px 0;cursor:pointer;font:inherit}
.beat:hover{border-color:var(--inst-blue)}.beat b{display:block;color:var(--inst-blue);margin-bottom:2px}
.beat small{color:var(--inst-muted)}
.beat.danger{background:transparent;border-color:var(--st-red-line)}.beat.danger:hover{border-color:var(--st-red)}
.ghost{background:transparent;border:1px solid var(--hairline);color:var(--inst-muted);
 border-radius:var(--radius-sm);padding:12px 14px;font:inherit;cursor:pointer}.ghost:hover{border-color:var(--inst-muted)}
.row{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
input[type=text]{width:100%;background:var(--inst-deep);border:1px solid var(--hairline);color:var(--inst-text);
 border-radius:var(--radius-sm);padding:11px 14px;font:inherit;margin-top:10px}
.note{color:var(--inst-label);font-size:var(--fs-xs)}
a{color:var(--inst-blue)}
.clip{font-size:var(--fs-xs);color:var(--inst-muted);margin:4px 0}.clip.here{color:var(--inst-text)}
.private .lock,.lock{color:var(--inst-text);font-weight:600}
.flash{max-width:760px;margin:0 auto 6px;color:var(--st-red);font-size:var(--fs-sm);opacity:0;transition:opacity .2s}
.flash.show{opacity:1}
.sr,.live{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
:focus-visible{outline:2px solid var(--inst-blue);outline-offset:2px}
/* the dreaming hero — the dream developing out of the dark (ADR-0014: "the clip developing, not a spinner") */
.stage{position:relative;border-radius:var(--radius-md);overflow:hidden;border:1px solid var(--hairline);
 aspect-ratio:16/10;background:#090b11;box-shadow:0 24px 64px -42px #000;margin:6px 0 0}
.aurora{position:absolute;inset:-24%;filter:blur(42px) saturate(115%);opacity:0;animation:develop 7s 1s ease forwards}
.aurora i{position:absolute;border-radius:50%;mix-blend-mode:screen}
.aurora i:nth-child(1){width:46%;height:60%;left:8%;top:14%;background:radial-gradient(circle,#5fd0c8,transparent 62%);animation:drift1 19s ease-in-out infinite}
.aurora i:nth-child(2){width:54%;height:64%;right:4%;top:6%;background:radial-gradient(circle,#8aa6ff,transparent 62%);animation:drift2 23s ease-in-out infinite}
.aurora i:nth-child(3){width:50%;height:56%;left:24%;bottom:0;background:radial-gradient(circle,#6f7bd6,transparent 62%);animation:drift3 27s ease-in-out infinite}
.stage .grain{position:absolute;inset:0;opacity:.05;mix-blend-mode:overlay;pointer-events:none;
 background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}
.stage .cap{position:absolute;left:0;right:0;bottom:0;padding:22px 22px 20px;z-index:2;
 background:linear-gradient(0deg,rgba(9,11,17,.86),transparent)}
.beat-q{font-family:Georgia,'Times New Roman',serif;font-style:italic;font-size:1.4rem;line-height:1.28;
 color:#eef1f8;margin:0;max-width:34ch}
.dreamrow{display:flex;align-items:baseline;justify-content:space-between;gap:14px;flex-wrap:wrap}
.elapsed-xl{font-family:Georgia,'Times New Roman',serif;font-weight:400;font-variant-numeric:tabular-nums;
 font-size:2rem;line-height:1;color:#cfd6e6}
@keyframes develop{from{opacity:0}to{opacity:.6}}
@keyframes drift1{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(12%,-8%) scale(1.12)}}
@keyframes drift2{0%,100%{transform:translate(0,0) scale(1.05)}50%{transform:translate(-10%,9%) scale(.92)}}
@keyframes drift3{0%,100%{transform:translate(0,0) scale(.95)}50%{transform:translate(8%,-12%) scale(1.1)}}
@media (prefers-reduced-motion:reduce){.aurora{animation:none;opacity:.5}.aurora i{animation:none}}
@media (prefers-reduced-transparency:reduce){.stage .grain{display:none}}
</style></head><body><div class=wrap>
<h1>Lucid <span class=subtle>· interactive dream loop</span></h1>
<div class=sub>Watch a clip, choose what happens next — the story picks up from the last frame.
Each clip is made one at a time, so it never crowds out your other apps for the graphics card.</div>
<div id=flash role=alert aria-live=assertive class=flash></div>
<div id=live role=status aria-live=polite class=live></div>
<div id=app><div class=card>loading…</div></div>
</div><script>
const CSRF=document.querySelector('meta[name=csrf]').content;
const E=s=>(s==null?'':String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let LAST=null,lastSig='',BEATS=[],BEATS_FOR=-1,burnArmed=false,burnMsg='',delArmed=false,delMsg='',lastAnn='',flashT=null;
let dreamTimer=null,dreamStartMs=0;

function fmt(t){const m=Math.floor(t/60),s=t%60;return m+':'+String(s).padStart(2,'0');}
// tick the elapsed time locally so the dreaming stage DOM (its aurora animation) persists between polls
function manageTicker(t){
 if(t.phase==='dreaming'){
  dreamStartMs=Date.now()-((t.elapsed||0)*1000);
  if(!dreamTimer)dreamTimer=setInterval(()=>{const e=document.getElementById('elapsed-xl');
   if(e)e.textContent=fmt(Math.max(0,Math.floor((Date.now()-dreamStartMs)/1000)));},1000);
 }else if(dreamTimer){clearInterval(dreamTimer);dreamTimer=null;}
}
function flash(m){const f=document.getElementById('flash');if(!f)return;f.textContent=m;f.classList.add('show');
 clearTimeout(flashT);flashT=setTimeout(()=>{f.classList.remove('show');f.textContent='';},6000);}
// status dots carry meaning by FILL+colour AND a screen-reader text equivalent (never colour alone)
function dotEl(on,name){return `<span class=item><span class="dot ${on?'on':'off'}" aria-hidden=true></span>`
 +`${E(name)} <span class=sr>${on?'— ready':'— not responding'}</span></span>`;}

function sig(s){const r=s.readiness,t=s.turn,c=s.chain;
 return [r.coordinator,r.comfyui,r.ollama,r.can_dream,s.private,
  c?c.nodes.length:-1,c?c.nodes[c.nodes.length-1].id:-1,
  t.phase,burnArmed,burnMsg,delArmed,delMsg].join('|');}   // elapsed is ticked locally, not via sig

function chainCard(n){let h=`<div class=card><b>Your dream so far</b> · ${n.length} frame(s)`;
 n.forEach((x,i)=>{const last=i===n.length-1;
  h+=`<div class="clip${last?' here':''}" title="${E(x.clip?x.clip.split('/').pop():'')}">`
   +`${last?'▸ ':''}${E(x.label||'opening')}</div>`;});
 return h+`</div>`;}

// persistent dreams are saved on disk — disclose retention + a two-step delete that wipes every sink
function libraryCard(){let h=`<div class=card><div class=note>Saved on this computer (your dream `
  +`library) — kept until you delete it.</div>`;
 if(delMsg)h+=`<div class="banner ${delMsg[0]==='!'?'bad':'good'}">${E(delMsg.replace(/^!/,''))}</div>`;
 if(delArmed)h+=`<div class=row><button class="beat danger" onclick='doDelete()'>`
  +`Delete permanently — this can't be undone</button>`
  +`<button class=ghost onclick='cancelDel()'>Cancel</button></div>`;
 else h+=`<button class="beat danger" onclick='armDel()'>🗑 Delete this dream</button>`;
 return h+`</div>`;}

function build(s){const r=s.readiness,t=s.turn;let h='';
 // readiness — three honest, function-named dots
 h+=`<div class=card><div class=ready>`
  +dotEl(r.coordinator,'Graphics turn-taking')+dotEl(r.comfyui,'Video generator')
  +dotEl(r.ollama,'Story suggestions')+`</div>`;
 if(!r.can_dream)h+=`<div class=banner>Can't dream right now — ${E(r.why.join('; '))}. `
  +`When a piece is missing, Lucid steps back and leaves your wallpaper untouched.</div>`;
 h+=`</div>`;
 // private session — a cool/neutral MODE card (warmth stays reserved for the "needs you" cue)
 if(s.private){h+=`<div class="card private"><span class=lock>🔒 Private session</span>`
  +`<div class=note style="margin-top:4px">Kept in memory, not in your saved files. Never shown elsewhere, `
  +`never set as wallpaper. Wiped when you log out — the one frame the renderer must write to disk is `
  +`sealed and burned with it.</div>`;
  if(burnMsg)h+=`<div class="banner ${burnMsg[0]==='!'?'bad':'good'}">${E(burnMsg.replace(/^!/,''))}</div>`;
  if(burnArmed)h+=`<div class=row><button class="beat danger" onclick='doBurn()'>`
   +`Burn permanently — this can't be undone</button>`
   +`<button class=ghost onclick='cancelBurn()'>Cancel</button></div>`;
  else h+=`<button class="beat danger" style="margin-top:10px" onclick='armBurn()'>🔥 Burn this dream now</button>`;
  h+=`</div>`;}
 // main surface
 if(!s.chain){
  h+=`<div class=card><b>Start a dream</b>`
   +`<div class=note style="margin-top:6px">Begin an interactive dream — then choose what happens next, one beat at a time.</div>`
   +`<label style="display:block;margin:12px 0">Opening image <span class=note>(optional — an abstract frame is used if you give neither)</span><br>`
   +`<input type=file id=img accept="image/*" style="margin-top:6px;color:var(--inst-muted);max-width:100%"></label>`
   +`<label style="display:block;margin:12px 0">…or describe the opening <span class=note>(your words → an image)</span><br>`
   +`<input type=text id=opentext placeholder="e.g. a calm aurora over dark rolling hills"></label>`
   +`<label style="display:flex;gap:9px;align-items:flex-start;margin:12px 0;cursor:pointer">`
   +`<input type=checkbox id=priv style="margin-top:3px"><span><span class=lock>🔒 Private session</span> `
   +`<span class=note>— kept in memory, not saved, never shown elsewhere, wiped when you log out.</span></span></label>`
   +`<div class=note style="margin-bottom:4px">Otherwise the dream is saved on this computer (your dream library) until you delete it.</div>`
   +`<button class=beat onclick='startDream()' id=startbtn>✦ Begin a dream</button>`
   +`<div id=startmsg class=note style="margin-top:10px">Any image you upload is checked for real-person `
   +`likeness first, and its location/camera metadata is stripped.</div></div>`;
 }else if(t.phase==='dreaming'){
  h+=chainCard(s.chain.nodes);
  h+=`<div class=stage aria-busy=true><div class=aurora><i></i><i></i><i></i></div><div class=grain></div>`
   +`<div class=cap>`
   +(t.label&&t.label!=='custom'?`<p class=beat-q>“${E(t.label)}”</p>`
     :`<p class=beat-q style="opacity:.6">the next moment is forming…</p>`)
   +`</div></div>`
   +`<div class=card><div class=dreamrow><span><b>✦ Dreaming this beat…</b>`
   +`<div class=note style="margin-top:2px">Making the next clip — this usually takes a few minutes.</div></span>`
   +`<span class=elapsed-xl id=elapsed-xl>${fmt(t.elapsed||0)}</span></div>`
   +`<div class=note style="margin-top:8px">It runs through the graphics lease, so it never crowds out your `
   +`other apps — you can watch it in the keyhole tray.</div></div>`;
 }else{
  h+=chainCard(s.chain.nodes);
  if(!s.private)h+=libraryCard();   // private dreams use Burn (above); persistent get Delete + retention
  h+=`<div class=card><b>What happens next?</b>`;
  if(t.phase==='skipped')h+=`<div class=banner>That beat was skipped — the graphics card was needed `
   +`elsewhere, so the dream fails open and your desktop is untouched. Choose again when you're ready.</div>`;
  else if(t.phase==='error')h+=`<div class="banner bad">That clip didn't come through — your desktop `
   +`is untouched. Try again.</div>`;
  else if(t.phase==='refused')h+=`<div class=banner>That direction isn't something Lucid can make. `
   +`Try a different turn.</div>`;
  if(r.can_dream){
   h+=`<div id=beats><div class=note>considering the next moves…</div></div>`
    +`<input id=own type=text placeholder="…or type what happens next" `
    +`onkeydown="if(event.key==='Enter')dreamOwn()">`;
  }else h+=`<div class=note>Choosing what happens next switches on once everything above is ready.</div>`;
  h+=`</div>`;
 }
 return h;}

function announce(s){const t=s.turn;let m='';
 if(t.phase==='dreaming')m='Dreaming this beat — a few minutes.';
 else if(t.phase==='skipped')m='That beat was skipped; your desktop is untouched.';
 else if(t.phase==='error')m='That clip did not come through.';
 else if(!s.readiness.can_dream)m='Cannot dream right now.';
 else if(s.chain)m='Ready — '+s.chain.nodes.length+' frame(s) so far.';
 else m='Ready to start a dream.';
 if(m!==lastAnn){lastAnn=m;const l=document.getElementById('live');if(l)l.textContent=m;}}

function paint(s){LAST=s;lastSig=sig(s);
 const app=document.getElementById('app');
 const own=document.getElementById('own');                 // never let the poll eat a half-typed prompt
 const sv=(own&&document.activeElement===own)?{v:own.value,a:own.selectionStart,b:own.selectionEnd}:null;
 app.innerHTML=build(s);
 const o2=document.getElementById('own');
 if(sv&&o2){o2.value=sv.v;try{o2.setSelectionRange(sv.a,sv.b);}catch(e){}o2.focus();}
 if(s.chain&&s.readiness.can_dream&&s.turn.phase!=='dreaming')loadBeats();
 manageTicker(s.turn);
 announce(s);}

async function load(){let s;try{s=await(await fetch('/api/state')).json();}catch(e){return;}
 if(sig(s)===lastSig)return;        // diff-render: only rebuild when something meaningful changed
 paint(s);}

async function loadBeats(){const el=document.getElementById('beats');if(!el)return;
 // key the held menu on the TIP NODE ID (+ reset on start/burn/delete), not chain length: two
 // different dreams both reach length 1, so a length key served a prior dream's beats after a
 // burn->restart. The server holds per frame too (no reroll), so a refetch returns the same set.
 const tip=LAST&&LAST.chain?LAST.chain.nodes[LAST.chain.nodes.length-1].id:-1;
 const paintBeats=t=>t.map((b,i)=>`<button class=beat onclick='dream(${i})'><b>${E(b.label)}</b>`
  +`<small>${E(b.prompt)}</small></button>`).join('');
 if(BEATS_FOR===tip&&BEATS.length){el.innerHTML=paintBeats(BEATS);return;}  // already have this frame's beats
 let j;try{j=await(await fetch('/api/beats')).json();}catch(e){return;}
 BEATS=j.beats||[];BEATS_FOR=tip;
 const el2=document.getElementById('beats');if(!el2)return;
 el2.innerHTML=BEATS.length?paintBeats(BEATS):'<div class=note>No suggestions — type your own below.</div>';}

async function post(body){let j;
 try{j=await(await fetch('/api/dream',{method:'POST',
  headers:{'Content-Type':'application/json','X-Lucid-Token':CSRF},body:JSON.stringify(body)})).json();}
 catch(e){flash('Could not reach Lucid — try again.');return;}
 if(j.error)flash(j.error);
 lastSig='';load();}                // force an immediate repaint into the 'dreaming' state
function dream(i){const b=BEATS[i];if(b)post({prompt:b.prompt,label:b.label});}
function dreamOwn(){const el=document.getElementById('own');const v=el?el.value.trim():'';
 if(v)post({prompt:v,label:'custom'});}

// --- seed upload + B2 likeness guard (preserved from the upload feature; consent flow intact) ---
function fileB64(f){return new Promise(r=>{const rd=new FileReader();rd.onload=()=>r(rd.result.split(',')[1]);rd.readAsDataURL(f);});}
async function startDream(consent){
 const priv=document.getElementById('priv').checked, f=document.getElementById('img').files[0];
 const txt=document.getElementById('opentext'), text=txt?txt.value.trim():'';
 const msg=document.getElementById('startmsg'), btn=document.getElementById('startbtn');
 const body={private:priv};
 if(f){body.image_b64=await fileB64(f);body.consent=!!consent;msg.textContent='🔎 checking your image for real-person likeness…';btn.disabled=true;}
 else if(text){body.text=text;body.consent=!!consent;msg.textContent='✦ painting your opening…';btn.disabled=true;}
 let j;try{j=await(await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json','X-Lucid-Token':CSRF},body:JSON.stringify(body)})).json();}
 catch(e){if(btn)btn.disabled=false;msg.textContent='Could not reach Lucid — try again.';return;}
 if(btn)btn.disabled=false;
 if(j.blocked){
   if(j.requires_consent){if(confirm(j.reason+'\\n\\nContinue?'))return startDream(true);msg.textContent='Cancelled.';return;}
   msg.textContent='🚫 '+j.reason;return;  // hard block (e.g. possible minor) — not overridable
 }
 if(j.error){msg.textContent=j.error;return;}
 BEATS=[];BEATS_FOR=-1;lastSig='';load();   // a fresh dream: drop the prior frame's held menu
}

// --- burn: two-step inline consent + an honest, persistent outcome (no native dialogs) ---
function armBurn(){burnArmed=true;burnMsg='';if(LAST)paint(LAST);}
function cancelBurn(){burnArmed=false;if(LAST)paint(LAST);}
async function doBurn(){burnArmed=false;let j;
 try{j=await(await fetch('/api/burn',{method:'POST',headers:{'X-Lucid-Token':CSRF}})).json();}
 catch(e){flash('Burn could not run — try again.');return;}
 if(j.failed&&j.failed.length)burnMsg='!Some traces could NOT be wiped and remain on disk: '
  +j.failed.join('; ')+'. They are retried at next start; delete by hand to be certain.';
 else burnMsg='This dream is gone — '+(j.burned||0)+' location(s) wiped.';
 BEATS=[];BEATS_FOR=-1;lastSig='';load();}

function armDel(){delArmed=true;delMsg='';if(LAST)paint(LAST);}
function cancelDel(){delArmed=false;if(LAST)paint(LAST);}
async function doDelete(){delArmed=false;let j;
 try{j=await(await fetch('/api/delete',{method:'POST',headers:{'X-Lucid-Token':CSRF}})).json();}
 catch(e){flash('Delete could not run — try again.');return;}
 if(j.failed&&j.failed.length)delMsg='!Some files could NOT be deleted: '+j.failed.join('; ')
  +'. Delete by hand to be certain.';
 else delMsg='';   // success — the dream is gone; the page returns to Start
 BEATS=[];BEATS_FOR=-1;lastSig='';load();}

function nextDelay(){if(document.hidden)return 15000;
 return (LAST&&LAST.turn&&LAST.turn.phase==='dreaming')?2500:5000;}
async function loop(){await load();setTimeout(loop,nextDelay());}
loop();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_raw(self, code, data, ctype, cache):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Content-Type-Options", "nosniff")   # we serve bundled JS/SVG — don't let the
        self.send_header("Content-Length", str(len(data)))      # browser MIME-sniff a response
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path):
        """Serve the built React bundle (web/dist); SPA-fallback to index.html (CSRF-injected)."""
        rel = path.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(WEB_DIST, rel))
        if full != WEB_DIST and not full.startswith(WEB_DIST + os.sep):
            return self._send(403, "forbidden", "text/plain")    # never traverse out of dist/
        if not os.path.isfile(full):
            full = os.path.join(WEB_DIST, "index.html")           # SPA fallback
        if not os.path.isfile(full):
            return self._send(404, "not found", "text/plain")
        with open(full, "rb") as f:
            data = f.read()
        if os.path.basename(full) == "index.html":                # inject the per-process CSRF token
            return self._send_raw(200, data.replace(b"__CSRF__", CSRF.encode()),
                                  "text/html; charset=utf-8", cache=False)
        ctype = _MIME.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        self._send_raw(200, data, ctype, cache=True)

    def _serve_media(self, kind):
        """Serve a chain node's clip (mp4) or anchor frame (png). `id` indexes the server-held chain,
        so the path is never user-supplied — no traversal surface; loopback-only."""
        from urllib.parse import urlparse, parse_qs
        try:
            nid = int(parse_qs(urlparse(self.path).query).get("id", [""])[0])
        except ValueError:
            return self._send(400, "bad id", "text/plain")
        chain = chain_or_none()
        node = next((n for n in (chain["nodes"] if chain else []) if n.get("id") == nid), None)
        if node is None:
            return self._send(404, "no such frame", "text/plain")
        if kind == "clip":
            p = node.get("clip")
        else:
            of = node.get("out_frame")
            p = os.path.join(L.E.INPUT_DIR, of) if of else None
        if not p or not os.path.isfile(p):
            return self._send(404, "not found", "text/plain")
        with open(p, "rb") as f:
            data = f.read()
        self._send_raw(200, data, _MIME.get(os.path.splitext(p)[1].lower(), "application/octet-stream"),
                       cache=False)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            return self._send(200, "ok", "text/plain")
        if path == "/api/state":
            return self._send(200, json.dumps(state()), "application/json")
        if path == "/api/beats":   # slow (Ollama) — fetched separately so the page never blocks
            return self._send(200, json.dumps({"beats": beats()}), "application/json")
        if path == "/api/clip":
            return self._serve_media("clip")
        if path == "/api/frame":
            return self._serve_media("frame")
        if path == "/api/queue":   # ADR-0019: the durable held + needs-review board (read-only)
            return self._send(200, json.dumps(H.board()), "application/json")
        if HAS_DIST:                                 # the React build is the primary surface
            return self._serve_static(path)
        if path in ("/", "/index.html"):             # fallback: the inline vanilla page (no build present)
            return self._send(200, PAGE.replace("__CSRF__", CSRF), "text/html; charset=utf-8")
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/api/dream", "/api/burn", "/api/start", "/api/delete",
                        "/api/queue/retry", "/api/queue/dismiss", "/api/queue/approve"):
            return self._send(404, "not found", "text/plain")
        # CSRF: a state-changing POST must carry the per-process token embedded in the page (a
        # cross-origin page can't read it). Fail closed. Defense-in-depth: reject a bad Origin too.
        if not hmac.compare_digest(self.headers.get("X-Lucid-Token", ""), CSRF):
            return self._send(403, json.dumps({"error": "missing/invalid CSRF token"}), "application/json")
        origin = self.headers.get("Origin")
        if origin and origin not in ORIGIN_OK:
            return self._send(403, json.dumps({"error": "cross-origin refused"}), "application/json")
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n > MAX_BODY:   # reject an oversized body BEFORE reading it into memory (security)
            return self._send(413, json.dumps({"error": "payload too large"}), "application/json")
        try:
            req = json.loads(self.rfile.read(n) or "{}") if n else {}
        except Exception:
            return self._send(400, json.dumps({"error": "bad request"}), "application/json")
        if path in ("/api/queue/retry", "/api/queue/dismiss", "/api/queue/approve"):
            # ADR-0019: the human disposes a held/needs-review request (CSRF + Origin checked above).
            # The id is validated INSIDE lucid_hub (no traversal); a bad id maps to 400. Durable spool
            # only — these never touch the private queue (which has no review surface by design).
            jid = req.get("id")
            try:
                if path.endswith("/retry"):
                    ok = H.retry(jid)
                elif path.endswith("/dismiss"):
                    ok = H.dismiss(jid)
                else:
                    ok = H.approve(jid)
            except ValueError:
                return self._send(400, json.dumps({"error": "bad id"}), "application/json")
            return self._send(200, json.dumps({"ok": bool(ok)}), "application/json")
        if path == "/api/burn":   # wipe the private session's every sink (ADR-0016)
            _end_session()        # invalidate any in-flight beat + reclaim the GPU lease BEFORE wiping
            removed, failed = L.burn(SESSION)
            return self._send(200, json.dumps({"ok": not failed, "burned": len(removed),
                                               "failed": failed}), "application/json")
        if path == "/api/delete":   # delete a PERSISTENT dream's every sink (a private one -> burn)
            _end_session()        # invalidate any in-flight beat + reclaim the GPU lease BEFORE wiping
            if L.ST.is_private(SESSION):
                removed, failed = L.burn(SESSION)
            else:
                removed, failed = L.ST.purge_persistent(SESSION)
            return self._send(200, json.dumps({"ok": not failed, "deleted": len(removed),
                                               "failed": failed}), "application/json")
        if path == "/api/start":  # begin a dream; uploaded image (B2-gated in start) or synthetic frame
            private = bool(req.get("private"))
            consent = bool(req.get("consent"))
            img_b64 = req.get("image_b64")
            if not _START_SEM.acquire(blocking=False):   # bound concurrent decode + vision-model loads
                return self._send(429, json.dumps({"error": "busy — try again in a moment"}), "application/json")
            try:
                _supersede_turn()     # invalidate any in-flight beat BEFORE clearing — and reset to idle
                L.ST.clear(SESSION)   # clean any prior session of this name before a fresh start
                _release_lease()      # a fresh dream starts cold — drop the prior session's ComfyUI lease
                if img_b64:
                    if not readiness()["ollama"]:   # B2 needs the vision model; fail fast, don't hang
                        return self._send(200, json.dumps({"error": "can't check the image — the narrator (Ollama) is unavailable"}), "application/json")
                    try:
                        raw = base64.b64decode(img_b64, validate=True)
                        if len(raw) > MAX_IMG:
                            raise ValueError("image too large (max 20 MB)")
                        seed = _decode_seed(raw)
                    except Exception as e:
                        return self._send(200, json.dumps({"error": f"invalid image: {e}"}), "application/json")
                    try:
                        L.start(SESSION, seed, private=private, consent=consent)  # B2 runs INSIDE start()
                    except L.SeedBlocked as e:
                        return self._send(200, json.dumps({"blocked": True, **e.verdict.as_dict()}), "application/json")
                    except Exception as e:
                        return self._send(200, json.dumps({"error": f"start failed: {e}"}), "application/json")
                    finally:
                        try:
                            os.remove(seed)
                        except OSError:
                            pass
                    return self._send(200, json.dumps({"ok": True, "private": private}), "application/json")
                if req.get("text"):   # text-to-opening: render via t2i, then B2 (a t2i CAN render a person)
                    if _ensure_lease() is None:   # spawn+own ComfyUI for the opening render (on-demand)
                        return self._send(200, json.dumps({"error": "the video generator is unavailable — can't paint an opening (GPU busy?)"}), "application/json")
                    import tempfile
                    fd, seed = tempfile.mkstemp(suffix=".png")
                    os.close(fd)
                    try:
                        T2I.generate_opening(req["text"], seed)   # gates the description; predict-before-load
                    except ValueError as e:                       # red-line blocked the description
                        os.remove(seed)
                        return self._send(200, json.dumps({"error": str(e)}), "application/json")
                    except Exception as e:                        # VRAM contention / ComfyUI / etc. — honest
                        try:
                            os.remove(seed)
                        except OSError:
                            pass
                        return self._send(200, json.dumps({"error": str(e)}), "application/json")
                    try:
                        L.start(SESSION, seed, private=private, consent=consent)  # B2 on the generated image
                    except L.SeedBlocked as e:
                        return self._send(200, json.dumps({"blocked": True, **e.verdict.as_dict()}), "application/json")
                    except Exception as e:
                        return self._send(200, json.dumps({"error": f"start failed: {e}"}), "application/json")
                    finally:
                        try:
                            os.remove(seed)
                        except OSError:
                            pass
                    return self._send(200, json.dumps({"ok": True, "private": private}), "application/json")
                # no image, no text -> a server-generated abstract opening (trusted; no real person)
                seed = _synthetic_opening()
                try:
                    L.start(SESSION, seed, private=private, _trusted_seed=True)
                except Exception as e:
                    return self._send(200, json.dumps({"error": f"start failed: {e}"}), "application/json")
                finally:
                    try:
                        os.remove(seed)
                    except OSError:
                        pass
                return self._send(200, json.dumps({"ok": True, "private": private}), "application/json")
            finally:
                _START_SEM.release()
        # ---- /api/dream: start ONE gated, leased turn on a WORKER and return at once ----
        # The turn is minutes long; blocking the request was the central UX gap. The page reads the
        # honest in-flight TURN record (dreaming / done / skipped / refused / error) instead.
        rd = readiness()
        if not rd["can_dream"]:
            return self._send(200, json.dumps({"error": "Not ready yet — " + "; ".join(rd["why"])}),
                              "application/json")
        prompt, label = req.get("prompt"), req.get("label", "custom")
        if not prompt:
            return self._send(200, json.dumps({"error": "That suggestion is no longer available — pick again."}),
                              "application/json")
        if S.gate_prompt(prompt) is None:   # fast, deterministic rejection before any GPU work
            return self._send(200, json.dumps(
                {"error": "That direction isn't something Lucid can make. Try a different turn."}),
                "application/json")
        with TURN_LOCK:
            if TURN["phase"] == "dreaming":
                return self._send(200, json.dumps(
                    {"error": "A dream is already in flight — one beat at a time."}), "application/json")
            epoch = TURN["epoch"]   # this turn's generation; a later start/delete/burn bumps it to supersede
            TURN.update(phase="dreaming", label=label, error=None, started=time.monotonic())
        threading.Thread(target=_run_turn, args=(prompt, label, epoch), daemon=True).start()
        return self._send(202, json.dumps({"ok": True, "started": True}), "application/json")


def _burn_private_on_stop():
    """systemd ExecStop hook (ADR-0016 + ADR-0019 Condition 1): make "wiped when you log out" TRUE for
    EVERY live private session, not just this web process's own SESSION. The ephemeral private queue
    (ADR-0019 §5) can hold N sessions; burning only SESSION left the other N-1 sessions' on-disk sealed
    anchor frames (input/.lucid-priv-<s>/) alive until the next startup reap — "burned on logout" was
    FALSE as-written. Now burn the union of the private queue and the live private dream sessions, then
    clear the tmpfs queue dir. Structurally safe: list_priv_queue()/list_private() only ever return
    PRIVATE sessions, so a normal persistent dream is never destroyed."""
    try:
        sessions = sorted(set(L.ST.list_priv_queue()) | set(L.ST.list_private()))
        wiped = 0
        for s in sessions:
            removed, failed = L.burn(s)
            wiped += len(removed)
            if failed:
                print(f"on-stop burn: session {s!r} left {len(failed)} sink(s) un-wiped: {failed}", flush=True)
        cleared = L.ST.clear_priv_queue_dir()
        tail = "; cleared queue dir" if cleared else ""
        print(f"on-stop burn: wiped {wiped} sink(s) across {len(sessions)} private session(s){tail}", flush=True)
    except Exception as e:
        print(f"on-stop burn skipped: {e}", flush=True)


def main():
    if "--burn-private" in sys.argv:   # systemd ExecStop hook — see _burn_private_on_stop
        _burn_private_on_stop()
        return
    try:  # sweep private clip/frame orphans whose tmpfs session is gone (crash/logout) — ADR-0016
        reaped = L.ST.reap_orphans()
        if reaped:
            print(f"reaped {len(reaped)} orphaned private session(s): {reaped}", flush=True)
    except Exception as e:
        print(f"orphan reap skipped: {e}", flush=True)
    # Warm-keep lease must be released when the server stops: the coordinator owns ComfyUI
    # INDEPENDENTLY of this process, so a held lease would leak ~17 GB if we just exit. Release on
    # SIGTERM/SIGINT (systemd stop / Ctrl-C); the idle reaper covers the walked-away case. A hard
    # SIGKILL of this server still leaks until the daemon restarts — a documented edge (ADR-0015).
    def _shutdown(_signum, _frame):
        _release_lease()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    threading.Thread(target=_lease_reaper, daemon=True).start()
    print(f"Lucid web → http://{HOST}:{PORT}  (session '{SESSION}')", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()

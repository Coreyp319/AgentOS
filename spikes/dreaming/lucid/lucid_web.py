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
  GET  /healthz     200 "ok"  (status-panel reachability probe; X-Lucid-Pid header = owner PID)
  GET  /api/state   readiness (coordinator/comfyui/ollama) + current chain + validated beats
  POST /api/dream   one gated, leased turn (same-origin guarded) — {prompt|choose, label}

Single owner of :PORT. Normally runs as the `agentos-lucid` user service (the status panel links
to it). A bare `python3 lucid_web.py` that finds the port already served by lucid YIELDS (prints +
exits 0, so the service never crash-loops on EADDRINUSE); pass `--takeover` (or LUCID_TAKEOVER=1)
to replace the incumbent, which releases its lease and exits 0 first. Dev helper: integrations/lucid/dev.sh.
Host/port via LUCID_WEB_HOST / LUCID_WEB_PORT (default 127.0.0.1:8765, loopback only).
"""
import base64
import errno
import hashlib
import hmac
import io
import json
import os
import re
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
import lucid_stash as SH   # noqa: E402  (ADR-0028: the encrypted, passphrase-locked private stash)
import lucid_stitch as STCH  # noqa: E402  (download the whole dream as one stitched MP4)
import lucid_priv_drain as PD  # noqa: E402  (ADR-0019 §5 / ADR-0036 D9: in-session drainer for the EPHEMERAL private queue)

HOST = os.environ.get("LUCID_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("LUCID_WEB_PORT", "8765"))
# The CURRENT dream is now mutable (ADR-0028: save & reopen). The web app was hardcoded to one
# session ("web") and clobbered it on every start; it now switches between many named saved dreams
# (the library) and decrypted stash dreams. _CUR holds the active session; SESSION_LOCK serializes a
# switch against the readers. A switch always rides _end_session() (epoch bump + lease release) so a
# beat in flight for the old session can neither clobber the new one nor leak its lease.
_CUR = {"session": os.environ.get("LUCID_WEB_SESSION", "web")}
SESSION_LOCK = threading.Lock()


def cur_session():
    return _CUR["session"]


def set_session(name):
    with SESSION_LOCK:
        _CUR["session"] = name
ORIGIN_OK = {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}
# Extra trusted origins (comma-separated) — e.g. a tailnet HTTPS name behind `tailscale serve`
# so Lucid works on the go. The per-process CSRF token stays the primary guard (a cross-origin
# page can't read it); this only widens the defense-in-depth Origin allowlist to declared names.
ORIGIN_OK |= {o.strip() for o in os.environ.get("LUCID_EXTRA_ORIGINS", "").split(",") if o.strip()}
# the built React bundle (self-hosted, no CDN); served as the primary surface when present
WEB_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "dist")
HAS_DIST = os.path.isdir(WEB_DIST)   # fixed at startup — avoid a stat() on every (polled) GET
_MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
         ".json": "application/json", ".woff2": "font/woff2", ".woff": "font/woff",
         ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm", ".ico": "image/x-icon",
         ".webmanifest": "application/manifest+json", ".map": "application/json"}
# Per-process CSRF token: embedded in the page, required as a header on every state-changing POST.
# A cross-origin page can't read it (same-origin policy), so it closes the missing-Origin CSRF gap.
CSRF = secrets.token_hex(16)


def _share_key():
    """ADR-0027: the file-backed X-Share-Key shared with the phone-share hub (lucid_share.py).
    Read on demand so service start-order doesn't matter; '' (file missing) disables the share
    path entirely. tailnet membership is the real boundary; this key is defense-in-depth."""
    p = os.environ.get("SHARE_KEY_FILE") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "agentos", "share.key")
    try:
        with open(p) as f:
            return f.read().strip()
    except Exception:
        return ""


# Bound the expensive start path (each upload = an image decode + a ~13s vision model load) so a
# burst of /api/start can't exhaust memory / thrash the GPU the coordinator arbitrates (security).
_START_SEM = threading.BoundedSemaphore(2)
# Serialize whole-dream stitches: a download spawns ffmpeg (a re-encode of a long dream is CPU-heavy
# and runs for seconds-to-minutes). One at a time keeps a mashed button / many tabs from oversubscribing
# the cores the box also runs inference on; a second concurrent request gets a clean 503 (review).
_DOWNLOAD_SEM = threading.BoundedSemaphore(1)
# Serialize SAM2 segmentations (ADR-0032): a rapid re-tap must not stack concurrent segment graphs against
# one stale free-VRAM snapshot (the admission TOCTOU) — a second concurrent /api/segment fails open to a point.
_SEG_SEM = threading.BoundedSemaphore(1)
# Serialize per-choice "potential path" previews (ADR-0023): ONE still render at a time, riding the WARM lease,
# so the dwell-speculation never stacks two ~17 GB Wan admissions or co-resides with the real beat. A dedicated
# epoch supersedes a stale node's queue the instant a new dwell (or a pick/start/burn) arrives; the active flag
# spares an in-flight preview from the idle reaper. ALL fail-open — a preview that never arrives is the accepted
# resting state (the card stays on the seed still).
_PREVIEW_SEM = threading.BoundedSemaphore(1)
PREVIEW_LOCK = threading.Lock()
PREVIEW_EPOCH = 0
PREVIEW_ACTIVE = False
PREVIEW_MAX = int(os.environ.get("LUCID_PREVIEW_MAX", "4"))            # at most N previews per dwell (top-first)
PREVIEW_HEADROOM_MIB = int(os.environ.get("LUCID_PREVIEW_HEADROOM_MIB", "1500"))
# Privacy gate (ADR-0023 amendment, responsible-ai-privacy-skeptic consult 2026-06-21): the per-choice preview
# generator renders glimpses of paths the user may NOT take, on a box they live next to. So it is OFF by default
# (the user opts in via a client-side toggle — `previewsEnabled()` — which gates whether the page ever fires the
# trigger), NEVER runs for a private/incognito dream (server-enforced below, regardless of the toggle), and this
# env var is the server-side kill-switch (LUCID_PREVIEWS=0 disables the endpoint outright).
PREVIEWS_ENABLED = os.environ.get("LUCID_PREVIEWS", "1") != "0"
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
# A cold lease admission (predict-before-load) can be REFUSED by a few MB when the live UE wallpaper
# (ADR-0023) sits right at the VRAM knife edge — free VRAM ticks back up within seconds. That refusal
# is TRANSIENT, not a hard "no": surfacing it as a bare "skipped" forced the user to re-submit (the
# "first prompt resets, second renders" bug). So retry the cold spawn a bounded number of times before
# failing open. Only the FIRST beat of a session (or the first after the idle-reap) cold-spawns; once
# warm the held token is reused with no re-admission, so this loop is paid once, not per beat. Module-
# level (not just env) so a test can patch them to 0 for a fast, deterministic retry assertion.
ADMIT_RETRIES = int(os.environ.get("LUCID_ADMIT_RETRIES", "6"))      # extra cold-spawn attempts on refusal
ADMIT_BACKOFF = float(os.environ.get("LUCID_ADMIT_BACKOFF", "2.0"))  # seconds between attempts


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


def _skip_reason():
    """An honest one-line reason a turn failed open at the LEASE stage, for the 'skipped' banner
    (audit 2.1) — so the surface never promises "try again in a moment" when the cause is structural.
    Reads lease_spawn's LAST_REFUSAL. None when there's nothing honest to add (a superseded turn — its
    banner is discarded anyway; a genuine preempt — the calm generic banner fits). The coordinator
    already attempts an `ollama stop` reclaim BEFORE it denies, so a SURVIVING admission shortfall means
    a NON-reclaimable holder (the live wallpaper + another app/game) is using the card — say that."""
    r = getattr(L, "LAST_REFUSAL", None)
    if not r:
        return None
    if r.get("kind") == "unreachable":
        return ("Graphics turn-taking (the GPU coordinator) isn't responding, so the dream couldn't "
                "start. Your desktop is untouched.")
    return ("The graphics card is full — another app (a game or a heavy app) or the live wallpaper is "
            "using it. Close it or wait for it to finish, then try again.")


def _run_turn(prompt, label, epoch=None, length=None, parent_id=None, session=None):
    """Worker: drive ONE leased turn, then record an honest outcome (never a silent no-op).
    Warm-keep: ensure the session's batch lease (spawn ComfyUI once, reuse after) and hand it to
    step() as external — step neither Spawns nor Releases, so ComfyUI stays warm across beats.
    `epoch` (the turn generation captured at /api/dream) gates every state mutation: if a
    start/delete/burn supersedes this turn mid-beat, both step's chain persist and the terminal TURN
    update are discarded. `epoch=None` (tests / untracked callers) keeps the legacy unguarded path."""
    global TOKEN_DEADLINE
    session = session or cur_session()   # captured at /api/dream time so a mid-beat switch can't reroute it
    try:
        if epoch is not None and not _epoch_current(epoch):  # superseded before we even spawned — skip
            return
        if _ensure_lease(epoch) is None:  # coordinator down / GPU busy / ComfyUI cold / superseded — fail open
            phase, err = "skipped", _skip_reason()   # SAY WHY (audit 2.1) — not a silent/misleading skip
        else:
            is_current = (lambda: _epoch_current(epoch)) if epoch is not None else None
            node = L.step(session, prompt, label, external_lease=True, is_current=is_current,
                          length=length, parent_id=parent_id, raise_errors=True)
            phase, err = ("done" if node else "skipped"), None
    except SystemExit as e:        # red-line gate refused the prompt (B3)
        phase, err = "refused", str(e)
    except L.GenerationError as e:  # a SUBSTANTIVE ComfyUI failure (OOM/backend-down/bad-graph/timeout) —
        phase, err = "error", e.user_msg   # surface it honestly, not as a calm "skipped" (audit 1.1/2/5/6)
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


def _run_hero(node_id, epoch=None, session=None):
    """Worker: re-render an existing beat at HERO quality under the warm lease (ADR-0033). Mirrors
    _run_turn's lease + epoch discipline — ensure the session's batch lease, hand it to rerender_hero as
    EXTERNAL (no per-call Spawn/Release), and gate every mutation on `epoch` so a start/delete/burn
    mid-render discards the result. The draft clip is untouched; on success node['hero_clip'] is set."""
    global TOKEN_DEADLINE
    session = session or cur_session()
    try:
        if epoch is not None and not _epoch_current(epoch):    # superseded before we spawned — skip
            return
        if _ensure_lease(epoch) is None:   # coordinator down / GPU busy / ComfyUI cold / superseded — fail open
            phase, err = "skipped", _skip_reason()   # SAY WHY (audit 2.1)
        else:
            is_current = (lambda: _epoch_current(epoch)) if epoch is not None else None
            node = L.rerender_hero(session, node_id, external_lease=True, is_current=is_current,
                                   raise_errors=True)
            phase, err = ("done" if node else "skipped"), None
    except L.GenerationError as e:  # a SUBSTANTIVE failure — surface honestly, not a calm "skipped"
        phase, err = "error", e.user_msg
    except Exception as e:         # noqa: BLE001 — fail open, but SAY SO
        phase, err = "error", str(e)
    with LEASE_LOCK:               # render done — restart the idle countdown only while this turn owns the session
        if CURRENT_TOKEN and (epoch is None or TURN["epoch"] == epoch):
            TOKEN_DEADLINE = time.monotonic() + IDLE_SECS
    with TURN_LOCK:
        if epoch is not None and TURN["epoch"] != epoch:
            return                 # superseded mid-render — don't clobber the fresh state
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
    ready. A knife-edge admission refusal is retried (ADMIT_RETRIES/ADMIT_BACKOFF) before giving up.
    Returns the token, or None = FAIL OPEN (coordinator down / admission refused even after retries /
    ComfyUI never came up) — the caller skips the turn, never forces VRAM. Serialized by LEASE_LOCK so two
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
        # Cold spawn. A coordinator admission REFUSAL (lease_spawn -> None while the daemon is up — the
        # GPU is momentarily a few MB short because the UE wallpaper sits at the knife edge) is TRANSIENT:
        # retry a bounded number of times, letting free VRAM recover, before failing open. The readiness
        # gate already ensured the coordinator was up at request time, so a None here is overwhelmingly a
        # margin refusal, not a dead daemon; a genuinely-down daemon just costs the (bounded) retry window
        # before the honest skip. Re-check _superseded() each pass so a start/burn/delete aborts promptly.
        token = None
        for attempt in range(ADMIT_RETRIES + 1):
            if _superseded():
                return None
            token = L.lease_spawn("batch")                  # admission: predict-before-load (fail open)
            if token is not None:
                break
            if attempt >= ADMIT_RETRIES:                    # transient-refusal budget exhausted — honest skip
                return None
            time.sleep(ADMIT_BACKOFF)                       # let the knife-edge free VRAM recover, re-admit
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
    _cancel_previews()   # the lease is going away — abandon any in-flight preview queue (ADR-0023, before LEASE_LOCK)
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
        with PREVIEW_LOCK:
            previewing = PREVIEW_ACTIVE   # a preview render is in flight — reaping would SIGKILL it (ADR-0023)
        with LEASE_LOCK:
            if (CURRENT_TOKEN and not dreaming and not previewing and TOKEN_DEADLINE
                    and time.monotonic() > TOKEN_DEADLINE):
                print(f"[lucid] idle {IDLE_SECS}s — releasing ComfyUI lease {CURRENT_TOKEN}", flush=True)
                L.lease_release(CURRENT_TOKEN)
                CURRENT_TOKEN, TOKEN_DEADLINE = None, None


# ---------------- ADR-0023: per-choice "potential path" preview worker ----------------
def _cancel_previews():
    """Supersede any in-flight preview worker (a new dwell/node, a pick, a start/delete/burn, or a lease
    teardown). The worker re-checks the epoch before and after each render and abandons its queue on a bump, so
    a stale node's previews never delay the real beat. The bump is the immediate cancel; the warm-lease +
    TURN-phase guards in the worker already stop it when a beat starts or the lease is reclaimed."""
    global PREVIEW_EPOCH
    with PREVIEW_LOCK:
        PREVIEW_EPOCH += 1


def _preview_current(epoch):
    with PREVIEW_LOCK:
        return PREVIEW_EPOCH == epoch


def _run_previews(node_id, beats, epoch, session):
    """Daemon worker: render one still PREVIEW per beat SERIALLY under the WARM lease, top-first, so each choice
    card fills in with its OWN "potential path" instead of the shared seed still. The real beat ALWAYS wins:
    before every render we re-check the cancel epoch, refuse while a beat/hero is in flight, ride the warm lease
    ONLY (never Spawn just to preview), bail if ComfyUI is busy, and gate on free-VRAM headroom — every guard
    fail-open (skip the beat, leave the seed still). The frontend's /api/beats poll surfaces each preview as it
    lands (progressive fill-in)."""
    global TOKEN_DEADLINE, PREVIEW_ACTIVE
    if not PREVIEWS_ENABLED or L.ST.is_private(session):   # belt-and-suspenders privacy gate (consult 2026-06-21)
        return
    if not _PREVIEW_SEM.acquire(blocking=False):     # a prior run is active; the epoch bump already redirected it
        return
    try:
        for b in beats[:PREVIEW_MAX]:
            if not _preview_current(epoch):           # a newer dwell / a pick / a teardown superseded us
                return
            with TURN_LOCK:                           # the real beat (or a hero finalize) owns the GPU — defer
                if TURN["phase"] == "dreaming":
                    return
            with LEASE_LOCK:                          # ride the warm lease ONLY — never spawn just to preview
                warm = CURRENT_TOKEN is not None
            if not warm:
                return
            if L.E._comfy_busy():                     # a render is queued/running — don't stack behind it
                return
            free = L.E._comfy_free_mib()              # cold/None or below the floor -> skip (fail-open)
            if free is None or free < PREVIEW_HEADROOM_MIB:
                return
            with PREVIEW_LOCK:
                PREVIEW_ACTIVE = True
            try:
                L.generate_beat_preview(session, node_id, b, external_lease=True)
            except Exception:
                pass                                  # fail-open: this card simply stays on the seed still
            finally:
                with PREVIEW_LOCK:
                    PREVIEW_ACTIVE = False
                with LEASE_LOCK:                      # a preview just used the lease — it isn't idle; push the deadline
                    if CURRENT_TOKEN:
                        TOKEN_DEADLINE = time.monotonic() + IDLE_SECS
    finally:
        _PREVIEW_SEM.release()


def chain_or_none(session=None):
    try:
        return L.load_chain(session or cur_session())
    except Exception:
        return None


def _download_filename(chain, sess):
    """A safe, friendly download name: the dream's library label, then the session id, then a literal
    fallback. Slugged to [A-Za-z0-9._-] so it drops into Content-Disposition with no quoting /
    header-injection risk. The strip is re-run AFTER the 60-char cut so a boundary that lands on a
    separator can't leave a trailing '-'/'.' before the extension; falling through to the session id
    (not straight to 'dream') keeps two punctuation-only-named dreams from colliding on one filename."""
    for raw in ((chain.get("name") or "").strip(), (sess or "").strip(), "dream"):
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-_.")[:60].strip("-_.")
        if slug:
            return slug + ".mp4"
    return "dream.mp4"


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
    sess = cur_session()
    chain = chain_or_none(sess)
    return {"session": sess, "name": (chain.get("name") if chain else None),
            "readiness": readiness(), "chain": chain,
            "private": L.ST.is_private(sess) or bool(chain and chain.get("private")),
            "turn": turn_snapshot(),
            "engine": {"active": L.E.current_engine(), "options": ["wan", "10eros"]},
            # ADR-0028: the encrypted private stash — status only (never an entry list here; that
            # requires an unlock and rides /api/stash). `saved` = is THIS dream already in the stash?
            "stash": {"exists": SH.exists(), "unlocked": SH.is_unlocked(),
                      "saved_id": SH.opened_sessions().get(sess)}}


def beats(node_id=None):
    """The HELD per-frame menu (ADR-0015 §1: "no reroll"). The model proposes once per node and the
    proposal is persisted on it (lucid_linear.beats_for_node); every later read re-serves it verbatim,
    so the suggestions can't change under the user on a reload / second tab / a skipped fail-open turn.
    `node_id` (optional) is the beat the menu grounds on — default the tip (continue), an earlier id to
    branch a new take from it. Frozen while a beat is in flight — never roll against a picked-from frame."""
    if chain_or_none() is None:
        return []
    with TURN_LOCK:
        rolling = TURN["phase"] != "dreaming"   # in-flight: serve what's held, never roll a new menu
    sess = cur_session()
    try:
        with BEATS_LOCK:                        # one roll per node even under concurrent reads
            raw = L.beats_for_node(sess, node_id, roll=rolling)
        # ADR-0023: tag each beat with its content-address `key` + the `preview` ref (a per-choice "potential
        # path" still, if the dwell worker has rendered it yet — else null). Pure read; the seed still is the
        # default until a preview lands. Decoration is best-effort — never let it drop the (text) menu.
        return L.decorate_beats(sess, node_id, raw)
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
.seglen{margin:6px 0 12px}
.seglen-label{display:block;color:var(--inst-label);font-size:var(--fs-xs);margin-bottom:6px}
.seglen-opts{display:flex;gap:6px;flex-wrap:wrap}
.lenbtn{flex:1 1 0;min-width:44px;text-align:center;background:transparent;border:1px solid var(--hairline);color:var(--inst-muted);border-radius:var(--radius-sm);padding:7px 0;cursor:pointer}
.lenbtn:hover{border-color:var(--inst-blue)}
.lenbtn.on{background:var(--blue-wash);border-color:var(--inst-blue);color:var(--inst-blue);font-weight:600}
input[type=text]{width:100%;background:var(--inst-deep);border:1px solid var(--hairline);color:var(--inst-text);
 border-radius:var(--radius-sm);padding:11px 14px;font:inherit;margin-top:10px}
.note{color:var(--inst-label);font-size:var(--fs-xs)}
a{color:var(--inst-blue)}
.clip{font-size:var(--fs-xs);color:var(--inst-muted);margin:6px 0}.clip.here{color:var(--inst-text)}
.clip small{display:block;color:var(--inst-label);font-size:var(--fs-xs);margin-top:1px}
.clip.here small{color:var(--inst-muted)}
.tag-mature{display:inline-block;font-size:10px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;
 padding:1px 6px;border-radius:999px;color:var(--brand-warm);
 border:1px solid color-mix(in srgb,var(--brand-warm) 45%,transparent);
 background:color-mix(in srgb,var(--brand-warm) 9%,transparent)}
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
let LAST=null,lastSig='',BEATS=[],BEATS_FOR=-1,burnArmed=false,burnMsg='',delArmed=false,delMsg='',lastAnn='',flashT=null,SEGLEN=33;
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
 n.forEach((x,i)=>{const last=i===n.length-1;const txt=x.prompt||x.caption||'';   // the motion prompt behind each clip
  h+=`<div class="clip${last?' here':''}" title="${E(txt||(x.clip?x.clip.split('/').pop():''))}">`
   +`${last?'▸ ':''}${E(x.label||'opening')}`
   +(x.rating==='mature'?` <span class=tag-mature>mature</span>`:``)
   +(txt?`<small>${E(txt)}</small>`:``)+`</div>`;});
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
  if(t.phase==='skipped')h+=`<div class=banner>${t.error?E(t.error)
    :'That beat was skipped — the graphics card was busy. Your desktop is untouched.'} Nothing was lost.</div>`;
  else if(t.phase==='error')h+=`<div class="banner bad">${t.error?E(t.error)
    :"That clip didn't come through."} Your desktop is untouched.</div>`;
  else if(t.phase==='refused')h+=`<div class=banner>That direction isn't something Lucid can make. `
   +`Try a different turn.</div>`;
  if(r.can_dream){
   h+=`<div class=seglen><span class="seglen-label">Length of the next moment</span><div class="seglen-opts">`
    +[[17,'1s'],[33,'2s'],[49,'3s']].map(o=>  // capped at 3s: 720x1280 GGUF beats past 49f run past the gen timeout
      `<button type=button data-len="${o[0]}" class="lenbtn${SEGLEN===o[0]?' on':''}" `
      +`onclick="setLen(${o[0]})">${o[1]}</button>`).join('')
    +`</div></div>`
    +`<div id=beats><div class=note>considering the next moves…</div></div>`
    +`<input id=own type=text placeholder="…or type what happens next" `
    +`onkeydown="if(event.key==='Enter')dreamOwn()">`;
  }else h+=`<div class=note>Choosing what happens next switches on once everything above is ready.</div>`;
  h+=`</div>`;
 }
 return h;}

function announce(s){const t=s.turn;let m='';
 if(t.phase==='dreaming')m='Dreaming this beat — a few minutes.';
 else if(t.phase==='skipped')m=t.error||'That beat was skipped; your desktop is untouched.';
 else if(t.phase==='error')m=t.error||'That clip did not come through.';
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

// A restart mints a fresh per-process CSRF token, so an OLD tab's POSTs get a silent 403 and the
// button just appears dead. Detect that one case and recover honestly: tell the user, then reload
// to pick up a fresh token. Returns parsed JSON, or null when it has taken over a stale session.
function staleReload(){flash('Lucid restarted — refreshing your session…');setTimeout(()=>location.reload(),1400);}
async function jpost(url,body){
 const res=await fetch(url,{method:'POST',
  headers:{'Content-Type':'application/json','X-Lucid-Token':CSRF},
  body:body===undefined?undefined:JSON.stringify(body)});
 if(res.status===403){staleReload();return null;}   // stale CSRF after a restart — reload to recover
 return res.json();}

async function post(body){let j;
 try{j=await jpost('/api/dream',body);}
 catch(e){flash('Could not reach Lucid — try again.');return;}
 if(j===null)return;                 // stale session: staleReload() is taking over
 if(j.error)flash(j.error);
 lastSig='';load();}                // force an immediate repaint into the 'dreaming' state
function setLen(n){SEGLEN=n;document.querySelectorAll('.lenbtn').forEach(b=>b.classList.toggle('on',+b.dataset.len===n));}
function dream(i){const b=BEATS[i];if(b)post({prompt:b.prompt,label:b.label,length:SEGLEN});}
function dreamOwn(){const el=document.getElementById('own');const v=el?el.value.trim():'';
 if(v)post({prompt:v,label:'custom',length:SEGLEN});}

// --- seed upload + B2 likeness guard (preserved from the upload feature; consent flow intact) ---
function fileB64(f){return new Promise(r=>{const rd=new FileReader();rd.onload=()=>r(rd.result.split(',')[1]);rd.readAsDataURL(f);});}
async function startDream(consent){
 const priv=document.getElementById('priv').checked, f=document.getElementById('img').files[0];
 const txt=document.getElementById('opentext'), text=txt?txt.value.trim():'';
 const msg=document.getElementById('startmsg'), btn=document.getElementById('startbtn');
 const body={private:priv};
 if(f){body.image_b64=await fileB64(f);body.consent=!!consent;msg.textContent='🔎 checking your image for real-person likeness…';btn.disabled=true;}
 else if(text){body.text=text;body.consent=!!consent;msg.textContent='✦ painting your opening…';btn.disabled=true;}
 let j;try{j=await jpost('/api/start',body);}
 catch(e){if(btn)btn.disabled=false;msg.textContent='Could not reach Lucid — try again.';return;}
 if(j===null){if(btn)btn.disabled=false;return;}   // stale session — reloading to refresh the token
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
 try{j=await jpost('/api/burn');}
 catch(e){flash('Burn could not run — try again.');return;}
 if(j===null)return;
 if(j.failed&&j.failed.length)burnMsg='!Some traces could NOT be wiped and remain on disk: '
  +j.failed.join('; ')+'. They are retried at next start; delete by hand to be certain.';
 else burnMsg='This dream is gone — '+(j.burned||0)+' location(s) wiped.';
 BEATS=[];BEATS_FOR=-1;lastSig='';load();}

function armDel(){delArmed=true;delMsg='';if(LAST)paint(LAST);}
function cancelDel(){delArmed=false;if(LAST)paint(LAST);}
async function doDelete(){delArmed=false;let j;
 try{j=await jpost('/api/delete');}
 catch(e){flash('Delete could not run — try again.');return;}
 if(j===null)return;
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

    def _send(self, code, body, ctype, extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
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
        so the path is never user-supplied — no traversal surface; loopback-only.

        ADR-0023: /api/frame ALSO serves a per-choice "potential path" PREVIEW via `?preview=<ref>` — a still
        the dwell worker rendered for a gutter beat. The ref is UNTRUSTED, so it is validated to be EXACTLY a
        name THIS session minted (L._valid_preview_ref: `<session>_bp_<id>_<hex>.png`, optional sealed subdir,
        must exist) before it ever reaches the filesystem — same no-traversal discipline as the segmask ref."""
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        if kind == "frame" and q.get("preview", [""])[0]:
            ref = L._valid_preview_ref(cur_session(), q.get("preview", [""])[0])
            if not ref:
                return self._send(404, "no such preview", "text/plain")
            p = os.path.join(L.E.INPUT_DIR, ref)
            if not os.path.isfile(p):
                return self._send(404, "not found", "text/plain")
            with open(p, "rb") as f:
                data = f.read()
            return self._send_raw(200, data, _MIME.get(os.path.splitext(p)[1].lower(),
                                                        "application/octet-stream"), cache=False)
        try:
            nid = int(q.get("id", [""])[0])
        except ValueError:
            return self._send(400, "bad id", "text/plain")
        chain = chain_or_none()
        node = next((n for n in (chain["nodes"] if chain else []) if n.get("id") == nid), None)
        if node is None:
            return self._send(404, "no such frame", "text/plain")
        if kind == "clip":
            # ADR-0033: play the finalized HERO re-render when present (the same shot in HD), falling back to
            # the draft. The client's `?v=` cache-buster is the node's hero/clip ref, so a finalize refetches.
            hero = node.get("hero_clip")
            p = hero if (hero and os.path.isfile(hero)) else node.get("clip")
        else:
            of = node.get("out_frame")
            p = os.path.join(L.E.INPUT_DIR, of) if of else None
        if not p or not os.path.isfile(p):
            return self._send(404, "not found", "text/plain")
        with open(p, "rb") as f:
            data = f.read()
        self._send_raw(200, data, _MIME.get(os.path.splitext(p)[1].lower(), "application/octet-stream"),
                       cache=False)

    def _serve_thumb(self):
        """A saved LIBRARY dream's tip frame, for the grid thumbnail (ADR-0028). `session` is
        valid_session-checked and must be a NON-private library dream — a private frame is never
        served here. The frame ref comes from the chain (never user-supplied), resolved by lucid_store
        (strict basename, no traversal)."""
        from urllib.parse import urlparse, parse_qs
        sess = parse_qs(urlparse(self.path).query).get("session", [""])[0]
        if not L.ST.valid_session(sess):
            return self._send(400, "bad session", "text/plain")
        chain = chain_or_none(sess)
        if chain is None or chain.get("private"):
            return self._send(404, "no thumb", "text/plain")
        nodes = chain.get("nodes") or []
        of = nodes[-1].get("out_frame") if nodes else None
        try:
            p = L.ST.frame_abs(sess, False, of) if of else None
        except Exception:
            p = None
        if not p or not os.path.isfile(p):
            return self._send(404, "not found", "text/plain")
        with open(p, "rb") as f:
            data = f.read()
        self._send_raw(200, data, _MIME.get(os.path.splitext(p)[1].lower(), "application/octet-stream"),
                       cache=False)

    def _send_file(self, path, ctype, download_name):
        """Stream a finished file as an attachment, chunked (never loads the whole MP4 into RAM —
        a stitched dream can be tens of MB). `download_name` is an ASCII slug, so it's safe to drop
        verbatim into Content-Disposition (no CRLF, no quote-escaping needed)."""
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(262144)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _serve_download(self):
        """Stitch a dream's clips into one MP4 and stream it as a download. `session` (optional)
        selects any saved library dream; absent = the current dream (which MAY be private — its
        bytes are then stitched in a tmpfs workdir so no private byte touches shared disk, and a
        crash leftover is swept by lucid_store.clear_download_scratch). The clip set comes only from
        the server-held chain (no user path reaches ffmpeg). Like the sibling media GETs (/api/clip,
        /api/frame) this carries no CSRF/Origin check — same-origin policy keeps a cross-origin page
        from reading the bytes, and serialization (below) bounds the only real cost (an ffmpeg spawn).
        Works even when ComfyUI/Ollama are down — it just reads clips off disk."""
        import shutil
        from urllib.parse import urlparse, parse_qs
        sess = parse_qs(urlparse(self.path).query).get("session", [""])[0] or cur_session()
        if not L.ST.valid_session(sess):
            return self._send(400, "bad session", "text/plain")
        chain = chain_or_none(sess)
        if chain is None:
            return self._send(404, "no such dream", "text/plain")
        private = L.ST.is_private(sess) or bool(chain.get("private"))
        paths = STCH.clip_spine(chain)
        if not paths:
            return self._send(404, "nothing to download yet — this dream has no clips", "text/plain")
        if not STCH.have_ffmpeg():
            return self._send(503, "video stitching unavailable (ffmpeg not found)", "text/plain")
        if not _DOWNLOAD_SEM.acquire(blocking=False):   # one stitch at a time — never oversubscribe cores
            return self._send(503, "a download is already being prepared — try again in a moment", "text/plain")
        # EVERYTHING after the acquire lives under the finally that releases the permit — INCLUDING
        # make_download_workdir, which CAN raise (a full tmpfs, or the sealed-dir path refusing a planted
        # symlink). If it raised outside the try the permit would leak and every later download would 503
        # "already being prepared" FOREVER, until the process restarts (resource-safety review: the safety
        # serialization must not be able to wedge the feature it guards). _send_file deliberately stays
        # OUTSIDE the inner excepts: a client disconnect mid-stream raises an OSError, and we must let it
        # propagate (the finally still cleans up) rather than catch it and try to write a 500 to a socket
        # that's already closed.
        workdir = None
        try:
            try:
                workdir = L.ST.make_download_workdir(private)   # tmpfs (sealed) for private; OS temp otherwise
            except OSError:
                return self._send(500, "could not prepare the download (no scratch space)", "text/plain")
            out = os.path.join(workdir, "dream.mp4")
            try:
                STCH.stitch(paths, out)
            except STCH.StitchError as e:
                return self._send(500, f"could not stitch the dream: {e}", "text/plain")
            self._send_file(out, "video/mp4", _download_filename(chain, sess))
        finally:
            if workdir:
                shutil.rmtree(workdir, ignore_errors=True)
            _DOWNLOAD_SEM.release()

    def _json200(self, obj):
        return self._send(200, json.dumps(obj), "application/json")

    def _handle_library_or_stash(self, path, req):
        """ADR-0028 — save & reopen (the library) and the encrypted private stash. CSRF + Origin were
        already checked by do_POST. Every reply is a 200 {ok|error} the surface narrates."""
        # ---------- library: reopen / rename a saved (non-private) dream ----------
        if path == "/api/open":
            sess = req.get("session")
            if not (isinstance(sess, str) and L.ST.valid_session(sess)):
                return self._json200({"error": "bad session"})
            chain = chain_or_none(sess)
            if chain is None or chain.get("private"):
                return self._json200({"error": "no such saved dream"})
            _end_session()                       # leave the current dream cleanly (it stays saved)
            set_session(sess)
            return self._json200({"ok": True, "session": sess})
        if path == "/api/rename":                # rename a saved library dream — the current one, or any by `session`
            sess = req.get("session")
            if sess is None:                     # no session -> the CURRENT dream (back-compat)
                sess = cur_session()
            elif not (isinstance(sess, str) and L.ST.valid_session(sess)):   # path-safety, mirrors /api/open
                return self._json200({"error": "bad session"})
            chain = chain_or_none(sess)
            if chain is None or chain.get("private"):
                return self._json200({"error": "no such saved dream"})
            chain["name"] = str(req.get("name") or "").strip()[:80] or None
            L.ST.save_chain(sess, False, chain)
            return self._json200({"ok": True, "name": chain["name"], "session": sess})

        # ---------- stash: encrypted, passphrase-locked private dreams ----------
        if path == "/api/stash/init":
            try:
                SH.init(req.get("passphrase") or "")
            except FileExistsError:
                return self._json200({"error": "a stash already exists"})
            except ValueError as e:
                return self._json200({"error": str(e)})
            return self._json200({"ok": True})
        if path == "/api/stash/unlock":
            try:
                ok = SH.unlock(req.get("passphrase") or "")
            except FileNotFoundError:
                return self._json200({"error": "no stash yet — set a passphrase to create one"})
            except ValueError:
                ok = False
            return self._json200({"ok": bool(ok), **({} if ok else {"error": "wrong passphrase"})})
        if path == "/api/stash/lock":
            was_open = cur_session() in SH.opened_sessions()
            SH.reseal_opened(burn=True)          # seal any open working copy, then forget the key
            SH.lock()
            if was_open:                         # the open dream was just burned -> clean slate
                set_session(L.ST.new_session_id(None))
            return self._json200({"ok": True})
        if path == "/api/stash/save":
            sess = cur_session()
            if not SH.is_unlocked():
                return self._json200({"error": "unlock the stash first"})
            if not L.ST.is_private(sess):
                return self._json200({"error": "only a private dream can be saved to the stash"})
            try:
                entry = SH.save_session(sess, name=req.get("name"))
            except Exception as e:
                return self._json200({"error": f"could not save: {e}"})
            return self._json200({"ok": True, "id": entry["id"], "name": entry["name"]})
        if path == "/api/stash/open":
            if not SH.is_unlocked():
                return self._json200({"error": "unlock the stash first"})
            sid = req.get("id")
            if not SH._valid_id(sid):
                return self._json200({"error": "bad id"})
            try:
                _end_session()
                sess, _chain = SH.open_into(sid)
            except Exception as e:
                return self._json200({"error": f"could not open: {e}"})
            set_session(sess)
            return self._json200({"ok": True, "session": sess})
        if path == "/api/stash/rename":
            if not SH.is_unlocked():
                return self._json200({"error": "unlock the stash first"})
            return self._json200({"ok": bool(SH.rename(req.get("id"), req.get("name") or ""))})
        if path == "/api/stash/delete":
            if not SH.is_unlocked():
                return self._json200({"error": "unlock the stash first"})
            sid = req.get("id")
            if not SH._valid_id(sid):
                return self._json200({"error": "bad id"})
            removed = SH.delete(sid)
            if cur_session() == SH.restore_name(sid):   # the open copy was burned by delete()
                set_session(L.ST.new_session_id(None))
            return self._json200({"ok": bool(removed)})
        if path == "/api/stash/passphrase":
            try:
                ok = SH.change_passphrase(req.get("old") or "", req.get("new") or "")
            except FileNotFoundError:
                return self._json200({"error": "no stash yet"})
            except ValueError as e:
                return self._json200({"error": str(e)})
            return self._json200({"ok": bool(ok), **({} if ok else {"error": "wrong current passphrase"})})
        return self._send(404, "not found", "text/plain")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":   # X-Lucid-Pid lets a second instance ID this owner (yield/takeover)
            return self._send(200, "ok", "text/plain", {"X-Lucid-Pid": str(os.getpid())})
        if path == "/api/state":
            return self._send(200, json.dumps(state()), "application/json")
        if path == "/api/beats":   # slow (Ollama) — fetched separately so the page never blocks
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query).get("node", [None])[0]   # which beat to grow from (default tip)
            nid = int(q) if (q is not None and q.lstrip("-").isdigit()) else None
            return self._send(200, json.dumps({"beats": beats(nid)}), "application/json")
        if path == "/api/clip":
            return self._serve_media("clip")
        if path == "/api/frame":
            return self._serve_media("frame")
        if path == "/api/download":   # stitch this dream's clips into one MP4 (attachment download)
            return self._serve_download()
        if path == "/api/queue":   # ADR-0019: the durable held + needs-review board (read-only)
            return self._send(200, json.dumps(H.board()), "application/json")
        if path == "/api/library":   # ADR-0028: the saved (non-private) dream library
            return self._send(200, json.dumps({"dreams": L.ST.list_persistent()}), "application/json")
        if path == "/api/library/thumb":   # ADR-0028: a saved dream's tip frame (grid thumbnail)
            return self._serve_thumb()
        if path == "/api/stash":   # ADR-0028: stash status; entry list ONLY when unlocked
            out = {"exists": SH.exists(), "unlocked": SH.is_unlocked()}
            if SH.is_unlocked():
                out["dreams"] = SH.listing()
            return self._send(200, json.dumps(out), "application/json")
        if HAS_DIST:                                 # the React build is the primary surface
            return self._serve_static(path)
        if path in ("/", "/index.html"):             # fallback: the inline vanilla page (no build present)
            return self._send(200, PAGE.replace("__CSRF__", CSRF), "text/html; charset=utf-8")
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/api/dream", "/api/hero", "/api/burn", "/api/start", "/api/delete", "/api/engine",
                        "/api/note", "/api/note/delete", "/api/segment", "/api/beat-previews",
                        "/api/queue/retry", "/api/queue/dismiss", "/api/queue/approve",
                        # ADR-0028: save & reopen + the encrypted private stash
                        "/api/open", "/api/rename", "/api/stash/init", "/api/stash/unlock",
                        "/api/stash/lock", "/api/stash/save", "/api/stash/open", "/api/stash/rename",
                        "/api/stash/delete", "/api/stash/passphrase"):
            return self._send(404, "not found", "text/plain")
        # CSRF: a state-changing POST must carry the per-process token embedded in the page (a
        # cross-origin page can't read it). Fail closed. Defense-in-depth: reject a bad Origin too.
        # ADR-0027: /api/start ALSO accepts a valid file-backed X-Share-Key from the on-box share
        # hub (lucid_share.py) — the iOS Shortcut can't read the per-process CSRF token. A non-empty
        # key AND a non-empty header are both required (so ""=="" can never authenticate).
        sk = _share_key()
        share_ok = (path == "/api/start" and sk != ""
                    and bool(self.headers.get("X-Share-Key"))
                    and hmac.compare_digest(self.headers.get("X-Share-Key", ""), sk))
        if not share_ok:
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
        if path == "/api/engine":   # ADR-0023: live i2v engine toggle (wan <-> 10eros)
            prev = L.E.current_engine()
            active = L.E.set_engine(req.get("engine"))
            if active != prev:
                _release_lease()    # next beat re-admits/re-spawns with the new engine's VRAM estimate
            return self._send(200, json.dumps({"ok": True, "engine": active}), "application/json")
        if path == "/api/note":   # ADR-0023/0025: attach a moment annotation to a node (spatial+semantic)
            # Tiny + synchronous (no worker): just a guarded chain write. add_note clamps t + the optional
            # (x,y,r) region, validates the tag, and red-line-gates the untrusted text — a bad tag / failing
            # text / non-numeric coord maps to a 200 {error}. x,y,r are OPTIONAL (legacy time-only notes).
            try:
                note = L.add_note(cur_session(), int(req.get("node")), req.get("t", 0.0),
                                  req.get("tag"), req.get("text", ""),
                                  x=req.get("x"), y=req.get("y"), r=req.get("r"),
                                  mask=req.get("mask"))   # ADR-0032: optional validated segmentation-mask ref
            except (ValueError, TypeError) as e:
                return self._send(200, json.dumps({"error": str(e)}), "application/json")
            return self._send(200, json.dumps({"ok": True, "note": note}), "application/json")
        if path == "/api/refine":   # ADR-0023: sharpen the viewer's OWN rough next-beat idea at a juncture.
            # Model PROPOSES the rewrite; the deterministic red-line gate DISPOSES (on both the rough input
            # and the refined output). Tiny + synchronous: an Ollama text/vision call only — NO lease, NO
            # turn (never supersedes a dream), NO chain write — so it's safe during the dwell, fails honest,
            # and needs no can_dream gate (it only needs the narrator; an unreachable one returns a reason).
            return self._json200(L.refine_beat(cur_session(), req.get("text", ""), req.get("node")))
        if path == "/api/segment":   # ADR-0032: tap -> SAM2 object mask (warm-only, headroom-gated, fail-open)
            # Extract the tapped frame into a SEALED input location, segment under the WARM lease (segment_at
            # gates on free VRAM; cold/contended -> None), validate the mask, and return it INLINE as a
            # no-store data-URL (NOT a new unauthenticated GET). Any failure -> {ok:false} and the client
            # saves a plain point. The transient frame is single-use (deleted). Private masks seal + burn.
            sess = cur_session()
            # (ADR §5) refuse during a LIVE beat — the calm, legible downgrade; also skips a wasted ffmpeg
            # extract. And SERIALIZE: a rapid re-tap must not stack concurrent SAM2 graphs against one stale
            # free-VRAM read (the segment-admission TOCTOU) — non-blocking, fail-open to a point on contention.
            with TURN_LOCK:
                if TURN["phase"] == "dreaming":
                    return self._send(200, json.dumps({"ok": False, "reason": "GPU busy — saved as a point"}),
                                      "application/json")
            # BLOCKING with a timeout (not instant-fail): a tap WAITS for a prior segment (~seconds) and then
            # proceeds — only a genuinely-stuck segmenter (>20s) downgrades to a point. segment_at itself bails
            # fast when ComfyUI is busy, so the lock is never held long.
            if not _SEG_SEM.acquire(timeout=20):
                return self._send(200, json.dumps({"ok": False, "reason": "segmenter busy — saved as a point"}),
                                  "application/json")
            try:
                try:
                    node_id = int(req.get("node"))
                    t = max(0.0, float(req.get("t", 0.0)))
                    x, y = float(req.get("x")), float(req.get("y"))
                except (ValueError, TypeError):
                    return self._send(200, json.dumps({"ok": False, "reason": "bad request"}), "application/json")
                chain = L.load_chain(sess)
                node = next((nn for nn in chain.get("nodes", []) if nn.get("id") == node_id), None)
                clip = node.get("clip") if node else None
                private = L.ST.is_private(sess) or bool(chain.get("private"))
                wk_name, wk_abs = L.ST.frame_ref(sess, private, f"{sess}_segmask_work.png")
                # Resolve the frame to segment: a CLIP node -> extract the tapped moment (transient, deleted);
                # a clip-LESS node (the opening still, tappable via idx===0) -> its stored out_frame directly
                # (already an INPUT_DIR-relative still — segment it in place, never delete the kept frame).
                seg_name, transient = None, None
                if clip:
                    fr_name, fr_abs = L.ST.frame_ref(sess, private, f"{sess}_segframe.png")
                    if L.E.extract_frame_at(clip, t, fr_name, out_path=fr_abs):
                        seg_name, transient = fr_name, fr_abs
                elif node and node.get("out_frame"):
                    seg_name = node["out_frame"]
                if not seg_name:
                    return self._send(200, json.dumps({"ok": False, "reason": "no frame to tag"}), "application/json")
                res = L.E.segment_at(seg_name, x, y, wk_abs)
                if transient:
                    try:
                        os.remove(transient)            # the extracted clip frame is single-use
                    except OSError:
                        pass
                if not res:                             # cold / contended / empty / degenerate -> save a point
                    return self._send(200, json.dumps({"ok": False, "reason": "segmenter unavailable"}),
                                      "application/json")
                # CONTENT-ADDRESS the mask (append-only — ADR-0005): a re-tap with a new silhouette gets a new
                # name and never clobbers a kept mask; an identical mask dedupes to the same file.
                with open(wk_abs, "rb") as f:
                    data = f.read()
                h = hashlib.blake2b(data, digest_size=6).hexdigest()
                mk_name, mk_abs = L.ST.frame_ref(sess, private, f"{sess}_segmask_{h}.png")
                os.replace(wk_abs, mk_abs)
                # preview = a FEATHERED copy of the mask (smooths the low-res stair-step edge for the overlay);
                # the stored guide mask `data` stays binarized. Falls back to the raw mask if smoothing fails.
                prev = L.E.smooth_mask_preview(mk_abs) or data
                preview = "data:image/png;base64," + base64.b64encode(prev).decode()  # gated, no-store response
                return self._send(200, json.dumps({"ok": True, "mask": mk_name, "preview": preview}),
                                  "application/json")
            finally:
                _SEG_SEM.release()
        if path == "/api/beat-previews":   # ADR-0023: render per-choice "potential path" stills during the DWELL
            # Opportunistic + fail-open: start a worker that renders one still per beat SERIALLY under the WARM
            # lease, so the cards stop all showing the seed image. Returns immediately; the page's /api/beats poll
            # surfaces each preview as it lands. NEVER spawns a lease (skip if cold) and NEVER runs during a live
            # beat — the real beat always wins. A new node supersedes a prior dwell's queue (epoch bump).
            nv = req.get("node")
            node_id = (int(nv) if isinstance(nv, (int, float))
                       or (isinstance(nv, str) and nv.lstrip("-").isdigit()) else None)
            sess = cur_session()
            # PRIVACY (consult 2026-06-21): never speculate on un-taken paths for a private/incognito dream — that
            # is the one dream the user most signaled "minimize this". Server-enforced regardless of the client
            # toggle. The env kill-switch (LUCID_PREVIEWS=0) disables the feature outright.
            if not PREVIEWS_ENABLED or L.ST.is_private(sess):
                return self._send(200, json.dumps({"ok": True, "started": 0}), "application/json")
            with LEASE_LOCK:                              # ride the warm lease only — never spawn just to preview
                warm = CURRENT_TOKEN is not None
            with TURN_LOCK:                              # never speculate while a real beat is in flight
                busy = TURN["phase"] == "dreaming"
            if not warm or busy:
                return self._send(200, json.dumps({"ok": True, "started": 0}), "application/json")
            try:
                bts = L.beats_for_node(sess, node_id, roll=False)   # serve-only: the dwell never rolls the menu
            except Exception:
                bts = []
            if not bts:
                return self._send(200, json.dumps({"ok": True, "started": 0}), "application/json")
            _cancel_previews()                           # supersede any prior dwell/node's worker
            with PREVIEW_LOCK:
                ep = PREVIEW_EPOCH
            threading.Thread(target=_run_previews, args=(node_id, list(bts), ep, sess), daemon=True).start()
            return self._send(202, json.dumps({"ok": True, "started": len(bts[:PREVIEW_MAX])}), "application/json")
        if path == "/api/note/delete":   # ADR-0023: drop a moment annotation by id (idempotent)
            try:
                ok = L.remove_note(cur_session(), int(req.get("node")), req.get("id"))
            except (ValueError, TypeError) as e:
                return self._send(200, json.dumps({"error": str(e)}), "application/json")
            return self._send(200, json.dumps({"ok": bool(ok)}), "application/json")
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
        if path in ("/api/open", "/api/rename", "/api/stash/init", "/api/stash/unlock",
                    "/api/stash/lock", "/api/stash/save", "/api/stash/open", "/api/stash/rename",
                    "/api/stash/delete", "/api/stash/passphrase"):
            return self._handle_library_or_stash(path, req)   # ADR-0028 (save & reopen + private stash)
        if path == "/api/burn":   # wipe the CURRENT private session's every sink (ADR-0016)
            sess = cur_session()
            _end_session()        # invalidate any in-flight beat + reclaim the GPU lease BEFORE wiping
            removed, failed = L.burn(sess)
            set_session(L.ST.new_session_id(None))   # back to a clean empty slate (library / Start)
            return self._send(200, json.dumps({"ok": not failed, "burned": len(removed),
                                               "failed": failed}), "application/json")
        if path == "/api/delete":   # delete a dream's every sink (a private one -> burn). Optional
            # `session` deletes ANY saved dream from the library; omitted = the current dream.
            req_sess = req.get("session")
            target = req_sess if (isinstance(req_sess, str) and L.ST.valid_session(req_sess)) else cur_session()
            is_cur = (target == cur_session())
            if is_cur:
                _end_session()    # invalidate any in-flight beat + reclaim the GPU lease BEFORE wiping
            if L.ST.is_private(target):
                removed, failed = L.burn(target)
            else:
                removed, failed = L.ST.purge_persistent(target)
            if is_cur:
                set_session(L.ST.new_session_id(None))   # deleted the active dream -> clean slate
            return self._send(200, json.dumps({"ok": not failed, "deleted": len(removed),
                                               "failed": failed}), "application/json")
        if path == "/api/start":  # begin a NEW dream; uploaded image (B2-gated in start) or synthetic frame
            private = bool(req.get("private"))
            consent = bool(req.get("consent"))
            name = req.get("name")   # human label for the library (ADR-0028)
            # the "Mature dream" toggle -> a user-declared content floor on the chain, so every beat menu is
            # steered mature from frame 0 instead of waiting for the conservative per-frame VLM to flip. Only
            # ever floors UP; the red line is independent + code-enforced (lucid_safety). Default off = today.
            rating_floor = "mature" if bool(req.get("mature")) else None
            img_b64 = req.get("image_b64")
            if not _START_SEM.acquire(blocking=False):   # bound concurrent decode + vision-model loads
                return self._send(429, json.dumps({"error": "busy — try again in a moment"}), "application/json")
            try:
                # Starting a new dream LEAVES the current one intact (it stays in the library) — no clear.
                # Only tear down the current session's in-flight beat + GPU lease; mint a fresh session id
                # so named dreams coexist. Switch the current session ONLY after start() succeeds.
                _end_session()
                new_sess = L.ST.new_session_id(name)

                def _started(p):
                    set_session(new_sess)
                    return self._send(200, json.dumps(
                        {"ok": True, "private": p, "session": new_sess}), "application/json")

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
                        L.start(new_sess, seed, private=private, consent=consent, name=name, rating_floor=rating_floor)  # B2 INSIDE start()
                    except L.SeedBlocked as e:
                        return self._send(200, json.dumps({"blocked": True, **e.verdict.as_dict()}), "application/json")
                    except Exception as e:
                        return self._send(200, json.dumps({"error": f"start failed: {e}"}), "application/json")
                    finally:
                        try:
                            os.remove(seed)
                        except OSError:
                            pass
                    return _started(private)
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
                        L.start(new_sess, seed, private=private, consent=consent, name=name, rating_floor=rating_floor)  # B2 on the gen
                    except L.SeedBlocked as e:
                        return self._send(200, json.dumps({"blocked": True, **e.verdict.as_dict()}), "application/json")
                    except Exception as e:
                        return self._send(200, json.dumps({"error": f"start failed: {e}"}), "application/json")
                    finally:
                        try:
                            os.remove(seed)
                        except OSError:
                            pass
                    return _started(private)
                # no image, no text -> a server-generated abstract opening (trusted; no real person)
                seed = _synthetic_opening()
                try:
                    L.start(new_sess, seed, private=private, _trusted_seed=True, name=name, rating_floor=rating_floor)
                except Exception as e:
                    return self._send(200, json.dumps({"error": f"start failed: {e}"}), "application/json")
                finally:
                    try:
                        os.remove(seed)
                    except OSError:
                        pass
                return _started(private)
            finally:
                _START_SEM.release()
        if path == "/api/hero":   # ADR-0033: re-render a chosen beat at HERO quality (two-tier finalize)
            # Reuses the beat's stored seed/prompt/anchor — the SAME shot at 20-step fidelity. Shares the
            # one-beat-in-flight TURN lock + warm lease with /api/dream, so a finalize and a draft beat can't
            # run at once (they serialize on the single GPU lease). Returns 202; the page polls the TURN record.
            rd = readiness()
            if not rd["can_dream"]:
                return self._send(200, json.dumps({"error": "Not ready yet — " + "; ".join(rd["why"])}),
                                  "application/json")
            nv = req.get("node")
            node_id = (int(nv) if isinstance(nv, (int, float))
                       or (isinstance(nv, str) and nv.lstrip("-").isdigit()) else None)
            if node_id is None:
                return self._send(200, json.dumps({"error": "no beat to finalize"}), "application/json")
            with TURN_LOCK:
                if TURN["phase"] == "dreaming":
                    return self._send(200, json.dumps(
                        {"error": "A dream is already in flight — one beat at a time."}), "application/json")
                epoch = TURN["epoch"]   # a later start/delete/burn bumps it to supersede this render
                TURN.update(phase="dreaming", label="Finalizing in HD", error=None, started=time.monotonic())
            _cancel_previews()   # a finalize owns the GPU now — abandon any in-flight preview queue (ADR-0023)
            threading.Thread(target=_run_hero, args=(node_id, epoch, cur_session()), daemon=True).start()
            return self._send(202, json.dumps({"ok": True, "started": True}), "application/json")
        # ---- /api/dream: start ONE gated, leased turn on a WORKER and return at once ----
        # The turn is minutes long; blocking the request was the central UX gap. The page reads the
        # honest in-flight TURN record (dreaming / done / skipped / refused / error) instead.
        rd = readiness()
        if not rd["can_dream"]:
            return self._send(200, json.dumps({"error": "Not ready yet — " + "; ".join(rd["why"])}),
                              "application/json")
        prompt, label = req.get("prompt"), req.get("label", "custom")
        length = req.get("length")   # optional next-segment length; lucid_engine.clamp_length disposes
        pv = req.get("parent")       # optional: branch a new take from this beat (default None = the tip)
        parent_id = int(pv) if isinstance(pv, (int, float)) or (isinstance(pv, str) and pv.lstrip("-").isdigit()) else None
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
        _cancel_previews()   # the user picked — abandon any in-flight preview queue; the real beat wins (ADR-0023)
        # capture the session NOW so a switch mid-beat can't reroute this turn (the epoch still guards persist)
        threading.Thread(target=_run_turn, args=(prompt, label, epoch, length, parent_id, cur_session()),
                         daemon=True).start()
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
        dl = L.ST.clear_download_scratch()   # wipe any in-flight download-stitch scratch (may be private)
        tail = (("; cleared queue dir" if cleared else "")
                + ("; cleared download scratch" if dl else ""))
        print(f"on-stop burn: wiped {wiped} sink(s) across {len(sessions)} private session(s){tail}", flush=True)
    except Exception as e:
        print(f"on-stop burn skipped: {e}", flush=True)


def _incumbent_lucid_pid():
    """PID of the lucid already serving :PORT (read from its /healthz `X-Lucid-Pid` header), or 0 if
    the port's holder doesn't answer as lucid (free, or some other service). Decides a collision."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/healthz", timeout=1.0) as r:
            pid = r.headers.get("X-Lucid-Pid", "")
        return int(pid) if pid.isdigit() else 0
    except Exception:
        return 0


def _terminate_incumbent(pid, grace=6.0):
    """SIGTERM the incumbent lucid — it releases its warm-keep lease and exits 0 via its own handler —
    then wait for it to go, escalating to SIGKILL if it won't. Returns once the PID is gone."""
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)            # liveness probe (signal 0 — no-op while alive)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        if sig is signal.SIGKILL:
            print(f"[lucid] incumbent pid {pid} survived SIGKILL", flush=True)


def _bind_server():
    """Bind the HTTP server on (HOST, PORT), arbitrating single-ownership so a manual run and the
    systemd service never crash-loop on EADDRINUSE (the orphan-port bug).

    Default = YIELD: if another lucid already serves :PORT, print and exit 0 — systemd treats it as
    success, so no restart storm; if a NON-lucid process holds the port, exit 1 (a real conflict).
    Opt-in `--takeover` / LUCID_TAKEOVER=1 (interactive dev only — the service never sets it, so there
    is no service↔dev ping-pong): SIGTERM the incumbent lucid, then bind once the port frees."""
    takeover = "--takeover" in sys.argv or os.environ.get("LUCID_TAKEOVER") == "1"
    terminated = False
    for _ in range(40):                       # after a takeover, retry the bind for up to ~8s
        server = ThreadingHTTPServer((HOST, PORT), Handler, bind_and_activate=False)
        try:
            server.server_bind()              # allow_reuse_address (SO_REUSEADDR) is set first
            server.server_activate()
            return server
        except OSError as e:
            server.server_close()
            if e.errno != errno.EADDRINUSE:
                raise
        if terminated:                        # already evicted the incumbent — just wait for the port
            time.sleep(0.2)
            continue
        pid = _incumbent_lucid_pid()
        if pid == 0:
            print(f"[lucid] {HOST}:{PORT} is held by a non-lucid process — refusing to start. Free it "
                  f"(`fuser -k {PORT}/tcp`) or set LUCID_WEB_PORT.", flush=True)
            sys.exit(1)
        if not takeover:
            print(f"[lucid] already serving at http://{HOST}:{PORT} (pid {pid}) — not starting a second "
                  f"instance. Use the `agentos-lucid` service; pass --takeover to replace it.", flush=True)
            sys.exit(0)
        print(f"[lucid] --takeover: replacing incumbent lucid (pid {pid}) on {HOST}:{PORT}", flush=True)
        _terminate_incumbent(pid)
        terminated = True
    print(f"[lucid] could not bind {HOST}:{PORT} after takeover — giving up.", flush=True)
    sys.exit(1)


def main():
    if "--burn-private" in sys.argv:   # systemd ExecStop hook — see _burn_private_on_stop
        _burn_private_on_stop()
        return
    server = _bind_server()            # single-owner arbitration of :PORT — yields/exits cleanly on a collision
    try:  # sweep private clip/frame orphans whose tmpfs session is gone (crash/logout) — ADR-0016
        reaped = L.ST.reap_orphans()
        if reaped:
            print(f"reaped {len(reaped)} orphaned private session(s): {reaped}", flush=True)
        # a download-stitch scratch dir (possibly a private MP4) left by a crash mid-download has no
        # session index, so reap_orphans can't see it — clear the whole transient root outright.
        if L.ST.clear_download_scratch():
            print("cleared orphaned download-stitch scratch", flush=True)
    except Exception as e:
        print(f"orphan reap skipped: {e}", flush=True)
    # Reopen where the user left off (ADR-0028): the most-recently-edited saved dream, else a fresh
    # empty slate. LUCID_WEB_SESSION still pins a specific session (tests / explicit). Honors the
    # library instead of the old single hardcoded "web".
    if not os.environ.get("LUCID_WEB_SESSION"):
        try:
            lib = L.ST.list_persistent()
            set_session(lib[0]["session"] if lib else L.ST.new_session_id(None))
        except Exception:
            set_session(L.ST.new_session_id(None))
    # Warm-keep lease must be released when the server stops: the coordinator owns ComfyUI
    # INDEPENDENTLY of this process, so a held lease would leak ~17 GB if we just exit. Release on
    # SIGTERM/SIGINT (systemd stop / Ctrl-C); the idle reaper covers the walked-away case. A hard
    # SIGKILL of this server still leaks until the daemon restarts — a documented edge (ADR-0015).
    # ADR-0028: before releasing, re-seal any OPEN stash working copy back to ciphertext and burn its
    # tmpfs (the key is still in memory here; the ExecStop --burn-private hook runs in a separate
    # process that has no key, so reseal must happen in THIS process on stop).
    # ADR-0019 §5 / ADR-0036 D9: the EPHEMERAL private-request drainer runs IN-PROCESS as a daemon
    # thread so it lives and dies with the session (no after-logout window). It re-runs held PRIVATE
    # create-from-image requests on a best-effort lease and ages out idle ones — every terminal is a
    # SILENT burn (never a review row). daemon=True tears it down on a hard logout kill; the stop event
    # is the clean path. Defined before _shutdown so the closure can set it.
    _priv_drain_stop = threading.Event()
    def _shutdown(_signum, _frame):
        _priv_drain_stop.set()                 # ask the private drainer to stop (daemon=True covers a hard kill)
        try:
            SH.reseal_opened(burn=True)
        except Exception:
            pass
        _release_lease()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    threading.Thread(target=_lease_reaper, daemon=True).start()
    threading.Thread(target=PD.run_in_session, args=(_priv_drain_stop,),
                     daemon=True, name="lucid-priv-drain").start()
    print(f"Lucid web → http://{HOST}:{PORT}  (session '{cur_session()}')", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

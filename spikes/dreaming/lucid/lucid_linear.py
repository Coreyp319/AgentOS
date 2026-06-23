#!/usr/bin/env python3
"""Lucid MVP — a linear chain through the coordinator lease (ADR-0015).

The smallest lucid that proves the bet AND sits on the right side of the safety line. Unlike the
original spike (`lucid_engine.py`, which runs the ~21.8 GB i2v step with no lease and trusts
`keep_alive:0`), every video beat here:

  1. proposes beats via Ollama, then SCHEMA-VALIDATES + RED-LINE-FILTERS them (lucid_safety, B3);
  2. EVICTS the beat model from VRAM (`ollama stop`, the ADR-0018 lever), then CONFIRMS it gone,
     before touching video VRAM (lucid_safety.force_evict, B1 — confirm-only had no teeth and skipped
     every turn while the page kept the model warm);
  3. acquires the video lease by asking agentosd to Spawn+own ComfyUI under the BATCH tier
     (predict-before-load admission; reuses the dream.sh client) — and FAILS OPEN (ADR-0003) if the
     coordinator is unreachable or refuses;
  4. generates ONE clip, appends a node to an ATOMIC, append-only chain.json (B4);
  5. Releases the lease in a finally -> agentosd SIGKILLs the owned ComfyUI -> VRAM reclaimed.

On preemption (live inference arrives) agentosd SIGKILLs the owned ComfyUI under us; the in-flight
clip is lost (a cache artifact only) and the step reports it, per ADR-0009 §3 (live inference
outranks the dream).

Test seams (env) so the dance is provable WITHOUT a real GPU run (mirrors dream.sh):
  LUCID_PROFILE         launch profile agentosd Spawns/owns (default: comfyui)
  LUCID_GEN_CMD         override the generation step (stub it; the lease orchestration still runs)
  LUCID_READY_CMD       readiness probe override (default: curl ComfyUI /system_stats)
  LUCID_EST_MIB         admission estimate (default 17000 ≈ Wan 14B i2v; OWED: measure real peak)
The B2 seed-likeness guard (ADR-0017) is NOT bypassable — start() is its single chokepoint; every
user-supplied seed passes B2 (a server-generated abstract opening is the only trusted seed).
"""
import argparse
import hashlib
import json
import os
import random
import re
import socket
import subprocess
import sys
import time
import urllib.error
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_engine as E   # noqa: E402  (generation backend + workflow parameterization)
import lucid_safety as S   # noqa: E402  (the deterministic gates)
import lucid_store as ST   # noqa: E402  (persistent vs private/ephemeral storage — ADR-0016)
import lucid_b2 as B2      # noqa: E402  (seed-image likeness guard — ADR-0017)
import lucid_ground as G   # noqa: E402  (ADR-0037 grounding gates — L0 canon ledger + L2 palette flag)

COORD_NAME = "org.agentos.Coordinator1"
COORD_PATH = "/org/agentos/Coordinator1"
# ADR-0041 cross-workflow VRAM-demand arbiter (a SEPARATE process from the coordinator). The drainer
# parks here on a busy lease so a deferred dream drains the instant the GPU frees, instead of
# failing-open-and-requeuing. Optional: absent → wait_turn() fails open and the dream proceeds as before.
QUEUE_NAME = "org.agentos.Queue1"
QUEUE_PATH = "/org/agentos/Queue1"
# busctl timeout for a WaitTurn park — must EXCEED the arbiter's server-side wait window (default 30s)
# so we don't cut a legitimate wait short; it's only a backstop against a wedged arbiter (→ fail-open).
QUEUE_WAIT_TIMEOUT = int(os.environ.get("LUCID_QUEUE_WAIT_SECS", "40"))
PROFILE = os.environ.get("LUCID_PROFILE", "comfyui")
# Params appended to the daemon-owned profile argv (dream.sh DREAM_PARAMS parity). The real
# `comfyui` profile takes none; the `sleep` stand-in profile takes a duration (smoke-testing).
PARAMS = [p for p in os.environ.get("LUCID_PARAMS", "").split() if p]
EST_MIB = int(os.environ.get("LUCID_EST_MIB", "17000"))   # back-compat default (Wan)


def _est_mib():
    """Admission estimate (MiB): explicit LUCID_EST_MIB wins; else the *active* engine's measured
    peak (E.est_mib() — Q4/Wan ~17 GB, 10Eros Q6 ~19 GB) so the LTX lane doesn't under-admit and OOM.
    Resolved at spawn time so a live engine toggle is reflected on the next lease.

    Calibration (80k telemetry.jsonl samples, 2026-06-21 — resolves the "measure real peak" OWED): the
    ComfyUI proc footprint clusters at 16-17 GB (the Q4 default lane, matching est 17000), tails to
    18-19 GB (Q6, est 19000), and the card has hit free<1 GB during real renders. est is therefore
    correctly calibrated — do NOT shave it to widen the admission margin. The asymmetry is the point: a
    false refusal is a now-retried annoyance (lucid_web ADMIT_RETRIES), an under-estimate is an OOM
    crash. The knife-edge that refuses a Q4 job by a few MB is the live UE wallpaper's ~6 GB baseline
    eating the last of free VRAM, not a bad est — the structural fix is freeing UE's VRAM (ADR-0029
    throttle-to-admit), not a smaller number here."""
    env = os.environ.get("LUCID_EST_MIB")
    return int(env) if env else E.est_mib()


COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1:8188")
READY_TIMEOUT = int(os.environ.get("LUCID_READY_TIMEOUT", "180"))


def log(*a):
    print("[lucid]", *a, file=sys.stderr)


# ---------------- coordinator lease client (reuses the dream.sh contract) ----------------
def _coord(*args):
    return subprocess.run(["busctl", "--user", "call", COORD_NAME, COORD_PATH, COORD_NAME, *args],
                          capture_output=True, text=True, timeout=30)


# Last lease_spawn outcome, so the SURFACE can tell the user WHY a turn failed open instead of a
# silent/misleading "skipped" (audit finding 2.1). None once a spawn is granted; otherwise a dict:
#   {"kind": "unreachable"|"refused", "reason": <raw>, "short_mib", "free_mib", "est_mib"}.
# Read by lucid_web right after _ensure_lease returns None. Single-exclusive lease + LEASE_LOCK make
# the write→read window effectively serial for the interactive loop; best-effort otherwise.
LAST_REFUSAL = None

_REFUSAL_RE = re.compile(r"short\s+(\d+)M.*free\s+(\d+)M.*est\s+(\d+)M", re.IGNORECASE)


def _parse_refusal(reason):
    m = _REFUSAL_RE.search(reason or "")
    out = {"kind": "refused", "reason": reason or "GPU busy"}
    if m:
        out.update(short_mib=int(m.group(1)), free_mib=int(m.group(2)), est_mib=int(m.group(3)))
    return out


def lease_spawn(tier="batch"):
    """Ask agentosd to Spawn+own ComfyUI under a lease. Returns a token, or None to fail open
    (coordinator down OR admission refused -> the dream yields, never forces VRAM). On a None return,
    LAST_REFUSAL carries the reason so the surface can be honest about it.

    `tier` defaults to "batch" so the interactive Lucid loop (create_from_image.py, lucid_web.py)
    is unchanged. The ADR-0019 drainer passes tier="best-effort" so arbitrate() (coord.rs:129-135)
    structurally Queues this run behind ANY holder and lets Tier::Interactive preempt it
    (lease.rs:583-592) — fail-open BY CONSTRUCTION, never by measurement (design doc G3)."""
    global LAST_REFUSAL
    r = _coord("Spawn", "susas", tier, str(_est_mib()), PROFILE, str(len(PARAMS)), *PARAMS)
    if r.returncode != 0:
        LAST_REFUSAL = {"kind": "unreachable", "reason": r.stderr.strip() or r.stdout.strip()}
        log(f"coordinator unreachable ({r.stderr.strip() or r.stdout.strip()}) — fail open (ADR-0003)")
        return None
    parts = r.stdout.split()           # "bts true <token> <msg...>"
    granted = len(parts) >= 2 and parts[1] == "true"
    if not granted:
        reason = ' '.join(parts[2:]).strip('"') or "GPU busy"
        LAST_REFUSAL = _parse_refusal(reason)
        log(f"admission refused — {reason} — fail open")
        return None
    LAST_REFUSAL = None
    token = parts[2] if len(parts) >= 3 else "0"
    log(f"lease GRANTED (token {token}); agentosd owns ComfyUI")
    return token


def _coord_holder_tier():
    """The coordinator's current holder tier ('interactive'|'batch'|'best-effort'|'yielding'|None),
    best-effort. Used to tell a genuine PREEMPT (a higher tier took the GPU and SIGKILLed our owned
    ComfyUI -> the dream yields calmly) apart from a ComfyUI crash / backend-down (same connection
    error, but no higher-priority holder)."""
    try:
        r = _coord("Status")           # "bstu <held> <tier> <token> <free>"
        parts = r.stdout.split()
        if len(parts) >= 3 and parts[1] == "true":
            return parts[2].strip('"').lower() or None
    except Exception:                  # noqa: BLE001 — best-effort classifier, never raises
        pass
    return None


def lease_release(token):
    if token and token != "0":
        _coord("Release", "t", token)
        log(f"released lease {token} — agentosd SIGKILLs ComfyUI, VRAM reclaimed")


def wait_turn(tier="best-effort", est=None):
    """ADR-0041: park behind the cross-workflow VRAM-demand queue (org.agentos.Queue1) until it's our
    turn — so a deferred dream drains the INSTANT the lease frees, instead of failing-open-and-requeuing
    on a busy GPU. Returns True if the arbiter granted a turn (the lease is free; go acquire), False
    otherwise. FAIL-OPEN by construction: an absent/down arbiter, a timeout, a non-zero return, or any
    error all return False and the caller proceeds EXACTLY as if there were no queue — its own lease
    fail-open + the drainer's requeue still apply, so the dream is never blocked or dropped by the queue.
    Uses the AGENT verb (clamps tier to {best-effort, batch}); only a BACKGROUND drainer should call it —
    the interactive loop must never block on a turn (the user is waiting)."""
    est = _est_mib() if est is None else int(est)
    try:
        r = subprocess.run(
            ["busctl", "--user", "call", QUEUE_NAME, QUEUE_PATH, QUEUE_NAME,
             "WaitTurnAgent", "su", tier, str(est)],
            capture_output=True, text=True, timeout=QUEUE_WAIT_TIMEOUT)
    except Exception as e:                  # noqa: BLE001 — the arbiter is OPTIONAL; never raise into a drain
        log(f"queue WaitTurn unreachable ({e}) — proceeding without a turn (fail-open, ADR-0041)")
        return False
    if r.returncode != 0:
        return False                       # no arbiter / bus error → fail open (no queue, proceed)
    parts = r.stdout.split()               # "bs true your_turn" | "bs false timeout|queue_full|…"
    granted = len(parts) >= 2 and parts[1] == "true"
    if granted:
        log("queue: our turn — the lease should be free; proceeding to acquire")
    return granted


def wait_ready():
    cmd = os.environ.get("LUCID_READY_CMD")
    import time
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if cmd:
            if subprocess.run(cmd, shell=True).returncode == 0:
                return True
        else:
            try:
                import urllib.request
                urllib.request.urlopen(f"http://{COMFY_HOST}/system_stats", timeout=2).read()
                return True
            except Exception:
                pass
        time.sleep(1)
    return False


# ---------------- B2 seed-image likeness guard (ADR-0017 — start() is the SINGLE chokepoint) --------
class SeedBlocked(Exception):
    """B2 refused a user seed. `.verdict` carries the dispositive flags + reason so the surface can
    show the block (and, for a real-person verdict, offer consent)."""
    def __init__(self, verdict):
        self.verdict = verdict
        super().__init__(verdict.reason)


# ---------------- linear chain state (append-only, atomic; private-aware via lucid_store) -------
def load_chain(session):
    return ST.load_chain(session, ST.is_private(session))


def save_chain(session, chain):
    ST.save_chain(session, ST.is_private(session), chain)


def start(session, opening_image, private=False, consent=False, _trusted_seed=False, premise=None,
          name=None, seed=None, rating_floor=None):
    # start() is the SINGLE B2 chokepoint (ADR-0017). _trusted_seed=True is reachable ONLY for a
    # server-generated abstract opening (no real person) — never for a user-supplied image. Every
    # user seed passes B2 here, so no surface can route around the guard.
    if not _trusted_seed:
        v = B2.check_seed(opening_image)
        if not v.ok and not (v.requires_consent and consent):
            raise SeedBlocked(v)
    ST.ensure_session(session, private)   # establishes privacy-ness; is_private(session) now true
    os.makedirs(E.INPUT_DIR, exist_ok=True)
    ref_name, abs_path = ST.frame_ref(session, private, f"{session}_n0.png")
    import shutil
    shutil.copy(opening_image, abs_path)
    # premise: the session's initial prompt — "what this dream is about". Persisted with the chain so
    # context_for() can bias EVERY beat suggestion toward it, not just the opening frame (the Start
    # "initial prompt"). Optional + gated by the caller; stored trimmed, never required.
    # name + created: the LIBRARY metadata (ADR-0028). `name` is the human label shown in the saved-
    # dreams list (defaults to the session id downstream if empty); `created` lets the library sort by
    # age. Both are persisted with the chain so the listing needs no sidecar.
    # seed: the dream's BASE seed (ADR-0033). Every beat derives a deterministic per-node seed from it
    # (_beat_seed = base + node id), so (a) a clip is reproducible — the hero re-render of a draft beat
    # reuses the SAME seed to refine the SAME shot, not roll a new one — and (b) the whole dream shares one
    # noise family instead of the old per-beat random() lottery (a small steadiness win on identity drift).
    # ADR-0036 D5: an explicit `seed` (frozen at enqueue by `freeze_intent`) is used when given, so a
    # DEFERRED create re-runs the SAME base seed instead of rolling a fresh one at drain — a deferred
    # retry reproduces the same noise family. None (the default) preserves today's mint-on-start.
    chain = {"session": session, "private": private,
             "name": (name or "").strip()[:80] or None,
             "created": time.time(),
             "seed": seed if seed is not None else random.randint(1, 2**31 - 1),
             "premise": (premise or "").strip()[:300] or None,
             # rating_floor: the USER-DECLARED content floor (the "Mature dream" toggle). The per-frame VLM
             # rating (ground_frame) is conservative — it only flips to 'mature' when the seed image/premise
             # already reads explicit, so a suggestive-but-clothed opening renders a whole dream SFW and the
             # viewer never sees mature choices. This floors EVERY beat menu + refine to the declared tier from
             # frame 0; the VLM can still ratchet a SFW dream UP (monotone), never the floor DOWN. Validated to
             # the one tier we support ('mature'); anything else -> None = today's pure-VLM behaviour. The red
             # line (minors / real people / non-consent) is INDEPENDENT and code-enforced — a floor can't widen it.
             "rating_floor": "mature" if rating_floor == "mature" else None,
             "nodes": [
                 {"id": 0, "parent": None, "label": "opening", "prompt": None,
                  "seed": None, "clip": None, "out_frame": ref_name}]}
    save_chain(session, chain)
    return chain


def freeze_intent(prompt, *, quality="draft", seed=None):
    """ADR-0036 D5: capture the generation intent as VALUES at enqueue time, so a deferred drain
    reproduces what was asked instead of re-deriving it from mutable globals. The durable drainer
    ENFORCES three of these: the frozen `prompt` (vs the old triplicated module constant), the frozen
    `seed` (vs a fresh mint at drain — so a deferred RETRY reproduces the same noise family), and the
    frozen `engine` family (it pins `set_engine` so a wan↔10eros registry flip can't run a different
    pipeline than the one admitted).

    `workflow`, `quality`, and `est_mib` are RECORDED for audit/forward-compat but live-resolved at
    drain — honestly: `workflow` (the intra-family graph) has no runtime setter, so it can't drift
    within a process; only an operator `LUCID_WORKFLOW` change between oneshot fires could, which the
    always-draft create-from-image lane never triggers. `est_mib` needs no enforcement because the
    engine pin runs BEFORE the lease, so the live `est_mib()` already matches the pinned engine.

    Returns a plain JSON-able dict stored verbatim in the queue record under `frozen`; the drainers
    fall back to their module constants ONLY for a legacy record that predates this freeze."""
    return {
        "v": 1,
        "prompt": prompt,
        "seed": seed if seed is not None else random.randint(1, 2**31 - 1),
        "engine": E.current_engine(),
        "workflow": os.path.basename(E.WORKFLOW),
        "quality": quality,
        "est_mib": E.est_mib(),
    }


def burn(session):
    """Wipe a private session's every sink (ADR-0016). No-op set for a persistent session."""
    return ST.burn(session)


# ---------------- beat-gen (grounded + validated) ----------------
def propose(context, n=4, rating="sfw", frame_b64=None):
    """Ollama beat-gen with the rating-selected steering (sfw|mature) and the current frame attached for
    grounding, then schema-validate + red-line-filter (lucid_safety). [] -> type-your-own. The red-line
    is rating-INDEPENDENT and always fail-closed; `rating` only swaps the creative clause.

    The narrator is a small (3B) model: it occasionally returns fewer than n valid beats (an intermittent
    collapse, or a beat red-line-dropped), which would leave the menu thin. So we roll ONCE MORE if short
    and merge the unique beats (model proposes, code disposes) — the repair roll costs one extra eviction
    only when needed, and the held-menu contract means it happens at most once per frame. Beat-gen runs at
    BEAT_TEMP (above the shared 0.6 fidelity lane) for a little more narrative surprise. Still fail-open:
    any exception / a persistently-empty roll returns what we have (possibly []) -> type-your-own."""
    sys_p = E.build_sys(rating, n)
    # A swapped-in beat narrator (E.NARRATOR_MODEL != the vision MODEL) may be text-only — drop the image
    # and let it work from the caption ground_frame already put in `context`. Default narrator == MODEL, so
    # the frame is still attached and behaviour is unchanged.
    images = [frame_b64] if (frame_b64 and E.NARRATOR_MODEL == E.MODEL) else None
    out, seen = [], set()
    for _attempt in range(2):                              # initial roll + one repair roll if short
        try:
            raw = E._ollama_json(sys_p, context, model=E.NARRATOR_MODEL, images=images, temperature=E.BEAT_TEMP)
        except Exception as e:
            log(f"beat-gen failed ({e}) — type your own")
            break
        for b in S.validate_beats(raw, n):
            key = (b["label"].lower(), b["prompt"].lower())   # the repair roll must not duplicate the first
            if key not in seen:
                seen.add(key)
                out.append(b)
        if len(out) >= n:
            break
    return out[:n]


# ---------------- juncture prompt refine (model proposes, code disposes) ----------------
REFINE_MAX_IN = 600     # a rough next-beat idea, not an essay


def _extract_prompt(raw):
    """Pull the single refined beat out of the narrator's JSON ({"prompt":"..."}). A small model sometimes
    skips the wrapper and just returns the line — accept that too (treat the whole reply as the prompt).
    Strips wrapping quotes and caps to the workflow length. '' on nothing usable -> caller fails honest."""
    s = (raw or "").strip()
    if not s:
        return ""
    try:
        data = json.loads(s)
        if isinstance(data, dict) and isinstance(data.get("prompt"), str):
            s = data["prompt"]
    except (json.JSONDecodeError, TypeError):
        pass        # not JSON — the reply IS the prompt (a smaller model may skip the wrapper)
    return s.strip().strip('"').strip()[:S.PROMPT_MAX]


def _refine_context(session, node_id):
    """Best-effort grounding for a refine: (context, rating, frame_b64). Prefers the node's SEALED caption +
    rating (the menu roll already grounded this frame — no second VLM pass), grounding fresh ONLY when a
    caption is missing AND a frame is available. NEVER fails the refine on a grounding miss: any error (no
    chain yet, unreachable model, unresolvable frame) degrades to the text-only, SFW-default path — exactly
    like beat-gen's fail-open grounding. The caller still red-line-gates the input and output regardless."""
    try:
        chain = load_chain(session)
        node = _node_or_tip(chain, node_id)
        caption = node.get("caption")
        rating = _max_rating(node.get("rating"), chain.get("rating_floor"))   # sealed floor + the declared floor
        frame_b64 = E.frame_to_b64(_frame_abs(session, node))
        if not caption and frame_b64:                       # frame never grounded yet — one cheap pass
            cap, rt = E.ground_frame(frame_b64, chain.get("premise"))
            if cap and S.red_line_ok(cap):                  # a model-written caption is untrusted text too
                caption = cap
            rating = _max_rating(rating, rt)
        return context_for(session, caption=caption, node=node), rating, frame_b64
    except Exception as e:
        log(f"refine: grounding unavailable ({e}) — refining text-only")
        return "", "sfw", None


def refine_beat(session, text, node_id=None, _call=None):
    """Sharpen the viewer's OWN rough next-beat idea into one vivid, frame-grounded, two-dial beat — the
    juncture twin of the Start surface's lucid_refine, but mid-dream and grounded on the current frame +
    premise + story-so-far (ADR-0023). The model PROPOSES the rewrite; the deterministic red-line gate
    DISPOSES on BOTH the rough input and the refined output, so refine can never hand back a prompt Lucid
    would block. Fails honestly: a blank/red-lined idea, an unreachable narrator, or an unsafe result
    returns a calm {"ok": False, "reason": ...}, never a silent or unsafe string. No lease, no turn, no
    chain write — safe to call during the dwell (same VRAM profile as a menu roll). `_call(system, user)
    -> str` is injectable so the contract is testable without a model (grounding is then skipped — no
    chain/GPU needed).

    Returns {"ok": True, "refined": "<editable prompt>"} or {"ok": False, "reason": "<honest message>"}."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "Type an idea for the next moment first."}
    text = text[:REFINE_MAX_IN]
    # Gate the INPUT too — a red-lined idea is refused before we spend a model call on it (mirrors lucid_refine).
    if S.gate_prompt(text) is None:
        return {"ok": False, "reason": "That idea isn't something Lucid can dream. Try another."}
    context, rating, frame_b64 = _refine_context(session, node_id) if _call is None else ("", "sfw", None)
    sys_p = E.build_refine_sys(rating)
    parts = [p for p in (context,) if p]
    parts.append('The viewer\'s rough idea for what happens next: "' + text + '"')
    parts.append("Rewrite THAT idea into one beat as instructed — keep their intent, sharpen and ground it.")
    user = "\n".join(parts)
    # A swapped-in text-only beat narrator can't see the frame — it works from the caption already in `context`
    # (same rule as propose()). Default narrator == the vision MODEL -> attach the frame for tighter grounding.
    images = [frame_b64] if (frame_b64 and E.NARRATOR_MODEL == E.MODEL) else None
    try:
        raw = _call(sys_p, user) if _call else E._ollama_json(
            sys_p, user, model=E.NARRATOR_MODEL, images=images, temperature=E.BEAT_TEMP)
    except Exception as e:
        log(f"refine failed ({e}) — send your own")
        return {"ok": False, "reason": "Couldn't reach the writer just now — send your own, or try again."}
    out = _extract_prompt(raw)
    if not out:
        return {"ok": False, "reason": "No refinement came back — send your own."}
    # Gate the OUTPUT — a refine can NEVER hand back a prompt the red-line would block (fail-closed).
    if S.gate_prompt(out) is None:
        return {"ok": False, "reason": "Couldn't refine that safely — try rephrasing."}
    return {"ok": True, "refined": out}


def _max_rating(*ratings):
    """The dream's content rating is a MONOTONE floor (sfw < mature): once any frame grounds mature it
    never silently drops back on a later frame. That keeps the render LoRA stable across a dream (no
    per-frame flicker) AND means a typed-own beat fired before a frame's menu has rolled inherits the
    dream's established rating instead of a blind sfw. Unknown/None -> sfw (safe default)."""
    return "mature" if any(r == "mature" for r in ratings) else "sfw"


# ---------------- tree helpers (the chain is a tree via `parent`; linear is the 1-lane case) ----------
def _by_id(chain):
    return {n["id"]: n for n in chain["nodes"]}


def _node_or_tip(chain, node_id):
    """The node to act on: an explicit id (branch from there), else the latest append (the linear tip).
    A bad/unknown id degrades to the tip rather than erroring — model proposes, code disposes."""
    if node_id is None:
        return chain["nodes"][-1]
    return _by_id(chain).get(node_id, chain["nodes"][-1])


def _ancestry(chain, node):
    """root -> node (inclusive) following parent pointers — the branch's own spine. For a linear chain
    the tip's ancestry is every node, so callers that pass the tip get the legacy whole-chain behaviour."""
    by = _by_id(chain)
    line, cur, seen = [], node, set()
    while cur is not None and cur["id"] not in seen:
        line.append(cur); seen.add(cur["id"]); cur = by.get(cur.get("parent"))
    return list(reversed(line))


def _next_id(chain):
    """A fresh node id that can't collide with an existing sibling (parent+1 collides when a node already
    has a child — i.e. the moment a take branches). Monotonic over the whole tree."""
    return max(n["id"] for n in chain["nodes"]) + 1


# ---------------- moment annotations (notes) — spatial + semantic feed-forward ----------------
NOTE_TAGS = ("more", "less", "hold", "change")
# ADR-0025 amendment: a note may carry a normalized region (x,y,r) — WHERE on the frame the viewer tapped.
# r defaults when a point is given without one; the LTX engine turns it into a soft-disc attention mask.
DEFAULT_NOTE_RADIUS = 0.18


def _note_seq(chain):
    """A monotonic per-chain note ordinal so a removed note's id is never re-minted (stable refs for
    the frontend). Derived from the max existing "nt<seq>" ordinal across EVERY node + 1, so it keeps
    climbing even after a delete — code disposes, deterministically."""
    hi = -1
    for n in chain["nodes"]:
        for note in (n.get("notes") or []):
            nid = str(note.get("id", ""))
            if nid.startswith("nt") and nid[2:].isdigit():
                hi = max(hi, int(nid[2:]))
    return hi + 1


def _valid_mask_ref(session, mask):
    """ADR-0032: validate an UNTRUSTED mask ref from the client (code disposes — never trust a path). It must
    be EXACTLY a name /api/segment mints for THIS session — basename `<session>_segmask_<hex>.png`, optionally
    under the single sealed subdir `.lucid-priv-<session>/` and nothing else — AND the file must exist. No
    traversal, no abs path, no foreign subdir, no substring-collision on the session id (we anchor on the
    `<session>_segmask_` prefix, not `in`). Returns the ref or None (None -> save as a plain point, fail-open)."""
    if not mask:
        return None
    m = str(mask)
    if ".." in m or m.startswith("/"):
        return None
    d, b = os.path.split(m)
    if d not in ("", f".lucid-priv-{session}"):                 # only the session's own sealed subdir
        return None
    if not re.fullmatch(rf"{re.escape(session)}_segmask_[0-9a-f]+\.png", b):
        return None
    return m if os.path.exists(os.path.join(E.INPUT_DIR, m)) else None


# ---------------- ADR-0023: per-choice "potential path" still previews ----------------
def _beat_key(beat):
    """A stable content-address for a beat (label+prompt) — the preview PNG's id, since a beat carries no
    node id of its own. blake2b over `label\\x00prompt` so distinct siblings never collide and a re-served
    identical beat resolves to the SAME preview (idempotent, held like the menu)."""
    s = ((beat.get("label") or "") + "\x00" + (beat.get("prompt") or "")).encode("utf-8", "replace")
    return hashlib.blake2b(s, digest_size=6).hexdigest()


def _valid_preview_ref(session, ref):
    """Validate an UNTRUSTED preview ref from the client (code disposes — never trust a path), the exact twin
    of _valid_mask_ref. Must be EXACTLY a name generate_beat_preview mints for THIS session — basename
    `<session>_bp_<nodeId>_<hex>.png`, optionally under the single sealed subdir `.lucid-priv-<session>/` and
    nothing else — AND the file must exist. No traversal, no abs path, no foreign subdir, no session
    substring-collision (anchored on the `<session>_bp_` prefix). Returns the ref or None (None -> 404)."""
    if not ref:
        return None
    m = str(ref)
    if ".." in m or m.startswith("/"):
        return None
    d, b = os.path.split(m)
    if d not in ("", f".lucid-priv-{session}"):                 # only the session's own sealed subdir
        return None
    if not re.fullmatch(rf"{re.escape(session)}_bp_\d+_[0-9a-f]+\.png", b):
        return None
    return m if os.path.exists(os.path.join(E.INPUT_DIR, m)) else None


def add_note(session, node_id, t, tag, text="", x=None, y=None, r=None, mask=None):
    """Attach a moment annotation to a node (ADR-0023/0025/0032 spatial + semantic feed-forward). `tag` is
    one of NOTE_TAGS (else ValueError); `t` is clamped to >= 0; `text` is UNTRUSTED — if present it must pass
    the red-line gate (else ValueError) so a steered prompt can never carry it past the gate later. An
    OPTIONAL spatial point (`x`,`y` normalized 0..1, origin top-left) with radius `r` records WHERE on the
    frame the viewer tapped; persisted only when both x and y are given (legacy time-only notes stay clean).
    An OPTIONAL `mask` (ADR-0032) is the INPUT_DIR-relative ref of a stored segmentation mask produced by
    /api/segment; it is validated (code disposes — session-scoped, no traversal, must exist) and persisted
    so the engine uses the precise object silhouette, with (x,y,r) kept as the soft-disc fallback. The note
    is appended to the node's `notes` list with a monotonic per-chain id and persisted. Returns the note
    dict. Raises ValueError on a bad tag, red-line-failing text, or an unknown node id."""
    if tag not in NOTE_TAGS:
        raise ValueError(f"bad note tag {tag!r} (expected one of {NOTE_TAGS})")
    t = max(0.0, float(t))
    text = text or ""
    if text and not S.red_line_ok(text):   # untrusted free-text — fail-closed, exactly like a prompt
        raise ValueError("note text refused by red-line gate")
    # Optional spatial region — clamp into the frame; default + bound the radius. Code disposes (the model
    # only proposes a tap location); an out-of-range coord is clamped, never trusted verbatim.
    region = None
    if x is not None and y is not None:
        cx = min(1.0, max(0.0, float(x)))
        cy = min(1.0, max(0.0, float(y)))
        rr = min(0.9, max(0.02, float(r) if r is not None else DEFAULT_NOTE_RADIUS))
        region = (cx, cy, rr)
    mask_ref = _valid_mask_ref(session, mask)
    chain = load_chain(session)
    node = _by_id(chain).get(node_id)
    if node is None:
        raise ValueError(f"no such node {node_id!r}")
    note = {"id": "nt" + str(_note_seq(chain)), "t": t, "tag": tag, "text": text}
    if region:
        note["x"], note["y"], note["r"] = region
    if mask_ref:
        note["mask"] = mask_ref
    node.setdefault("notes", []).append(note)
    save_chain(session, chain)
    return note


def remove_note(session, node_id, note_id):
    """Drop a note by id from a node. Returns True iff a note was removed (False if the node or note is
    unknown — idempotent delete). Persists only when something actually changed."""
    chain = load_chain(session)
    node = _by_id(chain).get(node_id)
    if node is None:
        return False
    notes = node.get("notes") or []
    kept = [n for n in notes if n.get("id") != note_id]
    if len(kept) == len(notes):
        return False
    # ADR-0032: unlink the removed note's stored segmentation mask (append-only artifact cleanup; the
    # private burn also wipes the sealed dir). Best-effort — a missing file is fine (idempotent). Masks are
    # CONTENT-ADDRESSED, so an identical mask may be shared by another note — only unlink one no surviving
    # note still references (else removing one note would blank another's silhouette).
    gone = next((n for n in notes if n.get("id") == note_id), None)
    if gone and gone.get("mask") and not any(k.get("mask") == gone["mask"] for k in kept):
        try:
            os.remove(os.path.join(E.INPUT_DIR, gone["mask"]))
        except OSError:
            pass
    node["notes"] = kept
    save_chain(session, chain)
    return True


def _region_phrase(x, y):
    """A coarse, deterministic location phrase for a normalized tap (origin top-left) — so a note's text is
    understood as referring to THAT part of the frame (per-region steering, ADR-0025/0032). Code disposes: a
    fixed 3x3 grid over the frame, never a model's guess at the object's name (which would need a caption pass;
    the on-frame numbered pin is the precise spatial referent). Returns a phrase like 'the upper-left'."""
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError):
        return None
    col = "left" if x < 0.34 else "right" if x > 0.66 else "center"
    row = "top" if y < 0.34 else "bottom" if y > 0.66 else "middle"
    if row == "middle" and col == "center":
        return "the center"
    if col == "center":
        return "the " + row
    if row == "middle":
        return "the " + col
    return "the %s-%s" % (row, col)


def _steering_suffix(notes):
    """A SHORT deterministic prompt suffix that feeds a parent's notes forward to the next beat (code
    disposes — sorted by `t`, fixed phrasing per tag). Empty string when there are no notes. The caller
    appends this to the base prompt and gates the COMBINED string, so steering text is red-line-gated too.
    When a note carries a region the deterministic phrasing names WHERE (the coarse grid phrase), so the
    text-only fallback path still localizes the steer the same way the VLM decomposition does (ADR-0032)."""
    parts = []
    for n in sorted(notes, key=lambda x: x.get("t", 0.0)):
        tag, text = n.get("tag"), (n.get("text") or "").strip()
        rloc = _region_phrase(n["x"], n["y"]) if ("x" in n and "y" in n) else None
        loc = (" in " + rloc) if rloc else ""
        if tag == "more":
            parts.append("; emphasize " + (text or "this") + loc)
        elif tag == "less":
            parts.append("; less " + (text or "of this") + loc)
        elif tag == "hold":
            parts.append("; hold the framing and composition")
        elif tag == "change":
            parts.append("; change " + (text or "this") + loc)
    return "".join(parts)


def _anchor_for(session, parent, notes, anchor_name):
    """Choose the spatial anchor frame for the next beat. If the parent has a `hold` note AND a clip,
    extract the frame at the LAST hold note's `t` (E.extract_frame_at, store-owned path); on success use
    it, else fall back to the parent's stored `out_frame`. No hold note / no clip / extraction failure
    all degrade to the last frame — fail-open, the dream still advances."""
    holds = [n for n in notes if n.get("tag") == "hold"]
    clip = parent.get("clip")
    if not holds or not clip:
        return parent["out_frame"]
    t = max(0.0, float(holds[-1].get("t", 0.0)))   # the LAST hold note in list order
    ref_name, abs_path = ST.frame_ref(session, ST.is_private(session), anchor_name)
    anchor = E.extract_frame_at(clip, t, ref_name, out_path=abs_path)
    return anchor if anchor else parent["out_frame"]


def _frame_abs(session, node):
    """Absolute path of a node's anchor frame (privacy-aware), or None if unresolvable — grounding then
    degrades to the text-only, SFW-default path rather than failing the menu."""
    try:
        return ST.frame_abs(session, ST.is_private(session), node.get("out_frame"))
    except Exception:
        return None


def _tip_frame_abs(session, chain):   # back-compat shim
    return _frame_abs(session, chain["nodes"][-1])


def roll_menu(session, chain, n=4, node=None):
    """Roll the held menu for a node (default the tip) GROUNDED on its actual frame (ADR-0014 §6).
    Returns (beats, caption, rating): one VLM pass captions + content-rates the frame (premise-aware,
    safe default 'sfw'), then beats are proposed with the rating-selected steering AND the frame image,
    with the story context taken along THAT node's branch. The caption is red-line-checked before it is
    ever fed back / persisted / shown. Pure orchestration over E + S; the seam beats_for_node rolls against."""
    node = node or chain["nodes"][-1]
    frame_b64 = E.frame_to_b64(_frame_abs(session, node))
    caption, rating = E.ground_frame(frame_b64, chain.get("premise"))
    rating = _max_rating(rating, chain.get("rating_floor"))   # user-declared floor wins (monotone, sticky-up)
    if caption and not S.red_line_ok(caption):     # a model-written caption is untrusted text too
        caption = None
    canon = _canon_for(chain, node, caption)       # ADR-0037 L0: fold this frame into the node's canon...
    if canon:
        node["canon"] = canon                      # ...persisted by beats_for_node's save_chain
    # ...and pass it in so THIS roll steers on the fresh fold (context_for re-reads from disk, where this
    # turn's canon isn't persisted yet — without this the canon would be one beat behind / inert on first roll).
    beats = propose(context_for(session, caption=caption, node=node, canon=canon),
                    n=n, rating=rating, frame_b64=frame_b64)
    return beats, caption, rating


def beats_for_node(session, node_id=None, n=4, roll=True):
    """The HELD "what happens next" menu for a node (default the tip; an explicit id branches from an
    earlier beat). The menu is a deterministic property of THAT frame: proposed once, persisted on the
    node, re-served verbatim — see beats_for_tip's contract below. Per-node so every beat in the tree
    (the lit tip OR an alternate take you've scrolled back to) carries its own held set of next moves."""
    chain = load_chain(session)
    node = _node_or_tip(chain, node_id)
    held = node.get("beats")
    if isinstance(held, list) and held:            # already rolled for this frame — hold it
        return held
    if not roll:                                   # in-flight: never roll, just serve what's held
        return held if isinstance(held, list) else []
    proposed, caption, rating = roll_menu(session, chain, n=n, node=node)
    if proposed:                                   # seal only a real menu (don't pin a transient [])
        node["beats"] = proposed
        node["rating"] = _max_rating(node.get("rating"), rating)   # monotone floor (sticky-up)
        if caption:
            node["caption"] = caption
        save_chain(session, chain)
    return proposed


def beats_for_tip(session, n=4, roll=True):
    """The HELD "what happens next" menu for the current chain tip (ADR-0015 §1: "no reroll").

    The model proposes the menu ONCE per frame; that proposal is persisted on the tip node and
    re-served verbatim forever after — so once the user is looking at a set of options it is held
    until the chain advances to a NEW tip (a clip they picked is generated and appended). The old
    spike re-rolled the non-deterministic LLM on every read, so a reload / second tab / cache
    eviction / a skipped fail-open turn all silently swapped the menu under the user. Now the menu
    is a deterministic property of the frame, not of a client cache.

    - `roll=False` (a beat is in flight) NEVER calls the model — it returns the already-held menu (or
      [] if none was rolled yet), so the tip can't sprout a fresh menu mid-turn.
    - A transient empty roll (Ollama down -> propose() == []) is NOT sealed: we return [] and let a
      later call retry, rather than pin "type your own" onto the frame permanently.

    Persisting onto the tip is the one late-bound write to an otherwise append-only node; it is sealed
    once a child node is appended (a new tip with no `beats` is what the next turn rolls against).
    """
    return beats_for_node(session, None, n=n, roll=roll)


# ---------------- ADR-0033: deterministic seed + persistent subject anchor ----------------
def _beat_seed(chain, node_id):
    """The deterministic noise seed for a beat node (ADR-0033). Derived from the dream's BASE seed + the
    node id, so a re-render (the hero pass) of a node reproduces its EXACT shot, while sibling takes
    (different ids) still differ. Legacy chains predating the base seed fall back to a STABLE per-session
    value (zlib.crc32 — process-independent, unlike hash()) so their re-renders reproduce too."""
    base = chain.get("seed")
    if not isinstance(base, int):
        base = zlib.crc32((chain.get("session") or "").encode()) & 0x7FFFFFFF
    return ((base + int(node_id)) % (2**31 - 1)) or 1


SUBJECT_ANCHOR_ENABLED = os.environ.get("LUCID_SUBJECT_ANCHOR", "1") != "0"


def _subject_for(session, chain):
    """The dream's persistent subject descriptor (ADR-0033), captured ONCE from the OPENING frame and
    cached on the chain. Returns the descriptor (str) or None (kill-switch off / no frame / model down /
    red-line refusal -> the render stays motion-only, exactly as before). Costs one extra VLM pass on the
    FIRST rendered beat only.

    Mutates `chain["subject"]` IN MEMORY (an "" sentinel records a null capture) but does NOT persist —
    the caller (step) owns the single guarded save, so a superseded/deleted turn never writes the capture
    back and can't resurrect a wiped session. Once a turn persists, the cache is on disk and a later beat
    short-circuits here; until then it re-grounds (idempotent, fail-open)."""
    if not SUBJECT_ANCHOR_ENABLED:
        return None
    if "subject" in chain:                  # captured before (possibly "" = grounded-but-empty)
        return (chain.get("subject") or None)
    opening = _by_id(chain).get(0) or chain["nodes"][0]
    subj = E.ground_subject(E.frame_to_b64(_frame_abs(session, opening)))
    if subj and not S.red_line_ok(subj):    # a model-written descriptor is untrusted text too
        subj = None
    chain["subject"] = subj or ""           # in-memory sentinel; step()'s persist carries it to disk
    return subj or None


def _with_subject(subject, render_prompt):
    """Quietly prefix the render prompt with the persistent subject so the identity carries across cuts
    (ADR-0033). No subject -> the prompt is returned unchanged (legacy behaviour). The COMBINED string is
    red-line-gated by the caller, so the descriptor passes the same gate as any prompt."""
    subject = (subject or "").strip()
    if not subject:
        return render_prompt
    body = (render_prompt or "").strip()
    prefix = subject if subject.endswith((".", "!", "?")) else subject + "."
    return (prefix + " " + body).strip() if body else prefix


# ---------------- ADR-0037: grounding gates (L0 canon ledger + L2 palette flag) ----------------
CANON_ENABLED = os.environ.get("LUCID_CANON", "1") != "0"      # L0 hybrid canon ledger (kill-switch)
# L2 defaults OFF: the palette gate is "flag-only until a fixture sanity pass calibrates it" (lucid_ground
# docstring) and its cv2 child runs inside the leased turn — opt in (LUCID_PALETTE=1) after calibration.
PALETTE_ENABLED = os.environ.get("LUCID_PALETTE", "0") != "0"
# The canon LLM fold is an EXTRA Ollama load that lands ADDITIVELY on top of a resident ComfyUI (~17GB warm
# lease). Predict-before-load (ADR-0003): if measured free VRAM is below the narrator footprint + a
# compositor reserve, skip the model half rather than risk an OOM the coordinator never sees.
CANON_HEADROOM_MIB = int(os.environ.get("LUCID_CANON_HEADROOM_MIB", "4000"))


def _canon_for(chain, node, caption, *, delta_fn=None):
    """ADR-0037 L0: this node's canon = its PARENT's canon folded with (this beat's label + grounded
    caption). Cached on the node — per-node like beats/caption, so a branch carries its OWN spine's canon
    (re-derive-O(spine) by construction; a revert just reads the cached node). Code disposes
    time_of_day/mood; the LLM delta (lucid_ground.ledger_delta_llm — qwen2.5vl on the 0.6 fidelity lane,
    the model the on-box gate cleared) proposes who/what/story. `delta_fn` overrides the LLM seam (tests
    inject a fake; defaults to the real one). Fail-open: kill-switch off / no caption / low VRAM headroom /
    any error -> None and the steering degrades to today's labels chain. The caller's save_chain persists it
    (private -> sealed tmpfs via ST.save_chain; canon is a chain FIELD, never a sidecar -> single-sink,
    freed on burn with the chain)."""
    if not CANON_ENABLED or not caption:
        return None
    try:
        if node.get("canon"):
            return node["canon"]                       # already folded for this frame — hold it
        free = E._comfy_free_mib()                     # None = ComfyUI cold (no warm lease) -> VRAM is free
        if free is not None and free < CANON_HEADROOM_MIB:
            return None                                # under the warm lease + low headroom -> skip the load
        pid = node.get("parent")
        parent = _by_id(chain).get(pid) if pid is not None else None
        prior = (parent.get("canon") if parent else None) or G.empty_canon()
        seed = not (parent and parent.get("canon"))    # root / a chain predating canon -> a SEED pass
        return G.update_canon(prior, node.get("label"), caption,
                              delta_fn=(delta_fn or G.ledger_delta_llm), seed=seed)
    except Exception:
        return None                                    # fail-open: keep no canon, steering uses labels


class GenerationError(Exception):
    """A SUBSTANTIVE ComfyUI failure (OOM / bad graph / backend-down / timeout / no video) — as opposed
    to a genuine lease PREEMPT (interactive work SIGKILLed ComfyUI mid-render, a calm fail-open yield).
    `.user_msg` is the honest human-facing line the surface shows instead of the false-comforting
    'skipped — your desktop is untouched'. Raised only when the caller opts in (raise_errors=True);
    fail-open callers (create_from_image, the drainers) still get None (audit findings 1.1/2/5/6/11)."""
    def __init__(self, user_msg, cause=""):
        self.user_msg = user_msg
        self.cause = cause
        super().__init__(cause or user_msg)


def _is_unreachable_error(e):
    """True iff the exception is a TRANSPORT error = ComfyUI didn't answer (urllib/socket). A
    RuntimeError/TimeoutError that a REACHABLE ComfyUI raised (an error status / a too-long render) is
    NOT 'unreachable' — it's substantive. NB TimeoutError subclasses OSError, so exclude it first."""
    if isinstance(e, (GenerationError, TimeoutError)):
        return False
    return isinstance(e, (urllib.error.URLError, ConnectionError, socket.timeout, OSError))


def _human_gen_error(e):
    """Map a raw generation exception to one honest, non-alarming, actionable line."""
    s = str(e).lower()
    if "out of memory" in s or "outofmemory" in s or "torch.cuda" in s and "memory" in s:
        return "The graphics card ran out of memory on this clip — try a shorter or lower-quality beat."
    if isinstance(e, TimeoutError):
        return "The render took too long and timed out."
    if _is_unreachable_error(e):
        return "The video backend (ComfyUI) wasn't reachable — it may have been preempted or shut down."
    if "/prompt rejected" in s or "prompt_no_outputs" in s:
        return "ComfyUI rejected the render request (a workflow or model problem)."
    if "produced no video" in s or "no video" in s:
        return "The render finished but produced no video."
    return "The clip didn't come through (a generation error)."


def _classify_generation_failure(e):
    """'preempt' (calm fail-open yield) vs 'error' (surface honestly). A genuine preempt = a higher-tier
    lease took the GPU and SIGKILLed our owned ComfyUI -> a transport error AND the coordinator now shows
    a higher-priority holder. A transport error with NO such holder is a crash/backend-down (an error);
    a RuntimeError/TimeoutError from a reachable ComfyUI is always an error."""
    if _is_unreachable_error(e) and _coord_holder_tier() in ("interactive", "yielding"):
        return "preempt"
    return "error"


# ---------------- one leased, confirmed-evicted, gated video beat ----------------
def _evict_targets():
    """The Ollama models that MUST leave VRAM before the ~17 GB i2v lease (B1, ADR-0015 §3): the vision
    model AND the (possibly swapped-in) narrator. De-duped, order preserved — when the narrator IS the
    vision model it's one evict. This list grew to include the narrator because anticipatory pre-warm
    (ADR-0045) can keep the ~8.7 GB narrator resident, and 8.7 + 17 GB OOMs a 24 GB card."""
    return list(dict.fromkeys([E.MODEL, E.NARRATOR_MODEL]))


def generate_video(session, prompt, anchor_frame, tier="batch", external_lease=False, length=None,
                   rating="sfw", guides=None, seed=None, quality="draft", raise_errors=False):
    """B1 dance: actively evict beat model (`ollama stop`) + confirm -> lease -> generate -> release. Returns clip path,
    or None to skip the turn (fail open). The prompt MUST already have passed S.gate_prompt.
    Private sessions render to a sealed subdir and the clip is moved to tmpfs (ADR-0016).
    `rating` (the tip frame's sealed content rating) sets the render LoRA strength so a SFW beat is
    not rendered by the explicit-anatomy graph (code disposes; default SFW = LoRA off).
    `tier` (default "batch") is threaded to lease_spawn; the drainer passes "best-effort".

    `external_lease=True` (warm-keep, ADR-0015): the CALLER already holds a batch lease and the
    coordinator already owns a ready ComfyUI, so this turn neither Spawns nor Releases — it just
    evicts the beat model, confirms ComfyUI is up, and generates. The default (False) preserves the
    per-beat Spawn/Release dance for every one-shot caller (create_from_image, the drain path).

    `guides` (LTX-only spatial feed-forward): an ordered list of (frame_abs_path, t_seconds, tag)
    forwarded to E.run_beat, which pins each frame as an LTXVAddGuide keyframe (fail-open; Wan ignores)."""
    private = ST.is_private(session)
    for _m in _evict_targets():   # BOTH the VLM and a swapped-in narrator — pre-warm (ADR-0045) can hold either
        if not S.force_evict(_m):
            log(f"could not evict '{_m}' from VRAM — refusing to load video (B1 fail-closed)")
            return None
    token = None
    if not external_lease:
        token = lease_spawn(tier)
        if token is None:
            return None
    try:
        if not wait_ready():
            log("ComfyUI not ready in time — skipping (requeue)")
            return None
        gen_cmd = os.environ.get("LUCID_GEN_CMD")
        if gen_cmd:                                   # test seam: prove the dance without a GPU
            if subprocess.run(gen_cmd, shell=True).returncode != 0:
                return None
            scope = ST._priv_output_dir(session) if private else None  # never a global output walk
            return ST.place_clip(session, private, _newest_clip(scope))
        clip, _seed = E.run_beat(prompt, anchor_frame, seed=seed, length=length, rating=rating,
                                 quality=quality, output_prefix=ST.output_prefix(session, private),
                                 guides=guides)
        return ST.place_clip(session, private, clip)  # private: move out of shared output -> tmpfs
    except Exception as e:                            # noqa: BLE001
        # Don't blame SIGKILL for everything (audit finding 11): an OOM, a /prompt 400, a backend-down,
        # or a render timeout are NOT preempts and must not be painted as the calm 'skipped — desktop
        # untouched'. Classify, then either yield calmly (genuine preempt) or surface honestly.
        if not raise_errors:                          # legacy fail-open callers: keep the None contract,
            log(f"generation error ({type(e).__name__}: {e}) — fail open; clip lost, loop yields")
            return None                               # but log honestly (no false 'likely preempted')
        if _classify_generation_failure(e) == "preempt":
            log(f"beat yielded — interactive work preempted the dream; ComfyUI was reclaimed ({e})")
            return None                               # a real preempt: the dream yields, calm fail-open
        # Substantive failure under a WARM lease: stop the (possibly still-running) job so a timed-out/errored
        # render doesn't keep burning the held ComfyUI — the next beat shouldn't queue behind a dead one (audit
        # 3.1). Best-effort; the per-call Spawn/Release path reclaims via SIGKILL anyway, so only warm needs it.
        if external_lease:
            try:
                E.cc.interrupt()
            except Exception:                         # noqa: BLE001 — never let cleanup mask the real error
                pass
        log(f"generation FAILED ({type(e).__name__}: {e}) — surfacing as an error, not a skip")
        raise GenerationError(_human_gen_error(e), cause=str(e)) from e
    finally:
        if not external_lease:
            lease_release(token)  # warm-keep caller owns the lease across beats; releases it itself


def _newest_clip(scope_dir=None):
    out = scope_dir or os.path.join(E.cc.COMFY_ROOT, "output")
    if not os.path.isdir(out):
        return None
    best, best_m = None, -1
    for root, _d, files in os.walk(out):
        for fn in files:
            if fn.lower().endswith((".mp4", ".webm", ".mkv")):
                p = os.path.join(root, fn)
                m = os.path.getmtime(p)
                if m > best_m:
                    best, best_m = p, m
    return best


def generate_beat_preview(session, node_id, beat, external_lease=True):
    """ADR-0023: render ONE still PREVIEW for a gutter choice — a glimpse of the "potential path" this beat
    would grow into, so the choice cards stop all showing the same seed image. The cheapest faithful render: a
    MIN_LEN draft i2v from the node's conditioning frame, on the (gated) beat prompt + the node's deterministic
    seed (an honest prefix of the real beat), whose LAST frame is extracted to a sealed-for-private PNG; the
    transient clip is then deleted (a preview is a still, never a chain node). Returns the preview ref
    (INPUT_DIR-relative — the sealed subpath for private) or None.

    Rides the WARM batch lease (external_lease=True): the caller (lucid_web's dwell worker) already holds the
    lease and ComfyUI is up, so this neither Spawns nor Releases — serial, in-warm-process, never a second
    ~17 GB admission (the council's blessed dwell-speculation; reuses ADR-0032's warm-lease pattern). IDEMPOTENT
    (content-addressed by node+beat — a held menu re-renders nothing) and TOTALLY fail-open (any error -> None,
    so the card simply stays on the seed still)."""
    try:
        private = ST.is_private(session)
        chain = load_chain(session)
        node = _node_or_tip(chain, node_id)
        key = _beat_key(beat)
        ref_name, abs_path = ST.frame_ref(session, private, f"{session}_bp_{node['id']}_{key}.png")
        if os.path.exists(abs_path):                  # held like the menu — already rendered for this frame
            return ref_name
        gated = S.gate_prompt(beat.get("prompt") or "")
        if gated is None:                             # never render an ungated prompt — skip (fail-open)
            return None
        anchor = node.get("out_frame")                # the conditioning frame this path continues FROM
        if not anchor:
            return None
        rating = _max_rating(chain.get("rating_floor"), *(n.get("rating") for n in _ancestry(chain, node)))
        # deterministic per-(node,beat) seed: reproducible across reloads AND distinct across siblings, so each
        # card's path actually differs (a same-seed render of two prompts can converge).
        seed = ((_beat_seed(chain, node["id"]) + zlib.crc32(key.encode())) % (2**31 - 1)) or 1
        clip = generate_video(session, gated, anchor, external_lease=external_lease,
                              length=E.MIN_LEN, rating=rating, seed=seed, quality="draft")
        if not clip:
            return None
        out = E.extract_last_frame(clip, ref_name, out_path=abs_path)
        # The clip is a transient — a preview is a STILL, never a chain node. Delete its whole stem family
        # (the .mp4 AND any VHS metadata sidecar .png, which carries the un-taken beat's prompt) so no
        # un-chosen-path residue lingers in the shared output dir (privacy consult 2026-06-21, P3/P4). The
        # `{session}_`-prefixed names are also a purge_persistent backstop if a crash skips this.
        try:
            stem = os.path.basename(os.path.splitext(clip)[0])
            cdir = os.path.dirname(clip)
            for fn in os.listdir(cdir):
                if fn.startswith(stem + "."):
                    try:
                        os.remove(os.path.join(cdir, fn))
                    except OSError:
                        pass
        except OSError:
            pass
        return ref_name if out else None
    except Exception as e:
        log(f"preview: failed ({e}) — card stays on the seed still (fail-open)")
        return None


def decorate_beats(session, node_id, beats):
    """Add per-beat `key` (content-address) + `preview` (the still's ref iff it EXISTS on disk, else None) to a
    held menu, for /api/beats. PURE READ — never generates (that's the dwell worker's job), no dir side effects
    (uses the read-only frame_abs, not frame_ref). The preview is keyed to the node the menu grounds on (the tip
    if node_id is None), so a branch-from-an-earlier-beat menu resolves its OWN previews, not the tip's."""
    if not beats:
        return beats
    try:
        chain = load_chain(session)
        nid = _node_or_tip(chain, node_id)["id"]
        private = ST.is_private(session)
    except Exception:
        return [{**b, "key": _beat_key(b), "preview": None} for b in beats]
    out = []
    for b in beats:
        key = _beat_key(b)
        name = f"{session}_bp_{nid}_{key}.png"
        ref = None
        try:
            if os.path.exists(ST.frame_abs(session, private, name)):
                ref = (f".lucid-priv-{session}/{name}" if private else name)
        except Exception:
            ref = None
        out.append({**b, "key": key, "preview": ref})
    return out


def _notes_digest(chain, parent):
    """A stable hash binding a reviewed reading to the EXACT (parent, notes) it was derived from (ADR-0023
    fuse-review). /api/fuse returns it with the reading it produced; /api/dream recomputes it before running
    a user-edited reading and REFUSES on a mismatch — so an edit reviewed against one set of notes can never
    silently run against a different set (the staleness gate). Deliberately NOTES-ONLY: the typed prompt is
    NOT included (editing your words must not false-trigger a "your notes changed" error — the edited reading
    is what runs regardless of the words), and the subject is NOT included (it is captured once and lives
    inside the fused text already, and is in-memory-only at fuse time so it would spuriously diverge). The
    fuse CACHE keys on the prompt separately, so the readback still updates as you type."""
    h = hashlib.blake2b(digest_size=12)
    h.update(str(parent.get("id")).encode())
    for n in sorted(parent.get("notes") or [], key=lambda x: str(x.get("id", ""))):
        region = "%s,%s,%s" % (n.get("x"), n.get("y"), n.get("r")) if "x" in n else ""
        h.update(("\x00%s|%s|%s|%s|%s|%s" % (n.get("id"), n.get("tag"), n.get("text") or "",
                                             n.get("t"), region, n.get("mask") or "")).encode("utf-8", "replace"))
    return h.hexdigest()


def _collect_note_frames(session, parent, notes, private):
    """Screenshot each tagged MOMENT of the parent clip and return (tagged, guides) — the two channels a
    note feeds forward (ADR-0023/0025/0032). `tagged` = [{b64,tag,text,t,region}] for the VLM decomposition
    (BOTH engines — it authors the prompt); `guides` = [(abs_path,t,tag,region,mask_abs)] for the LTX engine
    to pin as keyframe/attention conditioning (the pixel channel). `region` on a tagged item is the coarse
    location phrase so the per-note text is read as referring to WHERE the viewer tapped. Fail-open: an
    unresolved frame is skipped. Shared by step() AND fuse_direction() so the Shot Card readback shows the
    SAME decomposition the render will run."""
    tagged, guides = [], []
    clip = parent.get("clip")
    for i, note in enumerate(sorted(notes, key=lambda x: x.get("t", 0.0))):
        b64 = None
        region = (note["x"], note["y"], note["r"]) if ("x" in note and "y" in note) else None
        if clip:
            nm, ap = ST.frame_ref(session, private, f"{session}_note{i}.png")
            fn = E.extract_frame_at(clip, note.get("t", 0.0), nm, out_path=ap)
            if fn:
                b64 = E.frame_to_b64(ap)
                mask_abs = None
                if note.get("mask"):
                    cand = os.path.join(E.INPUT_DIR, note["mask"])
                    mask_abs = cand if os.path.exists(cand) else None
                guides.append((ap, float(note.get("t", 0.0)), note.get("tag"), region, mask_abs))
        else:                                         # clip-less opening: use the parent's stored frame
            b64 = E.frame_to_b64(_frame_abs(session, parent))
        if b64:
            tagged.append({"b64": b64, "tag": note.get("tag"), "text": note.get("text", ""),
                           "t": float(note.get("t", 0.0)),
                           "region": _region_phrase(region[0], region[1]) if region else None})
    return tagged, guides


def fuse_direction(session, parent_id, prompt, *, _decompose=None, _subject=None, allow_model=True):
    """ADR-0023/0033: assemble the EXACT prompt the next beat would run — the notes decomposed (or the
    deterministic suffix), with the persistent subject folded IN — WITHOUT any lease/GPU, so the surface can
    show it and let the user correct it BEFORE committing minutes of generation. This is "model proposes,
    code disposes" made visible at the one juncture it was previously invisible: the fused string a 3B VLM
    authored, which used to drop straight into the render. NB the fuse runs ONE local VLM pass that LOADS
    AND EVICTS the 3B narrator (`keep_alive:0`, the same residency profile as a menu roll) — it does not hold
    it warm; the caller throttles it (TURN-phase backpressure) so an eager fuse never thrashes the slot.

    Returns a JSON-able dict:
      {ok, reason, fused, subject, source ('decompose'|'suffix'), rows, notes_digest}
    `fused` is the RED-LINE-GATED final string (subject prefix included) or None if it can't be gated even
    after the deterministic fallback. `rows` is the structured note channel (ordered by t) for the Shot Card.
    `allow_model=False` skips the VLM decomposition entirely (straight to the deterministic suffix) — used by
    the TURN-phase backpressure so a fuse fired while a beat is generating never competes for the narrator slot.
    `_decompose(beat_prompt, tagged, premise)->str|None` and `_subject(session, chain)->str|None` are
    injectable so the contract is testable without a model (and the deterministic path is then exercised)."""
    try:
        chain = load_chain(session)
    except Exception as e:                            # noqa: BLE001 — no dream loaded yet
        log(f"fuse: no chain ({e})")
        return {"ok": False, "reason": "No dream is loaded.", "fused": None, "subject": "",
                "source": "suffix", "rows": [], "notes_digest": ""}
    parent = _node_or_tip(chain, parent_id)
    notes = parent.get("notes") or []
    prompt = prompt or ""
    private = ST.is_private(session)
    digest = _notes_digest(chain, parent)
    rows = []
    for n in sorted(notes, key=lambda x: x.get("t", 0.0)):
        rows.append({"id": n.get("id"), "tag": n.get("tag"), "text": n.get("text") or "",
                     "t": float(n.get("t", 0.0)), "region": ("x" in n and "y" in n),
                     "mask": bool(n.get("mask"))})
    tagged, _guides = _collect_note_frames(session, parent, notes, private) if notes else ([], [])
    decompose = _decompose if _decompose is not None else E.decompose_notes
    refined = decompose(prompt, tagged, premise=chain.get("premise")) if (allow_model and tagged) else None
    source = "decompose" if refined else "suffix"
    body = refined if refined else (prompt + _steering_suffix(notes))
    subject = (_subject(session, chain) if _subject is not None else _subject_for(session, chain)) or ""
    fused = S.gate_prompt(_with_subject(subject, body))
    if fused is None:                                 # a red-lined fusion (e.g. a model embellishment) — fall
        body = prompt + _steering_suffix(notes)       # back to the deterministic suffix (no model text) + re-gate
        source = "suffix"
        fused = S.gate_prompt(_with_subject(subject, body))
    return {"ok": fused is not None,
            "reason": None if fused is not None else "Lucid couldn't read that direction safely — edit your notes.",
            "fused": fused, "subject": subject, "source": source, "rows": rows, "notes_digest": digest}


def step(session, prompt, label, tier="batch", external_lease=False, is_current=None, length=None,
         parent_id=None, quality="draft", raise_errors=False, fused_edited=None, anchor_override=None):
    """One leased turn: gate the prompt (both paths), generate under lease, append a node.
    `quality` ('draft'|'hero', ADR-0033) selects the Wan render lane; the interactive loop renders 'draft'
    and the node records a deterministic `seed` + `anchor` so rerender_hero() can refine the SAME shot.
    `parent_id` (optional) forks the new beat from THAT node instead of the tip — a new take growing
    from an earlier frame (the tree branches). Default None = continue the linear tip (legacy callers).
    `length` (optional) is the caller's chosen next-segment frame count; clamped in lucid_engine
    (code disposes) and recorded on the node so the chain can show per-segment duration.
    `tier` (default "batch") preserves the interactive callers; the ADR-0019 drainer passes
    "best-effort" so a held re-run is structurally preemptible by Tier::Interactive (fail-open).
    `external_lease=True` (warm-keep) threads through to generate_video so a caller holding the
    batch lease across beats (lucid_web) neither Spawns nor Releases ComfyUI per turn.

    `fused_edited` (optional, ADR-0023): the user reviewed the composed prompt in the Shot Card and either
    accepted or edited it. When given, step runs it VERBATIM — it skips the VLM decomposition AND the subject
    prefix (both already reflected in the reviewed text), so what the user saw is exactly what renders. The
    LTX pixel guides and the hold-anchor still derive from the notes, so a text edit never disables a region
    mask. The string is still red-line-gated here (defense in depth; /api/dream gates it at the door too).

    `is_current` (optional) is a freshness predicate checked RIGHT BEFORE the chain persist — the one
    state mutation. A beat is minutes long; the chain is loaded at the top and held in memory across
    generation, so if the session was cleared/restarted/deleted meanwhile (a `/api/start`,
    `/api/delete`, or burn arrived), writing this stale in-memory chain back would resurrect deleted
    data or clobber the new dream. If the caller says the turn is no longer current, we discard the
    clip (a cache artifact) and leave the chain untouched — fail-open, exactly like a preempt.

    `anchor_override` (optional, ADR-0040): an INPUT_DIR-relative keyframe (a prompt-edited frame from
    E.edit_frame) to seed this beat FROM instead of the parent's last frame. When given it IS the steering,
    so the parent's moment-notes (decomposition + LTX guides + the hold-anchor) are skipped — the edited
    keyframe shows the starting pose and `prompt` directs the motion (the subject prefix still applies)."""
    private = ST.is_private(session)
    chain = load_chain(session)
    parent = _node_or_tip(chain, parent_id)
    seg_len = E.clamp_length(length)
    # Feed the PARENT node's moment annotations forward (ADR-0023). STEERING (semantic): a short,
    # deterministic suffix built from the notes is appended to the base prompt BEFORE the gate, so the
    # steering text is red-line-gated alongside the prompt (no note can carry text past the gate). With
    # no notes the suffix is "" and the prompt is unchanged — legacy behaviour intact.
    # ADR-0040: an edited keyframe (anchor_override) IS the new direction — ignore the parent's moment-notes
    # (decomposition / LTX guides / hold-anchor) so a stale per-moment steer can't fight the edited pose.
    notes = [] if anchor_override else (parent.get("notes") or [])
    # The two channels a note feeds forward (ADR-0023/0025/0032), collected ONCE: `tagged` (per-moment frames
    # + intent + coarse region) for the VLM decomposition that authors the prompt, and `guides` (abs_path, t,
    # tag, region, mask) for the LTX engine to pin as keyframe/attention conditioning. Built BEFORE any
    # GPU/lease work; the GUIDES are applied to the engine below ONLY when LTX is active (Wan ignores them).
    tagged, guides = (_collect_note_frames(session, parent, notes, private) if notes else ([], []))
    if fused_edited is not None:
        # ADR-0023: the user reviewed (and possibly edited) the composed prompt in the Shot Card. Run THAT
        # verbatim — do NOT re-decompose and do NOT re-prepend the subject (it is already inside the reviewed
        # text), so "what you saw is what runs". The structured channels still derive from the notes (the LTX
        # `guides` above + the hold-anchor below): a text edit never silently disables a mask or the anchor.
        prompt_final = fused_edited
    else:
        # DECOMPOSITION (image-capable, BOTH engines): hand the per-note frames + intent to the VLM
        # (E.decompose_notes) and let it author one refined i2v continuation prompt — the model SEES what the
        # viewer pointed at, preferred over the blind text-suffix. Fail-open (ADR-0003): no notes / model
        # unavailable / returns None -> the deterministic text-suffix path, and with NO notes
        # prompt_final == (prompt or "") — legacy behaviour exactly.
        refined = E.decompose_notes(prompt or "", tagged, premise=chain.get("premise")) if tagged else None
        prompt_final = refined if refined else ((prompt or "") + _steering_suffix(notes))
        # IDENTITY (ADR-0033): quietly prefix the persistent subject (captured once from the opening frame)
        # so the face/clothing carry across the independent i2v cuts. Render-prompt ONLY. The COMBINED string
        # is red-line-gated below, so the descriptor passes the same gate as any prompt. None -> unchanged.
        prompt_final = _with_subject(_subject_for(session, chain), prompt_final)
    gated = S.gate_prompt(prompt_final)
    if gated is None:
        raise SystemExit("prompt refused by red-line gate (B3)")
    nid = _next_id(chain)   # collision-free across the tree (parent+1 would clash once a node forks);
    # computed up-front so the spatial anchor frame gets the new node's stable name.
    # ANCHOR (spatial): a `hold` note pins the next beat to a tagged MOMENT of the parent clip, not just
    # its last frame; falls back to parent["out_frame"] on no-hold / no-clip / extraction failure.
    anchor = anchor_override or _anchor_for(session, parent, notes, f"{session}_n{nid}_anchor.png")
    # The monotone content floor governs the render LoRA — taken along the NEW beat's branch (parent's
    # ancestry), so a take consistent with the frame it grows from inherits that line's rating, and a
    # SFW branch off a SFW ancestor isn't dragged mature by an unrelated sibling. The opening (typed
    # before its first roll) falls back to "sfw" — the safe default. The red-line gate is independent.
    rating = _max_rating(chain.get("rating_floor"), *(n.get("rating") for n in _ancestry(chain, parent)))
    # GUIDES gate: hand the per-moment guide frames to the engine ONLY for the LTX engine (10eros),
    # which applies them as guide-conditioning (fail-open in the engine). Wan keeps its VLM-decomposed
    # prompt + single anchor path, so it gets guides=None. No notes -> guides is [] -> None either way.
    g = guides if (guides and E.current_engine() == "10eros") else None
    # SEED (ADR-0033): deterministic per-node noise, so this exact shot can be re-rendered at hero quality
    # (rerender_hero reuses node["seed"]/["anchor"]/["prompt"]). Persisted on the node below.
    beat_seed = _beat_seed(chain, nid)
    clip = generate_video(session, gated, anchor, tier=tier, external_lease=external_lease,
                          length=seg_len, rating=rating, guides=g, seed=beat_seed, quality=quality,
                          raise_errors=raise_errors)
    if clip is None:
        log("turn skipped (fail open) — chain unchanged")
        return None
    if is_current is not None and not is_current():
        log("turn superseded mid-beat (session restarted/deleted) — discarding clip, chain unchanged")
        return None
    ref_name, abs_path = ST.frame_ref(session, private, f"{session}_n{nid}.png")
    out_frame = E.extract_last_frame(clip, ref_name, out_path=abs_path)  # store owns the path
    node = {"id": nid, "parent": parent["id"], "label": label, "prompt": gated,
            "seed": beat_seed, "anchor": anchor, "quality": quality,
            "clip": clip, "out_frame": out_frame, "length": seg_len, "rating": rating}
    if PALETTE_ENABLED:                              # ADR-0037 L2: flag-only palette drift vs the parent frame
        try:                                         # keep-not-block; the verdict is steering telemetry + the
            drift = G.palette_drift(_frame_abs(session, parent), abs_path)   # calm consistency chip, never a gate
            node["palette"] = G.palette_verdict(drift)   # 'steady' | 'shifted' | 'unknown' (couldn't measure)
        except Exception:
            pass                                     # fail-open: a missing verdict simply shows no chip
    chain["nodes"].append(node)
    save_chain(session, chain)
    return node


def rerender_hero(session, node_id, tier="batch", external_lease=False, is_current=None,
                  raise_errors=False):
    """Re-render an existing beat at HERO quality (ADR-0033): reuse the node's stored seed + (gated) prompt
    + anchor + rating so the non-distilled 20-step Wan lane refines the SAME shot the draft produced, and
    store it as node['hero_clip'] (the draft `clip` is kept, so the browse loop stays fast and the keeper
    plays/downloads in HD). The chain's `out_frame` anchors are NOT re-derived: the hero is the same shot at
    higher fidelity (a same-seed hero last frame is ~identical), so the next beat still continues from the
    draft's frame and downstream continuity is untouched. Returns the updated node, or None (unknown /
    clip-less / opening node, a fail-open yield, or a superseded turn).

    Wan-only quality lane (the chosen engine); harmless on 10eros (run_beat ignores `quality` there).
    `external_lease`/`tier`/`is_current` mirror step() so a warm-keep web caller owns the lease across the
    re-render and a mid-flight /api/start can't resurrect a deleted dream. Only node['hero_clip'] is written
    (onto a FRESHLY re-loaded chain), so a concurrent edit during the minutes-long render isn't clobbered."""
    chain = load_chain(session)
    node = _by_id(chain).get(node_id)
    if node is None or not node.get("clip"):
        log(f"hero: node {node_id!r} unknown or not yet rendered — nothing to refine")
        return None
    parent = _by_id(chain).get(node.get("parent"))
    anchor = node.get("anchor")
    if not anchor and parent is not None:        # legacy node (pre-anchor persist): re-derive from parent
        anchor = _anchor_for(session, parent, parent.get("notes") or [],
                             f"{session}_n{node_id}_anchor.png")
    if not anchor:
        anchor = node.get("out_frame")           # last resort — render off the node's own stored frame
    gated = S.gate_prompt(node.get("prompt") or "")   # the stored prompt already passed; re-gate defensively
    if gated is None:
        log(f"hero: node {node_id!r} prompt no longer passes the red-line gate — skipping")
        return None
    seed = node.get("seed")
    if not isinstance(seed, int):
        seed = _beat_seed(chain, node_id)        # legacy node without a stored seed
    clip = generate_video(session, gated, anchor, tier=tier, external_lease=external_lease,
                          length=node.get("length"), rating=node.get("rating", "sfw"),
                          seed=seed, quality="hero", raise_errors=raise_errors)
    if clip is None:
        log("hero: re-render skipped (fail open) — chain unchanged")
        return None
    if is_current is not None and not is_current():
        log("hero: superseded mid-render (session restarted/deleted) — discarding clip, chain unchanged")
        return None
    fresh = load_chain(session)                  # re-load: our copy may be stale after a minutes-long render
    fnode = _by_id(fresh).get(node_id)
    if fnode is None:
        log("hero: node vanished during render (deleted) — discarding clip")
        return None
    fnode["hero_clip"] = clip
    save_chain(session, fresh)
    return fnode


def replace_beat(session, node_id, anchor_override, prompt=None, length=None, tier="batch",
                 external_lease=False, is_current=None, raise_errors=False):
    """ADR-0040: re-render an existing beat IN PLACE from an edited keyframe (`anchor_override`, an
    INPUT_DIR-relative name produced by E.edit_frame), replacing its clip + out_frame. The node's ORIGINAL
    is backed up ONCE to node['prev'] so revert_beat() can restore it — the edit is reversible, never a
    destructive overwrite (ADR-0005). `prompt` (the edit instruction) becomes the motion prompt; None reuses
    the node's stored prompt verbatim. The opening (parent-less) node has no clip and can't be replaced.
    Children are NOT re-rendered (they keep their own stored anchors) — replacing a shot doesn't retroactively
    redraw its continuation; the user can edit those too. Returns the updated node, or None (unknown/opening
    node, a fail-open yield, or a superseded turn). Mirrors step()/rerender_hero's lease + epoch discipline."""
    private = ST.is_private(session)
    chain = load_chain(session)
    node = _by_id(chain).get(node_id)                 # STRICT lookup — never the tip-fallback (would edit the wrong shot)
    if node is None or node.get("parent") is None:
        return None                                   # the opening still has no clip to replace
    seg_len = E.clamp_length(length if length is not None else node.get("length"))
    rating = _max_rating(chain.get("rating_floor"), *(n.get("rating") for n in _ancestry(chain, node)))
    if prompt:                                        # the edit instruction directs the motion; gate + subject-prefix it
        gated = S.gate_prompt(_with_subject(_subject_for(session, chain), prompt))
        if gated is None:
            raise SystemExit("prompt refused by red-line gate (B3)")
    else:
        gated = node.get("prompt")                    # reuse the node's stored prompt (already gated + subject-prefixed)
    seed = node.get("seed") or _beat_seed(chain, node["id"])
    clip = generate_video(session, gated, anchor_override, tier=tier, external_lease=external_lease,
                          length=seg_len, rating=rating, seed=seed, quality=node.get("quality", "draft"),
                          raise_errors=raise_errors)
    if clip is None:
        log("edit-replace skipped (fail open) — chain unchanged")
        return None
    if is_current is not None and not is_current():
        log("edit-replace superseded mid-render — discarding clip, chain unchanged")
        return None
    # Re-load the chain AFTER the minutes-long render (mirrors rerender_hero): a synchronous /api/note or
    # /api/edit/revert may have written it meanwhile, so our top-of-function copy is stale — applying to the
    # FRESH chain avoids clobbering that write (the append-only / lost-update guard, ADR-0005).
    fresh = load_chain(session)
    node = _by_id(fresh).get(node_id)
    if node is None or node.get("parent") is None:        # node deleted/rebased under us — discard the clip
        log("edit-replace target vanished mid-render — discarding clip, chain unchanged")
        return None
    # Back up the ORIGINAL once (a 2nd edit keeps the FIRST original, so revert always returns to the source
    # shot). The new out_frame gets a FRESH name so prev's frame file survives for revert (append-only, ADR-0005).
    node.setdefault("prev", {k: node.get(k) for k in
                             ("clip", "out_frame", "anchor", "prompt", "seed", "length", "quality")})
    edits = int(node.get("edits", 0)) + 1
    ref_name, abs_path = ST.frame_ref(session, private, f"{session}_n{node['id']}_e{edits}.png")
    out_frame = E.extract_last_frame(clip, ref_name, out_path=abs_path)
    node.update({"clip": clip, "out_frame": out_frame, "anchor": anchor_override, "prompt": gated,
                 "seed": seed, "length": seg_len, "edited": True, "edits": edits})
    save_chain(session, fresh)
    return node


def revert_beat(session, node_id):
    """ADR-0040: undo an in-place edit (replace_beat) — restore the node's backed-up ORIGINAL clip/frame/
    prompt from node['prev']. Idempotent: a node with no backup (never edited / already reverted) returns
    None and changes nothing. Returns the restored node, or None. The discarded edited clip/frame files are
    left on disk (harmless extra scratch for a persistent dream; a private dream burns them on logout)."""
    chain = load_chain(session)
    node = _by_id(chain).get(node_id)
    prev = node.get("prev") if isinstance(node, dict) else None
    if not isinstance(prev, dict):
        return None
    node.update(prev)
    for k in ("prev", "edited", "edits"):
        node.pop(k, None)
    save_chain(session, chain)
    return node


def context_for(session, caption=None, node=None, canon=None):
    """Story-so-far from the chain, led by the session's premise (the initial prompt) so every proposed
    beat stays on-theme. `node` (optional) scopes the labels to THAT beat's branch (root -> node), so a
    take growing from an earlier frame is proposed against its own line, not a sibling's; default None =
    the linear whole-chain spine (legacy). `caption` (optional) is the freshly-grounded description of
    the current frame, used as "on screen now" before it has been sealed on the node. `canon` (optional,
    ADR-0037) is the FRESHLY-folded canon for this node — passed in so the steering reflects the fold that
    just happened (context_for re-reads the chain from disk, where this turn's canon isn't persisted yet);
    None falls back to the node's persisted canon."""
    chain = load_chain(session)
    here = _by_id(chain).get(node["id"]) if node else None
    spine = _ancestry(chain, here) if here else chain["nodes"]
    labels = [n["label"] for n in spine if n["label"] not in (None, "opening")]
    cap = caption if caption is not None else (here or chain["nodes"][-1]).get("caption")
    parts = []
    premise = chain.get("premise")
    if premise:
        parts.append("This dream is about: " + premise + ".")
    # ADR-0037 L0: the canon ledger REPLACES the literal labels-telephone join. The canon is scoped to THIS
    # node (its spine's cached fold), so a branch is proposed against its own line. Fail-open: no canon
    # (kill-switch off / not yet folded / old chain) -> today's labels chain exactly.
    canon = canon if canon is not None else (here or chain["nodes"][-1]).get("canon")
    canon_line = G.canon_to_context(canon) if (CANON_ENABLED and canon) else ""
    if canon_line:
        parts.append(canon_line)
    elif labels:
        parts.append("Story so far: " + " -> ".join(labels) + ".")
    else:
        parts.append("The dream is just beginning.")
    parts.append("On screen now: " + (cap or "the opening image."))
    # Reinforce the beat-gen prompt's DIAL 2 (advance-the-narrative) at the END of the user turn — the
    # spot a small model weights hardest. The system prompt carries this on its own; this just sharpens
    # premise-advancement (A/B-verified: noticeably richer, more on-premise menus). Pure steering — the
    # red-line gate is still the only safety authority and is unaffected.
    parts.append("Advance THIS dream toward its premise and the open question in the story so far — "
                 "surprise me THROUGH the frame, don't just hold it.")
    return " ".join(parts)


# ---------------- CLI ----------------
def _main():
    ap = argparse.ArgumentParser(description="Lucid MVP — linear chain through the lease (ADR-0015)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("session"); s.add_argument("--image", required=True)
    s.add_argument("--private", action="store_true", help="ephemeral incognito dream — sealed, not saved, auto-burned (ADR-0016)")
    s.add_argument("--consent", action="store_true", help="I am this person / have the right to use this image (B2, ADR-0017)")
    b = sub.add_parser("beats"); b.add_argument("session")
    st = sub.add_parser("step"); st.add_argument("session")
    st.add_argument("--choose", type=int); st.add_argument("--prompt"); st.add_argument("--label", default="custom")
    bn = sub.add_parser("burn"); bn.add_argument("session")
    args = ap.parse_args()

    if args.cmd == "start":
        try:
            c = start(args.session, args.image, private=args.private, consent=args.consent)
        except SeedBlocked as e:
            hint = "  (re-run with --consent if you are this person / hold the rights)" if e.verdict.requires_consent else ""
            print(f"seed refused (B2): {e.verdict.reason}{hint}")
            return
        tag = " [PRIVATE — ephemeral, sealed, not saved]" if args.private else ""
        print(f"started '{args.session}' (anchor {c['nodes'][0]['out_frame']}){tag}")
    elif args.cmd == "burn":
        removed, failed = burn(args.session)
        if removed or failed:
            msg = f"burned '{args.session}': {len(removed)} sink(s) removed"
            if failed:
                msg += f"; {len(failed)} FAILED (NOT wiped): {failed}"
            print(msg)
        else:
            print(f"nothing private to burn for '{args.session}'")
    elif args.cmd == "beats":
        beats, caption, rating = roll_menu(args.session, load_chain(args.session))
        print(f"on screen: {caption or '(ungrounded)'}  |  rating: {rating}")
        if not beats:
            print("  (no valid beats — type your own)")
        for i, bt in enumerate(beats):
            print(f"  [{i}] {bt['label']}  ::  {bt['prompt']}")
    elif args.cmd == "step":
        if args.prompt:
            prompt, label = args.prompt, args.label
        else:
            beats, _cap, _rating = roll_menu(args.session, load_chain(args.session))
            if not beats:
                raise SystemExit("no beats proposed; pass --prompt to type your own")
            chosen = beats[args.choose or 0]
            prompt, label = chosen["prompt"], chosen["label"]
        nd = step(args.session, prompt, label)
        print(f"-> node {nd['id']} clip={nd['clip']}" if nd else "-> turn skipped (fail open)")


if __name__ == "__main__":
    _main()

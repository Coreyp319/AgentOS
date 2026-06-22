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

COORD_NAME = "org.agentos.Coordinator1"
COORD_PATH = "/org/agentos/Coordinator1"
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
          name=None):
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
    chain = {"session": session, "private": private,
             "name": (name or "").strip()[:80] or None,
             "created": time.time(),
             "seed": random.randint(1, 2**31 - 1),
             "premise": (premise or "").strip()[:300] or None,
             "nodes": [
                 {"id": 0, "parent": None, "label": "opening", "prompt": None,
                  "seed": None, "clip": None, "out_frame": ref_name}]}
    save_chain(session, chain)
    return chain


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
        rating = _max_rating(node.get("rating"))            # sealed monotone floor; None -> sfw
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


def _steering_suffix(notes):
    """A SHORT deterministic prompt suffix that feeds a parent's notes forward to the next beat (code
    disposes — sorted by `t`, fixed phrasing per tag). Empty string when there are no notes. The caller
    appends this to the base prompt and gates the COMBINED string, so steering text is red-line-gated too."""
    parts = []
    for n in sorted(notes, key=lambda x: x.get("t", 0.0)):
        tag, text = n.get("tag"), (n.get("text") or "").strip()
        if tag == "more":
            parts.append("; emphasize " + (text or "this"))
        elif tag == "less":
            parts.append("; less " + (text or "of this"))
        elif tag == "hold":
            parts.append("; hold the framing and composition")
        elif tag == "change":
            parts.append("; change " + (text or "this"))
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
    if caption and not S.red_line_ok(caption):     # a model-written caption is untrusted text too
        caption = None
    beats = propose(context_for(session, caption=caption, node=node), n=n, rating=rating, frame_b64=frame_b64)
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
    if not S.force_evict(E.MODEL):
        log(f"could not evict '{E.MODEL}' from VRAM — refusing to load video (B1 fail-closed)")
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
        rating = _max_rating(*(n.get("rating") for n in _ancestry(chain, node)))
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


def step(session, prompt, label, tier="batch", external_lease=False, is_current=None, length=None,
         parent_id=None, quality="draft", raise_errors=False):
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

    `is_current` (optional) is a freshness predicate checked RIGHT BEFORE the chain persist — the one
    state mutation. A beat is minutes long; the chain is loaded at the top and held in memory across
    generation, so if the session was cleared/restarted/deleted meanwhile (a `/api/start`,
    `/api/delete`, or burn arrived), writing this stale in-memory chain back would resurrect deleted
    data or clobber the new dream. If the caller says the turn is no longer current, we discard the
    clip (a cache artifact) and leave the chain untouched — fail-open, exactly like a preempt."""
    private = ST.is_private(session)
    chain = load_chain(session)
    parent = _node_or_tip(chain, parent_id)
    seg_len = E.clamp_length(length)
    # Feed the PARENT node's moment annotations forward (ADR-0023). STEERING (semantic): a short,
    # deterministic suffix built from the notes is appended to the base prompt BEFORE the gate, so the
    # steering text is red-line-gated alongside the prompt (no note can carry text past the gate). With
    # no notes the suffix is "" and the prompt is unchanged — legacy behaviour intact.
    notes = parent.get("notes") or []
    # DECOMPOSITION (image-capable): screenshot each tagged MOMENT of the parent clip, hand the frames
    # + their per-note intent to the VLM (E.decompose_notes) and let it author one refined i2v
    # continuation prompt. The model SEES what the viewer pointed at; this is preferred over the blind
    # text-suffix. Built BEFORE any GPU/lease work. Fail-open (ADR-0003): if there are no notes, or the
    # model is unavailable / returns None, we fall back to the deterministic text-suffix path below, and
    # with NO notes prompt_final == (prompt or "") — legacy behaviour exactly.
    tagged = []
    # GUIDES (image-conditioning, LTX-only): the SAME per-note frames screenshotted for the VLM are
    # also collected as (abs_path, t, tag) so the LTX engine can pin guide-conditioning on each tagged
    # MOMENT of the timeline. Only the clip-bearing parent contributes (the clip-less opening has no
    # timeline to pin a guide on); applied to the engine below ONLY when the LTX engine is active.
    guides = []
    if notes:
        clip = parent.get("clip")
        for i, note in enumerate(sorted(notes, key=lambda x: x.get("t", 0.0))):
            b64 = None
            if clip:
                nm, ap = ST.frame_ref(session, private, f"{session}_note{i}.png")
                fn = E.extract_frame_at(clip, note.get("t", 0.0), nm, out_path=ap)
                if fn:
                    b64 = E.frame_to_b64(ap)
                    # (abs_path, t, tag, region, mask_abs) — region=(x,y,r) is the WHERE (ADR-0025); mask_abs
                    # is a stored SEGMENTATION mask (ADR-0032, preferred over the disc). None for legacy notes.
                    region = (note["x"], note["y"], note["r"]) if ("x" in note and "y" in note) else None
                    mask_abs = None
                    if note.get("mask"):
                        cand = os.path.join(E.INPUT_DIR, note["mask"])
                        mask_abs = cand if os.path.exists(cand) else None
                    guides.append((ap, float(note.get("t", 0.0)), note.get("tag"), region, mask_abs))  # LTX pin
            else:                                       # clip-less opening: b64 the parent's stored frame
                b64 = E.frame_to_b64(_frame_abs(session, parent))
            if b64:
                tagged.append({"b64": b64, "tag": note.get("tag"),
                               "text": note.get("text", ""), "t": float(note.get("t", 0.0))})
    refined = E.decompose_notes(prompt or "", tagged, premise=chain.get("premise")) if tagged else None
    # VLM decomposition preferred; the deterministic steering suffix is the FALLBACK (model unavailable
    # / returned None / no tagged frames). The model's own output is still red-line-gated below.
    prompt_final = refined if refined else ((prompt or "") + _steering_suffix(notes))
    # IDENTITY (ADR-0033): quietly prefix the persistent subject (captured once from the opening frame)
    # so the face/clothing carry across the independent i2v cuts. Render-prompt ONLY — the beat MENU stays
    # motion/idea-led (it must not be dampened, see [[lucid-beatgen-prompt-redesign]]). The COMBINED string
    # is red-line-gated below, so the descriptor passes the same gate as any prompt. None -> unchanged.
    prompt_final = _with_subject(_subject_for(session, chain), prompt_final)
    gated = S.gate_prompt(prompt_final)
    if gated is None:
        raise SystemExit("prompt refused by red-line gate (B3)")
    nid = _next_id(chain)   # collision-free across the tree (parent+1 would clash once a node forks);
    # computed up-front so the spatial anchor frame gets the new node's stable name.
    # ANCHOR (spatial): a `hold` note pins the next beat to a tagged MOMENT of the parent clip, not just
    # its last frame; falls back to parent["out_frame"] on no-hold / no-clip / extraction failure.
    anchor = _anchor_for(session, parent, notes, f"{session}_n{nid}_anchor.png")
    # The monotone content floor governs the render LoRA — taken along the NEW beat's branch (parent's
    # ancestry), so a take consistent with the frame it grows from inherits that line's rating, and a
    # SFW branch off a SFW ancestor isn't dragged mature by an unrelated sibling. The opening (typed
    # before its first roll) falls back to "sfw" — the safe default. The red-line gate is independent.
    rating = _max_rating(*(n.get("rating") for n in _ancestry(chain, parent)))
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


def context_for(session, caption=None, node=None):
    """Story-so-far from the chain, led by the session's premise (the initial prompt) so every proposed
    beat stays on-theme. `node` (optional) scopes the labels to THAT beat's branch (root -> node), so a
    take growing from an earlier frame is proposed against its own line, not a sibling's; default None =
    the linear whole-chain spine (legacy). `caption` (optional) is the freshly-grounded description of
    the current frame, used as "on screen now" before it has been sealed on the node."""
    chain = load_chain(session)
    here = _by_id(chain).get(node["id"]) if node else None
    spine = _ancestry(chain, here) if here else chain["nodes"]
    labels = [n["label"] for n in spine if n["label"] not in (None, "opening")]
    cap = caption if caption is not None else (here or chain["nodes"][-1]).get("caption")
    parts = []
    premise = chain.get("premise")
    if premise:
        parts.append("This dream is about: " + premise + ".")
    parts.append("Story so far: " + " -> ".join(labels) + "." if labels else "The dream is just beginning.")
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

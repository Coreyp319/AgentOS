#!/usr/bin/env python3
"""Lucid MVP — a linear chain through the coordinator lease (ADR-0015).

The smallest lucid that proves the bet AND sits on the right side of the safety line. Unlike the
original spike (`lucid_engine.py`, which runs the ~21.8 GB i2v step with no lease and trusts
`keep_alive:0`), every video beat here:

  1. proposes beats via Ollama, then SCHEMA-VALIDATES + RED-LINE-FILTERS them (lucid_safety, B3);
  2. CONFIRMS the beat model is evicted before touching video VRAM (lucid_safety.confirm_evicted, B1);
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
import json
import os
import subprocess
import sys

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
EST_MIB = int(os.environ.get("LUCID_EST_MIB", "17000"))
COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1:8188")
READY_TIMEOUT = int(os.environ.get("LUCID_READY_TIMEOUT", "180"))


def log(*a):
    print("[lucid]", *a, file=sys.stderr)


# ---------------- coordinator lease client (reuses the dream.sh contract) ----------------
def _coord(*args):
    return subprocess.run(["busctl", "--user", "call", COORD_NAME, COORD_PATH, COORD_NAME, *args],
                          capture_output=True, text=True, timeout=30)


def lease_spawn():
    """Ask agentosd to Spawn+own ComfyUI under the batch lease. Returns a token, or None to
    fail open (coordinator down OR admission refused -> the dream yields, never forces VRAM)."""
    r = _coord("Spawn", "susas", "batch", str(EST_MIB), PROFILE, str(len(PARAMS)), *PARAMS)
    if r.returncode != 0:
        log(f"coordinator unreachable ({r.stderr.strip() or r.stdout.strip()}) — fail open (ADR-0003)")
        return None
    parts = r.stdout.split()           # "bts true <token> <msg...>"
    granted = len(parts) >= 2 and parts[1] == "true"
    if not granted:
        log(f"admission refused — {' '.join(parts[2:]) or 'GPU busy'} — fail open")
        return None
    token = parts[2] if len(parts) >= 3 else "0"
    log(f"lease GRANTED (token {token}); agentosd owns ComfyUI")
    return token


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


def start(session, opening_image, private=False, consent=False, _trusted_seed=False):
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
    chain = {"session": session, "private": private, "nodes": [
        {"id": 0, "parent": None, "label": "opening", "prompt": None,
         "seed": None, "clip": None, "out_frame": ref_name}]}
    save_chain(session, chain)
    return chain


def burn(session):
    """Wipe a private session's every sink (ADR-0016). No-op set for a persistent session."""
    return ST.burn(session)


# ---------------- beat-gen (validated) ----------------
def propose(context, n=4):
    """Ollama beat-gen, then schema-validate + red-line-filter (lucid_safety). [] -> type-your-own."""
    try:
        raw = E._ollama_json(E.SYS_SFW.format(n=n), context)
    except Exception as e:
        log(f"beat-gen failed ({e}) — type your own")
        return []
    return S.validate_beats(raw, n)


# ---------------- one leased, confirmed-evicted, gated video beat ----------------
def generate_video(session, prompt, anchor_frame):
    """B1 dance: confirm beat model evicted -> lease -> generate -> release. Returns clip path,
    or None to skip the turn (fail open). The prompt MUST already have passed S.gate_prompt.
    Private sessions render to a sealed subdir and the clip is moved to tmpfs (ADR-0016)."""
    private = ST.is_private(session)
    if not S.confirm_evicted(E.MODEL):
        log(f"could not confirm '{E.MODEL}' evicted — refusing to load video (B1 fail-closed)")
        return None
    token = lease_spawn()
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
        clip, _seed = E.run_beat(prompt, anchor_frame,
                                 output_prefix=ST.output_prefix(session, private))
        return ST.place_clip(session, private, clip)  # private: move out of shared output -> tmpfs
    except Exception as e:
        log(f"generation error ({e}) — likely preempted (SIGKILL); clip lost, loop yields")
        return None
    finally:
        lease_release(token)


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


def step(session, prompt, label):
    """One linear turn: gate the prompt (both paths), generate under lease, append a node."""
    gated = S.gate_prompt(prompt)
    if gated is None:
        raise SystemExit("prompt refused by red-line gate (B3)")
    private = ST.is_private(session)
    chain = load_chain(session)
    parent = chain["nodes"][-1]
    clip = generate_video(session, gated, parent["out_frame"])
    if clip is None:
        log("turn skipped (fail open) — chain unchanged")
        return None
    nid = parent["id"] + 1
    ref_name, abs_path = ST.frame_ref(session, private, f"{session}_n{nid}.png")
    out_frame = E.extract_last_frame(clip, ref_name, out_path=abs_path)  # store owns the path
    node = {"id": nid, "parent": parent["id"], "label": label, "prompt": gated,
            "seed": None, "clip": clip, "out_frame": out_frame}
    chain["nodes"].append(node)
    save_chain(session, chain)
    return node


def context_for(session):
    """Linear story-so-far from the chain (the labels along the single spine)."""
    chain = load_chain(session)
    labels = [n["label"] for n in chain["nodes"] if n["label"] not in (None, "opening")]
    cap = chain["nodes"][-1].get("caption")
    parts = ["Story so far: " + " -> ".join(labels) + "." if labels else "The dream is just beginning."]
    parts.append("On screen now: " + (cap or "the opening image."))
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
        ctx = context_for(args.session)
        print("context:", ctx)
        beats = propose(ctx)
        if not beats:
            print("  (no valid beats — type your own)")
        for i, bt in enumerate(beats):
            print(f"  [{i}] {bt['label']}  ::  {bt['prompt']}")
    elif args.cmd == "step":
        if args.prompt:
            prompt, label = args.prompt, args.label
        else:
            beats = propose(context_for(args.session))
            if not beats:
                raise SystemExit("no beats proposed; pass --prompt to type your own")
            chosen = beats[args.choose or 0]
            prompt, label = chosen["prompt"], chosen["label"]
        nd = step(args.session, prompt, label)
        print(f"-> node {nd['id']} clip={nd['clip']}" if nd else "-> turn skipped (fail open)")


if __name__ == "__main__":
    _main()

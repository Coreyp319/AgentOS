#!/usr/bin/env python3
"""ADR-0036 Phase 1 tests — D5 (freeze the generation intent as VALUES), I9 (clock seam), D3
(cross-lane anti-scheduler). Proves the drainers resolve the FROZEN intent (prompt + seed + engine),
not a mutable registry pointer, and that a LEGACY record (no `frozen`) still drains via the
module-constant fallback — byte-identical to the pre-D5 behavior for create-from-image. No GPU / no
lease / no ComfyUI: L.start/L.step/S.gate_prompt/E are stubbed so only the freeze-routing is exercised.

Run: python3 test_lucid_d5_freeze.py"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_linear as L
import lucid_engine as E
import lucid_safety as S
import lucid_queue as Q
import lucid_drain as D
import lucid_priv_drain as PD

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


def _png(spool, name="seed.png"):
    p = os.path.join(spool, name)
    with open(p, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")   # a present file; the drainer only checks os.path.isfile
    return p


# ============================ freeze_intent — the value capture ============================
fi = L.freeze_intent("a calm drift", quality="hero", seed=123)
check("freeze_intent captures the prompt verbatim", fi["prompt"] == "a calm drift")
check("freeze_intent honors an explicit seed", fi["seed"] == 123)
check("freeze_intent records the quality", fi["quality"] == "hero")
check("freeze_intent records engine + workflow + est as values",
      fi["engine"] in ("wan", "10eros") and bool(fi["workflow"]) and isinstance(fi["est_mib"], int))
check("freeze_intent mints a seed when none is given", isinstance(L.freeze_intent("p")["seed"], int))
check("freeze_intent is JSON-able (stored verbatim in the record)", json.dumps(fi) and True)


# ============================ D5 — the DURABLE drainer reads the frozen intent ============================
cap = {}
_real = (L.start, L.step, S.gate_prompt, E.current_engine, E.set_engine)


def _fake_start(session, snapshot, private=False, _trusted_seed=False, seed=None, **k):
    cap["seed"] = seed
    cap["private"] = private
    cap["trusted"] = _trusted_seed


def _fake_step(session, prompt, label, tier="batch", **k):
    cap["prompt"] = prompt
    cap["tier"] = tier
    return {"clip": "c.mp4", "out_frame": "f.png"}   # a produced node -> "done"


L.start, L.step, S.gate_prompt = _fake_start, _fake_step, (lambda p: p)   # identity gate isolates routing

# (a) a record WITH frozen intent -> the drainer uses the FROZEN prompt + seed, not the module constant
cap.clear()
with tempfile.TemporaryDirectory() as spool:
    rec = {"id": "shot_abc123", "snapshot": _png(spool),
           "frozen": {"v": 1, "prompt": "FROZEN custom prompt", "seed": 777,
                      "engine": E.current_engine(), "workflow": "wf.json", "quality": "draft", "est_mib": 17000}}
    out = D._governed_runner(rec)
    check("durable drain -> done on a produced node", out == "done")
    check("durable drain -> used the FROZEN prompt (not the MOTION_PROMPT constant)",
          cap.get("prompt") == "FROZEN custom prompt")
    check("durable drain -> used the FROZEN seed", cap.get("seed") == 777)
    check("durable drain -> _trusted_seed stays True (B2 cleared at enqueue)", cap.get("trusted") is True)
    check("durable drain -> best-effort tier so Interactive preempts", cap.get("tier") == "best-effort")

# (b) a LEGACY record (no frozen) -> falls back to the module constant + a minted seed (byte-identical)
cap.clear()
with tempfile.TemporaryDirectory() as spool:
    out = D._governed_runner({"id": "shot_legacy1", "snapshot": _png(spool)})   # NO frozen key
    check("legacy drain -> done", out == "done")
    check("legacy drain -> falls back to the MOTION_PROMPT constant", cap.get("prompt") == D.MOTION_PROMPT)
    check("legacy drain -> seed stays None so L.start mints (today's behavior)", cap.get("seed") is None)

# (c) the durable (ONESHOT) drainer PINS the frozen engine when it drifted from the live one
pins = []
E.set_engine = lambda name: pins.append(name)
E.current_engine = lambda: "wan"                       # live engine is wan...
with tempfile.TemporaryDirectory() as spool:
    D._governed_runner({"id": "shot_eng1", "snapshot": _png(spool),
                        "frozen": {"prompt": "p", "seed": 1, "engine": "10eros"}})   # ...admitted under 10eros
    check("durable drain -> pins the FROZEN engine when it drifted from live", pins == ["10eros"])
pins.clear()
with tempfile.TemporaryDirectory() as spool:
    D._governed_runner({"id": "shot_eng2", "snapshot": _png(spool),
                        "frozen": {"prompt": "p", "seed": 1, "engine": "wan"}})
    check("durable drain -> no engine pin when frozen == live", pins == [])

L.start, L.step, S.gate_prompt, E.current_engine, E.set_engine = _real   # restore everything


# ============================ D5 — the PRIVATE drainer uses frozen but does NOT pin the engine ============================
pcap = {}
_realp = (PD.L.start, PD.L.step, PD.S.gate_prompt, E.set_engine)


def _pstart(session, snapshot, private=False, _trusted_seed=False, seed=None, **k):
    pcap["seed"] = seed
    pcap["private"] = private


def _pstep(session, prompt, label, tier="batch", **k):
    pcap["prompt"] = prompt
    return {"clip": "c.mp4", "out_frame": "f.png"}


pinned = []
PD.L.start, PD.L.step, PD.S.gate_prompt = _pstart, _pstep, (lambda p: p)
E.set_engine = lambda n: pinned.append(n)              # spy: the private (in-process) drainer must NEVER call this
with tempfile.TemporaryDirectory() as spool:
    out = PD._governed_private_runner({"id": "shot_priv1", "snapshot": _png(spool),
                                       "frozen": {"prompt": "PRIV frozen", "seed": 555, "engine": "10eros"}})
    check("private drain -> done", out == "done")
    check("private drain -> used the frozen prompt", pcap.get("prompt") == "PRIV frozen")
    check("private drain -> used the frozen seed", pcap.get("seed") == 555)
    check("private drain -> renders to the private (sealed) sinks", pcap.get("private") is True)
    check("private drain -> does NOT pin the engine (in-process: no global mutation)", pinned == [])
PD.L.start, PD.L.step, PD.S.gate_prompt, E.set_engine = _realp   # restore


# ============================ I9 — durable writeback is replayable with an injected clock ============================
with tempfile.TemporaryDirectory() as spool:
    Q.enqueue("shot_clock1", "t", None, spool=spool, now=0.0)
    claimed = Q.claim(spool, "shot_clock1")
    st = Q.writeback(spool, claimed, "gpu-busy", now=100.0)
    held = json.load(open(os.path.join(spool, "shot_clock1.held.json")))
    check("I9 -> one failure under TTL re-files held", st == "held")
    check("I9 -> next_retry_after is computed off the INJECTED clock (>= 100)", held["next_retry_after"] >= 100.0)

with tempfile.TemporaryDirectory() as spool:
    Q.enqueue("shot_clock2", "t", None, spool=spool, now=0.0)
    claimed = Q.claim(spool, "shot_clock2")
    st = Q.writeback(spool, claimed, "gpu-busy", now=Q.DEFER_TTL_S + 1)   # an injected age past the deferral TTL
    check("I9 -> an injected age past DEFER_TTL_S expires deterministically (no wall-clock dependence)",
          st == "expired")

# a full drain_once TICK is replayable: the eligibility gate AND the writeback both honor injected now
D.R.write = lambda items: None                         # isolate the review.json sidecar side-effect
with tempfile.TemporaryDirectory() as spool:
    Q.enqueue("shot_tick1", "t", None, spool=spool, now=0.0)
    _fail = lambda rec: "gpu-busy"
    r1 = D.drain_once(spool, _fail, now=10.0)          # runs, fails -> re-held with a backoff off now=10
    check("I9 tick -> first fire ran the held job", r1 is not None and r1[0] == "shot_tick1")
    floor = json.load(open(os.path.join(spool, "shot_tick1.held.json")))["next_retry_after"]
    check("I9 tick -> backoff floor set off the INJECTED clock (> 10)", floor > 10.0)
    r2 = D.drain_once(spool, _fail, now=floor - 1.0)   # BEFORE the floor -> not eligible, nothing runs
    check("I9 tick -> a fire before the floor runs nothing (injected-clock eligibility)", r2 is None)
    r3 = D.drain_once(spool, _fail, now=floor + 1.0)   # AFTER the floor -> eligible again
    check("I9 tick -> a fire after the floor runs it again (replayable from the injected clock)", r3 is not None)


# ============================ D3 — FIFO is per-lane; no module merges two spool dirs ============================
with tempfile.TemporaryDirectory() as laneA, tempfile.TemporaryDirectory() as laneB:
    Q.enqueue("a1", "t", None, spool=laneA)
    Q.enqueue("a2", "t", None, spool=laneA)
    Q.enqueue("b1", "t", None, spool=laneB)
    a_ids = [r["id"] for r in Q.drain_order(Q.read_held(laneA))]
    b_ids = [r["id"] for r in Q.drain_order(Q.read_held(laneB))]
    check("D3 -> lane A drains its OWN arrival FIFO only", a_ids == ["a1", "a2"])
    check("D3 -> lane B is independent (no cross-lane interleave)", b_ids == ["b1"])
    check("D3 -> seq is PER-SPOOL (lane B's first item is seq 1, not a global 3)",
          Q.read_held(laneB)[0]["seq"] == 1)
    check("D3 -> the queue exposes NO two-spool merge/global-order function",
          not [n for n in dir(Q) if "merge" in n.lower() or "global_order" in n.lower()])


print(f"{ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)

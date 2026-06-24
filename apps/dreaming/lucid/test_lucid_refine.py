#!/usr/bin/env python3
"""Unit tests for prompt refine (the Start "Refine" button backend). No model — the Ollama call is
injected via `_call`. Proves the contract + the input/output red-line gating. Run: python3 test_lucid_refine.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_refine as R

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


# happy path: a clean idea -> a refined, gated prompt
r = R.refine("aurora over hills", _call=lambda s, u: "A slow aurora ripples over dark rolling hills under a calm night sky.")
check("clean idea -> ok + refined text", r["ok"] and "aurora" in r["refined"].lower())
check("refined is trimmed of quotes", '"' not in r.get("refined", ""))

# empty input -> honest refusal, no model call
called = {"n": 0}
r = R.refine("   ", _call=lambda s, u: (called.__setitem__("n", called["n"] + 1), "x")[1])
check("empty input -> not ok", not r["ok"] and "reason" in r)
check("empty input -> no model call", called["n"] == 0)

# model error -> honest fail-open reason
r = R.refine("a quiet forest", _call=lambda s, u: (_ for _ in ()).throw(RuntimeError("ollama down")))
check("model error -> not ok with reason", not r["ok"] and "reason" in r)

# empty model output -> not ok
r = R.refine("a quiet forest", _call=lambda s, u: "   ")
check("empty model output -> not ok", not r["ok"])

# overlong input is truncated, not rejected outright
r = R.refine("calm sea " * 200, _call=lambda s, u: "A calm sea at dawn, gentle swell catching first light.")
check("overlong input still refines (truncated)", r["ok"])

# output is length-capped
long_out = "a vivid filmable scene, " * 50
r = R.refine("a scene", _call=lambda s, u: long_out)
check("refined output capped to MAX_OUT", r["ok"] and len(r["refined"]) <= R.MAX_OUT)

print(f"lucid_refine: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)

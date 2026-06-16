#!/usr/bin/env python3
"""Unit tests for the B2 seed guard disposition (ADR-0017). The model PROPOSES (mocked here),
code DISPOSES — so the safety-critical decision is provable with no vision model. Run:
python3 test_lucid_b2.py"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_b2 as B2

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


# --- dispose(): the deterministic gate over the model's proposed flags ---
v = B2.dispose({"has_face": False, "real_person": False, "possibly_minor": False, "desc": "a landscape"})
check("no face -> allow", v.ok and not v.requires_consent)

v = B2.dispose({"has_face": True, "real_person": False, "possibly_minor": False, "desc": "a cartoon"})
check("drawn/cartoon face -> allow (not a real person)", v.ok)

v = B2.dispose({"has_face": True, "real_person": True, "possibly_minor": False, "desc": "a man"})
check("real person -> BLOCK by default", not v.ok)
check("real person -> overridable with consent", v.requires_consent)

v = B2.dispose({"has_face": True, "real_person": True, "possibly_minor": True, "desc": "a child"})
check("possible minor -> BLOCK", not v.ok)
check("possible minor -> NOT overridable (hard red-line)", not v.requires_consent)

v = B2.dispose({"has_face": False, "real_person": False, "possibly_minor": True, "desc": "unsure"})
check("minor-flag alone -> BLOCK hard (fail-closed on age)", not v.ok and not v.requires_consent)

# --- malformed model output -> fail-closed BLOCK ---
check("non-dict classify -> block, not checked", (lambda r: not r.ok and r.flags.get("checked") is False)(B2.dispose("garbage")))
check("None classify -> block", not B2.dispose(None).ok)

# --- keyless / refusal-shaped JSON must BLOCK, not default-allow (responsible-ai fix) ---
check("empty dict -> block (no default-allow)", not B2.dispose({}).ok and B2.dispose({}).flags.get("checked") is False)
check("missing a key -> block", not B2.dispose({"has_face": True, "real_person": True}).ok)
check("non-bool value -> block", not B2.dispose({"has_face": "yes", "real_person": True, "possibly_minor": False}).ok)

# --- check_seed with an injected classifier (no live model) ---
real = B2.check_seed("/nonexistent.png", _call=lambda p: {"has_face": True, "real_person": True, "possibly_minor": False})
check("check_seed real person -> needs consent", (not real.ok) and real.requires_consent)

clean = B2.check_seed("/nonexistent.png", _call=lambda p: {"has_face": False, "real_person": False, "possibly_minor": False})
check("check_seed clean -> ok", clean.ok)

def _boom(_):
    raise RuntimeError("model down")
down = B2.check_seed("/nonexistent.png", _call=_boom)
check("check_seed model-down -> fail-closed block", not down.ok and not down.requires_consent)
check("check_seed model-down -> reason mentions refused", "refused" in down.reason.lower())

# --- as_dict round-trips the flags for the web ---
d = real.as_dict()
check("as_dict carries ok/requires_consent/reason", set(["ok", "requires_consent", "reason"]) <= set(d))

print(f"lucid_b2: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)

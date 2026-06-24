#!/usr/bin/env python3
"""Unit tests for anticipatory model residency (ADR-0045) — the on-app-open pre-warm of the narrator +
VLM, and the matching B1 evict-both guard. No model, GPU, or daemon: the preload contract + the
fail-open behavior are provable in isolation. Run: python3 test_lucid_prewarm.py"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lucid_engine as E   # noqa: E402
import lucid_linear as L   # noqa: E402

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


# --- prewarm_models: WHAT gets warmed --------------------------------------------------------------
calls = []
got = E.prewarm_models(_warm=lambda m, k: calls.append((m, k)))
check("default warms the narrator (big) FIRST, then the VLM (avoids load-time eviction)",
      [m for m, _ in calls] == [E.NARRATOR_MODEL, E.MODEL])
check("returns the list it warmed", got == [E.NARRATOR_MODEL, E.MODEL])
check("uses the configured keep-alive (bounded, not -1)",
      all(k == E.PREWARM_KEEP_ALIVE for _, k in calls) and E.PREWARM_KEEP_ALIVE != -1)

# de-dup: narrator == VLM (the LUCID_NARRATOR_MODEL=qwen fold-back) → exactly one warm
calls2 = []
check("dedups when narrator == VLM",
      E.prewarm_models(models=["qwen2.5vl:3b", "qwen2.5vl:3b"], _warm=lambda m, k: calls2.append(m))
      == ["qwen2.5vl:3b"] and calls2 == ["qwen2.5vl:3b"])

# explicit keep-alive override is threaded through
calls3 = []
E.prewarm_models(models=["m1"], keep_alive="30s", _warm=lambda m, k: calls3.append((m, k)))
check("keep_alive override is passed to the warm", calls3 == [("m1", "30s")])

# falsy / empty model ids are filtered out
check("filters empty model ids",
      E.prewarm_models(models=["", None, "real"], _warm=lambda m, k: None) == ["real"])

# --- kill-switch: LUCID_PREWARM=0 -> no-op ---------------------------------------------------------
_saved = E.PREWARM
E.PREWARM = False
hit = []
check("kill-switch off → warms nothing, returns []",
      E.prewarm_models(_warm=lambda m, k: hit.append(m)) == [] and hit == [])
E.PREWARM = _saved

# --- fail-open: a warm that raises never propagates, and never stops the others -------------------
seen = []


def _boom(m, k):
    seen.append(m)
    raise RuntimeError("ollama unreachable")


try:
    res = E.prewarm_models(models=["a", "b"], _warm=_boom)
    raised = False
except Exception:
    raised = True
check("a failing warm does not raise (fail-open)", raised is False)
check("fail-open still attempts every model", seen == ["a", "b"])

# --- _preload_one: WEIGHTS-ONLY (no prompt → no tokens, no content) -------------------------------
captured = {}


class _Resp:
    def read(self):
        return b""


def _fake_urlopen(req, timeout=None):
    captured["url"] = req.full_url
    captured["body"] = json.loads(req.data.decode())
    return _Resp()


_real_urlopen = urllib.request.urlopen
urllib.request.urlopen = _fake_urlopen
try:
    E._preload_one("qwen2.5vl:3b", "4m")
finally:
    urllib.request.urlopen = _real_urlopen
check("preload posts to /api/generate", captured.get("url", "").endswith("/api/generate"))
check("preload sends NO prompt and NO messages (weights-only)",
      "prompt" not in captured.get("body", {}) and "messages" not in captured.get("body", {}))
check("preload pins with the given keep_alive", captured.get("body", {}).get("keep_alive") == "4m")
check("preload names the model", captured.get("body", {}).get("model") == "qwen2.5vl:3b")
check("preload pins num_ctx == NUM_CTX (so the warm is reused, not reloaded by the real call)",
      captured.get("body", {}).get("options", {}).get("num_ctx") == E.NUM_CTX)

# --- B1 evict guard: BOTH the VLM and the narrator leave VRAM before the i2v lease ----------------
_m, _n = E.MODEL, E.NARRATOR_MODEL
E.MODEL, E.NARRATOR_MODEL = "vlm:x", "narr:y"
check("evict targets = both models, order preserved", L._evict_targets() == ["vlm:x", "narr:y"])
E.MODEL = E.NARRATOR_MODEL = "same:z"
check("evict targets dedups when narrator == VLM (one evict)", L._evict_targets() == ["same:z"])
E.MODEL, E.NARRATOR_MODEL = _m, _n

# --- report ---
print(f"lucid_prewarm: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)

#!/usr/bin/env python3
"""Lucid prompt refine — turn a user's rough idea into one vivid, filmable opening prompt.

Backs the Start surface's "Refine" button: the human types a loose initial prompt, hits Refine, and
the local narrator model (Ollama — the same one that proposes beats) rewrites it into a concrete,
SFW, single-shot description they can then EDIT and send. Model PROPOSES; the deterministic red-line
gate (lucid_safety.gate_prompt) DISPOSES — both the input idea and the refined output pass the gate,
so refine can never yield a prompt Lucid would block. Fails honestly: on a model error or a blocked
result it returns a plain reason, never a silent or unsafe string.

── Backend contract (for the Start-surface owner in the React app / lucid_web.py) ───────────────
    POST /api/refine  {"text": "<rough idea>"}
      → {"ok": true,  "refined": "<polished one-shot prompt>"}     # fill it back, still editable
      → {"ok": false, "reason": "<honest, calm message>"}          # show inline; keep their text

    import lucid_refine as R ;  R.refine(text)  -> the same dict (no GPU; Ollama text model only)
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_safety as S   # the deterministic red-line gate (model proposes, code disposes)

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL = os.environ.get("LUCID_REFINE_MODEL", os.environ.get("LUCID_MODEL", "gemma4:latest"))
MAX_IN = 600      # a rough idea, not an essay
MAX_OUT = 320     # one or two filmable sentences

_SYS = (
    "You rewrite a person's rough idea into ONE vivid, concrete opening shot for a short video. "
    "Keep it SFW and filmable: name the subject, the setting, the quality of light, and a sense of "
    "gentle motion. One or two sentences, present tense. No preamble, no quotes, no lists — return "
    "ONLY the rewritten prompt. Never depict a real identifiable person, a minor, or anything "
    "explicit or violent."
)


def refine(text, _call=None):
    """Refine a rough idea into a filmable prompt. Returns {"ok",...}. `_call(system,user)->str` is
    injectable so the contract is testable without a model."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "Type an idea first."}
    text = text[:MAX_IN]
    # Gate the INPUT too — a red-lined idea is refused before we spend a model call on it.
    if S.gate_prompt(text) is None:
        return {"ok": False, "reason": "That idea isn't something Lucid can make. Try another."}
    try:
        raw = _call(_SYS, text) if _call else _chat(_SYS, text)
    except Exception:
        return {"ok": False, "reason": "Couldn't reach the writer just now — type your own, or try again."}
    out = (raw or "").strip().strip('"').strip()[:MAX_OUT]
    if not out:
        return {"ok": False, "reason": "No suggestion came back — type your own."}
    # Gate the OUTPUT — a refine can NEVER hand back a prompt the red-line would block.
    if S.gate_prompt(out) is None:
        return {"ok": False, "reason": "Couldn't refine that safely — try rephrasing."}
    return {"ok": True, "refined": out}


def _chat(system, user):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False, "keep_alive": 0, "options": {"temperature": 0.7},
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["message"]["content"]


if __name__ == "__main__":   # manual: python3 lucid_refine.py "a calm aurora over dark hills"
    print(json.dumps(refine(" ".join(sys.argv[1:]) or "a calm aurora over dark hills"), indent=2))

#!/usr/bin/env python3
"""Lucid MVP safety gates (ADR-0015 §4, §3) — deterministic, model-external, fail-closed.

"Model proposes, code disposes." Every prompt that can parameterize a GPU workflow — whether the
LLM proposed it or the user typed it — must pass through THIS module first. None of these functions
calls a generating model; they are pure/total and unit-testable without a model, GPU, or daemon
(the feed.rs precedent: pure mapping + tests, ADR-0001 discipline).

Gates:
  validate_beats(raw_json_text, n)  -> list[{"label","prompt"}]   (B3 schema; total, never raises)
  red_line_ok(text)                 -> bool                        (B3/B2-text; fail-closed filter)
  gate_prompt(text)                 -> str | None                  (the single chokepoint, both paths)
  confirm_evicted(model, ...)       -> bool                        (B1 eviction-confirm, no keep_alive hope)

The red-line TERM LIST is a conservative starting set; the real content (and a classifier) is owed
to responsible-ai-privacy-skeptic + security-reviewer (ADR-0015 "still owed"). What is NOT owed and
is fixed here: the red-line is CODE, external to the LLM, applied to BOTH paths, fail-closed.
"""
import json
import re
import urllib.request

LABEL_MAX = 40
PROMPT_MAX = 400

# --- B3: structured-output schema (total validator) ----------------------------------------------
def validate_beats(raw, n=4):
    """Parse + schema-validate the beat-gen response. Returns a list of clean, red-line-passed
    beats (≤ n). ANY structural failure -> [] (caller degrades to type-your-own). Never raises."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    beats = data.get("beats")
    if not isinstance(beats, list):
        return []
    out = []
    for item in beats:
        if not isinstance(item, dict):
            continue  # schema drift: a string/number where an object was required
        label, prompt = item.get("label"), item.get("prompt")
        if not isinstance(label, str) or not isinstance(prompt, str):
            continue  # {"prompt": 42} etc. — reject, do not coerce
        label, prompt = label.strip()[:LABEL_MAX], prompt.strip()[:PROMPT_MAX]
        if not label or not prompt:
            continue
        if not (red_line_ok(label) and red_line_ok(prompt)):
            continue  # drop a violating beat (fail-closed) rather than display/run it
        out.append({"label": label, "prompt": prompt})
        if len(out) >= n:
            break
    return out


# --- B3/B2(text): the deterministic red-line + injection filter ----------------------------------
# Conservative deny list (OWED: real content/classifier -> responsible-ai + security). Word-boundary
# matched, case-insensitive. Two categories the SKILL hard-constraints name: minors, real-person
# likeness. Plus a minimal prompt-injection strip (the LLM output / user text must not carry
# instructions that re-open the gate).
_REDLINE_TERMS = [
    # minors
    r"child", r"children", r"kid", r"kids", r"minor", r"minors", r"teen", r"teens",
    r"teenager", r"underage", r"toddler", r"infant", r"baby", r"babies", r"preteen",
    r"schoolgirl", r"schoolboy", r"loli", r"shota",
    # non-consensual real-person likeness (text cues; the IMAGE vector is B2, owed)
    r"deepfake", r"revenge porn", r"nonconsensual", r"non-consensual",
]
_REDLINE = re.compile(r"(?i)\b(" + "|".join(_REDLINE_TERMS) + r")\b")
# prompt-injection markers we refuse to pass through into a workflow prompt
_INJECTION = re.compile(r"(?i)(ignore (all |the |your )?(previous|prior|above) (instructions|rules)"
                        r"|disregard (the )?(red.?line|safety|system)"
                        r"|system\s*:|<\s*/?\s*(system|s)\s*>)")


def red_line_ok(text):
    """True iff `text` is safe to let reach a GPU workflow. Fail-closed: non-str, a red-line term,
    or an injection marker -> False."""
    if not isinstance(text, str) or not text.strip():
        return False
    if _REDLINE.search(text):
        return False
    if _INJECTION.search(text):
        return False
    return True


def gate_prompt(text):
    """The single chokepoint for BOTH the chosen-beat and the type-your-own paths (ADR-0015 §4).
    Returns the sanitized prompt, or None if it must be refused. Apply this in the engine right
    before _set_widgets — no prompt reaches a workflow without passing here."""
    if not isinstance(text, str):
        return None
    text = text.strip()[:PROMPT_MAX]
    return text if red_line_ok(text) else None


# --- B1: confirm the beat model is evicted BEFORE the video lease (keep_alive:0 is only a hint) ---
def confirm_evicted(model, ollama_host="http://127.0.0.1:11434", timeout=30, poll=1.0,
                    _now=None, _sleep=None):
    """Poll Ollama /api/ps until `model` is absent from the resident set, or `timeout` s elapse.
    Returns True iff confirmed evicted. The video step must NOT acquire VRAM until this is True
    (ADR-0015 §3). `_now`/`_sleep` are injectable for tests (no real clock)."""
    import time as _t
    now = _now or _t.monotonic
    sleep = _sleep or _t.sleep
    deadline = now() + timeout
    while True:
        resident = _resident_models(ollama_host)
        # fail-closed: only confirm eviction on a KNOWN-good residency set that lacks the model.
        # An unreachable Ollama (resident is None) is treated as "still possibly resident" -> we
        # keep polling and ultimately return False rather than admit the video on unknown state.
        if resident is not None and model not in resident:
            return True
        if now() >= deadline:
            return False
        sleep(poll)


def _resident_models(ollama_host):
    """Set of resident model names, or None if Ollama can't be reached (unknown residency)."""
    try:
        with urllib.request.urlopen(ollama_host + "/api/ps", timeout=3) as r:
            data = json.load(r)
        return {m.get("name") for m in data.get("models", [])}
    except Exception:
        return None

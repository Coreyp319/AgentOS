#!/usr/bin/env python3
"""Lucid B2 — the seed-image likeness guard (ADR-0015 B2, ADR-0017).

The responsible-ai council BLOCKER: lucid is image-to-video, so a seed photo of a real person
animates that person — the deepfake/NCII mechanism. This guard inspects a user-supplied seed
BEFORE it can become a dream.

Model PROPOSES, code DISPOSES: a local vision model (Ollama, no new dependency) classifies the
image; a deterministic, fail-closed rule HERE decides block / needs-consent / allow. The vision
model runs a different task than the narrator and grades EXTERNAL input (the user's image), not
its own output — so it is not "grading its own homework."

Disposition (fail-closed):
  possibly_minor                  -> BLOCK, hard, never overridable.
  real photographed person (face) -> BLOCK by default; allowed only with explicit per-seed consent
                                     ("this is me / I have the right to use this image").
  classify failed / unparseable / model down -> BLOCK ("can't verify => refuse").
  no face, or a drawing/render/abstract -> allow.

OWED (ADR-0017): a deterministic CV detector (opencv/mediapipe) as the primary gate — the VLM is
the no-new-dependency starting gate, not the last word. Adult-tone + real person stays hard-refused
once the NSFW opt-in exists (never operator-waivable for a third party).
"""
import base64
import hashlib
import json
import os
import urllib.request

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
# A vision-capable model. Default to gemma4 (smallest vision model on this box); override to a
# dedicated detector when one is installed. NOT used for narration's output — external input only.
B2_MODEL = os.environ.get("LUCID_B2_MODEL", "gemma4:latest")

_PROMPT = (
    "You are an image safety classifier for a video generator. Inspect the image and return ONLY "
    'JSON: {"has_face": bool, "real_person": bool (a real PHOTOGRAPHED human, not a drawing / '
    '3D render / painting / abstract), "possibly_minor": bool, "desc": "<=8 words"}. '
    "If you are unsure whether a depicted person could be under 18, set possibly_minor true."
)


class Verdict:
    def __init__(self, ok, requires_consent, reason, flags):
        self.ok = ok
        self.requires_consent = requires_consent
        self.reason = reason
        self.flags = flags

    def as_dict(self):
        return {"ok": self.ok, "requires_consent": self.requires_consent,
                "reason": self.reason, **self.flags}


def _classify(path, _call=None):
    """Ask the vision model. `_call` is injectable for tests (no live model)."""
    if _call is not None:
        return _call(path)
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    body = json.dumps({
        "model": B2_MODEL,
        "messages": [{"role": "user", "content": _PROMPT, "images": [b64]}],
        "stream": False, "format": "json", "keep_alive": 0,
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(json.load(r)["message"]["content"])


def dispose(c):
    """The deterministic gate over the model's proposed flags (pure; unit-testable)."""
    if not isinstance(c, dict):
        return Verdict(False, False, "couldn't read the safety check — refused (fail-closed).", {"checked": False})
    # The classifier must actually answer all three booleans. A keyless / refusal-shaped JSON
    # (e.g. {} or a hedge) must BLOCK, not default-allow via .get() falsiness (responsible-ai review).
    if not all(isinstance(c.get(k), bool) for k in ("has_face", "real_person", "possibly_minor")):
        return Verdict(False, False,
                       "the safety check didn't return a clear result — refused (fail-closed).",
                       {"checked": False})
    has_face = c["has_face"]
    real = c["real_person"]
    minor = c["possibly_minor"]
    flags = {"has_face": has_face, "real_person": real, "possibly_minor": minor,
             "desc": str(c.get("desc", ""))[:60], "checked": True}
    if minor:
        return Verdict(False, False,
                       "Blocked: the image may depict a minor. This is a hard red-line and cannot be overridden.",
                       flags)
    if has_face and real:
        return Verdict(False, True,
                       "This looks like a real person. To continue, confirm you are this person or have the "
                       "right to use this image.", flags)
    return Verdict(True, False, "ok", flags)


_cache = {}  # content-hash -> Verdict, so a consent re-submit of the same image doesn't re-run the VLM


def check_seed(path, _call=None):
    """Inspect a user seed image. Returns a Verdict, fail-closed on any error/uncertainty.
    Memoized by image content so the consent round-trip doesn't pay the model twice."""
    h = None
    if _call is None:
        try:
            h = hashlib.sha256(open(path, "rb").read()).hexdigest()
        except Exception:
            h = None
    if h and h in _cache:
        return _cache[h]
    try:
        c = _classify(path, _call=_call)
    except Exception as e:
        return Verdict(False, False, f"Couldn't verify the image safely ({e}) — refused.", {"checked": False})
    v = dispose(c)
    if h:
        if len(_cache) > 32:
            _cache.clear()
        _cache[h] = v
    return v

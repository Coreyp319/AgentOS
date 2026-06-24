#!/usr/bin/env python3
"""ADR-0037 — the deterministic cores of Lucid grounding gates (L0 canon ledger + L2 palette gate).

Promoted from the gating spike (`spike_canon_ledger.py`, 22/22 selftests + measured GO on the hybrid
split). "Model proposes, code disposes" applied to the dream's narrative memory:

  L0 CANON LEDGER (hybrid — each fact handled by what's measurably better at it):
    • code-disposed extractors for time_of_day / mood   (regex tracked changes 1.00 in the spike)
    • the LLM delta for subjects / entrances / synopsis  (only a model does who/what/story; code = 0.00)
    • place = best-effort, ungated                        (unreliable on both a 3B and a regex)
    • all merged by the deterministic `merge_canon` dispose (caps, append-only subjects, evidence- and
      caption-grounding, token-subset dedup, coercion, empty/garbled -> fail-open keep-prior)

  L2 PALETTE GATE (flag-only): zero-install cv2 histogram correlation between two frames, shelling the
    ComfyUI venv exactly like `lucid_facecv.py` — reads paths, returns a number, writes NO file, and is
    fail-closed-None (a detector that can't run reports "couldn't measure", never "steady").

NOT wired into the live server (`lucid_linear`) yet — that is gated on the on-box `--full --runs 20`
gate AND the `/api/state` private-canon egress decision (ADR-0037 §Open/owed). This module is the pure,
reversible building block; integration is a later, reviewed step.

`merge_canon` is the function the ADR pins; `test_lucid_ground_canon.py` is its spec. Fail-open for
STEERING (a bad ledger degrades to today's label-chain context); the red line stays the only fail-closed
safety authority (lucid_safety.gate_prompt), unaffected.
"""
import copy
import json
import os
import re
import subprocess

import lucid_safety as S   # red_line_ok — a model-written ledger string is untrusted text too

# ---------------- L0 contract (caps + the delta schema the model returns) ----------------
CAP_SUBJECTS = 4
CAP_PROPS = 8
SYN_CAP = 600                                    # synopsis hard cap (keep-tail) when accumulating
SINGLE_KEYS = ("place", "time_of_day", "mood")   # single-valued, replace-evidence-gated
LIST_KEYS = ("subjects", "props")                # append-dedup-cap
CODE_KEYS = ("time_of_day", "mood")              # the hybrid boundary: code owns these, not the LLM

# All-empty shape (a 3B ECHOES literal example values as data — the spike proved it). Field meanings live
# in the prose prompts below, never as fillable examples.
_SCHEMA = '{"add_subjects":[],"add_props":[],"set":{},"evidence":"","synopsis_suffix":"","drift_note":""}'

_SYS_DELTA = (
    "You keep a tiny CANON for a silent dream video — the durable facts the story must not forget. "
    "You are given the canon SO FAR, the beat the viewer just chose, and a one-line caption of what is "
    "now on screen. Reply with ONLY a small JSON delta of what CHANGED this beat — never restate facts "
    "that are unchanged. Fill it ONLY with words from THIS beat's caption; copy NOTHING from these "
    "instructions.\n"
    "FIELDS (all optional; leave a field empty when nothing applies):\n"
    "• add_subjects — list; ONLY a genuinely NEW CHARACTER (a person or animal) that just ENTERED on "
    "screen. NEVER weather, light, sky, water, plants, or scenery — those go in add_props or are the "
    "place. Never re-list someone already in the canon; never use it to restate or 'change' an existing "
    "subject (their identity is fixed).\n"
    "• add_props — list; notable things that newly APPEAR on screen.\n"
    "• set — an object of the single-valued facts that just TURNED: place (omit time_of_day and mood — "
    "those are handled elsewhere). Omit a key that did not change.\n"
    "• evidence — the exact words from the caption that prove the `set`. A `set` with no caption "
    "evidence is ignored.\n"
    "• synopsis_suffix — at most ONE short clause (<=120 chars) advancing the story, or empty.\n"
    "• drift_note — at most a short phrase if something looks inconsistent, or empty.\n"
    "If NOTHING durable changed this beat, return every field empty. Output ONLY the JSON object, "
    "with these exact keys and no others: " + _SCHEMA
)
_SYS_SEED = (
    "You START a tiny CANON for a silent dream video from its OPENING shot. Read the one-line caption "
    "and list ALL the durable facts you can see, using ONLY words from the caption.\n"
    "• add_subjects — every CHARACTER (person or animal) present; NEVER weather, light, plants, or "
    "scenery (those are add_props or the place).\n"
    "• add_props — the notable objects present.\n"
    "• set — the place if the caption states it (omit time_of_day and mood — handled elsewhere).\n"
    "• evidence — the caption itself (it is the proof).\n"
    "• synopsis_suffix — one short clause naming the scene.\n"
    "Output ONLY the JSON object, these exact keys and no others: " + _SCHEMA
)

# Deterministic code-half of the hybrid: time_of_day + mood are extracted by keyword (last match wins —
# a cut updates them), which beat the 3B's own change-tracking in the spike (1.00 vs 0.83) for free.
_TIME_WORDS = ["dawn", "sunrise", "morning", "midday", "noon", "afternoon", "dusk", "sunset",
               "twilight", "evening", "nightfall", "midnight", "night", "dark"]
_MOOD_WORDS = ["calm", "serene", "peaceful", "tense", "dread", "ominous", "joyful", "wonder",
               "melancholy", "eerie", "menacing", "hopeful", "lonely", "frantic"]


def empty_canon():
    return {"synopsis": "", "facts": {"subjects": [], "place": None, "time_of_day": None,
                                      "mood": None, "props": []}}


def _clean_str(x, cap=120):
    """A model string that survives the red line, trimmed/capped; else None."""
    if not isinstance(x, str):
        return None
    x = x.strip().rstrip(".").strip()[:cap]
    if not x:
        return None
    return x if S.red_line_ok(x) else None


def _supported(value, caption_union, ratio=0.6):
    """Supported if >= `ratio` of the value's significant tokens appear in the caption union — majority
    overlap, so a hallucinated CLAUSE sharing one common word fails the grounding guard. 0.6 (not 0.5) so a
    2-of-4 garbled compound ('notable-thall-sailing-ship') is rejected (ADR-0037 on-box smoke 2026-06-22)."""
    if not value:
        return True
    toks = [t for t in re.findall(r"[a-z]+", value.casefold()) if len(t) > 3]
    if not toks:
        return True
    return (sum(1 for t in toks if t in caption_union) / len(toks)) >= ratio


_STOP = {"a", "an", "the", "his", "her", "its", "their", "of", "on", "in", "at"}


def _content_toks(s):
    """Significant tokens of a phrase, articles/preps stripped — so "a keeper" and "the keeper" both
    reduce to {keeper} and dedup against "lighthouse keeper" ({lighthouse, keeper})."""
    return {t for t in re.findall(r"[a-z]+", s.casefold()) if t not in _STOP}


# A subject is a CHARACTER (who). A vision model (qwen2.5vl) mislabels caption-grounded weather/light/
# water/plant nouns as subjects — they pass the grounding guard (they ARE in the caption) and then squat
# the 4-subject cap, STARVING a real character (the ADR-0037 on-box smoke 2026-06-22 dropped "cat" because
# fog/mist/clouds filled the slots). Code disposes: such a scenery phrase is REROUTED to props (a "thing",
# the ADR's own taxonomy), not dropped — so an entrance like "vine" is still remembered, just not as "who".
_SCENERY_NOUNS = {
    "fog", "mist", "cloud", "rain", "snow", "wind", "smoke", "haze", "storm", "breeze", "drizzle",
    "frost", "dew", "vapor", "vapour", "steam", "gust", "downpour",                       # weather/air
    "sun", "moon", "star", "light", "beam", "ray", "shadow", "sky", "sunlight", "moonlight",
    "glow", "glare", "darkness", "gloom",                                                 # light/sky
    "sea", "ocean", "wave", "water", "river", "stream", "fire", "flame", "spark", "ember",
    "tide", "surf", "spray", "foam",                                                      # water/fire
    "vine", "leaf", "leaves", "flower", "petal", "branch", "root", "moss", "grass", "fern",
    "blossom", "bud", "bloom", "ivy", "weed", "frond",                                    # plants
}
# Modifiers a scenery phrase may carry and still be wholly scenery ("green vine", "grey clouds", "thick fog").
_SCENERY_MODIFIERS = {
    "grey", "gray", "green", "dark", "white", "black", "red", "blue", "golden", "gold", "silver",
    "pale", "brown", "yellow", "orange", "purple", "crimson", "azure", "emerald", "amber", "ashen", "rosy",
    "thick", "thin", "soft", "bright", "dim", "faint", "heavy", "gentle", "cold", "warm", "wet", "damp",
    "dry", "distant", "swirling", "drifting", "rolling", "gathering", "rising", "falling", "low", "high",
}


def _is_scenery_subject(phrase):
    """True only when the WHOLE phrase is scenery (a scenery noun + optional modifiers) — so "green vine"/
    "grey clouds"/"fog" reroute, but "grey cat"/"sea turtle"/"lighthouse keeper"/"keeper of the light"
    stay subjects (a non-scenery content token anywhere keeps it a character). De-pluralized head match."""
    toks = [t for t in re.findall(r"[a-z]+", phrase.casefold()) if t not in _STOP]
    if not toks:
        return False
    def _scenery(t):
        return t in _SCENERY_NOUNS or t.rstrip("s") in _SCENERY_NOUNS
    return any(_scenery(t) for t in toks) and all(_scenery(t) or t in _SCENERY_MODIFIERS for t in toks)


def _is_modifier_only(phrase):
    """A candidate with no substantive noun — only adjectives/modifiers ('thick', 'grey') — names nothing
    and is never a valid subject or prop. (hermes3 leaked the lone adjective 'thick', split off 'thick
    fog', as a subject in the on-box smoke 2026-06-22.)"""
    toks = [t for t in re.findall(r"[a-z]+", phrase.casefold()) if t not in _STOP]
    return bool(toks) and all(t in _SCENERY_MODIFIERS for t in toks)


def _coerce_list(v):
    """A 3B emits a list-field as a bare string ('lantern, rope') or scalar; split a non-empty string on
    commas/semicolons. Anything else -> []."""
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        return [p.strip() for p in re.split(r"[;,]", v) if p.strip()]
    return []


def _coerce_set(v, rej):
    """`set` as a dict (good), or a 'key: value' string (parse it), or a bare key (un-recoverable)."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and ":" in v:
        k, _, val = v.partition(":")
        val = val.strip().strip("'\"").strip()
        if k.strip() and val:
            return {k.strip(): val}
        rej.append("set:string-no-value")
        return {}
    if v is not None:
        rej.append("set:not-a-dict")
    return {}


def merge_canon(prior, delta, *, accumulate_synopsis=True, evidence_text=None):
    """Pure, deterministic DISPOSE. Returns (new_canon, rejections). NEVER raises; any malformed input
    degrades to `prior` (fail-open steering). `evidence_text` (the caption) enables the caption-grounding
    guard — a fact whose value has no majority-token in the caption is dropped (None = guard off)."""
    new = copy.deepcopy(prior)
    rej = []
    if not isinstance(delta, dict):
        return new, ["delta:not-a-dict"]

    if accumulate_synopsis:
        suf = _clean_str(delta.get("synopsis_suffix"), cap=120)
        if suf:
            joined = (new["synopsis"] + " " + suf).strip() if new["synopsis"] else suf
            new["synopsis"] = joined[-SYN_CAP:].lstrip()       # keep the tail (recent matters most)

    sub_in = _coerce_list(delta.get("add_subjects")) if "add_subjects" in delta else []
    prop_in = _coerce_list(delta.get("add_props")) if "add_props" in delta else []
    if "add_subjects" in delta and not sub_in and delta.get("add_subjects") not in (None, "", []):
        rej.append("add_subjects:uncoercible")
    if "add_props" in delta and not prop_in and delta.get("add_props") not in (None, "", []):
        rej.append("add_props:uncoercible")
    # reroute scenery the model mislabeled as a subject -> props (frees the subject cap for real characters)
    keep_subj, rerouted = [], []
    for s in sub_in:
        (rerouted if isinstance(s, str) and _is_scenery_subject(s) else keep_subj).append(s)
    if rerouted:
        rej.append("add_subjects:scenery->props")
        prop_in = prop_in + rerouted

    for dkey, fkey, vals, cap in (("add_subjects", "subjects", keep_subj, CAP_SUBJECTS),
                                  ("add_props", "props", prop_in, CAP_PROPS)):
        for v in vals:
            cv = _clean_str(v, cap=60)
            if not cv:
                rej.append(f"{dkey}:dropped-string")
                continue
            if len(cv.split()) > 5:                            # a subject/prop is a phrase, not a clause
                rej.append(f"{dkey}:too-long")
                continue
            if _is_modifier_only(cv):                          # a bare adjective ('thick') names nothing
                rej.append(f"{dkey}:modifier-only")
                continue
            if evidence_text is not None and not _supported(cv, evidence_text.casefold()):
                rej.append(f"{dkey}:ungrounded")               # caption-grounding hallucination guard
                continue
            cvt = _content_toks(cv)                             # token-subset dedup (articles stripped):
            if any(cvt and (cvt <= _content_toks(e) or _content_toks(e) <= cvt)       # "a keeper" ⊆
                   for e in new["facts"][fkey]):                                       # "lighthouse keeper"
                continue                                       # -> dup; "cat" vs "cathedral" -> kept
            if len(new["facts"][fkey]) >= cap:
                rej.append(f"{dkey}:over-cap")                 # drop NEW, never evict stable
                continue
            new["facts"][fkey].append(cv)

    setd = _coerce_set(delta.get("set"), rej)
    ev = _clean_str(delta.get("evidence"), cap=120)
    for key, val in setd.items():
        if key in LIST_KEYS:                                   # subjects/props are append-only
            rej.append(f"set.{key}:list-key-not-settable")
            continue
        if key not in SINGLE_KEYS:
            rej.append(f"set.{key}:unknown-key")
            continue
        cv = _clean_str(val, cap=60)
        if not cv:
            rej.append(f"set.{key}:no-value")
            continue
        if evidence_text is not None:                          # live: the value must be IN the caption
            if not _supported(cv, evidence_text.casefold()):
                rej.append(f"set.{key}:ungrounded")
                continue
        elif not ev:                                           # offline: fall back to non-empty evidence
            rej.append(f"set.{key}:no-evidence")
            continue
        new["facts"][key] = cv

    return new, rej


# ---------------- the deterministic code-half (when/feel) ----------------
def _last_in_caption(caption, words):
    """The word from `words` whose last occurrence is LATEST in the caption (the state the scene ends in
    — a cut updates it), or None. Caption-order, not word-list order."""
    cl = (caption or "").casefold()
    best, best_pos = None, -1
    for w in words:
        hits = list(re.finditer(r"\b" + re.escape(w) + r"\b", cl))
        if hits and hits[-1].start() > best_pos:
            best, best_pos = w, hits[-1].start()
    return best


def extract_time_of_day(caption):
    """The latest time-word in the caption, or None. The hybrid's code-disposed `when`."""
    return _last_in_caption(caption, _TIME_WORDS)


def extract_mood(caption):
    """The latest mood-word in the caption, or None. The hybrid's code-disposed `feel`."""
    return _last_in_caption(caption, _MOOD_WORDS)


def canon_to_context(canon):
    """Render the canon as the steering line that REPLACES context_for's `" -> ".join(labels)`."""
    f = canon["facts"]
    bits = []
    if f["subjects"]:
        bits.append("who: " + ", ".join(f["subjects"]))
    for k in SINGLE_KEYS:
        if f[k]:
            bits.append(k.replace("_", " ") + ": " + f[k])
    if f["props"]:
        bits.append("things: " + ", ".join(f["props"]))
    facts_line = ("Canon — " + "; ".join(bits) + ".") if bits else ""
    syn = ("Story so far: " + canon["synopsis"]) if canon["synopsis"] else ""
    return " ".join(p for p in (syn, facts_line) if p).strip()


def update_canon(prior, beat_label, caption, *, delta_fn=None, seed=False):
    """One canon update (the HYBRID orchestrator). Code disposes time_of_day/mood from the caption; the
    LLM (injected `delta_fn(prior, beat_label, caption, seed=)` -> delta dict | None) proposes
    subjects/entrances/synopsis/place. The LLM's time_of_day/mood are STRIPPED so code owns when/feel
    (the measured-better path). `delta_fn=None` -> code-only (pure, no model). Fail-open throughout."""
    canon = copy.deepcopy(prior)
    code_set = {}
    tod = extract_time_of_day(caption)
    if tod:
        code_set["time_of_day"] = tod
    mood = extract_mood(caption)
    if mood:
        code_set["mood"] = mood
    if code_set:
        canon, _ = merge_canon(canon, {"set": code_set, "evidence": caption}, evidence_text=caption)
    if delta_fn is not None:
        delta = delta_fn(canon, beat_label, caption, seed=seed)
        if isinstance(delta, dict):
            d = dict(delta)
            s = {k: v for k, v in (d.get("set") or {}).items() if k not in CODE_KEYS}   # hybrid boundary
            d["set"] = s
            canon, _ = merge_canon(canon, d, evidence_text=caption)
    return canon


def ledger_delta_llm(prior, beat_label, caption, *, seed=False, model=None, temperature=0.6):
    """The LLM half: one delta (or seed) pass on the 0.6 FIDELITY lane (NOT BEAT_TEMP). Returns a delta
    dict or None (fail-open). Lazy-imports the engine so the pure logic above stays import-light. In
    production this folds into the opening/next-turn `ground_frame` vision pass (which also sees the
    frame); here it is the text-only seam used by the spike."""
    import lucid_engine as E
    sysp = _SYS_SEED if seed else _SYS_DELTA
    model = model or E.MODEL
    if seed:
        user = "Opening caption: " + (caption or "(unknown)")
    else:
        user = ("Canon so far: " + json.dumps(prior.get("facts") or {}, ensure_ascii=False) + "\n"
                "Beat the viewer chose: " + (beat_label or "(none)") + "\n"
                "On screen now: " + (caption or "(unknown)"))
    try:
        raw = E._ollama_json(sysp, user, model=model, temperature=temperature)
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


# ---------------- L2 palette gate (flag-only; cv2 in the ComfyUI venv) ----------------
VENV_PY = os.environ.get("LUCID_CV_PYTHON", os.path.expanduser("~/ComfyUI/.venv/bin/python"))

# HSV hue×sat histogram correlation between two frames (1.0 = identical palette). The child READS two
# paths and prints one number — it writes NO file (a private frame's path must not spawn a new sink).
_PALETTE_CHILD = r"""
import sys, json, cv2
a = cv2.imread(sys.argv[1]); b = cv2.imread(sys.argv[2])
if a is None or b is None:
    print(json.dumps({"corr": None})); raise SystemExit
def hist(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
    cv2.normalize(h, h, 0, 1, cv2.NORM_MINMAX)
    return h
print(json.dumps({"corr": float(cv2.compareHist(hist(a), hist(b), cv2.HISTCMP_CORREL))}))
"""

# Unmeasured (ADR-0037 ships L2 flag-only until a fixture sanity pass calibrates it): below this HSV-hist
# correlation the palette is flagged "shifted". Tunable, NOT yet a gate that rejects.
PALETTE_STEADY = float(os.environ.get("LUCID_PALETTE_STEADY", "0.50"))
# A 2-frame HSV histogram is sub-second; the short timeout caps a hung interpreter so the cv2 child can't
# delay a lease release (resource-safety review 2026-06-22) — it never sits 30s on a turn's critical path.
PALETTE_TIMEOUT_S = float(os.environ.get("LUCID_PALETTE_TIMEOUT_S", "10"))


def palette_drift(path_a, path_b):
    """HSV-histogram correlation in [-1, 1] (1 = identical palette), or **None** when it couldn't run
    (venv missing, unreadable image, cv2 error). Fail-closed-None, exactly like lucid_facecv.faces():
    the caller treats None as 'couldn't measure', never as 'steady'."""
    if not os.path.exists(VENV_PY) or not (path_a and path_b
                                           and os.path.exists(path_a) and os.path.exists(path_b)):
        return None
    try:
        r = subprocess.run([VENV_PY, "-c", _PALETTE_CHILD, path_a, path_b],
                           capture_output=True, text=True, timeout=PALETTE_TIMEOUT_S)
        if r.returncode != 0:
            return None
        c = json.loads(r.stdout.strip()).get("corr")
        return float(c) if isinstance(c, (int, float)) else None
    except Exception:
        return None


def palette_verdict(corr):
    """Flag-only verdict from a correlation: 'unknown' (couldn't measure) / 'steady' / 'shifted'. Never
    rejects — the surface composes the calm amber chip from this (ADR-0037 surface contract)."""
    if corr is None:
        return "unknown"
    return "steady" if corr >= PALETTE_STEADY else "shifted"

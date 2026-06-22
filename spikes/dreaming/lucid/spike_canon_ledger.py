#!/usr/bin/env python3
"""ADR-0037 L0 — the GATING ledger-prompt spike (canon ledger feasibility).

This is the experiment the ADR makes load-bearing: can a 3B hold a stable
`{synopsis, facts:{subjects,place,time_of_day,props,mood}}` across ~5 beats — on
the 0.6 fidelity lane, in DELTA form, after a deterministic code-merge — without
drift, bloat, hallucination, or self-contradiction? And does it beat a zero-LLM
code-accumulated control (the ai-generation-reviewer's required A/B — "ship the
LLM only if it measurably wins")?

THROWAWAY SPIKE (spikes/, excluded from the workspace). Two halves:

  1. The DETERMINISTIC dispose/merge (`merge_ledger`) — the future lucid_ground.py
     L0 function. Model PROPOSES a bounded delta; CODE DISPOSES with precedence:
       - parse-fail / wrong-type  -> drop that field, keep prior (fail-open)
       - facts_change needs `evidence`; single-valued keys replace; SUBJECTS are
         APPEND-ONLY (identity is ADR-0033's job, not a ledger back-door)
       - facts_add: append-dedup-cap (over-cap drops the NEW, never evicts stable)
       - empty delta -> clean no-op (never persist a degraded result over a good one)
       - every string red-line-gated (it loops back into a system turn)
     `--selftest` exercises this offline (no Ollama) and is what runs in CI here.

  2. The LIVE measurement harness — runs scripted fixtures with KNOWN ground truth
     N times, scores the ADR go/no-go bar, and A/Bs the LLM ledger vs the code-only
     accumulator. Needs Ollama + a small model; run it on the box when VRAM is free.

Usage:
    python3 spike_canon_ledger.py --selftest                 # offline, deterministic
    python3 spike_canon_ledger.py --smoke                    # 1 live run, see the deltas
    python3 spike_canon_ledger.py --full --runs 20           # the gating sweep
    python3 spike_canon_ledger.py --full --runs 20 --lane    # + 0.6-vs-0.78 lane delta
    python3 spike_canon_ledger.py --model qwen2.5vl:3b --full # vision narrator (text-only here)
"""
import argparse
import copy
import json
import re
import statistics
import sys

import lucid_engine as E   # _ollama_json (0.6 fidelity lane, keep_alive:0), MODEL/NARRATOR_MODEL
import lucid_safety as S    # red_line_ok — the ledger is untrusted model text too

# ---------------- the L0 contract (caps + the delta schema the model must return) ----------------
CAP_SUBJECTS = 4
CAP_PROPS = 8
SYN_CAP = 600            # synopsis hard cap (keep-tail) when accumulating
SINGLE_KEYS = ("place", "time_of_day", "mood")   # single-valued, replace-evidence-gated
LIST_KEYS = ("subjects", "props")                # append-dedup-cap

# NOTE (spike findings, 2026-06-21, hermes3:3b @ 0.6):
#  (1) the reviewer-recommended NESTED form `facts_change:{key:{to,evidence}}` is beyond a 3B — it
#      reliably FLATTENS it, so intended changes never landed. -> FLAT `set` + one `evidence`.
#  (2) a schema with literal EXAMPLE VALUES gets ECHOED: the 3B copied "a NEW character that just
#      entered" into subjects and the placeholder evidence string verbatim (defeating the gate). -> the
#      shape shown is ALL-EMPTY (nothing to copy); the field meanings live in the prose rules only.
# The shape doubles as the "nothing changed" default, so an idle beat is a clean empty delta.
_LEDGER_SCHEMA = (
    '{"add_subjects":[],"add_props":[],"set":{},"evidence":"","synopsis_suffix":"","drift_note":""}'
)

# The ledger UPDATE prompt (delta form). Deliberately narrow — a 3B fixates/collapses on a broad
# "maintain world-state" task (see lucid_engine _SYS_TMPL history). It returns ONLY what CHANGED this
# beat; code carries everything stable forward. NO re-emitting the whole ledger.
_SYS_LEDGER = (
    "You keep a tiny CANON for a silent dream video — the durable facts the story must not forget. "
    "You are given the canon SO FAR, the beat the viewer just chose, and a one-line caption of what is "
    "now on screen. Reply with ONLY a small JSON delta of what CHANGED this beat — never restate facts "
    "that are unchanged. Fill it ONLY with words from THIS beat's caption; copy NOTHING from these "
    "instructions.\n"
    "FIELDS (all optional; leave a field empty when nothing applies):\n"
    "• add_subjects — list; ONLY a genuinely NEW person/animal that just ENTERED on screen. Never "
    "re-list someone already in the canon; never use it to restate or 'change' an existing subject "
    "(their identity is fixed).\n"
    "• add_props — list; notable things that newly APPEAR on screen.\n"
    "• set — an object of the single-valued facts that just TURNED: any of time_of_day, place, mood "
    "(e.g. the light goes from day to night). Omit a key that did not change.\n"
    "• evidence — the exact words from the caption that prove the `set`. If `set` is empty, leave "
    "evidence empty. A `set` with no caption evidence is ignored.\n"
    "• synopsis_suffix — at most ONE short clause (<=120 chars) advancing the story, or empty.\n"
    "• drift_note — at most a short phrase if something looks inconsistent, or empty.\n"
    "If NOTHING durable changed this beat, return every field empty. Output ONLY the JSON object, "
    "with these exact keys and no others: " + _LEDGER_SCHEMA
)

# SEED prompt (spike finding 2026-06-21: the per-beat delta under-emits `place`/initial facts -> stable_ret
# fails on place). The OPENING is grounded ONCE anyway; ask it for the FULL initial fact-set (not a delta).
# In production this rides the opening ground_frame pass. Reuses the same delta shape so merge_ledger
# disposes it identically (caps, grounding, red-line); the difference is only the instruction.
_SYS_SEED = (
    "You START a tiny CANON for a silent dream video from its OPENING shot. Read the one-line caption "
    "and list ALL the durable facts you can see, using ONLY words from the caption.\n"
    "• add_subjects — every person/animal present.\n"
    "• add_props — the notable objects present.\n"
    "• set — the place, and time_of_day and mood if the caption states them (omit a key not stated).\n"
    "• evidence — the caption itself (it is the proof).\n"
    "• synopsis_suffix — one short clause naming the scene.\n"
    "Output ONLY the JSON object, these exact keys and no others: " + _LEDGER_SCHEMA
)


def empty_ledger():
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


def ledger_to_context(ledger):
    """Render the canon as the steering line that REPLACES context_for's `" -> ".join(labels)`."""
    f = ledger["facts"]
    bits = []
    if f["subjects"]:
        bits.append("who: " + ", ".join(f["subjects"]))
    for k in SINGLE_KEYS:
        if f[k]:
            bits.append(k.replace("_", " ") + ": " + f[k])
    if f["props"]:
        bits.append("things: " + ", ".join(f["props"]))
    facts_line = ("Canon — " + "; ".join(bits) + ".") if bits else ""
    syn = ("Story so far: " + ledger["synopsis"]) if ledger["synopsis"] else ""
    return " ".join(p for p in (syn, facts_line) if p).strip()


# ---------------- the DISPOSE: model proposes a delta, CODE merges (the L0 function) ----------------
def _coerce_list(v):
    """A 3B emits a list-field as a bare string ('lantern, rope') or a scalar; be liberal (Postel) —
    split a non-empty string on commas/semicolons into a list. Anything else -> []."""
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        return [p.strip() for p in re.split(r"[;,]", v) if p.strip()]
    return []


def _coerce_set(v, rej):
    """A 3B emits `set` as a dict (good), or a string like 'time_of_day: night' / "time_of_day:'night'"
    (parse it), or a bare key with no value (un-recoverable -> reject). Returns a dict."""
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


def merge_ledger(prior, delta, *, accumulate_synopsis=True, evidence_text=None):
    """Pure, deterministic. Returns (new_ledger, rejections). NEVER raises; any malformed input
    degrades to `prior` (fail-open steering). This is the contract the ADR pins; the selftest is
    its spec. `evidence_text` (the caption) enables the caption-substring GROUNDING guard: a fact whose
    value has no token in the caption is dropped as a hallucination (offline/None = guard off)."""
    new = copy.deepcopy(prior)
    rej = []
    if not isinstance(delta, dict):
        return new, ["delta:not-a-dict"]

    # synopsis: append-bounded keep-tail. (The ADR PREFERS re-derive-O(spine); accumulate is here so
    # the spike can MEASURE the feedback-loop growth the reviewer flagged.)
    if accumulate_synopsis:
        suf = _clean_str(delta.get("synopsis_suffix"), cap=120)
        if suf:
            joined = (new["synopsis"] + " " + suf).strip() if new["synopsis"] else suf
            new["synopsis"] = joined[-SYN_CAP:].lstrip()   # keep the tail (recent matters most)

    # adds: add_subjects / add_props -> append-dedup-cap (coerce sloppy string types; ground vs caption)
    for dkey, fkey, cap in (("add_subjects", "subjects", CAP_SUBJECTS),
                            ("add_props", "props", CAP_PROPS)):
        if dkey not in delta:
            continue
        vals = _coerce_list(delta.get(dkey))
        if not vals and delta.get(dkey) not in (None, "", []):
            rej.append(f"{dkey}:uncoercible")
        for v in vals:
            cv = _clean_str(v, cap=60)
            if not cv:
                rej.append(f"{dkey}:dropped-string")
                continue
            if len(cv.split()) > 5:                             # a subject/prop is a phrase, not a clause
                rej.append(f"{dkey}:too-long")                  # (kills "seaglass glistens in his lantern's beam")
                continue
            if evidence_text is not None and not _supported(cv, evidence_text.casefold()):
                rej.append(f"{dkey}:ungrounded")                # caption-substring hallucination guard
                continue
            cvt = set(re.findall(r"[a-z]+", cv.casefold()))     # token-subset dedup: "keeper" ⊆ "lighthouse
            if any(cvt and (cvt <= set(re.findall(r"[a-z]+", e.casefold()))                # keeper" -> dup;
                            or set(re.findall(r"[a-z]+", e.casefold())) <= cvt)            # "cat" vs
                   for e in new["facts"][fkey]):                                           # "cathedral" -> kept
                continue                                        # dedup (exact or token-subset), silent
            if len(new["facts"][fkey]) >= cap:
                rej.append(f"{dkey}:over-cap")                  # drop NEW, never evict stable
                continue
            new["facts"][fkey].append(cv)

    # set: single-valued key->value, gated by `evidence` (offline) or caption-grounding (live).
    # subjects/props can't be set here (append-only).
    setd = _coerce_set(delta.get("set"), rej)
    ev = _clean_str(delta.get("evidence"), cap=120)
    for key, val in setd.items():
        if key in LIST_KEYS:
            rej.append(f"set.{key}:list-key-not-settable")
            continue
        if key not in SINGLE_KEYS:
            rej.append(f"set.{key}:unknown-key")
            continue
        cv = _clean_str(val, cap=60)
        if not cv:
            rej.append(f"set.{key}:no-value")
            continue
        if evidence_text is not None:                           # live: the value must be IN the caption
            if not _supported(cv, evidence_text.casefold()):
                rej.append(f"set.{key}:ungrounded")
                continue
        elif not ev:                                            # offline: fall back to non-empty evidence
            rej.append(f"set.{key}:no-evidence")
            continue
        new["facts"][key] = cv

    return new, rej


# ---------------- the LIVE update (one beat) ----------------
def ledger_update_llm(prior, beat_label, caption, *, model, temperature, system=_SYS_LEDGER):
    """One delta pass (or a SEED pass when system=_SYS_SEED). Returns (delta_dict|None, raw_str).
    None on parse failure (fail-open)."""
    if system is _SYS_SEED:
        user = "Opening caption: " + (caption or "(unknown)")
    else:
        user = (
            "Canon so far: " + (json.dumps(prior["facts"], ensure_ascii=False)) + "\n"
            "Synopsis so far: " + (prior["synopsis"] or "(none)") + "\n"
            "Beat the viewer chose: " + (beat_label or "(none)") + "\n"
            "On screen now: " + (caption or "(unknown)")
        )
    try:
        raw = E._ollama_json(system, user, model=model, temperature=temperature)
    except Exception as e:
        return None, f"<ollama-error: {e}>"
    try:
        d = json.loads(raw)
        return (d if isinstance(d, dict) else None), raw
    except json.JSONDecodeError:
        return None, raw


# ---------------- the ZERO-LLM control (the A/B baseline) ----------------
# The honest "does L0 even need a model" control: a bounded synopsis (labels + latest caption) plus a
# keyword extractor for the EASY single-valued facts (time_of_day, mood). subjects/place/props are left
# empty — a regex can't do them — so the A/B shows exactly where the LLM's marginal value is.
_TIME_WORDS = ["dawn", "sunrise", "morning", "midday", "noon", "afternoon", "dusk", "sunset",
               "twilight", "evening", "nightfall", "midnight", "night", "dark"]
_MOOD_WORDS = ["calm", "serene", "peaceful", "tense", "dread", "ominous", "joyful", "wonder",
               "melancholy", "eerie", "menacing", "hopeful"]


def accumulate_codeonly(prior, beat_label, caption):
    new = copy.deepcopy(prior)
    cl = (caption or "").casefold()
    for w in _TIME_WORDS:                                       # last match wins (a cut updates it)
        if re.search(r"\b" + re.escape(w) + r"\b", cl):
            new["facts"]["time_of_day"] = w
    for w in _MOOD_WORDS:
        if re.search(r"\b" + re.escape(w) + r"\b", cl):
            new["facts"]["mood"] = w
    add = (beat_label or "").strip()
    if add:
        joined = (new["synopsis"] + " -> " + add) if new["synopsis"] else add
        new["synopsis"] = joined[-SYN_CAP:].lstrip()
    return new


# ---------------- fixtures: scripted dreams with KNOWN ground truth ----------------
# Each beat: caption (the ONLY evidence — hallucination = a fact not supported by the caption union),
# label (the viewer's choice), and the EXPECTED canon-state after this beat. `change` marks an INTENDED
# turn (key -> value) that must be tracked AND held; `stable` marks facts that must NOT drift.
FIXTURES = [
    {
        "name": "lighthouse",
        "opening": {"caption": "A lighthouse keeper in an oilskin coat stands on a north cliff at dusk, holding a brass lantern.",
                    "label": "opening"},
        "beats": [
            {"label": "Raise the lantern", "caption": "The keeper lifts the brass lantern; wind stirs his oilskin coat on the cliff at dusk."},
            {"label": "Night falls",       "caption": "The sky darkens to full night; stars come out over the keeper on the cliff.",
             "change": {"time_of_day": "night"}},
            {"label": "Fog rolls in",      "caption": "Thick fog rolls across the cliff at night; the lantern flame brightens."},
            {"label": "A ship appears",    "caption": "A tall sailing ship appears on the dark horizon beyond the foggy cliff.",
             "enters": ["ship"]},
            {"label": "The beam sweeps",   "caption": "The lighthouse beam sweeps over the ship as the keeper watches at night."},
        ],
        # canon the ledger SHOULD hold by the end:
        "stable": {"subjects_has": ["keeper"], "place_has": "cliff"},
        "final_change": {"time_of_day": "night"},
        "final_enters": ["ship"],
    },
    {
        "name": "greenhouse",
        "opening": {"caption": "A young botanist in a linen apron waters orchids in a sunlit glass greenhouse at noon.",
                    "label": "opening"},
        "beats": [
            {"label": "Mist the leaves",   "caption": "The botanist mists the orchid leaves; sun pours through the glass greenhouse at noon."},
            {"label": "A vine uncurls",    "caption": "A green vine slowly uncurls along a glass pane of the sunlit greenhouse.",
             "enters": ["vine"]},
            {"label": "Clouds gather",     "caption": "Grey clouds gather and the greenhouse dims to an overcast afternoon.",
             "change": {"time_of_day": "afternoon"}},
            {"label": "Rain on the glass",  "caption": "Rain streaks the glass roof; the botanist watches the orchids in the dim greenhouse."},
            {"label": "A cat slips in",    "caption": "A grey cat slips between the pots in the rainy afternoon greenhouse.",
             "enters": ["cat"]},
        ],
        "stable": {"subjects_has": ["botanist"], "place_has": "greenhouse"},
        "final_change": {"time_of_day": "afternoon"},
        "final_enters": ["vine", "cat"],
    },
]


# ---------------- scoring ----------------
def _supported(value, caption_union, ratio=0.5):
    """A fact value is supported if at least `ratio` of its significant tokens appear in the caption
    union (the caption-substring hallucination guard the reviewer proposed). Majority-overlap (not
    any-token) so a hallucinated CLAUSE that merely shares one common word ("...lantern's beam") fails."""
    if not value:
        return True
    toks = [t for t in re.findall(r"[a-z]+", value.casefold()) if len(t) > 3]
    if not toks:
        return True
    hits = sum(1 for t in toks if t in caption_union)
    return (hits / len(toks)) >= ratio


def score_history(history, fixture, captions):
    """history = ledger AFTER each beat (len == #beats). Returns a metrics dict."""
    final = history[-1]
    f = final["facts"]
    cap_union = " ".join(captions).casefold()

    # SUBJECT retention — the load-bearing canon fact ("the dream forgets its CHARACTER"). Gated.
    subj_checks = [any(want in s.casefold() for s in f["subjects"])
                   for want in fixture["stable"]["subjects_has"]]
    subj_ret = sum(subj_checks) / len(subj_checks) if subj_checks else 1.0
    # PLACE retention — reported but NOT gated in v1: the spike found place is unreliably extractable on a
    # 3B (it files "north cliff" under props) AND by a naive regex (grabs "oilskin coat"). Least-valuable
    # fact; demoted to best-effort rather than forcing a bigger model for it.
    place_ret = 1.0 if (f["place"] and fixture["stable"]["place_has"] in f["place"].casefold()) else 0.0

    # intended-change tracked AND held, with subjects not corrupted by the change
    chg_ok = []
    for k, v in fixture["final_change"].items():
        chg_ok.append(v in (f[k] or "").casefold())
    change_tracked = sum(chg_ok) / len(chg_ok) if chg_ok else 1.0

    # new-subject capture (a genuine entrance should appear as a subject or prop)
    pool = " ".join(f["subjects"] + f["props"]).casefold()
    enters_ok = [e in pool for e in fixture["final_enters"]]
    enters_rate = sum(enters_ok) / len(enters_ok) if enters_ok else 1.0

    # hallucination: fact values not supported by the caption union (raw), and what the guard removes
    all_vals = list(f["subjects"]) + list(f["props"]) + [f[k] for k in SINGLE_KEYS if f[k]]
    unsupported = [v for v in all_vals if not _supported(v, cap_union)]
    halluc_raw = (len(unsupported) / len(all_vals)) if all_vals else 0.0

    # synopsis growth across beats (chars) — slope tells us linear (bad) vs sub-linear (ok)
    lens = [len(h["synopsis"]) for h in history]

    return {"subj_ret": subj_ret, "place_ret": place_ret, "change_tracked": change_tracked,
            "enters_rate": enters_rate, "halluc_raw": halluc_raw,
            "syn_lens": lens, "n_facts": len(all_vals), "unsupported": unsupported}


def _run_one(fixture, *, mode, model, temperature):
    """One full pass over a fixture. mode in {'llm','code'}. Returns (history, captions, valid_rate)."""
    led = empty_ledger()
    # seed from the opening caption so beat 1 has canon to hold
    captions = [fixture["opening"]["caption"]]
    if mode == "llm":
        cap0 = fixture["opening"]["caption"]
        d, _ = ledger_update_llm(led, fixture["opening"]["label"], cap0,
                                 model=model, temperature=temperature, system=_SYS_SEED)
        led, _ = merge_ledger(led, d or {}, evidence_text=cap0)
        valid = [d is not None]
    else:
        led = accumulate_codeonly(led, fixture["opening"]["label"], fixture["opening"]["caption"])
        valid = []
    history = []
    for b in fixture["beats"]:
        captions.append(b["caption"])
        if mode == "llm":
            d, _ = ledger_update_llm(led, b["label"], b["caption"], model=model, temperature=temperature)
            led, _ = merge_ledger(led, d or {}, evidence_text=b["caption"])
            valid.append(d is not None)
        else:
            led = accumulate_codeonly(led, b["label"], b["caption"])
        history.append(copy.deepcopy(led))
    valid_rate = (sum(valid) / len(valid)) if valid else 1.0
    return history, captions, valid_rate


def _slope(lens):
    """Per-beat synopsis growth slope (chars/beat) via least squares; <~ a clause/beat is sub-linear-ish."""
    n = len(lens)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx, my = statistics.mean(xs), statistics.mean(lens)
    den = sum((x - mx) ** 2 for x in xs) or 1
    return sum((x - mx) * (y - my) for x, y in zip(xs, lens)) / den


def run_sweep(*, runs, model, temperature, mode):
    agg = {"subj_ret": [], "place_ret": [], "change_tracked": [], "enters_rate": [], "halluc_raw": [],
           "valid": [], "slope": []}
    for fx in FIXTURES:
        for _ in range(runs):
            history, captions, valid = _run_one(fx, mode=mode, model=model, temperature=temperature)
            m = score_history(history, fx, captions)
            agg["subj_ret"].append(m["subj_ret"])
            agg["place_ret"].append(m["place_ret"])
            agg["change_tracked"].append(m["change_tracked"])
            agg["enters_rate"].append(m["enters_rate"])
            agg["halluc_raw"].append(m["halluc_raw"])
            agg["valid"].append(valid)
            agg["slope"].append(_slope(m["syn_lens"]))
    return {k: (statistics.mean(v) if v else 0.0) for k, v in agg.items()}


# ---------------- the go/no-go bar (from ADR-0037 §Open/owed) ----------------
BAR = {"subj_ret": 0.90, "change_tracked": 0.90, "enters_rate": 0.75,
       "halluc_raw": 0.10, "valid": 0.95}


def _verdict_line(name, val, thresh, lower_is_better=False):
    ok = (val <= thresh) if lower_is_better else (val >= thresh)
    arrow = "<=" if lower_is_better else ">="
    return f"  [{'PASS' if ok else 'FAIL'}] {name:16} {val:6.2f}  ({arrow} {thresh})", ok


def report(label, res, *, gate=True):
    print(f"\n=== {label} ===")
    rows = [
        _verdict_line("subj_ret", res["subj_ret"], BAR["subj_ret"]),
        _verdict_line("change_tracked", res["change_tracked"], BAR["change_tracked"]),
        _verdict_line("enters_rate", res["enters_rate"], BAR["enters_rate"]),
        _verdict_line("halluc_raw", res["halluc_raw"], BAR["halluc_raw"], lower_is_better=True),
        _verdict_line("valid_delta", res["valid"], BAR["valid"]),
    ]
    for line, _ in rows:
        print(line)
    print(f"   .   place_ret {res['place_ret']:.2f} (best-effort, NOT gated — see ADR §place-demoted)")
    print(f"   .   synopsis slope {res['slope']:.1f} chars/beat (sub-linear if ~<=120; linear growth = re-derive mandatory)")
    if gate:
        passed = all(ok for _, ok in rows)
        print(f"   => {'GO' if passed else 'NO-GO'} for L0-as-LLM on {label}")
        return passed
    return None


# ---------------- offline selftest (the dispose spec; runs HERE, no Ollama) ----------------
def selftest():
    t = 0
    def check(cond, msg):
        nonlocal t
        t += 1
        assert cond, f"selftest #{t} FAILED: {msg}"
        print(f"  ok #{t}: {msg}")

    L = empty_ledger()
    # 1. add subjects + props
    L1, rej = merge_ledger(L, {"add_subjects": ["a keeper"], "add_props": ["lantern"]})
    check(L1["facts"]["subjects"] == ["a keeper"] and L1["facts"]["props"] == ["lantern"], "add subject+prop")
    # 2. dedup (casefold), no dup
    L2, _ = merge_ledger(L1, {"add_subjects": ["A Keeper"]})
    check(L2["facts"]["subjects"] == ["a keeper"], "casefold dedup keeps one subject")
    # 2b. token-subset dedup: a more/less specific restatement is a dup; an unrelated word is kept
    Ls = empty_ledger(); Ls["facts"]["subjects"] = ["lighthouse keeper"]
    Ls2, _ = merge_ledger(Ls, {"add_subjects": ["keeper", "a cathedral"]})
    check(Ls2["facts"]["subjects"] == ["lighthouse keeper", "a cathedral"],
          "token-subset dedup drops 'keeper', keeps 'cathedral'")
    # 3. set single-valued WITH evidence
    L3, _ = merge_ledger(L2, {"set": {"time_of_day": "night"}, "evidence": "sky went dark"})
    check(L3["facts"]["time_of_day"] == "night", "evidence-gated set applies")
    # 4. set WITHOUT evidence rejected
    L4, rej = merge_ledger(L3, {"set": {"time_of_day": "dawn"}, "evidence": ""})
    check(L4["facts"]["time_of_day"] == "night" and any("no-evidence" in r for r in rej), "no-evidence set rejected")
    # 5. subjects are append-only: set.subjects rejected
    L5, rej = merge_ledger(L3, {"set": {"subjects": "a stranger"}, "evidence": "x"})
    check(L5["facts"]["subjects"] == ["a keeper"] and any("list-key-not-settable" in r for r in rej),
          "subjects cannot be set (append-only; identity is ADR-0033)")
    # 6. empty delta = no-op
    L6, rej = merge_ledger(L3, {})
    check(L6 == L3 and rej == [], "empty delta is a clean no-op")
    # 7. non-dict delta = fail-open keep prior
    L7, rej = merge_ledger(L3, "garbage")
    check(L7 == L3 and rej == ["delta:not-a-dict"], "non-dict delta degrades to prior")
    # 8. props cap holds, drops NEW not stable
    base = empty_ledger()
    base["facts"]["props"] = [f"p{i}" for i in range(CAP_PROPS)]
    L8, rej = merge_ledger(base, {"add_props": ["overflow"]})
    check("overflow" not in L8["facts"]["props"] and len(L8["facts"]["props"]) == CAP_PROPS
          and any("over-cap" in r for r in rej), "over-cap drops the new prop, keeps stable")
    # 9. uncoercible field (a number) dropped, valid sibling survives
    L9, rej = merge_ledger(L, {"add_props": 42, "add_subjects": ["ok"]})
    check(L9["facts"]["subjects"] == ["ok"] and any("add_props:uncoercible" in r for r in rej),
          "uncoercible field dropped, valid sibling applied")
    # 9b. unknown set key rejected, sibling applies
    L9b, rej = merge_ledger(L3, {"set": {"weather": "rain", "mood": "eerie"}, "evidence": "fog and rain"})
    check(L9b["facts"]["mood"] == "eerie" and any("set.weather:unknown-key" in r for r in rej),
          "unknown set key rejected, known sibling applied")
    # 9c. COERCE a 3B's bare-string list field into a list
    L9c, _ = merge_ledger(L, {"add_props": "a lantern, weathered rope"})
    check(L9c["facts"]["props"] == ["a lantern", "weathered rope"], "coerce comma-string -> prop list")
    # 9d. COERCE a 3B's 'key: value' string set, evidence-gated
    L9d, _ = merge_ledger(L2, {"set": "time_of_day: 'night'", "evidence": "the sky went dark"})
    check(L9d["facts"]["time_of_day"] == "night", "coerce 'key: value' string set")
    # 9e. string set with an empty value is un-recoverable -> rejected
    L9e, rej = merge_ledger(L2, {"set": "time_of_day:"})
    check(L9e["facts"].get("time_of_day") is None and any("set:string-no-value" in r for r in rej),
          "string set with empty value rejected")
    # 9e2. bare key with no colon -> not-a-dict
    L9e2, rej = merge_ledger(L2, {"set": "time_of_day"})
    check(any("set:not-a-dict" in r for r in rej), "bare-key (no colon) string set rejected")
    # 9f. caption-grounding guard: an ungrounded add is dropped, a grounded one applies
    L9f, rej = merge_ledger(L, {"add_props": ["a dragon", "a lantern"]},
                            evidence_text="the keeper lifts a brass lantern on the cliff")
    check(L9f["facts"]["props"] == ["a lantern"] and any("add_props:ungrounded" in r for r in rej),
          "caption-grounding drops the hallucinated prop, keeps the grounded one")
    # 9f2. clause-as-prop (>5 words) rejected even though it shares a caption word
    L9f2, rej = merge_ledger(L, {"add_props": ["seaglass glistens in his lantern's beam"]},
                             evidence_text="the keeper lifts a brass lantern on the cliff")
    check(L9f2["facts"]["props"] == [] and any("add_props:too-long" in r for r in rej),
          "clause-as-prop rejected by the word-count cap")
    # 9f3. majority-overlap: one shared word in a multi-word phrase is NOT enough
    L9f3, rej = merge_ledger(L, {"add_props": ["glowing dragon scales"]},
                             evidence_text="the keeper lifts a brass lantern; scales of rust on the rail")
    check("glowing dragon scales" not in L9f3["facts"]["props"] and any("ungrounded" in r for r in rej),
          "majority-overlap rejects a phrase sharing only one caption word")
    # 9g. caption-grounding on set: a value not in the caption is rejected
    L9g, rej = merge_ledger(L2, {"set": {"time_of_day": "night"}, "evidence": "x"},
                            evidence_text="the sun blazes at high noon")
    check(L9g["facts"].get("time_of_day") != "night" and any("set.time_of_day:ungrounded" in r for r in rej),
          "caption-grounding rejects an ungrounded set value")
    # 10. synopsis accumulates + keep-tail cap
    big = empty_ledger(); big["synopsis"] = "x" * (SYN_CAP - 5)
    L10, _ = merge_ledger(big, {"synopsis_suffix": "a brand new clause here"})
    check(len(L10["synopsis"]) <= SYN_CAP, "synopsis keep-tail respects the cap")
    # 11. code-only control tracks time-of-day from a caption
    c = accumulate_codeonly(empty_ledger(), "Night falls", "the sky darkens to full night")
    check(c["facts"]["time_of_day"] == "night", "code-only control extracts time_of_day")
    # 12. hallucination guard: unsupported value flagged
    check(not _supported("dragon", "a keeper on a cliff at night") and _supported("keeper", "a keeper on a cliff"),
          "caption-substring guard separates supported from hallucinated")
    print(f"\nselftest: {t}/{t} passed — the dispose contract holds.")


def main():
    ap = argparse.ArgumentParser(description="ADR-0037 L0 gating ledger-prompt spike")
    ap.add_argument("--selftest", action="store_true", help="offline dispose spec (no Ollama)")
    ap.add_argument("--smoke", action="store_true", help="1 live run per fixture, print the canon + deltas")
    ap.add_argument("--full", action="store_true", help="the gating sweep")
    ap.add_argument("--runs", type=int, default=20, help="runs per fixture for --full")
    ap.add_argument("--model", default=E.NARRATOR_MODEL, help="ledger model (default: registry narrator-beats)")
    ap.add_argument("--temp", type=float, default=0.6, help="fidelity lane (ADR: 0.6, NOT BEAT_TEMP)")
    ap.add_argument("--lane", action="store_true", help="also run at 0.78 to measure the lane delta")
    args = ap.parse_args()

    if args.selftest:
        selftest(); return

    if args.smoke:
        for fx in FIXTURES:
            print(f"\n########## SMOKE: {fx['name']} (model={args.model}, temp={args.temp}) ##########")
            led = empty_ledger()
            led, _ = merge_ledger(led, (ledger_update_llm(led, fx["opening"]["label"],
                                  fx["opening"]["caption"], model=args.model, temperature=args.temp,
                                  system=_SYS_SEED)[0] or {}), evidence_text=fx["opening"]["caption"])
            for b in fx["beats"]:
                d, raw = ledger_update_llm(led, b["label"], b["caption"], model=args.model, temperature=args.temp)
                led, rej = merge_ledger(led, d or {}, evidence_text=b["caption"])
                print(f"\n• beat: {b['label']}\n  cap: {b['caption']}")
                print(f"  delta: {json.dumps(d) if d else '<<unparseable>> ' + raw[:120]}")
                if rej:
                    print(f"  rejected: {rej}")
                print(f"  canon: {json.dumps(led['facts'], ensure_ascii=False)}")
            print(f"\n  context line -> {ledger_to_context(led)!r}")
        return

    if args.full:
        print(f"model={args.model}  fixtures={[f['name'] for f in FIXTURES]}  runs/fixture={args.runs}")
        llm = run_sweep(runs=args.runs, model=args.model, temperature=args.temp, mode="llm")
        go = report(f"LLM ledger @ temp {args.temp}", llm, gate=True)
        code = run_sweep(runs=1, model=args.model, temperature=args.temp, mode="code")
        report("zero-LLM code-accumulated control (A/B)", code, gate=False)
        # the reviewer's A/B verdict: the LLM must WIN on what code can't do (subjects/place/changes)
        margin = llm["change_tracked"] - code["change_tracked"]
        enters = llm["enters_rate"] - code["enters_rate"]
        print(f"\nA/B — LLM beats code-only by: change_tracked +{margin:.2f}, new-subject +{enters:.2f}")
        print("   => ship the LLM only if it BOTH clears the bar AND beats this control on subjects/entrances.")
        if args.lane:
            hot = run_sweep(runs=args.runs, model=args.model, temperature=0.78, mode="llm")
            report("LLM ledger @ temp 0.78 (the surprise lane — should be WORSE)", hot, gate=False)
            print(f"\nlane delta (0.6 - 0.78): subj_ret {llm['subj_ret']-hot['subj_ret']:+.2f}, "
                  f"halluc {llm['halluc_raw']-hot['halluc_raw']:+.2f} (expect 0.6 better)")
        print(f"\nOVERALL: {'GO — L0-as-LLM is viable' if go else 'NO-GO — fall back to the code-only accumulator'}")
        return

    ap.print_help()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""A/B harness for the Lucid beat-gen prompt (the "what happens next" branch suggestions).

Drives the EXACT engine path (E._ollama_json + the same JSON/format contract) so an A/B is faithful to
production. Compares the CURRENT _SYS_TMPL against a candidate template on the same scenarios, grounding
on a real frame when one is given (the narrator is a vision model), and prints a crude divergence metric
so "generic" vs "branchy" is measurable, not just vibes.

    python3 ab_branch_prompt.py baseline [frame.png]
    python3 ab_branch_prompt.py compare candidate_tmpl.txt [frame.png]

candidate_tmpl.txt: the full replacement _SYS_TMPL (with {n}, {rating_clause}, doubled-brace JSON).
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_engine as E  # noqa: E402

# Text-only scenarios (context_for-shaped user messages) — isolate the PROMPT's effect on divergence.
SCENARIOS = [
    "This dream is about: a woman wandering an empty seaside hotel at dusk. "
    "Story so far: she steps inside -> the lobby is silent. "
    "On screen now: a woman stands in a tall doorway, dim amber light behind her, looking into the room.",

    "This dream is about: a quiet library that doesn't obey physics. "
    "Story so far: the dream is just beginning. "
    "On screen now: rows of bookshelves recede into shadow under a single hanging lamp.",

    "This dream is about: a man chasing a memory through a rain-slicked city. "
    "Story so far: he leaves the station -> neon smears in the puddles. "
    "On screen now: a man in a wet coat stands at a crosswalk, head turned slightly, traffic lights glowing red.",

    # ACCEPTANCE GATE (interestingness critic): a deliberately banal frame, sparse premise, empty story.
    # This is the exact failure mode the complaint describes — the new prompt must STILL spread 4 distinct
    # moves here, not 4 ambient fidgets.
    "This dream is about: a woman in a doorway. "
    "Story so far: the dream is just beginning. "
    "On screen now: a woman stands in a doorway in dim light.",
]

# crude fidget-detector: a prompt is "fidget-only" if it leans on ambient micro-motion AND carries no
# strong narrative-move signal. Lexical + directional only (the real judgement is reading the 4 cards).
FIDGET = re.compile(r"\b(breath|breathe|breathing|hair|fabric|gaze|blink|sway|flutter|shifts? (?:her |his )?weight|stir)\b", re.I)
MOVE = re.compile(r"\b(enter|appear|resolv|figure|glow|dusk|dawn|colou?r|morph|dissolv|becomes?|transform|push-?in|pull-?back|reveal|shadow|float|gravity|weather|tide|rain|door|light(s)? (?:dim|swell|bloom|drain)|widen|tighten|dread|awe|vast|tip)", re.I)


def run(tmpl, context, rating, n, frame_b64, temperature=0.6):
    """One beat-gen roll with an arbitrary system template, via the real _ollama_json + format contract."""
    clause = E._RATING_CLAUSE.get(rating, E._RATING_CLAUSE["sfw"])
    system = tmpl.format(n=n, rating_clause=clause)
    raw = E._ollama_json(system, context, images=[frame_b64] if frame_b64 else None, temperature=temperature)
    try:
        data = json.loads(raw)
        return [b for b in (E._sanitize(x) for x in data.get("beats", [])) if b][:n]
    except Exception as e:
        return [{"label": "PARSE-FAIL", "prompt": f"{e}: {raw[:160]}"}]


_WORD = re.compile(r"[a-z']+")


def divergence(beats):
    """Crude 0..1 divergence: how varied are the prompts? mean pairwise Jaccard DISTANCE of word-sets,
    plus the count of distinct opening verbs/words. Higher = the choices are less interchangeable."""
    sets = [set(_WORD.findall(b["prompt"].lower())) for b in beats if b.get("prompt")]
    if len(sets) < 2:
        return 0.0, 0
    dists, pairs = 0.0, 0
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            u = sets[i] | sets[j]
            inter = sets[i] & sets[j]
            dists += 1.0 - (len(inter) / len(u) if u else 0.0)
            pairs += 1
    firsts = {(_WORD.findall(b["label"].lower()) or [""])[0] for b in beats}
    return (dists / pairs if pairs else 0.0), len(firsts)


def show(title, beats):
    div, firsts = divergence(beats)
    fidget = sum(1 for b in beats if FIDGET.search(b["prompt"]) and not MOVE.search(b["prompt"]))
    empty = sum(1 for b in beats if not b["label"].strip())
    print(f"\n  ── {title}  [lex-div={div:.2f}, distinct-labels={firsts}/{len(beats)}, "
          f"fidget-only={fidget}/{len(beats)}, empty-labels={empty}/{len(beats)}]")
    for b in beats:
        flag = " ⚠fidget" if (FIDGET.search(b["prompt"]) and not MOVE.search(b["prompt"])) else ""
        print(f"     • {(b['label'] or '∅'):<30}  {b['prompt']}{flag}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    cand_path = sys.argv[2] if mode == "compare" else None
    frame_arg = sys.argv[3] if mode == "compare" and len(sys.argv) > 3 else (
        sys.argv[2] if mode == "baseline" and len(sys.argv) > 2 else None)

    frame_b64, rating = None, "sfw"
    if frame_arg and os.path.exists(frame_arg):
        frame_b64 = E.frame_to_b64(frame_arg)
        cap, rating = E.ground_frame(frame_b64, premise=None)
        print(f"[frame {os.path.basename(frame_arg)}] grounded caption={cap!r} rating={rating}")

    old = E._SYS_TMPL
    new = None
    if mode == "compare":
        with open(cand_path) as f:
            new = f.read().strip()
        # fail fast if the candidate breaks the .format() brace contract
        try:
            new.format(n=4, rating_clause="X")
        except Exception as e:
            print(f"!! candidate template FAILS .format() contract: {e}")
            sys.exit(2)

    n = 4
    for i, ctx in enumerate(SCENARIOS, 1):
        print("\n" + "=" * 100)
        print(f"SCENARIO {i}: {ctx}")
        show("OLD (current @0.6)", run(old, ctx, rating, n, frame_b64, temperature=0.6))
        if new:
            show("NEW (candidate @%.2f)" % E.BEAT_TEMP, run(new, ctx, rating, n, frame_b64, temperature=E.BEAT_TEMP))


if __name__ == "__main__":
    main()

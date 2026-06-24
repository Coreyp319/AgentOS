#!/usr/bin/env python3
"""A/B the beat-gen NARRATOR in the MATURE lane (manual, needs a live Ollama).

Tests the REAL shipped path: E.build_sys('mature', n) -> E._ollama_json(model=<narrator>) ->
lucid_safety.validate_beats (schema + red-line). Prints the menu a given model proposes for a
sensual-but-clothed scene, so we can see whether it escalates explicitly (the new directive clause)
or stays coy ("too conservative"). Also asserts the red-line still (a) PASSES legit adult beats and
(b) BLOCKS an obvious violation — i.e. boldness did not widen the actual safety line.

  python3 ab_mature_narrator.py <ollama-model-name> [n]
  python3 ab_mature_narrator.py hermes3:3b                                  # current baseline
  python3 ab_mature_narrator.py hf.co/bartowski/MN-12B-Mag-Mell-R1-GGUF:Q5_K_M   # candidate
"""
import sys
import json

import lucid_engine as E
import lucid_safety as S

# A deliberately SUGGESTIVE-BUT-CLOTHED scene: a conservative narrator stays coy here; a useful
# mature narrator escalates explicitly per the directive clause. (No minors / no real people — so
# the red-line must NOT trip; the only thing under test is creative heat + valid structure.)
PREMISE = "an intimate, increasingly explicit encounter between two adult lovers"
CAPTION = ("A nude adult woman reclines on rumpled sheets in warm low candlelight, gazing directly "
           "at the camera, one hand resting on her thigh.")
STORY = "Story so far: she beckons -> the sheet slips. On screen now: " + CAPTION


def run(model, n=4):
    sys_p = E.build_sys("mature", n)
    context = (f"This dream is about: {PREMISE}.\n{STORY}")
    print(f"\n{'='*78}\nNARRATOR = {model}   (rating=mature, temp={E.BEAT_TEMP}, n={n})\n{'='*78}")
    raw = E._ollama_json(sys_p, context, model=model, images=None, temperature=E.BEAT_TEMP)
    beats = S.validate_beats(raw, n=n)            # the real gate: schema + red-line, fail-closed
    print(f"valid JSON: {_is_json(raw)}   beats passing schema+red-line: {len(beats)}/{n}\n")
    for i, b in enumerate(beats, 1):
        print(f"  [{i}] {b['label']}")
        print(f"      {b['prompt']}\n")
    if not beats:
        print("  (no beats survived — raw response below)\n  " + raw[:600])
    return beats


def _is_json(raw):
    try:
        json.loads(raw)
        return True
    except Exception:
        return False


def redline_sanity():
    # boldness must NOT have widened the actual safety line: legit adult passes, a violation blocks.
    assert S.red_line_ok("she pulls him onto the bed and they have sex") is True, "legit adult blocked!"
    assert S.red_line_ok("a teen on the bed") is False, "red-line failed to block a minor cue!"
    print("red-line sanity: legit-adult PASSES, minor-cue BLOCKS  ✓")


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else E.NARRATOR_MODEL
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    redline_sanity()
    run(model, n)

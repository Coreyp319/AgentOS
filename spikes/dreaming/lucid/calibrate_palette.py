#!/usr/bin/env python3
"""ADR-0037 L2 — the palette-threshold sanity pass (§Open/owed). CPU-only (cv2 histogram via lucid_ground),
no GPU. Characterizes the REAL drift distribution from the beat-frames on disk so PALETTE_STEADY stops being
an "unmeasured" guess (0.50).

Two reference distributions from the persistent (non-private) dream frames in ComfyUI/input:
  * WITHIN-dream consecutive pairs  (n_i -> n_{i+1}) = the dream's own beat-to-beat evolution — the baseline
    of "normal" change. An INTENTIONAL cut (night falls / new place) lives in this set's low tail and SHOULD
    flag (the chip says "colors shifted — fine if you meant it"); imperceptible continuity is the high mass.
  * CROSS-dream pairs (one dream's opening vs another's) = unrelated palettes — "definitely different", the
    floor the threshold must sit well above.

It does NOT auto-change anything; it prints the distributions + a data-grounded recommended PALETTE_STEADY
and how each verdict would land. Run: python3 calibrate_palette.py
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_engine as E   # noqa: E402  (INPUT_DIR)
import lucid_ground as G   # noqa: E402  (palette_drift = the same cv2 child the gate uses)

_N = re.compile(r"^(?P<dream>.+)_n(?P<idx>\d+)\.png$")


def _frames_by_dream():
    out = {}
    for p in glob.glob(os.path.join(E.INPUT_DIR, "*_n*.png")):
        m = _N.match(os.path.basename(p))
        if not m:
            continue
        out.setdefault(m["dream"], []).append((int(m["idx"]), p))
    for d in out:
        out[d].sort()
    return out


def _stats(xs):
    xs = sorted(v for v in xs if v is not None)
    if not xs:
        return None
    n = len(xs)
    def pct(q):
        return xs[min(n - 1, int(q * n))]
    return {"n": n, "min": xs[0], "p10": pct(0.10), "p25": pct(0.25), "median": pct(0.50),
            "p75": pct(0.75), "max": xs[-1], "mean": sum(xs) / n}


def _line(name, s):
    if not s:
        print(f"  {name:14} (no measurable pairs)")
        return
    print(f"  {name:14} n={s['n']:3d}  min={s['min']:.3f} p10={s['p10']:.3f} p25={s['p25']:.3f} "
          f"median={s['median']:.3f} p75={s['p75']:.3f} max={s['max']:.3f}")


def main():
    if not os.path.exists(G.VENV_PY):
        print(f"cv2 venv missing ({G.VENV_PY}) — cannot measure; L2 stays fail-closed-None ('unknown').")
        return
    by_dream = _frames_by_dream()
    dreams = {d: fs for d, fs in by_dream.items() if len(fs) >= 2}
    print(f"frames: {sum(len(v) for v in by_dream.values())} across {len(by_dream)} dreams "
          f"({len(dreams)} with >=2 frames)\n")

    within = []                                # vs-PARENT (consecutive): the as-wired L2 comparison
    vs_open = []                               # vs-OPENING (cumulative): the ADR's named alternative
    for d, fs in dreams.items():
        for (_, a), (_, b) in zip(fs, fs[1:]):
            within.append(G.palette_drift(a, b))
        op = fs[0][1]
        for _, b in fs[1:]:
            vs_open.append(G.palette_drift(op, b))

    openings = [fs[0][1] for fs in by_dream.values() if fs]
    cross = []
    for i in range(len(openings)):
        for j in range(i + 1, len(openings)):
            cross.append(G.palette_drift(openings[i], openings[j]))

    ws, cs, vo = _stats(within), _stats(cross), _stats(vs_open)
    print("HSV-hist correlation (1.0 = identical palette):")
    _line("vs-parent", ws)        # consecutive beats — what L2 is wired to compare
    _line("vs-opening", vo)       # cumulative drift from the dream's first frame (the alternative)
    _line("cross-dream", cs)

    # Recommendation: sit the threshold below typical evolution (so continuity isn't flagged) yet above the
    # cross-dream mass (so a real change is). The within-dream p10/p25 is the "noticeable jump" knee; the
    # cross-dream p75 is the "definitely different" ceiling. Pick the midpoint, clamped into a sane band.
    if ws and cs:
        knee = ws["p10"]                       # bottom tenth of a dream's own steps = the big jumps
        ceil_cross = cs["p75"]
        rec = round(max(min((knee + ceil_cross) / 2, knee), 0.20), 2)
        cur = G.PALETTE_STEADY
        print(f"\ncurrent PALETTE_STEADY = {cur} (was unmeasured)")
        print(f"recommended PALETTE_STEADY ~ {rec}  "
              f"(within-dream p10={knee:.3f} = the 'noticeable jump' knee; cross-dream p75={ceil_cross:.3f})")
        for thr, tag in ((cur, "current"), (rec, "recommended")):
            wf = sum(1 for v in within if v is not None and v < thr)
            cf = sum(1 for v in cross if v is not None and v < thr)
            print(f"  @ {thr:.2f} ({tag}): within-dream flagged 'shifted' {wf}/{ws['n']} "
                  f"({100*wf/ws['n']:.0f}%), cross-dream flagged {cf}/{cs['n']} ({100*cf/cs['n']:.0f}%)")
        print("\nread: within-dream 'shifted' % ~= how often the chip lights on a dream's own beats (intentional\n"
              "cuts SHOULD be in here); cross-dream should be ~100% (a real palette change always flags).")
    else:
        print("\nnot enough measurable pairs to recommend a threshold (need more dreams with >=2 frames).")


if __name__ == "__main__":
    main()

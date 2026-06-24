#!/usr/bin/env python3
"""Standalone harness to verify LTX/10Eros keyframe-guide conditioning (spatial feed-forward) WITHOUT
the full Lucid UI/daemon. It drives the REAL engine code path (build_10eros_i2v.build + the
_inject_ltx_guides surgery + run_beat), so what you test here is exactly what the app runs.

Two modes:
  --dry-run   build the LTX graph for an anchor + prompt, splice the guides in, and DUMP the resulting
              ComfyUI graph to JSON (default /tmp/lucid_guided_graph.json) + a summary. No GPU, no
              ComfyUI — proves the LTXVAddGuide injection is structurally valid and lets you load the
              JSON into ComfyUI to eyeball the guide chain.
  --run       force the 10Eros engine and actually submit via run_beat -> ComfyUI -> an mp4. The real
              GPU test (needs ComfyUI reachable with the 10Eros/LTX model, like a normal dream beat).

A guide is `PATH@T[:TAG]` where T is seconds into the clip and TAG in more|less|hold|change (default
hold). The anchor (--anchor) is the seed/first frame, exactly as a beat's anchor.

Examples:
  ./verify_guides.py --anchor frame.png --prompt "she turns toward the sea" \\
      --guide frame.png@1.4:hold --guide other.png@0.5:more --dry-run
  LUCID_ENGINE=10eros ./verify_guides.py --anchor frame.png --prompt "..." \\
      --guide frame.png@1.4:hold --run
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_engine as E   # the real engine: build (ltx), _inject_ltx_guides, run_beat, current_engine

TAGS = ("more", "less", "hold", "change")


def parse_guide(spec):
    """`PATH@T[:TAG]` -> (abs_path, t_seconds, tag)."""
    if "@" not in spec:
        sys.exit(f"bad --guide '{spec}': expected PATH@T[:TAG]")
    path, rest = spec.rsplit("@", 1)
    tag = "hold"
    if ":" in rest:
        rest, tag = rest.split(":", 1)
    if tag not in TAGS:
        sys.exit(f"bad tag '{tag}' in --guide '{spec}': one of {TAGS}")
    try:
        t = float(rest)
    except ValueError:
        sys.exit(f"bad timestamp '{rest}' in --guide '{spec}'")
    ap = os.path.abspath(path)
    if not os.path.isfile(ap):
        sys.exit(f"--guide file not found: {ap}")
    return (ap, t, tag)


def _count(api, ct):
    return sum(1 for n in api.values() if isinstance(n, dict) and n.get("class_type") == ct)


def dry_run(anchor, prompt, length, guides, out, api_graph=None):
    clamped = E._clamp_length_ltx(length)
    if api_graph:
        # OFFLINE: inject into an ALREADY-CONVERTED api graph (e.g. workflows/10eros-i2v.api.json).
        # No ComfyUI needed — this isolates and proves the _inject_ltx_guides surgery itself.
        with open(api_graph) as f:
            api = json.load(f)
    else:
        # build the real LTX graph from the anchor; NB build() calls ComfyUI /object_info to convert
        # the UI workflow to api format, so this branch needs ComfyUI reachable (no GPU render though).
        built = E.ltx.build(os.path.abspath(anchor), prompt, E.LTX_LONGER, clamped,
                            "lucid/verify", steps=E.LTX_STEPS, seed=1234, gguf=E.LTX_GGUF)
        api = built[0] if isinstance(built, tuple) else built
    before_g, before_l = _count(api, "LTXVAddGuide"), _count(api, "LoadImage")
    # _inject_ltx_guides copies guide frames into ComfyUI's input dir; ensure it exists so the
    # structural test runs fully even on a box without a populated input/ dir.
    os.makedirs(E.INPUT_DIR, exist_ok=True)
    api = E._inject_ltx_guides(api, guides, clamped)
    after_g, after_l = _count(api, "LTXVAddGuide"), _count(api, "LoadImage")
    with open(out, "w") as f:
        json.dump(api, f, indent=2)
    print(f"anchor        : {anchor}")
    print(f"length (clamp): {length} -> {clamped} frames")
    print(f"guides given  : {len(guides)}")
    for ap, t, tag in guides:
        print(f"   {tag:6} @ {t:>5.2f}s  strength={E.LTX_GUIDE_STRENGTH.get(tag, 0.6)}  {os.path.basename(ap)}")
    print(f"LTXVAddGuide  : {before_g} -> {after_g}  (+{after_g - before_g})")
    print(f"LoadImage     : {before_l} -> {after_l}  (+{after_l - before_l})")
    ok = (after_g - before_g) == len(guides) and (after_l - before_l) == len(guides)
    print(f"graph written : {out}")
    print("RESULT        :", "OK — guides spliced into the graph" if ok
          else "guides were NOT all injected (fail-open skip? check the log above)")
    print("\nGPU-UNVERIFIED: load the JSON into ComfyUI to eyeball the guide chain, or use --run for a real render.")
    return 0 if ok else 1


def run(anchor, prompt, length, guides):
    if E.current_engine() != "10eros":
        try:
            E.set_engine("10eros")
        except Exception:
            os.environ["LUCID_ENGINE"] = "10eros"
    if E.current_engine() != "10eros":
        sys.exit("could not select the 10Eros engine — set LUCID_ENGINE=10eros")
    os.makedirs(E.INPUT_DIR, exist_ok=True)
    name = os.path.basename(anchor)
    dst = os.path.join(E.INPUT_DIR, name)
    if os.path.abspath(anchor) != os.path.abspath(dst):
        shutil.copy(anchor, dst)            # run_beat expects the anchor in ComfyUI's input dir
    print(f"submitting one guided beat via run_beat (engine={E.current_engine()}, {len(guides)} guide(s))…")
    clip, seed = E.run_beat(prompt, name, length=length, guides=guides)
    print(f"OK — clip: {clip}  (seed {seed})")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Verify LTX keyframe-guide conditioning end-to-end.")
    ap.add_argument("--anchor", help="seed/first frame (PNG); required unless --api-graph")
    ap.add_argument("--prompt", default="", help="the next-beat prompt")
    ap.add_argument("--guide", action="append", default=[], metavar="PATH@T[:TAG]",
                    help="a tagged guide frame; repeatable")
    ap.add_argument("--length", type=int, default=getattr(E, "DEFAULT_LEN", 49), help="segment frame count")
    ap.add_argument("--out", default="/tmp/lucid_guided_graph.json", help="(dry-run) graph dump path")
    ap.add_argument("--api-graph", help="(dry-run, OFFLINE) inject into this pre-converted api graph "
                    "instead of building one — no ComfyUI needed")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="build+inject+dump the graph; no GPU render")
    g.add_argument("--run", action="store_true", help="actually submit via ComfyUI (real GPU test)")
    a = ap.parse_args()
    if not a.api_graph and not a.anchor:
        sys.exit("--anchor is required (or use --api-graph for an offline injection check)")
    if a.anchor and not os.path.isfile(a.anchor):
        sys.exit(f"--anchor not found: {a.anchor}")
    if a.run and not a.anchor:
        sys.exit("--run needs --anchor")
    guides = [parse_guide(s) for s in a.guide]
    if not guides:
        print("note: no --guide given; this just builds the base graph (no spatial feed-forward to test)")
    sys.exit(dry_run(a.anchor, a.prompt, a.length, guides, a.out, api_graph=a.api_graph) if a.dry_run
             else run(a.anchor, a.prompt, a.length, guides))


if __name__ == "__main__":
    main()

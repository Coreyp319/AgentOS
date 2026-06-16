#!/usr/bin/env python3
"""AgentOS Lucid — thin slice of the interactive branching dream loop (ADR-0014).

THROWAWAY SPIKE. It runs generation **directly**, with NO VRAM-coordinator lease
yet (ship gate, ADR-0006/0010/0013). Its only job is to prove that
"click -> the story continues" is worth building.

One turn of the loop:
    parent.out_frame  ---------------------------------+  (i2v first frame)
                                                       v
    story-so-far  --(Ollama, keep_alive:0)-->  beats[{label, prompt}]
                                                       |
    chosen prompt + first frame + seed  --(Remix-i2v)--+--> new clip
                                                            |
    ffmpeg last-frame  <----------------------------------- + (becomes next anchor)

State is a branching tree on disk (clips + tree.json). Each node:
    {id, parent, label, prompt, seed, clip, out_frame, caption}

Reuses spikes/dreaming/comfy_client.py for ComfyUI; reuses the Remix-i2v
workflow we built. Honors ADR-0009/0014 defaults: SFW unless told otherwise, and
a fail-closed red-line (no minors, no real identifiable people) in the LLM
contract. NOT consent-gated / NOT lease-arbitrated — that lands when it leaves
the spike.
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import urllib.request

# import the shared ComfyUI client from the parent spike dir
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import comfy_client as cc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_WF = os.path.join(HERE, "..", "workflows", "Wan2.2-Remix-NSFW-i2v-v3.0.json")
WORKFLOW = os.environ.get("LUCID_WORKFLOW", os.path.abspath(REPO_WF))
INPUT_DIR = os.path.join(cc.COMFY_ROOT, "input")
DREAMS_DIR = os.environ.get(
    "LUCID_DREAMS", os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "agentos", "dreams"))

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
# beat-gen is a tiny task; use whatever's available. keep_alive:0 evicts it so
# the GPU is free for the video step (ADR-0009 §3 mutual exclusion).
MODEL = os.environ.get("LUCID_MODEL", "gemma4:latest")

DEFAULT_W, DEFAULT_H, DEFAULT_LEN = 720, 1280, 33  # ~2s portrait @16fps; matches the
# workflow's baked WanImageToVideo length and stays under the VRAM-thrash line (ADR-0014 §6)

SYS_SFW = (
    "You are the narrator of a SILENT, looping dream video. Given the story so far "
    "and what is on screen now, propose {n} distinct 'what happens next' choices. "
    "Each: a 2-5 word button LABEL, and a vivid image-to-video MOTION prompt "
    "(camera movement + subject motion, present tense, concrete, under 40 words) "
    "that continues smoothly FROM THE CURRENT FRAME. Keep it SFW. "
    "RED LINE (never violate): no minors, no real or identifiable real people. "
    'Return ONLY JSON: {{"beats":[{{"label":"...","prompt":"..."}}]}}.'
)


# ---------------- LLM (beat-gen) ----------------
def _ollama_json(system, user, model=MODEL):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        "format": "json",
        "keep_alive": 0,            # evict right after -> frees VRAM for video
        "options": {"temperature": 0.9},
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    # first call pays model-load latency (cold weights + possible VRAM evict)
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["message"]["content"]


def _sanitize(beat):
    """code disposes: cap lengths, drop empties. (Red-line filtering is owed to a
    real guard per ADR-0014 §7; this is the spike's minimal hygiene.)"""
    label = (beat.get("label") or "").strip()[:40]
    prompt = (beat.get("prompt") or "").strip()[:400]
    return {"label": label, "prompt": prompt} if label and prompt else None


def propose_beats(context, n=4):
    raw = _ollama_json(SYS_SFW.format(n=n), context)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    beats = [b for b in (_sanitize(x) for x in data.get("beats", [])) if b]
    return beats[:n]


def story_context(tree, node_id):
    """The path from root to node, as a short synopsis the LLM can continue."""
    chain, cur = [], node_id
    while cur is not None:
        nd = tree["nodes"][str(cur)]
        if nd.get("label") and nd["label"] != "opening":
            chain.append(nd["label"])
        cur = nd.get("parent")
    chain.reverse()
    cap = tree["nodes"][str(node_id)].get("caption")
    parts = []
    if chain:
        parts.append("Story so far: " + " -> ".join(chain) + ".")
    else:
        parts.append("The dream is just beginning.")
    parts.append("On screen now: " + (cap or "the opening image."))
    return " ".join(parts)


# ---------------- video (one beat) ----------------
def extract_last_frame(mp4_path, name, out_path=None):
    """Grab the true last frame. `out_path` (an absolute, caller-owned destination from
    lucid_store) is written verbatim — the privacy layer owns the path, so the privacy-unaware
    engine never re-derives a private location (privacy-review BLOCKER). Else INPUT_DIR/name."""
    if out_path is None:
        os.makedirs(INPUT_DIR, exist_ok=True)
        out_path = os.path.join(INPUT_DIR, name)
    # -sseof -3 + -update: write every frame to one file; the last one survives.
    subprocess.run(
        ["ffmpeg", "-y", "-sseof", "-3", "-i", mp4_path,
         "-update", "1", "-q:v", "2", out_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return name


def _set_widgets(wf, prompt, image_name, seed, w, h, length, output_prefix=None):
    for nd in wf["nodes"]:
        t, title = nd["type"], (nd.get("title") or "")
        wv = nd.get("widgets_values")
        if t == "CLIPTextEncode" and "Positive" in title:
            wv[0] = prompt
        elif t == "LoadImage":
            wv[0] = image_name
        elif t == "Seed (rgthree)":
            wv[0] = seed
        elif t == "WanImageToVideo":
            nd["widgets_values"] = [w, h, length, 1]
        elif t == "VHS_VideoCombine" and isinstance(wv, dict):
            # %date% tokens only expand in ComfyUI's UI frontend; submitted via the API
            # they're taken literally (a dir named "%date:...%"). Use a clean, anchor-
            # derived prefix so clips land in output/lucid/ traceable to their parent.
            # output_prefix lets the caller redirect (e.g. a private subdir, ADR-0016).
            wv["filename_prefix"] = output_prefix or ("lucid/" + os.path.splitext(image_name)[0])


def run_beat(prompt, first_frame_name, seed=None,
             w=DEFAULT_W, h=DEFAULT_H, length=DEFAULT_LEN, timeout=1800, output_prefix=None):
    """Parameterize the Remix-i2v workflow and generate one clip. Returns mp4 path."""
    if seed is None:
        seed = random.randint(1, 2**31)
    with open(WORKFLOW) as f:
        wf = json.load(f)
    _set_widgets(wf, prompt, first_frame_name, seed, w, h, length, output_prefix)
    api = cc.ui_to_api(wf)
    files, _hist = cc.generate(api, timeout=timeout)
    if not files:
        raise RuntimeError("generation produced no video")
    return files[0], seed


# ---------------- tree state ----------------
def _session_dir(session):
    return os.path.join(DREAMS_DIR, session)


def load_tree(session):
    with open(os.path.join(_session_dir(session), "tree.json")) as f:
        return json.load(f)


def save_tree(session, tree):
    d = _session_dir(session)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "tree.json"), "w") as f:
        json.dump(tree, f, indent=2)


def start(session, opening_image=None, from_clip=None):
    """Create a session whose root 'out_frame' is the opening anchor."""
    os.makedirs(_session_dir(session), exist_ok=True)
    if from_clip:
        out_frame = extract_last_frame(from_clip, f"{session}_n0.png")
        clip = from_clip
    elif opening_image:
        out_frame = f"{session}_n0.png"
        shutil.copy(opening_image, os.path.join(INPUT_DIR, out_frame))
        clip = None
    else:
        raise ValueError("need --image or --from")
    tree = {"session": session, "counter": 0, "current": 0, "nodes": {
        "0": {"id": 0, "parent": None, "label": "opening", "prompt": None,
              "seed": None, "clip": clip, "out_frame": out_frame, "caption": None}}}
    save_tree(session, tree)
    return tree


def step(session, prompt, label, seed=None, parent=None):
    """Generate one clip continuing from `parent` (default: current)."""
    tree = load_tree(session)
    parent = tree["current"] if parent is None else parent
    anchor = tree["nodes"][str(parent)]["out_frame"]
    clip, used_seed = run_beat(prompt, anchor, seed=seed)
    nid = tree["counter"] + 1
    out_frame = extract_last_frame(clip, f"{session}_n{nid}.png")
    tree["nodes"][str(nid)] = {
        "id": nid, "parent": parent, "label": label, "prompt": prompt,
        "seed": used_seed, "clip": clip, "out_frame": out_frame, "caption": None}
    tree["counter"] = nid
    tree["current"] = nid
    save_tree(session, tree)
    return tree["nodes"][str(nid)]


# ---------------- CLI (headless test harness) ----------------
def _main():
    ap = argparse.ArgumentParser(description="Lucid dream-loop spike (ADR-0014)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("session")
    s.add_argument("--image"); s.add_argument("--from", dest="from_clip")
    b = sub.add_parser("beats"); b.add_argument("session")
    st = sub.add_parser("step"); st.add_argument("session")
    st.add_argument("--choose", type=int, help="index into proposed beats")
    st.add_argument("--prompt", help="type-your-own beat (overrides --choose)")
    st.add_argument("--label", default="custom")
    args = ap.parse_args()

    if args.cmd == "start":
        t = start(args.session, args.image, args.from_clip)
        print(f"started '{args.session}' root out_frame={t['nodes']['0']['out_frame']}")
    elif args.cmd == "beats":
        t = load_tree(args.session)
        ctx = story_context(t, t["current"])
        print("context:", ctx)
        for i, bt in enumerate(propose_beats(ctx)):
            print(f"  [{i}] {bt['label']}  ::  {bt['prompt']}")
    elif args.cmd == "step":
        t = load_tree(args.session)
        if args.prompt:
            prompt, label = args.prompt, args.label
        else:
            beats = propose_beats(story_context(t, t["current"]))
            chosen = beats[args.choose or 0]
            prompt, label = chosen["prompt"], chosen["label"]
        print(f"generating: {label} :: {prompt}")
        nd = step(args.session, prompt, label)
        print(f"-> node {nd['id']} clip={nd['clip']}")


if __name__ == "__main__":
    _main()

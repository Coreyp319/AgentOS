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
import lucid_models  # noqa: E402  (registry: the beat model is an editable affiliation, not hardcoded)
import build_10eros_i2v as ltx  # noqa: E402  (the verified LTX-2.3 / 10Eros i2v graph builder)

HERE = os.path.dirname(os.path.abspath(__file__))
# Non-distilled GGUF i2v (Enhanced nolight, real CFG + 20 steps) + NSFW-22 explicit-anatomy LoRA on the
# LOW-noise expert only (high stays 0.0 — see LORA_HIGH/LORA_LOW): fixes the distilled Remix's melt
# AND the LoRA-on-high "anatomy from within anatomy" regression. ~4x the time per beat. Revert to pure
# non-distilled = enhNSFW-nolight-i2v.api.json; to distilled = LUCID_WORKFLOW=<remix .json>.
REPO_WF = os.path.join(HERE, "..", "workflows", "lucid-nolight-nsfw-i2v.api.json")
WORKFLOW = os.environ.get("LUCID_WORKFLOW", os.path.abspath(REPO_WF))
INPUT_DIR = os.path.join(cc.COMFY_ROOT, "input")
DREAMS_DIR = os.environ.get(
    "LUCID_DREAMS", os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "agentos", "dreams"))

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
# beat-gen is a tiny text task — resolve the model from the registry ("narrator" role) so it is an
# editable affiliation, not hardcoded. A SMALL model (qwen2.5vl:3b, 3.2 GB) frees in ~3s under
# force_evict and can coexist with the lighter video models; gemma4 (9.6 GB) was slow/wedge-prone to
# evict and blocked the video step every turn (ADR-0015 §3 force-evict / ADR-0018 small-model lane).
MODEL = os.environ.get("LUCID_MODEL") or lucid_models.get("narrator", "qwen2.5vl:3b")

DEFAULT_W, DEFAULT_H, DEFAULT_LEN = 720, 1280, 33  # ~2s portrait @16fps; matches the
# workflow's baked WanImageToVideo length and stays under the VRAM-thrash line (ADR-0014 §6)
# A user-chosen "next segment length" is bounded HERE (code disposes): at 720x1280 on the non-distilled
# GGUF, beats past 49f (3s) run past the 1800s gen timeout (and toward the VRAM-thrash line); Wan's latent
# stride wants 4k+1 frame counts. 17..49f ≈ 1..3s @16fps. (Raise once a faster expert — fp8 — lands.)
MIN_LEN, MAX_LEN = 17, 49
# I2V ModelSamplingSD3 shift. Shared by BOTH i2v paths, so the default stays 8.0: that is the distilled
# Remix's baked schedule AND a sane 720x1280 value (Wan shift tracks resolution, not distillation —
# higher res wants higher shift). Drop to ~5.0 via LUCID_SHIFT to calm motion on the non-distilled graph;
# do NOT lower it on the Remix path.
DEFAULT_SHIFT = float(os.environ.get("LUCID_SHIFT", "8.0"))
# Explicit-anatomy LoRA (NSFW-22) strength, SPLIT BY EXPERT. The HIGH-noise expert lays out bodies, so an
# explicit-anatomy LoRA there fights the layout and melts anatomy ("anatomy from within anatomy") — the
# #1 distortion cause after lightning. Keep it OFF high (0.0) and ON low (~0.6): the playbook's
# low-noise-only rule. Tune live with LUCID_I2V_LORA_HIGH / LUCID_I2V_LORA_LOW and restart.
LORA_HIGH = float(os.environ.get("LUCID_I2V_LORA_HIGH", "0.0"))
LORA_LOW = float(os.environ.get("LUCID_I2V_LORA_LOW", "0.6"))
# Instareal realism LoRA — "the realism filter", the lever behind the best early Wan 2.2 results.
# Like the anatomy LoRA it rides the LOW-noise expert (skin/detail) and stays OFF high (anti-melt).
# Unlike anatomy it is NOT rating-gated — realism applies to every beat. Tune live with
# LUCID_REALISM_HIGH / LUCID_REALISM_LOW and restart (realism on low is the skin/texture win).
REALISM_HIGH = float(os.environ.get("LUCID_REALISM_HIGH", "0.0"))
REALISM_LOW = float(os.environ.get("LUCID_REALISM_LOW", "0.7"))

# ── i2v ENGINE selection (ADR-0023 10Eros lane) ──────────────────────────────────────────────
# Two interchangeable i2v backends behind run_beat: "wan" (the default — the non-distilled Wan 2.2
# GGUF + NSFW-22 LoRA graph above) and "10eros"/"ltx" (LTX-2.3 10Eros via the verified
# build_10eros_i2v builder). Editable affiliation: LUCID_ENGINE env, else the registry "i2v-engine"
# role, else "wan" (so nothing changes until flipped). 10Eros is a single merged checkpoint, so the
# Wan high/low LoRA split + ModelSamplingSD3 shift do not apply on its path.
ENGINE = (os.environ.get("LUCID_ENGINE")
          or lucid_models.get("i2v-engine", "wan")).strip().lower()  # import-time default
_ENGINE_OVERRIDE = None   # runtime override set by the web toggle (set_engine)
LTX_LONGER = int(os.environ.get("LUCID_LTX_LONGER", "768"))   # longer-edge px; 1024 if VRAM allows
LTX_STEPS = int(os.environ.get("LUCID_LTX_STEPS", "28"))      # verified hand/detail sweet spot
# Default the lucid lane to Q4_K_M: on a LIVE desktop only ~20 GB is free, and Q6's ~19 GB job + the
# coordinator's ~1.2 GB headroom exceeds that, so Q6 leases get admission-refused mid-use. Q4 (~15 GB
# job) admits reliably and is fast; raise to Q6 via LUCID_LTX_GGUF when the GPU is freer (a fresh login).
LTX_GGUF = os.environ.get("LUCID_LTX_GGUF") or "10Eros_v1-Q4_K_M.gguf"
LTX_MIN_LEN, LTX_MAX_LEN = 17, 121   # LTX ~5 s window; snap to its 8-frame latent stride (8k+1)
# VRAM admission estimate (predict-before-load) = the JOB's own allocation, NOT total-GPU. Q4 ~15 GB
# (≈ Wan), Q6 ~19 GB. Must be ≤ free − coordinator_headroom or the lease is refused; Q6 only admits on
# a light desktop. est_mib() picks per the active quant. (The ~22 GB I first used was total-GPU incl.
# desktop — wrong as an `est`; it refused every lease.)
EST_MIB_WAN, EST_MIB_LTX = 17000, 17000   # LTX default lane = Q4_K_M (~Wan footprint)

# ── LTX-ONLY keyframe-guide conditioning (spatial feed-forward) ───────────────────────────────────
# Maps a viewer-tag intent (more|less|hold|change) to an LTXVAddGuide `strength` — how hard the model
# is pinned to the tagged frame at that video position. "hold" (keep this exact moment) pins hardest;
# "less" (de-emphasize) pins softest. Unknown/missing tags fall to a neutral 0.6 (see _inject_ltx_guides).
LTX_GUIDE_STRENGTH = {"hold": 0.9, "more": 0.7, "change": 0.55, "less": 0.4}


def _is_ltx(name):
    return (name or "").strip().lower() in ("10eros", "ltx", "ltx2", "ltx-2.3")


def current_engine():
    """Live-resolved i2v engine, canonicalized to 'wan' | '10eros'. Precedence: runtime web override
    > LUCID_ENGINE env > registry/import default. Resolved per-call so a live toggle takes effect on
    the next beat without a restart."""
    name = _ENGINE_OVERRIDE or os.environ.get("LUCID_ENGINE") or ENGINE
    return "10eros" if _is_ltx(name) else "wan"


def set_engine(name):
    """Runtime engine override (web toggle). Accepts 'wan'|'10eros'(+ltx aliases); ignores junk.
    Returns the resolved active engine."""
    global _ENGINE_OVERRIDE
    n = (name or "").strip().lower()
    if _is_ltx(n) or n == "wan":
        _ENGINE_OVERRIDE = n
    return current_engine()


def est_mib():
    """VRAM admission estimate (MiB) for the *active* engine + quant — what lucid_linear leases with.
    Q6/Q8 GGUF need ~19 GB (only admits on a light desktop); Q4 ~17 GB (≈ Wan, admits reliably)."""
    if current_engine() != "10eros":
        return EST_MIB_WAN
    return 19000 if ("Q6" in LTX_GGUF.upper() or "Q8" in LTX_GGUF.upper()) else EST_MIB_LTX


def clamp_length(n):
    """Snap a proposed segment length to a Wan-friendly 4k+1 count inside the VRAM-safe band.
    A bad/oversized/None request can never thrash the GPU — the model proposes, code disposes."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return DEFAULT_LEN
    n = max(MIN_LEN, min(MAX_LEN, n))
    return ((n - 1) // 4) * 4 + 1   # snap down to 4k+1 (latent stride)

# ── beat-gen steering, split into TWO orthogonal concerns (was one hard-SFW string) ──────────────
# `_SYS_TMPL` is the CONTINUITY core — narrator role + the i2v guidance that keeps last-frame chaining
# coherent (subtle motion, hold the same pose/framing). It is the SAME for every rating. Only the
# {rating_clause} varies, so the *content rating* never bleeds into the *motion guidance* (and vice
# versa). {n} and the JSON contract are filled by build_sys(); the doubled braces survive .format().
_SYS_TMPL = (
    "You are the narrator of a SILENT, looping dream video. Study the CURRENT FRAME (attached when "
    "available) plus the story so far, then propose {n} DISTINCT 'what happens next' choices that each "
    "clearly continue FROM THE CURRENT FRAME and stay faithful to what is actually on screen. "
    "Each: a 2-5 word button LABEL, and a vivid image-to-video MOTION prompt "
    "(camera movement + subject motion, present tense, concrete, under 40 words). "
    "Favor SUBTLE motion that keeps the subject in the SAME pose and framing (small gestures, "
    "breathing, shifting weight, hair, fabric, gaze, gentle camera) — NOT turning away, walking off, "
    "or large repositioning; the current composition must stay in view. "
    "Make the {n} choices genuinely different from one another. {rating_clause} "
    "RED LINE (never violate): no minors, no real or identifiable real people. "
    'Return ONLY JSON: {{"beats":[{{"label":"...","prompt":"..."}}]}}.'
)
# The ONLY part the inferred content rating swaps. The RED LINE above is rating-independent and is ALSO
# re-enforced deterministically in code (lucid_safety.red_line_ok) on every beat — this clause only
# tunes creative tone, it can never widen the red line.
_RATING_CLAUSE = {
    "sfw": "Keep every choice strictly SFW — no nudity or sexual content.",
    "mature": ("This dream is adult/mature: you MAY propose sensual or explicit continuations when they "
               "fit what is on screen. (Still absolutely no minors and no real, identifiable people.)"),
}


def build_sys(rating, n):
    """The beat-gen system prompt for an inferred content `rating` ('sfw'|'mature'). Unknown -> SFW."""
    clause = _RATING_CLAUSE.get(rating, _RATING_CLAUSE["sfw"])
    return _SYS_TMPL.format(n=n, rating_clause=clause)


# back-compat: the throwaway lucid_engine CLI (propose_beats) and any external reference still resolve
# E.SYS_SFW; .replace leaves {n} + the JSON {{...}} intact so SYS_SFW.format(n=n) keeps working.
SYS_SFW = _SYS_TMPL.replace("{rating_clause}", _RATING_CLAUSE["sfw"])

# ── frame grounding (ADR-0014 §6): the narrator is a VISION model (qwen2.5vl) — let it SEE the frame.
# One short VLM pass captions + content-rates the current frame; "model proposes, code disposes": the
# rating only ever selects the steering clause above + the render LoRA strength, and ANY uncertainty
# (unreachable model, bad JSON, unknown value) collapses to the SAFE default "sfw". Mirrors the
# image-message shape already proven in lucid_b2 (the seed-likeness VLM).
SYS_GROUND = (
    "You are a careful visual analyst for a private, local, single-user dream-video tool. "
    "Look at the attached image. Reply with ONLY JSON: "
    '{"caption":"<one vivid present-tense sentence describing exactly what is on screen>",'
    '"rating":"sfw"}. '
    'Set "rating":"mature" ONLY if the image itself (or the stated premise) is clearly adult, sensual, '
    'or explicit; otherwise "sfw". This rating adjusts creative tone only — it NEVER permits minors or '
    "real, identifiable people."
)

# ── note decomposition (ADR-0014): the i2v workflow takes only ONE seed image, so when the viewer
# tags several moments of the clip they just watched, those EXTRA frames can't be fed to ComfyUI.
# Instead a VISION model SEES every tagged frame + the viewer's per-frame intent and decomposes them
# into ONE concrete i2v continuation prompt. Same image-message shape as SYS_GROUND; same "model
# proposes, code disposes" stance — neutral here, a downstream red-line gate is the safety authority.
SYS_DECOMPOSE = (
    "You are a film-continuation assistant for a private, local, single-user dream-video tool. "
    "You receive several numbered frames taken from the clip the viewer just watched. Each frame has "
    "an intent tag from the viewer: more = emphasize this element going forward, less = reduce or "
    "remove it, hold = keep this exact framing/composition, change = alter it. You are also given the "
    "viewer's chosen next direction for the upcoming beat. "
    "DECOMPOSE all of this into ONE vivid, concrete image-to-video continuation prompt. Ground every "
    "detail ONLY in what is actually visible in the frames — do not invent elements that are not shown. "
    'Reply with ONLY JSON: {"prompt":"<one continuation prompt>"}.'
)


# ---------------- LLM (beat-gen) ----------------
def _ollama_json(system, user, model=MODEL, images=None):
    """One JSON-mode chat turn. `images` (list of base64 PNG/JPEG) attaches to the user message so a
    vision model can ground on the actual frame (lucid_b2 shape); None keeps the text-only path."""
    user_msg = {"role": "user", "content": user}
    if images:
        user_msg["images"] = images
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, user_msg],
        "stream": False,
        "format": "json",
        "keep_alive": 0,            # evict right after -> frees VRAM for video
        # 0.9 favored divergence over fidelity to the frame; 0.6 keeps options varied but on-scene now
        # that the model can actually see what it is continuing from (the grounding pass).
        "options": {"temperature": 0.6},
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    # first call pays model-load latency (cold weights + possible VRAM evict)
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["message"]["content"]


def frame_to_b64(path):
    """Base64 of an anchor frame for VLM grounding. None on ANY failure (caller falls back to the
    text-only, SFW-default path). Size-bounded — our frames are small PNGs; refuse anything absurd
    rather than read an arbitrarily large file into memory."""
    if not path:
        return None
    try:
        import base64
        with open(path, "rb") as f:
            data = f.read(16 * 1024 * 1024 + 1)   # cap the read; our frames are well under this
        if not data or len(data) > 16 * 1024 * 1024:
            return None
        return base64.b64encode(data).decode("ascii")
    except Exception:
        return None


def ground_frame(frame_b64, premise=None):
    """One VLM pass over the current frame -> (caption|None, rating). `rating` is code-disposed: ONLY a
    literal "mature" opens up; everything else (including an unreachable/garbled model) -> "sfw" (the
    safe default). The caller red-line-checks the caption before persisting/showing/feeding it back."""
    if not frame_b64:
        return None, "sfw"
    user = ("Premise: " + premise.strip() + "\n" if premise else "") + "Describe and rate this frame."
    try:
        data = json.loads(_ollama_json(SYS_GROUND, user, images=[frame_b64]))
    except Exception:
        return None, "sfw"
    cap = data.get("caption") if isinstance(data, dict) else None
    cap = cap.strip()[:200] if isinstance(cap, str) and cap.strip() else None
    rating = "mature" if isinstance(data, dict) and data.get("rating") == "mature" else "sfw"
    return cap, rating


def decompose_notes(beat_prompt, tagged, premise=None):
    """One VLM pass that SEES the viewer's tagged frames + their per-frame intent and decomposes them
    into a single refined i2v continuation prompt. `tagged` is a list of {"b64","tag","text","t"}
    already ordered by t. Returns the prompt (str, <=300 chars) or None on ANY failure/empty (the
    caller then falls back to the text-only path). Same image-message + fail-safe shape as
    ground_frame; safety is the downstream red-line gate's job, not ours."""
    if not tagged:
        return None
    lines = []
    if premise:
        lines.append("Premise: " + premise.strip())
    lines.append("Chosen next direction: " + (beat_prompt or "").strip())
    lines.append("Tagged frames (in order):")
    for i, t in enumerate(tagged):
        note = (t.get("text") or "").strip()
        lines.append(
            "Frame %d [%s]%s" % (i + 1, t.get("tag", ""), (" — " + note) if note else ""))
    user = "\n".join(lines)
    try:
        data = json.loads(_ollama_json(
            SYS_DECOMPOSE, user, images=[t["b64"] for t in tagged]))
    except Exception:
        return None
    prompt = data.get("prompt") if isinstance(data, dict) else None
    prompt = prompt.strip()[:300] if isinstance(prompt, str) and prompt.strip() else None
    return prompt


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


def extract_frame_at(mp4_path, t, name, out_path=None):
    """Grab the frame at `t` seconds so the next beat anchors on a user-tagged moment
    (spatial feed-forward), not just the clip's end. Same `out_path`/`name` convention as
    extract_last_frame (caller-owned absolute path verbatim, else INPUT_DIR/name). Returns
    the ref `name` on success, None on any failure so the caller can fall back to the last frame."""
    if out_path is None:
        os.makedirs(INPUT_DIR, exist_ok=True)
        out_path = os.path.join(INPUT_DIR, name)
    t = max(0.0, float(t))
    # -ss before -i: fast keyframe seek; -frames:v 1 + -update: write exactly one frame.
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", mp4_path,
             "-frames:v", "1", "-update", "1", "-q:v", "2", out_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return name
    except Exception as e:
        print(f"extract_frame_at({mp4_path}, t={t}) failed: {e}")
        return None


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
        elif t == "ModelSamplingSD3":   # both experts: calmer motion, stay near the seed pose
            wv[0] = DEFAULT_SHIFT
        elif t == "VHS_VideoCombine" and isinstance(wv, dict):
            # %date% tokens only expand in ComfyUI's UI frontend; submitted via the API
            # they're taken literally (a dir named "%date:...%"). Use a clean, anchor-
            # derived prefix so clips land in output/lucid/ traceable to their parent.
            # output_prefix lets the caller redirect (e.g. a private subdir, ADR-0016).
            wv["filename_prefix"] = output_prefix or ("lucid/" + os.path.splitext(image_name)[0])


def _is_api_graph(wf):
    """True for an already-converted /prompt API graph (dict of class_type nodes) vs a UI workflow.
    Lets WORKFLOW be either the UI Remix graph OR a non-distilled API graph (the anti-melt swap)."""
    return isinstance(wf, dict) and "nodes" not in wf and bool(wf) and all(
        isinstance(v, dict) and "class_type" in v for v in wf.values())


def _api_prompt_node(api):
    """The positive CLIPTextEncode id: the node whose positive/negative inputs link DIRECTLY to a
    CLIPTextEncode (Wan routes conditioning through WanImageToVideo, so don't trust slot order)."""
    for n in api.values():
        p, q = n.get("inputs", {}).get("positive"), n.get("inputs", {}).get("negative")
        if isinstance(p, list) and isinstance(q, list) and \
           api.get(p[0], {}).get("class_type") == "CLIPTextEncode" and \
           api.get(q[0], {}).get("class_type") == "CLIPTextEncode":
            return p[0]
    return None


def _set_widgets_api(api, prompt, image_name, seed, w, h, length, output_prefix=None, lora_low=LORA_LOW):
    """Parameterize a non-distilled API-format i2v graph (UnetLoaderGGUF experts) — the same knobs
    _set_widgets sets on the UI Remix graph. Negative stays the graph's baked anatomy-quality text.
    `lora_low` is the LOW-noise explicit-anatomy LoRA strength, set by the inferred content rating
    (sfw -> 0.0 keeps the NSFW LoRA OFF so a SFW beat isn't rendered by an explicit-anatomy graph;
    mature -> the tuned strength)."""
    pos = _api_prompt_node(api)
    if pos:
        api[pos]["inputs"]["text"] = prompt
    for n in api.values():
        ct, ins = n["class_type"], n["inputs"]
        if ct == "LoadImage":
            ins["image"] = image_name
        elif ct == "WanImageToVideo":
            ins["width"], ins["height"], ins["length"] = w, h, length
        elif ct == "ModelSamplingSD3":
            ins["shift"] = DEFAULT_SHIFT
        elif ct == "KSamplerAdvanced":
            ins["noise_seed"] = seed
        elif ct == "LoraLoaderModelOnly":
            name = ins.get("lora_name", "").lower()   # high-noise expert lays out bodies → keep LoRA off it
            is_high = ("-h-" in name) or ("_high" in name)
            if "instareal" in name:                   # realism filter — applies regardless of rating
                ins["strength_model"] = REALISM_HIGH if is_high else REALISM_LOW
            else:                                      # explicit-anatomy (NSFW-22) — rating-gated, off high
                ins["strength_model"] = LORA_HIGH if is_high else lora_low
        elif ct == "VHS_VideoCombine":
            ins["filename_prefix"] = output_prefix or ("lucid/" + os.path.splitext(image_name)[0])


def lora_low_for(rating):
    """Render-side disposal of the inferred rating: only "mature" engages the explicit-anatomy LoRA;
    "sfw" (and any unknown) keeps it OFF so the renderer matches the beats it was given."""
    return LORA_LOW if rating == "mature" else 0.0


def _clamp_length_ltx(n):
    """Snap a proposed segment length to LTX's 8-frame latent stride (8k+1) inside its safe band —
    LTX's analogue of clamp_length; a bad/None request can never thrash the GPU (code disposes)."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = DEFAULT_LEN
    n = max(LTX_MIN_LEN, min(LTX_MAX_LEN, n))
    return ((n - 1) // 8) * 8 + 1


def _ltx_cond_latent_vae_sampler(api):
    """Locate the splice points for LTX keyframe-guide injection in a built 10Eros/LTX api graph.
    Returns (cond_node_id, latent_src, vae_src, sampler_id) or None if any is unidentifiable
    (caller then skips injection — fail-open). LTXVAddGuide takes positive/negative CONDITIONING +
    LATENT + VAE and emits (positive, negative, latent); we chain it between the conditioning source
    (the single LTXVConditioning, which the UI workflow chains AddGuide from) and the sampler.

      cond_node_id : the LTXVConditioning id (outputs positive@0, negative@1)
      latent_src   : [node, slot] currently feeding the sampler's `latent_image`
      vae_src      : [node, slot] the video VAE (LTXVAddGuide encodes the guide image with it)
      sampler_id   : the SamplerCustomAdvanced id whose latent_image we rewire to the last guide
    """
    conds = [i for i, n in api.items() if n.get("class_type") == "LTXVConditioning"]
    samplers = [i for i, n in api.items() if n.get("class_type") == "SamplerCustomAdvanced"]
    if len(conds) != 1 or not samplers:
        return None
    cond_id = conds[0]
    # the sampler that actually consumes a latent_image (the first-pass sampler in the stripped graph)
    sampler_id = next((i for i in samplers
                       if isinstance(api[i]["inputs"].get("latent_image"), list)), None)
    if sampler_id is None:
        return None
    latent_src = list(api[sampler_id]["inputs"]["latent_image"])
    # the video VAE: prefer the one the LTXVImgToVideoInplace seed uses; else any VAELoader* node
    vae_src = None
    inplace = [n for n in api.values()
               if n.get("class_type", "").startswith("LTXVImgToVideoInplace")]
    if inplace and isinstance(inplace[0]["inputs"].get("vae"), list):
        vae_src = list(inplace[0]["inputs"]["vae"])
    if vae_src is None:
        vae_node = next((i for i, n in api.items()
                         if n.get("class_type") in ("VAELoaderKJ", "VAELoader")
                         and "audio" not in i.lower()), None)
        if vae_node is not None:
            vae_src = [vae_node, 0]
    if vae_src is None:
        return None
    return cond_id, latent_src, vae_src, sampler_id


def _inject_ltx_guides(api, guides, length):
    """LTX-ONLY keyframe-guide conditioning (spatial feed-forward), additive + fail-open.

    Splice one LoadImage + one LTXVAddGuide per guide between the graph's LTXVConditioning and its
    sampler, mirroring the UI workflow's chain:
        LTXVConditioning -> AddGuide -> AddGuide -> ... -> sampler
    Each LTXVAddGuide pins `image` at video position `frame_idx` with `strength`, threading
    (positive, negative, latent) slot-for-slot (0,1,2). The single-anchor `first_frame_name` remains
    the seed; guides are an *extra* spatial pin on top of it.

    `guides` = ordered-by-t list of (frame_abs_path, t_seconds, tag). Mutates `api` IN PLACE only on
    full success; on ANY error logs and leaves `api` untouched (single anchor). `length` is the clamped
    LTX frame count, used to bound frame_idx into the clip.
    """
    if not guides:
        return api
    try:
        sp = _ltx_cond_latent_vae_sampler(api)
        if sp is None:
            print("LTX guides: could not identify conditioning/latent/vae/sampler — skipping (fail-open)")
            return api
        cond_id, latent_src, vae_src, sampler_id = sp
        # output frame rate from the LTXVConditioning node (fallback 24)
        fps = 24
        try:
            fr = api[cond_id]["inputs"].get("frame_rate")
            if isinstance(fr, (int, float)) and fr > 0:
                fps = float(fr)
        except Exception:
            pass
        os.makedirs(INPUT_DIR, exist_ok=True)
        # the anchor name seeds a collision-free base for the copied guide frames
        base = os.path.splitext(os.path.basename(api[next(
            (i for i, n in api.items() if n.get("class_type") == "LoadImage"), None)]
            ["inputs"]["image"]))[0] if any(
            n.get("class_type") == "LoadImage" for n in api.values()) else "guide"
        # the conditioning sources for the FIRST guide come from the LTXVConditioning outputs
        pos_src, neg_src, lat_src = [cond_id, 0], [cond_id, 1], list(latent_src)
        new_ids = set()                          # nodes WE add — never rewire these back onto themselves
        for i, g in enumerate(guides):
            frame_abs_path, t_seconds, tag = g
            name = f"{base}_guide{i}.png"
            shutil.copy(frame_abs_path, os.path.join(INPUT_DIR, name))
            tagl = (tag or "").strip().lower()
            # "hold" = continue FROM this composition -> pin at the clip START (frame 0); more/less/change
            # pin at their own timeline position as a reference keyframe. (This frame_idx mapping is the
            # knob to validate on a real LTX render via verify_guides.py.)
            frame_idx = 0 if tagl == "hold" else max(0, min(int(length) - 1, round(float(t_seconds) * fps)))
            strength = LTX_GUIDE_STRENGTH.get(tagl, 0.6)
            img_id, guide_id = f"g{i}_img", f"g{i}_guide"
            api[img_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
            api[guide_id] = {"class_type": "LTXVAddGuide", "inputs": {
                "positive": list(pos_src), "negative": list(neg_src),
                "vae": list(vae_src), "latent": list(lat_src),
                "image": [img_id, 0], "frame_idx": frame_idx, "strength": strength}}
            new_ids.add(img_id); new_ids.add(guide_id)
            # the next guide (and finally the sampler) chains from THIS guide's outputs
            pos_src, neg_src, lat_src = [guide_id, 0], [guide_id, 1], [guide_id, 2]
        # rewire the sampler's latent_image to the last guide's latent output
        api[sampler_id]["inputs"]["latent_image"] = list(lat_src)
        # rewire whatever ELSE consumed the conditioning (CFGGuider/STGGuider*) to the last guide's
        # pos/neg. Skip the guide nodes we just added (the first one legitimately reads [cond,0/1]).
        for nid, n in api.items():
            if nid in new_ids:
                continue
            ins = n.get("inputs", {})
            if isinstance(ins.get("positive"), list) and ins["positive"][:1] == [cond_id] \
               and ins["positive"][1] == 0:
                ins["positive"] = list(pos_src)
            if isinstance(ins.get("negative"), list) and ins["negative"][:1] == [cond_id] \
               and ins["negative"][1] == 1:
                ins["negative"] = list(neg_src)
        print(f"LTX guides: injected {len(guides)} LTXVAddGuide node(s) (fps={fps})")
        return api
    except Exception as e:
        print(f"LTX guide injection failed ({e}); proceeding with single anchor (fail-open)")
        return api


def _run_beat_ltx(prompt, first_frame_name, seed, length, timeout, output_prefix, guides=None):
    """LTX-2.3 / 10Eros i2v beat: reuse the verified build_10eros_i2v graph (GGUF + Gemma TE +
    LTX-2.3 VAEs, 768px, 28-step LTXVScheduler). The anchor frame lives in ComfyUI's input dir
    (lucid's convention); build() reads its aspect and sets a literal resolution + length. 10Eros is
    a single merged checkpoint, so the Wan LoRA/shift knobs don't apply here (content is prompt-driven;
    the rating still gates the prompt + steers beats upstream).

    `guides` (LTX-only spatial feed-forward) is an optional ordered list of (frame_abs_path, t, tag);
    when present we splice LTXVAddGuide nodes into the built graph (additive, fail-open) so the beat
    also conditions on user-tagged moments. The single-anchor `first_frame_name` stays the seed."""
    seed_path = os.path.join(INPUT_DIR, first_frame_name)
    out_prefix = output_prefix or ("lucid/" + os.path.splitext(first_frame_name)[0])
    clamped = _clamp_length_ltx(length)
    api, _rw, _bad = ltx.build(seed_path, prompt, LTX_LONGER, clamped,
                               out_prefix, steps=LTX_STEPS, seed=seed, gguf=LTX_GGUF)
    if guides:                                  # LTX-only keyframe guides (spatial feed-forward)
        api = _inject_ltx_guides(api, guides, clamped)
    files, _hist = cc.generate(api, timeout=timeout)
    if not files:
        raise RuntimeError("generation produced no video")
    return files[0], seed


def run_beat(prompt, first_frame_name, seed=None,
             w=DEFAULT_W, h=DEFAULT_H, length=DEFAULT_LEN, timeout=1800, output_prefix=None,
             rating="sfw", guides=None):
    """Parameterize the i2v workflow (UI Remix OR non-distilled API graph) and generate one clip.
    `rating` ('sfw'|'mature') sets the LOW-noise LoRA strength so a SFW continuation is NOT rendered
    by the explicit-anatomy graph (default SFW = LoRA off; the shipped path passes the sealed rating).
    ENGINE routes to the LTX-2.3 / 10Eros backend when selected (registry/env), Wan otherwise.

    `guides` (LTX-only) = ordered list of (frame_abs_path, t_seconds, tag in more|less|hold|change)
    that pin tagged moments into the clip via LTXVAddGuide (spatial feed-forward). Ignored on the Wan
    path; with guides=None/empty the behavior is byte-identical to before (additive, fail-open)."""
    if seed is None:
        seed = random.randint(1, 2**31)
    if current_engine() == "10eros":
        # pass `guides` only when present so the no-guide call is byte-identical to before
        if guides:
            return _run_beat_ltx(prompt, first_frame_name, seed, length, timeout, output_prefix,
                                 guides=guides)
        return _run_beat_ltx(prompt, first_frame_name, seed, length, timeout, output_prefix)
    length = clamp_length(length)   # defensive: never trust a caller-supplied frame count
    with open(WORKFLOW) as f:
        wf = json.load(f)
    if _is_api_graph(wf):                          # non-distilled GGUF graph (anti-melt) — drive directly
        api = dict(wf)
        _set_widgets_api(api, prompt, first_frame_name, seed, w, h, length, output_prefix,
                         lora_low=lora_low_for(rating))
    else:                                          # UI Remix graph — widgets then convert
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

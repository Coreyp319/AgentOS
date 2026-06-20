#!/usr/bin/env python3
"""AgentOS dreaming — minimal ComfyUI backend client.

ONE local generation backend, shared by both surfaces:
  - the ambient "dreaming" wallpaper (image-to-video loops), and
  - the on-demand KRunner action (text-to-video from a user prompt).

Pure stdlib (no extra deps) so agentosd / a KRunner D-Bus service can shell out
to it without a heavy venv. Talks to a running ComfyUI over its HTTP API.

Capabilities:
  - ui_to_api(workflow): convert a ComfyUI UI-format workflow (the kind the app
    saves / ships as a template) into the /prompt API graph format.
  - generate(api_prompt): submit, wait, return the produced video file path(s).
  - free_vram(): POST /free — the VRAM-yield lever agentosd uses so Ollama /
    nimbus-flux can reclaim the GPU (ties into ADR-0004 graphics-yield).

CLI:
  comfy_client.py run-template <template.json> --prompt "..." [--length N]
                  [--steps N] [--seed N] [--width W] [--height H]
                  [--prune-class CLS ...] [--out-prefix NAME]
  comfy_client.py free
"""
import argparse
import json
import os
import subprocess
import time
import uuid
import urllib.error
import urllib.request

HOST = os.environ.get("COMFY_HOST", "127.0.0.1:8188")
BASE = f"http://{HOST}"
COMFY_ROOT = os.environ.get("COMFY_ROOT", "/home/corey/ComfyUI")
OUTPUT_DIR = os.path.join(COMFY_ROOT, "output")

CONTROL_VALS = {"randomize", "fixed", "increment", "decrement"}
# Virtual "routing" nodes carry no compute — ComfyUI's own "Save (API)" inlines
# them. We must resolve them away too, or consumers point at unknown class_types
# and /prompt 400s. Reroute = 1-in/1-out passthrough; KJNodes Set/GetNode wire by
# a shared label (Get fetches whatever feeds the same-named Set).
VIRTUAL_ROUTING = {"Reroute", "Reroute (rgthree)", "SetNode", "GetNode"}
_OBJ_CACHE = None


def _get(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.load(r)


def _post(path, obj):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {path}: {body[:3000]}")


def object_info():
    global _OBJ_CACHE
    if _OBJ_CACHE is None:
        _OBJ_CACHE = _get("/object_info")
    return _OBJ_CACHE


def _is_widget(spec):
    # Combos appear either as [[opt,...], {}] (type is a list) or as
    # ["COMBO", {"options": [...]}] in newer ComfyUI. Both are widgets.
    t = spec[0] if isinstance(spec, list) else spec
    return isinstance(t, list) or t in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO")


def _widget_input_names(class_type):
    """Ordered (name, spec) of widget (non-connection) inputs for a class."""
    oi = object_info().get(class_type)
    if not oi:
        return []
    out = []
    inp = oi.get("input", {})
    for section in ("required", "optional"):
        for name, spec in inp.get(section, {}).items():
            if _is_widget(spec):
                out.append((name, spec))
    return out


def _required_widget_specs(class_type):
    oi = object_info().get(class_type)
    if not oi:
        return []
    return [(n, s) for n, s in oi.get("input", {}).get("required", {}).items()
            if _is_widget(s)]


def _widget_default(spec):
    t = spec[0] if isinstance(spec, list) else spec
    opts = spec[1] if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) else {}
    if "default" in opts:
        return opts["default"]
    if isinstance(t, list):
        return t[0] if t else None
    if t == "COMBO":
        o = opts.get("options") or []
        return o[0] if o else None
    return {"INT": 0, "FLOAT": 0.0, "BOOLEAN": False, "STRING": ""}.get(t)


def _virtual_name(n):
    """Label of a KJNodes SetNode/GetNode (its set/get-by-name key)."""
    wv = n.get("widgets_values")
    if isinstance(wv, list) and wv and isinstance(wv[0], str):
        return wv[0]
    if isinstance(wv, dict):
        for k in ("constant", "name", "previousName"):
            if isinstance(wv.get(k), str):
                return wv[k]
    props = n.get("properties") or {}
    return props.get("previousName") or props.get("constant")


def flatten_subgraphs(wf):
    """Inline subgraph instances into a flat UI-format workflow.

    Handles one level of nesting (the shape ComfyUI templates ship). Boundary
    inputs that the instance does not override fall back to each internal node's
    own widget default; the subgraph's output is rewired to the parent consumer.
    Parent-fed subgraph inputs (instance input with a link) are also rewired.
    """
    defs = wf.get("definitions", {}).get("subgraphs", [])
    if not defs:
        return wf
    sgmap = {sg["id"]: sg for sg in defs}
    wf = json.loads(json.dumps(wf))
    maxlink = max([l[0] for l in wf.get("links", [])] + [0])
    state = {"link": maxlink}

    def newlink():
        state["link"] += 1
        return state["link"]

    nodes = wf["nodes"]
    links = wf.get("links", [])
    # parent link index by id (for rewiring instance I/O)
    plink = {l[0]: l for l in links}

    new_nodes = []
    for node in nodes:
        sg = sgmap.get(node["type"])
        if not sg:
            new_nodes.append(node)
            continue
        inst = node["id"]
        pref = f"{inst}_"
        boundary = set()
        sg_in_links = {}  # subgraph-input-name -> [internal link ids]
        for inp in sg.get("inputs", []):
            for lid in inp.get("linkIds", []) or []:
                boundary.add(lid)
            sg_in_links[inp["name"]] = inp.get("linkIds", []) or []
        # value a parent provides for each subgraph input (via instance input link)
        parent_src_for = {}  # subgraph-input-name -> (src_node, src_slot)
        for inp in node.get("inputs", []):
            lk = inp.get("link")
            if lk is not None and lk in plink:
                l = plink[lk]
                parent_src_for[inp["name"]] = (l[1], l[2])
        # inline internal nodes (prefix ids, drop boundary links -> widget default)
        for n in sg.get("nodes", []):
            nn = json.loads(json.dumps(n))
            nn["id"] = f"{pref}{n['id']}"
            for ip in nn.get("inputs", []):
                if ip.get("link") in boundary:
                    ip["link"] = None
            new_nodes.append(nn)
        # internal links (skip boundary), remap ids + node ids
        for l in sg.get("links", []):
            if l["id"] in boundary:
                continue
            nl = newlink()
            links.append([nl, f"{pref}{l['origin_id']}", l["origin_slot"],
                          f"{pref}{l['target_id']}", l["target_slot"], l.get("type")])
            for nn in new_nodes:
                if nn["id"] == f"{pref}{l['target_id']}":
                    for ip in nn.get("inputs", []):
                        if ip.get("link") == l["id"]:
                            ip["link"] = nl
        # parent-fed boundary inputs: connect parent source to internal consumers
        for name, (psrc, pslot) in parent_src_for.items():
            for lid in sg_in_links.get(name, []):
                # find internal node whose input used boundary link `lid`
                for n in sg.get("nodes", []):
                    for ip in n.get("inputs", []):
                        if ip.get("link") == lid:
                            nl = newlink()
                            links.append([nl, psrc, pslot, f"{pref}{n['id']}",
                                          0, ip.get("type")])
                            for nn in new_nodes:
                                if nn["id"] == f"{pref}{n['id']}":
                                    for x in nn.get("inputs", []):
                                        if x.get("name") == ip.get("name"):
                                            x["link"] = nl
        # rewire subgraph outputs -> internal source, fix parent links from instance
        ilink = {l["id"]: l for l in sg.get("links", [])}
        outsrc = {}
        for k, o in enumerate(sg.get("outputs", [])):
            for lid in o.get("linkIds", []) or []:
                if lid in ilink:
                    l = ilink[lid]
                    outsrc[k] = (f"{pref}{l['origin_id']}", l["origin_slot"])
        for l in links:
            if l[1] == inst and l[2] in outsrc:
                l[1], l[2] = outsrc[l[2]]

    wf["nodes"] = new_nodes
    wf["links"] = links
    wf.pop("definitions", None)
    return wf


def ui_to_api(wf):
    """Convert a ComfyUI UI-format workflow dict to the /prompt API graph dict.

    Subgraph instances are inlined first.

    Handles: muted(2)/bypassed(4) nodes (dropped, their links pruned),
    Note/MarkdownNote (dropped), the seed `control_after_generate` companion
    value interleaved in widgets_values, and widget-vs-link input ordering.
    """
    wf = flatten_subgraphs(wf)
    nodes = {n["id"]: n for n in wf["nodes"]}
    skip_types = {"Note", "MarkdownNote"}
    included = {
        nid: n
        for nid, n in nodes.items()
        if n.get("mode", 0) == 0
        and n["type"] not in skip_types
        and n["type"] not in VIRTUAL_ROUTING
    }
    links = {}
    for l in wf.get("links", []):
        # [link_id, src_node, src_slot, dst_node, dst_slot, type]
        links[l[0]] = (l[1], l[2])

    # Map each Set label -> SetNode id so a GetNode resolves to the Set's source.
    set_by_name = {}
    for _nid, _n in nodes.items():
        if _n["type"] == "SetNode":
            _nm = _virtual_name(_n)
            if _nm is not None:
                set_by_name[_nm] = _nid

    def virtual_src(node_id):
        """Immediate upstream a virtual routing node forwards to: a GetNode points
        at its matching SetNode (resolved onward by resolve_src); Reroute/SetNode
        pass through their single linked input."""
        n = nodes[node_id]
        if n["type"] == "GetNode":
            sn = set_by_name.get(_virtual_name(n))
            return (sn, 0) if sn is not None else None
        for ip in n.get("inputs", []):
            lk = ip.get("link")
            if lk in links:
                return links[lk]
        return None

    def resolve_src(node_id, slot, seen=None):
        """Resolve a link source to an included node, passing through bypassed
        (mode 4) and virtual routing (Reroute/Set/Get) nodes. Returns
        (node_id, slot) or None."""
        if node_id in included:
            return (node_id, slot)
        n = nodes.get(node_id)
        if n is None:
            return None
        seen = seen or set()
        if node_id in seen:
            return None
        seen.add(node_id)
        if n["type"] in VIRTUAL_ROUTING:  # routing node: forward to the real source
            vs = virtual_src(node_id)
            if not vs or vs[0] is None:
                return None
            return resolve_src(vs[0], vs[1], seen)
        if n.get("mode", 0) == 4:  # bypass -> pass-through by matching type
            outs = n.get("outputs", [])
            otype = outs[slot].get("type") if slot < len(outs) else None
            same_out = [i for i, o in enumerate(outs) if o.get("type") == otype]
            k = same_out.index(slot) if slot in same_out else 0
            cand = [ip for ip in n.get("inputs", [])
                    if ip.get("type") == otype and ip.get("link") in links]
            if not cand:
                return None
            ip = cand[k] if k < len(cand) else cand[0]
            s_node, s_slot = links[ip["link"]]
            return resolve_src(s_node, s_slot, seen)
        return None  # muted / skipped

    api = {}
    for nid, n in included.items():
        ct = n["type"]
        entry = {"class_type": ct, "inputs": {}}
        link_names = set()
        for inp in n.get("inputs", []):
            lk = inp.get("link")
            if lk is None or lk not in links:
                continue
            src = resolve_src(*links[lk])
            if src is None:  # upstream dropped/unbridgeable
                continue
            entry["inputs"][inp["name"]] = [str(src[0]), src[1]]
            link_names.add(inp["name"])

        wv = n.get("widgets_values")
        if wv is not None and not isinstance(wv, dict):
            # widgets_values aligns with ALL widget inputs in object_info order,
            # INCLUDING widgets "converted to inputs" (their slot is still present
            # in widgets_values; the link just overrides at runtime). So consume
            # every slot for alignment, but only set inputs that aren't linked.
            i = 0
            for (nm, spec) in _widget_input_names(ct):
                if i >= len(wv):
                    break
                val = wv[i]
                i += 1
                if nm not in link_names:
                    entry["inputs"][nm] = val
                opts = spec[1] if isinstance(spec, list) and len(spec) > 1 and \
                    isinstance(spec[1], dict) else {}
                if nm in ("seed", "noise_seed") or opts.get("control_after_generate"):
                    if i < len(wv) and isinstance(wv[i], str) and wv[i] in CONTROL_VALS:
                        i += 1
        elif isinstance(wv, dict):
            for nm, _spec in _widget_input_names(ct):
                if nm in wv and nm not in link_names:
                    entry["inputs"][nm] = wv[nm]

        # backfill any REQUIRED widget input the template omitted (version drift)
        for nm, spec in _required_widget_specs(ct):
            if nm in entry["inputs"] or nm in link_names:
                continue
            dv = _widget_default(spec)
            if dv is not None:
                entry["inputs"][nm] = dv
        api[str(nid)] = entry
    return api


# ---- graph helpers (used for test overrides + branch pruning) ----
def nodes_of(api, class_type):
    return [nid for nid, n in api.items() if n["class_type"] == class_type]


def _text_node_for(api, ref, depth=4):
    """Walk back from a conditioning ref [node_id, slot] to the CLIPTextEncode
    that feeds it (positive/negative may pass through guidance/combine nodes)."""
    if not isinstance(ref, list) or not ref:
        return None
    nid = str(ref[0])
    n = api.get(nid)
    if not n:
        return None
    if n["class_type"] == "CLIPTextEncode":
        return nid
    if depth <= 0:
        return None
    for v in n["inputs"].values():
        found = _text_node_for(api, v, depth - 1)
        if found:
            return found
    return None


def pos_neg_text_nodes(api):
    """(positive_node_id, negative_node_id), resolved via whatever node carries
    the conditioning (KSampler, or a CFGGuider behind SamplerCustomAdvanced)."""
    for nid, n in api.items():
        ins = n["inputs"]
        if "positive" in ins and "negative" in ins:
            p = _text_node_for(api, ins.get("positive"))
            ng = _text_node_for(api, ins.get("negative"))
            if p or ng:
                return p, ng
    cte = nodes_of(api, "CLIPTextEncode")
    return (cte[0] if cte else None, cte[1] if len(cte) > 1 else None)


def set_input(api, class_type, name, value, which=0):
    ids = nodes_of(api, class_type)
    if not ids:
        return False
    api[ids[which]]["inputs"][name] = value
    return True


def prune_class(api, class_type):
    """Remove all nodes of a class and any inputs pointing at them
    (used to drop e.g. a super-res branch for a faster test)."""
    drop = set(nodes_of(api, class_type))
    for nid in drop:
        api.pop(nid, None)
    for n in api.values():
        for k in list(n["inputs"].keys()):
            v = n["inputs"][k]
            if isinstance(v, list) and len(v) == 2 and str(v[0]) in drop:
                n["inputs"].pop(k)
    return len(drop)


# ---- headless output node ----
def has_output_node(api):
    """True if any node is a ComfyUI OUTPUT_NODE (Save*/Preview*). Uses /object_info."""
    oi = object_info()
    return any((oi.get(n["class_type"]) or {}).get("output_node") for n in api.values())


def ensure_output(api, prefix="ComfyUI"):
    """Some templates (ComfyUI subgraph "blueprints") output a VIDEO/IMAGE but ship no
    Save node, so /prompt rejects them with `prompt_no_outputs`. When run headless we
    add the missing Save node: SaveVideo behind a CreateVideo, else SaveImage behind a
    VAEDecode. Returns the class added, or None if an output node already exists."""
    if has_output_node(api):
        return None
    cv = nodes_of(api, "CreateVideo")
    if cv:
        api["save_video"] = {
            "class_type": "SaveVideo",
            "inputs": {"video": [cv[0], 0], "filename_prefix": prefix,
                       "format": "auto", "codec": "auto"},
        }
        return "SaveVideo"
    vd = nodes_of(api, "VAEDecode")
    if vd:
        api["save_image"] = {
            "class_type": "SaveImage",
            "inputs": {"images": [vd[0], 0], "filename_prefix": prefix},
        }
        return "SaveImage"
    return None


# ---- run ----
def submit(api_prompt, client_id=None):
    client_id = client_id or uuid.uuid4().hex
    r = _post("/prompt", {"prompt": api_prompt, "client_id": client_id})
    if "prompt_id" not in r:
        raise RuntimeError(f"/prompt rejected: {json.dumps(r)[:1000]}")
    return r["prompt_id"], client_id


def wait(prompt_id, timeout=3600, poll=2.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = _get(f"/history/{prompt_id}")
        if prompt_id in h:
            return h[prompt_id]
        time.sleep(poll)
    raise TimeoutError(f"generation {prompt_id} did not finish in {timeout}s")


def output_files(history):
    out = []
    for _node_id, o in history.get("outputs", {}).items():
        for key in ("images", "gifs", "videos"):
            for item in o.get(key, []):
                if not isinstance(item, dict):
                    continue
                fn = item.get("filename", "")
                sub = item.get("subfolder", "")
                if fn:
                    out.append(os.path.join(OUTPUT_DIR, sub, fn))
    return out


VIDEO_EXTS = (".mp4", ".webm", ".gif", ".mkv", ".webp")


def _newest_video(since=0.0):
    newest, mt = None, since
    for root, _d, fns in os.walk(OUTPUT_DIR):
        for fn in fns:
            if os.path.splitext(fn)[1].lower() in VIDEO_EXTS:
                p = os.path.join(root, fn)
                m = os.path.getmtime(p)
                if m > mt:
                    newest, mt = p, m
    return [newest] if newest else []


def generate(api_prompt, timeout=3600):
    start = time.time()
    pid, _ = submit(api_prompt)
    hist = wait(pid, timeout=timeout)
    status = hist.get("status", {})
    if status.get("status_str") == "error":
        raise RuntimeError(f"generation errored: {json.dumps(status)[:1500]}")
    files = [p for p in output_files(hist)
             if os.path.splitext(p)[1].lower() in VIDEO_EXTS and os.path.exists(p)]
    if not files:  # fallback: newest video written during this run
        files = _newest_video(since=start - 1)
    return files, hist


def free_vram():
    """Unload models + free VRAM. The /free endpoint returns an empty body,
    so we don't parse JSON — just require a 2xx."""
    data = json.dumps({"unload_models": True, "free_memory": True}).encode()
    req = urllib.request.Request(
        BASE + "/free", data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    rt = sub.add_parser("run-template")
    rt.add_argument("template")
    rt.add_argument("--prompt", default=None)
    rt.add_argument("--negative", default=None)
    rt.add_argument("--length", type=int, default=None)
    rt.add_argument("--steps", type=int, default=None)
    rt.add_argument("--seed", type=int, default=None)
    rt.add_argument("--width", type=int, default=None)
    rt.add_argument("--height", type=int, default=None)
    rt.add_argument("--out-prefix", default=None)
    rt.add_argument("--prune-class", action="append", default=[])
    rt.add_argument("--set", action="append", default=[],
                    help="CLASS.input=value (json value)")
    rt.add_argument("--timeout", type=int, default=3600)
    rt.add_argument("--no-ensure-save", action="store_true",
                    help="don't auto-add a Save node when the template lacks one")
    rt.add_argument("--dump-api", action="store_true")
    rt.add_argument("--open", action="store_true",
                    help="on success, desktop-notify and xdg-open the result")
    sub.add_parser("free")
    args = ap.parse_args()

    if args.cmd == "free":
        print("freed" if free_vram() else "free failed")
        return

    wf = json.load(open(args.template))
    api = ui_to_api(wf)

    for cls in args.prune_class:
        n = prune_class(api, cls)
        print(f"[prune] {cls}: removed {n} node(s)")

    # positive/negative resolved through the sampler links (order-independent)
    pos_node, neg_node = pos_neg_text_nodes(api)
    if args.prompt is not None and pos_node:
        api[pos_node]["inputs"]["text"] = args.prompt
    if args.negative is not None and neg_node:
        api[neg_node]["inputs"]["text"] = args.negative

    # common knobs across Wan/Hunyuan latent + sampler nodes
    for latent_cls in ("Wan22ImageToVideoLatent", "EmptyHunyuanLatentVideo",
                        "EmptyHunyuanLatent", "EmptyHunyuanVideo15Latent",
                        "HunyuanVideo15EmptyLatentVideo",
                        "EmptyLTXVLatentVideo", "EmptyCogVideoLatentVideo"):
        if args.length is not None:
            set_input(api, latent_cls, "length", args.length)
        if args.width is not None:
            set_input(api, latent_cls, "width", args.width)
        if args.height is not None:
            set_input(api, latent_cls, "height", args.height)
    for samp in ("KSampler", "KSamplerAdvanced", "SamplerCustom", "BasicScheduler"):
        if args.steps is not None:
            set_input(api, samp, "steps", args.steps)
    for noise_cls in ("KSampler", "KSamplerAdvanced", "SamplerCustom", "RandomNoise"):
        if args.seed is not None:
            set_input(api, noise_cls, "seed", args.seed)
            set_input(api, noise_cls, "noise_seed", args.seed)
    if args.out_prefix:
        for sv in ("SaveVideo", "VHS_VideoCombine", "SaveWEBM"):
            set_input(api, sv, "filename_prefix", args.out_prefix)
            set_input(api, sv, "filename", args.out_prefix)

    for s in args.set:
        key, _, val = s.partition("=")
        cls, _, inp = key.partition(".")
        try:
            val = json.loads(val)
        except Exception:
            pass
        set_input(api, cls, inp, val)

    if not args.no_ensure_save:
        added = ensure_output(api, args.out_prefix or "ComfyUI")
        if added:
            print(f"[ensure-save] template had no output node; added {added}")

    if args.dump_api:
        print(json.dumps(api, indent=1))
        return

    t0 = time.time()
    try:
        files, _hist = generate(api, timeout=args.timeout)
    except Exception as e:
        if args.open:
            subprocess.Popen(["notify-send", "-a", "AgentOS", "Video failed", str(e)[:200]])
        raise
    dt = time.time() - t0
    print(f"[done] {dt:.0f}s -> {files}")
    if args.open and files:
        subprocess.Popen(["notify-send", "-a", "AgentOS", "Video ready", files[0]])
        subprocess.Popen(["xdg-open", files[0]])


if __name__ == "__main__":
    _main()

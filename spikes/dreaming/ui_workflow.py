#!/usr/bin/env python3
"""api_to_ui: turn a /prompt API graph into a ComfyUI **canvas** workflow (.json
you drag into the UI), the inverse of comfy_client.ui_to_api.

Used to ship a user-friendly, tweakable 10Eros I2V workflow into ComfyUI's
workflow browser. Correctness is checked by round-tripping: ui_to_api(result)
must reproduce the original graph's wiring + the literals we set.

Layout is auto (longest-path columns). Widget-converted-to-input slots keep both
an inputs[] entry (with a `widget` marker) and their widgets_values slot, matching
how ComfyUI serialises them (and what ui_to_api expects).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comfy_client as cc          # noqa: E402
import build_10eros_i2v as b       # noqa: E402

CONTROL = {"seed", "noise_seed"}
TITLES = {
    "LoadImage": "① Seed image  (drop yours here)",
    "CLIPTextEncode": "② Prompt  (describe the motion)",
    "EmptyLTXVLatentVideo": "③ Length & size  (length ≤121 ≈ 5s)",
    "ResizeImagesByLongerEdge": "Resolution  (longer edge)",
    "UnetLoaderGGUF": "10Eros model (GGUF)",
    "DualCLIPLoaderGGUF": "Text encoder (Gemma + projection)",
    "SaveVideo": "Output video",
}
SIZES = {"CLIPTextEncode": [340, 200], "LoadImage": [300, 340],
         "SaveVideo": [340, 320], "VHS_VideoCombine": [340, 360]}


def _io_order(ct):
    """[(name, spec, is_widget)] in object_info order (required then optional)."""
    oi = cc.object_info().get(ct, {})
    out = []
    for sec in ("required", "optional"):
        for name, spec in oi.get("input", {}).get(sec, {}).items():
            out.append((name, spec, cc._is_widget(spec)))
    return out


def _outputs(ct):
    oi = cc.object_info().get(ct, {})
    types = oi.get("output", []) or []
    names = oi.get("output_name") or types
    return list(zip(names, types))


def api_to_ui(api):
    ids = list(api.keys())
    new_id = {old: i + 1 for i, old in enumerate(ids)}   # stable int ids

    def is_ref(v):
        return (isinstance(v, list) and len(v) == 2
                and isinstance(v[1], int) and str(v[0]) in api)

    nodes, links = {}, []
    link_seq = [0]

    # first pass: build node skeletons (inputs[], outputs[], widgets_values[])
    for old, n in api.items():
        ct = n["class_type"]
        io = _io_order(ct)
        conn = [(nm, sp) for nm, sp, w in io if not w]
        widgets = [(nm, sp) for nm, sp, w in io if w]
        node_in = {k: v for k, v in n["inputs"].items() if is_ref(v)}
        literals = {k: v for k, v in n["inputs"].items() if not is_ref(v)}
        conv = [nm for nm, _sp in widgets if nm in node_in]   # widget->input

        in_slots, slot_index = [], {}
        for nm, sp in conn:
            tp = sp[0] if isinstance(sp, list) else sp
            slot_index[nm] = len(in_slots)
            in_slots.append({"name": nm, "type": tp, "link": None})
        for nm in conv:
            sp = dict(widgets).get(nm)
            tp = sp[0] if isinstance(sp, list) else sp
            slot_index[nm] = len(in_slots)
            in_slots.append({"name": nm, "type": tp, "link": None,
                             "widget": {"name": nm}})
        # dynamic/variadic inputs (e.g. image_1, image_2 on a switch) that aren't
        # in object_info's static input list — append a slot so links can attach.
        for nm in node_in:
            if nm not in slot_index:
                slot_index[nm] = len(in_slots)
                in_slots.append({"name": nm, "type": "*", "link": None})

        wv = []
        for nm, sp in widgets:
            wv.append(literals.get(nm, cc._widget_default(sp)))
            opts = sp[1] if isinstance(sp, list) and len(sp) > 1 \
                and isinstance(sp[1], dict) else {}
            if nm in CONTROL or opts.get("control_after_generate"):
                wv.append("fixed")

        out_slots = [{"name": onm, "type": otp, "links": [], "slot_index": i}
                     for i, (onm, otp) in enumerate(_outputs(ct))]

        node = {"id": new_id[old], "type": ct, "pos": [0, 0],
                "size": SIZES.get(ct, [270, 140]), "flags": {}, "order": 0,
                "mode": 0, "inputs": in_slots, "outputs": out_slots,
                "properties": {"Node name for S&R": ct}, "widgets_values": wv}
        if ct in TITLES:
            node["title"] = TITLES[ct]
        nodes[old] = (node, slot_index, node_in)

    # second pass: wire links
    for old, (node, slot_index, node_in) in nodes.items():
        for nm, ref in node_in.items():
            src_old, src_slot = str(ref[0]), ref[1]
            link_seq[0] += 1
            lid = link_seq[0]
            src_node = nodes[src_old][0]
            otype = (src_node["outputs"][src_slot]["type"]
                     if src_slot < len(src_node["outputs"]) else "*")
            node["inputs"][slot_index[nm]]["link"] = lid
            src_node["outputs"][src_slot]["links"].append(lid)
            links.append([lid, new_id[src_old], src_slot,
                          new_id[old], slot_index[nm], otype])

    # auto-layout: longest-path columns
    succ = {}
    for l in links:
        succ.setdefault(l[1], []).append(l[3])
    depth = {}

    def d(nid, seen=()):
        if nid in depth:
            return depth[nid]
        ins = [l[1] for l in links if l[3] == nid]
        depth[nid] = 0 if not ins else 1 + max(
            (d(i, seen + (nid,)) for i in ins if i not in seen), default=-1)
        return depth[nid]
    for node, _si, _ni in nodes.values():
        d(node["id"])
    col_count = {}
    for node, _si, _ni in nodes.values():
        col = depth.get(node["id"], 0)
        row = col_count.get(col, 0)
        col_count[col] = row + 1
        node["pos"] = [60 + col * 360, 40 + row * 230]

    node_list = [n for n, _si, _ni in nodes.values()]
    # a friendly instruction card
    node_list.append({
        "id": len(node_list) + 1, "type": "MarkdownNote", "pos": [60, -180],
        "size": [560, 180], "flags": {}, "order": 0, "mode": 0,
        "inputs": [], "outputs": [], "title": "How to use — 10Eros I2V",
        "properties": {}, "widgets_values": [
            "**10Eros (LTX-2.3) image→video** — AgentOS / local-video-gen\n\n"
            "1. **① Seed image**: load your start frame.\n"
            "2. **② Prompt**: describe the *motion* (LTX needs explicit motion).\n"
            "3. **③ Length**: keep ≤121 (≈5s) — longer melts.\n"
            "4. Free Ollama first (`ollama stop`), then **Queue**.\n\n"
            "GGUF Q4_K_M + Gemma TE + LTX-2.3 VAEs. ~85s on a 4090."]})

    return {"last_node_id": len(node_list) + 1, "last_link_id": link_seq[0],
            "nodes": node_list, "links": links, "groups": [],
            "config": {}, "extra": {}, "version": 0.4}, new_id


def _wiring(api):
    cls = {nid: n["class_type"] for nid, n in api.items()}
    edges = []
    for nid, n in api.items():
        for k, v in n["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and str(v[0]) in cls:
                edges.append((n["class_type"], k, cls[str(v[0])], v[1]))
    return sorted(edges)


def verify(orig_api, ui):
    rt = cc.ui_to_api(ui)
    same_classes = (sorted(n["class_type"] for n in orig_api.values())
                    == sorted(n["class_type"] for n in rt.values()))
    same_wiring = _wiring(orig_api) == _wiring(rt)
    # key literals we set must survive
    blob = json.dumps(rt)
    keep = [b._pick_gguf(), "ltx-2.3_text_projection_bf16.safetensors",
            "LTX23_video_vae_bf16.safetensors", "10eros_seed.png"]
    lits = all(s in blob for s in keep)
    return same_classes, same_wiring, lits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-image", default=os.path.expanduser("~/ComfyUI/input/10eros_seed.png"))
    ap.add_argument("--length", type=int, default=97)
    ap.add_argument("--longer-edge", type=int, default=768)
    ap.add_argument("--out", default=os.path.expanduser(
        "~/ComfyUI/user/default/workflows/10Eros_I2V_AgentOS.json"))
    a = ap.parse_args()

    api, _rw, _bad = b.build(a.seed_image,
                             "A gentle, natural motion; cinematic, photorealistic.",
                             a.longer_edge, a.length, "10eros_i2v")
    ui, _map = api_to_ui(api)
    sc, sw, lits = verify(api, ui)
    print(f"round-trip: classes={'OK' if sc else 'FAIL'} "
          f"wiring={'OK' if sw else 'FAIL'} literals={'OK' if lits else 'FAIL'}")
    if not (sc and sw and lits):
        print("NOT writing (round-trip failed)")
        sys.exit(1)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(ui, open(a.out, "w"), indent=1)
    print(f"wrote {a.out}  ({len(ui['nodes'])} nodes, {len(ui['links'])} links)")


if __name__ == "__main__":
    main()

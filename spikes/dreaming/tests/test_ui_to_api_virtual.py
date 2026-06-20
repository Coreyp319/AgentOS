#!/usr/bin/env python3
"""Regression: ui_to_api resolves virtual routing nodes (Reroute / KJNodes Set/Get).

ComfyUI's UI graph wires many links *through* virtual nodes that carry no compute
(reroutes, and KJNodes Set/GetNode "wire by name"). ComfyUI's own "Save (API)"
inlines them; before this pass `ui_to_api` emitted them as real nodes, so any input
routed through a Get/Set pointed at an unknown class_type and /prompt 400'd.

Fixture: TenStrip's LTX2.3-10Eros I2V workflow (24 Set/Get nodes, 20 through-links).
Hermetic — stubs object_info() so it needs no running ComfyUI.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import comfy_client as cc  # noqa: E402

WF = os.path.join(os.path.dirname(HERE), "workflows",
                  "10Eros_10SNodes_I2V_v3_TiledSampler.json")


def _api():
    cc._OBJ_CACHE = {}  # link/virtual resolution needs no object_info
    return cc.ui_to_api(json.load(open(WF)))


def test_fixture_has_virtual_nodes():
    wf = json.load(open(WF))
    n = sum(1 for x in wf["nodes"] if x["type"] in cc.VIRTUAL_ROUTING)
    assert n >= 20, f"expected the 10Eros graph to use Set/Get heavily, found {n}"


def test_no_virtual_nodes_emitted():
    api = _api()
    leaked = sorted({n["class_type"] for n in api.values()
                     if n["class_type"] in cc.VIRTUAL_ROUTING})
    assert not leaked, f"virtual routing nodes leaked into api graph: {leaked}"


def test_no_input_routed_through_virtual_node():
    wf = json.load(open(WF))
    virt_ids = {str(n["id"]) for n in wf["nodes"]
                if n["type"] in cc.VIRTUAL_ROUTING}
    api = _api()
    dangling = [(nid, n["class_type"], name, v)
                for nid, n in api.items()
                for name, v in n["inputs"].items()
                if isinstance(v, list) and len(v) == 2 and str(v[0]) in virt_ids]
    assert not dangling, f"inputs still routed through virtual nodes: {dangling[:5]}"


def test_all_input_refs_point_to_existing_nodes():
    """Every resolved link must target a node present in the api graph."""
    api = _api()
    ids = set(api.keys())
    bad = [(nid, n["class_type"], name, v)
           for nid, n in api.items()
           for name, v in n["inputs"].items()
           if isinstance(v, list) and len(v) == 2
           and isinstance(v[0], str) and v[0].isdigit() and v[0] not in ids]
    assert not bad, f"dangling input refs to missing nodes: {bad[:5]}"


def _run():
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    print(f"\n{'OK' if not fails else str(fails) + ' FAILED'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    _run()

#!/usr/bin/env python3
"""Integration tests for the ADR-0037 grounding-gates WIRING (model-free, no GPU/server).

The pure dispose is pinned by test_lucid_ground_canon.py; these pin the lucid_linear/lucid_web SEAMS the
on-box review flagged: the per-node canon fold (_canon_for) with branch isolation + the VRAM-headroom skip
+ the cache, and the /api/state egress strip (_strip_canon) being recursive and non-mutating."""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_linear as L   # noqa: E402
import lucid_web as W      # noqa: E402


def _fake_delta(record):
    def f(prior, label, caption, *, seed=False):
        record.append((label, seed))
        # a NEW prop each call; subjects only on the seed (so we can see inheritance vs. fresh)
        return {"add_subjects": (["a keeper"] if seed else []), "add_props": ["ship"],
                "synopsis_suffix": (label or "opens")}
    return f


def _has_canon_key(o):
    if isinstance(o, dict):
        return "canon" in o or any(_has_canon_key(v) for v in o.values())
    if isinstance(o, list):
        return any(_has_canon_key(v) for v in o)
    return False


class CanonFor(unittest.TestCase):
    def setUp(self):
        self._free = L.E._comfy_free_mib
        L.E._comfy_free_mib = lambda: None    # cold ComfyUI -> VRAM free -> the headroom gate lets it run
        self._enabled = L.CANON_ENABLED
        L.CANON_ENABLED = True

    def tearDown(self):
        L.E._comfy_free_mib = self._free
        L.CANON_ENABLED = self._enabled

    def test_root_is_a_seed_pass(self):
        rec = []
        chain = {"nodes": [{"id": 0, "parent": None, "label": "opening"}]}
        c0 = L._canon_for(chain, chain["nodes"][0], "a keeper at dawn", delta_fn=_fake_delta(rec))
        self.assertEqual(rec, [("opening", True)])           # seed=True at the root
        self.assertIn("a keeper", c0["facts"]["subjects"])
        self.assertEqual(c0["facts"]["time_of_day"], "dawn")  # code-disposed from the caption

    def test_child_folds_parent_canon_and_code_tracks_when(self):
        rec = []
        parent_canon = {"synopsis": "keeper at dusk",
                        "facts": {"subjects": ["a keeper"], "place": None,
                                  "time_of_day": "dusk", "mood": None, "props": ["lantern"]}}
        chain = {"nodes": [{"id": 0, "parent": None, "label": "opening", "canon": parent_canon},
                           {"id": 1, "parent": 0, "label": "A ship appears"}]}
        c1 = L._canon_for(chain, chain["nodes"][1], "a tall ship on the horizon at night",
                          delta_fn=_fake_delta(rec))
        self.assertEqual(rec, [("A ship appears", False)])    # a DELTA (parent has canon), not a seed
        self.assertIn("a keeper", c1["facts"]["subjects"])    # inherited from the parent's canon
        self.assertIn("ship", c1["facts"]["props"])
        self.assertEqual(c1["facts"]["time_of_day"], "night")  # code re-extracts the new time

    def test_sibling_isolation_parent_not_mutated(self):
        parent_canon = {"synopsis": "s", "facts": {"subjects": ["a keeper"], "place": None,
                                                   "time_of_day": "dusk", "mood": None, "props": []}}
        chain = {"nodes": [{"id": 0, "parent": None, "label": "opening", "canon": parent_canon}]}
        before = copy.deepcopy(parent_canon)
        L._canon_for(chain, {"id": 1, "parent": 0, "label": "A"}, "a ship at night", delta_fn=_fake_delta([]))
        L._canon_for(chain, {"id": 2, "parent": 0, "label": "B"}, "rain at dawn", delta_fn=_fake_delta([]))
        self.assertEqual(chain["nodes"][0]["canon"], before)  # neither fold touched the shared parent

    def test_cached_canon_short_circuits_no_reroll(self):
        node = {"id": 5, "parent": None, "canon": {"facts": {"subjects": ["x"]}}}

        def boom(*a, **k):
            raise AssertionError("must not roll the model when a canon is already cached")
        c = L._canon_for({"nodes": [node]}, node, "cap", delta_fn=boom)
        self.assertIs(c, node["canon"])

    def test_low_vram_headroom_skips_the_model(self):
        L.E._comfy_free_mib = lambda: 100     # below CANON_HEADROOM_MIB -> additive load would risk OOM
        c = L._canon_for({"nodes": [{"id": 0, "parent": None, "label": "opening"}]},
                         {"id": 0, "parent": None, "label": "opening"}, "a keeper",
                         delta_fn=lambda *a, **k: self.fail("must not roll under low headroom"))
        self.assertIsNone(c)

    def test_killswitch_and_no_caption_are_noops(self):
        L.CANON_ENABLED = False
        self.assertIsNone(L._canon_for({"nodes": []}, {"id": 0}, "cap", delta_fn=_fake_delta([])))
        L.CANON_ENABLED = True
        self.assertIsNone(L._canon_for({"nodes": []}, {"id": 0}, "", delta_fn=_fake_delta([])))

    def test_exception_is_fail_open_none(self):
        c = L._canon_for({"nodes": []}, {"id": 0, "parent": None, "label": "x"}, "cap",
                         delta_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ollama down")))
        self.assertIsNone(c)                  # any error -> None -> steering falls back to labels


class StripCanonEgress(unittest.TestCase):
    def test_recursive_strip_chain_node_and_prev(self):
        chain = {"name": "d", "canon": {"x": 1},
                 "nodes": [{"id": 0, "canon": {"y": 2}, "palette": "steady",
                            "prev": {"id": 0, "canon": {"z": 3}}},   # a reverted node's backup
                           {"id": 1}]}
        view = W._strip_canon(chain)
        self.assertFalse(_has_canon_key(view))                # no 'canon' at ANY depth
        self.assertEqual(view["nodes"][0]["palette"], "steady")  # the non-content flag is kept
        self.assertEqual(view["name"], "d")

    def test_does_not_mutate_the_live_chain(self):
        chain = {"canon": {"x": 1}, "nodes": [{"id": 0, "canon": {"y": 2}, "prev": {"canon": {"z": 3}}}]}
        W._strip_canon(chain)
        self.assertEqual(chain["canon"], {"x": 1})
        self.assertEqual(chain["nodes"][0]["canon"], {"y": 2})
        self.assertEqual(chain["nodes"][0]["prev"]["canon"], {"z": 3})

    def test_none_passthrough(self):
        self.assertIsNone(W._strip_canon(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)

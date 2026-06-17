#!/usr/bin/env python3
"""Unit tests for the HELD per-frame beat menu (ADR-0015 §1: "no reroll").

`beats_for_tip` rolls the non-deterministic LLM ONCE per chain tip, persists the menu on the tip
node, and re-serves it verbatim — so the "what happens next" suggestions are held until the chain
advances (a clip the user picked is generated + appended). No model/GPU/daemon: `propose`,
`context_for`, and the chain store are stubbed, so this exercises only the hold logic.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_linear as L


class HeldBeatsTest(unittest.TestCase):
    def setUp(self):
        # in-memory chain + a counting, deterministic propose() stub (each roll is distinct)
        self.chain = {"session": "t", "private": False, "nodes": [
            {"id": 0, "parent": None, "label": "opening", "prompt": None,
             "seed": None, "clip": None, "out_frame": "t_n0.png"}]}
        self.saves = 0
        self.rolls = 0
        self._orig = {k: getattr(L, k) for k in ("propose", "context_for", "load_chain", "save_chain")}
        L.load_chain = lambda s: self.chain
        L.context_for = lambda s: "ctx"

        def _save(s, c):
            self.saves += 1
            self.chain = c

        def _roll(ctx, n=4):
            self.rolls += 1
            return [{"label": f"beat{self.rolls}", "prompt": f"p{self.rolls}"}]

        L.save_chain = _save
        L.propose = _roll

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)

    def test_rolls_once_then_holds(self):
        a = L.beats_for_tip("t")
        b = L.beats_for_tip("t")
        self.assertEqual(a, b)                                 # same menu, held
        self.assertEqual(self.rolls, 1)                        # model proposed exactly once for this frame
        self.assertEqual(self.chain["nodes"][-1]["beats"], a)  # persisted on the tip node

    def test_new_tip_rerolls_and_old_is_sealed(self):
        a = L.beats_for_tip("t")
        # advance the chain: the picked clip is generated + appended -> a new tip with no menu yet
        self.chain["nodes"].append({"id": 1, "parent": 0, "label": "x", "prompt": "p",
                                    "seed": None, "clip": "c.mp4", "out_frame": "t_n1.png"})
        b = L.beats_for_tip("t")
        self.assertNotEqual(a, b)                              # a NEW frame gets a fresh menu
        self.assertEqual(self.rolls, 2)
        self.assertEqual(self.chain["nodes"][0]["beats"], a)   # the old tip's menu stays sealed

    def test_roll_false_never_calls_model(self):
        self.assertEqual(L.beats_for_tip("t", roll=False), [])  # nothing rolled, and roll=False must not roll
        self.assertEqual(self.rolls, 0)
        L.beats_for_tip("t")                                    # roll once
        self.assertEqual(self.rolls, 1)
        again = L.beats_for_tip("t", roll=False)                # in-flight read serves the held menu
        self.assertEqual(again, self.chain["nodes"][-1]["beats"])
        self.assertEqual(self.rolls, 1)

    def test_transient_empty_roll_is_not_sealed(self):
        L.propose = lambda ctx, n=4: []                         # Ollama momentarily down
        self.assertEqual(L.beats_for_tip("t"), [])
        self.assertNotIn("beats", self.chain["nodes"][-1])      # an empty roll is NOT pinned onto the frame
        self.assertEqual(self.saves, 0)
        L.propose = lambda ctx, n=4: [{"label": "ok", "prompt": "go"}]  # recovery
        second = L.beats_for_tip("t")
        self.assertEqual(second, [{"label": "ok", "prompt": "go"}])
        self.assertEqual(self.chain["nodes"][-1]["beats"], second)  # a real roll seals normally


class StepSupersedeTest(unittest.TestCase):
    """step()'s is_current gate: a turn superseded mid-beat (session restarted/deleted) must NOT
    persist its node — writing the stale in-memory chain back would resurrect deleted data."""
    def setUp(self):
        self._orig = {k: getattr(L, k) for k in ("load_chain", "save_chain", "generate_video")}
        self._ispriv = L.ST.is_private
        self._frame_ref = L.ST.frame_ref
        self._extract = L.E.extract_last_frame
        self.saved = []
        L.load_chain = lambda s: {"session": "t", "nodes": [
            {"id": 0, "parent": None, "label": "opening", "out_frame": "t_n0.png"}]}
        L.save_chain = lambda s, c: self.saved.append(c)
        L.generate_video = lambda *a, **k: "clip.mp4"     # generation "succeeded"
        L.ST.is_private = lambda s: False
        L.ST.frame_ref = lambda s, p, name: (name, f"/tmp/{name}")
        L.E.extract_last_frame = lambda clip, ref, out_path=None: ref

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)
        L.ST.is_private = self._ispriv
        L.ST.frame_ref = self._frame_ref
        L.E.extract_last_frame = self._extract

    def test_superseded_step_skips_persist(self):
        node = L.step("t", "a calm aurora over hills", "l1", is_current=lambda: False)
        self.assertIsNone(node)                # discarded — treated like a fail-open skip
        self.assertEqual(self.saved, [])       # chain NOT written: no resurrection of a wiped session

    def test_current_step_persists(self):
        node = L.step("t", "a calm aurora over hills", "l1", is_current=lambda: True)
        self.assertIsNotNone(node)             # an uninterrupted turn appends its node normally
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(self.saved[0]["nodes"][-1]["id"], 1)


if __name__ == "__main__":
    unittest.main()

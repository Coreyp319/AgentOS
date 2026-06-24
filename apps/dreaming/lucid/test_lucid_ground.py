#!/usr/bin/env python3
"""Unit tests for the frame-grounding + auto-rating pass (ADR-0014 §6, the auto-infer content mode).

The rating is a MODEL proposal; code disposes. These tests pin that disposal — the part that must be
deterministic and conservative — WITHOUT a model/GPU: `_ollama_json` is stubbed to return crafted
text, so we test the dispose rules, not the VLM.

Invariants:
  * only a LITERAL {"rating":"mature"} opens up; every other value, shape, or failure -> "sfw" (safe);
  * a caption is parsed + length-capped, and absent/garbled -> None;
  * the steering prompt actually differs by rating, and the red line is in BOTH.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_engine as E


class GroundDisposeTest(unittest.TestCase):
    def setUp(self):
        self._orig = E._ollama_json
        self.last = {}

        def _stub_factory(payload):
            def _stub(system, user, model=E.MODEL, images=None):
                self.last = {"system": system, "user": user, "images": images}
                return payload
            return _stub
        self._make = _stub_factory

    def tearDown(self):
        E._ollama_json = self._orig

    def _ground(self, payload, premise=None):
        E._ollama_json = self._make(payload)
        return E.ground_frame("ZmFrZS1iNjQ=", premise)   # any truthy b64 -> the model is consulted

    def test_no_frame_is_safe_default_without_calling_model(self):
        E._ollama_json = self._make("SHOULD NOT BE CALLED")
        self.assertEqual(E.ground_frame(None), (None, "sfw"))
        self.assertEqual(self.last, {})                       # model was never consulted

    def test_mature_only_on_literal_value(self):
        _cap, rating = self._ground('{"caption":"a dim room","rating":"mature"}')
        self.assertEqual(rating, "mature")

    def test_everything_else_disposes_to_sfw(self):
        for payload in (
            '{"caption":"x","rating":"sfw"}',
            '{"caption":"x","rating":"MATURE"}',     # case-variant is NOT the literal -> safe
            '{"caption":"x","rating":"explicit"}',   # a synonym the model invented -> safe
            '{"caption":"x","rating":true}',         # wrong type
            '{"caption":"x"}',                        # missing
            'not json at all',                        # garbled
            '[]',                                     # wrong shape (list, not object)
        ):
            _cap, rating = self._ground(payload)
            self.assertEqual(rating, "sfw", f"payload {payload!r} must dispose to sfw")

    def test_model_failure_disposes_to_sfw(self):
        def _boom(*a, **k):
            raise RuntimeError("ollama down")
        E._ollama_json = _boom
        self.assertEqual(E.ground_frame("ZmFrZQ=="), (None, "sfw"))

    def test_caption_parsed_and_capped(self):
        cap, _r = self._ground('{"caption":"  a calm aurora over hills  ","rating":"sfw"}')
        self.assertEqual(cap, "a calm aurora over hills")     # trimmed
        long = '{"caption":"' + ("x" * 500) + '","rating":"sfw"}'
        cap, _r = self._ground(long)
        self.assertEqual(len(cap), 200)                       # capped
        cap, _r = self._ground('{"caption":"   ","rating":"sfw"}')
        self.assertIsNone(cap)                                # blank -> None

    def test_premise_is_passed_to_the_model(self):
        self._ground('{"caption":"x","rating":"sfw"}', premise="a noir thriller")
        self.assertIn("a noir thriller", self.last["user"])
        self.assertEqual(self.last["images"], ["ZmFrZS1iNjQ="])   # the frame IS attached


class SteeringTest(unittest.TestCase):
    def test_rating_clause_is_the_only_difference_and_red_line_in_both(self):
        sfw, mature = E.build_sys("sfw", 4), E.build_sys("mature", 4)
        self.assertNotEqual(sfw, mature)
        for s in (sfw, mature):
            self.assertIn("no minors", s.lower())             # the red line is rating-independent
            self.assertIn("no real", s.lower())
        self.assertIn("sfw", sfw.lower())
        self.assertIn("mature", mature.lower())

    def test_unknown_rating_builds_the_sfw_prompt(self):
        self.assertEqual(E.build_sys("???", 4), E.build_sys("sfw", 4))


if __name__ == "__main__":
    unittest.main()

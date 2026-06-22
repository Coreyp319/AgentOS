#!/usr/bin/env python3
"""Unit tests for lucid_ground (ADR-0037 L0 canon ledger + L2 palette gate) — the deterministic dispose.

The canon is a MODEL proposal; code disposes. These pin that disposal WITHOUT a model/GPU:
  * merge_canon enforces the delta contract (append-only subjects, evidence/caption grounding, caps,
    coercion of a 3B's sloppy types, token-subset dedup, empty/garbled -> fail-open keep-prior);
  * the hybrid boundary — code owns time_of_day/mood, the LLM's set of those is STRIPPED;
  * the L2 palette gate is fail-closed-None (couldn't-measure -> None/'unknown', never 'steady').
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_ground as G  # noqa: E402


class MergeContract(unittest.TestCase):
    def test_add_subject_and_prop(self):
        c, _ = G.merge_canon(G.empty_canon(), {"add_subjects": ["a keeper"], "add_props": ["lantern"]})
        self.assertEqual(c["facts"]["subjects"], ["a keeper"])
        self.assertEqual(c["facts"]["props"], ["lantern"])

    def test_casefold_and_token_subset_dedup(self):
        c, _ = G.merge_canon(G.empty_canon(), {"add_subjects": ["lighthouse keeper"]})
        c, _ = G.merge_canon(c, {"add_subjects": ["A Keeper", "keeper", "a cathedral"]})
        # "keeper" is a token-subset of "lighthouse keeper" -> dup; "cathedral" is unrelated -> kept
        self.assertEqual(c["facts"]["subjects"], ["lighthouse keeper", "a cathedral"])

    def test_set_evidence_gated_offline(self):
        c, _ = G.merge_canon(G.empty_canon(), {"set": {"place": "a cliff"}, "evidence": "on a cliff"})
        self.assertEqual(c["facts"]["place"], "a cliff")
        c2, rej = G.merge_canon(G.empty_canon(), {"set": {"place": "a cliff"}, "evidence": ""})
        self.assertIsNone(c2["facts"]["place"])
        self.assertTrue(any("no-evidence" in r for r in rej))

    def test_subjects_are_append_only(self):
        base = G.empty_canon(); base["facts"]["subjects"] = ["a keeper"]
        c, rej = G.merge_canon(base, {"set": {"subjects": "a stranger"}, "evidence": "x"})
        self.assertEqual(c["facts"]["subjects"], ["a keeper"])
        self.assertTrue(any("list-key-not-settable" in r for r in rej))

    def test_empty_delta_is_noop(self):
        base, _ = G.merge_canon(G.empty_canon(), {"add_subjects": ["a keeper"]})
        c, rej = G.merge_canon(base, {})
        self.assertEqual(c, base)
        self.assertEqual(rej, [])

    def test_non_dict_delta_fail_open(self):
        base, _ = G.merge_canon(G.empty_canon(), {"add_subjects": ["a keeper"]})
        c, rej = G.merge_canon(base, "garbage")
        self.assertEqual(c, base)
        self.assertEqual(rej, ["delta:not-a-dict"])

    def test_props_cap_drops_new_keeps_stable(self):
        base = G.empty_canon(); base["facts"]["props"] = [f"p{i}" for i in range(G.CAP_PROPS)]
        c, rej = G.merge_canon(base, {"add_props": ["overflow"]})
        self.assertNotIn("overflow", c["facts"]["props"])
        self.assertEqual(len(c["facts"]["props"]), G.CAP_PROPS)
        self.assertTrue(any("over-cap" in r for r in rej))

    def test_coerce_string_list_and_string_set(self):
        c, _ = G.merge_canon(G.empty_canon(), {"add_props": "a lantern, weathered rope"})
        self.assertEqual(c["facts"]["props"], ["a lantern", "weathered rope"])
        c2, _ = G.merge_canon(G.empty_canon(), {"set": "place: 'a north cliff'", "evidence": "on a cliff"})
        self.assertEqual(c2["facts"]["place"], "a north cliff")

    def test_uncoercible_dropped_sibling_survives(self):
        c, rej = G.merge_canon(G.empty_canon(), {"add_props": 42, "add_subjects": ["ok"]})
        self.assertEqual(c["facts"]["subjects"], ["ok"])
        self.assertTrue(any("add_props:uncoercible" in r for r in rej))

    def test_caption_grounding_drops_hallucination(self):
        cap = "the keeper lifts a brass lantern on the cliff"
        c, rej = G.merge_canon(G.empty_canon(), {"add_props": ["a dragon", "a lantern"]}, evidence_text=cap)
        self.assertEqual(c["facts"]["props"], ["a lantern"])
        self.assertTrue(any("ungrounded" in r for r in rej))

    def test_clause_as_prop_rejected(self):
        cap = "the keeper lifts a brass lantern on the cliff"
        c, rej = G.merge_canon(G.empty_canon(),
                               {"add_props": ["seaglass glistens in his lantern's beam"]}, evidence_text=cap)
        self.assertEqual(c["facts"]["props"], [])
        self.assertTrue(any("too-long" in r for r in rej))

    def test_red_line_string_dropped(self):
        # a ledger string that fails the red line is dropped, not persisted (untrusted model text)
        bad = "a normal subject " + ("x" * 5)
        c, _ = G.merge_canon(G.empty_canon(), {"add_subjects": [bad]})
        # the red line itself is lucid_safety's authority; here we assert merge ROUTES through it
        self.assertEqual(c["facts"]["subjects"], [bad] if __import__("lucid_safety").red_line_ok(bad) else [])


class HybridExtractors(unittest.TestCase):
    def test_extract_time_last_match_wins(self):
        self.assertEqual(G.extract_time_of_day("the sky darkens to full night"), "night")
        self.assertEqual(G.extract_time_of_day("from dawn the day brightened to noon"), "noon")
        self.assertIsNone(G.extract_time_of_day("a featureless room"))

    def test_extract_mood(self):
        self.assertEqual(G.extract_mood("an eerie, ominous hush settles"), "ominous")
        self.assertIsNone(G.extract_mood("a plain caption"))

    def test_update_canon_codeonly_tracks_when_feel(self):
        # delta_fn=None -> pure code path: time_of_day + mood from the caption, no model
        c = G.update_canon(G.empty_canon(), "Night falls", "the sky darkens to full night; an eerie hush")
        self.assertEqual(c["facts"]["time_of_day"], "night")
        self.assertEqual(c["facts"]["mood"], "eerie")
        self.assertEqual(c["facts"]["subjects"], [])  # no model -> no subjects

    def test_update_canon_strips_llm_when_feel(self):
        # the hybrid boundary: even if the LLM proposes time_of_day/mood, CODE owns them (LLM's stripped)
        def fake_delta(prior, label, caption, *, seed=False):
            return {"add_subjects": ["a ship"], "set": {"time_of_day": "WRONG", "place": "the cliff"},
                    "evidence": caption}
        cap = "a ship appears on the cliff at dusk"
        c = G.update_canon(G.empty_canon(), "A ship", cap, delta_fn=fake_delta)
        self.assertEqual(c["facts"]["time_of_day"], "dusk")    # code's, not the LLM's "WRONG"
        self.assertIn("a ship", c["facts"]["subjects"])         # LLM's subject kept
        self.assertEqual(c["facts"]["place"], "the cliff")      # LLM's place (best-effort) kept

    def test_canon_to_context_renders_steering_line(self):
        c = G.empty_canon()
        c["facts"]["subjects"] = ["a keeper"]; c["facts"]["time_of_day"] = "night"
        c["synopsis"] = "the keeper watches the sea"
        line = G.canon_to_context(c)
        self.assertIn("Story so far: the keeper watches the sea", line)
        self.assertIn("who: a keeper", line)
        self.assertIn("time of day: night", line)


class PaletteGate(unittest.TestCase):
    def test_fail_closed_none_on_missing_paths(self):
        self.assertIsNone(G.palette_drift("/no/such/a.png", "/no/such/b.png"))
        self.assertIsNone(G.palette_drift(None, None))

    def test_verdict_maps_none_to_unknown_never_steady(self):
        self.assertEqual(G.palette_verdict(None), "unknown")
        self.assertEqual(G.palette_verdict(0.99), "steady")
        self.assertEqual(G.palette_verdict(0.10), "shifted")


if __name__ == "__main__":
    unittest.main(verbosity=2)

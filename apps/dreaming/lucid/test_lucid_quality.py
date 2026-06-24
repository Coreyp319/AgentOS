#!/usr/bin/env python3
"""Unit tests for the ADR-0033 quality pass — Wan two-tier draft/hero + identity carry.

No model / GPU / daemon: the generation seam (E.run_beat via L.generate_video, or cc.generate for the
workflow-selection test) and the store/frame helpers are stubbed, so these exercise only the new wiring:
  - deterministic per-node seed (base + id), persisted on the node and reused by the hero re-render;
  - the persistent subject descriptor prefixed onto the RENDER prompt (and its kill-switch);
  - quality -> Wan workflow-lane selection (draft 4+4 lightning vs hero 20-step) + hero resolution;
  - lucid_stitch preferring a finalized hero_clip over the draft clip.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_engine as E   # noqa: E402
import lucid_linear as L   # noqa: E402
import lucid_stitch as STCH  # noqa: E402


class _StubbedStep(unittest.TestCase):
    """Shared stubs: a mutable in-memory chain, a generation seam that RECORDS what it was handed, and the
    store/frame helpers neutralized (mirrors test_lucid_linear's BranchTest). Subclasses assert on the
    recorded generate_video kwargs + the appended node."""
    def setUp(self):
        self._orig = {k: getattr(L, k) for k in ("load_chain", "save_chain", "generate_video")}
        self._ispriv, self._frame_ref = L.ST.is_private, L.ST.frame_ref
        self._extract = L.E.extract_last_frame
        self._ground = L.E.ground_subject
        self._subj_flag = L.SUBJECT_ANCHOR_ENABLED
        # base seed pinned so the derived per-node seed is predictable (base + node id)
        self.chain = {"session": "t", "seed": 5000, "nodes": [
            {"id": 0, "parent": None, "label": "opening", "out_frame": "t_n0.png", "rating": "sfw",
             "clip": None}]}
        self.calls = []   # every (prompt, anchor, kwargs) generate_video was handed

        def _gen(session, prompt, anchor_frame, **k):
            self.calls.append((prompt, anchor_frame, k))
            return "clip.mp4"

        L.load_chain = lambda s: self.chain
        L.save_chain = lambda s, c: setattr(self, "chain", c)
        L.generate_video = _gen
        L.ST.is_private = lambda s: False
        L.ST.frame_ref = lambda s, p, name: (name, f"/tmp/{name}")
        L.E.extract_last_frame = lambda clip, ref, out_path=None: ref
        L.E.ground_subject = lambda b64: "A woman with long red hair in a blue dress"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)
        L.ST.is_private, L.ST.frame_ref = self._ispriv, self._frame_ref
        L.E.extract_last_frame = self._extract
        L.E.ground_subject = self._ground
        L.SUBJECT_ANCHOR_ENABLED = self._subj_flag


class SeedTest(_StubbedStep):
    def test_node_seed_is_deterministic_and_persisted(self):
        node = L.step("t", "she turns to the window", "l1", is_current=lambda: True)
        # base(5000) + nid(1) == 5001, recorded on the node AND handed to the generator
        self.assertEqual(node["seed"], 5001)
        self.assertEqual(node["seed"], L._beat_seed(self.chain, node["id"]))
        self.assertEqual(self.calls[-1][2]["seed"], 5001)

    def test_quality_defaults_to_draft_and_is_recorded(self):
        node = L.step("t", "the light shifts", "l1", is_current=lambda: True)
        self.assertEqual(node["quality"], "draft")
        self.assertEqual(self.calls[-1][2]["quality"], "draft")
        self.assertIn("anchor", node)            # anchor persisted so the hero pass can reuse it

    def test_hero_quality_threads_through(self):
        L.step("t", "the light shifts", "l1", is_current=lambda: True, quality="hero")
        self.assertEqual(self.calls[-1][2]["quality"], "hero")

    def test_legacy_chain_without_base_seed_is_still_deterministic(self):
        c = {"session": "abc"}
        self.assertEqual(L._beat_seed(c, 2), L._beat_seed(c, 2))   # stable across calls
        self.assertNotEqual(L._beat_seed(c, 1), L._beat_seed(c, 2))  # ids separate siblings


class SubjectAnchorTest(_StubbedStep):
    def test_subject_is_prefixed_onto_the_render_prompt(self):
        L.step("t", "she turns to the window", "l1", is_current=lambda: True)
        rendered = self.calls[-1][0]
        self.assertTrue(rendered.startswith("A woman with long red hair in a blue dress."))
        self.assertIn("she turns to the window", rendered)
        # cached on the chain so the next beat doesn't re-ground
        self.assertEqual(self.chain.get("subject"), "A woman with long red hair in a blue dress")

    def test_kill_switch_leaves_the_prompt_motion_only(self):
        L.SUBJECT_ANCHOR_ENABLED = False
        L.step("t", "she turns to the window", "l1", is_current=lambda: True)
        self.assertEqual(self.calls[-1][0], "she turns to the window")   # no identity prefix

    def test_null_capture_does_not_crash_and_renders_motion_only(self):
        L.E.ground_subject = lambda b64: None
        L.step("t", "she turns", "l1", is_current=lambda: True)
        self.assertEqual(self.calls[-1][0], "she turns")
        self.assertEqual(self.chain.get("subject"), "")   # "" sentinel, captured-but-empty


class HeroRerenderTest(unittest.TestCase):
    """rerender_hero reuses the node's stored seed/prompt/anchor at hero quality, stores hero_clip, and
    leaves the draft clip + out_frame untouched."""
    def setUp(self):
        self._orig = {k: getattr(L, k) for k in ("load_chain", "save_chain", "generate_video")}
        self.chain = {"session": "t", "seed": 9000, "nodes": [
            {"id": 0, "parent": None, "label": "opening", "out_frame": "t_n0.png", "clip": None},
            {"id": 1, "parent": 0, "label": "a", "prompt": "a calm tide rolls in", "seed": 9001,
             "anchor": "t_n1_anchor.png", "rating": "sfw", "length": 33, "clip": "draft1.mp4",
             "out_frame": "t_n1.png"}]}
        self.calls = []

        def _gen(session, prompt, anchor_frame, **k):
            self.calls.append((prompt, anchor_frame, k))
            return "hero1.mp4"

        L.load_chain = lambda s: self.chain
        L.save_chain = lambda s, c: setattr(self, "chain", c)
        L.generate_video = _gen

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)

    def test_reuses_seed_prompt_anchor_and_stores_hero_clip(self):
        node = L.rerender_hero("t", 1, is_current=lambda: True)
        prompt, anchor, k = self.calls[-1]
        self.assertEqual(prompt, "a calm tide rolls in")   # SAME shot, not a fresh prompt
        self.assertEqual(anchor, "t_n1_anchor.png")        # SAME anchor
        self.assertEqual(k["seed"], 9001)                  # SAME seed
        self.assertEqual(k["quality"], "hero")
        self.assertEqual(node["hero_clip"], "hero1.mp4")
        self.assertEqual(node["clip"], "draft1.mp4")       # draft kept
        self.assertEqual(node["out_frame"], "t_n1.png")    # downstream anchor untouched

    def test_clip_less_or_unknown_node_is_a_noop(self):
        self.assertIsNone(L.rerender_hero("t", 0))         # opening node has no clip
        self.assertIsNone(L.rerender_hero("t", 99))        # unknown id
        self.assertEqual(self.calls, [])

    def test_superseded_render_is_discarded(self):
        node = L.rerender_hero("t", 1, is_current=lambda: False)
        self.assertIsNone(node)
        self.assertNotIn("hero_clip", self.chain["nodes"][1])


class WorkflowSelectionTest(unittest.TestCase):
    """run_beat(quality=...) selects the Wan workflow LANE: draft = 4+4 lightning (has the lightx2v LoRA),
    hero = non-distilled 20-step (no lightx2v) at HERO_W/HERO_H. The graph the generator receives is the
    proof — cc.generate is stubbed to capture it; no GPU."""
    def setUp(self):
        self._gen = E.cc.generate
        self._ov = E._ENGINE_OVERRIDE
        E.set_engine("wan")          # pin the Wan path regardless of the box's registry
        self.api = {}

        def _fake(api, timeout=None):
            self.api = api
            return (["/tmp/out.mp4"], {})
        E.cc.generate = _fake

    def tearDown(self):
        E.cc.generate = self._gen
        E._ENGINE_OVERRIDE = self._ov

    def _loras(self):
        return [n["inputs"].get("lora_name", "").lower()
                for n in self.api.values() if n.get("class_type") == "LoraLoaderModelOnly"]

    def _wan_wh(self):
        wi = next(n for n in self.api.values() if n.get("class_type") == "WanImageToVideo")
        return wi["inputs"]["width"], wi["inputs"]["height"]

    def _classes(self):
        return {n.get("class_type") for n in self.api.values()}

    def test_draft_uses_the_lightning_lane(self):
        E.run_beat("hello", "seed.png", seed=42, quality="draft")
        self.assertTrue(any("lightx2v" in l for l in self._loras()))   # 4+4 speed lane
        self.assertEqual(self._wan_wh(), (E.DEFAULT_W, E.DEFAULT_H))
        self.assertNotIn("ImageUpscaleWithModel", self._classes())     # draft does NOT upscale (fast browse)

    def test_hero_uses_the_non_distilled_lane_with_upscale(self):
        E.run_beat("hello", "seed.png", seed=42, quality="hero")
        self.assertFalse(any("lightx2v" in l for l in self._loras()))  # 20-step, no Lightning
        self.assertEqual(self._wan_wh(), (E.HERO_W, E.HERO_H))         # render res stays draft-res (upscale adds pixels)
        # ADR-0033 (measured): the actual "low-res feel" fix is the post-gen detail upscale, NOT steps/precision
        self.assertIn("ImageUpscaleWithModel", self._classes())

    def test_hero_fails_open_to_plain_20step_when_upscale_model_missing(self):
        orig = E.HERO_UPSCALE_MODEL
        E.HERO_UPSCALE_MODEL = "definitely_not_a_real_upscale_model_xyz.pth"
        try:
            E.run_beat("hello", "seed.png", seed=42, quality="hero")
        finally:
            E.HERO_UPSCALE_MODEL = orig
        # the upscale stage is dropped and the video-combine is rewired straight off the VAEDecode (still renders)
        self.assertNotIn("ImageUpscaleWithModel", self._classes())
        self.assertNotIn("UpscaleModelLoader", self._classes())
        vid = next(n for n in self.api.values() if n.get("class_type") == "VHS_VideoCombine")
        dec = next(i for i, n in self.api.items() if n.get("class_type") == "VAEDecode")
        self.assertEqual(vid["inputs"]["images"], [dec, 0])


class StitchHeroPreferenceTest(unittest.TestCase):
    """clip_spine plays the finalized hero_clip when present, falling back to the draft when the hero file
    is absent on disk (a partially-purged dream still downloads)."""
    def test_prefers_hero_then_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            draft = os.path.join(d, "draft.mp4"); open(draft, "w").close()
            hero = os.path.join(d, "hero.mp4"); open(hero, "w").close()
            chain = {"nodes": [
                {"id": 0, "parent": None, "clip": None},
                {"id": 1, "parent": 0, "clip": draft, "hero_clip": hero}]}
            self.assertEqual(STCH.clip_spine(chain), [os.path.abspath(hero)])   # hero wins
            os.remove(hero)
            self.assertEqual(STCH.clip_spine(chain), [os.path.abspath(draft)])  # hero gone -> draft plays


if __name__ == "__main__":
    unittest.main()

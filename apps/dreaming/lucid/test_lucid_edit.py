#!/usr/bin/env python3
"""Unit tests for ADR-0040 — prompt-guided keyframe edit ("edit-then-animate").

No model / GPU / daemon: the ComfyUI seam (cc.generate_image / cc.free_vram), the VRAM probes
(_comfy_free_mib / _comfy_busy), the generation seam (L.generate_video) and the store/frame helpers
are stubbed, so these exercise only the new wiring:
  - _edit_graph: the Qwen-Image-Edit api graph (loader swap, Lightning vs full lane, ref-image slots);
  - edit_frame: warm-only + headroom + busy gating, TOTAL fail-open, shared-output scratch scrubbed;
  - step(anchor_override=...): the edited keyframe seeds the beat AND supersedes the parent's notes;
  - replace_beat / revert_beat: in-place re-render with a once-only backup, reversible (ADR-0005).
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # apps/dreaming (comfy_client)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_engine as E   # noqa: E402
import lucid_linear as L   # noqa: E402


# ───────────────────────────── the edit graph (pure) ─────────────────────────────
class EditGraphTest(unittest.TestCase):
    def test_lightning_lane_4step_cfg1_and_loader_swap(self):
        g = E._edit_graph("frame.png", "raise the lantern", seed=7)
        self.assertEqual(g["unet"]["class_type"], "UnetLoaderGGUF")           # GGUF loader swap
        self.assertEqual(g["unet"]["inputs"]["unet_name"], E.EDIT_MODEL)
        self.assertEqual(g["clip"]["inputs"]["type"], "qwen_image")
        self.assertIn("lora", g)                                              # Lightning on by default
        self.assertEqual(g["ks"]["inputs"]["model"], ["cfgn", 0])            # UNET->lora->shift->cfgnorm->KSampler
        self.assertEqual(g["ks"]["inputs"]["steps"], 4)
        self.assertEqual(g["ks"]["inputs"]["cfg"], 1.0)
        self.assertEqual(g["ks"]["inputs"]["seed"], 7)
        self.assertEqual(g["pos"]["inputs"]["prompt"], "raise the lantern")
        self.assertNotIn("neg", g)                                           # G2: negative node elided at cfg=1.0
        self.assertEqual(g["ks"]["inputs"]["negative"], ["pos", 0])          # reuses positive (unused at cfg 1)
        self.assertEqual(g["enc"]["inputs"]["pixels"], ["scale", 0])         # source VAE-encoded as the latent
        self.assertEqual(g["save"]["inputs"]["filename_prefix"], "lucid/editkf")

    def test_full_lane_when_lightning_disabled(self):
        orig = E.EDIT_LIGHTNING_LORA
        try:
            E.EDIT_LIGHTNING_LORA = ""
            g = E._edit_graph("frame.png", "x")
            self.assertNotIn("lora", g)
            self.assertEqual(g["ks"]["inputs"]["model"], ["cfgn", 0])
            self.assertEqual(g["ks"]["inputs"]["steps"], 20)
            self.assertEqual(g["ks"]["inputs"]["cfg"], 4.0)
            self.assertIn("neg", g)                                          # full lane (cfg>1) builds a real negative
            self.assertEqual(g["ks"]["inputs"]["negative"], ["neg", 0])
            self.assertEqual(g["neg"]["inputs"]["prompt"], "")               # empty-instruction unconditional
        finally:
            E.EDIT_LIGHTNING_LORA = orig

    def test_reference_images_fill_plus_slots(self):
        g = E._edit_graph("frame.png", "match this", ref_names=["ref_a.png", "ref_b.png", "ref_c.png"])
        # source is image1; up to TWO refs become image2/image3 (Plus takes 3 total); a 3rd ref is dropped
        self.assertEqual(g["pos"]["inputs"]["image1"], ["scale", 0])
        self.assertEqual(g["pos"]["inputs"]["image2"], ["ref0", 0])
        self.assertEqual(g["pos"]["inputs"]["image3"], ["ref1", 0])
        self.assertNotIn("image4", g["pos"]["inputs"])
        self.assertEqual(g["ref0"]["inputs"]["image"], "ref_a.png")

    def test_no_reference_has_only_source(self):
        g = E._edit_graph("frame.png", "x")
        self.assertEqual(g["pos"]["inputs"]["image1"], ["scale", 0])
        self.assertNotIn("image2", g["pos"]["inputs"])


# ───────────────────────────── edit_frame (gated, fail-open) ─────────────────────────────
class EditFrameTest(unittest.TestCase):
    def setUp(self):
        self._orig = {k: getattr(E, k) for k in ("_comfy_free_mib", "_comfy_busy")}
        self._gen = E.cc.generate_image
        self._free = E.cc.free_vram
        self._scrub = E._scrub_edit_scratch
        self.freed = []
        self.scrubbed = 0
        E._comfy_free_mib = lambda: 20000          # plenty of headroom
        E._comfy_busy = lambda: False
        E.cc.free_vram = lambda: self.freed.append(True) or True
        # stub the shared-output scrub (the real one globs ~/ComfyUI/output/lucid/editkf_*) and count its calls,
        # so the test asserts it RUNS on every path without touching the real output dir.
        E._scrub_edit_scratch = lambda: setattr(self, "scrubbed", self.scrubbed + 1)
        self.tmp = tempfile.mkdtemp()
        # CP2: edit_frame now requires the source frame to exist under INPUT_DIR — redirect it to the tmpdir and
        # plant the source there (also lets the cover-resize run on a real image).
        self._input = E.INPUT_DIR
        E.INPUT_DIR = self.tmp
        from PIL import Image
        Image.new("RGB", (720, 1280), (10, 20, 30)).save(os.path.join(self.tmp, "frame.png"))
        # a real little PNG to stand in for the SaveImage scratch (so _save_validated_image actually runs)
        self.scratch = os.path.join(self.tmp, "editkf_00001_.png")
        Image.new("RGB", (800, 1328), (40, 50, 70)).save(self.scratch)   # a Kontext-bucket-ish aspect (≠ 720×1280)
        E.cc.generate_image = lambda graph, timeout=300: ([self.scratch], {})

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(E, k, v)
        E.cc.generate_image = self._gen
        E.cc.free_vram = self._free
        E._scrub_edit_scratch = self._scrub
        E.INPUT_DIR = self._input

    def test_success_writes_keyframe_and_scrubs_scratch(self):
        out = os.path.join(self.tmp, "kf_out.png")
        res = E.edit_frame("frame.png", "raise the lantern", out)
        self.assertEqual(res, out)
        self.assertTrue(os.path.exists(out))                  # clean keyframe sealed (atomic temp+rename)
        self.assertGreaterEqual(self.scrubbed, 1)             # shared-output scratch scrubbed (finally)
        self.assertTrue(self.freed)                           # reclaimed VRAM before the headroom read
        from PIL import Image
        with Image.open(out) as im:                           # G1: cover-fit to the i2v size (preview == animated)
            self.assertEqual(im.size, (E.DEFAULT_W, E.DEFAULT_H))

    def test_missing_source_frame_returns_none(self):
        # CP2: a burned/missing source must fail-open BEFORE the VRAM swap, not after
        self.assertIsNone(E.edit_frame("not_here.png", "x", os.path.join(self.tmp, "o.png")))
        self.assertFalse(self.freed)                          # never reclaimed VRAM for a doomed edit

    def test_disabled_kill_switch_returns_none(self):
        orig = E.EDIT_ENABLED
        try:
            E.EDIT_ENABLED = False
            self.assertIsNone(E.edit_frame("frame.png", "x", os.path.join(self.tmp, "o.png")))
        finally:
            E.EDIT_ENABLED = orig

    def test_empty_instruction_returns_none(self):
        self.assertIsNone(E.edit_frame("frame.png", "   ", os.path.join(self.tmp, "o.png")))

    def test_cold_comfy_returns_none(self):
        E._comfy_free_mib = lambda: None
        self.assertIsNone(E.edit_frame("frame.png", "x", os.path.join(self.tmp, "o.png")))

    def test_insufficient_headroom_returns_none(self):
        E._comfy_free_mib = lambda: E.EDIT_PEAK_MIB - 1     # below need
        self.assertIsNone(E.edit_frame("frame.png", "x", os.path.join(self.tmp, "o.png")))

    def test_busy_comfy_returns_none_without_freeing(self):
        E._comfy_busy = lambda: True
        self.assertIsNone(E.edit_frame("frame.png", "x", os.path.join(self.tmp, "o.png")))
        self.assertFalse(self.freed)                          # never evict a RUNNING render

    def test_generate_raises_is_fail_open(self):
        def _boom(graph, timeout=300):
            raise RuntimeError("comfy exploded")
        E.cc.generate_image = _boom
        self.assertIsNone(E.edit_frame("frame.png", "x", os.path.join(self.tmp, "o.png")))
        self.assertGreaterEqual(self.scrubbed, 1)             # scrub ran on the exception path (finally)

    def test_corrupt_output_is_rejected(self):
        bad = os.path.join(self.tmp, "bad.png")
        with open(bad, "wb") as f:
            f.write(b"not a png")
        E.cc.generate_image = lambda graph, timeout=300: ([bad], {})
        out = os.path.join(self.tmp, "o.png")
        self.assertIsNone(E.edit_frame("frame.png", "x", out))
        self.assertFalse(os.path.exists(out))                 # no keyframe written on reject
        self.assertGreaterEqual(self.scrubbed, 1)             # scratch scrubbed even on reject (finally)


# ───────────────────────────── orchestration (stubbed seam) ─────────────────────────────
class _StubbedChain(unittest.TestCase):
    """Mirror test_lucid_quality's _StubbedStep: in-memory chain + recording generate_video + neutralized
    store/frame helpers, so step()/replace_beat()/revert_beat() are exercised without a GPU."""
    def setUp(self):
        self._orig = {k: getattr(L, k) for k in ("load_chain", "save_chain", "generate_video")}
        self._ispriv, self._frame_ref = L.ST.is_private, L.ST.frame_ref
        self._extract = L.E.extract_last_frame
        self._ground = L.E.ground_subject
        self.chain = {"session": "t", "seed": 5000, "nodes": [
            {"id": 0, "parent": None, "label": "opening", "out_frame": "t_n0.png", "rating": "sfw",
             "clip": None, "notes": [{"id": "n", "t": 0.5, "tag": "hold", "text": "freeze"}]},
            {"id": 1, "parent": 0, "label": "beat one", "out_frame": "t_n1.png", "rating": "sfw",
             "clip": "old_clip.mp4", "prompt": "old render prompt", "seed": 5001, "length": 33,
             "quality": "draft"}]}
        self.calls = []

        def _gen(session, prompt, anchor_frame, **k):
            self.calls.append((prompt, anchor_frame, k))
            return "new_clip.mp4"

        L.load_chain = lambda s: self.chain
        L.save_chain = lambda s, c: setattr(self, "chain", c)
        L.generate_video = _gen
        L.ST.is_private = lambda s: False
        L.ST.frame_ref = lambda s, p, name: (name, f"/tmp/{name}")
        L.E.extract_last_frame = lambda clip, ref, out_path=None: ref
        L.E.ground_subject = lambda b64: "A woman with red hair"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)
        L.ST.is_private, L.ST.frame_ref = self._ispriv, self._frame_ref
        L.E.extract_last_frame = self._extract
        L.E.ground_subject = self._ground


class StepAnchorOverrideTest(_StubbedChain):
    def test_override_seeds_the_beat_and_skips_parent_notes(self):
        # decompose_notes MUST NOT run when anchor_override is set (the edit IS the steering)
        L.E.decompose_notes = lambda *a, **k: (_ for _ in ()).throw(AssertionError("notes decomposed"))
        node = L.step("t", "she walks toward the light", "l1", parent_id=0,
                      anchor_override="edited_kf.png", is_current=lambda: True)
        self.assertEqual(node["anchor"], "edited_kf.png")
        self.assertEqual(self.calls[-1][1], "edited_kf.png")          # generator seeded from the edit
        # identity prefix still applies; the user's instruction is the motion
        self.assertTrue(self.calls[-1][0].startswith("A woman with red hair."))
        self.assertIn("she walks toward the light", self.calls[-1][0])

    def test_without_override_uses_anchor_for(self):
        # no hold note on node 1 -> _anchor_for falls back to the parent's last frame (NOT the override)
        node = L.step("t", "she turns", "l1", parent_id=1, is_current=lambda: True)
        self.assertEqual(node["anchor"], "t_n1.png")                  # derived from the parent, not an override


class ReplaceBeatTest(_StubbedChain):
    def test_replace_backs_up_once_and_swaps_in_place(self):
        node = L.replace_beat("t", 1, "edited_kf.png", prompt="raise the lantern",
                              is_current=lambda: True)
        self.assertEqual(node["id"], 1)                              # SAME node (in place)
        self.assertEqual(node["anchor"], "edited_kf.png")
        self.assertEqual(node["clip"], "new_clip.mp4")
        self.assertTrue(node["edited"])
        self.assertEqual(node["prev"]["clip"], "old_clip.mp4")       # original backed up
        self.assertEqual(node["prev"]["prompt"], "old render prompt")
        self.assertTrue(self.calls[-1][0].startswith("A woman with red hair."))   # gated + subject-prefixed
        self.assertIn("raise the lantern", self.calls[-1][0])
        self.assertEqual(self.calls[-1][1], "edited_kf.png")

    def test_second_edit_keeps_the_FIRST_original(self):
        L.replace_beat("t", 1, "kf1.png", prompt="first", is_current=lambda: True)
        L.replace_beat("t", 1, "kf2.png", prompt="second", is_current=lambda: True)
        node = next(n for n in self.chain["nodes"] if n["id"] == 1)
        self.assertEqual(node["prev"]["clip"], "old_clip.mp4")       # not "new_clip.mp4" — revert -> the source
        self.assertEqual(node["edits"], 2)

    def test_prompt_none_reuses_the_nodes_stored_prompt(self):
        L.replace_beat("t", 1, "edited_kf.png", prompt=None, is_current=lambda: True)
        self.assertEqual(self.calls[-1][0], "old render prompt")     # verbatim (already gated + prefixed)

    def test_opening_node_cannot_be_replaced(self):
        self.assertIsNone(L.replace_beat("t", 0, "edited_kf.png", prompt="x", is_current=lambda: True))

    def test_unknown_node_is_none(self):
        self.assertIsNone(L.replace_beat("t", 99, "edited_kf.png", prompt="x", is_current=lambda: True))

    def test_fail_open_clip_none_leaves_chain_unchanged(self):
        L.generate_video = lambda *a, **k: None
        self.assertIsNone(L.replace_beat("t", 1, "edited_kf.png", prompt="x", is_current=lambda: True))
        node = next(n for n in self.chain["nodes"] if n["id"] == 1)
        self.assertEqual(node["clip"], "old_clip.mp4")               # untouched
        self.assertNotIn("prev", node)

    def test_superseded_render_is_discarded(self):
        self.assertIsNone(L.replace_beat("t", 1, "edited_kf.png", prompt="x", is_current=lambda: False))
        node = next(n for n in self.chain["nodes"] if n["id"] == 1)
        self.assertEqual(node["clip"], "old_clip.mp4")


class RevertBeatTest(_StubbedChain):
    def test_revert_restores_the_backed_up_shot(self):
        L.replace_beat("t", 1, "edited_kf.png", prompt="raise the lantern", is_current=lambda: True)
        node = L.revert_beat("t", 1)
        self.assertEqual(node["clip"], "old_clip.mp4")
        self.assertEqual(node["out_frame"], "t_n1.png")
        self.assertEqual(node["prompt"], "old render prompt")
        self.assertNotIn("prev", node)
        self.assertNotIn("edited", node)

    def test_revert_without_an_edit_is_a_noop(self):
        self.assertIsNone(L.revert_beat("t", 1))                     # never edited -> nothing to restore

    def test_revert_unknown_node_is_none(self):
        self.assertIsNone(L.revert_beat("t", 99))


# ───────────────────────── web-side: EDIT_PENDING lifecycle + label (pure) ─────────────────────────
import lucid_web as W   # noqa: E402  (imports cleanly — no bind side-effect until __main__)


class EditPendingTest(unittest.TestCase):
    """The previewed-but-uncommitted edit registry (ADR-0040): single-use claim, TTL expiry, explicit cap,
    and keyframe-scratch unlink on evict/expire (the security/privacy review's Med). INPUT_DIR is redirected
    to a tmpdir so the unlink targets test files, never the real input dir."""
    def setUp(self):
        self._input = W.L.E.INPUT_DIR
        self._ttl, self._max = W.EDIT_PENDING_TTL, W.EDIT_PENDING_MAX
        self.tmp = tempfile.mkdtemp()
        W.L.E.INPUT_DIR = self.tmp
        W.EDIT_PENDING.clear()

    def tearDown(self):
        W.L.E.INPUT_DIR = self._input
        W.EDIT_PENDING_TTL, W.EDIT_PENDING_MAX = self._ttl, self._max
        W.EDIT_PENDING.clear()

    def _kf(self, name):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as f:
            f.write(b"x")
        return name

    def test_put_then_pop_is_single_use(self):
        W._edit_pending_put("tok", {"session": "s", "node": 3, "placement": "branch",
                                    "prompt": "p", "keyframe": self._kf("kf.png")})
        rec = W._edit_pending_pop("tok")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["node"], 3)
        self.assertIsNone(W._edit_pending_pop("tok"))          # consumed — a 2nd claim fails

    def test_pop_unknown_token_is_none(self):
        self.assertIsNone(W._edit_pending_pop("nope"))

    def test_expired_pop_returns_none_and_unlinks_keyframe(self):
        kf = self._kf("expired.png")
        W._edit_pending_put("tok", {"session": "s", "keyframe": kf})
        W.EDIT_PENDING_TTL = -1                                # everything is now "expired"
        self.assertIsNone(W._edit_pending_pop("tok"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, kf)))   # stale scratch unlinked

    def test_put_prunes_expired_and_unlinks(self):
        old = self._kf("old.png")
        W._edit_pending_put("old", {"session": "s", "keyframe": old})
        W.EDIT_PENDING_TTL = -1                                # the next put prunes "old"
        W._edit_pending_put("new", {"session": "s", "keyframe": self._kf("new.png")})
        self.assertNotIn("old", W.EDIT_PENDING)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, old)))

    def test_cap_evicts_oldest_and_unlinks(self):
        W.EDIT_PENDING_MAX = 2
        for i in range(3):                                     # 3 puts into a cap of 2
            W._edit_pending_put(f"t{i}", {"session": "s", "keyframe": self._kf(f"k{i}.png")})
        self.assertLessEqual(len(W.EDIT_PENDING), 2)
        self.assertNotIn("t0", W.EDIT_PENDING)                 # oldest evicted
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "k0.png")))  # its scratch unlinked
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "k2.png")))   # newest kept

    def test_unlink_missing_keyframe_is_noop(self):
        W._unlink_pending_keyframe({"keyframe": "does_not_exist.png"})       # no exception
        W._unlink_pending_keyframe({})                                       # no keyframe key
        W._unlink_pending_keyframe(None)

    def test_edit_label_truncates(self):
        self.assertEqual(W._edit_label("short"), "short")
        long = "x" * 80
        self.assertTrue(W._edit_label(long).endswith("…"))
        self.assertLessEqual(len(W._edit_label(long)), 41)
        self.assertEqual(W._edit_label("  "), "edit")          # empty -> a sane default


class RunEditCommitTest(unittest.TestCase):
    """The commit worker dispatch (ADR-0040): 'branch' -> step(anchor_override), 'replace' -> replace_beat,
    lease-unavailable -> skipped. step/replace_beat are stubbed (no GPU); the TURN/lease globals are
    saved+restored. epoch=None takes the legacy unguarded path (no epoch checks)."""
    def setUp(self):
        self._ensure = W._ensure_lease
        self._step, self._replace = W.L.step, W.L.replace_beat
        self._turn = dict(W.TURN)
        self._tok = W.CURRENT_TOKEN
        self.calls = []
        W._ensure_lease = lambda epoch=None: "tok"
        W.CURRENT_TOKEN = None                                 # skip the TOKEN_DEADLINE bump branch
        W.L.step = lambda *a, **k: (self.calls.append(("step", a, k)) or {"id": 99})
        W.L.replace_beat = lambda *a, **k: (self.calls.append(("replace", a, k)) or {"id": 5})

    def tearDown(self):
        W._ensure_lease = self._ensure
        W.L.step, W.L.replace_beat = self._step, self._replace
        W.TURN.clear(); W.TURN.update(self._turn)
        W.CURRENT_TOKEN = self._tok

    def test_branch_calls_step_with_anchor_override(self):
        W._run_edit_commit(5, "branch", "kf.png", "the prompt", "lbl", None, None, "sess")
        kind, a, k = self.calls[-1]
        self.assertEqual(kind, "step")
        self.assertEqual(k.get("parent_id"), 5)
        self.assertEqual(k.get("anchor_override"), "kf.png")
        self.assertTrue(k.get("external_lease"))
        self.assertEqual(W.TURN["phase"], "done")

    def test_replace_calls_replace_beat_in_place(self):
        W._run_edit_commit(5, "replace", "kf.png", "the prompt", "lbl", None, None, "sess")
        kind, a, k = self.calls[-1]
        self.assertEqual(kind, "replace")
        self.assertIn(5, a)                                    # node_id passed positionally
        self.assertIn("kf.png", a)                             # the edited keyframe is the anchor
        self.assertEqual(k.get("prompt"), "the prompt")

    def test_skipped_when_lease_unavailable_is_fail_open(self):
        W._ensure_lease = lambda epoch=None: None
        W._run_edit_commit(5, "branch", "kf.png", "p", "l", None, None, "sess")
        self.assertEqual(W.TURN["phase"], "skipped")
        self.assertEqual(self.calls, [])                       # never rendered


if __name__ == "__main__":
    unittest.main()

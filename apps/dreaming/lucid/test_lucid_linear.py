#!/usr/bin/env python3
"""Unit tests for the HELD per-frame beat menu (ADR-0015 §1: "no reroll").

`beats_for_tip` rolls the non-deterministic LLM ONCE per chain tip, persists the menu on the tip
node, and re-serves it verbatim — so the "what happens next" suggestions are held until the chain
advances (a clip the user picked is generated + appended). No model/GPU/daemon: `roll_menu` (the
grounding+beat-gen seam) and the chain store are stubbed, so this exercises only the hold logic.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_linear as L


class HeldBeatsTest(unittest.TestCase):
    def setUp(self):
        # in-memory chain + a counting, deterministic roll_menu() stub (each roll is distinct). The stub
        # returns the (beats, caption, rating) triple the grounded seam yields; the hold logic must seal
        # all three on the tip and never re-roll while a menu is held.
        self.chain = {"session": "t", "private": False, "nodes": [
            {"id": 0, "parent": None, "label": "opening", "prompt": None,
             "seed": None, "clip": None, "out_frame": "t_n0.png"}]}
        self.saves = 0
        self.rolls = 0
        self._orig = {k: getattr(L, k) for k in ("roll_menu", "load_chain", "save_chain")}
        L.load_chain = lambda s: self.chain

        def _save(s, c):
            self.saves += 1
            self.chain = c

        def _roll(session, chain, n=4, node=None):   # node= : roll_menu now grounds on any beat (branch)
            self.rolls += 1
            return [{"label": f"beat{self.rolls}", "prompt": f"p{self.rolls}"}], f"cap{self.rolls}", "sfw"

        L.save_chain = _save
        L.roll_menu = _roll

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)

    def test_rolls_once_then_holds(self):
        a = L.beats_for_tip("t")
        b = L.beats_for_tip("t")
        self.assertEqual(a, b)                                 # same menu, held
        self.assertEqual(self.rolls, 1)                        # model proposed exactly once for this frame
        self.assertEqual(self.chain["nodes"][-1]["beats"], a)  # persisted on the tip node
        self.assertEqual(self.chain["nodes"][-1]["rating"], "sfw")    # the frame's rating is sealed too
        self.assertEqual(self.chain["nodes"][-1]["caption"], "cap1")  # ...and its grounding caption

    def test_rating_is_monotone_and_not_lowered_by_a_fresh_sfw_roll(self):
        # a frame that INHERITED 'mature' (step propagates the floor) must not be de-rated when its own
        # menu later grounds 'sfw' — else the floor would collapse and a typed-before-roll beat on the
        # next frame would render sfw. The roll_menu stub here grounds 'sfw'; the floor must hold.
        self.chain["nodes"][-1]["rating"] = "mature"
        L.beats_for_tip("t")
        self.assertEqual(self.chain["nodes"][-1]["rating"], "mature")
        self.assertEqual(L._max_rating(None, "sfw"), "sfw")        # unknown/None -> safe default
        self.assertEqual(L._max_rating("sfw", "mature"), "mature")  # sticky-up

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
        L.roll_menu = lambda session, chain, n=4, node=None: ([], None, "sfw")   # Ollama momentarily down
        self.assertEqual(L.beats_for_tip("t"), [])
        self.assertNotIn("beats", self.chain["nodes"][-1])      # an empty roll is NOT pinned onto the frame
        self.assertNotIn("rating", self.chain["nodes"][-1])     # nor is a rating sealed on a transient miss
        self.assertEqual(self.saves, 0)
        L.roll_menu = lambda session, chain, n=4, node=None: ([{"label": "ok", "prompt": "go"}], None, "sfw")  # recovery
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


class BranchTest(unittest.TestCase):
    """Branching: step(parent_id) forks a NEW take from an earlier beat — collision-free id, the right
    parent pointer, and a branch-scoped rating floor (a sfw branch is not dragged mature by a sibling).
    Default (no parent_id) still continues the linear tip. Heavy seams (generate/frame) are stubbed."""
    def setUp(self):
        self._orig = {k: getattr(L, k) for k in ("load_chain", "save_chain", "generate_video")}
        self._ispriv, self._frame_ref, self._extract = L.ST.is_private, L.ST.frame_ref, L.E.extract_last_frame
        # 0(root,sfw) -> 1(sfw) -> 2(mature): a linear chain whose tip is mature
        self.chain = {"session": "t", "nodes": [
            {"id": 0, "parent": None, "label": "opening", "out_frame": "t_n0.png", "rating": "sfw"},
            {"id": 1, "parent": 0, "label": "a", "out_frame": "t_n1.png", "rating": "sfw"},
            {"id": 2, "parent": 1, "label": "b", "out_frame": "t_n2.png", "rating": "mature"}]}
        L.load_chain = lambda s: self.chain
        L.save_chain = lambda s, c: setattr(self, "chain", c)
        L.generate_video = lambda *a, **k: "clip.mp4"
        L.ST.is_private = lambda s: False
        L.ST.frame_ref = lambda s, p, name: (name, f"/tmp/{name}")
        L.E.extract_last_frame = lambda clip, ref, out_path=None: ref

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)
        L.ST.is_private, L.ST.frame_ref, L.E.extract_last_frame = self._ispriv, self._frame_ref, self._extract

    def test_branch_from_earlier_beat_forks_a_sibling(self):
        node = L.step("t", "a quiet turn by the sea", "take2", is_current=lambda: True, parent_id=1)
        self.assertEqual(node["parent"], 1)                 # forked from beat 1, not the tip
        self.assertEqual(node["id"], 3)                     # collision-free (parent+1 == 2 would clash)
        kids = sorted(n["id"] for n in self.chain["nodes"] if n["parent"] == 1)
        self.assertEqual(kids, [2, 3])                      # beat 1 now forks into two takes
        self.assertEqual(node["rating"], "sfw")             # sfw ancestry → sfw (mature sibling ignored)

    def test_continue_tip_is_the_default(self):
        node = L.step("t", "the storm holds the lamp", "cont", is_current=lambda: True)  # no parent_id
        self.assertEqual(node["parent"], 2)                 # continues from the tip
        self.assertEqual(node["id"], 3)
        self.assertEqual(node["rating"], "mature")          # inherits the mature floor down the tip's spine


class NotesTest(unittest.TestCase):
    """Moment annotations (ADR-0023): notes persist on a node; add validates tag + red-line-gates the
    untrusted text; step() feeds a parent's notes forward — a `hold` note re-anchors the next beat on a
    tagged MOMENT (E.extract_frame_at), while no notes leave the legacy last-frame anchor untouched.
    Heavy seams (generate/frame extract) are stubbed exactly like BranchTest; generate_video records the
    anchor it was handed so the spatial feed-forward is assertable without a GPU."""
    def setUp(self):
        self._orig = {k: getattr(L, k) for k in ("load_chain", "save_chain", "generate_video")}
        self._ispriv, self._frame_ref = L.ST.is_private, L.ST.frame_ref
        self._extract_last, self._extract_at = L.E.extract_last_frame, L.E.extract_frame_at
        self._redline = L.S.red_line_ok
        # 0(root) -> 1(tip, has a real clip so a hold note can extract from it)
        self.chain = {"session": "t", "nodes": [
            {"id": 0, "parent": None, "label": "opening", "out_frame": "t_n0.png", "rating": "sfw",
             "clip": None},
            {"id": 1, "parent": 0, "label": "a", "out_frame": "t_n1.png", "rating": "sfw",
             "clip": "clip1.mp4"}]}
        self.gen_anchors = []   # every anchor frame generate_video was handed

        def _gen(session, prompt, anchor_frame, **k):
            self.gen_anchors.append(anchor_frame)
            return "clip.mp4"

        L.load_chain = lambda s: self.chain
        L.save_chain = lambda s, c: setattr(self, "chain", c)
        L.generate_video = _gen
        L.ST.is_private = lambda s: False
        L.ST.frame_ref = lambda s, p, name: (name, f"/tmp/{name}")
        L.E.extract_last_frame = lambda clip, ref, out_path=None: ref
        L.E.extract_frame_at = lambda clip, t, name, out_path=None: "anchorframe.png"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)
        L.ST.is_private, L.ST.frame_ref = self._ispriv, self._frame_ref
        L.E.extract_last_frame, L.E.extract_frame_at = self._extract_last, self._extract_at
        L.S.red_line_ok = self._redline

    def test_add_note_persists_on_node(self):
        note = L.add_note("t", 1, 1.4, "hold", "the lamp")
        self.assertEqual(note["tag"], "hold")
        self.assertEqual(note["t"], 1.4)
        self.assertEqual(note["text"], "the lamp")
        self.assertTrue(note["id"].startswith("nt"))
        node = next(n for n in self.chain["nodes"] if n["id"] == 1)
        self.assertEqual(node["notes"], [note])                 # appended onto the node's notes list

    def test_add_note_time_only_has_no_region_keys(self):
        # legacy notes (no x/y) stay clean — no x/y/r keys leak in (ADR-0025 amendment back-compat).
        note = L.add_note("t", 1, 0.5, "more", "")
        self.assertNotIn("x", note)
        self.assertNotIn("y", note)
        self.assertNotIn("r", note)

    def test_add_note_persists_spatial_region(self):
        # a tap point persists as x/y/r; a missing r takes the default; out-of-range coords clamp.
        note = L.add_note("t", 1, 0.5, "more", "the lamp", x=0.3, y=0.4, r=0.22)
        self.assertEqual((note["x"], note["y"], note["r"]), (0.3, 0.4, 0.22))
        d = L.add_note("t", 1, 0.5, "more", "", x=0.5, y=0.5)   # no r -> default
        self.assertEqual(d["r"], L.DEFAULT_NOTE_RADIUS)
        c = L.add_note("t", 1, 0.5, "more", "", x=1.9, y=-0.5, r=5.0)   # clamp into frame + radius bound
        self.assertEqual((c["x"], c["y"]), (1.0, 0.0))
        self.assertLessEqual(c["r"], 0.9)

    def test_remove_note(self):
        note = L.add_note("t", 1, 0.0, "more", "")
        self.assertTrue(L.remove_note("t", 1, note["id"]))      # removed
        node = next(n for n in self.chain["nodes"] if n["id"] == 1)
        self.assertEqual(node.get("notes"), [])                 # gone
        self.assertFalse(L.remove_note("t", 1, note["id"]))     # idempotent: a second delete is a no-op

    def test_add_note_persists_validated_mask_ref(self):
        # ADR-0032: a valid, existing, session-scoped mask ref persists; bogus refs are dropped (code disposes).
        import tempfile, shutil
        d = tempfile.mkdtemp(); orig = L.E.INPUT_DIR; L.E.INPUT_DIR = d
        try:
            with open(os.path.join(d, "t_segmask_0.png"), "wb") as f:
                f.write(b"x")
            note = L.add_note("t", 1, 0.5, "more", "", x=0.5, y=0.5, mask="t_segmask_0.png")
            self.assertEqual(note["mask"], "t_segmask_0.png")                 # valid ref persists
            add = lambda m: L.add_note("t", 1, 0.5, "more", "", x=0.5, y=0.5, mask=m)
            self.assertNotIn("mask", add("../etc/passwd"))                    # traversal
            self.assertNotIn("mask", add("/abs/t_segmask_0.png"))            # absolute path
            self.assertNotIn("mask", add("t_missing.png"))                   # wrong name pattern
            # substring-collision + foreign-subdir are now rejected (anchored prefix, not `in`):
            for bad in ("tother_segmask_0.png", "other/t_segmask_0.png", ".lucid-priv-x/t_segmask_0.png"):
                with open(os.path.join(d, os.path.basename(bad)), "wb") as f:
                    f.write(b"x")                                            # exists, but still rejected by shape
                self.assertNotIn("mask", add(bad), bad)
        finally:
            L.E.INPUT_DIR = orig; shutil.rmtree(d, ignore_errors=True)

    def test_remove_note_keeps_a_shared_content_addressed_mask(self):
        # content-addressed masks may be shared by two notes; removing one must NOT blank the other's silhouette.
        import tempfile, shutil
        d = tempfile.mkdtemp(); orig = L.E.INPUT_DIR; L.E.INPUT_DIR = d
        try:
            p = os.path.join(d, "t_segmask_abc123.png")
            with open(p, "wb") as f:
                f.write(b"x")
            a = L.add_note("t", 1, 0.0, "more", "", x=0.5, y=0.5, mask="t_segmask_abc123.png")
            b = L.add_note("t", 1, 0.5, "hold", "", x=0.4, y=0.4, mask="t_segmask_abc123.png")
            self.assertTrue(L.remove_note("t", 1, a["id"]))
            self.assertTrue(os.path.exists(p))                               # still referenced by b -> kept
            self.assertTrue(L.remove_note("t", 1, b["id"]))
            self.assertFalse(os.path.exists(p))                             # last reference gone -> unlinked
        finally:
            L.E.INPUT_DIR = orig; shutil.rmtree(d, ignore_errors=True)

    def test_remove_note_unlinks_mask(self):
        import tempfile, shutil
        d = tempfile.mkdtemp(); orig = L.E.INPUT_DIR; L.E.INPUT_DIR = d
        try:
            p = os.path.join(d, "t_segmask_9.png")
            with open(p, "wb") as f:
                f.write(b"x")
            note = L.add_note("t", 1, 0.0, "more", "", x=0.5, y=0.5, mask="t_segmask_9.png")
            self.assertTrue(os.path.exists(p))
            self.assertTrue(L.remove_note("t", 1, note["id"]))
            self.assertFalse(os.path.exists(p))                              # mask unlinked with the note
        finally:
            L.E.INPUT_DIR = orig; shutil.rmtree(d, ignore_errors=True)

    def test_add_note_rejects_bad_tag_and_redline_text(self):
        with self.assertRaises(ValueError):
            L.add_note("t", 1, 0.0, "nonsense", "")             # tag not in NOTE_TAGS
        L.S.red_line_ok = lambda text: False                    # the note text fails the red-line gate
        with self.assertRaises(ValueError):
            L.add_note("t", 1, 0.0, "more", "blocked text")     # untrusted text is fail-closed
        with self.assertRaises(ValueError):
            L.add_note("t", 999, 0.0, "more", "")               # unknown node id

    def test_step_anchors_on_hold_note_moment(self):
        # tip (node 1) carries a hold note at t=1.4; the next beat must anchor on the extracted moment
        L.add_note("t", 1, 1.4, "hold", "")
        self.captured_t = None
        orig = L.E.extract_frame_at

        def _extract(clip, t, name, out_path=None):
            self.captured_t = t
            return "anchorframe.png"

        L.E.extract_frame_at = _extract
        try:
            node = L.step("t", "a calm aurora", "l1", is_current=lambda: True)   # continue the tip
        finally:
            L.E.extract_frame_at = orig
        self.assertIsNotNone(node)
        self.assertEqual(self.captured_t, 1.4)                  # extracted at the hold note's t
        self.assertEqual(self.gen_anchors[-1], "anchorframe.png")   # generation anchored on the moment

    def test_step_without_notes_anchors_on_last_frame(self):
        calls = []
        L.E.extract_frame_at = lambda clip, t, name, out_path=None: calls.append(t) or "x.png"
        node = L.step("t", "a quiet sea", "l1", is_current=lambda: True)   # tip has no notes
        self.assertIsNotNone(node)
        self.assertEqual(calls, [])                             # extract_frame_at NOT called
        self.assertEqual(self.gen_anchors[-1], "t_n1.png")     # anchored on the parent's out_frame


class DecomposeTest(unittest.TestCase):
    """Screenshot-tagged-moments -> image-capable decomposition -> i2v prompt (ADR-0023). step() builds
    a `tagged` list (one entry per note: a screenshotted+b64'd frame of the PARENT clip plus the note's
    tag/text/t), hands it to the VLM (E.decompose_notes) for a refined continuation prompt, and gates
    THAT. Fail-open: no notes => decompose not called (legacy base); decompose -> None => the
    deterministic text-suffix fallback. Heavy seams stubbed exactly like NotesTest; generate_video
    records the gated prompt it was handed so the wired path is assertable without a GPU/model."""
    def setUp(self):
        self._orig = {k: getattr(L, k) for k in ("load_chain", "save_chain", "generate_video")}
        self._ispriv, self._frame_ref = L.ST.is_private, L.ST.frame_ref
        self._extract_last, self._extract_at = L.E.extract_last_frame, L.E.extract_frame_at
        self._to_b64, self._decompose = L.E.frame_to_b64, L.E.decompose_notes
        self._gate = L.S.gate_prompt
        # 0(root) -> 1(tip, real clip so each note can extract a moment from it); premise set so we can
        # assert decompose_notes is handed the dream's premise.
        self.chain = {"session": "t", "private": False, "premise": "a calm winter dream", "nodes": [
            {"id": 0, "parent": None, "label": "opening", "out_frame": "t_n0.png", "rating": "sfw",
             "clip": None},
            {"id": 1, "parent": 0, "label": "a", "out_frame": "t_n1.png", "rating": "sfw",
             "clip": "clip1.mp4"}]}
        self.gen_prompts = []      # every prompt generate_video was handed (already gated)
        self.decompose_calls = []  # (beat_prompt, tagged, premise) per E.decompose_notes call

        def _gen(session, prompt, anchor_frame, **k):
            self.gen_prompts.append(prompt)
            return "clip.mp4"

        def _decompose(beat_prompt, tagged, premise=None):
            self.decompose_calls.append((beat_prompt, tagged, premise))
            return "REFINED"                              # default: model returns a refined prompt

        L.load_chain = lambda s: self.chain
        L.save_chain = lambda s, c: setattr(self, "chain", c)
        L.generate_video = _gen
        L.ST.is_private = lambda s: False
        L.ST.frame_ref = lambda s, p, name: (name, f"/tmp/{name}")
        L.E.extract_last_frame = lambda clip, ref, out_path=None: ref
        L.E.extract_frame_at = lambda clip, t, name, out_path=None: name   # extraction "succeeds"
        L.E.frame_to_b64 = lambda path: "B64(" + str(path) + ")"           # truthy b64 for any path
        L.E.decompose_notes = _decompose
        L.S.gate_prompt = lambda p: "GATED:" + p          # identity-ish gate, asserts the wired string

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)
        L.ST.is_private, L.ST.frame_ref = self._ispriv, self._frame_ref
        L.E.extract_last_frame, L.E.extract_frame_at = self._extract_last, self._extract_at
        L.E.frame_to_b64, L.E.decompose_notes = self._to_b64, self._decompose
        L.S.gate_prompt = self._gate

    def test_notes_decompose_into_the_gated_refined_prompt(self):
        # (a) two notes on the tip -> decompose called with a tagged list of length 2 carrying b64/tag/
        #     text, and the generated node's prompt is the GATED REFINED prompt.
        L.add_note("t", 1, 0.5, "more", "the lamp")
        L.add_note("t", 1, 1.4, "change", "the sky")
        node = L.step("t", "a calm aurora", "l1", is_current=lambda: True)
        self.assertIsNotNone(node)
        self.assertEqual(len(self.decompose_calls), 1)
        beat_prompt, tagged, premise = self.decompose_calls[0]
        self.assertEqual(beat_prompt, "a calm aurora")            # the chosen beat is the seed prompt
        self.assertEqual(premise, "a calm winter dream")          # premise threaded through
        self.assertEqual(len(tagged), 2)                          # one entry per note
        tags = sorted(e["tag"] for e in tagged)
        self.assertEqual(tags, ["change", "more"])
        self.assertTrue(all(e["b64"] for e in tagged))            # each carries a (truthy) b64
        self.assertEqual({e["text"] for e in tagged}, {"the lamp", "the sky"})  # ...and its text
        self.assertEqual(self.gen_prompts[-1], "GATED:REFINED")   # node rendered the gated REFINED prompt
        self.assertEqual(node["prompt"], "GATED:REFINED")

    def test_decompose_none_falls_back_to_steering_suffix(self):
        # (b) model unavailable / returns None -> step uses base + _steering_suffix(notes), gated.
        L.E.decompose_notes = lambda beat_prompt, tagged, premise=None: None
        L.add_note("t", 1, 0.5, "more", "the lamp")
        node = L.step("t", "a quiet sea", "l1", is_current=lambda: True)
        self.assertIsNotNone(node)
        self.assertEqual(self.gen_prompts[-1], node["prompt"])
        self.assertTrue(self.gen_prompts[-1].startswith("GATED:"))
        self.assertIn("a quiet sea", self.gen_prompts[-1])        # the base prompt survived
        self.assertIn("emphasize the lamp", self.gen_prompts[-1])  # the steering phrase from the note

    def test_no_notes_skips_decompose_and_gates_the_base(self):
        # (c) no notes -> decompose NOT called; the prompt is just the gated base (legacy).
        node = L.step("t", "the storm holds the lamp", "l1", is_current=lambda: True)
        self.assertIsNotNone(node)
        self.assertEqual(self.decompose_calls, [])               # the VLM seam is never touched
        self.assertEqual(self.gen_prompts[-1], "GATED:the storm holds the lamp")
        self.assertEqual(node["prompt"], "GATED:the storm holds the lamp")

    def test_clipless_opening_b64s_the_stored_frame(self):
        # clip-less node (the opening): there is no clip to extract a moment from, so the note's frame is
        # the parent's STORED out_frame, b64'd via _frame_abs. extract_frame_at must NOT be called.
        calls = []
        L.E.extract_frame_at = lambda clip, t, name, out_path=None: calls.append(t) or name
        L.add_note("t", 0, 0.0, "more", "the dawn")              # note on the clip-less opening (node 0)
        node = L.step("t", "a slow sunrise", "l1", is_current=lambda: True, parent_id=0)
        self.assertIsNotNone(node)
        self.assertEqual(calls, [])                              # no clip -> no moment extraction
        self.assertEqual(len(self.decompose_calls), 1)
        _bp, tagged, _pr = self.decompose_calls[0]
        self.assertEqual(len(tagged), 1)
        self.assertTrue(tagged[0]["b64"])                        # b64'd the parent's stored frame instead


class GuidesTest(unittest.TestCase):
    """LTX guide-conditioning wiring (ADR-0023): the SAME per-note frames step() screenshots for the
    VLM `tagged` path are also collected as (abs_path, t, tag) guides and handed to generate_video —
    but ONLY when the LTX engine (10eros) is active. Wan (current_engine != "10eros") keeps its
    VLM-prompt + single-anchor path and gets guides=None; no notes -> guides=None for either engine.
    Heavy seams stubbed exactly like DecomposeTest; generate_video records ALL kwargs (incl. guides)
    so the wired path is assertable without a GPU/model."""
    def setUp(self):
        self._orig = {k: getattr(L, k) for k in ("load_chain", "save_chain", "generate_video")}
        self._ispriv, self._frame_ref = L.ST.is_private, L.ST.frame_ref
        self._extract_last, self._extract_at = L.E.extract_last_frame, L.E.extract_frame_at
        self._to_b64, self._decompose = L.E.frame_to_b64, L.E.decompose_notes
        self._gate, self._engine = L.S.gate_prompt, L.E.current_engine
        # 0(root) -> 1(tip, real clip so each note can extract a moment from it)
        self.chain = {"session": "t", "private": False, "premise": None, "nodes": [
            {"id": 0, "parent": None, "label": "opening", "out_frame": "t_n0.png", "rating": "sfw",
             "clip": None},
            {"id": 1, "parent": 0, "label": "a", "out_frame": "t_n1.png", "rating": "sfw",
             "clip": "clip1.mp4"}]}
        self.gen_kwargs = []   # the kwargs dict generate_video was handed (incl. guides)

        def _gen(session, prompt, anchor_frame, **k):
            self.gen_kwargs.append(k)
            return "clip.mp4"

        L.load_chain = lambda s: self.chain
        L.save_chain = lambda s, c: setattr(self, "chain", c)
        L.generate_video = _gen
        L.ST.is_private = lambda s: False
        L.ST.frame_ref = lambda s, p, name: (name, f"/tmp/{name}")
        L.E.extract_last_frame = lambda clip, ref, out_path=None: ref
        # extraction "succeeds", returning the abs path it was handed (so the guide path == out_path)
        L.E.extract_frame_at = lambda clip, t, name, out_path=None: out_path
        L.E.frame_to_b64 = lambda path: "B64(" + str(path) + ")"           # truthy b64 for any path
        L.E.decompose_notes = lambda beat_prompt, tagged, premise=None: "REFINED"
        L.S.gate_prompt = lambda p: "GATED:" + p
        L.E.current_engine = lambda: "10eros"          # default: LTX engine active

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)
        L.ST.is_private, L.ST.frame_ref = self._ispriv, self._frame_ref
        L.E.extract_last_frame, L.E.extract_frame_at = self._extract_last, self._extract_at
        L.E.frame_to_b64, L.E.decompose_notes = self._to_b64, self._decompose
        L.S.gate_prompt, L.E.current_engine = self._gate, self._engine

    def test_ltx_engine_gets_guides_per_note(self):
        # (a) engine == "10eros" + two notes -> generate_video handed guides of length 2, each a
        #     (abs_path, t, tag, region) tuple carrying the right t/tag; region is None for time-only notes.
        L.add_note("t", 1, 0.5, "more", "the lamp")
        L.add_note("t", 1, 1.4, "change", "the sky")
        node = L.step("t", "a calm aurora", "l1", is_current=lambda: True)
        self.assertIsNotNone(node)
        self.assertEqual(len(self.gen_kwargs), 1)
        guides = self.gen_kwargs[-1].get("guides")
        self.assertIsNotNone(guides)
        self.assertEqual(len(guides), 2)                       # one guide per note
        for g in guides:
            self.assertEqual(len(g), 5)                        # (path, t, tag, region, mask_abs) — ADR-0032
            self.assertTrue(isinstance(g[0], str) and g[0])    # a (truthy) abs path
            self.assertIsNone(g[3])                            # time-only notes carry no region
            self.assertIsNone(g[4])                            # ...and no segmentation mask
        by_t = {g[1]: g[2] for g in guides}
        self.assertEqual(by_t, {0.5: "more", 1.4: "change"})   # right t -> tag mapping

    def test_ltx_engine_threads_region_when_note_has_xy(self):
        # (a') a note tagged with a spatial point (x,y[,r]) -> the guide tuple's region carries (x,y,r),
        #      clamped; a time-only sibling stays region=None (ADR-0025 amendment).
        L.add_note("t", 1, 0.5, "more", "the lamp", x=0.3, y=0.4, r=0.2)
        L.add_note("t", 1, 1.4, "change", "the sky")           # no point -> region None
        node = L.step("t", "a calm aurora", "l1", is_current=lambda: True)
        self.assertIsNotNone(node)
        guides = self.gen_kwargs[-1].get("guides")
        by_t = {g[1]: g[3] for g in guides}                    # t -> region
        self.assertEqual(by_t[0.5], (0.3, 0.4, 0.2))           # point threaded through
        self.assertIsNone(by_t[1.4])                           # sibling stays time-only

    def test_wan_engine_gets_no_guides(self):
        # (b) engine == "wan" + notes -> guides=None (Wan keeps its VLM-prompt + single-anchor path).
        L.E.current_engine = lambda: "wan"
        L.add_note("t", 1, 0.5, "more", "the lamp")
        node = L.step("t", "a calm aurora", "l1", is_current=lambda: True)
        self.assertIsNotNone(node)
        self.assertEqual(len(self.gen_kwargs), 1)
        self.assertIsNone(self.gen_kwargs[-1].get("guides"))   # Wan fallback: no guide-conditioning

    def test_no_notes_means_no_guides_any_engine(self):
        # (c) no notes -> guides=None regardless of engine (here LTX is active).
        node = L.step("t", "the storm holds the lamp", "l1", is_current=lambda: True)
        self.assertIsNotNone(node)
        self.assertEqual(len(self.gen_kwargs), 1)
        self.assertIsNone(self.gen_kwargs[-1].get("guides"))


class RegionPhraseTest(unittest.TestCase):
    """ADR-0032 per-region steering: a tap (x,y) becomes a coarse, DETERMINISTIC location phrase (a 3x3 grid
    over the frame), and _steering_suffix names that location so the text-only fallback localizes the steer
    the same way the VLM decomposition does. Pure functions — no chain/model/GPU."""
    def test_region_phrase_grid(self):
        self.assertEqual(L._region_phrase(0.5, 0.5), "the center")
        self.assertEqual(L._region_phrase(0.1, 0.1), "the top-left")
        self.assertEqual(L._region_phrase(0.9, 0.9), "the bottom-right")
        self.assertEqual(L._region_phrase(0.5, 0.1), "the top")
        self.assertEqual(L._region_phrase(0.1, 0.5), "the left")
        self.assertIsNone(L._region_phrase(None, 0.5))            # bad coord -> None (code disposes)

    def test_steering_suffix_localizes_a_regional_note(self):
        notes = [{"id": "nt0", "t": 0.5, "tag": "more", "text": "glow brighter", "x": 0.1, "y": 0.1},
                 {"id": "nt1", "t": 1.0, "tag": "less", "text": "", "x": 0.9, "y": 0.5}]
        s = L._steering_suffix(notes)
        self.assertIn("emphasize glow brighter in the top-left", s)
        self.assertIn("less of this in the right", s)             # no text -> default phrase + location

    def test_steering_suffix_legacy_note_has_no_location(self):
        s = L._steering_suffix([{"id": "nt0", "t": 0.0, "tag": "more", "text": "the lamp"}])
        self.assertEqual(s, "; emphasize the lamp")               # no region -> unchanged (byte-compat)


class NotesDigestTest(unittest.TestCase):
    """ADR-0023 staleness gate: the notes_digest binds a reviewed/edited reading to the EXACT
    (parent, notes, typed-prompt) it derived from, so /api/dream can refuse an edit reviewed against a
    different note set. Pure hash over the chain — no model/GPU."""
    def _chain(self):
        return {"session": "t", "nodes": [
            {"id": 0, "parent": None, "label": "opening", "out_frame": "t_n0.png", "clip": None},
            {"id": 1, "parent": 0, "label": "a", "out_frame": "t_n1.png", "clip": "c.mp4",
             "notes": [{"id": "nt0", "t": 0.5, "tag": "more", "text": "the lamp"}]}]}

    def test_digest_stable_and_note_sensitive(self):
        ch = self._chain(); par = ch["nodes"][1]
        d0 = L._notes_digest(ch, par)
        self.assertEqual(d0, L._notes_digest(ch, par))                      # stable for identical notes
        par["notes"][0]["text"] = "the candle"
        self.assertNotEqual(d0, L._notes_digest(ch, par))                   # note edit -> new digest

    def test_digest_is_prompt_independent(self):
        # NOTES-ONLY by design: editing the typed words must NOT change the staleness token (the edited
        # reading is what runs regardless of the words), so a prompt edit can't false-trigger the gate.
        ch = self._chain(); par = ch["nodes"][1]
        self.assertEqual(L._notes_digest(ch, par), L._notes_digest(ch, par))

    def test_digest_changes_when_a_note_is_added(self):
        ch = self._chain(); par = ch["nodes"][1]
        d0 = L._notes_digest(ch, par)
        par["notes"].append({"id": "nt1", "t": 1.0, "tag": "hold", "text": ""})
        self.assertNotEqual(d0, L._notes_digest(ch, par))


class FuseDirectionTest(unittest.TestCase):
    """ADR-0023 fuse-review: fuse_direction assembles the EXACT prompt the next beat would run — notes
    decomposed (or the deterministic suffix), subject folded IN — with NO lease/GPU, so the Shot Card can
    show + let the user correct it. decompose/subject are injected; the frame seams are stubbed so the
    deterministic assembly + the red-line fallback are assertable without a model."""
    def setUp(self):
        self._ispriv, self._frame_ref = L.ST.is_private, L.ST.frame_ref
        self._extract_at, self._to_b64, self._gate = L.E.extract_frame_at, L.E.frame_to_b64, L.S.gate_prompt
        self._load = L.load_chain
        self.chain = {"session": "t", "private": False, "premise": "a winter dream", "nodes": [
            {"id": 0, "parent": None, "label": "opening", "out_frame": "t_n0.png", "clip": None},
            {"id": 1, "parent": 0, "label": "a", "out_frame": "t_n1.png", "clip": "c.mp4",
             "notes": [{"id": "nt0", "t": 0.5, "tag": "more", "text": "the lamp", "x": 0.1, "y": 0.1, "r": 0.2}]}]}
        L.load_chain = lambda s: self.chain
        L.ST.is_private = lambda s: False
        L.ST.frame_ref = lambda s, p, name: (name, f"/tmp/{name}")
        L.E.extract_frame_at = lambda clip, t, name, out_path=None: out_path or name
        L.E.frame_to_b64 = lambda path: "B64"
        L.S.gate_prompt = lambda p: None if "BLOCK" in p else p          # identity-ish; refuses a BLOCK string

    def tearDown(self):
        L.load_chain = self._load
        L.ST.is_private, L.ST.frame_ref = self._ispriv, self._frame_ref
        L.E.extract_frame_at, L.E.frame_to_b64, L.S.gate_prompt = self._extract_at, self._to_b64, self._gate

    def test_fuse_uses_decompose_and_folds_subject(self):
        seen = {}
        def _dec(bp, tagged, premise=None):
            seen["bp"], seen["tagged"], seen["premise"] = bp, tagged, premise
            return "REFINED CONTINUATION"
        res = L.fuse_direction("t", 1, "a calm aurora", _decompose=_dec, _subject=lambda s, c: "A lone figure")
        self.assertTrue(res["ok"])
        self.assertEqual(res["source"], "decompose")
        self.assertEqual(res["subject"], "A lone figure")
        self.assertEqual(res["fused"], "A lone figure. REFINED CONTINUATION")   # subject folded into the text
        self.assertEqual(seen["bp"], "a calm aurora")
        self.assertEqual(seen["premise"], "a winter dream")
        self.assertEqual(seen["tagged"][0]["region"], "the top-left")           # per-region location threaded
        self.assertEqual(len(res["rows"]), 1)
        self.assertEqual(res["rows"][0]["tag"], "more")
        self.assertTrue(res["rows"][0]["region"])                               # row marks it as regional
        self.assertTrue(res["notes_digest"])

    def test_fuse_falls_back_to_suffix_when_decompose_none(self):
        res = L.fuse_direction("t", 1, "a quiet sea", _decompose=lambda *a, **k: None,
                               _subject=lambda s, c: "")
        self.assertTrue(res["ok"])
        self.assertEqual(res["source"], "suffix")
        self.assertIn("a quiet sea", res["fused"])
        self.assertIn("emphasize the lamp in the top-left", res["fused"])       # deterministic + localized

    def test_fuse_redline_model_output_falls_back_to_suffix(self):
        # a model that returns a red-lined fusion is NOT shown — fall back to the deterministic suffix + re-gate.
        res = L.fuse_direction("t", 1, "a quiet sea", _decompose=lambda *a, **k: "BLOCK this",
                               _subject=lambda s, c: "")
        self.assertTrue(res["ok"])
        self.assertEqual(res["source"], "suffix")
        self.assertNotIn("BLOCK", res["fused"])

    def test_allow_model_false_skips_decompose(self):
        # TURN-phase backpressure: allow_model=False never touches the VLM seam, straight to the suffix reading.
        called = []
        res = L.fuse_direction("t", 1, "a calm aurora",
                               _decompose=lambda *a, **k: called.append(1) or "REFINED",
                               _subject=lambda s, c: "", allow_model=False)
        self.assertEqual(called, [])                                            # decompose NOT called
        self.assertEqual(res["source"], "suffix")
        self.assertIn("emphasize the lamp in the top-left", res["fused"])       # deterministic reading

    def test_fuse_no_notes_is_just_the_subject_folded_prompt(self):
        self.chain["nodes"][1]["notes"] = []
        called = []
        res = L.fuse_direction("t", 1, "the lamp flickers",
                               _decompose=lambda *a, **k: called.append(1) or "X",
                               _subject=lambda s, c: "A figure")
        self.assertEqual(called, [])                                            # no notes -> decompose untouched
        self.assertEqual(res["fused"], "A figure. the lamp flickers")
        self.assertEqual(res["rows"], [])


class FusedEditedStepTest(unittest.TestCase):
    """ADR-0023: step(fused_edited=...) runs the user-reviewed prompt VERBATIM — no re-decompose, no subject
    re-prefix — while the LTX pixel guides still derive from the notes (a text edit never disables a mask).
    Heavy seams stubbed like GuidesTest; decompose/_subject_for are tripwires that must NOT fire."""
    def setUp(self):
        self._orig = {k: getattr(L, k) for k in ("load_chain", "save_chain", "generate_video", "_subject_for")}
        self._ispriv, self._frame_ref = L.ST.is_private, L.ST.frame_ref
        self._extract_last, self._extract_at = L.E.extract_last_frame, L.E.extract_frame_at
        self._to_b64, self._decompose = L.E.frame_to_b64, L.E.decompose_notes
        self._gate, self._engine = L.S.gate_prompt, L.E.current_engine
        self.chain = {"session": "t", "private": False, "premise": None, "nodes": [
            {"id": 0, "parent": None, "label": "opening", "out_frame": "t_n0.png", "rating": "sfw", "clip": None},
            {"id": 1, "parent": 0, "label": "a", "out_frame": "t_n1.png", "rating": "sfw", "clip": "clip1.mp4"}]}
        self.gen_prompts, self.gen_kwargs, self.decompose_calls, self.subject_calls = [], [], [], []

        def _gen(session, prompt, anchor_frame, **k):
            self.gen_prompts.append(prompt); self.gen_kwargs.append(k); return "clip.mp4"

        L.load_chain = lambda s: self.chain
        L.save_chain = lambda s, c: setattr(self, "chain", c)
        L.generate_video = _gen
        L._subject_for = lambda s, c: self.subject_calls.append(1) or "SHOULD-NOT-PREFIX"
        L.ST.is_private = lambda s: False
        L.ST.frame_ref = lambda s, p, name: (name, f"/tmp/{name}")
        L.E.extract_last_frame = lambda clip, ref, out_path=None: ref
        L.E.extract_frame_at = lambda clip, t, name, out_path=None: out_path
        L.E.frame_to_b64 = lambda path: "B64"
        L.E.decompose_notes = lambda *a, **k: self.decompose_calls.append(1) or "SHOULD-NOT-DECOMPOSE"
        L.S.gate_prompt = lambda p: "GATED:" + p
        L.E.current_engine = lambda: "10eros"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)
        L.ST.is_private, L.ST.frame_ref = self._ispriv, self._frame_ref
        L.E.extract_last_frame, L.E.extract_frame_at = self._extract_last, self._extract_at
        L.E.frame_to_b64, L.E.decompose_notes = self._to_b64, self._decompose
        L.S.gate_prompt, L.E.current_engine = self._gate, self._engine

    def test_fused_edited_runs_verbatim_skipping_decompose_and_subject(self):
        L.add_note("t", 1, 0.5, "more", "the lamp", x=0.3, y=0.4)
        node = L.step("t", "a calm aurora", "custom", is_current=lambda: True,
                      fused_edited="A figure. the lamp flares as the room dims")
        self.assertIsNotNone(node)
        self.assertEqual(self.decompose_calls, [])                  # the VLM seam is NOT touched
        self.assertEqual(self.subject_calls, [])                    # subject is NOT re-prefixed
        self.assertEqual(node["prompt"], "GATED:A figure. the lamp flares as the room dims")
        guides = self.gen_kwargs[-1].get("guides")                  # structured channel still derives from notes
        self.assertIsNotNone(guides)
        self.assertEqual(len(guides), 1)
        self.assertEqual(guides[0][3], (0.3, 0.4, L.DEFAULT_NOTE_RADIUS))   # the region survived the text edit

    def test_no_fused_edited_keeps_the_decompose_path(self):
        L.add_note("t", 1, 0.5, "more", "the lamp")
        node = L.step("t", "a calm aurora", "custom", is_current=lambda: True)   # no fused_edited
        self.assertEqual(len(self.decompose_calls), 1)              # legacy path: decompose IS called
        self.assertEqual(len(self.subject_calls), 1)                # ...and subject IS prefixed


class DeclaredRatingFloorTest(unittest.TestCase):
    """The user-declared 'Mature dream' floor (chain['rating_floor']). The per-frame VLM is conservative,
    so a mature-INTENDED dream off a tame frame must still steer mature from frame 0. Stubs the model seams
    (frame->b64, ground_frame returns sfw with no model, propose) and asserts the floor wins, monotone-up,
    and is a strict no-op when absent (today's behaviour). Pure logic — no model/GPU/store."""
    def setUp(self):
        import lucid_engine as E
        self.E = E
        self._orig = {"f2b": E.frame_to_b64, "fabs": L._frame_abs,
                      "propose": L.propose, "load": L.load_chain}
        E.frame_to_b64 = lambda p: None           # ungrounded -> ground_frame returns (None,'sfw'), no model
        L._frame_abs = lambda session, node: "x.png"
        L.load_chain = lambda s: self.chain        # context_for re-loads the chain -> serve the test one
        self.chain = None
        self.seen = {}

        def _propose(ctx, n=4, rating="sfw", frame_b64=None):
            self.seen["rating"] = rating          # capture the steering tier the menu was rolled with
            return [{"label": "x", "prompt": "y"}]
        L.propose = _propose

    def tearDown(self):
        self.E.frame_to_b64 = self._orig["f2b"]
        L._frame_abs = self._orig["fabs"]
        L.propose = self._orig["propose"]
        L.load_chain = self._orig["load"]

    def _chain(self, floor):
        return {"session": "t", "premise": None, "rating_floor": floor,
                "nodes": [{"id": 0, "parent": None, "label": "opening", "out_frame": "t_n0.png"}]}

    def test_declared_floor_steers_mature_on_a_sfw_frame(self):
        self.chain = self._chain("mature")
        _beats, _cap, rating = L.roll_menu("t", self.chain)
        self.assertEqual(rating, "mature")             # floor wins over the sfw VLM rating
        self.assertEqual(self.seen["rating"], "mature")  # ...and the menu was actually steered mature

    def test_absent_floor_is_a_no_op(self):
        self.chain = self._chain(None)
        _beats, _cap, rating = L.roll_menu("t", self.chain)
        self.assertEqual(rating, "sfw")                # unchanged: pure-VLM behaviour
        self.assertEqual(self.seen["rating"], "sfw")

    def test_start_persists_only_a_valid_floor(self):
        # start() builds chain['rating_floor'] = 'mature' iff exactly 'mature', else None (validated, not trusted).
        import inspect
        src = inspect.getsource(L.start)
        self.assertIn('"rating_floor": "mature" if rating_floor == "mature" else None', src)


if __name__ == "__main__":
    unittest.main()

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

    def test_remove_note(self):
        note = L.add_note("t", 1, 0.0, "more", "")
        self.assertTrue(L.remove_note("t", 1, note["id"]))      # removed
        node = next(n for n in self.chain["nodes"] if n["id"] == 1)
        self.assertEqual(node.get("notes"), [])                 # gone
        self.assertFalse(L.remove_note("t", 1, note["id"]))     # idempotent: a second delete is a no-op

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
        #     (abs_path, t, tag) tuple carrying the right t/tag.
        L.add_note("t", 1, 0.5, "more", "the lamp")
        L.add_note("t", 1, 1.4, "change", "the sky")
        node = L.step("t", "a calm aurora", "l1", is_current=lambda: True)
        self.assertIsNotNone(node)
        self.assertEqual(len(self.gen_kwargs), 1)
        guides = self.gen_kwargs[-1].get("guides")
        self.assertIsNotNone(guides)
        self.assertEqual(len(guides), 2)                       # one guide per note
        for g in guides:
            self.assertEqual(len(g), 3)                        # (path, t, tag)
            self.assertTrue(isinstance(g[0], str) and g[0])    # a (truthy) abs path
        by_t = {g[1]: g[2] for g in guides}
        self.assertEqual(by_t, {0.5: "more", 1.4: "change"})   # right t -> tag mapping

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


if __name__ == "__main__":
    unittest.main()

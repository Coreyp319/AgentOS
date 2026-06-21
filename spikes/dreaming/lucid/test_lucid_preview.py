#!/usr/bin/env python3
"""Per-choice "potential path" preview invariants (ADR-0023): the gutter choice cards each render their OWN
preview still — generated SERIALLY under the warm lease during the decision dwell — instead of all showing the
same seed image. No GPU/daemon: the generation seam (generate_video / extract / lease primitives) and the chain
store are stubbed, so this exercises only the orchestration, the content-addressing, the path validator, and the
fail-open + cancel-aware worker.

Two layers:
  * lucid_linear: generate_beat_preview (idempotent, gated, deterministic-distinct seed, sealed-for-private,
    transient clip deleted, fail-open), decorate_beats (key + preview-if-on-disk), _beat_key, _valid_preview_ref.
  * lucid_web: _run_previews worker (warm-lease-only, real-beat-always-wins, headroom/busy gated, cancel-epoch),
    _cancel_previews.
"""
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_linear as L   # noqa: E402
import lucid_web as W      # noqa: E402


class PreviewBackend(unittest.TestCase):
    """generate_beat_preview + decorate_beats + the helpers, with the heavy seams stubbed onto a tmp INPUT_DIR
    so the real os.path.exists / os.remove are exercised."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = {k: getattr(L, k) for k in ("load_chain", "generate_video")}
        self._ispriv, self._frame_ref, self._frame_abs = L.ST.is_private, L.ST.frame_ref, L.ST.frame_abs
        self._extract, self._gate = L.E.extract_last_frame, L.S.gate_prompt
        self._input_dir = L.E.INPUT_DIR
        L.E.INPUT_DIR = self.tmp
        # 0(root) -> 1(tip): node 1 is the frame previews grow from
        self.chain = {"session": "t", "seed": 1000, "private": False, "nodes": [
            {"id": 0, "parent": None, "label": "opening", "out_frame": "t_n0.png", "rating": "sfw"},
            {"id": 1, "parent": 0, "label": "a", "out_frame": "t_n1.png", "rating": "sfw"}]}
        self.gen_calls = []

        def _frame_ref(s, p, name):
            sub = os.path.join(self.tmp, f".lucid-priv-{s}") if p else self.tmp
            os.makedirs(sub, exist_ok=True)
            ref = (f".lucid-priv-{s}/{name}" if p else name)
            return ref, os.path.join(sub, name)

        def _frame_abs(s, p, name):
            base = os.path.basename(name)
            return (os.path.join(self.tmp, f".lucid-priv-{s}", base) if p
                    else os.path.join(self.tmp, base))

        def _gen(session, prompt, anchor_frame, **k):
            self.gen_calls.append({"prompt": prompt, "anchor": anchor_frame, **k})
            clip = os.path.join(self.tmp, "clip_transient.mp4")
            with open(clip, "wb") as f:
                f.write(b"mp4")
            return clip

        def _extract(clip, ref, out_path=None):
            with open(out_path, "wb") as f:
                f.write(b"png")
            return ref

        L.load_chain = lambda s: self.chain
        L.generate_video = _gen
        L.ST.is_private = lambda s: False
        L.ST.frame_ref = _frame_ref
        L.ST.frame_abs = _frame_abs
        L.E.extract_last_frame = _extract
        L.S.gate_prompt = lambda p: p   # identity gate

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)
        L.ST.is_private, L.ST.frame_ref, L.ST.frame_abs = self._ispriv, self._frame_ref, self._frame_abs
        L.E.extract_last_frame, L.S.gate_prompt = self._extract, self._gate
        L.E.INPUT_DIR = self._input_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- _beat_key ----
    def test_beat_key_stable_and_distinct(self):
        a = {"label": "x", "prompt": "go left"}
        self.assertEqual(L._beat_key(a), L._beat_key(dict(a)))           # stable for identical content
        self.assertNotEqual(L._beat_key(a), L._beat_key({"label": "x", "prompt": "go right"}))

    # ---- generate_beat_preview ----
    def test_renders_extracts_and_deletes_transient_clip(self):
        beat = {"label": "a", "prompt": "she turns toward the sea"}
        # plant a VHS-style metadata sidecar next to the clip — it must be reaped with the .mp4 (consult P4)
        with open(os.path.join(self.tmp, "clip_transient.png"), "wb") as f:
            f.write(b"sidecar-prompt")
        ref = L.generate_beat_preview("t", 1, beat)
        key = L._beat_key(beat)
        self.assertEqual(ref, f"t_bp_1_{key}.png")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, ref)))     # the still landed on disk
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "clip_transient.mp4")))  # transient deleted
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "clip_transient.png")))  # ...and its sidecar (no prompt residue)
        g = self.gen_calls[-1]
        self.assertEqual(g["anchor"], "t_n1.png")                        # grows from node 1's conditioning frame
        self.assertEqual(g["length"], L.E.MIN_LEN)                       # cheapest: the min-length draft
        self.assertEqual(g["quality"], "draft")
        self.assertTrue(g["external_lease"])                             # rides the warm lease (no Spawn/Release)
        self.assertEqual(g["rating"], "sfw")                            # ancestry floor

    def test_idempotent_when_preview_already_on_disk(self):
        beat = {"label": "a", "prompt": "p"}
        key = L._beat_key(beat)
        with open(os.path.join(self.tmp, f"t_bp_1_{key}.png"), "wb") as f:
            f.write(b"png")                                              # a held preview already exists
        ref = L.generate_beat_preview("t", 1, beat)
        self.assertEqual(ref, f"t_bp_1_{key}.png")
        self.assertEqual(self.gen_calls, [])                            # no re-render (held like the menu)

    def test_gate_refusal_skips_render(self):
        L.S.gate_prompt = lambda p: None                                # red-line refuses the prompt
        self.assertIsNone(L.generate_beat_preview("t", 1, {"label": "a", "prompt": "bad"}))
        self.assertEqual(self.gen_calls, [])                            # never render an ungated prompt

    def test_seed_is_deterministic_and_distinct_across_siblings(self):
        L.generate_beat_preview("t", 1, {"label": "a", "prompt": "left"})
        s1 = self.gen_calls[-1]["seed"]
        os.remove(os.path.join(self.tmp, f"t_bp_1_{L._beat_key({'label':'a','prompt':'left'})}.png"))
        L.generate_beat_preview("t", 1, {"label": "a", "prompt": "left"})
        s1b = self.gen_calls[-1]["seed"]
        L.generate_beat_preview("t", 1, {"label": "b", "prompt": "right"})
        s2 = self.gen_calls[-1]["seed"]
        self.assertEqual(s1, s1b)                                        # reproducible (same node+beat)
        self.assertNotEqual(s1, s2)                                      # distinct siblings -> distinct paths

    def test_private_seals_into_subdir(self):
        L.ST.is_private = lambda s: True
        ref = L.generate_beat_preview("t", 1, {"label": "a", "prompt": "p"})
        self.assertTrue(ref.startswith(".lucid-priv-t/"))               # sealed ref (burned with the session)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, ref)))

    def test_fail_open_on_generation_error(self):
        def _boom(*a, **k):
            raise RuntimeError("preempted")
        L.generate_video = _boom
        self.assertIsNone(L.generate_beat_preview("t", 1, {"label": "a", "prompt": "p"}))

    def test_fail_open_when_extract_returns_none(self):
        L.E.extract_last_frame = lambda clip, ref, out_path=None: None
        self.assertIsNone(L.generate_beat_preview("t", 1, {"label": "a", "prompt": "p"}))

    # ---- decorate_beats ----
    def test_decorate_adds_key_and_preview_only_when_on_disk(self):
        a = {"label": "a", "prompt": "1"}
        b = {"label": "b", "prompt": "2"}
        with open(os.path.join(self.tmp, f"t_bp_1_{L._beat_key(a)}.png"), "wb") as f:
            f.write(b"png")                                             # a's preview rendered; b's not yet
        out = L.decorate_beats("t", None, [a, b])                       # node None -> resolves to tip (id 1)
        self.assertEqual(out[0]["key"], L._beat_key(a))
        self.assertEqual(out[0]["preview"], f"t_bp_1_{L._beat_key(a)}.png")
        self.assertIsNone(out[1]["preview"])                            # absent -> null (card stays seed still)
        self.assertEqual(out[1]["key"], L._beat_key(b))

    def test_decorate_private_uses_sealed_subpath(self):
        L.ST.is_private = lambda s: True
        a = {"label": "a", "prompt": "1"}
        sub = os.path.join(self.tmp, ".lucid-priv-t")
        os.makedirs(sub, exist_ok=True)
        with open(os.path.join(sub, f"t_bp_1_{L._beat_key(a)}.png"), "wb") as f:
            f.write(b"png")
        out = L.decorate_beats("t", 1, [a])
        self.assertEqual(out[0]["preview"], f".lucid-priv-t/t_bp_1_{L._beat_key(a)}.png")

    def test_decorate_empty_is_passthrough(self):
        self.assertEqual(L.decorate_beats("t", 1, []), [])

    # ---- _valid_preview_ref (twin of _valid_mask_ref) ----
    def test_valid_preview_ref(self):
        good = "t_bp_1_abc123.png"
        with open(os.path.join(self.tmp, good), "wb") as f:
            f.write(b"x")
        self.assertEqual(L._valid_preview_ref("t", good), good)
        # rejected shapes (each must fail-closed -> None)
        for bad in ("../etc/passwd", "/abs/t_bp_1_abc.png", "t_missing_1_abc.png",
                    "tother_bp_1_abc123.png", "other/t_bp_1_abc123.png",
                    ".lucid-priv-x/t_bp_1_abc123.png", "t_bp_x_abc.png", "t_bp_1_NOTHEX.png"):
            self.assertIsNone(L._valid_preview_ref("t", bad), bad)
        self.assertIsNone(L._valid_preview_ref("t", "t_bp_1_deadbeef.png"))  # well-formed but absent on disk
        self.assertIsNone(L._valid_preview_ref("t", ""))


class PreviewWorker(unittest.TestCase):
    """_run_previews: serial, warm-lease-only, real-beat-always-wins, headroom/busy gated, cancel-epoch aware —
    every guard fail-open. The generation seam (L.generate_beat_preview) and the VRAM probes are stubbed."""

    def setUp(self):
        W.CURRENT_TOKEN = "tok"                       # a warm lease is held (the normal dwell precondition)
        W.TOKEN_DEADLINE = None
        with W.TURN_LOCK:
            W.TURN.update(phase="idle", label=None, error=None, started=None)
        with W.PREVIEW_LOCK:
            W.PREVIEW_EPOCH = 0
            W.PREVIEW_ACTIVE = False
        W._PREVIEW_SEM = threading.BoundedSemaphore(1)
        self._was_enabled = W.PREVIEWS_ENABLED
        W.PREVIEWS_ENABLED = True
        self._orig_gen = L.generate_beat_preview
        self._busy, self._free, self._ispriv = L.E._comfy_busy, L.E._comfy_free_mib, L.ST.is_private
        self.calls = []
        L.generate_beat_preview = lambda session, node_id, beat, external_lease=True: \
            self.calls.append((node_id, beat["label"]))
        L.E._comfy_busy = lambda: False              # not queued behind a render
        L.E._comfy_free_mib = lambda: 9999           # plenty of headroom
        L.ST.is_private = lambda s: False            # public dream (the private gate is exercised explicitly)

    def tearDown(self):
        L.generate_beat_preview = self._orig_gen
        L.E._comfy_busy, L.E._comfy_free_mib, L.ST.is_private = self._busy, self._free, self._ispriv
        W.PREVIEWS_ENABLED = self._was_enabled
        W.CURRENT_TOKEN = None
        with W.TURN_LOCK:
            W.TURN.update(phase="idle")

    def _beats(self, n=2):
        return [{"label": chr(ord("a") + i), "prompt": str(i)} for i in range(n)]

    def test_renders_each_beat_in_order(self):
        W._run_previews(7, self._beats(3), W.PREVIEW_EPOCH, "t")
        self.assertEqual(self.calls, [(7, "a"), (7, "b"), (7, "c")])

    def test_caps_at_preview_max(self):
        W._run_previews(1, self._beats(W.PREVIEW_MAX + 3), W.PREVIEW_EPOCH, "t")
        self.assertEqual(len(self.calls), W.PREVIEW_MAX)            # never more than the per-dwell cap

    def test_skips_when_no_warm_lease(self):
        W.CURRENT_TOKEN = None                                     # never spawn just to preview
        W._run_previews(1, self._beats(), W.PREVIEW_EPOCH, "t")
        self.assertEqual(self.calls, [])

    def test_real_beat_always_wins(self):
        with W.TURN_LOCK:
            W.TURN.update(phase="dreaming")                        # a real beat / hero finalize is in flight
        W._run_previews(1, self._beats(), W.PREVIEW_EPOCH, "t")
        self.assertEqual(self.calls, [])

    def test_private_dream_renders_no_previews(self):
        # PRIVACY (consult 2026-06-21): never speculate on un-taken paths for a private/incognito dream.
        L.ST.is_private = lambda s: True
        W._run_previews(1, self._beats(), W.PREVIEW_EPOCH, "t")
        self.assertEqual(self.calls, [])

    def test_env_kill_switch_disables_previews(self):
        W.PREVIEWS_ENABLED = False                                 # LUCID_PREVIEWS=0 server kill-switch
        W._run_previews(1, self._beats(), W.PREVIEW_EPOCH, "t")
        self.assertEqual(self.calls, [])

    def test_defers_when_comfy_busy(self):
        L.E._comfy_busy = lambda: True
        W._run_previews(1, self._beats(), W.PREVIEW_EPOCH, "t")
        self.assertEqual(self.calls, [])

    def test_skips_below_headroom_floor(self):
        L.E._comfy_free_mib = lambda: 10                           # below PREVIEW_HEADROOM_MIB
        W._run_previews(1, self._beats(), W.PREVIEW_EPOCH, "t")
        self.assertEqual(self.calls, [])

    def test_skips_when_comfy_cold(self):
        L.E._comfy_free_mib = lambda: None                        # ComfyUI unreachable -> do not preview
        W._run_previews(1, self._beats(), W.PREVIEW_EPOCH, "t")
        self.assertEqual(self.calls, [])

    def test_stale_epoch_renders_nothing(self):
        stale = W.PREVIEW_EPOCH - 1                                # a newer dwell already superseded this run
        W._run_previews(1, self._beats(), stale, "t")
        self.assertEqual(self.calls, [])

    def test_cancel_mid_run_stops_the_queue(self):
        ep = W.PREVIEW_EPOCH

        def _gen(session, node_id, beat, external_lease=True):
            self.calls.append(beat["label"])
            W._cancel_previews()                                   # a pick / start lands mid-run
        L.generate_beat_preview = _gen
        W._run_previews(1, self._beats(3), ep, "t")
        self.assertEqual(self.calls, ["a"])                       # stopped before the second render

    def test_cancel_previews_bumps_epoch(self):
        e0 = W.PREVIEW_EPOCH
        W._cancel_previews()
        self.assertEqual(W.PREVIEW_EPOCH, e0 + 1)


if __name__ == "__main__":
    unittest.main()

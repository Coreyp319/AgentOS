#!/usr/bin/env python3
"""Honest failure-surface tests (dream->ComfyUI path audit, 2026-06-21).

The audit found that when a dream legitimately cannot run, the surface LIED: a structural admission
refusal (a game holding VRAM) was painted "try again in a moment"; a real OOM / backend-down / bad
graph / timeout was swallowed and reported as the calm "skipped — desktop untouched" (or a false
"likely preempted (SIGKILL)"). These tests pin the fix:
  * lease_spawn captures the refusal reason (LAST_REFUSAL) instead of discarding it;
  * generate_video distinguishes a genuine PREEMPT (yield calmly) from a SUBSTANTIVE failure (raise a
    typed GenerationError) — opt-in via raise_errors so legacy fail-open callers are byte-unchanged;
  * _run_turn surfaces a substantive failure as phase="error" with an honest user_msg, and a refusal
    as a "skipped" that SAYS WHY;
  * comfy_client._newest_video's fallback is scoped to the run's prefix (no cross-session clip bleed).
"""
import os
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_linear as L   # noqa: E402
import lucid_web as W      # noqa: E402
import comfy_client as cc  # noqa: E402


class _Reply:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class RefusalParseTest(unittest.TestCase):
    def test_extracts_deficit_numbers(self):
        r = L._parse_refusal('0 "denied: short 2688M (free 15374M vs est 17000M + headroom 1062M)"')
        self.assertEqual(r["kind"], "refused")
        self.assertEqual((r["short_mib"], r["free_mib"], r["est_mib"]), (2688, 15374, 17000))

    def test_tolerates_unparseable(self):
        r = L._parse_refusal("GPU busy")
        self.assertEqual(r["kind"], "refused")
        self.assertNotIn("short_mib", r)


class LeaseSpawnRefusalTest(unittest.TestCase):
    def setUp(self):
        self._coord, self._last = L._coord, L.LAST_REFUSAL
        L.LAST_REFUSAL = "sentinel"

    def tearDown(self):
        L._coord, L.LAST_REFUSAL = self._coord, self._last

    def test_granted_clears_last_refusal(self):
        L._coord = lambda *a: _Reply('bts true 7 "granted batch token 7 (free 19750M)"')
        self.assertEqual(L.lease_spawn("batch"), "7")
        self.assertIsNone(L.LAST_REFUSAL)

    def test_refused_captures_reason(self):
        L._coord = lambda *a: _Reply('bts false 0 "denied: short 2688M (free 15374M vs est 17000M + headroom 1062M)"')
        self.assertIsNone(L.lease_spawn("batch"))
        self.assertEqual(L.LAST_REFUSAL["kind"], "refused")
        self.assertEqual(L.LAST_REFUSAL["short_mib"], 2688)

    def test_unreachable_captures_kind(self):
        L._coord = lambda *a: _Reply("", "Connection refused", 1)
        self.assertIsNone(L.lease_spawn("batch"))
        self.assertEqual(L.LAST_REFUSAL["kind"], "unreachable")


class ClassifyTest(unittest.TestCase):
    def setUp(self):
        self._holder = L._coord_holder_tier

    def tearDown(self):
        L._coord_holder_tier = self._holder

    def test_runtime_error_is_substantive(self):
        L._coord_holder_tier = lambda: "interactive"   # even with a higher holder, a reported error is real
        self.assertEqual(L._classify_generation_failure(RuntimeError("generation errored: OOM")), "error")

    def test_timeout_is_substantive_not_unreachable(self):
        # TimeoutError subclasses OSError — must NOT be mistaken for an unreachable/transport error.
        self.assertFalse(L._is_unreachable_error(TimeoutError("did not finish in 1800s")))
        L._coord_holder_tier = lambda: "interactive"
        self.assertEqual(L._classify_generation_failure(TimeoutError("slow")), "error")

    def test_connection_error_with_interactive_holder_is_preempt(self):
        L._coord_holder_tier = lambda: "interactive"
        self.assertEqual(L._classify_generation_failure(urllib.error.URLError("Connection refused")), "preempt")

    def test_connection_error_without_holder_is_error(self):
        L._coord_holder_tier = lambda: None     # ComfyUI gone but no higher holder -> a crash, surface it
        self.assertEqual(L._classify_generation_failure(urllib.error.URLError("Connection refused")), "error")

    def test_human_msg_for_oom(self):
        self.assertIn("memory", L._human_gen_error(RuntimeError("torch.OutOfMemoryError: ran out of memory")).lower())


class GenerateVideoRaiseTest(unittest.TestCase):
    """generate_video: raise_errors=True surfaces substantive failures; the default stays fail-open None."""
    def setUp(self):
        self._env = os.environ.pop("LUCID_GEN_CMD", None)
        self._force, self._priv, self._wait, self._runbeat, self._holder = (
            L.S.force_evict, L.ST.is_private, L.wait_ready, L.E.run_beat, L._coord_holder_tier)
        L.S.force_evict = lambda m: True
        L.ST.is_private = lambda s: False
        L.wait_ready = lambda: True
        L._coord_holder_tier = lambda: None

    def tearDown(self):
        if self._env is not None:
            os.environ["LUCID_GEN_CMD"] = self._env
        (L.S.force_evict, L.ST.is_private, L.wait_ready, L.E.run_beat, L._coord_holder_tier) = (
            self._force, self._priv, self._wait, self._runbeat, self._holder)

    def _raise(self, exc):
        def _f(*a, **k):
            raise exc
        L.E.run_beat = _f

    def test_oom_raises_generation_error_when_opted_in(self):
        self._raise(RuntimeError("generation errored: torch.OutOfMemoryError: ran out of memory on your GPU"))
        with self.assertRaises(L.GenerationError) as cm:
            L.generate_video("t", "p", "a.png", external_lease=True, raise_errors=True)
        self.assertIn("memory", cm.exception.user_msg.lower())

    def test_substantive_failure_swallowed_to_none_by_default(self):
        self._raise(RuntimeError("generation errored: OOM"))
        self.assertIsNone(L.generate_video("t", "p", "a.png", external_lease=True, raise_errors=False))

    def test_preempt_connection_error_yields_none_even_when_opted_in(self):
        L._coord_holder_tier = lambda: "interactive"
        self._raise(urllib.error.URLError("Connection refused"))
        self.assertIsNone(L.generate_video("t", "p", "a.png", external_lease=True, raise_errors=True))

    def test_backend_down_without_holder_raises(self):
        L._coord_holder_tier = lambda: None
        self._raise(urllib.error.URLError("Connection refused"))
        with self.assertRaises(L.GenerationError):
            L.generate_video("t", "p", "a.png", external_lease=True, raise_errors=True)


class NewestVideoScopeTest(unittest.TestCase):
    def setUp(self):
        self._out = cc.OUTPUT_DIR
        self.tmp = tempfile.mkdtemp()
        cc.OUTPUT_DIR = self.tmp

    def tearDown(self):
        cc.OUTPUT_DIR = self._out
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, rel, mtime):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
        os.utime(p, (mtime, mtime))
        return p

    def test_prefix_excludes_other_sessions_clip(self):
        mine = self._mk("lucid/dream-abc/clip_00001.mp4", 8e8)
        self._mk("web/clip_00009.mp4", 9e8)            # FOREIGN + newer
        self.assertEqual(cc._newest_video(since=0, prefix="lucid/dream-abc/clip"), [mine])

    def test_no_prefix_is_global_newest(self):
        self._mk("x/a.mp4", 8e8)
        b = self._mk("y/b.mp4", 9e8)
        self.assertEqual(cc._newest_video(since=0), [b])

    def test_output_prefix_read_from_graph(self):
        graph = {"7": {"class_type": "VHS_VideoCombine",
                       "inputs": {"filename_prefix": "lucid/dream-abc/clip"}}}
        self.assertEqual(cc._output_prefix(graph), "lucid/dream-abc/clip")


class RunTurnHonestyTest(unittest.TestCase):
    """The web worker maps each outcome to an HONEST phase + reason."""
    def setUp(self):
        W.CURRENT_TOKEN = None
        W.TOKEN_DEADLINE = None
        with W.TURN_LOCK:
            W.TURN.update(phase="idle", label=None, error=None, started=None)
        self._admit = (W.ADMIT_RETRIES, W.ADMIT_BACKOFF)
        W.ADMIT_BACKOFF = 0.0
        self._orig = (L.lease_spawn, L.lease_release, L.wait_ready, W._http_ok, L.step, L.LAST_REFUSAL)
        L.lease_release = lambda t: None
        L.wait_ready = lambda: True
        W._http_ok = lambda url, timeout=1.5: True

    def tearDown(self):
        (L.lease_spawn, L.lease_release, L.wait_ready, W._http_ok, L.step, L.LAST_REFUSAL) = self._orig
        (W.ADMIT_RETRIES, W.ADMIT_BACKOFF) = self._admit

    def _phase(self):
        with W.TURN_LOCK:
            return W.TURN["phase"], W.TURN["error"]

    def test_substantive_failure_is_error_not_skipped(self):
        L.lease_spawn = lambda tier="batch": "tok1"
        L.step = lambda *a, **k: (_ for _ in ()).throw(
            L.GenerationError("The graphics card ran out of memory on this clip — try a shorter beat.", cause="OOM"))
        W._run_turn("a beat", "l1")
        phase, err = self._phase()
        self.assertEqual(phase, "error")
        self.assertIn("memory", err.lower())

    def test_structural_refusal_skip_says_why(self):
        W.ADMIT_RETRIES = 0
        L.lease_spawn = lambda tier="batch": None
        L.LAST_REFUSAL = {"kind": "refused", "short_mib": 2688, "free_mib": 15374, "est_mib": 17000, "reason": "x"}
        W._run_turn("a beat", "l1")
        phase, err = self._phase()
        self.assertEqual(phase, "skipped")
        self.assertIsNotNone(err)
        self.assertIn("another app", err.lower())
        self.assertNotIn("try again in a moment", err.lower())   # no false promise of transience

    def test_coordinator_unreachable_skip_says_so(self):
        W.ADMIT_RETRIES = 0
        L.lease_spawn = lambda tier="batch": None
        L.LAST_REFUSAL = {"kind": "unreachable", "reason": "Connection refused"}
        W._run_turn("a beat", "l1")
        phase, err = self._phase()
        self.assertEqual(phase, "skipped")
        self.assertIn("coordinator", err.lower())

    def test_red_line_refusal_still_maps_to_refused(self):
        L.lease_spawn = lambda tier="batch": "tok1"
        L.step = lambda *a, **k: (_ for _ in ()).throw(SystemExit("prompt refused by red-line gate (B3)"))
        W._run_turn("bad", "l1")
        self.assertEqual(self._phase()[0], "refused")


class InterruptOnWarmFailureTest(unittest.TestCase):
    """A substantive failure under a WARM lease interrupts the orphaned ComfyUI job so it stops burning
    the held GPU (audit 3.1); a genuine preempt does not (the job is already gone)."""
    def setUp(self):
        self._env = os.environ.pop("LUCID_GEN_CMD", None)
        self._save = (L.S.force_evict, L.ST.is_private, L.wait_ready, L.E.run_beat,
                      L._coord_holder_tier, L.E.cc.interrupt)
        L.S.force_evict = lambda m: True
        L.ST.is_private = lambda s: False
        L.wait_ready = lambda: True
        L._coord_holder_tier = lambda: None
        self.interrupts = []
        L.E.cc.interrupt = lambda: (self.interrupts.append(1), True)[1]

    def tearDown(self):
        if self._env is not None:
            os.environ["LUCID_GEN_CMD"] = self._env
        (L.S.force_evict, L.ST.is_private, L.wait_ready, L.E.run_beat,
         L._coord_holder_tier, L.E.cc.interrupt) = self._save

    def test_warm_substantive_failure_interrupts_the_orphaned_job(self):
        L.E.run_beat = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("did not finish in 1800s"))
        with self.assertRaises(L.GenerationError):
            L.generate_video("t", "p", "a.png", external_lease=True, raise_errors=True)
        self.assertEqual(len(self.interrupts), 1)

    def test_preempt_does_not_interrupt(self):
        L._coord_holder_tier = lambda: "interactive"
        L.E.run_beat = lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("Connection refused"))
        self.assertIsNone(L.generate_video("t", "p", "a.png", external_lease=True, raise_errors=True))
        self.assertEqual(self.interrupts, [])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Warm-keep lease invariants (ADR-0015): lucid_web holds ONE coordinator batch lease across a
session's beats instead of Spawn+Release per beat. No GPU/daemon — the lease primitives and the
ComfyUI readiness probe are stubbed so the invariants are deterministic:

  1. ONE Spawn across multiple beats; step() is called with external_lease=True (it neither Spawns
     nor Releases — ComfyUI stays warm).
  2. A stale lease (ComfyUI gone after a preempt SIGKILL) is released and a fresh one spawned.
  3. Admission refused / coordinator down -> the turn is 'skipped' (fail open), no lease held.
  4. burn / delete / a fresh start release the held lease.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_web as W   # noqa: E402
import lucid_linear as L  # noqa: E402


class WarmKeep(unittest.TestCase):
    def setUp(self):
        # reset warm-keep state + the turn record
        W.CURRENT_TOKEN = None
        W.TOKEN_DEADLINE = None
        with W.TURN_LOCK:
            W.TURN.update(phase="idle", label=None, error=None, started=None)
        # stub the lease primitives (count Spawns/Releases) and force ComfyUI "ready/up"
        self.spawns, self.releases, self.steps = [], [], []
        self._orig = (L.lease_spawn, L.lease_release, L.wait_ready, W._http_ok, L.step)

        def fake_spawn(tier="batch"):
            self.spawns.append(tier)
            return f"tok{len(self.spawns)}"

        def fake_step(session, prompt, label, tier="batch", external_lease=False, is_current=None,
                      length=None, **_kw):   # tolerate _run_turn's length= (and future kwargs)
            self.steps.append({"prompt": prompt, "external_lease": external_lease})
            return {"id": len(self.steps)}   # a truthy node -> phase "done"

        L.lease_spawn = fake_spawn
        L.lease_release = lambda t: self.releases.append(t)
        L.wait_ready = lambda: True
        W._http_ok = lambda url, timeout=1.5: True   # ComfyUI answers -> reuse on the next beat
        L.step = fake_step

    def tearDown(self):
        (L.lease_spawn, L.lease_release, L.wait_ready, W._http_ok, L.step) = self._orig

    def test_one_spawn_across_beats_external_lease(self):
        W._run_turn("a beat", "l1")
        W._run_turn("another beat", "l2")
        self.assertEqual(len(self.spawns), 1, "warm-keep must Spawn ComfyUI ONCE across beats")
        self.assertTrue(self.steps and all(s["external_lease"] for s in self.steps),
                        "step() must run external_lease=True (no per-beat Spawn/Release)")
        self.assertEqual(self.releases, [], "no Release between beats")
        self.assertEqual(W.CURRENT_TOKEN, "tok1")
        with W.TURN_LOCK:
            self.assertEqual(W.TURN["phase"], "done")

    def test_stale_lease_respawns_on_preempt(self):
        W._run_turn("a", "l1")                       # holds tok1
        W._http_ok = lambda *a, **k: False           # ComfyUI SIGKILLed by a preempt -> gone
        W._run_turn("b", "l2")                       # detect stale -> release tok1, spawn tok2
        self.assertEqual(len(self.spawns), 2)
        self.assertIn("tok1", self.releases)
        self.assertEqual(W.CURRENT_TOKEN, "tok2")

    def test_fail_open_when_admission_refused(self):
        L.lease_spawn = lambda tier="batch": None    # GPU busy / coordinator down
        W._run_turn("a", "l1")
        with W.TURN_LOCK:
            self.assertEqual(W.TURN["phase"], "skipped")
        self.assertIsNone(W.CURRENT_TOKEN)
        self.assertEqual(self.steps, [], "no generation attempted without a lease")

    def test_release_lease_is_idempotent(self):
        W._run_turn("a", "l1")
        self.assertEqual(W.CURRENT_TOKEN, "tok1")
        W._release_lease()
        self.assertEqual(self.releases, ["tok1"])
        self.assertIsNone(W.CURRENT_TOKEN)
        W._release_lease()                            # second release is a no-op
        self.assertEqual(self.releases, ["tok1"])


class EpochGuard(unittest.TestCase):
    """A start/delete/burn arriving mid-beat must SUPERSEDE the in-flight worker: its terminal TURN
    write is discarded so it can't clobber the fresh idle state with a stale done/error/skipped."""
    def setUp(self):
        W.CURRENT_TOKEN = None
        W.TOKEN_DEADLINE = None
        with W.TURN_LOCK:
            W.TURN.update(phase="idle", label=None, error=None, started=None, epoch=0)
        self._orig = (L.lease_spawn, L.lease_release, L.wait_ready, W._http_ok, L.step)
        L.lease_spawn = lambda tier="batch": "tok1"
        L.lease_release = lambda t: None
        L.wait_ready = lambda: True
        W._http_ok = lambda url, timeout=1.5: True

    def tearDown(self):
        (L.lease_spawn, L.lease_release, L.wait_ready, W._http_ok, L.step) = self._orig

    def test_superseded_worker_does_not_clobber_turn(self):
        # /api/dream captures the epoch and marks the turn dreaming
        with W.TURN_LOCK:
            epoch = W.TURN["epoch"]
            W.TURN.update(phase="dreaming", label="l1", started=0.0)
        seen = {}

        def fake_step(session, prompt, label, tier="batch", external_lease=False, is_current=None,
                      length=None, **_kw):
            seen["before"] = is_current()      # still the live turn at the start of the persist window
            W._supersede_turn()                # a /api/start (or delete/burn) lands mid-beat
            seen["after"] = is_current()       # now superseded
            return {"id": 1}                   # generation produced a (now stale) node

        L.step = fake_step
        W._run_turn("a", "l1", epoch)
        self.assertTrue(seen["before"])
        self.assertFalse(seen["after"])
        with W.TURN_LOCK:                       # the worker's "done" was discarded; supersede's idle stands
            self.assertEqual(W.TURN["phase"], "idle")

    def test_current_worker_still_records_outcome(self):
        with W.TURN_LOCK:
            epoch = W.TURN["epoch"]
            W.TURN.update(phase="dreaming", label="l1", started=0.0)
        L.step = lambda *a, **k: {"id": 1}      # no supersede this time
        W._run_turn("a", "l1", epoch)
        with W.TURN_LOCK:
            self.assertEqual(W.TURN["phase"], "done")   # an uninterrupted turn records normally


if __name__ == "__main__":
    unittest.main()

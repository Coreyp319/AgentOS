#!/usr/bin/env python3
"""ADR-0023 fuse-review cache (lucid_web._fuse_cached): the "how Lucid reads this" readback is assembled by
fuse_direction (a VLM call, no lease/GPU) and held by (session, parent, notes_digest, prompt) so reopening
it — or hitting Dream-it right after — is instant and STABLE. The CACHE keys on the typed prompt too (a
different prompt -> a fresh reading), but the staleness token returned to the client is NOTES-ONLY, so
editing your words can't false-trigger the staleness gate. No model/GPU/daemon: fuse_direction + the chain
store are stubbed, so this exercises only the cache/key logic."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_web as W
import lucid_linear as L


class FuseCacheTest(unittest.TestCase):
    def setUp(self):
        self._orig = {k: getattr(L, k) for k in ("load_chain", "_node_or_tip", "_notes_digest", "fuse_direction")}
        self._engine = L.E.current_engine
        W._FUSE_CACHE.clear()
        # a parent whose notes_digest we control, and a counting fuse_direction so cache hits are observable.
        self.parent = {"id": 1, "notes": []}
        self.digest = "DIGEST-A"
        self.calls = 0
        self.allow_model = []
        self._turn_phase = W.TURN["phase"]

        def _fuse(session, parent_id, prompt, **k):
            self.calls += 1
            self.allow_model.append(k.get("allow_model", True))
            # the reading echoes the notes-only digest the client must send back to /api/dream
            src = "decompose" if k.get("allow_model", True) else "suffix"
            return {"ok": True, "fused": f"READING({prompt})", "subject": "", "source": src,
                    "rows": [], "notes_digest": self.digest, "reason": None}

        L.load_chain = lambda s: {"session": s, "nodes": [self.parent]}
        L._node_or_tip = lambda chain, pid: self.parent
        L._notes_digest = lambda chain, parent: self.digest
        L.fuse_direction = _fuse
        L.E.current_engine = lambda: "wan"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)
        L.E.current_engine = self._engine
        W.TURN["phase"] = self._turn_phase
        W._FUSE_CACHE.clear()

    def test_backpressure_during_a_dream_skips_model_and_cache(self):
        # council P1.6: while a beat generates, the eager fuse must NOT run the VLM (load/evict thrash against
        # the in-flight render). It returns the deterministic suffix reading, UNCACHED so a real reading is
        # computed once the beat finishes.
        W.TURN["phase"] = "dreaming"
        a = W._fuse_cached("s", 1, "a calm aurora")
        b = W._fuse_cached("s", 1, "a calm aurora")
        self.assertEqual(self.calls, 2)                  # no caching during a dream — recomputed each call
        self.assertFalse(self.allow_model[-1])           # ran with allow_model=False (suffix only)
        self.assertEqual(a["source"], "suffix")
        self.assertEqual(a, b)

    def test_cache_hit_on_repeat_same_prompt_and_notes(self):
        a = W._fuse_cached("s", 1, "a calm aurora")
        b = W._fuse_cached("s", 1, "a calm aurora")
        self.assertEqual(self.calls, 1)                      # second call served from cache
        self.assertEqual(a, b)
        self.assertEqual(a["engine"], "wan")                 # engine stamped on
        self.assertEqual(a["notes_digest"], "DIGEST-A")

    def test_prompt_change_refuses_the_cache(self):
        W._fuse_cached("s", 1, "a calm aurora")
        W._fuse_cached("s", 1, "a storm rises")              # different words -> a fresh reading
        self.assertEqual(self.calls, 2)

    def test_notes_change_busts_the_cache(self):
        W._fuse_cached("s", 1, "a calm aurora")
        self.digest = "DIGEST-B"                             # a note was added/edited/removed
        W._fuse_cached("s", 1, "a calm aurora")
        self.assertEqual(self.calls, 2)

    def test_staleness_token_is_notes_only(self):
        # the token the client echoes back is the NOTES digest — independent of the typed prompt, so a prompt
        # edit never false-triggers /api/dream's staleness refusal.
        a = W._fuse_cached("s", 1, "words one")
        b = W._fuse_cached("s", 1, "words two")
        self.assertEqual(a["notes_digest"], b["notes_digest"])


if __name__ == "__main__":
    unittest.main()

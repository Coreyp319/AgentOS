#!/usr/bin/env python3
"""Tests for the Hermes agent-default adapter (ADR-0049 Phase 2).

The load-bearing invariant: the surgical write changes ONLY the model.default line — every comment,
blank line, and the commented-out fallback_model block survive byte-for-byte (no yaml round-trip).
Plus: 0/>1 default lines → refuse; the inverse is per-key + revert restores exactly; propose writes
nothing. Nothing touches the real ~/.hermes/config.yaml (a temp file is used throughout).

Run:  python3 -m unittest discover -s integrations/setup/tests
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import setup            # noqa: E402
import agent_targets    # noqa: E402

# a realistic slice of ~/.hermes/config.yaml: comments, the model: block, other top-level keys, and the
# commented-out fallback_model block that a yaml round-trip would destroy.
CONFIG = """\
# AgentOS / Hermes config
model:
  default: qwen3.6-27b-64k
  provider: custom
  base_url: http://localhost:11434/v1
  api_key: ollama
  context_length: 65536
providers: {}
agent:
  max_turns: 150          # inline comment must survive
x_search:
  model: grok-4.20-reasoning

# ── Fallback Model ────────────────────────────────────────────────────
# fallback_model:
#   provider: openrouter
#   model: anthropic/claude-sonnet-4
"""


class _R:
    def __init__(self, rc=0):
        self.returncode = rc


class HermesAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentos-adapter-")
        self.cfg = Path(self.tmp) / "config.yaml"
        self.cfg.write_text(CONFIG)
        self._xdg = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = self.tmp            # isolate the manifest ledger
        self.a = agent_targets.HermesAdapter(self.cfg)

    def tearDown(self):
        if self._xdg is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self._xdg
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_current_reads_default(self):
        self.assertEqual(self.a.current(), "qwen3.6-27b-64k")

    def test_propose_writes_nothing(self):
        before = self.cfg.read_text()
        p = self.a.propose("gemma4-26b-64k")
        self.assertTrue(p["changes"])
        self.assertIn("gemma4-26b-64k", p["diff"])
        self.assertEqual(self.cfg.read_text(), before)      # DRY RUN — untouched

    def test_set_default_is_byte_exact_except_the_one_line(self):
        before = CONFIG.split("\n")
        res = self.a.set_default("gemma4-26b-64k")
        self.assertTrue(res["ok"])
        self.assertEqual(res["prior"], "qwen3.6-27b-64k")
        after = self.cfg.read_text().split("\n")
        self.assertEqual(len(before), len(after))
        diffs = [(b, a) for b, a in zip(before, after) if b != a]
        self.assertEqual(len(diffs), 1)                     # exactly ONE line changed
        self.assertEqual(diffs[0][1], "  default: gemma4-26b-64k")
        # the fallback_model block + comments survived verbatim
        text = self.cfg.read_text()
        self.assertIn("# fallback_model:", text)
        self.assertIn("#   model: anthropic/claude-sonnet-4", text)
        self.assertIn("max_turns: 150          # inline comment must survive", text)
        self.assertEqual(self.a.current(), "gemma4-26b-64k")

    def test_set_then_revert_restores_exactly(self):
        original = self.cfg.read_text()
        self.a.set_default("gemma4-26b-64k")
        self.assertEqual(len([a for a in setup.manifest_actions() if a.get("kind") == "set-default"]), 1)
        rev = self.a.revert()
        self.assertTrue(rev["ok"])
        self.assertEqual(rev["restored"], "qwen3.6-27b-64k")
        self.assertEqual(self.cfg.read_text(), original)    # byte-for-byte back to the start
        self.assertEqual([a for a in setup.manifest_actions() if a.get("kind") == "set-default"], [])

    def test_idempotent_same_value(self):
        res = self.a.set_default("qwen3.6-27b-64k")
        self.assertTrue(res["ok"])
        self.assertEqual(res.get("skipped"), "already-default")

    def test_refuse_when_no_default(self):
        self.cfg.write_text("agent:\n  max_turns: 5\nproviders: {}\n")
        res = self.a.set_default("x:1b")
        self.assertFalse(res["ok"])
        self.assertIn("found 0", res["reason"])

    def test_refuse_when_multiple_defaults(self):
        self.cfg.write_text("model:\n  default: a\n  default: b\n")   # ambiguous → must refuse
        res = self.a.set_default("x:1b")
        self.assertFalse(res["ok"])
        self.assertIn("found 2", res["reason"])

    def test_quoted_value_replaced_cleanly(self):
        self.cfg.write_text('model:\n  default: "old:tag"\n  provider: custom\n')
        self.assertEqual(self.a.current(), "old:tag")
        self.a.set_default("new:tag")
        self.assertIn("  default: new:tag", self.cfg.read_text())

    def test_revert_with_nothing_to_revert(self):
        self.assertFalse(self.a.revert()["ok"])

    def test_rejects_injection_ref(self):                    # security: no newline/YAML injection into config
        before = self.cfg.read_text()
        res = self.a.set_default("ok:tag\n  evil_key: pwned")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "bad-ref")
        self.assertEqual(self.cfg.read_text(), before)       # nothing written
        self.assertEqual(self.a.set_default("qwen\n")["reason"], "bad-ref")   # fullmatch rejects trailing \n too

    def test_quoted_default_reverts_byte_exact(self):        # reversibility: prior literal restored verbatim
        self.cfg.write_text('model:\n  default: "old:tag"\n  provider: custom\n')
        original = self.cfg.read_text()
        self.assertTrue(self.a.set_default("new:tag")["ok"])
        self.assertIn("  default: new:tag", self.cfg.read_text())
        self.assertTrue(self.a.revert()["ok"])
        self.assertEqual(self.cfg.read_text(), original)     # the quotes came back exactly

    def test_inverse_recorded_before_write_blocks_on_ledger_failure(self):
        before = self.cfg.read_text()
        orig = setup.record_action
        setup.record_action = lambda *a, **k: False          # simulate a manifest write failure
        try:
            res = self.a.set_default("gemma4-26b-64k")
        finally:
            setup.record_action = orig
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "ledger-write-failed")
        self.assertEqual(self.cfg.read_text(), before)       # config NOT changed without a durable inverse

    def test_estimate_fit_honest_and_unmeasured(self):
        setup._OLLAMA_LIST_CACHE = ["gemma4-26b-64k:latest"]
        try:
            f = self.a.estimate_fit("gemma4-26b-64k", hw={"vram_gb": 24})
        finally:
            setup._OLLAMA_LIST_CACHE = None
        self.assertFalse(f["measured"])
        self.assertIn(f["verdict"], ("fits", "tight", "too-big", "unknown"))

    def test_registry_and_get_adapter(self):
        self.assertIn("hermes", agent_targets.ADAPTERS)
        self.assertIsInstance(agent_targets.get_adapter("hermes", self.cfg), agent_targets.HermesAdapter)
        self.assertIsNone(agent_targets.get_adapter("openclaw"))   # no framework for a non-existent agent


class CanaryTests(unittest.TestCase):
    """The measured canary (ADR-0049 Phase 2b) — never loads real Ollama; generate/ps are injected."""
    def setUp(self):
        self._cache = setup._OLLAMA_LIST_CACHE
        setup._OLLAMA_LIST_CACHE = ["m:tag"]

    def tearDown(self):
        setup._OLLAMA_LIST_CACHE = self._cache

    def test_pass_on_gpu_and_fast(self):
        gen = lambda ref: {"eval_count": 160, "eval_duration": 2_000_000_000}     # 80 tok/s
        ps = lambda: [{"name": "m:tag", "size": 1000, "size_vram": 1000}]         # 100% on GPU
        c = agent_targets.measured_canary("m:tag", generate=gen, ps=ps, run=lambda *a, **k: _R(0), stop_after=False)
        self.assertTrue(c["pass"])
        self.assertEqual(c["reason"], "ok")

    def test_cpu_offload_fails(self):
        gen = lambda ref: {"eval_count": 160, "eval_duration": 2_000_000_000}
        ps = lambda: [{"name": "m:tag", "size": 1000, "size_vram": 300}]          # 30% on GPU → thrash
        c = agent_targets.measured_canary("m:tag", generate=gen, ps=ps, run=lambda *a, **k: _R(0))
        self.assertFalse(c["pass"])
        self.assertEqual(c["reason"], "cpu-offload")

    def test_too_slow_fails(self):
        gen = lambda ref: {"eval_count": 2, "eval_duration": 2_000_000_000}       # 1 tok/s
        ps = lambda: [{"name": "m:tag", "size": 1000, "size_vram": 1000}]
        c = agent_targets.measured_canary("m:tag", generate=gen, ps=ps, run=lambda *a, **k: _R(0))
        self.assertFalse(c["pass"])
        self.assertEqual(c["reason"], "too-slow")

    def test_not_present(self):
        setup._OLLAMA_LIST_CACHE = []
        c = agent_targets.measured_canary("absent:1b")
        self.assertFalse(c["pass"])
        self.assertEqual(c["reason"], "not-present")

    def test_load_failed_is_caught(self):
        def boom(ref):
            raise RuntimeError("connection refused")
        c = agent_targets.measured_canary("m:tag", generate=boom, ps=lambda: [], run=lambda *a, **k: _R(0))
        self.assertFalse(c["pass"])
        self.assertIn("load-failed", c["reason"])

    def test_admission_refuses_before_loading_when_vram_insufficient(self):
        # coordinator down → fall back to current-free-VRAM admission; refuse WITHOUT loading.
        orig = agent_targets._ollama_size_gb
        agent_targets._ollama_size_gb = lambda ref: 20.0
        loaded = []
        try:
            c = agent_targets.measured_canary("m:tag", generate=lambda r: loaded.append(1) or {},
                                              ps=lambda: [], hw={"vram_free_mib": 4096}, run=lambda *a, **k: _R(0),
                                              acquire=lambda *a, **k: (None, "unreachable"))
        finally:
            agent_targets._ollama_size_gb = orig
        self.assertFalse(c["pass"])
        self.assertEqual(c["reason"], "insufficient-free-vram")
        self.assertEqual(loaded, [])                         # generate() was never called — no blind load

    def test_coordinator_denied_refuses_without_loading(self):
        loaded = []
        c = agent_targets.measured_canary("m:tag", generate=lambda r: loaded.append(1) or {},
                                          ps=lambda: [], acquire=lambda *a, **k: (None, "no free VRAM"),
                                          run=lambda *a, **k: _R(0))
        self.assertFalse(c["pass"])
        self.assertEqual(c["reason"], "coordinator-denied")
        self.assertEqual(loaded, [])                         # the coordinator gate ran BEFORE any load

    def test_coordinator_grant_releases_lease(self):
        gen = lambda ref: {"eval_count": 160, "eval_duration": 2_000_000_000}
        ps = lambda: [{"name": "m:tag", "size": 1000, "size_vram": 1000}]
        released = []
        c = agent_targets.measured_canary("m:tag", generate=gen, ps=ps, run=lambda *a, **k: _R(0),
                                          acquire=lambda *a, **k: (777, None),
                                          release=lambda tok, **k: released.append(tok))
        # ps reports the model resident here, so admission is skipped and no lease is taken (token stays None).
        self.assertTrue(c["pass"])
        self.assertEqual(released, [None])                   # release is always called (here with no token)

    def test_coordinator_grant_when_not_resident_releases_token(self):
        gen = lambda ref: {"eval_count": 160, "eval_duration": 2_000_000_000}
        # ps empty on the admission check (not resident → acquire), then resident for the measure
        calls = {"n": 0}
        def ps():
            calls["n"] += 1
            return [] if calls["n"] == 1 else [{"name": "m:tag", "size": 1000, "size_vram": 1000}]
        released = []
        c = agent_targets.measured_canary("m:tag", generate=gen, ps=ps, run=lambda *a, **k: _R(0),
                                          acquire=lambda *a, **k: (777, None),
                                          release=lambda tok, **k: released.append(tok))
        self.assertTrue(c["pass"])
        self.assertEqual(released, [777])                    # the granted lease token is released after


if __name__ == "__main__":
    unittest.main(verbosity=2)

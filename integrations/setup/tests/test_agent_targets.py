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


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Per-call tier classification (ADR-0041) — the fix for the hardcoded `interactive`.

The load-bearing safety: a LIVE turn must NEVER be classified `batch` (that would make the user's turn
yield to the dream). So only the unambiguous-background platforms map to batch; everything else, incl.
an unknown/empty platform and a live turn that happens to carry a UUID task_id, stays interactive.

Run from the plugin dir:  python3 -m unittest tests.test_tier_classify -v
"""
import importlib.util
import os
import sys
import unittest

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PLUGIN_DIR)  # so __init__'s `import coordinator`/`import lease_client` fallback resolves
_spec = importlib.util.spec_from_file_location("gpu_coordinator_pkg", os.path.join(_PLUGIN_DIR, "__init__.py"))
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)

# Every live human-facing platform Hermes uses — none of these may ever be classified batch.
_LIVE_PLATFORMS = ["cli", "telegram", "discord", "tui", "slack", "imessage", "feishu",
                   "api_server", "gateway", "acp", "darwin", "linux", "", None]


class TierClassify(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("AGENTOS_GPU_BATCH_PLATFORMS", None)

    def test_live_platforms_are_always_interactive(self):
        for p in _LIVE_PLATFORMS:
            self.assertEqual(plugin._classify_tier(platform=p), "interactive", f"platform={p!r}")

    def test_background_platforms_are_batch(self):
        self.assertEqual(plugin._classify_tier(platform="cron"), "batch")
        self.assertEqual(plugin._classify_tier(platform="subagent"), "batch")
        self.assertEqual(plugin._classify_tier(platform="CRON"), "batch")  # case-insensitive
        self.assertEqual(plugin._classify_tier(platform=" subagent "), "batch")  # trimmed

    def test_task_id_never_forces_batch(self):
        # Hermes fills a UUID task_id for live turns, so task_id presence must NOT classify — a live
        # platform with a (UUID) task_id stays interactive. This is the regression the fix avoids.
        self.assertEqual(
            plugin._classify_tier(platform="telegram", task_id="3f2a-… a uuid"), "interactive")

    def test_no_platform_defaults_interactive(self):
        self.assertEqual(plugin._classify_tier(), "interactive")  # missing kwarg → safe default

    def test_env_overrides_the_whole_set(self):
        os.environ["AGENTOS_GPU_BATCH_PLATFORMS"] = "discord, slack"
        self.assertEqual(plugin._classify_tier(platform="discord"), "batch")
        self.assertEqual(plugin._classify_tier(platform="slack"), "batch")
        self.assertEqual(plugin._classify_tier(platform="cron"), "interactive")  # override REPLACES the default
        self.assertEqual(plugin._classify_tier(platform="telegram"), "interactive")

    def test_empty_env_is_interactive_always(self):
        os.environ["AGENTOS_GPU_BATCH_PLATFORMS"] = ""
        for p in ["cron", "subagent", "telegram"]:
            self.assertEqual(plugin._classify_tier(platform=p), "interactive", p)

    def test_get_coordinator_caches_one_per_tier(self):
        a = plugin._get_coordinator("interactive")
        b = plugin._get_coordinator("interactive")
        c = plugin._get_coordinator("batch")
        self.assertIs(a, b, "same tier returns the cached coordinator")
        self.assertIsNot(a, c, "distinct tiers get distinct coordinators")
        self.assertEqual(a._tier, "interactive")
        self.assertEqual(c._tier, "batch")


if __name__ == "__main__":
    unittest.main()

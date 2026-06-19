#!/usr/bin/env python3
"""Unit tests for the AgentOS status panel's kind-aware status logic.

Pure-function tests: `run` (the systemctl shell-out) and `reach` (the HTTP probe) are
injected, so nothing here touches the real system. Run with:

    python3 -m unittest discover -s integrations/status-panel/tests
    # or:  python3 integrations/status-panel/tests/test_status_panel.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import status_panel as sp  # noqa: E402


def show_output(**props) -> str:
    """Render a `systemctl show -p …` style output from kwargs."""
    return "".join(f"{k}={v}\n" for k, v in props.items())


def fake_run(show=None, listing=None):
    """Build a `run(argv)->stdout` double. `show` is the show-output string; `listing` is a
    single `list-units --plain --no-legend` line (UNIT LOAD ACTIVE SUB DESC)."""
    def _run(argv):
        if "show" in argv:
            return show or ""
        if "list-units" in argv:
            return listing or ""
        return ""
    return _run


class UnitStatus(unittest.TestCase):
    def status(self, svc, **fake):
        return sp._unit_status(svc, run=fake_run(**fake))

    def test_daemon_running_is_up(self):
        r = self.status({"unit": "x.service", "kind": "daemon"},
                        show=show_output(LoadState="loaded", ActiveState="active",
                                         SubState="running", Result="success"))
        self.assertEqual(r["status"], "up")
        self.assertEqual(r["state"], "running")

    def test_daemon_inactive_is_down(self):
        r = self.status({"unit": "x.service", "kind": "daemon"},
                        show=show_output(LoadState="loaded", ActiveState="inactive",
                                         SubState="dead", Result="success"))
        self.assertEqual(r["status"], "down")

    def test_task_clean_exit_is_ok_not_down(self):
        # A fire-and-forget launcher that exited cleanly did its job — "ran ✓", never "down".
        r = self.status({"unit": "launcher.service", "kind": "task"},
                        show=show_output(LoadState="loaded", ActiveState="inactive",
                                         SubState="dead", Result="success"))
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["state"], "ran ✓")

    def test_on_demand_inactive_is_idle_not_down(self):
        # A coordinator-spawned backend (ComfyUI) is dormant until asked — "on-demand", never "down".
        r = self.status({"unit": "comfyui.service", "kind": "on_demand"},
                        show=show_output(LoadState="loaded", ActiveState="inactive",
                                         SubState="dead", Result="success"))
        self.assertEqual(r["status"], "idle")
        self.assertEqual(r["state"], "on-demand")

    def test_on_demand_active_is_up(self):
        # When something IS dreaming, the on-demand backend reads as a normal running service.
        r = self.status({"unit": "comfyui.service", "kind": "on_demand"},
                        show=show_output(LoadState="loaded", ActiveState="active",
                                         SubState="running", Result="success"))
        self.assertEqual(r["status"], "up")

    def test_idle_on_demand_is_calm(self):
        # Dormant on-demand must not raise attention (unlike a down daemon).
        self.assertFalse(sp._is_attention({"status": "idle", "kind": "on_demand", "reach": ""}))

    def test_watch_active_is_ready(self):
        r = self.status({"unit": "w.path", "kind": "watch"},
                        show=show_output(LoadState="loaded", ActiveState="active",
                                         SubState="waiting", Result="success"))
        self.assertEqual(r["status"], "up")
        self.assertEqual(r["state"], "ready")

    def test_failed_state_with_label(self):
        r = self.status({"unit": "x.service", "kind": "daemon"},
                        show=show_output(LoadState="loaded", ActiveState="failed",
                                         SubState="failed", Result="exit-code"))
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["state"], "failed (exit-code)")

    def test_task_nonzero_exit_is_failed(self):
        # Result != success outranks the kind=task "ran ✓" shortcut.
        r = self.status({"unit": "launcher.service", "kind": "task"},
                        show=show_output(LoadState="loaded", ActiveState="inactive",
                                         SubState="dead", Result="exit-code"))
        self.assertEqual(r["status"], "failed")

    def test_activating_is_starting(self):
        r = self.status({"unit": "x.service", "kind": "daemon"},
                        show=show_output(LoadState="loaded", ActiveState="activating",
                                         SubState="start", Result="success"))
        self.assertEqual(r["status"], "starting")

    def test_absent_when_not_found(self):
        r = self.status({"unit": "missing.service", "kind": "daemon"},
                        show=show_output(LoadState="not-found", ActiveState="inactive"))
        self.assertEqual(r["status"], "absent")
        self.assertEqual(r["state"], "not installed")

    def test_escaped_match_falls_back_to_list_units(self):
        # The xdg-autostart wallpaper unit: `show` can't resolve its escaped \x2d name, so the
        # status must come from the list-units listing instead. Task that exited → "ran ✓".
        r = self.status(
            {"match": "app-*hexen*wallpaper*@autostart.service", "kind": "task"},
            show=show_output(LoadState="not-found"),
            listing="app-nimbus\\x2dhexen\\x2dwallpaper@autostart.service loaded inactive dead Wallpaper",
        )
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["unit"], "app-nimbus\\x2dhexen\\x2dwallpaper@autostart.service")

    def test_match_no_unit_is_absent(self):
        r = self.status({"match": "nope-*.service", "kind": "daemon"}, listing="")
        self.assertEqual(r["status"], "absent")


class Attention(unittest.TestCase):
    def test_failed_needs_attention(self):
        self.assertTrue(sp._is_attention({"status": "failed", "kind": "daemon"}))

    def test_down_daemon_needs_attention(self):
        self.assertTrue(sp._is_attention({"status": "down", "kind": "daemon", "reach": ""}))

    def test_down_watch_is_not_attention(self):
        self.assertFalse(sp._is_attention({"status": "down", "kind": "watch", "reach": ""}))

    def test_up_but_unreachable_needs_attention(self):
        self.assertTrue(sp._is_attention({"status": "up", "kind": "daemon", "reach": "unreachable"}))

    def test_ok_and_ready_are_calm(self):
        self.assertFalse(sp._is_attention({"status": "ok", "kind": "task", "reach": ""}))
        self.assertFalse(sp._is_attention({"status": "up", "kind": "watch", "reach": ""}))


class BuildStatus(unittest.TestCase):
    CATALOG = {
        "groups": ["AI core", "Desktop QoL"],
        "services": [
            {"id": "d", "name": "Daemon", "group": "AI core", "kind": "daemon",
             "unit": "d.service", "health": "http://x"},
            {"id": "t", "name": "Task", "group": "Desktop QoL", "kind": "task", "unit": "t.service"},
            {"id": "f", "name": "Failer", "group": "AI core", "kind": "daemon", "unit": "f.service"},
        ],
    }

    def _run(self, argv):
        unit = argv[argv.index("show") + 1] if "show" in argv else ""
        table = {
            "d.service": show_output(LoadState="loaded", ActiveState="active", SubState="running", Result="success"),
            "t.service": show_output(LoadState="loaded", ActiveState="inactive", SubState="dead", Result="success"),
            "f.service": show_output(LoadState="loaded", ActiveState="failed", SubState="failed", Result="exit-code"),
        }
        return table.get(unit, "")

    def test_summary_counts_and_contract(self):
        data = sp.build_status(catalog=self.CATALOG, run=self._run, reach=lambda u: "reachable")
        # honest summary: daemon up + task ok = 2 healthy; the failed daemon = 1 attention.
        self.assertEqual(data["summary"], {"total": 3, "healthy": 2, "attention": 1})
        self.assertIn("generated_at", data)
        self.assertEqual(data["groups"], ["AI core", "Desktop QoL"])
        # Data contract: every key the panel.html consumer reads must be present on each row.
        REQUIRED = {"id", "name", "group", "desc", "url", "scope", "kind", "reach", "status", "state"}
        for row in data["services"]:
            self.assertTrue(REQUIRED.issubset(row), f"missing keys: {REQUIRED - set(row)}")
        # reachability probed for the daemon with a health URL; the up daemon is reachable.
        self.assertEqual(next(s for s in data["services"] if s["id"] == "d")["reach"], "reachable")

    def test_bad_row_becomes_one_error_row_not_a_blackout(self):
        catalog = {"groups": [], "services": [{"id": "ok", "name": "Good", "group": "g", "unit": "d.service"},
                                              {"id": "bad", "name": "Broken", "group": "g", "unit": "bad.service"}]}
        def boom(argv):
            unit = argv[argv.index("show") + 1] if "show" in argv else ""
            if unit == "d.service":
                return show_output(LoadState="loaded", ActiveState="active", SubState="running", Result="success")
            raise RuntimeError("simulated probe failure")
        # One unit probes fine, one raises. build_status must still return BOTH rows (the bad
        # one degraded to an error row), never a blank panel or a 500.
        data = sp.build_status(catalog=catalog, run=boom, reach=lambda u: "")
        self.assertEqual(len(data["services"]), 2)
        bad = next(s for s in data["services"] if s["id"] == "bad")
        self.assertEqual(bad["state"], "catalog error")
        self.assertNotIn("error", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)

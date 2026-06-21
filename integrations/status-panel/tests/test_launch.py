#!/usr/bin/env python3
"""Tests for the launch surface (ADR-0031), folded into status_panel.py from spikes/atrium/.

The security-critical invariants:
  • a shell one-liner ("Copy fix") is emitted ONLY to a provably-local request — never to a
    tailnet/phone client (fail-closed, gap #4);
  • a remote origin never gets a DEAD door — an un-served service degrades to desktop-only and a
    loopback url is rewritten to the tailnet host (gaps #2/#3);
  • the KRunner `.desktop` launchers are generated deterministically and only for real doors.

Pure-function tests + a couple of live-Handler checks for the routes that live in do_GET. No
real systemd: the one path that would shell out (build_launch via cached_status) is exercised
with a hand-built status dict. Run:
    python3 -m unittest discover -s integrations/status-panel/tests
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import status_panel as sp  # noqa: E402
import gen_launchers as gl  # noqa: E402


class ClassifyOrigin(unittest.TestCase):
    def test_direct_loopback_is_local_and_can_copy_fix(self):
        o = sp.classify_origin("127.0.0.1", {})
        self.assertFalse(o["remote"])
        self.assertTrue(o["can_copy_fix"])

    def test_ipv6_loopback_is_local(self):
        self.assertTrue(sp.classify_origin("::1", {})["can_copy_fix"])

    def test_tailscale_serve_forwarded_is_remote_no_shell(self):
        o = sp.classify_origin("127.0.0.1", {"X-Forwarded-For": "100.64.0.9",
                                             "X-Forwarded-Host": "4090.tailnet.ts.net:9123",
                                             "X-Forwarded-Proto": "https"})
        self.assertTrue(o["remote"])
        self.assertFalse(o["can_copy_fix"])
        self.assertEqual(o["host"], "4090.tailnet.ts.net:9123")

    def test_tailscale_identity_header_alone_is_remote(self):
        o = sp.classify_origin("127.0.0.1", {"Tailscale-User-Login": "corey@example.com"})
        self.assertTrue(o["remote"])
        self.assertFalse(o["can_copy_fix"])

    def test_nonloopback_peer_is_remote_even_without_headers(self):
        o = sp.classify_origin("192.168.1.50", {})
        self.assertTrue(o["remote"])
        self.assertFalse(o["can_copy_fix"])

    def test_spoofed_xff_from_local_only_restricts_itself(self):
        o = sp.classify_origin("127.0.0.1", {"X-Forwarded-For": "1.2.3.4"})
        self.assertFalse(o["can_copy_fix"])

    def test_nonloopback_host_header_blocks_copy_fix(self):
        o = sp.classify_origin("127.0.0.1", {"Host": "4090.tailnet.ts.net"})
        self.assertTrue(o["remote"])
        self.assertFalse(o["can_copy_fix"])

    def test_loopback_host_allows_copy_fix(self):
        o = sp.classify_origin("127.0.0.1", {"Host": "127.0.0.1:9123"})
        self.assertFalse(o["remote"])
        self.assertTrue(o["can_copy_fix"])

    def test_empty_forwarded_for_is_still_remote(self):
        o = sp.classify_origin("127.0.0.1", {"X-Forwarded-For": ""})
        self.assertTrue(o["remote"])
        self.assertFalse(o["can_copy_fix"])


class DoorFor(unittest.TestCase):
    LOCAL = {"remote": False, "host": None, "can_copy_fix": True}
    REMOTE = {"remote": True, "host": "4090.tailnet.ts.net:9123", "can_copy_fix": False}
    REMOTE_NOHOST = {"remote": True, "host": None, "can_copy_fix": False}

    def test_local_url_is_open_loopback(self):
        self.assertEqual(sp.door_for({"url": "http://127.0.0.1:8765"}, self.LOCAL),
                         {"state": "open", "href": "http://127.0.0.1:8765"})

    def test_no_url_is_monitor_only(self):
        self.assertEqual(sp.door_for({"name": "Ollama"}, self.LOCAL)["state"], "monitor-only")
        self.assertEqual(sp.door_for({"name": "Ollama"}, self.REMOTE)["state"], "monitor-only")

    def test_remote_served_rewrites_to_tailnet_host_and_port(self):
        d = sp.door_for({"url": "http://127.0.0.1:8765", "tailnet": True}, self.REMOTE)
        self.assertEqual(d, {"state": "open", "href": "https://4090.tailnet.ts.net:8765/"})

    def test_remote_unserved_is_desktop_only_never_dead(self):
        d = sp.door_for({"url": "http://127.0.0.1:8188", "tailnet": False}, self.REMOTE)
        self.assertEqual(d, {"state": "desktop-only", "href": ""})

    def test_remote_without_forwarded_host_degrades_not_dead(self):
        d = sp.door_for({"url": "http://127.0.0.1:8765", "tailnet": True}, self.REMOTE_NOHOST)
        self.assertEqual(d, {"state": "desktop-only", "href": ""})

    def test_malformed_url_degrades_to_desktop_only_not_crash(self):
        d = sp.door_for({"url": "http://127.0.0.1:notaport", "tailnet": True}, self.REMOTE)
        self.assertEqual(d["state"], "desktop-only")

    def test_malformed_forwarded_host_is_not_a_dead_door(self):
        remote = {"remote": True, "host": "evil.com/phish", "can_copy_fix": False}
        d = sp.door_for({"url": "http://127.0.0.1:8765", "tailnet": True}, remote)
        self.assertEqual(d, {"state": "desktop-only", "href": ""})


class TailnetHostBase(unittest.TestCase):
    def test_strips_port(self):
        self.assertEqual(sp._tailnet_host_base("4090.tailnet.ts.net:9123"), "4090.tailnet.ts.net")

    def test_no_port(self):
        self.assertEqual(sp._tailnet_host_base("4090.tailnet.ts.net"), "4090.tailnet.ts.net")

    def test_none(self):
        self.assertIsNone(sp._tailnet_host_base(None))

    def test_rejects_host_with_path_or_junk(self):
        self.assertIsNone(sp._tailnet_host_base("evil.com/phish"))
        self.assertIsNone(sp._tailnet_host_base("a b c"))
        self.assertIsNone(sp._tailnet_host_base("http://evil"))

    def test_rejects_out_of_range_port(self):
        self.assertIsNone(sp._tailnet_host_base("4090.ts.net:0"))
        self.assertIsNone(sp._tailnet_host_base("4090.ts.net:99999"))

    def test_rejects_bad_dns_labels(self):
        self.assertIsNone(sp._tailnet_host_base("-leading.dash.net"))
        self.assertIsNone(sp._tailnet_host_base(".."))


class FixCommand(unittest.TestCase):
    def test_user_scope(self):
        self.assertEqual(sp.fix_command({"unit": "x.service", "scope": "user"}),
                         "systemctl --user reset-failed x.service && systemctl --user restart x.service")

    def test_system_scope_uses_sudo(self):
        self.assertTrue(sp.fix_command({"unit": "x.service", "scope": "system"}).startswith("sudo systemctl"))

    def test_no_unit_empty(self):
        self.assertEqual(sp.fix_command({"scope": "user"}), "")


class BuildLaunch(unittest.TestCase):
    STATUS = {
        "groups": ["AI core"],
        "services": [
            {"id": "lucid", "name": "Lucid", "group": "AI core", "url": "http://127.0.0.1:8765",
             "tailnet": True, "status": "up", "state": "running", "kind": "daemon",
             "scope": "user", "unit": "agentos-lucid.service"},
            {"id": "swaync", "name": "Notifications", "group": "AI core", "status": "failed",
             "state": "failed", "kind": "daemon", "scope": "user", "unit": "swaync.service"},
        ],
        "summary": {"total": 2, "healthy": 1, "attention": 1},
        "generated_at": 123.0,
    }

    def test_local_origin_emits_fix_for_attention_row(self):
        p = sp.build_launch(self.STATUS, sp.classify_origin("127.0.0.1", {}))
        sw = next(s for s in p["services"] if s["id"] == "swaync")
        self.assertIn("fix", sw)
        self.assertIn("systemctl --user reset-failed swaync.service", sw["fix"])
        self.assertTrue(p["origin"]["can_copy_fix"])

    def test_remote_origin_never_emits_a_shell_command(self):
        remote = sp.classify_origin("127.0.0.1", {"X-Forwarded-Host": "4090.tailnet.ts.net:9123",
                                                  "X-Forwarded-For": "100.64.0.9"})
        p = sp.build_launch(self.STATUS, remote)
        for s in p["services"]:
            self.assertNotIn("fix", s, f"{s['id']} leaked a shell command to a remote client")
        self.assertFalse(p["origin"]["can_copy_fix"])
        lucid = next(s for s in p["services"] if s["id"] == "lucid")
        self.assertEqual(lucid["door"]["href"], "https://4090.tailnet.ts.net:8765/")

    def test_healthy_row_never_gets_a_fix(self):
        p = sp.build_launch(self.STATUS, sp.classify_origin("127.0.0.1", {}))
        lucid = next(s for s in p["services"] if s["id"] == "lucid")
        self.assertNotIn("fix", lucid)


class CachedStatus(unittest.TestCase):
    """The 1.5s TTL snapshot must build once and reuse, so a wedged unit can't fan out into N
    stuck request threads (ADR-0031 resource-safety finding)."""
    def setUp(self):
        self._orig = sp.build_status
        self._orig_cache = dict(sp._status_cache)
        self.calls = 0

        def counting():
            self.calls += 1
            return {"services": [], "groups": [], "summary": {}, "generated_at": 1.0}
        sp.build_status = counting
        sp._status_cache.update({"t": 0.0, "v": None})

    def tearDown(self):
        sp.build_status = self._orig
        sp._status_cache.update(self._orig_cache)

    def test_concurrent_polls_share_one_build(self):
        sp.cached_status()
        sp.cached_status()
        sp.cached_status()
        self.assertEqual(self.calls, 1, "the second/third poll should reuse the cached snapshot")


class GenLaunchers(unittest.TestCase):
    CATALOG = {"services": [
        {"id": "lucid", "name": "Lucid", "desc": "dream loop", "url": "http://127.0.0.1:8765"},
        {"id": "ollama", "name": "Ollama", "desc": "model server"},        # no url ⇒ no launcher
        {"id": "comfyui", "name": "ComfyUI", "desc": "gen", "url": "http://127.0.0.1:8188", "tailnet": False},
    ]}

    def test_only_url_bearing_services_get_a_launcher(self):
        e = gl.desktop_entries(self.CATALOG)
        self.assertIn("agentos-launch-lucid.desktop", e)
        self.assertIn("agentos-launch-comfyui.desktop", e)   # tailnet:false still a local door
        self.assertNotIn("agentos-launch-ollama.desktop", e)  # monitor-only: nothing to open

    def test_entry_is_a_valid_desktop_file(self):
        body = gl.desktop_entries(self.CATALOG)["agentos-launch-lucid.desktop"]
        self.assertIn("[Desktop Entry]", body)
        self.assertIn("Type=Application", body)
        self.assertIn("Exec=xdg-open http://127.0.0.1:8765", body)
        self.assertIn("Name=Lucid", body)
        self.assertIn("Keywords=agentos;atrium;lucid;", body)
        self.assertNotIn("\n\n", body)  # no blank lines in a desktop entry

    def test_percent_in_url_is_escaped(self):
        e = gl.desktop_entries({"services": [
            {"id": "x", "name": "X", "url": "http://127.0.0.1:9/a%2Fb"}]})
        self.assertIn("Exec=xdg-open http://127.0.0.1:9/a%%2Fb", e["agentos-launch-x.desktop"])

    def test_rejects_non_http_url_and_bad_id(self):
        e = gl.desktop_entries({"services": [
            {"id": "evil", "name": "E", "url": "file:///etc/passwd"},
            {"id": "bad id", "name": "B", "url": "http://127.0.0.1:9"},
            {"id": "ok", "name": "OK", "url": "http://127.0.0.1:9 ; rm -rf"}]})
        self.assertEqual(e, {})  # all three rejected (non-http / bad id / space in url)

    def test_install_remove_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            apps = Path(d)
            written = gl.install(apps_dir=apps, icon="applications-internet")
            self.assertTrue(any(f.startswith("agentos-launch-") for f in written))
            self.assertTrue(list(apps.glob("agentos-launch-*.desktop")))
            removed = gl.remove(apps_dir=apps)
            self.assertTrue(removed)
            self.assertEqual(list(apps.glob("agentos-launch-*.desktop")), [])

    def test_install_prunes_our_stale_launchers(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            apps = Path(d)
            # A launcher WE wrote previously (carries the marker) for a now-removed service.
            stale = apps / "agentos-launch-removed-service.desktop"
            stale.write_text(f"[Desktop Entry]\nType=Application\n{gl.MARKER}\n")
            gl.install(apps_dir=apps, icon="applications-internet")
            self.assertFalse(stale.exists(), "our own stale launcher should be pruned")

    def test_install_preserves_foreign_launcher(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            apps = Path(d)
            # A same-namespace file we did NOT write (no marker) — apply must never delete it.
            foreign = apps / "agentos-launch-removed-service.desktop"
            foreign.write_text("[Desktop Entry]\nType=Application\n")  # no marker
            gl.install(apps_dir=apps, icon="applications-internet")
            self.assertTrue(foreign.exists(), "a user-authored launcher must be preserved")
            # remove() must likewise only touch ours.
            gl.remove(apps_dir=apps)
            self.assertTrue(foreign.exists(), "remove must not delete a foreign launcher either")


class LaunchRoutes(unittest.TestCase):
    """Spin up the real Handler to lock the routes/guards that live in do_GET: the launch view,
    the PWA shell, and the icon path-traversal guard."""
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), sp.Handler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get(self, path):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path)
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, r.getheader("Content-Type", ""), body

    def test_atrium_serves_launch_view(self):
        st, ctype, body = self._get("/atrium")
        self.assertEqual(st, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"The Atrium", body)

    def test_view_launch_query_serves_launch_view(self):
        st, _, body = self._get("/?view=launch")
        self.assertEqual(st, 200)
        self.assertIn(b"The Atrium", body)

    def test_root_still_serves_diagnose_panel(self):
        st, _, body = self._get("/")
        self.assertEqual(st, 200)
        self.assertIn(b"system status", body)   # panel.html title, not the launch view

    def test_manifest_and_sw_served(self):
        st, ctype, body = self._get("/manifest.webmanifest")
        self.assertEqual(st, 200)
        self.assertIn(b"Atrium", body)
        st2, ctype2, _ = self._get("/sw.js")
        self.assertEqual(st2, 200)
        self.assertIn("javascript", ctype2)

    def test_icon_served(self):
        st, _, body = self._get("/icons/icon-192.png")
        self.assertEqual(st, 200)
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_icon_traversal_is_blocked(self):
        st, _, _ = self._get("/icons/..%2f..%2fstatus_panel.py")
        self.assertEqual(st, 404)
        st2, _, body2 = self._get("/icons/../status_panel.py")
        self.assertEqual(st2, 404)
        self.assertNotIn(b"classify_origin", body2)

    def test_icon_dotdot_is_404_not_a_dropped_connection(self):
        # `/icons/..` flattens to the icons dir itself — must 404 cleanly, not raise
        # IsADirectoryError and drop the connection (security review finding).
        st, _, _ = self._get("/icons/..")
        self.assertEqual(st, 404)

    def test_launch_json_local_is_origin_local(self):
        st, ctype, body = self._get("/launch.json")
        self.assertEqual(st, 200)
        self.assertIn("application/json", ctype)
        data = json.loads(body)
        self.assertIn("origin", data)
        self.assertFalse(data["origin"]["remote"])  # loopback test client


if __name__ == "__main__":
    unittest.main(verbosity=2)

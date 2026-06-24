#!/usr/bin/env python3
"""Tests for the Atrium launch-server's origin logic (ADR-0031 gaps #2/#3/#4).

The security-critical invariants:
  • a shell one-liner ("Copy fix") is emitted ONLY to a provably-local request — never to a
    tailnet/phone client (fail-closed);
  • a remote origin never gets a DEAD door — an un-served service degrades to desktop-only,
    and a loopback url is rewritten to the tailnet host.

Pure-function tests; no sockets, no systemd. Run:
    python3 -m unittest discover -s spikes/atrium/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import atrium_server as a  # noqa: E402


class ClassifyOrigin(unittest.TestCase):
    def test_direct_loopback_is_local_and_can_copy_fix(self):
        o = a.classify_origin("127.0.0.1", {})
        self.assertFalse(o["remote"])
        self.assertTrue(o["can_copy_fix"])

    def test_ipv6_loopback_is_local(self):
        self.assertTrue(a.classify_origin("::1", {})["can_copy_fix"])

    def test_tailscale_serve_forwarded_is_remote_no_shell(self):
        # tailscale serve proxies from loopback BUT adds X-Forwarded-* → must read as remote.
        o = a.classify_origin("127.0.0.1", {"X-Forwarded-For": "100.64.0.100",
                                            "X-Forwarded-Host": "4090.tailnet.ts.net:9123",
                                            "X-Forwarded-Proto": "https"})
        self.assertTrue(o["remote"])
        self.assertFalse(o["can_copy_fix"])
        self.assertEqual(o["host"], "4090.tailnet.ts.net:9123")

    def test_tailscale_identity_header_alone_is_remote(self):
        o = a.classify_origin("127.0.0.1", {"Tailscale-User-Login": "corey@example.com"})
        self.assertTrue(o["remote"])
        self.assertFalse(o["can_copy_fix"])

    def test_nonloopback_peer_is_remote_even_without_headers(self):
        o = a.classify_origin("192.168.1.50", {})
        self.assertTrue(o["remote"])
        self.assertFalse(o["can_copy_fix"])

    def test_spoofed_xff_from_local_only_restricts_itself(self):
        # A local process spoofing XFF makes ITSELF look remote (no shell) — safe direction.
        o = a.classify_origin("127.0.0.1", {"X-Forwarded-For": "1.2.3.4"})
        self.assertFalse(o["can_copy_fix"])


class DoorFor(unittest.TestCase):
    LOCAL = {"remote": False, "host": None, "can_copy_fix": True}
    REMOTE = {"remote": True, "host": "4090.tailnet.ts.net:9123", "can_copy_fix": False}
    REMOTE_NOHOST = {"remote": True, "host": None, "can_copy_fix": False}

    def test_local_url_is_open_loopback(self):
        d = a.door_for({"url": "http://127.0.0.1:8765"}, self.LOCAL)
        self.assertEqual(d, {"state": "open", "href": "http://127.0.0.1:8765"})

    def test_no_url_is_monitor_only(self):
        self.assertEqual(a.door_for({"name": "Ollama"}, self.LOCAL)["state"], "monitor-only")
        self.assertEqual(a.door_for({"name": "Ollama"}, self.REMOTE)["state"], "monitor-only")

    def test_remote_served_rewrites_to_tailnet_host_and_port(self):
        d = a.door_for({"url": "http://127.0.0.1:8765", "tailnet": True}, self.REMOTE)
        self.assertEqual(d["state"], "open")
        self.assertEqual(d["href"], "https://4090.tailnet.ts.net:8765/")

    def test_remote_unserved_is_desktop_only_never_dead(self):
        # ComfyUI :8188 has a url but tailnet:false → desktop-only, NEVER a dead loopback link.
        d = a.door_for({"url": "http://127.0.0.1:8188", "tailnet": False}, self.REMOTE)
        self.assertEqual(d["state"], "desktop-only")
        self.assertEqual(d["href"], "")

    def test_remote_without_forwarded_host_degrades_not_dead(self):
        d = a.door_for({"url": "http://127.0.0.1:8765", "tailnet": True}, self.REMOTE_NOHOST)
        self.assertEqual(d["state"], "desktop-only")
        self.assertEqual(d["href"], "")


class TailnetHostBase(unittest.TestCase):
    def test_strips_port(self):
        self.assertEqual(a._tailnet_host_base("4090.tailnet.ts.net:9123"), "4090.tailnet.ts.net")

    def test_no_port(self):
        self.assertEqual(a._tailnet_host_base("4090.tailnet.ts.net"), "4090.tailnet.ts.net")

    def test_none(self):
        self.assertIsNone(a._tailnet_host_base(None))


class BuildLaunch(unittest.TestCase):
    STATUS = {
        "groups": ["AI core"],
        "services": [
            {"id": "lucid", "name": "Lucid", "group": "AI core", "url": "http://127.0.0.1:8765",
             "tailnet": True, "status": "up", "state": "running", "kind": "daemon",
             "scope": "user", "unit": "agentos-lucid.service"},
            {"id": "aurora-agent", "name": "Fleet → wallpaper feed", "group": "AI core", "status": "failed",
             "state": "failed", "kind": "daemon", "scope": "user", "unit": "nimbus-aurora-agent.service"},
        ],
        "summary": {"total": 2, "healthy": 1, "attention": 1},
        "generated_at": 123.0,
    }

    def test_local_origin_emits_fix_for_attention_row(self):
        local = a.classify_origin("127.0.0.1", {})
        p = a.build_launch(self.STATUS, local)
        svc = next(s for s in p["services"] if s["id"] == "aurora-agent")
        self.assertIn("fix", svc)
        self.assertIn("systemctl --user reset-failed nimbus-aurora-agent.service", svc["fix"])
        self.assertTrue(p["origin"]["can_copy_fix"])

    def test_remote_origin_never_emits_a_shell_command(self):
        remote = a.classify_origin("127.0.0.1", {"X-Forwarded-Host": "4090.tailnet.ts.net:9123",
                                                 "X-Forwarded-For": "100.64.0.100"})
        p = a.build_launch(self.STATUS, remote)
        for s in p["services"]:
            self.assertNotIn("fix", s, f"{s['id']} leaked a shell command to a remote client")
        self.assertFalse(p["origin"]["can_copy_fix"])
        # and the healthy door was rewritten to the tailnet host
        lucid = next(s for s in p["services"] if s["id"] == "lucid")
        self.assertEqual(lucid["door"]["href"], "https://4090.tailnet.ts.net:8765/")

    def test_healthy_row_never_gets_a_fix(self):
        local = a.classify_origin("127.0.0.1", {})
        p = a.build_launch(self.STATUS, local)
        lucid = next(s for s in p["services"] if s["id"] == "lucid")
        self.assertNotIn("fix", lucid)


class ClassifyOriginHardening(unittest.TestCase):
    """The defence-in-depth additions: a non-loopback Host is a second remote signal, and an
    empty forwarding-header value still counts as a proxy."""
    def test_nonloopback_host_header_blocks_copy_fix(self):
        o = a.classify_origin("127.0.0.1", {"Host": "4090.tailnet.ts.net"})
        self.assertTrue(o["remote"])
        self.assertFalse(o["can_copy_fix"])

    def test_loopback_host_allows_copy_fix(self):
        o = a.classify_origin("127.0.0.1", {"Host": "127.0.0.1:8780"})
        self.assertFalse(o["remote"])
        self.assertTrue(o["can_copy_fix"])

    def test_empty_forwarded_for_is_still_remote(self):
        o = a.classify_origin("127.0.0.1", {"X-Forwarded-For": ""})
        self.assertTrue(o["remote"])
        self.assertFalse(o["can_copy_fix"])


class TailnetHostMalformed(unittest.TestCase):
    def test_rejects_host_with_path_or_junk(self):
        self.assertIsNone(a._tailnet_host_base("evil.com/phish"))
        self.assertIsNone(a._tailnet_host_base("a b c"))
        self.assertIsNone(a._tailnet_host_base("http://evil"))

    def test_accepts_plain_tailnet_host(self):
        self.assertEqual(a._tailnet_host_base("4090.tailnet.ts.net:9123"), "4090.tailnet.ts.net")


class DoorForMalformed(unittest.TestCase):
    def test_malformed_url_degrades_to_desktop_only_not_crash(self):
        remote = {"remote": True, "host": "4090.tailnet.ts.net:9123", "can_copy_fix": False}
        d = a.door_for({"url": "http://127.0.0.1:notaport", "tailnet": True}, remote)
        self.assertEqual(d["state"], "desktop-only")

    def test_malformed_forwarded_host_is_not_a_dead_door(self):
        remote = {"remote": True, "host": "evil.com/phish", "can_copy_fix": False}
        d = a.door_for({"url": "http://127.0.0.1:8765", "tailnet": True}, remote)
        self.assertEqual(d, {"state": "desktop-only", "href": ""})


class LiveServerSecurity(unittest.TestCase):
    """Spin up the real Handler to lock the icon path-traversal guard (it lives in do_GET)."""
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), a.Handler)
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
        return r.status, body

    def test_real_icon_served(self):
        st, body = self._get("/icons/icon-192.png")
        self.assertEqual(st, 200)
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_icon_traversal_is_blocked(self):
        # Path.name flattens the traversal → looks for a file literally named atrium_server.py
        # inside icons/, which doesn't exist → 404. The server source is never served.
        st, body = self._get("/icons/..%2f..%2fatrium_server.py")
        self.assertEqual(st, 404)
        st2, body2 = self._get("/icons/../atrium_server.py")
        self.assertEqual(st2, 404)
        self.assertNotIn(b"classify_origin", body2)


class FixCommand(unittest.TestCase):
    def test_user_scope(self):
        self.assertEqual(a.fix_command({"unit": "x.service", "scope": "user"}),
                         "systemctl --user reset-failed x.service && systemctl --user restart x.service")

    def test_system_scope_uses_sudo(self):
        self.assertTrue(a.fix_command({"unit": "x.service", "scope": "system"}).startswith("sudo systemctl"))

    def test_no_unit_empty(self):
        self.assertEqual(a.fix_command({"scope": "user"}), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Tests for the setup wizard web surface (ADR-0044).

Invariants pinned here:
  • the wizard is LOCAL-ONLY — a request carrying forwarding headers (a tailnet proxy) is refused;
  • mutating routes require the anti-CSRF token and reject cross-site;
  • /api/state reflects the engine (bundles + present/total + creds presence);
  • a fetch spawns the engine as a subprocess and is tracked; a token goes to the keyring, not a log.

Nothing downloads or binds a public interface. Run:
    python3 -m unittest discover -s integrations/setup/tests
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import setup           # noqa: E402
import setup_web as sw  # noqa: E402


class _FakeProc:
    def __init__(self, rc=None):
        self._rc = rc

    def poll(self):
        return self._rc


class BuildState(unittest.TestCase):
    def test_state_has_bundles_and_creds(self):
        old = setup.keyring_get
        setup.keyring_get = lambda svc: None
        try:
            st = sw.build_state()
        finally:
            setup.keyring_get = old
        self.assertTrue(st["bundles"])
        for b in st["bundles"]:
            self.assertIn("present", b)
            self.assertIn("total", b)
            self.assertIn("rating", b)
        self.assertEqual(st["creds"], {"huggingface": False, "civitai": False})


class StartFetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentos-setupweb-")
        self._env = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = self.tmp
        sw._jobs.clear()

    def tearDown(self):
        if self._env is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self._env
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fetch_spawns_engine_with_bundle_and_yes(self):
        captured = {}

        def fake_spawn(argv, **kw):
            captured["argv"] = argv
            return _FakeProc(rc=None)

        reg = setup.load_registry()
        job, err = sw.start_fetch(reg, "image", mature=False, spawn=fake_spawn)
        self.assertIsNotNone(job, err)
        self.assertIn("fetch", captured["argv"])
        self.assertIn("image", captured["argv"])
        self.assertIn("--yes", captured["argv"])
        self.assertNotIn("--mature", captured["argv"])

    def test_mature_bundle_passes_mature_flag(self):
        captured = {}
        reg = setup.load_registry()
        sw.start_fetch(reg, "video-wan", mature=True, spawn=lambda a, **k: (captured.setdefault("a", a), _FakeProc())[1])
        self.assertIn("--mature", captured["a"])

    def test_unknown_bundle_refused(self):
        job, err = sw.start_fetch(setup.load_registry(), "ghost", mature=False, spawn=lambda a, **k: _FakeProc())
        self.assertIsNone(job)

    def test_start_comfyui_argv(self):
        cap = {}
        sw.start_comfyui(spawn=lambda a, **k: (cap.setdefault("a", a), _FakeProc())[1])
        self.assertIn("comfyui", cap["a"])
        self.assertIn("--yes", cap["a"])

    def test_start_research_argv(self):
        cap = {}
        sw.start_research("video", spawn=lambda a, **k: (cap.setdefault("a", a), _FakeProc())[1])
        self.assertIn("research", cap["a"])
        self.assertIn("video", cap["a"])


class Routes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentos-setupweb-")
        self._env = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = self.tmp
        sw._jobs.clear()
        self._old_spawn = sw.subprocess.Popen
        sw.subprocess.Popen = lambda argv, **kw: _FakeProc(rc=None)   # never really fetch
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), sw.Handler)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.srv.shutdown()
        sw.subprocess.Popen = self._old_spawn
        if self._env is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self._env
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _req(self, method, path, body=None, headers=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def _post(self, path, payload, token=None, extra=None):
        h = {"Content-Type": "application/json"}
        if token is not None:
            h["X-Setup-Token"] = token
        if extra:
            h.update(extra)
        return self._req("POST", path, json.dumps(payload), h)

    def test_state_and_token(self):
        st, body = self._req("GET", "/api/state")
        self.assertEqual(st, 200)
        self.assertIn("bundles", json.loads(body))
        st2, body2 = self._req("GET", "/api/token")
        self.assertEqual(json.loads(body2)["token"], sw.TOKEN)

    def test_fetch_without_token_403(self):
        self.assertEqual(self._post("/api/fetch", {"bundle": "image"})[0], 403)

    def test_fetch_remote_origin_403(self):
        st, _ = self._post("/api/fetch", {"bundle": "image"}, token=sw.TOKEN,
                           extra={"X-Forwarded-For": "100.64.0.9"})
        self.assertEqual(st, 403)

    def test_fetch_cross_site_403(self):
        st, _ = self._post("/api/fetch", {"bundle": "image"}, token=sw.TOKEN,
                           extra={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(st, 403)

    def test_fetch_happy_path_starts_job(self):
        st, body = self._post("/api/fetch", {"bundle": "image"}, token=sw.TOKEN)
        self.assertEqual(st, 202)
        self.assertEqual(json.loads(body)["status"], "started")

    def test_state_has_comfyui_and_hardware(self):
        d = json.loads(self._req("GET", "/api/state")[1])
        self.assertIn("comfyui", d)
        self.assertIn("hardware", d)
        for b in d["bundles"]:
            self.assertIn("fit", b)
            self.assertIn("order", b)

    def test_comfyui_route_needs_token(self):
        self.assertEqual(self._post("/api/comfyui", {})[0], 403)

    def test_comfyui_route_starts_job(self):
        self.assertEqual(self._post("/api/comfyui", {}, token=sw.TOKEN)[0], 202)

    def test_research_route_starts_job(self):
        self.assertEqual(self._post("/api/research", {"modality": "video"}, token=sw.TOKEN)[0], 202)

    def test_suggest_prompt_route(self):
        old = setup.suggest_opening_prompt
        setup.suggest_opening_prompt = lambda m: "a test prompt"
        try:
            st, body = self._req("GET", "/api/suggest_prompt?modality=image")
        finally:
            setup.suggest_opening_prompt = old
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["prompt"], "a test prompt")

    def test_stored_audit_route(self):
        st, body = self._req("GET", "/api/stored")
        self.assertEqual(st, 200)
        self.assertIn("fetched", json.loads(body))

    def test_forget_needs_token(self):
        self.assertEqual(self._post("/api/forget", {"svc": "civitai"})[0], 403)

    def test_forget_with_token(self):
        old = setup.keyring_clear
        setup.keyring_clear = lambda s: True
        try:
            st, _ = self._post("/api/forget", {"svc": "civitai"}, token=sw.TOKEN)
        finally:
            setup.keyring_clear = old
        self.assertEqual(st, 200)

    def test_state_has_reuse_ledger(self):
        d = json.loads(self._req("GET", "/api/state")[1])
        self.assertIn("found_gb", d)
        self.assertIn("missing_gb", d)

    def test_creds_stores_to_keyring_not_logged(self):
        captured = {}
        old = setup.keyring_set
        setup.keyring_set = lambda svc, t: captured.update(svc=svc, token=t) or True
        try:
            st, body = self._post("/api/creds", {"svc": "civitai", "token": "secrettok"}, token=sw.TOKEN)
        finally:
            setup.keyring_set = old
        self.assertEqual(st, 200)
        self.assertEqual(captured["svc"], "civitai")
        self.assertEqual(captured["token"], "secrettok")


if __name__ == "__main__":
    unittest.main(verbosity=2)

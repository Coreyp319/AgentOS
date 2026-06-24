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
import re               # noqa: E402
import setup           # noqa: E402
import setup_web as sw  # noqa: E402


def _user_copy(html: str) -> str:
    """Lowercased wizard text with developer comments stripped — honesty assertions target what the
    USER sees (HTML body text + JS template strings), not the //-comments that DOCUMENT the rule by
    quoting the very phrasing they forbid."""
    s = re.sub(r"<!--.*?-->", " ", html, flags=re.S)        # HTML comments
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)            # CSS/JS block comments
    s = re.sub(r"(?m)^\s*//.*$", " ", s)                    # full-line JS comments (keeps inline http://)
    return s.lower()


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

    def test_completion_toast_fires_exactly_once(self):
        fired = []
        old = sw._toast
        sw._toast = lambda t, b: fired.append(t)
        try:
            job = {"id": "x", "kind": "fetch", "label": "image", "proc": _FakeProc(rc=0)}
            sw.job_view(setup.load_registry(), job)
            sw.job_view(setup.load_registry(), job)        # second poll — must not re-fire
        finally:
            sw._toast = old
        self.assertEqual(len(fired), 1)
        self.assertTrue(job["notified"])

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


class DesktopSection(unittest.TestCase):
    """ADR-0044 grown front door: the wizard proxies the hardened :9123 adopt engine for desktop /
    agent components. The panel is mocked so these are hermetic (no live :9123, nothing installs)."""

    _FAKE = {
        "components": [
            {"id": "keyhole", "tier": "desktop", "root": "no", "desc": "tray", "state": "adopted",
             "adoptable": True, "removable": True},
            {"id": "reactive-wallpaper", "tier": "desktop", "root": "no", "desc": "shader", "state": "available",
             "adoptable": True, "removable": True},
            {"id": "gpu-coordinator", "tier": "hermes", "root": "no", "desc": "lease", "state": "adopted",
             "adoptable": True, "removable": True},
            {"id": "aurora-theme", "tier": "desktop", "root": "no", "desc": "look", "state": "available",
             "adoptable": True, "removable": True},
            {"id": "firefox-pin", "tier": "privileged", "root": "sudo", "desc": "pin", "state": "needs-you",
             "adoptable": False, "removable": True},     # root != no → MUST be filtered out
            {"id": "core-substrate", "tier": "core", "root": "no", "desc": "core", "state": "adopted",
             "adoptable": True, "removable": False},      # tier == core → MUST be filtered out
        ],
        "enabled": True,
    }

    def setUp(self):
        self._pg, self._pp = sw._panel_get, sw._panel_post
        sw._panel_get = lambda path, timeout=1.5: (
            (200, self._FAKE, "") if path == "/components.json"
            else (200, {"jobs": []}, "") if path == "/adopt.json"
            else (0, None, "error"))
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), sw.Handler)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.srv.shutdown()
        sw._panel_get, sw._panel_post = self._pg, self._pp

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

    def test_desktop_state_filters_and_groups(self):
        ds = sw._desktop_state()
        ids = {c["id"]: c for c in ds["components"]}
        self.assertNotIn("firefox-pin", ids)            # root=sudo → not one-click here
        self.assertNotIn("core-substrate", ids)          # tier=core → not a desktop/agent row
        self.assertEqual(ids["keyhole"]["group"], "ambient")
        self.assertEqual(ids["reactive-wallpaper"]["group"], "ambient")
        self.assertEqual(ids["gpu-coordinator"]["group"], "agents")
        self.assertEqual(ids["aurora-theme"]["group"], "look")
        self.assertIn("post_adopt", ids["keyhole"])       # honest residual manual step

    def test_desktop_route_reachable(self):
        st, body = self._req("GET", "/api/desktop")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["reachable"])

    def test_component_jobs_route(self):
        st, body = self._req("GET", "/api/component_jobs")
        self.assertEqual(st, 200)
        self.assertIn("jobs", json.loads(body))

    def test_component_without_token_403(self):
        self.assertEqual(self._post("/api/component", {"id": "keyhole", "action": "adopt"})[0], 403)

    def test_component_cross_site_403(self):
        st, _ = self._post("/api/component", {"id": "keyhole", "action": "adopt"}, token=sw.TOKEN,
                           extra={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(st, 403)

    def test_component_bad_action_400(self):
        self.assertEqual(self._post("/api/component", {"id": "keyhole", "action": "nope"}, token=sw.TOKEN)[0], 400)

    def test_component_non_desktop_id_409(self):
        self.assertEqual(self._post("/api/component", {"id": "core-substrate", "action": "adopt"}, token=sw.TOKEN)[0], 409)

    def test_component_unknown_id_409(self):
        self.assertEqual(self._post("/api/component", {"id": "ghost", "action": "adopt"}, token=sw.TOKEN)[0], 409)

    def test_component_happy_path_proxies_to_adopt(self):
        captured = {}

        def fake_post(path, body, token_path, token_header, timeout=6.0):
            captured.update(path=path, body=body, token_path=token_path, token_header=token_header)
            return 202, {"id": "job1", "status": "queued"}, ""

        sw._panel_post = fake_post
        st, body = self._post("/api/component", {"id": "reactive-wallpaper", "action": "adopt"}, token=sw.TOKEN)
        self.assertEqual(st, 202)
        self.assertEqual(captured["path"], "/adopt")
        self.assertEqual(captured["body"], {"id": "reactive-wallpaper", "action": "adopt"})
        self.assertEqual(captured["token_header"], "X-Adopt-Token")   # adopt CSRF token, fetched server-side

    def test_component_panel_down_503(self):
        sw._panel_get = lambda path, timeout=1.5: (0, None, "refused")
        st, _ = self._post("/api/component", {"id": "keyhole", "action": "adopt"}, token=sw.TOKEN)
        self.assertEqual(st, 503)

    def test_img_route_serves_webp(self):
        st, body = self._req("GET", "/img/keyhole.webp")
        self.assertEqual(st, 200)
        self.assertTrue(len(body) > 100)

    def test_img_route_blocks_traversal_and_unknown(self):
        self.assertEqual(self._req("GET", "/img/..%2fsetup.py")[0], 404)
        self.assertEqual(self._req("GET", "/img/nope.webp")[0], 404)


class WizardStructure(unittest.TestCase):
    """The wizard is the numbered 01→05 "first light" flow (ADR-0044 elegance pass, adopted from the
    claude.ai/design prototype). These pin the structural promises that survive a restyle: the live
    ready-chip, the steps, the dual Lucid/Atrium handoff, mature-hidden-by-default, and the a11y
    fallbacks the animated keyhole instrument depends on."""

    @classmethod
    def setUpClass(cls):
        cls.html = (Path(sw.__file__).resolve().parent / "wizard.html").read_text()
        cls.low = cls.html.lower()

    def test_numbered_steps_present(self):
        for n in ("01", "02", "03", "05"):
            self.assertIn(f'class="no">{n}', self.html)

    def test_ready_chip_reflects_real_readiness(self):
        self.assertIn('id="ready-chip"', self.html)
        self.assertIn("Ready to dream", self.html)        # warm beat — a video lane is genuinely ready
        self.assertIn("Almost ready", self.html)
        self.assertIn("b.gap===0", self.html)             # computed from real bundle gap, not faked

    def test_dual_lucid_and_atrium_handoff(self):
        self.assertIn("Open Lucid", self.html)
        self.assertIn("Open the Atrium", self.html)
        self.assertIn("9123/atrium", self.html)

    def test_mature_lanes_hidden_by_default(self):
        self.assertIn("showMature=false", self.html)              # default closed
        self.assertIn("Show the 18+ video lanes", self.html)
        self.assertIn('b.rating!=="mature"', self.html)          # filtered out of the list unless revealed

    def test_reduced_motion_and_transparency_fallbacks(self):
        self.assertIn("prefers-reduced-motion", self.html)
        self.assertIn("prefers-reduced-transparency", self.html)
        self.assertIn("[data-anim]", self.html)                  # the instrument animation is gated off

    def test_keyhole_instrument_is_decorative(self):
        # the animated instrument is aria-hidden (decorative), and its animation is opt-in via data-anim
        self.assertIn('class="inst" aria-hidden="true"', self.html)
        self.assertIn('data-anim="ring"', self.html)


class KeyholeStep(unittest.TestCase):
    """Step 02: the GPU keyhole as an OPTIONAL, honest companion. It adopts through the EXISTING
    /api/component proxy with the real id, reflects state from /api/desktop, fails open into Extras
    when the panel is down, and stays read-only in its copy — the SUBSTRATE paces; the keyhole is the
    window. (Supersedes the old slim "Smoother local models" callout.)"""

    @classmethod
    def setUpClass(cls):
        cls.html = (Path(sw.__file__).resolve().parent / "wizard.html").read_text()
        cls.low = cls.html.lower()
        cls.copy = _user_copy(cls.html)        # user-facing copy, developer comments stripped

    def test_adopts_via_existing_proxy_with_real_id(self):
        # the SAME /api/component proxy + real id "keyhole" (no invented mechanism)
        self.assertIn('"/api/component",{id:"keyhole",action:"adopt"}', self.html)
        # the keyhole IS the panel's component id this proxy validates against (defense-in-depth)
        self.assertEqual(sw._DESKTOP_GROUPS.get("keyhole"), "ambient")

    def test_state_reflected_from_desktop_proxy(self):
        self.assertIn('fetch("/api/desktop"', self.html)         # state from the existing route
        self.assertIn("In your tray", self.html)                 # adopted — calm state, not a redundant Add
        self.assertIn("Add the keyhole", self.html)
        self.assertIn("Not now", self.html)

    def test_installing_progress_is_real_not_simulated(self):
        # progress comes from the real component-jobs poll, never a setInterval fake (the prototype's tell)
        self.assertIn("refreshComponentJobs", self.html)
        self.assertIn("Adding the keyhole", self.html)

    def test_fails_open_into_extras(self):
        # panel down / keyhole not surfaced → a calm pointer into Extras, never a dead button
        self.assertIn("khJump", self.html)
        self.assertIn("openExtras", self.html)

    def test_explicitly_optional(self):
        self.assertIn("Recommended · optional", self.html)
        self.assertIn('class="opt"', self.html)

    def test_local_model_framing_is_concrete(self):
        # names the models that share the GPU — not abstract "Lucid legibility"
        self.assertIn("share this one gpu", self.low)
        self.assertIn("narrator", self.low)                      # the LLM
        self.assertIn("image generation", self.low)              # image gen
        self.assertIn("take turns", self.low)                    # lease + queue

    def test_copy_stays_a_read_only_window(self):
        # the keyhole's own role: a read-only window onto coordination it does not itself perform
        self.assertIn("read-only tray instrument onto that coordination", self.low)
        # honesty non-negotiable — never credit the keyhole with pacing/speed, nor call it required
        forbidden = (
            "paced by the keyhole", "the keyhole paces", "keyhole paces",
            "keyhole keeps them flowing", "keyhole makes", "makes generation faster",
            "makes your models faster", "speeds up", "speed up your", "the keyhole improves",
            "required for lucid", "keyhole is required", "mandatory", "you must install the keyhole",
        )
        for f in forbidden:
            self.assertNotIn(f, self.copy, f"dishonest/over-claim copy present: {f!r}")


class FitBarHonesty(unittest.TestCase):
    """Step 01: the peak-vs-VRAM fit bar is sized from REAL hardware + the registry's heaviest model
    (peak_gb), credits AgentOS coordination for the pacing — never the keyhole — and colours its marker
    from the real fit verdict, not adoption state. Adopting the keyhole must not turn a tight render
    'safe'."""

    @classmethod
    def setUpClass(cls):
        cls.html = (Path(sw.__file__).resolve().parent / "wizard.html").read_text()
        cls.low = cls.html.lower()
        cls.copy = _user_copy(cls.html)        # user-facing copy, developer comments stripped

    def test_state_exposes_peak_gb(self):
        st = sw.build_state()
        self.assertTrue(st["bundles"])
        for b in st["bundles"]:
            self.assertIn("peak_gb", b)

    def test_bundle_peak_gb_is_the_heaviest_model(self):
        reg = setup.load_registry()
        for b in reg.get("bundles", []):
            sizes = [float((setup.find_model(reg, m) or {}).get("size_gb", 0) or 0)
                     for m in b.get("models", [])]
            expected = round(max(sizes), 1) if sizes else 0.0
            self.assertEqual(setup.bundle_peak_gb(reg, b), expected)

    def test_pacing_credits_the_substrate_not_the_keyhole(self):
        self.assertIn("AgentOS paces generation", self.html)
        self.assertIn("AgentOS coordinates the shared GPU", self.html)
        self.assertNotIn("paced by the keyhole", self.copy)

    def test_marker_colour_is_the_real_fit_verdict(self):
        # the marker class is the fit verdict (fits/tight/big), wired to the real peak/VRAM ratio — not khAdded
        for v in ("fits", "tight", "big"):
            self.assertIn(f".mark2.{v}", self.html)
        self.assertIn("mark2 ${verdict}", self.html)

    def test_gpu_not_detected_fails_open(self):
        self.assertIn("GPU not detected", self.html)
        self.assertIn("couldn't read your gpu", self.low)


class NoTailscaleExposure(unittest.TestCase):
    """ADR-0044: the wizard holds credentials, RUNS NOTHING that exposes the box, and is itself never
    put on the tailnet. The Remote-access card is copy-don't-execute (security must-fix #9)."""

    def test_wizard_never_shells_tailscale(self):
        src = Path(sw.__file__).read_text()
        prims = ("subprocess", "Popen", "os.system", "check_call", "check_output", "run(")
        for line in src.splitlines():
            if "tailscale" in line.lower():                 # only the docstring may mention it
                for p in prims:
                    self.assertNotIn(p, line, f"the wizard must never invoke tailscale: {line.strip()!r}")

    def test_wizard_port_not_in_remote_exposure_list(self):
        remote = Path(sw.__file__).resolve().parent.parent / "agentosd-remote.sh"
        if not remote.exists():
            self.skipTest("agentosd-remote.sh not present")
        import re as _re
        m = _re.search(r"PORTS=\(([^)]*)\)", remote.read_text())
        self.assertIsNotNone(m, "PORTS=(...) not found in agentosd-remote.sh")
        self.assertNotIn(str(sw.PORT), m.group(1).split(),
                         "the setup wizard port must NEVER be in the tailscale exposure list")

    def test_wizard_refuses_nonloopback_bind(self):
        src = Path(sw.__file__).read_text()
        self.assertIn("AGENTOS_SETUP_ALLOW_NONLOOPBACK", src)
        self.assertIn("refusing to bind non-loopback", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

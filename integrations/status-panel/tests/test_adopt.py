#!/usr/bin/env python3
"""Tests for progressive feature adoption (ADR-0043).

Security/safety invariants pinned here:
  • the registry (components.conf) is parsed the same way _driver.sh parses it;
  • read-only detection maps probe→state honestly (present→adopted, absent+root:no→available,
    absent+sudo/manual→needs-you, can't-tell→unknown);
  • validate() admits ONLY a real registry row, ONLY a known action, and ONLY root:no (one-click);
    sudo/manual are refused (printed, never one-click); AGENTOS_ADOPT=0 disables the path;
  • POST /adopt requires the anti-CSRF token, rejects Sec-Fetch-Site:cross-site, AND is
    LOCAL-ORIGIN ONLY (a remote/phone origin is refused — the catalog is read-only there);
  • the out-of-sandbox worker runs the registry's own install.sh/uninstall.sh with the trusted
    component id as --only (no wire string), and the systemd-run argv carries only the job id;
  • the ledger guards (dedupe/cap/cooldown) are atomic; a claim is an at-most-once CAS; a stale
    active job is reaped, not stuck.

Nothing shells out, launches a worker, or installs anything. Run:
    python3 -m unittest discover -s integrations/status-panel/tests
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import status_panel as sp        # noqa: E402
import adopt as A                # noqa: E402
import adopt_run as AR           # noqa: E402


def _write_registry(path: Path) -> None:
    path.write_text(
        "# AgentOS components — test fixture\n"
        "\n"
        "core-substrate   | core       | on  | no     | ../crates/agentosd/dist/apply.sh | ../crates/agentosd/dist/restore.sh | the substrate\n"
        "lucid            | service    | on  | no     | lucid/apply.sh                   | lucid/restore.sh                   | dream surface\n"
        "firefox-pin      | privileged | on  | sudo   | browser-create-video/policy/apply-policy.sh | browser-create-video/policy/restore-policy.sh | root pin\n"
        "tailscale-remote | remote     | off | manual | agentosd-remote.sh up            | agentosd-remote.sh down            | tailnet\n"
    )


# ── registry parsing ────────────────────────────────────────────────────────────────────────
class ParseRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkstemp(suffix=".conf")[1])
        self.tmp.write_text(
            "# header\n\n"
            "lucid | service | on | no | lucid/apply.sh | lucid/restore.sh | the lucid surface\n"
            "firefox-pin | privileged | on | sudo | p/apply.sh | p/restore.sh | pin\n"
            "shortrow | desktop | off | no\n")          # short row → padded
        self._old = A.REGISTRY
        A.REGISTRY = self.tmp

    def tearDown(self):
        A.REGISTRY = self._old
        self.tmp.unlink(missing_ok=True)

    def test_parses_rows_skips_comments_and_blanks(self):
        self.assertEqual([r["id"] for r in A.parse_registry()], ["lucid", "firefox-pin", "shortrow"])

    def test_trims_fields(self):
        r = A.find("lucid")
        self.assertEqual((r["tier"], r["root"], r["apply"]), ("service", "no", "lucid/apply.sh"))

    def test_short_row_is_padded_not_dropped(self):
        self.assertEqual(A.find("shortrow")["apply"], "")


# ── read-only detection → state mapping ──────────────────────────────────────────────────────
class StateMapping(unittest.TestCase):
    def _state(self, present, root):
        old = A.probe_present
        A.probe_present = lambda comp: present
        try:
            return A.component_state({"id": "x", "root": root})
        finally:
            A.probe_present = old

    def test_present_is_adopted(self):
        self.assertEqual(self._state(True, "no"), "adopted")

    def test_absent_root_no_is_available(self):
        self.assertEqual(self._state(False, "no"), "available")

    def test_absent_sudo_is_needs_you(self):
        self.assertEqual(self._state(False, "sudo"), "needs-you")

    def test_absent_manual_is_needs_you(self):
        self.assertEqual(self._state(False, "manual"), "needs-you")

    def test_blind_probe_is_unknown(self):
        self.assertEqual(self._state(None, "no"), "unknown")


# ── ledger isolation base ────────────────────────────────────────────────────────────────────
class LedgerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentos-adopt-test-")
        self._env = {k: os.environ.get(k) for k in ("XDG_RUNTIME_DIR", "XDG_STATE_HOME", "AGENTOS_ADOPT")}
        os.environ["XDG_RUNTIME_DIR"] = self.tmp
        os.environ["XDG_STATE_HOME"] = self.tmp
        os.environ.pop("AGENTOS_ADOPT", None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _comp(self, cid="lucid", root="no", tier="service"):
        return {"id": cid, "tier": tier, "default": "on", "root": root,
                "apply": f"{cid}/apply.sh", "restore": f"{cid}/restore.sh", "desc": cid}


class Ledger(LedgerBase):
    def test_create_update_read_roundtrip(self):
        e, reason = A.try_create_job(self._comp(), "adopt")
        self.assertEqual(reason, "")
        self.assertTrue(A.valid_job_id(e["id"]))
        self.assertEqual(e["status"], "queued")
        A.update_job(e["id"], status="adopted", outcome="installed")
        self.assertEqual(A.read_ledger()["jobs"][e["id"]]["status"], "adopted")

    def test_dedupe_active(self):
        A.try_create_job(self._comp(), "adopt")
        e, reason = A.try_create_job(self._comp(), "adopt")
        self.assertIsNone(e)
        self.assertIn("already", reason)

    def test_global_cap(self):
        for i in range(A.MAX_ACTIVE):
            A.try_create_job(self._comp(f"c{i}"), "adopt")
        e, reason = A.try_create_job(self._comp(f"c{A.MAX_ACTIVE}"), "adopt")
        self.assertIsNone(e)
        self.assertIn("too many", reason)

    def test_cooldown_after_finish(self):
        e, _ = A.try_create_job(self._comp(), "adopt")
        A.update_job(e["id"], status="adopted")
        e2, reason = A.try_create_job(self._comp(), "adopt")
        self.assertIsNone(e2)
        self.assertIn("moment", reason)

    def test_atomic_claim_is_at_most_once(self):
        e, _ = A.try_create_job(self._comp(), "adopt")
        self.assertTrue(A.claim_job(e["id"]))           # first wins → applying
        self.assertEqual(A.read_ledger()["jobs"][e["id"]]["status"], "applying")
        self.assertFalse(A.claim_job(e["id"]))          # second loses

    def test_unadopt_claim_goes_to_unadopting(self):
        e, _ = A.try_create_job(self._comp(), "unadopt")
        self.assertTrue(A.claim_job(e["id"]))
        self.assertEqual(A.read_ledger()["jobs"][e["id"]]["status"], "unadopting")

    def test_reaper_fails_a_stuck_active_job(self):
        old = time.time() - (A.STALE_ACTIVE_S + 10)
        data = {"v": 1, "jobs": {"x": {"id": "x", "status": "applying", "updated": old}}}
        A._reap(data)
        self.assertEqual(data["jobs"]["x"]["status"], "failed")

    def test_public_job_shows_stuck_active_as_failed(self):
        old = time.time() - (A.STALE_ACTIVE_S + 10)
        p = A.public_job({"id": "x", "comp": "c", "status": "applying", "updated": old}, True)
        self.assertEqual(p["status"], "failed")

    def test_public_job_log_flag_local_only(self):
        e = {"id": "x", "comp": "c", "status": "adopted", "updated": time.time(), "log": "/x/y.log"}
        self.assertTrue(A.public_job(e, True).get("has_log"))
        self.assertNotIn("has_log", A.public_job(e, False))

    def test_prune_drops_old_terminal(self):
        data = {"v": 1, "jobs": {
            "old": {"id": "old", "status": "adopted", "updated": time.time() - 7 * 3600},
            "fresh": {"id": "fresh", "status": "applying", "updated": time.time()}}}
        A.prune(data)
        self.assertNotIn("old", data["jobs"])
        self.assertIn("fresh", data["jobs"])


# ── validate (admission) ──────────────────────────────────────────────────────────────────────
class Validate(LedgerBase):
    def setUp(self):
        super().setUp()
        self.reg = Path(self.tmp) / "components.conf"
        _write_registry(self.reg)
        self._old_reg = A.REGISTRY
        A.REGISTRY = self.reg

    def tearDown(self):
        A.REGISTRY = self._old_reg
        super().tearDown()

    def test_happy_root_no(self):
        comp, reason = A.validate("lucid", "adopt")
        self.assertIsNotNone(comp)
        self.assertEqual(reason, "")

    def test_unadopt_is_valid_action(self):
        self.assertIsNotNone(A.validate("lucid", "unadopt")[0])

    def test_unknown_component(self):
        self.assertIsNone(A.validate("ghost", "adopt")[0])

    def test_unknown_action(self):
        self.assertIsNone(A.validate("lucid", "frobnicate")[0])

    def test_sudo_component_refused_printed(self):
        comp, reason = A.validate("firefox-pin", "adopt")
        self.assertIsNone(comp)
        self.assertIn("sudo", reason)

    def test_manual_component_refused_printed(self):
        comp, reason = A.validate("tailscale-remote", "adopt")
        self.assertIsNone(comp)
        self.assertIn("manual", reason)

    def test_kill_switch_disables_all(self):
        os.environ["AGENTOS_ADOPT"] = "0"
        try:
            comp, reason = A.validate("lucid", "adopt")
            self.assertIsNone(comp)
            self.assertIn("disabled", reason)
        finally:
            os.environ.pop("AGENTOS_ADOPT", None)

    def test_sensitive_unadopt_refused(self):
        # core-substrate / status-panel are install-only — un-adopt would bounce the lease or self.
        comp, reason = A.validate("core-substrate", "unadopt")
        self.assertIsNone(comp)
        self.assertIn("terminal", reason)

    def test_sensitive_adopt_still_allowed(self):
        self.assertIsNotNone(A.validate("core-substrate", "adopt")[0])  # first install is fine

    def test_removable_flag_marks_sensitive(self):
        by_id = {c["id"]: c for c in A.list_components(local=True)}
        self.assertFalse(by_id["core-substrate"]["removable"])
        self.assertTrue(by_id["lucid"]["removable"])


class TimeoutLadder(unittest.TestCase):
    def test_reap_ladder_is_ordered(self):
        # worker self-aborts (840) < unit hard cap (900) < reaper threshold (930) — a stuck job
        # always converges to an honest `failed`, never an eternal spinner.
        self.assertLess(AR.RUN_TIMEOUT, A.WORKER_TIMEOUT_S)
        self.assertLess(A.WORKER_TIMEOUT_S, A.STALE_ACTIVE_S)


# ── spawn / worker command construction (no injection) ───────────────────────────────────────
class SpawnWorker(unittest.TestCase):
    def test_systemd_run_argv_shape(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

        old_run, old_which = A.subprocess.run, A.shutil.which
        A.subprocess.run, A.shutil.which = fake_run, (lambda x: "/usr/bin/systemd-run")
        try:
            ok, err = A.spawn_worker("abcdef0123456789")
        finally:
            A.subprocess.run, A.shutil.which = old_run, old_which
        self.assertTrue(ok, err)
        cmd = captured["cmd"]
        self.assertIn("--user", cmd)
        self.assertIn("agentos-adopt-abcdef0123456789", cmd)
        self.assertTrue(any(str(A.WORKER) == c for c in cmd))
        self.assertEqual(cmd[-1], "abcdef0123456789")          # the job id is the ONLY worker arg
        self.assertTrue(any(c.startswith("--property=RuntimeMaxSec=") for c in cmd))

    def test_no_systemd_run_is_honest_failure(self):
        old = A.shutil.which
        A.shutil.which = lambda x: None
        try:
            ok, err = A.spawn_worker("abcdef0123456789")
        finally:
            A.shutil.which = old
        self.assertFalse(ok)
        self.assertIn("systemd-run", err)


class WorkerDriver(unittest.TestCase):
    def _capture(self, action):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

        old = AR.subprocess.run
        AR.subprocess.run = fake_run
        try:
            AR._run_driver(action, "lucid", io.StringIO())
        finally:
            AR.subprocess.run = old
        return captured["cmd"]

    def test_adopt_runs_install_only_yes(self):
        cmd = self._capture("adopt")
        self.assertIn("--only", cmd)
        self.assertIn("lucid", cmd)
        self.assertIn("--yes", cmd)
        self.assertTrue(any(c.endswith("install.sh") for c in cmd))
        self.assertFalse(any(c.endswith("uninstall.sh") for c in cmd))

    def test_unadopt_runs_uninstall_only(self):
        cmd = self._capture("unadopt")
        self.assertTrue(any(c.endswith("uninstall.sh") for c in cmd))
        self.assertIn("--only", cmd)
        self.assertIn("lucid", cmd)


# ── HTTP routes (token + cross-site + LOCAL-ONLY + admission) ─────────────────────────────────
class AdoptRoutes(LedgerBase):
    def setUp(self):
        super().setUp()
        self.reg = Path(self.tmp) / "components.conf"
        _write_registry(self.reg)
        self._old_reg, A.REGISTRY = A.REGISTRY, self.reg
        self._old_spawn, A.spawn_worker = A.spawn_worker, (lambda jid: (True, ""))
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), sp.Handler)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.srv.shutdown()
        A.REGISTRY, A.spawn_worker = self._old_reg, self._old_spawn
        super().tearDown()

    def _req(self, method, path, body=None, headers=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def _post(self, payload, token=None, extra=None):
        h = {"Content-Type": "application/json"}
        if token is not None:
            h["X-Adopt-Token"] = token
        if extra:
            h.update(extra)
        return self._req("POST", "/adopt", json.dumps(payload), h)

    def test_token_endpoint(self):
        st, body = self._req("GET", "/adopt/token")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["token"], A.TOKEN)

    def test_components_json_lists_registry(self):
        st, body = self._req("GET", "/components.json")
        self.assertEqual(st, 200)
        d = json.loads(body)
        self.assertIn("lucid", [c["id"] for c in d["components"]])
        self.assertIn("origin", d)

    def test_components_json_remote_not_adoptable(self):
        # A remote (tailnet) origin sees the catalog read-only — no one-click button.
        st, body = self._req("GET", "/components.json", None, {"X-Forwarded-For": "100.64.0.100"})
        d = json.loads(body)
        self.assertTrue(all(c["adoptable"] is False for c in d["components"]))

    def test_post_without_token_403(self):
        self.assertEqual(self._post({"id": "lucid", "action": "adopt"})[0], 403)

    def test_post_bad_token_403(self):
        self.assertEqual(self._post({"id": "lucid", "action": "adopt"}, token="deadbeef")[0], 403)

    def test_post_cross_site_403(self):
        st, _ = self._post({"id": "lucid", "action": "adopt"}, token=A.TOKEN,
                           extra={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(st, 403)

    def test_post_remote_origin_refused_local_only(self):
        # The adoption-specific gate: a remote/phone origin can NEVER adopt (installs software).
        st, body = self._post({"id": "lucid", "action": "adopt"}, token=A.TOKEN,
                              extra={"X-Forwarded-For": "100.64.0.100"})
        self.assertEqual(st, 403)
        self.assertIn("desktop", json.loads(body)["error"])

    def test_post_empty_body_400(self):
        self.assertEqual(self._req("POST", "/adopt", None, {"X-Adopt-Token": A.TOKEN})[0], 400)

    def test_post_unknown_component_409(self):
        st, body = self._post({"id": "ghost", "action": "adopt"}, token=A.TOKEN)
        self.assertEqual(st, 409)
        self.assertIn("unknown component", json.loads(body)["error"])

    def test_post_sudo_component_409(self):
        st, body = self._post({"id": "firefox-pin", "action": "adopt"}, token=A.TOKEN)
        self.assertEqual(st, 409)
        self.assertIn("sudo", json.loads(body)["error"])

    def test_post_unadopt_sensitive_409(self):
        st, body = self._post({"id": "core-substrate", "action": "unadopt"}, token=A.TOKEN)
        self.assertEqual(st, 409)
        self.assertIn("terminal", json.loads(body)["error"])

    def test_post_happy_path_creates_job(self):
        st, body = self._post({"id": "lucid", "action": "adopt"}, token=A.TOKEN)
        self.assertEqual(st, 202)
        d = json.loads(body)
        self.assertEqual(d["status"], "queued")
        self.assertIn(d["id"], A.read_ledger()["jobs"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

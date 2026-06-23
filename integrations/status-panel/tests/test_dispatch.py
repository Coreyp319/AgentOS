#!/usr/bin/env python3
"""Tests for dispatch-an-agent-on-a-down-service (ADR-0039).

Security/safety invariants pinned here:
  • the auto-fix allowlist (`can_auto_recover`) is OPT-IN — a catalog entry must set
    `auto_recover:true` AND be user-scope AND off the never-auto denylist; system-scope and
    un-flagged (GPU/lease/compositor) units always escalate;
  • a dispatch is only allowed against a service CURRENTLY in attention, de-duped per service,
    capped, cooldown'd, and crashloop-braked — the ledger guards are enforced atomically;
  • POST /dispatch requires the anti-CSRF token AND rejects Sec-Fetch-Site:cross-site;
  • kill-switches: AGENTOS_DISPATCH=0 (all) / AGENTOS_DISPATCH_CLOUD=0 (Claude);
  • evidence is redacted before it can leave the box; the proposed one-liner + log are local-only;
  • a worker claim is an atomic CAS; a SIGKILLed worker's incident is reaped, not stuck.

Nothing shells out, launches a worker, or calls a model. Run:
    python3 -m unittest discover -s integrations/status-panel/tests
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import status_panel as sp        # noqa: E402
import dispatch as D             # noqa: E402
import dispatch_run as DR        # noqa: E402


def _svc(sid, scope="user", unit=None, status="failed", kind="daemon", auto=True):
    return {"id": sid, "name": sid.title(), "group": "AI core", "scope": scope,
            "unit": unit if unit is not None else f"{sid}.service",
            "auto_recover": auto, "status": status, "state": status, "kind": kind, "reach": ""}


class AllowList(unittest.TestCase):
    def test_opted_in_user_scope_is_auto_eligible(self):
        self.assertTrue(D.can_auto_recover(_svc("swaync"))[0])

    def test_not_opted_in_escalates(self):
        ok, why = D.can_auto_recover(_svc("swaync", auto=False))
        self.assertFalse(ok)
        self.assertIn("auto_recover", why)

    def test_system_scope_escalates_needs_sudo(self):
        ok, why = D.can_auto_recover(_svc("ollama", scope="system"))
        self.assertFalse(ok)
        self.assertIn("sudo", why)

    def test_never_auto_units_escalate(self):
        for unit in ("agentos-status-panel.service", "agentos-lease.service"):
            self.assertFalse(D.can_auto_recover(_svc("x", unit=unit))[0], unit)

    def test_no_unit_escalates(self):
        self.assertFalse(D.can_auto_recover({"auto_recover": True, "scope": "user", "unit": ""})[0])

    def test_recover_command_mirrors_panel_fix(self):
        svc = _svc("swaync")
        self.assertEqual(D.recover_command(svc), sp.fix_command(svc))


class Redact(unittest.TestCase):
    def test_strips_secrets_and_pii(self):
        raw = ("Authorization: Bearer abc123XYZ\napi_key=supersecretvalue\n"
               "token: eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJSMeKKF\n"
               "connect 10.0.0.42 user@example.com /home/corey/secret\n"
               "blob deadbeefdeadbeefdeadbeefdeadbeef0000")
        out = D.redact(raw)
        for leak in ("abc123XYZ", "supersecretvalue", "10.0.0.42", "user@example.com",
                     "/home/corey", "deadbeefdeadbeefdeadbeefdeadbeef0000", "eyJhbGci"):
            self.assertNotIn(leak, out, leak)
        self.assertIn("[redacted]", out)


class LedgerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentos-disp-test-")
        self._env = {k: os.environ.get(k) for k in
                     ("XDG_RUNTIME_DIR", "XDG_STATE_HOME", "AGENTOS_DISPATCH", "AGENTOS_DISPATCH_CLOUD")}
        os.environ["XDG_RUNTIME_DIR"] = self.tmp
        os.environ["XDG_STATE_HOME"] = self.tmp
        os.environ.pop("AGENTOS_DISPATCH", None)
        os.environ.pop("AGENTOS_DISPATCH_CLOUD", None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _inject(self, *entries):
        def f(data):
            inc = data.setdefault("incidents", {})
            for e in entries:
                inc[e["id"]] = e
        D._mutate_ledger(f)


class Ledger(LedgerBase):
    def test_create_update_read_roundtrip(self):
        e, reason = D.try_create_incident(_svc("swaync"), "claude")
        self.assertEqual(reason, "")
        self.assertTrue(D.valid_incident_id(e["id"]))
        self.assertEqual(e["status"], "queued")
        self.assertTrue(e["auto_eligible"])
        D.update_incident(e["id"], status="recovered", outcome="recovered by restart")
        led = D.read_ledger()
        self.assertEqual(led["incidents"][e["id"]]["status"], "recovered")

    def test_public_incident_redacts_for_remote(self):
        e, _ = D.try_create_incident(_svc("swaync"), "claude")
        D.update_incident(e["id"], status="needs-approval", confidence="high",
                          proposal="systemctl --user restart foo", diagnosis="it crashed",
                          log="/home/x/.local/state/agentos/dispatch/a.log")
        full = D.read_ledger()["incidents"][e["id"]]
        remote, local = D.public_incident(full, False), D.public_incident(full, True)
        self.assertNotIn("proposal", remote)         # shell one-liner — local only
        self.assertNotIn("log", remote)              # private path — never exposed
        self.assertNotIn("log", local)
        self.assertEqual(local["proposal"], "systemctl --user restart foo")
        self.assertEqual(remote["diagnosis"], "it crashed")
        self.assertEqual(remote["confidence"], "high")
        self.assertIn("auto_eligible", remote)

    def test_reaper_fails_a_stuck_active_incident(self):
        old = time.time() - (D.STALE_ACTIVE_S + 10)
        data = {"v": 1, "incidents": {"x": {"id": "x", "status": "investigating", "updated": old}}}
        D._reap(data)
        self.assertEqual(data["incidents"]["x"]["status"], "failed")

    def test_public_incident_shows_stuck_active_as_failed(self):
        old = time.time() - (D.STALE_ACTIVE_S + 10)
        p = D.public_incident({"id": "x", "svc": "s", "status": "investigating", "updated": old}, True)
        self.assertEqual(p["status"], "failed")

    def test_prune_drops_old_terminal(self):
        data = {"v": 1, "incidents": {
            "old": {"id": "old", "status": "recovered", "updated": time.time() - 7 * 3600},
            "fresh": {"id": "fresh", "status": "investigating", "updated": time.time()}}}
        D.prune(data)
        self.assertNotIn("old", data["incidents"])
        self.assertIn("fresh", data["incidents"])


class Claim(LedgerBase):
    def test_atomic_claim_is_at_most_once(self):
        e, _ = D.try_create_incident(_svc("swaync"), "claude")
        self.assertTrue(D.claim_incident(e["id"]))      # first wins → triaging
        self.assertEqual(D.read_ledger()["incidents"][e["id"]]["status"], "triaging")
        self.assertFalse(D.claim_incident(e["id"]))     # second loses (already claimed)

    def test_claim_missing_incident_is_false(self):
        self.assertFalse(D.claim_incident("ffffffffffffffff"))


class TryCreate(LedgerBase):
    def test_happy_path(self):
        e, reason = D.try_create_incident(_svc("swaync"), "claude")
        self.assertIsNotNone(e)
        self.assertEqual(reason, "")

    def test_dedupe_active(self):
        D.try_create_incident(_svc("swaync"), "claude")
        e, reason = D.try_create_incident(_svc("swaync"), "claude")
        self.assertIsNone(e)
        self.assertIn("already", reason)

    def test_global_cap(self):
        for i in range(D.MAX_ACTIVE):
            D.try_create_incident(_svc(f"s{i}"), "claude")
        e, reason = D.try_create_incident(_svc(f"s{D.MAX_ACTIVE}"), "claude")
        self.assertIsNone(e)
        self.assertIn("too many", reason)

    def test_cooldown_after_finish(self):
        e, _ = D.try_create_incident(_svc("swaync"), "claude")
        D.update_incident(e["id"], status="recovered")
        e2, reason = D.try_create_incident(_svc("swaync"), "claude")
        self.assertIsNone(e2)
        self.assertIn("moment", reason)

    def test_crashloop_brake_skips_first_aid(self):
        # FIRST_AID_MAX prior first-aid restarts of this svc within the window, but past cooldown.
        now, past_cd = time.time(), time.time() - (D.COOLDOWN_S + 10)
        self._inject(*[{"id": f"{i:016x}", "svc": "swaync", "status": "recovered",
                        "first_aid_tried": True, "updated": past_cd} for i in range(D.FIRST_AID_MAX)])
        e, reason = D.try_create_incident(_svc("swaync"), "claude")
        self.assertIsNotNone(e, reason)
        self.assertTrue(e["skip_first_aid"])          # auto-restart braked → escalate instead
        self.assertFalse(e["auto_eligible"])


class Validate(LedgerBase):
    def _snap(self, *svcs):
        return {"services": list(svcs)}

    def test_happy(self):
        svc, reason = D.validate("swaync", "claude", self._snap(_svc("swaync")))
        self.assertIsNotNone(svc)
        self.assertEqual(reason, "")

    def test_unknown_target(self):
        self.assertIsNone(D.validate("swaync", "gemini", self._snap(_svc("swaync")))[0])

    def test_unknown_service(self):
        self.assertIsNone(D.validate("nope", "claude", self._snap(_svc("swaync")))[0])

    def test_healthy_refused(self):
        svc, reason = D.validate("lucid", "claude", self._snap(_svc("lucid", status="up")))
        self.assertIsNone(svc)
        self.assertIn("attention", reason)

    def test_kill_switch_disables_all(self):
        os.environ["AGENTOS_DISPATCH"] = "0"
        try:
            svc, reason = D.validate("swaync", "claude", self._snap(_svc("swaync")))
            self.assertIsNone(svc)
            self.assertIn("disabled", reason)
        finally:
            os.environ.pop("AGENTOS_DISPATCH", None)

    def test_cloud_kill_switch_blocks_claude_not_hermes(self):
        os.environ["AGENTOS_DISPATCH_CLOUD"] = "0"
        try:
            snap = self._snap(_svc("swaync"))
            self.assertIsNone(D.validate("swaync", "claude", snap)[0])      # cloud off
            self.assertIsNotNone(D.validate("swaync", "hermes", snap)[0])   # local still ok
        finally:
            os.environ.pop("AGENTOS_DISPATCH_CLOUD", None)


class BuildBrief(unittest.TestCase):
    def test_brief_journal_is_redacted(self):
        def fake_run(args, timeout=4.0):
            return "Bearer sk-abcdefghijklmnop1234 failed for /home/corey/x"
        cat = {"services": [{"id": "swaync", "scope": "user", "unit": "swaync.service"}]}
        old = sp.CATALOG_PATH
        try:
            tmp = Path(tempfile.mkstemp(suffix=".json")[1])
            tmp.write_text(json.dumps(cat))
            sp.CATALOG_PATH = tmp
            brief = D.build_brief(_svc("swaync"), run=fake_run)
            self.assertNotIn("sk-abcdefghijklmnop1234", brief["journal"])
            self.assertNotIn("/home/corey", brief["journal"])
        finally:
            sp.CATALOG_PATH = old


class ParseModelJson(unittest.TestCase):
    def test_fenced_block(self):
        d = DR._parse_model_json('ok\n```json\n{"diagnosis":"x","proposed_fix":"systemctl restart y","confidence":"high"}\n```')
        self.assertEqual(d["proposed_fix"], "systemctl restart y")
        self.assertEqual(d["confidence"], "high")

    def test_fenced_block_with_nested_braces_in_fix(self):
        d = DR._parse_model_json('```json\n{"diagnosis":"d","proposed_fix":"sed -i s/{a}/{b}/ f","confidence":"low"}\n```')
        self.assertEqual(d["proposed_fix"], "sed -i s/{a}/{b}/ f")

    def test_unfenced_balanced_object_survives_braces(self):
        d = DR._parse_model_json('analysis {"diagnosis":"d","proposed_fix":"echo {x}","confidence":"medium"} trailing')
        self.assertEqual(d["proposed_fix"], "echo {x}")

    def test_no_block_degrades_to_diagnosis(self):
        d = DR._parse_model_json("I could not determine the cause.")
        self.assertIn("could not determine", d["diagnosis"])
        self.assertEqual(d["proposed_fix"], "")

    def test_picks_last_block(self):
        d = DR._parse_model_json('```json\n{"diagnosis":"a","proposed_fix":""}\n```\n```json\n{"diagnosis":"b","proposed_fix":"cmd"}\n```')
        self.assertEqual(d["diagnosis"], "b")


class DispatchRoutes(LedgerBase):
    def setUp(self):
        super().setUp()
        import threading
        from http.server import ThreadingHTTPServer
        self._old_spawn = D.spawn_worker
        D.spawn_worker = lambda iid, target: (True, "")
        self._old_cache = sp._status_cache
        snap = {"groups": ["AI core"],
                "services": [_svc("swaync"), _svc("lucid", status="up", auto=False)],
                "summary": {"total": 2, "healthy": 1, "attention": 1}}
        sp._status_cache = {"t": time.monotonic(), "v": snap}
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), sp.Handler)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.srv.shutdown()
        D.spawn_worker = self._old_spawn
        sp._status_cache = self._old_cache
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
            h["X-Dispatch-Token"] = token
        if extra:
            h.update(extra)
        return self._req("POST", "/dispatch", json.dumps(payload), h)

    def test_token_endpoint(self):
        st, body = self._req("GET", "/dispatch/token")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["token"], D.TOKEN)

    def test_post_without_token_403(self):
        self.assertEqual(self._post({"id": "swaync", "target": "claude"})[0], 403)

    def test_post_bad_token_403(self):
        self.assertEqual(self._post({"id": "swaync", "target": "claude"}, token="deadbeef")[0], 403)

    def test_post_cross_site_403(self):
        st, _ = self._post({"id": "swaync", "target": "claude"}, token=D.TOKEN,
                           extra={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(st, 403)

    def test_post_empty_body_400(self):
        self.assertEqual(self._req("POST", "/dispatch", None, {"X-Dispatch-Token": D.TOKEN})[0], 400)

    def test_post_unknown_service_409(self):
        st, body = self._post({"id": "ghost", "target": "claude"}, token=D.TOKEN)
        self.assertEqual(st, 409)
        self.assertIn("unknown service", json.loads(body)["error"])

    def test_post_healthy_service_409(self):
        st, body = self._post({"id": "lucid", "target": "claude"}, token=D.TOKEN)
        self.assertEqual(st, 409)
        self.assertIn("attention", json.loads(body)["error"])

    def test_post_happy_path_creates_incident(self):
        st, body = self._post({"id": "swaync", "target": "claude"}, token=D.TOKEN)
        self.assertEqual(st, 202)
        d = json.loads(body)
        self.assertEqual(d["status"], "queued")
        self.assertIn(d["id"], D.read_ledger()["incidents"])

    def test_dispatch_json_redacts_for_remote(self):
        e, _ = D.try_create_incident(_svc("swaync"), "claude")
        D.update_incident(e["id"], status="needs-approval", proposal="systemctl --user restart foo",
                          diagnosis="crashed")
        st, body = self._req("GET", "/dispatch.json", None, {"X-Forwarded-For": "100.64.0.9"})
        inc = json.loads(body)["incidents"][0]
        self.assertNotIn("proposal", inc)
        self.assertEqual(inc["diagnosis"], "crashed")
        body2 = self._req("GET", "/dispatch.json")[1]
        self.assertEqual(json.loads(body2)["incidents"][0]["proposal"], "systemctl --user restart foo")

    def test_dispatch_log_local_only(self):
        e, _ = D.try_create_incident(_svc("swaync"), "claude")
        logp = Path(self.tmp) / "agentos" / "dispatch"
        logp.mkdir(parents=True, exist_ok=True)
        (logp / "x.log").write_text("diagnostic transcript")
        D.update_incident(e["id"], log=str(logp / "x.log"))
        st, _ = self._req("GET", f"/dispatch/log?id={e['id']}", None, {"X-Forwarded-For": "100.64.0.9"})
        self.assertEqual(st, 403)
        st2, body = self._req("GET", f"/dispatch/log?id={e['id']}")
        self.assertEqual(st2, 200)
        self.assertIn(b"diagnostic transcript", body)

    def test_dispatch_log_path_outside_dir_refused(self):
        e, _ = D.try_create_incident(_svc("swaync"), "claude")
        D.update_incident(e["id"], log="/etc/passwd")
        st, body = self._req("GET", f"/dispatch/log?id={e['id']}")
        self.assertEqual(st, 200)
        self.assertNotIn(b"root:", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)

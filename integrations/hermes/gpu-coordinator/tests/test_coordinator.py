"""Behavior + concurrency tests for LeaseCoordinator (the impure driver) with a fake
client. These exercise the panel invariants end-to-end through the real lock/pump:

  M2  no double-acquire under concurrency; the burst shares ONE token
  M4  next_call invoked exactly once on every path; refcount balanced; fail-open total
  M1  a renew failure mid-hold re-acquires (self-heal)
  kill-switch ⇒ pure passthrough, zero client traffic

Run from the plugin dir:  python3 -m unittest tests.test_coordinator -v
"""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from coordinator import LeaseCoordinator  # noqa: E402


class FakeClient:
    """In-memory stand-in for BusctlLeaseClient. Records calls; configurable outcome."""

    def __init__(self):
        self.lock = threading.Lock()
        self.next_token = 1
        self.acquires = 0
        self.releases = []
        self.renews = []
        self.held = set()            # tokens the "daemon" currently holds
        self.mode = "grant"          # grant | deny | unreachable
        self.renew_returns = True    # False simulates TTL expiry / daemon restart

    def acquire(self, tier, est):
        with self.lock:
            self.acquires += 1
            if self.mode == "grant":
                t = self.next_token
                self.next_token += 1
                self.held.add(t)
                return (True, t)
            return (False, None)     # deny / unreachable both look like this to the caller

    def release(self, token):
        with self.lock:
            self.releases.append(token)
            existed = token in self.held
            self.held.discard(token)
            return existed

    def renew(self, token):
        with self.lock:
            self.renews.append(token)
            if not self.renew_returns:
                return False
            return token in self.held


def _coord(client, **kw):
    kw.setdefault("renew_interval_s", 3600)   # don't let the real timer fire during tests
    return LeaseCoordinator(client, tier="interactive", estimate_mib=512, **kw)


class TestSingleCall(unittest.TestCase):
    def test_acquire_then_release_around_one_call(self):
        c = FakeClient()
        co = _coord(c)
        out = co.wrap(lambda req: "RESPONSE", {"messages": []})
        self.assertEqual(out, "RESPONSE")
        self.assertEqual(c.acquires, 1)
        self.assertEqual(len(c.releases), 1)
        self.assertEqual(c.held, set())           # released
        self.assertEqual(co.state.refcount, 0)
        self.assertIsNone(co.state.token)

    def test_next_call_invoked_exactly_once_on_grant(self):
        c = FakeClient()
        co = _coord(c)
        calls = []
        co.wrap(lambda req: calls.append(req) or "x", {})
        self.assertEqual(len(calls), 1)


class TestFailOpen(unittest.TestCase):
    def test_unreachable_still_runs_call_once(self):
        c = FakeClient()
        c.mode = "unreachable"
        co = _coord(c)
        calls = []
        out = co.wrap(lambda req: calls.append(1) or "ok", {})
        self.assertEqual(out, "ok")
        self.assertEqual(len(calls), 1)           # M4: exactly once
        self.assertGreaterEqual(co.fail_open_count, 1)
        self.assertIsNone(co.state.token)         # no token held
        self.assertEqual(c.releases, [])          # nothing to release
        self.assertEqual(co.state.refcount, 0)

    def test_next_call_runs_even_if_client_raises(self):
        class BoomClient(FakeClient):
            def acquire(self, tier, est):
                raise RuntimeError("dbus exploded")
        c = BoomClient()
        co = _coord(c)
        calls = []
        out = co.wrap(lambda req: calls.append(1) or "ok", {})
        self.assertEqual(out, "ok")               # enter never raises (M4 total)
        self.assertEqual(len(calls), 1)
        self.assertEqual(co.state.refcount, 0)

    def test_exception_in_next_call_still_releases(self):
        c = FakeClient()
        co = _coord(c)
        with self.assertRaises(ValueError):
            co.wrap(lambda req: (_ for _ in ()).throw(ValueError("boom")), {})
        self.assertEqual(co.state.refcount, 0)    # finally ran _exit
        self.assertEqual(c.held, set())           # token released despite the raise


class TestKillSwitch(unittest.TestCase):
    def test_disabled_is_pure_passthrough(self):
        c = FakeClient()
        co = _coord(c)
        os.environ["AGENTOS_GPU_COORD_DISABLE"] = "1"
        try:
            out = co.wrap(lambda req: "x", {})
        finally:
            del os.environ["AGENTOS_GPU_COORD_DISABLE"]
        self.assertEqual(out, "x")
        self.assertEqual(c.acquires, 0)           # zero client traffic
        self.assertEqual(co.state.refcount, 0)


class TestConcurrency(unittest.TestCase):
    def test_concurrent_burst_shares_one_token(self):
        c = FakeClient()
        co = _coord(c)
        N = 16
        barrier = threading.Barrier(N)
        errors = []

        def body(req):
            # all N calls are simultaneously inside the wrap before any returns
            barrier.wait(timeout=5)
            return "ok"

        def run():
            try:
                co.wrap(body, {})
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=run) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(c.acquires, 1)           # M2/M3: ONE acquire for the whole burst
        self.assertEqual(len(c.releases), 1)      # released once by the last leaver
        self.assertEqual(c.held, set())
        self.assertEqual(co.state.refcount, 0)
        self.assertIsNone(co.state.token)


class TestSelfHeal(unittest.TestCase):
    def test_renew_failure_reacquires(self):
        c = FakeClient()
        co = _coord(c)
        # take a hold by entering once and keeping refcount up
        co._enter()
        self.assertEqual(c.acquires, 1)
        first_token = co.state.token
        self.assertIsNotNone(first_token)
        # daemon "restarts": the held token is no longer recognized
        c.renew_returns = False
        c.held.discard(first_token)
        co._renew_tick()                          # one renewer iteration (deterministic)
        # self-heal: old token cleared, a fresh acquire taken
        self.assertEqual(c.acquires, 2)
        self.assertIsNotNone(co.state.token)
        self.assertNotEqual(co.state.token, first_token)
        co._exit()
        self.assertEqual(co.state.refcount, 0)
        self.assertEqual(c.held, set())


if __name__ == "__main__":
    unittest.main()

"""Live integration test against a running `agentosd lease` daemon.

Drives the REAL BusctlLeaseClient through the LeaseCoordinator and asserts the lease is
actually held (tier=interactive) DURING a wrapped call and released after. Self-skips when
the coordinator isn't on the session bus, so the suite stays green without a daemon.

Run from the plugin dir:  python3 -m unittest tests.test_integration -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from coordinator import LeaseCoordinator      # noqa: E402
from lease_client import BusctlLeaseClient     # noqa: E402


def _can_grant_fresh_interactive() -> bool:
    """Live test needs the daemon on the bus AND enough free VRAM for a fresh interactive
    grant to fit (est 256 + the daemon's 512 headroom floor). The GPU on this box is
    contended by a cycling ComfyUI, so skip — rather than flap — under VRAM pressure."""
    st = BusctlLeaseClient().status()
    if st is None:
        return False
    _held, _tier, _token, free = st
    return free >= 1024


@unittest.skipUnless(_can_grant_fresh_interactive(),
                     "agentosd lease unreachable or GPU too full for a fresh grant")
class TestLiveLease(unittest.TestCase):
    def test_lease_held_during_call_released_after(self):
        client = BusctlLeaseClient()
        # small estimate so a fresh interactive grant fits whatever VRAM is free right now
        coord = LeaseCoordinator(client, tier="interactive", estimate_mib=256, renew_interval_s=3600)

        seen = {}

        def body(_req):
            seen["status"] = client.status()      # observe the lease from outside, mid-call
            return "RESPONSE"

        out = coord.wrap(body, {"messages": []})

        self.assertEqual(out, "RESPONSE")
        self.assertEqual(coord.fail_open_count, 0, "daemon was reachable; should not have failed open")

        held, tier, token, _free = seen["status"]
        self.assertTrue(held, "lease should be held DURING the wrapped call")
        self.assertEqual(tier, "interactive")
        self.assertGreater(token, 0)

        after = client.status()
        self.assertFalse(after[0], "lease should be released AFTER the call returns")
        self.assertIsNone(coord.state.token)
        self.assertEqual(coord.state.refcount, 0)


if __name__ == "__main__":
    unittest.main()

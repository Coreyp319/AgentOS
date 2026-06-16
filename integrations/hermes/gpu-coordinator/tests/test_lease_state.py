"""Pure-core tests for LeaseState — the deterministic refcount/epoch state machine.

Run from the plugin dir:  python3 -m unittest tests.test_lease_state -v
These encode the panel's MUST-FIX invariants (docs/research/0007):
  M3  epoch/generation lost-update guard
  M1  self-heal: renew-failure clears the token so the shell re-acquires
  M4  balanced refcount, idempotent/total exit
No threads, no I/O — every transition is asserted directly.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lease_state import LeaseState  # noqa: E402


class TestEnterExit(unittest.TestCase):
    def test_first_enter_requests_acquire(self):
        s = LeaseState()
        plan = s.enter()
        self.assertIsNotNone(plan)
        self.assertEqual(plan.epoch, 0)
        self.assertEqual(s.refcount, 1)
        self.assertTrue(s.acquiring)

    def test_second_enter_while_acquiring_does_not_start_a_second_acquire(self):
        s = LeaseState()
        s.enter()
        plan2 = s.enter()
        self.assertIsNone(plan2)            # ≤1 acquire in flight (M3)
        self.assertEqual(s.refcount, 2)

    def test_enter_while_holding_token_just_shares(self):
        s = LeaseState()
        p = s.enter()
        s.commit_acquire(p.epoch, granted=True, token=5)
        plan2 = s.enter()
        self.assertIsNone(plan2)            # token held → no acquire, share it
        self.assertEqual(s.refcount, 2)

    def test_commit_granted_stores_token_and_starts_renewer(self):
        s = LeaseState()
        p = s.enter()
        out = s.commit_acquire(p.epoch, granted=True, token=5)
        self.assertTrue(out.stored)
        self.assertTrue(out.start_renewer)
        self.assertFalse(out.fail_open)
        self.assertIsNone(out.release_stale)
        self.assertEqual(s.token, 5)
        self.assertFalse(s.acquiring)

    def test_commit_not_granted_is_fail_open_no_pump(self):
        s = LeaseState()
        p = s.enter()
        out = s.commit_acquire(p.epoch, granted=False, token=None)
        self.assertFalse(out.stored)
        self.assertTrue(out.fail_open)
        self.assertFalse(out.pump)          # daemon down → don't spin
        self.assertIsNone(s.token)
        self.assertFalse(s.acquiring)

    def test_exit_last_releases_token_and_bumps_epoch(self):
        s = LeaseState()
        p = s.enter()
        s.commit_acquire(p.epoch, True, 5)
        out = s.exit()
        self.assertEqual(out.release, 5)
        self.assertEqual(s.refcount, 0)
        self.assertIsNone(s.token)
        self.assertEqual(s.epoch, 1)        # generation closed

    def test_exit_nonlast_does_not_release(self):
        s = LeaseState()
        p = s.enter()
        s.commit_acquire(p.epoch, True, 5)
        s.enter()
        out = s.exit()
        self.assertIsNone(out.release)
        self.assertEqual(s.refcount, 1)
        self.assertEqual(s.token, 5)

    def test_balanced_three_calls_release_exactly_once(self):
        s = LeaseState()
        p = s.enter()
        s.commit_acquire(p.epoch, True, 1)
        s.enter()
        s.enter()
        releases = [o.release for o in (s.exit(), s.exit(), s.exit()) if o.release is not None]
        self.assertEqual(s.refcount, 0)
        self.assertEqual(releases, [1])     # M4: balanced, single release
        self.assertIsNone(s.token)

    def test_idempotent_exit_floors_at_zero(self):
        s = LeaseState()
        out = s.exit()                      # exit with refcount already 0
        self.assertIsNone(out.release)
        self.assertEqual(s.refcount, 0)     # never negative (M4)


class TestLostUpdate(unittest.TestCase):
    """M3: an Acquire started under epoch E that commits after refcount hit 0
    (epoch advanced) must release the stale token rather than store it."""

    def test_epoch_advanced_to_zero_releases_stale_no_pump(self):
        s = LeaseState()
        p = s.enter()                       # epoch 0, acquiring, refcount 1
        out = s.exit()                      # refcount 0, epoch → 1, token None
        self.assertIsNone(out.release)
        self.assertEqual(s.epoch, 1)
        c = s.commit_acquire(0, granted=True, token=9)   # the in-flight epoch-0 acquire
        self.assertFalse(c.stored)
        self.assertEqual(c.release_stale, 9)             # abandon → release it
        self.assertFalse(c.pump)                         # refcount 0 → nothing to re-establish
        self.assertIsNone(s.token)

    def test_epoch_advanced_with_active_calls_pumps_and_reestablishes(self):
        s = LeaseState()
        s.enter()                           # epoch 0, acquiring, refcount 1
        s.exit()                            # refcount 0, epoch → 1, acquiring STILL true (commit pending)
        p2 = s.enter()                      # refcount 1; acquiring already true → no 2nd acquire
        self.assertIsNone(p2)
        c = s.commit_acquire(0, True, 9)    # stale epoch-0 acquire lands
        self.assertFalse(c.stored)
        self.assertEqual(c.release_stale, 9)
        self.assertTrue(c.pump)             # refcount>0, token None → re-establish
        p3 = s.begin_acquire_if_needed()    # shell pumps
        self.assertIsNotNone(p3)
        self.assertEqual(p3.epoch, 1)
        c3 = s.commit_acquire(p3.epoch, True, 10)
        self.assertTrue(c3.stored)
        self.assertEqual(s.token, 10)

    def test_duplicate_token_while_already_holding_is_released(self):
        s = LeaseState()
        p = s.enter()
        s.commit_acquire(p.epoch, True, 5)  # holding token 5
        # a spurious second grant for the same generation must not overwrite
        c = s.commit_acquire(p.epoch, True, 6)
        self.assertFalse(c.stored)
        self.assertEqual(c.release_stale, 6)
        self.assertEqual(s.token, 5)


class TestSelfHeal(unittest.TestCase):
    """M1: a Renew/Release returning False mid-hold clears the token so the shell
    re-acquires — covers TTL expiry and daemon restart with one mechanism."""

    def test_renew_failed_on_live_token_clears_and_signals_pump(self):
        s = LeaseState()
        p = s.enter()
        s.commit_acquire(p.epoch, True, 7)
        pump = s.renew_failed(7)
        self.assertTrue(pump)
        self.assertIsNone(s.token)
        self.assertIsNotNone(s.begin_acquire_if_needed())

    def test_renew_failed_on_stale_token_is_noop(self):
        s = LeaseState()
        p = s.enter()
        s.commit_acquire(p.epoch, True, 7)
        pump = s.renew_failed(6)            # not the current token
        self.assertFalse(pump)
        self.assertEqual(s.token, 7)

    def test_renew_failed_after_release_is_noop(self):
        s = LeaseState()
        p = s.enter()
        s.commit_acquire(p.epoch, True, 7)
        s.exit()                            # token already cleared/released
        pump = s.renew_failed(7)
        self.assertFalse(pump)
        self.assertIsNone(s.token)


if __name__ == "__main__":
    unittest.main()

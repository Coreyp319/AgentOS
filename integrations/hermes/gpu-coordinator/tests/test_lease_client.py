"""Tests for the pure busctl reply parsers (lease_client).

Fixtures are REAL output captured from `agentosd lease` via
`busctl --user --json=short call ...` (see docs/research/0007 / the build log), not
guessed. The parsers must be total: any malformed/short/garbage reply maps to the
fail-open sentinel (None / False), never to a bogus token.

Run from the plugin dir:  python3 -m unittest tests.test_lease_client -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lease_client as _lc  # noqa: E402
from lease_client import (  # noqa: E402
    parse_call_reply,
    parse_acquire,
    parse_bool_reply,
    parse_status,
    _JEEPNEY_OK,
)

try:
    from jeepney import MessageType as _MT
except Exception:  # pragma: no cover
    _MT = None

# --- real captured samples ---
ACQUIRE_DENIED = '{"type":"bts","data":[false,0,"denied: short 261M (free 2299M vs est 2048M + headroom 512M)"]}'
ACQUIRE_GRANTED = '{"type":"bts","data":[true,5,"granted interactive token 5 (free 12000M)"]}'
RELEASE_FALSE = '{"type":"b","data":[false]}'
RELEASE_TRUE = '{"type":"b","data":[true]}'
STATUS_IDLE = '{"type":"bstu","data":[false,"",0,2299]}'
STATUS_HELD = '{"type":"bstu","data":[true,"interactive",5,12000]}'


class TestParseCallReply(unittest.TestCase):
    def test_acquire_denied(self):
        self.assertEqual(parse_call_reply(ACQUIRE_DENIED)[:2], [False, 0])

    def test_release_bool(self):
        self.assertEqual(parse_call_reply(RELEASE_FALSE), [False])

    def test_status_shape(self):
        self.assertEqual(parse_call_reply(STATUS_IDLE), [False, "", 0, 2299])

    def test_garbage_is_none(self):
        self.assertIsNone(parse_call_reply("not json at all"))

    def test_empty_is_none(self):
        self.assertIsNone(parse_call_reply(""))
        self.assertIsNone(parse_call_reply("   \n"))

    def test_missing_data_key_is_none(self):
        self.assertIsNone(parse_call_reply('{"type":"b"}'))

    def test_data_not_a_list_is_none(self):
        self.assertIsNone(parse_call_reply('{"type":"b","data":true}'))

    def test_busctl_error_text_is_none(self):
        # a busctl failure prints to stderr, but if any non-JSON lands on stdout we
        # must not parse a token out of it.
        self.assertIsNone(parse_call_reply("Failed to call method: ..."))


class TestParseAcquire(unittest.TestCase):
    def test_granted_returns_token(self):
        self.assertEqual(parse_acquire(ACQUIRE_GRANTED), (True, 5))

    def test_denied_returns_no_token(self):
        self.assertEqual(parse_acquire(ACQUIRE_DENIED), (False, None))

    def test_garbage_fails_open(self):
        self.assertEqual(parse_acquire("boom"), (False, None))

    def test_granted_but_zero_token_is_not_held(self):
        # granted=true with token 0 is contradictory → treat as not held (fail-open)
        weird = '{"type":"bts","data":[true,0,"x"]}'
        self.assertEqual(parse_acquire(weird), (False, None))

    def test_short_data_fails_open(self):
        self.assertEqual(parse_acquire('{"type":"bts","data":[true]}'), (False, None))


class TestParseBoolReply(unittest.TestCase):
    def test_true(self):
        self.assertTrue(parse_bool_reply(RELEASE_TRUE))

    def test_false(self):
        self.assertFalse(parse_bool_reply(RELEASE_FALSE))

    def test_garbage_is_false(self):
        self.assertFalse(parse_bool_reply("nope"))

    def test_non_bool_payload_is_false(self):
        self.assertFalse(parse_bool_reply('{"type":"b","data":[1]}'))


class TestParseStatus(unittest.TestCase):
    def test_idle(self):
        self.assertEqual(parse_status(STATUS_IDLE), (False, "", 0, 2299))

    def test_held(self):
        self.assertEqual(parse_status(STATUS_HELD), (True, "interactive", 5, 12000))

    def test_garbage_is_none(self):
        self.assertIsNone(parse_status("boom"))

    def test_short_is_none(self):
        self.assertIsNone(parse_status('{"type":"bstu","data":[true,"x"]}'))


# --- persistent jeepney transport (the ADR-0013 B4 fix) ---------------------------------

class _FakeHeader:
    def __init__(self, mt):
        self.message_type = mt


class _FakeReply:
    def __init__(self, body, mt=None):
        self.body = body
        self.header = _FakeHeader(mt if mt is not None else (_MT.method_return if _MT else None))


class _FakeConn:
    """A scripted stand-in for a jeepney blocking connection."""
    def __init__(self, script):
        self.script = list(script)   # each item: a _FakeReply, or an Exception to raise
        self.calls = []
        self.closed = False

    def send_and_get_reply(self, msg, timeout=None):
        self.calls.append(msg)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


@unittest.skipUnless(_JEEPNEY_OK, "jeepney not installed")
class TestJeepneyLeaseClient(unittest.TestCase):
    def setUp(self):
        self._orig_open = _lc.open_dbus_connection
        self._made = []     # connections handed out, in order
        self._queue = []    # connections to hand out (else an empty FakeConn)

        def factory(*a, **k):
            c = self._queue.pop(0) if self._queue else _FakeConn([])
            self._made.append(c)
            return c

        _lc.open_dbus_connection = factory

    def tearDown(self):
        _lc.open_dbus_connection = self._orig_open

    @staticmethod
    def _r(body, mt=None):
        return _FakeReply(body, mt)

    def test_acquire_renew_release_reuse_one_connection(self):
        # THE FIX: a cooperative lease must ride ONE persistent connection so the daemon's
        # B4 peer-disconnect auto-release does not fire between calls.
        conn = _FakeConn([
            self._r([True, 7, "granted"]),   # Acquire
            self._r([True]),                 # Renew
            self._r([True]),                 # Release
        ])
        self._queue = [conn]
        cl = _lc.JeepneyLeaseClient(timeout_s=1.0)
        self.assertEqual(cl.acquire("interactive", 1024), (True, 7))
        self.assertTrue(cl.renew(7))
        self.assertTrue(cl.release(7))
        self.assertEqual(len(self._made), 1)     # opened exactly once
        self.assertEqual(len(conn.calls), 3)     # all three verbs over it
        self.assertFalse(conn.closed)            # connection kept alive

    def test_denied_acquire_fails_open(self):
        self._queue = [_FakeConn([self._r([False, 0, "denied"])])]
        self.assertEqual(_lc.JeepneyLeaseClient().acquire("interactive", 1024), (False, None))

    def test_status_decodes_body(self):
        self._queue = [_FakeConn([self._r([True, "interactive", 7, 12000])])]
        self.assertEqual(_lc.JeepneyLeaseClient().status(), (True, "interactive", 7, 12000))

    def test_socket_error_drops_then_reconnects(self):
        broken = _FakeConn([RuntimeError("socket gone")])
        fresh = _FakeConn([self._r([True, 9, "granted"])])
        self._queue = [broken, fresh]
        cl = _lc.JeepneyLeaseClient()
        self.assertEqual(cl.acquire("interactive", 1024), (False, None))  # fail-open
        self.assertTrue(broken.closed)                                    # dropped
        self.assertEqual(cl.acquire("interactive", 1024), (True, 9))      # reconnected
        self.assertEqual(len(self._made), 2)

    @unittest.skipUnless(_MT is not None, "jeepney MessageType unavailable")
    def test_dbus_error_reply_keeps_connection(self):
        conn = _FakeConn([
            self._r(["boom"], mt=_MT.error),   # error reply → fail-open, but conn is healthy
            self._r([True, 3, "granted"]),     # reused, no reconnect
        ])
        self._queue = [conn]
        cl = _lc.JeepneyLeaseClient()
        self.assertEqual(cl.acquire("interactive", 1024), (False, None))
        self.assertFalse(conn.closed)
        self.assertEqual(cl.acquire("interactive", 1024), (True, 3))
        self.assertEqual(len(self._made), 1)   # NOT reconnected on an error reply

    def test_bad_tier_never_touches_the_bus(self):
        conn = _FakeConn([])
        self._queue = [conn]
        self.assertEqual(_lc.JeepneyLeaseClient().acquire("bogus", 1024), (False, None))
        self.assertEqual(len(conn.calls), 0)
        self.assertEqual(len(self._made), 0)


if __name__ == "__main__":
    unittest.main()

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
from lease_client import (  # noqa: E402
    parse_call_reply,
    parse_acquire,
    parse_bool_reply,
    parse_status,
)

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


if __name__ == "__main__":
    unittest.main()

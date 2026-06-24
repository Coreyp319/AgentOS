#!/usr/bin/env python3
"""ADR-0047 spike tests — the two privacy invariants + transport selection.

Run:  python3 test_notify.py     (prints 'N passed')
"""
from __future__ import annotations

import notify as N

BASE = "https://4090.tail096c29.ts.net:8765"
_tests = []
def test(fn): _tests.append(fn); return fn


# --- blocker #1: content-free payloads -----------------------------------------

@test
def banned_key_rejected():
    try:
        N.assert_content_free({"dream_name": "The Red Door"})
        raise AssertionError("should have raised")
    except N.ContentLeak:
        pass

@test
def nested_banned_key_rejected():
    try:
        N.assert_content_free({"ok": {"deep": {"prompt": "explicit text"}}})
        raise AssertionError("should have raised")
    except N.ContentLeak:
        pass

@test
def safe_payload_allows_generic_title_only():
    p = N.dream_grew("d1", "n1").payload(BASE)
    assert p["title"] == "Lucid"
    N._safe_payload(p)  # must NOT raise — title is the generic constant

@test
def safe_payload_rejects_smuggled_dream_title():
    p = N.dream_grew("d1", "n1").payload(BASE)
    p["title"] = "My Private Dream"     # smuggle real content into the title
    try:
        N._safe_payload(p)
        raise AssertionError("should have raised")
    except N.ContentLeak:
        pass

@test
def deep_link_carries_only_opaque_id():
    link = N.dream_grew("d1", "n-00042").deep_link(BASE)
    assert link == BASE + "/?node=n-00042"
    # nothing in the link except the node id
    assert "dream" not in link.lower() and "prompt" not in link.lower()

@test
def payload_has_no_image_or_thumbnail_key():
    p = N.dream_grew("d1", "n1").payload(BASE)
    for k in ("image", "thumbnail", "thumb", "frame", "clip"):
        assert k not in p


# --- blockers #2/#3: private/mature do not push --------------------------------

@test
def private_dream_suppressed():
    ok, why = N.should_notify(N.DreamMeta("p1", private=True))
    assert not ok and "private" in why

@test
def mature_dream_suppressed_without_optin():
    ok, why = N.should_notify(N.DreamMeta("m1", rating="mature"))
    assert not ok and "mature" in why

@test
def mature_dream_allowed_with_explicit_optin():
    ok, _ = N.should_notify(N.DreamMeta("m1", rating="mature"), allow_mature=True)
    assert ok

@test
def asleep_box_suppressed():
    ok, why = N.should_notify(N.DreamMeta("d1", box_awake=False))
    assert not ok and "asleep" in why

@test
def sfw_awake_dream_allowed():
    ok, _ = N.should_notify(N.DreamMeta("d1"))
    assert ok


# --- transport selection -------------------------------------------------------

@test
def webpush_unavailable_without_dep():
    ok, why = N.WebPushTransport().available()
    assert not ok and "pywebpush" in why

@test
def telegram_unavailable_without_creds():
    ok, why = N.TelegramTransport(None, None).available()
    assert not ok

@test
def telegram_dry_run_when_configured_but_send_false():
    t = N.TelegramTransport("123:abc", "456", send=False)
    ok, _ = t.available()
    assert ok
    r = t.send(N.dream_grew("d1", "n1"), base_url=BASE)
    assert r.ok and "DRY-RUN" in r.detail

@test
def notify_falls_back_to_log_when_others_unavailable():
    ts = [N.TelegramTransport(None, None), N.WebPushTransport(), N.LogTransport()]
    r = N.notify_on_done(N.DreamMeta("d1"), N.dream_grew("d1", "n1"), ts, base_url=BASE)
    assert r.transport == "log" and r.ok

@test
def notify_suppressed_for_private_reaches_no_transport():
    ts = [N.LogTransport()]
    r = N.notify_on_done(N.DreamMeta("p1", private=True), N.dream_grew("p1", "n1"), ts, base_url=BASE)
    assert r.transport == "none" and not r.ok

@test
def burst_count_renders_plural_but_still_content_free():
    p = N.dream_grew("d1", "n9", count=3).payload(BASE)
    assert "3 new clips" in p["body"]
    N._safe_payload(p)  # still content-free


if __name__ == "__main__":
    import io, contextlib
    passed = 0
    for t in _tests:
        try:
            with contextlib.redirect_stdout(io.StringIO()):  # silence LogTransport prints
                t()
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
    print(f"{passed}/{len(_tests)} passed")

#!/usr/bin/env python3
"""ADR-0047 — tests for lucid_notify (the productionized notify leg).
Run:  python3 test_lucid_notify.py    (prints 'N/N passed')
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types

import lucid_notify as N

BASE = "https://4090.tail096c29.ts.net:8765"
_tests = []
def test(fn): _tests.append(fn); return fn


# --- content-free (blocker #1) -------------------------------------------------

@test
def banned_and_title_rejected():
    for bad in ({"dream_name": "X"}, {"title": "leak"}, {"deep": {"prompt": "p"}}):
        try:
            N.assert_content_free(bad); raise AssertionError(f"no raise for {bad}")
        except N.ContentLeak:
            pass

@test
def safe_payload_allows_generic_title():
    N._safe_payload(N.dream_grew("d", "n").payload(BASE))   # must not raise

@test
def safe_payload_rejects_smuggled_title():
    p = N.dream_grew("d", "n").payload(BASE); p["title"] = "My Private Dream"
    try:
        N._safe_payload(p); raise AssertionError("no raise")
    except N.ContentLeak:
        pass

@test
def payload_has_no_media_keys():
    p = N.dream_grew("d", "n").payload(BASE)
    assert not ({"image", "thumbnail", "thumb", "frame", "clip"} & set(p))

@test
def deep_link_is_opaque():
    assert N.dream_grew("d", "n-7").deep_link(BASE) == BASE + "/?node=n-7"


# --- the decision (blockers #2/#3) ---------------------------------------------

@test
def gate_private_suppressed():
    ok, why = N.should_notify(N.DreamMeta("p", private=True)); assert not ok and "private" in why

@test
def gate_mature_needs_optin():
    assert not N.should_notify(N.DreamMeta("m", rating="mature"))[0]
    assert N.should_notify(N.DreamMeta("m", rating="mature"), allow_mature=True)[0]

@test
def gate_asleep_and_sfw():
    assert not N.should_notify(N.DreamMeta("d", box_awake=False))[0]
    assert N.should_notify(N.DreamMeta("d"))[0]


# --- meta derivation (pure) ----------------------------------------------------

@test
def meta_mature_from_floor_node_or_chain():
    assert N.meta_from_fields("s", False, "mature", {"id": 1}).rating == "mature"
    assert N.meta_from_fields("s", False, None, {"id": 1, "rating": "mature"}).rating == "mature"
    assert N.meta_from_fields("s", False, None, {"id": 2},
                              nodes=[{"rating": "mature"}]).rating == "mature"
    assert N.meta_from_fields("s", False, None, {"id": 1}).rating == "sfw"

@test
def meta_carries_private():
    assert N.meta_from_fields("s", True, None, {"id": 1}).private is True


# --- resolve_dream_meta via injected lucid_store/lucid_linear (hermetic) -------

def _inject(is_private_fn, load_chain_fn):
    st = types.ModuleType("lucid_store"); st.is_private = is_private_fn
    ll = types.ModuleType("lucid_linear"); ll.load_chain = load_chain_fn
    sys.modules["lucid_store"] = st; sys.modules["lucid_linear"] = ll

@test
def resolve_reads_chain():
    _inject(lambda s: False, lambda s: {"rating_floor": "mature", "nodes": [{"id": 0}]})
    try:
        m = N.resolve_dream_meta("sess", {"id": 3})
        assert m.private is False and m.rating == "mature" and m.dream_id == "sess"
    finally:
        sys.modules.pop("lucid_store", None); sys.modules.pop("lucid_linear", None)

@test
def resolve_failsafe_on_error():
    # load_chain raises -> uncertainty -> FAIL-SAFE: private+mature so should_notify suppresses.
    _inject(lambda s: False, lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        m = N.resolve_dream_meta("sess", {"id": 1})
        assert m.private is True
        assert N.should_notify(m)[0] is False
    finally:
        sys.modules.pop("lucid_store", None); sys.modules.pop("lucid_linear", None)


# --- config --------------------------------------------------------------------

@test
def config_from_file():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "notify.json")
        json.dump({"telegram": {"bot_token": "t", "chat_id": "c"}, "send": True,
                   "base_url": BASE}, open(p, "w"))
        cfg = N.load_config(path=p, env={})
        assert cfg["telegram"]["bot_token"] == "t" and cfg["send"] is True

@test
def config_env_overrides_and_send_defaults_false():
    cfg = N.load_config(path="/nonexistent", env={
        "LUCID_NOTIFY_TELEGRAM_TOKEN": "T", "LUCID_NOTIFY_TELEGRAM_CHAT": "C"})
    assert cfg["telegram"] == {"bot_token": "T", "chat_id": "C"}
    assert cfg.get("send", False) is False   # dry-run unless explicitly enabled


# --- transports + on_done ------------------------------------------------------

@test
def transports_empty_when_unconfigured():
    assert N.build_transports({}) == []

@test
def transports_telegram_when_configured():
    ts = N.build_transports({"telegram": {"bot_token": "t", "chat_id": "c"}})
    assert len(ts) == 1 and ts[0].name == "telegram"

@test
def telegram_dry_run_until_send_true():
    t = N.TelegramTransport("t", "c", send=False)
    assert t.available()[0]
    r = t.send(N.dream_grew("d", "n"), base_url=BASE)
    assert r.ok and "DRY-RUN" in r.detail

@test
def on_done_suppresses_private():
    r = N.on_done("s", {"id": 1}, _cfg={}, _meta=N.DreamMeta("s", private=True))
    assert r.transport == "none" and "suppressed" in r.detail

@test
def on_done_dormant_when_unconfigured():
    r = N.on_done("s", {"id": 1}, _cfg={}, _meta=N.DreamMeta("s"))
    assert r.transport == "none" and "dormant" in r.detail

@test
def on_done_routes_to_telegram_dry_run():
    r = N.on_done("s", {"id": 9}, base_url=BASE, _meta=N.DreamMeta("s"),
                  _cfg={}, _transports=[N.TelegramTransport("t", "c", send=False)])
    assert r.transport == "telegram" and r.ok

@test
def on_done_never_raises_on_bad_input():
    r = N.on_done(None, None, _meta=None, _cfg=None)   # forces resolve path; must not throw
    assert isinstance(r, N.SendResult)


if __name__ == "__main__":
    import io, contextlib
    p = 0
    for t in _tests:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                t()
            p += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
    print(f"{p}/{len(_tests)} passed")

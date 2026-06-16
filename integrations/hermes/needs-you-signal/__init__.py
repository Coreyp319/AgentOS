"""needs-you-signal — Hermes plugin for the AgentOS ambient layer (P2, ADR-0006).

Hermes keeps pending command-approvals only in the gateway process's RAM
(`tools/approval.py:_gateway_queues`); nothing externalises the count. This
observer plugin mirrors that state to a small file —

    ~/.hermes/needs_you.json  ->  {"pending": N, "updated_at": <ts>, "items": [...]}

— so an out-of-process poller (`agentosd feed`) can light the desktop "needs_you"
signal. It is observer-only: it never vetoes, delays, or alters an approval (the
`pre_/post_approval_request` hooks ignore return values anyway).

Robustness: the file is written atomically (temp + os.replace); `register()`
rewrites an empty file at gateway startup so a clean restart clears any stale
state; the consumer additionally gates on the gateway being alive.
"""

import json
import os
import tempfile
import threading
import time
from pathlib import Path


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


_LOCK = threading.Lock()
_PENDING: "dict[tuple, dict]" = {}  # (session_key, command) -> item
_PATH = _hermes_home() / "needs_you.json"


def _write() -> None:
    """Atomically publish the current pending set. Best-effort; never raises."""
    items = list(_PENDING.values())
    data = {"pending": len(items), "updated_at": time.time(), "items": items}
    tmp_name = None
    try:
        tmp = tempfile.NamedTemporaryFile(
            "w", dir=str(_PATH.parent), prefix=".needs_you.", suffix=".tmp", delete=False
        )
        tmp_name = tmp.name
        try:
            json.dump(data, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
        finally:
            tmp.close()
        os.replace(tmp_name, _PATH)
    except Exception:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def on_pre_approval_request(**kw) -> None:
    key = (kw.get("session_key", ""), kw.get("command", ""))
    with _LOCK:
        _PENDING[key] = {
            "session_key": kw.get("session_key", ""),
            "surface": kw.get("surface", ""),
            "description": kw.get("description", ""),
            "command": kw.get("command", ""),
            "since": time.time(),
        }
        _write()


def on_post_approval_response(**kw) -> None:
    # Fires on approve / deny / timeout — so every pending item self-clears.
    key = (kw.get("session_key", ""), kw.get("command", ""))
    with _LOCK:
        _PENDING.pop(key, None)
        _write()


def register(ctx) -> None:
    ctx.register_hook("pre_approval_request", on_pre_approval_request)
    ctx.register_hook("post_approval_response", on_post_approval_response)
    with _LOCK:
        _write()  # publish an empty file at startup (clears any stale state)

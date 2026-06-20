"""LeaseClient — the impure transport to org.agentos.Coordinator1.

Two transports, same fail-open contract (ADR-0003): any error, timeout, or unparseable
reply degrades to "not granted / False / None", and the caller proceeds with live
inference. The plugin must NEVER block or crash an inference because the coordinator is
unhappy.

  * ``JeepneyLeaseClient`` (PREFERRED) — a single, long-lived session-bus connection held
    for the plugin's lifetime. This is the ADR-0013 "socket client". It is REQUIRED for
    correctness: the daemon binds a cooperative ``Acquire`` to the caller's D-Bus
    connection and auto-releases the lease when that connection drops (ADR-0013 B4). A
    persistent connection means the cooperative lease survives across calls and B4 only
    fires when the plugin process actually dies.
  * ``BusctlLeaseClient`` (FALLBACK) — shells out to ``busctl`` per call. Functional and
    fully fail-open, but each call opens a fresh connection that dies immediately, so the
    daemon auto-releases the cooperative lease seconds later (``renew_failed`` churn; the
    hold never persists, the keyhole never shows it). Used only when jeepney is absent.

``make_lease_client()`` picks jeepney when importable, else busctl.

busctl hardening (panel MUST-FIX M7, docs/research/0007): absolute binary path, shell=False,
list-form argv, type-coerced args, wall-clock + busctl timeouts, capped output, total reply
parsing via --json=short. The reply *validation* is shared by both transports below.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from typing import List, Optional, Tuple

try:  # the persistent transport's dependency — pure-python, no native libs
    from jeepney import DBusAddress, MessageType, new_method_call
    from jeepney.io.blocking import open_dbus_connection
    _JEEPNEY_OK = True
except Exception:  # pragma: no cover - falls back to busctl
    _JEEPNEY_OK = False

BUS_NAME = "org.agentos.Coordinator1"
OBJ_PATH = "/org/agentos/Coordinator1"
IFACE = "org.agentos.Coordinator1"

# Resolve busctl once. Prefer the canonical path; fall back to PATH lookup at import. If
# neither exists the busctl client is permanently fail-open.
_BUSCTL = "/usr/bin/busctl" if os.path.exists("/usr/bin/busctl") else (shutil.which("busctl") or "")

_VALID_TIERS = frozenset({"interactive", "live", "batch", "overnight", "best-effort", "idle"})
_MAX_OUTPUT = 8192  # a typed reply is tens of bytes; anything larger is a hostile/garbage tell


# ----------------------------------------------------------------- reply validation (pure)
# Operate on a decoded reply body (a list), regardless of transport. Total: never raise.

def validate_acquire(data: Optional[List]) -> Tuple[bool, Optional[int]]:
    """`(granted, token)` from an Acquire/Spawn reply `[bool, uint, str]`. Fail-open: a
    denied/garbage reply, or a contradictory granted-with-token-0, yields `(False, None)`."""
    if data is None or len(data) < 2:
        return (False, None)
    granted, token = data[0], data[1]
    if granted is True and isinstance(token, int) and not isinstance(token, bool) and token > 0:
        return (True, token)
    return (False, None)


def validate_bool(data: Optional[List]) -> bool:
    """The single bool from a Release/Renew reply `[bool]`. Fail-open: anything else False."""
    if data is None or len(data) != 1:
        return False
    return data[0] is True


def validate_status(data: Optional[List]) -> Optional[Tuple[bool, str, int, int]]:
    """`(held, tier, token, free_mib)` from a Status reply `[bool, str, uint, uint]`, or None."""
    if data is None or len(data) < 4:
        return None
    held, tier, token, free = data[0], data[1], data[2], data[3]
    if not isinstance(held, bool) or not isinstance(tier, str):
        return None
    if isinstance(token, bool) or isinstance(free, bool):
        return None
    if not isinstance(token, int) or not isinstance(free, int):
        return None
    return (held, tier, token, free)


# --------------------------------------------------------------- busctl --json=short parsing

def parse_call_reply(stdout: str) -> Optional[List]:
    """Parse a `busctl --json=short` reply into its `data` array, or None on any anomaly.

    Shape: {"type":"<sig>","data":[...]}. Total: never raises, never returns a partial.
    """
    if not stdout or not stdout.strip():
        return None
    try:
        obj = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    data = obj.get("data")
    if not isinstance(data, list):
        return None
    return data


def parse_acquire(stdout: str) -> Tuple[bool, Optional[int]]:
    return validate_acquire(parse_call_reply(stdout))


def parse_bool_reply(stdout: str) -> bool:
    return validate_bool(parse_call_reply(stdout))


def parse_status(stdout: str) -> Optional[Tuple[bool, str, int, int]]:
    return validate_status(parse_call_reply(stdout))


# ----------------------------------------------------------- jeepney persistent transport

class JeepneyLeaseClient:
    """Persistent-connection transport (ADR-0013 "socket client"). See module docstring for
    why a long-lived connection is required (B4 peer-disconnect auto-release).

    One session-bus connection is opened lazily and reused for every call (serialized by a
    lock — the renewer thread and the inference wrap share it). Fail-open: any send/reply
    error drops the connection (reconnect next call) and returns the unreachable sentinel;
    a D-Bus *error reply* keeps the connection (it is healthy) and returns the sentinel.
    """

    def __init__(self, timeout_s: float = 2.0) -> None:
        self.timeout_s = float(timeout_s)
        self._addr = DBusAddress(OBJ_PATH, bus_name=BUS_NAME, interface=IFACE)
        self._conn = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return True

    # -- connection lifecycle (caller holds self._lock) --
    def _connect(self):
        if self._conn is None:
            self._conn = open_dbus_connection(bus="SESSION")
        return self._conn

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _call(self, method: str, sig: str, body: tuple) -> Optional[List]:
        """One round-trip over the persistent connection → reply body as a list, or None
        (fail-open). A broken socket drops the connection; an error reply does not."""
        with self._lock:
            try:
                conn = self._connect()
                reply = conn.send_and_get_reply(
                    new_method_call(self._addr, method, sig, body), timeout=self.timeout_s
                )
            except Exception:
                self._drop()  # socket/timeout — force a clean reconnect next call
                return None
            try:
                if reply.header.message_type != MessageType.method_return:
                    return None  # D-Bus error reply: connection is fine, just fail-open
                return list(reply.body)
            except Exception:  # pragma: no cover - defensive
                self._drop()
                return None

    # -- coordinator verbs (each fail-open) --

    def acquire(self, tier: str, estimate_mib: int) -> Tuple[bool, Optional[int]]:
        if tier not in _VALID_TIERS:
            return (False, None)
        est = max(0, int(estimate_mib))
        return validate_acquire(self._call("Acquire", "su", (tier, est)))

    def release(self, token: int) -> bool:
        tok = int(token)
        if tok <= 0:
            return False
        return validate_bool(self._call("Release", "t", (tok,)))

    def renew(self, token: int) -> bool:
        tok = int(token)
        if tok <= 0:
            return False
        return validate_bool(self._call("Renew", "t", (tok,)))

    def status(self) -> Optional[Tuple[bool, str, int, int]]:
        return validate_status(self._call("Status", "", ()))


# ------------------------------------------------------------------ busctl transport

def _bus_env() -> dict:
    """Minimal env for busctl: just what it needs to find the user session bus."""
    env = {}
    for k in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "XDG_DATA_DIRS"):
        v = os.environ.get(k)
        if v:
            env[k] = v
    return env


class BusctlLeaseClient:
    def __init__(self, timeout_s: float = 1.0, busctl_timeout_s: int = 2) -> None:
        self.timeout_s = timeout_s
        self.busctl_timeout_s = busctl_timeout_s

    def available(self) -> bool:
        return bool(_BUSCTL)

    def _run(self, method: str, sig: str, *args: str) -> Optional[str]:
        """Invoke one coordinator method. Returns stdout (str) or None on any failure."""
        if not _BUSCTL:
            return None
        argv = [
            _BUSCTL, "--user", "--json=short", f"--timeout={self.busctl_timeout_s}",
            "call", BUS_NAME, OBJ_PATH, IFACE, method,
        ]
        if sig:
            argv.append(sig)
            argv.extend(args)
        try:
            proc = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                env=_bus_env(),
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode != 0:
            return None
        out = proc.stdout or ""
        if len(out) > _MAX_OUTPUT:
            return None
        return out

    # -- coordinator verbs (each fail-open) --

    def acquire(self, tier: str, estimate_mib: int) -> Tuple[bool, Optional[int]]:
        if tier not in _VALID_TIERS:           # never let an unexpected tier reach argv
            return (False, None)
        est = max(0, int(estimate_mib))
        out = self._run("Acquire", "su", tier, str(est))
        if out is None:
            return (False, None)
        return parse_acquire(out)

    def release(self, token: int) -> bool:
        tok = int(token)
        if tok <= 0:
            return False
        out = self._run("Release", "t", str(tok))
        return parse_bool_reply(out) if out is not None else False

    def renew(self, token: int) -> bool:
        tok = int(token)
        if tok <= 0:
            return False
        out = self._run("Renew", "t", str(tok))
        return parse_bool_reply(out) if out is not None else False

    def status(self) -> Optional[Tuple[bool, str, int, int]]:
        out = self._run("Status", "")
        if out is None:
            return None
        return parse_status(out)


# ------------------------------------------------------------------------------ factory

def make_lease_client(timeout_s: float = 1.0):
    """Prefer the persistent jeepney connection (correct under ADR-0013 B4 peer-disconnect
    auto-release); fall back to the ephemeral busctl transport when jeepney is unavailable
    (functional but churns the cooperative lease — see the two classes above)."""
    if _JEEPNEY_OK:
        # jeepney round-trips are local + sub-ms; give the timeout a little headroom.
        return JeepneyLeaseClient(timeout_s=max(timeout_s, 2.0))
    return BusctlLeaseClient(timeout_s=timeout_s)

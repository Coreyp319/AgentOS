"""LeaseClient — the impure, hardened busctl transport to org.agentos.Coordinator1.

This is the only place the plugin shells out. Everything here is fail-open by
construction (ADR-0003): any subprocess error, timeout, nonzero exit, or unparseable
reply degrades to "not granted / False / None", and the caller proceeds with live
inference. The plugin must NEVER block or crash an inference because the coordinator is
unhappy.

Hardening (panel MUST-FIX M7, docs/research/0007):
  * absolute binary path (no $PATH hijack), shell=False, list-form argv (no injection)
  * every interpolated value is type-coerced before it touches argv (tier enum / int /
    int) — and argv carries no secret (only tier, estimate, token)
  * a wall-clock subprocess timeout AND busctl --timeout (belt and suspenders)
  * stdin=DEVNULL, captured+capped output, minimal env (just the bus address vars)
  * strict, total reply parsing via --json=short → fail-open on any anomaly

The transport is deliberately behind this thin class so ADR-0013 A1 (a private peer
socket + SO_PEERCRED) can replace busctl later without touching the coordinator logic.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import List, Optional, Tuple

BUS_NAME = "org.agentos.Coordinator1"
OBJ_PATH = "/org/agentos/Coordinator1"
IFACE = "org.agentos.Coordinator1"

# Resolve the binary once. Prefer the canonical path; fall back to PATH lookup at import
# (still a fixed absolute path thereafter). If neither exists the client is permanently
# fail-open — every call returns the unreachable sentinel.
_BUSCTL = "/usr/bin/busctl" if os.path.exists("/usr/bin/busctl") else (shutil.which("busctl") or "")

_VALID_TIERS = frozenset({"interactive", "live", "batch", "overnight", "best-effort", "idle"})
_MAX_OUTPUT = 8192  # a typed reply is tens of bytes; anything larger is a hostile/garbage tell


# --------------------------------------------------------------------------- parsing
# Pure functions — unit-tested against real captured busctl --json=short output.

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
    """`(granted, token)` from an Acquire/Spawn reply `[bool, uint, str]`.

    Fail-open: a denied/unreachable/garbage reply, or a contradictory granted-with-token-0,
    yields `(False, None)` — the caller proceeds without holding the lease.
    """
    data = parse_call_reply(stdout)
    if data is None or len(data) < 2:
        return (False, None)
    granted, token = data[0], data[1]
    if granted is True and isinstance(token, int) and not isinstance(token, bool) and token > 0:
        return (True, token)
    return (False, None)


def parse_bool_reply(stdout: str) -> bool:
    """The single bool from a Release/Renew reply `[bool]`. Fail-open: anything else False."""
    data = parse_call_reply(stdout)
    if data is None or len(data) != 1:
        return False
    return data[0] is True


def parse_status(stdout: str) -> Optional[Tuple[bool, str, int, int]]:
    """`(held, tier, token, free_mib)` from a Status reply `[bool, str, uint, uint]`, or None."""
    data = parse_call_reply(stdout)
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


# --------------------------------------------------------------------------- transport

def _bus_env() -> dict:
    """Minimal env for busctl: just what it needs to find the user session bus."""
    env = {}
    for k in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "XDG_DATA_DIRS"):
        v = os.environ.get(k)
        if v:
            env[k] = v
    # busctl --user falls back to $XDG_RUNTIME_DIR/bus when DBUS_SESSION_BUS_ADDRESS is unset.
    return env


class BusctlLeaseClient:
    def __init__(self, timeout_s: float = 1.0, busctl_timeout_s: int = 2) -> None:
        self.timeout_s = timeout_s
        self.busctl_timeout_s = busctl_timeout_s

    def available(self) -> bool:
        return bool(_BUSCTL)

    def _run(self, method: str, sig: str, *args: str) -> Optional[str]:
        """Invoke one coordinator method. Returns stdout (str) or None on any failure.

        Total / fail-open: a missing binary, nonzero exit, timeout, or oversized output
        all return None. Never raises.
        """
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

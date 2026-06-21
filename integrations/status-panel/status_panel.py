#!/usr/bin/env python3
"""AgentOS status panel — a tiny, read-only boot-health view.

Serves a calm web panel (panel.html) plus a /status.json endpoint that reports the
live state of the AgentOS + Nimbus boot stack: systemd unit state (user + system) and,
where a service exposes one, a quick port/HTTP reachability check.

Loopback-only, stdlib-only, no writes — it shells out to `systemctl show`/`list-units`
and does short-timeout HTTP GETs. The service catalog lives in services.json next door.
Open it at http://127.0.0.1:9123 (override with AGENTOS_STATUS_PORT / AGENTOS_STATUS_HOST).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
CATALOG_PATH = HERE / "services.json"
PANEL_PATH = HERE / "panel.html"
LAUNCH_PATH = HERE / "launch.html"
MANIFEST_PATH = HERE / "manifest.webmanifest"
SW_PATH = HERE / "sw.js"
ICONS_DIR = HERE / "icons"
HOST = os.environ.get("AGENTOS_STATUS_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGENTOS_STATUS_PORT", "9123"))

# Map (ActiveState, SubState) → a calm, coarse status the UI colours by.
#   up        steady green   — running / waiting / listening / exited-ok
#   starting  soft amber     — activating
#   stopping  soft amber     — deactivating
#   failed    steady warm-red— failed, or Result != success
#   down      grey           — inactive/dead (not currently up)
#   absent    faint grey     — unit not installed on this machine
#   unknown   grey           — couldn't determine


def _run(args: list[str], timeout: float = 4.0) -> str:
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
        return out.stdout
    except Exception:
        return ""


def _systemctl_base(scope: str) -> list[str]:
    return ["systemctl"] if scope == "system" else ["systemctl", "--user"]


def _state_from_list_units(scope: str, pattern: str, run=_run) -> dict:
    """Read LOAD/ACTIVE/SUB straight from `list-units` for a glob `match` entry — used for
    the xdg-autostart wallpaper unit, whose escaped \\x2d name doesn't round-trip through
    `systemctl show`. Columns: UNIT LOAD ACTIVE SUB DESCRIPTION."""
    out = run(_systemctl_base(scope) + ["list-units", "--all", "--plain", "--no-legend", pattern])
    for line in out.splitlines():
        tok = line.split()
        if len(tok) >= 4 and "." in tok[0]:
            return {"unit": tok[0], "LoadState": tok[1], "ActiveState": tok[2], "SubState": tok[3]}
    return {}


def _show_props(scope: str, unit: str, run=_run) -> dict:
    raw = run(
        _systemctl_base(scope)
        + ["show", unit, "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
           "-p", "Result", "-p", "ActiveEnterTimestamp"]
    )
    props = {}
    for line in raw.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            props[k] = v
    return props


def _unit_status(svc: dict, run=_run) -> dict:
    """Pure-ish: with `run` injected (a fn taking an argv list → stdout string) this has no
    side effects, so the status branching is unit-testable without shelling out."""
    scope = svc.get("scope", "user")
    # kind shapes what "healthy" means:
    #   daemon    — expected to stay active (inactive ⇒ down)
    #   watch     — a .path/.timer; active/waiting ⇒ "ready"
    #   task      — a oneshot/launcher that exits after its job; clean exit ⇒ "ran ✓", not down
    #   on_demand — a backend started only when something needs it (coordinator-spawned ComfyUI);
    #               dormant ⇒ "idle" (calm, never an alarm), running ⇒ up. See ADR-0015.
    kind = svc.get("kind", "daemon")

    # Resolve the unit id. `match` (glob) entries — e.g. the xdg-autostart wallpaper unit,
    # whose escaped \\x2d name is awkward to pass — are located via list-units first.
    listed = {}
    if svc.get("unit"):
        unit = svc["unit"]
    elif svc.get("match"):
        listed = _state_from_list_units(scope, svc["match"], run=run)
        unit = listed.get("unit", "")
    else:
        unit = ""
    if not unit:
        return {"status": "absent", "state": "not installed"}

    props = _show_props(scope, unit, run=run)
    # If show couldn't resolve the escaped name but list-units saw it, trust list-units.
    if props.get("LoadState", "") in ("", "not-found") and listed.get("ActiveState"):
        props = {**listed, "Result": "exit-code" if listed.get("ActiveState") == "failed" else "success"}

    load = props.get("LoadState", "")
    active = props.get("ActiveState", "")
    sub = props.get("SubState", "")
    result = props.get("Result", "")
    since = props.get("ActiveEnterTimestamp", "")

    if load in ("not-found", "") and not active:
        return {"status": "absent", "state": "not installed"}

    if active == "failed" or (result and result != "success"):
        status = "failed"
    elif active == "active":
        status = "up"
    elif active == "activating":
        status = "starting"
    elif active == "deactivating":
        status = "stopping"
    elif active in ("inactive", "dead", ""):
        # Down ≠ bad for non-daemons. A task that exited cleanly did its job ("ok"); an on-demand
        # backend (coordinator-spawned ComfyUI) is just dormant until asked ("idle"). Neither alarms.
        if kind == "task":
            status = "ok"
        elif kind == "on_demand":
            status = "idle"
        else:
            status = "absent" if load == "not-found" else "down"
    else:
        status = "unknown"

    # Friendly, honest label.
    if status == "failed":
        label = f"failed ({result})" if result and result != "success" else "failed"
    elif status == "absent":
        label = "not installed"
    elif status == "ok":
        label = "ran ✓"
    elif status == "idle":
        label = "on-demand"
    elif status == "up" and kind == "watch":
        label = "ready"  # a .path/.timer that's armed and waiting for its trigger
    elif status == "up" and sub in ("running", "listening", "waiting", "exited", "mounted"):
        label = sub
    else:
        label = sub or active or "unknown"

    return {
        "status": status,
        "state": label,
        "active": active,
        "sub": sub,
        "result": result,
        "since": since,
        "unit": unit,
    }


def _reachable(url: str) -> str:
    """Quick reachability: 'reachable' for any HTTP response (even 401/403/404 means the
    port is serving), 'unreachable' for connection refused/timeout, '' if no check."""
    if not url:
        return ""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "agentos-status"})
    try:
        with urllib.request.urlopen(req, timeout=1.5):
            return "reachable"
    except urllib.error.HTTPError:
        return "reachable"  # server answered, just not 2xx
    except Exception:
        return "unreachable"


def read_mood() -> dict:
    """The agent mood for the living backdrop (panel.html), read from the keyhole feed
    ($XDG_RUNTIME_DIR/nimbus-aurora/keyhole.json). Absent/unreadable → calm idle, so the
    backdrop just drifts. Read-only; never blocks the panel over a missing file."""
    calm = {"state": "idle", "busy": 0.0, "warm": 0.0, "snag": 0.0}
    rt = os.environ.get("XDG_RUNTIME_DIR", "")
    if not rt:
        return calm
    try:
        d = json.loads((Path(rt) / "nimbus-aurora" / "keyhole.json").read_text())
        f = d.get("floats", {})
        return {
            "state": d.get("state", "idle"),
            "busy": float(f.get("busy", 0.0)),
            "warm": float(f.get("warm", 0.0)),
            "snag": float(f.get("snag", 0.0)),
        }
    except Exception:
        return calm


def _is_attention(s: dict) -> bool:
    # Genuinely actionable: a failed unit, a daemon that should be up but is down, or
    # something that's "up" yet not answering its port (a split-brain). Tasks that ran,
    # armed watchers, and absent-optional units are NOT attention.
    if s["status"] == "failed":
        return True
    if s["status"] == "down" and s.get("kind", "daemon") == "daemon":
        return True
    if s["status"] in ("up", "starting") and s.get("reach") == "unreachable":
        return True
    return False


def build_status(catalog=None, run=_run, reach=_reachable) -> dict:
    if catalog is None:
        try:
            catalog = json.loads(CATALOG_PATH.read_text())
        except Exception as e:  # a broken catalog shouldn't 500 the panel — say so honestly
            return {"groups": [], "services": [], "generated_at": time.time(),
                    "summary": {"total": 0, "healthy": 0, "attention": 1}, "error": f"catalog: {e}"}

    services = []
    for svc in catalog.get("services", []):
        try:
            st = _unit_status(svc, run=run)
            # Probe reachability whenever a health URL exists (not just when "up"): a
            # failed-but-still-serving service is a split-brain worth surfacing, not hiding.
            # EXCEPT a dormant on-demand backend (status "idle") — "unreachable" is expected
            # there (nothing's asked for it yet), so probing only manufactures a false "no response".
            do_probe = svc.get("health") and st["status"] not in ("absent", "idle")
            reach_state = reach(svc.get("health", "")) if do_probe else ""
            services.append({
                "id": svc.get("id", "?"),
                "name": svc.get("name", "(unnamed)"),
                "group": svc.get("group", "Other"),
                "desc": svc.get("desc", ""),
                "url": svc.get("url", ""),
                # tailnet: is this service's url a real door over `tailscale serve`? Default True;
                # set false in services.json for url-bearing services deliberately not exposed
                # (ComfyUI :8188) so a remote/phone renderer shows them monitor-only, never a dead door.
                "tailnet": svc.get("tailnet", True),
                "scope": svc.get("scope", "user"),
                "kind": svc.get("kind", "daemon"),
                "reach": reach_state,
                **st,
            })
        except Exception as e:  # one bad row becomes one error row, never a blank panel
            print(f"status-panel: bad catalog row {svc!r}: {e}", flush=True)
            services.append({
                "id": svc.get("id", "?"), "name": svc.get("name", "(bad entry)"),
                "group": svc.get("group", "Other"), "desc": "", "url": "", "scope": "user",
                "kind": "daemon", "reach": "", "status": "unknown", "state": "catalog error",
            })

    summary = {
        "total": len(services),
        "healthy": sum(1 for s in services if s["status"] in ("up", "ok", "idle")),
        "attention": sum(1 for s in services if _is_attention(s)),
    }
    return {"groups": catalog.get("groups", []), "services": services,
            "summary": summary, "mood": read_mood(), "generated_at": time.time()}


# ── the launch surface (ADR-0031) ──────────────────────────────────────────────────────────
# A second renderer of the SAME contract, not a second source of truth: desktop = Plasma/KRunner
# (`.desktop` entries — see gen_launchers.py); phone = a read-only PWA launch view (launch.html)
# served by THIS daemon at /atrium (and /?view=launch), installable over the existing
# `tailscale serve` HTTPS origin. The two seams the design council flagged as unproven, now
# folded in from spikes/atrium/ with its tests:
#
#   1. ORIGIN-AWARE DOORS (gap #2/#3): on a remote (tailnet) origin a service is a live door only
#      if its port is actually `tailscale serve`-exposed. Loopback urls are rewritten to the
#      tailnet host; an un-served door (ComfyUI :8188, tailnet:false) renders *desktop-only*,
#      never a dead link the phone 404s on. Decided HERE on the server, not by a client host-read.
#   2. SERVER-EMITTED LOOPBACK SIGNAL (gap #4): the "Copy fix" shell one-liner is offered ONLY to a
#      provably-local request. `tailscale serve` fronts a tailnet request with X-Forwarded-* /
#      Tailscale-User-* headers and a non-loopback Host; a direct desktop request has neither.
#      Fail-closed with TWO independent signals (a forwarding header AND a loopback Host) on top of
#      a loopback peer. A local process spoofing headers only makes ITSELF more restricted — it can
#      never make a remote request look local (the proxy always adds X-Forwarded-For), so the
#      dangerous direction (a shell reaching the phone) is the blocked one.

_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}
# Headers a reverse proxy / `tailscale serve` adds to a forwarded (i.e. non-local) request.
# The PROXY family + the Host rewrite are the UNIVERSAL signal — `tailscale serve` stamps them on
# all served/funnel traffic. The IDENTITY family is USER-traffic only (absent for tagged devices
# and for Funnel), so it is advisory: its *presence* confirms remote, but its *absence* proves
# nothing — never gate trust on an identity header being present (it would mis-trust tagged nodes).
_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded",
                  "x-real-ip", "via", "cf-connecting-ip", "true-client-ip")
_IDENTITY_HEADERS = ("tailscale-user-login", "tailscale-user-name")
_FORWARD_HEADERS = _PROXY_HEADERS + _IDENTITY_HEADERS
_LABEL_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?$")  # one DNS label


def _hostname(host_header: str) -> str:
    """`4090.tailnet.ts.net:9123` → `4090.tailnet.ts.net`; `127.0.0.1:9123` → `127.0.0.1`."""
    if not host_header:
        return ""
    return host_header.rsplit(":", 1)[0] if ":" in host_header else host_header


def classify_origin(peer_ip: str, headers: dict) -> dict:
    """Decide, from the connecting peer + request headers, whether a request is REMOTE (came in
    over the tailnet via `tailscale serve`) and whether it is safe to hand it a shell command.
    `headers` keys are lower-cased before lookup. Returns {remote, host, can_copy_fix, why}."""
    h = {k.lower(): v for k, v in headers.items()}
    # TRUST BASIS (security): `tailscale serve` fronts a tailnet request from a loopback peer, but
    # (a) rewrites Host to the box's tailnet name and (b) adds X-Forwarded-* / Tailscale-User-*.
    # We require TWO independent local signals for can_copy_fix — a loopback Host AND zero
    # forwarding headers — not just a loopback peer, so neither signal alone is load-bearing.
    # Presence of the header KEY (any value, even empty) is a forwarding signal — an empty
    # X-Forwarded-For must not read as "no proxy".
    fwd = [name for name in _FORWARD_HEADERS if name in h]
    peer_local = (peer_ip or "") in _LOOPBACK
    host_hdr = h.get("host", "")
    # Fail CLOSED for the shell affordance: ONLY a present, loopback Host permits copy-fix. An absent
    # Host is ambiguity (a crafted HTTP/1.0 / raw-socket request can omit it) → it does NOT relax the
    # guard. Real desktop browsers always send `Host: 127.0.0.1:9123`, so the legit path pays nothing.
    host_local = host_hdr != "" and _hostname(host_hdr) in _LOOPBACK
    remote = bool(fwd) or not peer_local or (host_hdr != "" and not host_local)
    can_copy_fix = peer_local and not fwd and host_local
    # Sibling-door rewrite host: the proxy's X-Forwarded-Host, else (only when already proven remote)
    # the Host the phone actually connected to — so a proxy that omits XFH doesn't blank every door.
    fhost = h.get("x-forwarded-host") or (host_hdr if remote else None)
    why = (f"proxied (headers: {', '.join(fwd) or 'none'}; host {host_hdr or '-'}; peer {peer_ip})"
           if remote else f"direct loopback ({peer_ip})")
    return {"remote": remote, "host": fhost, "can_copy_fix": can_copy_fix, "why": why}


def _tailnet_host_base(forwarded_host) -> str | None:
    """`4090.tailXXXX.ts.net:9123` → `4090.tailXXXX.ts.net` (drop the port the phone is ON, so
    sibling doors can be built for other ports). None for a missing/malformed host — never build
    a door (an href the user is invited to click) from an unvalidated host. Validates each DNS
    label and the optional port range, so a bad `X-Forwarded-Host` can't shape the rewrite target."""
    if not forwarded_host:
        return None
    host = forwarded_host
    if host.count(":") == 1:
        host, _, port = host.partition(":")
        if not port.isdigit() or not (1 <= int(port) <= 65535):
            return None
    elif ":" in host:
        return None                           # ipv6 / junk — not a tailnet name
    labels = host.split(".")
    if not labels or any(not _LABEL_RE.match(lbl) for lbl in labels):
        return None
    return host


def door_for(svc: dict, origin: dict) -> dict:
    """Classify a service into a door the client renders verbatim — never a dead link.
    state ∈ {open (a real destination on THIS origin), monitor-only (no url), desktop-only
    (has a url but is not reachable on this remote origin — tailnet:false, or no trustworthy
    tailnet host to rewrite to)}."""
    url = svc.get("url", "")
    if not url:
        return {"state": "monitor-only", "href": ""}
    if not origin["remote"]:
        return {"state": "open", "href": url}            # local desktop: loopback url as-is
    # Remote/tailnet origin.
    if not svc.get("tailnet", True):
        return {"state": "desktop-only", "href": ""}
    base = _tailnet_host_base(origin.get("host"))
    if not base:
        return {"state": "desktop-only", "href": ""}     # remote but no host to rewrite to → honest
    try:
        port = urlsplit(url).port
    except ValueError:
        return {"state": "desktop-only", "href": ""}
    suffix = "" if port in (None, 443) else f":{port}"
    return {"state": "open", "href": f"https://{base}{suffix}/"}


def fix_command(svc: dict) -> str:
    """The read-only recovery one-liner (copy-don't-execute). Mirrors panel.html exactly."""
    unit = svc.get("unit", "")
    sc = "sudo systemctl" if svc.get("scope") == "system" else "systemctl --user"
    return f"{sc} reset-failed {unit} && {sc} restart {unit}" if unit else ""


def _public_origin(o: dict) -> dict:
    """The client-facing subset of an origin verdict — never the `why` debug string (which carries
    the peer IP / Host); the client only needs to know its trust + the host to render doors."""
    return {"remote": o["remote"], "can_copy_fix": o["can_copy_fix"], "host": o.get("host")}


def build_launch(status: dict, origin: dict) -> dict:
    """Fold the status payload into a launch payload: per-service door + (only when local) the
    recovery command. The client renders this with zero origin logic of its own. One bad service
    degrades to one skipped row, never a blank view (mirrors build_status)."""
    out_services = []
    for s in status.get("services", []):
        try:
            row = {
                "id": s.get("id"), "name": s.get("name"), "group": s.get("group"),
                "desc": s.get("desc", ""), "status": s.get("status"), "state": s.get("state"),
                "kind": s.get("kind", "daemon"), "reach": s.get("reach", ""),
                "scope": s.get("scope", "user"),
                "door": door_for(s, origin),
            }
            # Recovery command ONLY on a provably-local origin AND only for an attention row.
            if origin["can_copy_fix"] and _is_attention(s) and s.get("unit"):
                row["fix"] = fix_command(s)
            out_services.append(row)
        except Exception as e:  # one malformed row never blanks the launcher
            print(f"status-panel: bad launch row {s.get('id')!r}: {e}", flush=True)
    return {
        "groups": status.get("groups", []),
        "services": out_services,
        "summary": status.get("summary", {}),
        "generated_at": status.get("generated_at"),
        "origin": _public_origin(origin),
    }


# ── status snapshot with a short TTL ──────────────────────────────────────────────────────
# build_status() shells out to systemctl + probes; under a wedged unit it can take seconds.
# Memoise so concurrent /status.json + /launch.json polls don't each fork a fresh fan-out.
# CRITICAL (resource-safety): the slow build runs OUTSIDE the lock — serve-stale-while-refreshing
# — so a wedged `systemctl` can never park every request thread on the lock. While one thread
# refreshes, concurrent callers get the last-good snapshot immediately (fail-open: a stale panel,
# never a hung one). The returned dict is SHARED — treat it as read-only; handlers merge only at
# the top level (e.g. add `origin`), never mutate nested members.
_CACHE_TTL = 2.5
_status_cache: dict = {"t": 0.0, "v": None}
_status_lock = threading.Lock()
_status_refreshing = False


def cached_status() -> dict:
    global _status_refreshing
    now = time.monotonic()
    with _status_lock:
        have = _status_cache["v"]
        if have is not None and (now - _status_cache["t"]) <= _CACHE_TTL:
            return have                       # fresh enough
        if have is not None and _status_refreshing:
            return have                       # another thread is rebuilding — serve last-good
        _status_refreshing = True             # we own the refresh
    try:
        built = build_status()                # slow path, deliberately outside the lock
    except Exception:                         # build_status is internally fail-safe, but belt+braces
        with _status_lock:
            _status_refreshing = False
        if _status_cache["v"] is not None:
            return _status_cache["v"]
        raise
    with _status_lock:
        _status_cache["v"] = built
        _status_cache["t"] = time.monotonic()
        _status_refreshing = False
    return built


def why(svc_id: str, run=_run) -> dict:
    """Read-only `journalctl` tail for a *catalog* service. The unit is looked up from the
    trusted catalog by id — the query string id only selects a row, it is never used as a
    unit name — so there's no journalctl-arg injection surface."""
    try:
        catalog = json.loads(CATALOG_PATH.read_text())
    except Exception as e:
        return {"error": f"catalog: {e}"}
    svc = next((s for s in catalog.get("services", []) if s.get("id") == svc_id), None)
    if not svc:
        return {"error": "unknown service"}
    scope = svc.get("scope", "user")
    unit = svc.get("unit") or _state_from_list_units(scope, svc.get("match", ""), run=run).get("unit")
    if not unit:
        return {"error": "no unit for this service"}
    base = ["journalctl"] if scope == "system" else ["journalctl", "--user"]
    out = run(base + ["-u", unit, "-n", "14", "--no-pager", "-o", "short-iso"])
    return {"unit": unit, "lines": out.strip() or "(no recent journal lines)"}


def _toast(svc: dict) -> None:
    """Fire one calm swaync notification for a service that fell over. swaync owns the
    interrupt (surface-labor contract); the panel only proposes the recovery."""
    name, state = svc.get("name", svc.get("id", "A service")), svc.get("state", "failed")
    try:
        subprocess.run(
            ["notify-send", "-a", "AgentOS", "-u", "normal", "-i", "dialog-warning",
             f"{name} — {state}",
             "Came up at boot, now stopped. Open the status panel to see why and copy the fix."],
            check=False, timeout=5,
        )
    except Exception:
        pass


def _notify_loop(interval: float = 15.0) -> None:
    """Edge-detect *new* post-boot failures and route one debounced swaync toast each.
    Silent during the boot window and silent on recovery — only an earned interruption.
    Disable with AGENTOS_STATUS_NOTIFY=0."""
    if os.environ.get("AGENTOS_STATUS_NOTIFY", "1") == "0" or not shutil.which("notify-send"):
        return
    notified: set = set()
    settled = False
    started = time.monotonic()
    while True:
        time.sleep(interval)
        try:
            # Share the cached snapshot rather than forking a second systemctl fan-out every 15s.
            attn = {s["id"]: s for s in cached_status()["services"] if _is_attention(s)}
        except Exception:
            continue
        if not settled:
            # Boot churn is not a failure: wait until the stack first reads clean, or 90s.
            if not attn or (time.monotonic() - started) > 90:
                settled = True
                notified = set(attn)  # pre-settled failures were already surfaced at login
            continue
        for sid, svc in attn.items():
            if sid not in notified:
                _toast(svc)
                notified.add(sid)
        notified &= set(attn)  # a recovered service may earn a fresh toast if it fails again


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):  # quiet; journal handles logging
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _origin(self) -> dict:
        peer = self.client_address[0] if self.client_address else ""
        return classify_origin(peer, dict(self.headers.items()))

    def _send_file(self, path: Path, ctype: str, missing: str):
        try:
            self._send(200, path.read_bytes(), ctype)
        except FileNotFoundError:
            self._send(404, missing.encode(), "text/plain")

    def do_GET(self):
        from urllib.parse import parse_qs
        split = urlsplit(self.path)
        path = split.path
        qs = parse_qs(split.query)
        # `/` is the diagnose panel; `/?view=launch` and `/atrium` are the launch view (ADR-0031).
        if path in ("/", "/index.html"):
            if qs.get("view", [""])[0] == "launch":
                self._send_file(LAUNCH_PATH, "text/html; charset=utf-8", "launch.html missing")
            else:
                self._send_file(PANEL_PATH, "text/html; charset=utf-8", "panel.html missing")
        elif path == "/atrium":
            self._send_file(LAUNCH_PATH, "text/html; charset=utf-8", "launch.html missing")
        elif path == "/status.json":
            try:
                # Origin-decided server-side so a remote (phone) client never client-guesses its
                # own trust (gap #4): panel.html gates its shell affordances on origin.can_copy_fix.
                payload = dict(cached_status())          # top-level copy; never mutate nested
                payload["origin"] = _public_origin(self._origin())
                self._send(200, json.dumps(payload).encode(), "application/json")
            except Exception as e:  # never 500 the whole panel over one bad probe
                self._send(200, json.dumps({"error": str(e)}).encode(), "application/json")
        elif path == "/launch.json":
            origin = self._origin()
            try:
                payload = build_launch(cached_status(), origin)
                self._send(200, json.dumps(payload).encode(), "application/json")
            except Exception as e:
                # Never leak internals to a remote client.
                msg = str(e) if not origin["remote"] else "the box could not build the view"
                self._send(200, json.dumps({"error": msg, "services": [], "groups": []}).encode(),
                           "application/json")
        elif path == "/manifest.webmanifest":
            self._send_file(MANIFEST_PATH, "application/manifest+json", "manifest missing")
        elif path == "/sw.js":
            self._send_file(SW_PATH, "text/javascript; charset=utf-8", "sw.js missing")
        elif path.startswith("/icons/"):
            f = ICONS_DIR / Path(path).name      # Path.name flattens any ../ traversal
            try:
                # is_file() (not exists()) so `/icons/..` — which flattens to the dir itself —
                # doesn't slip past into a read that raises IsADirectoryError + drops the conn.
                if f.parent == ICONS_DIR and f.is_file():
                    self._send(200, f.read_bytes(), "image/png")
                else:
                    self._send(404, b"no icon", "text/plain")
            except OSError:
                self._send(404, b"no icon", "text/plain")
        elif path == "/why":
            sid = qs.get("id", [""])[0]
            self._send(200, json.dumps(why(sid)).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")


def main():
    # Security floor (ADR-0031): the panel now serves /launch.json + a (local-only) shell one-liner
    # and your service map. It must stay loopback-bound; `tailscale serve` is the ONLY sanctioned
    # exposure path. Refuse a non-loopback bind unless explicitly opted in, so an env typo can't
    # surface the shell affordance on the LAN.
    if _hostname(HOST) not in _LOOPBACK and os.environ.get("AGENTOS_STATUS_ALLOW_NONLOOPBACK") != "1":
        print(f"refusing to bind non-loopback host {HOST!r}; expose via `tailscale serve`, or set "
              f"AGENTOS_STATUS_ALLOW_NONLOOPBACK=1 to override.", file=sys.stderr)
        sys.exit(2)
    threading.Thread(target=_notify_loop, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"AgentOS status panel → http://{HOST}:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

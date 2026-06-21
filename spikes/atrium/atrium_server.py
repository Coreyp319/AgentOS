#!/usr/bin/env python3
"""The Atrium — a measurement spike for the AgentOS launch surface (ADR-0031).

This is NOT a new service. It proves the *thin* launch-view design the design council
decided on, so the numbers exist before any `integrations/` change:

  • desktop = Plasma/KRunner (`.desktop` entries) — out of scope here, it's a no-server path.
  • phone   = a read-only PWA launch view served by the EXISTING status panel.

So this server deliberately reuses the production catalog + status logic
(`integrations/status-panel/{services.json,status_panel.py}`) and only adds the launch-view
seams the council flagged as unproven:

  1. ORIGIN-AWARE DOORS (gap #2/#3): on a remote (tailnet) origin, a service is a live door
     only if its port is actually `tailscale serve`-exposed. Loopback URLs are rewritten to
     the tailnet host; un-served doors (ComfyUI :8188, `tailnet:false`) render *desktop-only*,
     never a dead link. Everything is decided HERE on the server and handed to the client —
     no client `location.host` guessing.

  2. SERVER-EMITTED LOOPBACK SIGNAL (gap #4): the "Copy fix" shell one-liner is emitted ONLY
     when the request is provably local. `tailscale serve` fronts a tailnet request with
     X-Forwarded-* / Tailscale-User-* headers and a non-loopback Host; a direct desktop request
     has neither. Fail-closed with TWO independent signals (forwarding headers AND a loopback
     Host) on top of a loopback peer. A local process spoofing headers only makes ITSELF more
     restricted — it can never make a remote request look local, because the proxy always adds
     X-Forwarded-For. The dangerous direction (a shell reaching the phone) is the blocked one.

  3. PWA shell (gap #5): /manifest.webmanifest + /sw.js so the view is installable over the
     existing HTTPS origin, with an honest offline state instead of a blank error.

Loopback-only, stdlib-only, no writes. Run:  python3 atrium_server.py   (→ :8780 by default)
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
# Reuse the production status panel — the launch view is a renderer over the SAME contract,
# not a second source of truth. (Repo layout: spikes/atrium → ../../integrations/status-panel.)
PANEL_DIR = HERE.parent.parent / "integrations" / "status-panel"
sys.path.insert(0, str(PANEL_DIR))
try:
    import status_panel as sp  # noqa: E402
except Exception as e:  # the spike still serves its static shell if the panel import fails
    sp = None
    _IMPORT_ERR = e

HOST = os.environ.get("ATRIUM_HOST", "127.0.0.1")
PORT = int(os.environ.get("ATRIUM_PORT", "8780"))

_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}
# Headers a reverse proxy / `tailscale serve` adds to a forwarded (i.e. non-local) request.
_FORWARD_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
                    "forwarded", "tailscale-user-login", "tailscale-user-name")
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+(:\d{1,5})?$")  # hostname[:port], nothing exotic


def _hostname(host_header: str) -> str:
    """`4090.tailnet.ts.net:9123` → `4090.tailnet.ts.net`; `127.0.0.1:8780` → `127.0.0.1`."""
    if not host_header:
        return ""
    return host_header.rsplit(":", 1)[0] if ":" in host_header else host_header


# ── pure, unit-testable core ─────────────────────────────────────────────────────────────

def classify_origin(peer_ip: str, headers: dict) -> dict:
    """Decide, from the connecting peer + request headers, whether this request is REMOTE
    (came in over the tailnet via `tailscale serve`) and whether it is safe to hand it a shell
    command. `headers` is a dict (keys lower-cased before lookup).

    Returns: {remote, host, can_copy_fix, why}
      • remote        — render doors for the tailnet, suppress un-served ports.
      • host          — the tailnet host:port the client navigated to (X-Forwarded-Host),
                        used to rewrite loopback doors → tailnet doors.
      • can_copy_fix  — emit the systemctl one-liner. True ONLY when provably local (fail-closed):
                        loopback peer, NO forwarding header present, and (if a Host header is
                        present) a loopback Host. Two independent signals, never one.
    """
    h = {k.lower(): v for k, v in headers.items()}
    # Presence of the header KEY (any value, even empty) is a forwarding signal — an empty
    # X-Forwarded-For must not read as "no proxy".
    fwd = [name for name in _FORWARD_HEADERS if name in h]
    peer_local = (peer_ip or "") in _LOOPBACK
    host_hdr = h.get("host", "")
    # A present-but-non-loopback Host disqualifies; an absent Host (malformed/old client) does
    # not relax the guard — it just leaves the loopback-peer + no-forwarding signals to decide.
    host_ok = (host_hdr == "") or (_hostname(host_hdr) in _LOOPBACK)
    remote = bool(fwd) or not peer_local or (host_hdr != "" and not host_ok)
    can_copy_fix = peer_local and not fwd and host_ok
    fhost = h.get("x-forwarded-host") or None
    why = (f"proxied (headers: {', '.join(fwd) or 'none'}; host {host_hdr or '-'}; peer {peer_ip})"
           if remote else f"direct loopback ({peer_ip})")
    return {"remote": remote, "host": fhost, "can_copy_fix": can_copy_fix, "why": why}


def _tailnet_host_base(forwarded_host: str | None) -> str | None:
    """`4090.tailXXXX.ts.net:9123` → `4090.tailXXXX.ts.net` (drop the port the phone is ON, so
    sibling doors can be built for other ports). Returns None for a missing/malformed host —
    we never build a door from an unvalidated host."""
    if not forwarded_host or not _HOST_RE.match(forwarded_host):
        return None
    return _hostname(forwarded_host)


def door_for(svc: dict, origin: dict) -> dict:
    """Classify a service into a door the client renders verbatim — never a dead link.

    state ∈ {open, monitor-only, desktop-only}
      • open         — a real destination on THIS origin (href set).
      • monitor-only — no url (it's a daemon/feed/watcher); show health, no action.
      • desktop-only — has a url but is NOT reachable on this (remote) origin (tailnet:false,
                       e.g. ComfyUI :8188 deliberately not exposed). Honest, not dead.
    """
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
        # Remote, but no trustworthy tailnet host to rewrite to — never emit a loopback link a
        # phone can't reach; degrade to desktop-only honestly.
        return {"state": "desktop-only", "href": ""}
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


def build_launch(status: dict, origin: dict) -> dict:
    """Fold the production status payload into a launch payload: per-service door + (only when
    local) the recovery command. The client renders this with zero origin logic of its own.
    One bad service degrades to one skipped row, never a blank view (mirrors build_status)."""
    out_services = []
    for s in status.get("services", []):
        try:
            door = door_for(s, origin)
            row = {
                "id": s.get("id"), "name": s.get("name"), "group": s.get("group"),
                "desc": s.get("desc", ""), "status": s.get("status"), "state": s.get("state"),
                "kind": s.get("kind", "daemon"), "reach": s.get("reach", ""),
                "scope": s.get("scope", "user"),
                "door": door,
            }
            # Recovery command ONLY on a provably-local origin AND only for an attention row.
            if origin["can_copy_fix"] and sp and sp._is_attention(s) and s.get("unit"):
                row["fix"] = fix_command(s)
            out_services.append(row)
        except Exception as e:  # one malformed row never blanks the launcher
            print(f"atrium: bad service row {s.get('id')!r}: {e}", flush=True)
    return {
        "groups": status.get("groups", []),
        "services": out_services,
        "summary": status.get("summary", {}),
        "generated_at": status.get("generated_at"),
        "origin": {
            "remote": origin["remote"],
            "can_copy_fix": origin["can_copy_fix"],
            "host": origin.get("host"),
        },
    }


# ── status snapshot with a short TTL ──────────────────────────────────────────────────────
# build_status() shells out to systemctl + does reachability probes; under a wedged unit it can
# take seconds. Memoise behind a lock so concurrent /launch.json polls share ONE refresh and a
# slow probe can't fan out into N stuck request threads. (This is the pattern the production
# fold-in must adopt; ADR-0031 resource-safety finding.)
_CACHE_TTL = 1.5
_cache = {"t": 0.0, "v": None}
_cache_lock = threading.Lock()


def cached_status() -> dict:
    now = time.monotonic()
    with _cache_lock:
        if _cache["v"] is None or (now - _cache["t"]) > _CACHE_TTL:
            _cache["v"] = sp.build_status()
            _cache["t"] = now
        return _cache["v"]


# ── HTTP plumbing ────────────────────────────────────────────────────────────────────────

_STATIC = {
    "/": ("atrium.html", "text/html; charset=utf-8"),
    "/atrium": ("atrium.html", "text/html; charset=utf-8"),
    "/index.html": ("atrium.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/contrast_probe.html": ("contrast_probe.html", "text/html; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _origin(self) -> dict:
        peer = self.client_address[0] if self.client_address else ""
        return classify_origin(peer, dict(self.headers.items()))

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in _STATIC:
            fname, ctype = _STATIC[path]
            try:
                self._send(200, (HERE / fname).read_bytes(), ctype)
            except FileNotFoundError:
                self._send(404, f"{fname} missing".encode(), "text/plain")
            return
        if path.startswith("/icons/"):
            f = HERE / "icons" / Path(path).name      # Path.name flattens any ../ traversal
            if f.parent == (HERE / "icons") and f.exists():
                self._send(200, f.read_bytes(), "image/png")
            else:
                self._send(404, b"no icon", "text/plain")
            return
        if path == "/launch.json":
            origin = self._origin()
            if sp is None:
                # Never leak filesystem paths / internals to a remote client.
                msg = (f"status panel import failed: {_IMPORT_ERR}" if not origin["remote"]
                       else "the box could not build the view")
                self._send(200, json.dumps({"error": msg, "services": [], "groups": []}).encode(),
                           "application/json")
                return
            try:
                payload = build_launch(cached_status(), origin)
                self._send(200, json.dumps(payload).encode(), "application/json")
            except Exception as e:
                msg = str(e) if not origin["remote"] else "the box could not build the view"
                self._send(200, json.dumps({"error": msg, "services": [], "groups": []}).encode(),
                           "application/json")
            return
        self._send(404, b"not found", "text/plain")


def main():
    # Security floor: this view exposes a (local-only) shell one-liner and your service map. It
    # must stay loopback-bound; `tailscale serve` is the ONLY sanctioned exposure path. Refuse a
    # non-loopback bind unless explicitly opted in, so an env typo can't surface it on the LAN.
    if _hostname(HOST) not in _LOOPBACK and os.environ.get("ATRIUM_ALLOW_NONLOOPBACK") != "1":
        print(f"refusing to bind non-loopback host {HOST!r}; expose via `tailscale serve`, or set "
              f"ATRIUM_ALLOW_NONLOOPBACK=1 to override.", file=sys.stderr)
        sys.exit(2)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Atrium spike → http://{HOST}:{PORT}  (catalog: {PANEL_DIR/'services.json'})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

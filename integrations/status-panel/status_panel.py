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
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG_PATH = HERE / "services.json"
PANEL_PATH = HERE / "panel.html"
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
            attn = {s["id"]: s for s in build_status()["services"] if _is_attention(s)}
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

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                self._send(200, PANEL_PATH.read_bytes(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(500, b"panel.html missing", "text/plain")
        elif path == "/status.json":
            try:
                body = json.dumps(build_status()).encode()
                self._send(200, body, "application/json")
            except Exception as e:  # never 500 the whole panel over one bad probe
                self._send(200, json.dumps({"error": str(e)}).encode(), "application/json")
        elif path == "/why":
            from urllib.parse import parse_qs, urlsplit
            sid = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
            self._send(200, json.dumps(why(sid)).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")


def main():
    threading.Thread(target=_notify_loop, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"AgentOS status panel → http://{HOST}:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

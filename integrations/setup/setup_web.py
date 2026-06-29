#!/usr/bin/env python3
"""AgentOS setup wizard — the localhost-only web surface for onboarding (ADR-0044).

A calm browser wizard over the setup.py engine: see what's already here, pick what you want to
make (text / image / video, SFW or an explicit Mature lane), fetch only the gaps with live
progress, paste a token if a lane needs one, and hand off to Lucid to make your first one.

SECURITY (ADR-0044): this surface captures credentials and can browse Mature models, so it is
**127.0.0.1 ONLY** and is NEVER fronted by `tailscale serve` (its port 9125 is deliberately
absent from agentosd-remote.sh PORTS). A non-loopback bind is refused unless explicitly opted in.
Mutating routes require an anti-CSRF token + a same-origin check; the fetch worker is a plain
subprocess of setup.py (this tool is not sandboxed like the status panel — it IS the installer).

stdlib-only. Run:  python3 setup_web.py   (or ./install.sh --onboard --web)
"""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as _urlerr, request as _urlreq
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import setup  # noqa: E402
import policy  # noqa: E402  — ADR-0049 family/safety gate (shared with setup.py)
import agent_targets  # noqa: E402  — ADR-0049 Phase 2 Hermes adapter (propose() is read-only/dry-run)

WIZARD = HERE / "wizard.html"
ASSETS = HERE / "assets"                         # preview thumbnails for the Desktop section (/img/<name>)
_IMG_RE = re.compile(r"^[A-Za-z0-9_-]+\.(webp|png|svg|jpg)$")
_IMG_CT = {"webp": "image/webp", "png": "image/png", "svg": "image/svg+xml", "jpg": "image/jpeg"}
HOST = os.environ.get("AGENTOS_SETUP_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGENTOS_SETUP_PORT", "9125"))
TOKEN = secrets.token_hex(16)                  # anti-CSRF; rotates per process
_LOOPBACK = {"127.0.0.1", "::1", "localhost", ""}

_jobs: dict[str, dict] = {}                     # job_id → {bundle, mature, proc, log, started, ...}
_jobs_lock = threading.Lock()


def _runtime_dir() -> Path:
    rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    d = Path(rt) / "agentos-setup"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


# ── state the UI renders ─────────────────────────────────────────────────────────────────────
def build_state(reg: dict | None = None) -> dict:
    reg = reg or setup.load_registry()
    setup._invalidate_ollama_cache()            # always reflect a just-finished pull
    hw = setup.detect_hardware()
    out_bundles = []
    for b in setup.bundles(reg):                # registry is pre-sorted text → image → video
        is_mature = b.get("rating") == "mature"
        plan = setup.plan_bundle(reg, b, include_mature=is_mature, hw=hw)
        present = sum(1 for r in plan["rows"] if r["state"] == "have")
        out_bundles.append({
            "id": b["id"], "modality": b.get("modality"), "rating": b.get("rating", "sfw"),
            "desc": b.get("desc", ""), "present": present, "total": len(plan["rows"]),
            "gap": len(plan["gap"]), "approx_gb": plan["approx_gb"],
            "needs_auth": plan["needs_auth"], "manual": sum(1 for a in plan["gap"] if a.get("via") == "manual"),
            "order": b.get("order", 9), "needs_comfyui": b.get("needs_comfyui", False),
            "why": b.get("why", ""), "fit": setup.bundle_fit(reg, b, hw),
            # the heaviest single-model footprint — the honest "peak" for the fit bar (GB held at once),
            # reflecting the GPU-aware downselect (so a small card sees its fitting set, not the full bundle)
            "peak_gb": setup.bundle_peak_gb(reg, b, hw),
            # hero-tier models deferred because they don't fit this GPU (surfaced as an upgrade, not pulled)
            "deferred": plan.get("deferred", []),
        })
    creds = {svc: bool(setup.keyring_get(svc)) for svc in ("huggingface", "civitai")}
    found_gb = missing_gb = 0.0                  # the reuse ledger: what's already here vs the gap
    for m in setup.models(reg):
        if m.get("modality") == "selector":
            continue
        sz = float(m.get("size_gb", 0) or 0)
        if setup.model_status(m)["state"] == "have":
            found_gb += sz
        else:
            missing_gb += sz
    return {"bundles": out_bundles, "creds": creds, "comfyui": setup.comfyui_present(),
            "hardware": hw, "found_gb": int(found_gb), "missing_gb": round(missing_gb, 1),
            "stored_count": len(setup.read_manifest().get("fetched", [])),
            "lucid_url": "http://127.0.0.1:8765", "generated_at": time.time()}


# ── fetch jobs (a plain subprocess of the engine; progress = models present / total) ──────────
def start_fetch(reg: dict, bundle_id: str, mature: bool, spawn=subprocess.Popen) -> tuple[dict | None, str]:
    b = setup.find_bundle(reg, bundle_id)
    if not b:
        return None, "unknown bundle"
    if (mature or b.get("rating") == "mature") and not mature:
        return None, "mature affirmation required"
    with _jobs_lock:
        if any(j["bundle"] == bundle_id and j["proc"] and j["proc"].poll() is None for j in _jobs.values()):
            return None, "already fetching this bundle"
    jid = secrets.token_hex(8)
    log = _runtime_dir() / f"fetch-{jid}.log"
    argv = ["python3", str(HERE / "setup.py"), "fetch", bundle_id]
    if mature or b.get("rating") == "mature":
        argv += ["--mature", "--yes"]
    else:
        argv += ["--yes"]
    fh = open(log, "w")
    proc = spawn(argv, stdout=fh, stderr=subprocess.STDOUT)
    job = {"id": jid, "kind": "fetch", "label": bundle_id, "bundle": bundle_id,
           "mature": bool(mature), "proc": proc, "log": str(log), "started": time.time()}
    with _jobs_lock:
        _jobs[jid] = job
    return job, ""


def _spawn_simple(kind: str, argv: list[str], label: str, spawn=subprocess.Popen) -> dict:
    """A one-shot job (ComfyUI install / research) — spawn setup.py, log to the runtime dir."""
    with _jobs_lock:
        if any(j["kind"] == kind and j["proc"] and j["proc"].poll() is None for j in _jobs.values()):
            return None
    jid = secrets.token_hex(8)
    log = _runtime_dir() / f"{kind}-{jid}.log"
    fh = open(log, "w")
    proc = spawn(argv, stdout=fh, stderr=subprocess.STDOUT)
    job = {"id": jid, "kind": kind, "label": label, "proc": proc, "log": str(log), "started": time.time()}
    with _jobs_lock:
        _jobs[jid] = job
    return job


def start_comfyui(spawn=subprocess.Popen) -> dict | None:
    return _spawn_simple("comfyui", ["python3", str(HERE / "setup.py"), "comfyui", "--yes"],
                         "ComfyUI runtime", spawn=spawn)


def start_research(modality: str, spawn=subprocess.Popen) -> dict | None:
    modality = modality if modality in ("text", "image", "video") else "video"
    return _spawn_simple("research", ["python3", str(HERE / "setup.py"), "research", modality],
                         f"latest {modality} models", spawn=spawn)


def start_research_models(modality: str, spawn=subprocess.Popen) -> dict | None:
    """ADR-0049: STRUCTURED research — spawn `setup.py research-json` writing a candidates artifact the
    job then parses (NOT a screen-scraped log tail; the council's must-fix). One at a time."""
    modality = modality if modality in ("text", "image", "video") else "text"
    with _jobs_lock:
        if any(j["kind"] == "research-json" and j["proc"] and j["proc"].poll() is None for j in _jobs.values()):
            return None
    jid = secrets.token_hex(8)
    art = _runtime_dir() / f"candidates-{jid}.json"
    log = _runtime_dir() / f"research-json-{jid}.log"
    fh = open(log, "w")
    argv = ["python3", str(HERE / "setup.py"), "research-json", modality, "--out", str(art)]
    proc = spawn(argv, stdout=fh, stderr=subprocess.STDOUT)
    job = {"id": jid, "kind": "research-json", "label": f"latest {modality} models", "proc": proc,
           "log": str(log), "artifact": str(art), "started": time.time()}
    with _jobs_lock:
        _jobs[jid] = job
    return job


def set_policy(body: dict) -> tuple[dict | None, str]:
    """Apply a policy change from the wizard. Sanitized by policy.normalize_policy on save. Enabling
    allow-any opens the uncensored surface, so it requires a one-time 18+ affirmation (D5) — persisted
    as mature_affirmed_at; once set it is not re-prompted. Returns (new_policy|None, error_code)."""
    cur = policy.load_policy()
    allow_any = bool(body.get("allow_any_ollama"))
    new = {
        "allow_any_ollama": allow_any,
        "family_allow": body.get("family_allow") if isinstance(body.get("family_allow"), list) else cur["family_allow"],
        "family_block": body.get("family_block") if isinstance(body.get("family_block"), list) else cur["family_block"],
        "mature_affirmed_at": cur.get("mature_affirmed_at"),
    }
    if allow_any and not new["mature_affirmed_at"]:
        if body.get("affirm_mature"):
            new["mature_affirmed_at"] = time.time()
        else:
            return None, "affirm-required"
    if not policy.save_policy(new):
        return None, "save-failed"
    return policy.load_policy(), ""


def _toast(title: str, body: str) -> None:
    """One calm ambient ping when a long unattended job finishes (T5). Default-on, disable with
    AGENTOS_SETUP_NOTIFY=0; no-ops without notify-send."""
    import shutil as _sh
    if os.environ.get("AGENTOS_SETUP_NOTIFY", "1") == "0" or not _sh.which("notify-send"):
        return
    try:
        subprocess.run(["notify-send", "-a", "AgentOS setup", "-i", "dialog-information", title, body],
                       timeout=5, check=False)
    except Exception:
        pass


def job_view(reg: dict, job: dict) -> dict:
    proc = job.get("proc")
    rc = proc.poll() if proc else None
    kind = job.get("kind", "fetch")
    if rc is not None and not job.get("notified"):       # fire once, on the terminal transition
        job["notified"] = True
        label = job.get("label", job.get("bundle", "setup"))
        verb = "ready" if rc == 0 else "didn't finish"
        _toast(f"{label} — {verb}",
               "Open the setup page." if rc == 0 else "See the setup page for details.")
    present = total = 0
    if kind == "fetch":
        b = setup.find_bundle(reg, job.get("bundle", ""))
        if b:
            plan = setup.plan_bundle(reg, b, include_mature=(b.get("rating") == "mature"),
                                     hw=setup.detect_hardware())
            present = sum(1 for r in plan["rows"] if r["state"] == "have")
            total = len(plan["rows"])
    elif kind == "comfyui":
        present, total = (1, 1) if setup.comfyui_present() else (0, 1)
    elif kind == "research-json":
        present, total = (1, 1) if rc == 0 else (0, 1)
    status = "running" if rc is None else ("done" if rc == 0 else "failed")
    n = 30 if kind == "research" else 6              # research's result IS its output — show more
    tail = ""
    try:
        tail = "\n".join(Path(job["log"]).read_text(errors="replace").splitlines()[-n:])
    except Exception:
        pass
    out = {"id": job["id"], "kind": kind, "label": job.get("label", job.get("bundle", "")),
           "status": status, "present": present, "total": total, "tail": tail}
    if kind == "research-json" and rc is not None:   # parse the JSON artifact, re-validate at READ time
        try:
            res = json.loads(Path(job["artifact"]).read_text())
            if res.get("ok"):
                res["candidates"] = setup.validate_candidates(reg, res.get("candidates", []))
            out["result"] = res
        except Exception:
            out["result"] = {"ok": False, "error": "no candidates produced — is `claude` authenticated?"}
    return out


def jobs_view(reg: dict) -> list[dict]:
    setup._invalidate_ollama_cache()
    with _jobs_lock:
        js = list(_jobs.values())
    return [job_view(reg, j) for j in js]


# ── desktop section: proxy the ADR-0043 adopt engine on :9123 (the grown front door) ──────────
# The :9123 status panel is the SINGLE catalog + ledger authority (adopt.list_components). The
# wizard NEVER parses components.conf itself and NEVER shells the driver — it proxies the panel's
# already-hardened /adopt engine server-to-server over loopback. The browser only ever talks to
# :9125; this hop is :9125 → :9123 with a fresh request (no client headers reflected outward).
_PANEL_PORT = int(os.environ.get("AGENTOS_STATUS_PORT", "9123"))
_PANEL = f"http://127.0.0.1:{_PANEL_PORT}"

# Presentation metadata for the Desktop section. The panel's /components.json stays the source of
# truth for WHAT is adoptable + its live state; this only GROUPS + annotates what it returns, so the
# pipe-delimited catalog format never has to change. Rows not listed fall to "integrations".
_DESKTOP_GROUPS = {
    "keyhole": "ambient", "reactive-wallpaper": "ambient",
    "aurora-theme": "look", "aurora-panel": "look", "aurora-notifications": "look",
    "hermes-plugins": "agents", "gpu-coordinator": "agents",
}
# Rows whose apply.sh ends in a manual desktop step the proxy can't perform — surfaced AFTER a
# successful adopt so a green "adopted" never lies (ux CRITICAL).
_POST_ADOPT = {
    "keyhole": "Add the Keyhole widget to your tray: right-click the system tray → "
               "Configure System Tray → Entries.",
}


class _NoRedirect(_urlreq.HTTPRedirectHandler):
    """Never follow a redirect on a panel call (explicit loopback, no redirect-following)."""
    def redirect_request(self, *a, **k):
        return None


_OPENER = _urlreq.build_opener(_NoRedirect)


def _panel_get(path: str, timeout: float = 1.5) -> tuple[int, object, str]:
    """GET a panel JSON route over loopback. Returns (code, obj|None, why). Distinguishes a panel
    that is DOWN ('refused') from one that is merely SLOW ('timeout') so the UI stays honest."""
    req = _urlreq.Request(_PANEL + path, headers={"Host": f"127.0.0.1:{_PANEL_PORT}",
                                                  "Accept": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null"), ""
    except _urlerr.HTTPError as e:
        return e.code, None, "http"
    except _urlerr.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, ConnectionRefusedError):
            return 0, None, "refused"
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return 0, None, "timeout"
        return 0, None, "error"
    except (socket.timeout, TimeoutError):
        return 0, None, "timeout"
    except Exception:
        return 0, None, "error"


def _panel_post(path: str, body: dict, token_path: str, token_header: str,
                timeout: float = 6.0) -> tuple[int, object, str]:
    """Server-to-server POST to a panel write route: fetch its same-origin token, then POST a FRESH
    request — loopback Host, NO client/forwarding headers reflected — so classify_origin().can_copy_fix
    passes and nothing from the browser crosses the hop (must-fix #4)."""
    tcode, tobj, twhy = _panel_get(token_path, timeout=2.0)
    if tcode != 200 or not isinstance(tobj, dict) or not tobj.get("token"):
        return 0, None, twhy or "panel token unavailable"
    req = _urlreq.Request(_PANEL + path, data=json.dumps(body).encode(), method="POST",
                          headers={"Host": f"127.0.0.1:{_PANEL_PORT}",
                                   "Content-Type": "application/json",
                                   token_header: tobj["token"]})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null"), ""
    except _urlerr.HTTPError as e:                    # 403/409 carry a JSON {error} the panel chose
        try:
            return e.code, json.loads(e.read() or b"null"), ""
        except Exception:
            return e.code, None, "http"
    except _urlerr.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, ConnectionRefusedError):
            return 0, None, "refused"
        return 0, None, "error"
    except Exception:
        return 0, None, "error"


def _desktop_state(timeout: float = 1.5) -> dict:
    """The Desktop section's catalog + badges, folded from the panel's /components.json (the single
    authority). Advisory DISPLAY only — never drives a mutating default. Lives off the /api/state
    hot path on its own /api/desktop endpoint so a slow panel can't stall every state poll."""
    code, obj, why = _panel_get("/components.json", timeout=timeout)
    if code != 200 or not isinstance(obj, dict):
        return {"reachable": False, "why": why or "error", "enabled": True, "components": []}
    rows = []
    for c in obj.get("components", []):
        if c.get("root") != "no" or c.get("tier") not in ("desktop", "hermes"):
            continue
        row = {"id": c.get("id"), "name": c.get("name") or c.get("id"),
               "tier": c.get("tier"), "desc": c.get("desc", ""),
               "state": c.get("state"), "adoptable": bool(c.get("adoptable")),
               "removable": bool(c.get("removable")),
               "group": _DESKTOP_GROUPS.get(c.get("id"), "integrations")}
        if c.get("id") in _POST_ADOPT:
            row["post_adopt"] = _POST_ADOPT[c["id"]]
        rows.append(row)
    return {"reachable": True, "enabled": bool(obj.get("enabled", True)), "components": rows}


# ── HTTP ──────────────────────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):                  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _is_local(self) -> bool:
        peer = (self.client_address[0] if self.client_address else "")
        host = self.headers.get("Host", "")
        hostname = host.rsplit(":", 1)[0] if host else ""
        # loopback peer AND (no/loopback Host) AND no forwarding headers — same rule as the panels
        fwd = any(h.lower().startswith("x-forwarded") for h in self.headers.keys())
        return peer in _LOOPBACK and hostname in _LOOPBACK and not fwd

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            try:
                self._send(200, WIZARD.read_bytes(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, b"wizard.html missing", "text/plain")
        elif path == "/api/state":
            self._json(200, build_state())
        elif path == "/api/token":
            self._json(200, {"token": TOKEN})
        elif path == "/api/jobs":
            self._json(200, {"jobs": jobs_view(setup.load_registry())})
        elif path == "/api/suggest_prompt":
            from urllib.parse import parse_qs
            mod = parse_qs(urlsplit(self.path).query).get("modality", ["image"])[0]
            self._json(200, {"prompt": setup.suggest_opening_prompt(mod if mod in ("image", "video") else "image")})
        elif path == "/api/stored":
            self._json(200, {"fetched": setup.read_manifest().get("fetched", [])})
        elif path == "/api/policy":                  # ADR-0049: policy + families + live Hermes default + ledger
            if not self._is_local():                 # the family policy is a sensitive taste profile (0600) —
                self._json(403, {"error": "this machine only"})   # don't disclose it to a non-loopback caller
                return
            self._json(200, {"policy": policy.load_policy(),
                             "known_families": list(policy.KNOWN_FAMILIES),
                             "precedence": "safety-denylist > family_block > allow_any > family_allow",
                             "hermes_default": setup.hermes_current_default(),
                             "adopted": setup.manifest_actions()})
        elif path == "/api/desktop":
            self._json(200, _desktop_state())
        elif path == "/api/component_jobs":
            code, obj, _why = _panel_get("/adopt.json", timeout=1.5)
            self._json(200, obj if (code == 200 and isinstance(obj, dict)) else {"jobs": []})
        elif path.startswith("/img/"):
            self._serve_asset(path[len("/img/"):])
        else:
            self._send(404, b"not found", "text/plain")

    def _serve_asset(self, name: str):
        """Serve a Desktop-section preview thumbnail from ./assets, allowlisted by name (no traversal)."""
        if not _IMG_RE.match(name):
            self._send(404, b"not found", "text/plain")
            return
        try:
            body = (ASSETS / name).read_bytes()
        except OSError:
            self._send(404, b"not found", "text/plain")
            return
        ext = name.rsplit(".", 1)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", _IMG_CT.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400")    # previews are static
        self.end_headers()
        self.wfile.write(body)

    def _guard(self) -> bool:
        if not self._is_local():
            self._json(403, {"error": "setup is available on this machine only"})
            return False
        tok = self.headers.get("X-Setup-Token", "")
        if not tok or not secrets.compare_digest(tok, TOKEN):
            self._json(403, {"error": "bad or missing token"})
            return False
        if self.headers.get("Sec-Fetch-Site", "") == "cross-site":
            self._json(403, {"error": "cross-site refused"})
            return False
        return True

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n <= 0 or n > 8192:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/fetch":
            if not self._guard():
                return
            body = self._body()
            reg = setup.load_registry()
            job, err = start_fetch(reg, str(body.get("bundle", "")), bool(body.get("mature")))
            if not job:
                self._json(409, {"error": err})
                return
            self._json(202, {"id": job["id"], "status": "started"})
        elif path == "/api/creds":
            if not self._guard():
                return
            body = self._body()
            svc = "huggingface" if body.get("svc") in ("hf", "huggingface") else "civitai"
            token = str(body.get("token", "")).strip()
            if not token:
                self._json(400, {"error": "no token"})
                return
            ok = setup.keyring_set(svc, token)        # token to keyring via stdin; never logged
            self._json(200 if ok else 500, {"ok": ok, "svc": svc})
        elif path == "/api/comfyui":
            if not self._guard():
                return
            job = start_comfyui()
            self._json(202 if job else 409,
                       {"id": job["id"], "status": "started"} if job else {"error": "already installing"})
        elif path == "/api/research":
            if not self._guard():
                return
            job = start_research(str(self._body().get("modality", "video")))
            self._json(202 if job else 409,
                       {"id": job["id"], "status": "started"} if job else {"error": "already researching"})
        elif path == "/api/forget":
            if not self._guard():
                return
            svc = "huggingface" if self._body().get("svc") in ("hf", "huggingface") else "civitai"
            self._json(200, {"ok": setup.keyring_clear(svc), "svc": svc})    # ADR-0044 "Forget token"
        elif path == "/api/component":
            # Adopt/remove a desktop/agent component by PROXYING the panel's hardened /adopt engine.
            # Defense-in-depth: the id+action are re-validated here against the SAME live /components.json
            # fold (one authority, no second parser) before the hop; install.sh re-validates again.
            if not self._guard():
                return
            body = self._body()
            comp_id = str(body.get("id", ""))
            action = str(body.get("action", "adopt"))
            if action not in ("adopt", "unadopt"):
                self._json(400, {"error": "bad action"})
                return
            ds = _desktop_state()
            if not ds["reachable"]:
                self._json(503, {"error": "the status panel (:9123) isn't reachable"})
                return
            row = next((c for c in ds["components"] if c["id"] == comp_id), None)
            if not row:
                self._json(409, {"error": "not an adoptable desktop component"})
                return
            if action == "adopt" and not row.get("adoptable"):
                self._json(409, {"error": "not one-click adoptable here"})
                return
            if action == "unadopt" and not row.get("removable"):
                self._json(409, {"error": "this component is install-only here"})
                return
            code, obj, why = _panel_post("/adopt", {"id": comp_id, "action": action},
                                         "/adopt/token", "X-Adopt-Token")
            if code == 0:
                self._json(503, {"error": why or "couldn't reach the status panel"})
                return
            self._json(code, obj if obj is not None else {"error": "panel error"})
        elif path == "/api/policy":                  # ADR-0049: set the family/safety policy
            if not self._guard():
                return
            new, err = set_policy(self._body())
            if not new:
                self._json(400 if err == "affirm-required" else 500, {"error": err})
                return
            self._json(200, {"ok": True, "policy": new})
        elif path == "/api/research_models":         # ADR-0049: structured research → candidates artifact
            if not self._guard():
                return
            job = start_research_models(str(self._body().get("modality", "text")))
            self._json(202 if job else 409,
                       {"id": job["id"], "status": "started"} if job else {"error": "already researching"})
        elif path == "/api/adopt":                   # ADR-0049: adopt a present, permitted ref (re-validated)
            if not self._guard():
                return
            body = self._body()
            reg = setup.load_registry()
            res = setup.adopt_candidate(reg, str(body.get("ref", "")).strip(),
                                        name=(str(body.get("name") or "") or None),
                                        modality=str(body.get("modality") or "text"),
                                        affirmed=bool(body.get("affirmed")))
            self._json(200 if res.get("ok") else 409, res)
        elif path == "/api/revert":                  # ADR-0049: undo an adopt by id
            if not self._guard():
                return
            res = setup.revert_action(str(self._body().get("id", "")).strip())
            self._json(200 if res.get("ok") else 409, res)
        elif path == "/api/hermes_propose":          # ADR-0049 Phase 2 (DRY RUN): preview a default change
            if not self._guard():                    # writes NOTHING — diff + a static fit estimate only.
                return
            ref = str(self._body().get("ref", "")).strip()
            if not ref:
                self._json(400, {"error": "no ref"})
                return
            ok, why = setup.permits_ref(setup.load_registry(), ref)   # denied/blocked refs are flagged, not previewed as live
            a = agent_targets.HermesAdapter()
            prop = a.propose(ref)
            prop["fit"] = a.estimate_fit(ref)
            prop["permitted"] = ok
            prop["permit_reason"] = why
            self._json(200, prop)
        else:
            self._send(404, b"not found", "text/plain")


def main() -> int:
    from socket import gethostname  # noqa
    if HOST not in _LOOPBACK and os.environ.get("AGENTOS_SETUP_ALLOW_NONLOOPBACK") != "1":
        print(f"✗ refusing to bind non-loopback host {HOST!r} — the setup wizard handles credentials "
              f"and must stay on-box. Set AGENTOS_SETUP_ALLOW_NONLOOPBACK=1 only if you know why.",
              file=sys.stderr)
        return 2
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"AgentOS setup wizard → http://{HOST}:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

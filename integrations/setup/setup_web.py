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
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import setup  # noqa: E402

WIZARD = HERE / "wizard.html"
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
        plan = setup.plan_bundle(reg, b, include_mature=is_mature)
        present = sum(1 for r in plan["rows"] if r["state"] == "have")
        out_bundles.append({
            "id": b["id"], "modality": b.get("modality"), "rating": b.get("rating", "sfw"),
            "desc": b.get("desc", ""), "present": present, "total": len(plan["rows"]),
            "gap": len(plan["gap"]), "approx_gb": plan["approx_gb"],
            "needs_auth": plan["needs_auth"], "manual": sum(1 for a in plan["gap"] if a.get("via") == "manual"),
            "order": b.get("order", 9), "needs_comfyui": b.get("needs_comfyui", False),
            "why": b.get("why", ""), "fit": setup.bundle_fit(reg, b, hw),
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


def job_view(reg: dict, job: dict) -> dict:
    proc = job.get("proc")
    rc = proc.poll() if proc else None
    kind = job.get("kind", "fetch")
    present = total = 0
    if kind == "fetch":
        b = setup.find_bundle(reg, job.get("bundle", ""))
        if b:
            plan = setup.plan_bundle(reg, b, include_mature=(b.get("rating") == "mature"))
            present = sum(1 for r in plan["rows"] if r["state"] == "have")
            total = len(plan["rows"])
    elif kind == "comfyui":
        present, total = (1, 1) if setup.comfyui_present() else (0, 1)
    status = "running" if rc is None else ("done" if rc == 0 else "failed")
    n = 30 if kind == "research" else 6              # research's result IS its output — show more
    tail = ""
    try:
        tail = "\n".join(Path(job["log"]).read_text(errors="replace").splitlines()[-n:])
    except Exception:
        pass
    return {"id": job["id"], "kind": kind, "label": job.get("label", job.get("bundle", "")),
            "status": status, "present": present, "total": total, "tail": tail}


def jobs_view(reg: dict) -> list[dict]:
    setup._invalidate_ollama_cache()
    with _jobs_lock:
        js = list(_jobs.values())
    return [job_view(reg, j) for j in js]


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
        else:
            self._send(404, b"not found", "text/plain")

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

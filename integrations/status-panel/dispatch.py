#!/usr/bin/env python3
"""AgentOS status-panel dispatch core (ADR-0039).

From a down service the panel can dispatch an agent to *investigate, fix, and log*. This
module is the part that runs INSIDE the hardened, loopback-bound panel: it never mutates
system state and never leaves the sandbox. It only —

  • mints + checks the anti-CSRF token,
  • validates a dispatch against the trusted catalog + live status + rate limits/kill-switches,
  • reads/writes the dispatch ledger (in $XDG_RUNTIME_DIR, writable via the unit's
    RuntimeDirectory= even under ProtectSystem=strict), atomically + flock'd, and
  • launches the worker (dispatch_run.py) as a transient `systemd-run --user` unit, which runs
    OUTSIDE the panel's sandbox and does the actual first-aid / model investigation.

The safety spine lives here as data: `can_auto_recover()` is the closed, OPT-IN allowlist that
the worker's deterministic first-aid is bound by — a catalog entry must carry `auto_recover:true`
AND be user-scope AND not on the never-auto denylist. Everything else (system-scope, GPU/lease
units, anything un-flagged) escalates to a human-gated proposal; the model only ever proposes.

stdlib-only, to match status_panel.py (no venv/deps)."""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import status_panel as sp

HERE = Path(__file__).resolve().parent
WORKER = HERE / "dispatch_run.py"

# Per-process anti-CSRF token (ADR-0039). Served same-origin via GET /dispatch/token and required
# as X-Dispatch-Token on POST /dispatch. A cross-origin page can POST but cannot read this (SOP on
# the token response) → CSRF-safe; the route also rejects Sec-Fetch-Site:cross-site. Rotates on restart.
TOKEN = secrets.token_hex(16)

VALID_TARGETS = ("claude", "hermes")

# The cloud model the diagnosis contract is validated against — pinned, not inherited, so a config
# flip can't silently change JSON-emission reliability or the latency budget (ai-generation review).
CLAUDE_MODEL = os.environ.get("AGENTOS_DISPATCH_CLAUDE_MODEL", "claude-sonnet-4-6")

# ── the auto-fix allowlist (the bounded-auto-fix gate) ──────────────────────────────────────
# Units the worker must NEVER auto-restart even if flagged — always escalate. The panel's own unit
# (a restart drops the page mid-incident) and the GPU lease daemon (a restart disrupts live leases).
NEVER_AUTO_UNITS = frozenset({
    "agentos-status-panel.service",
    "agentos-lease.service",
})

# ── rate limits + crashloop brake (abuse + cost + resource guard) ───────────────────────────
COOLDOWN_S = 90.0           # don't re-dispatch the SAME service within this window of a finish
MAX_ACTIVE = 3              # cap concurrent in-flight dispatches across all services
FIRST_AID_WINDOW_S = 1800.0  # rolling window for the crashloop brake (30 min)
FIRST_AID_MAX = 2           # after this many first-aid restarts of one svc in the window, stop
                            # auto-restarting it (escalate instead) — `reset-failed` defeats
                            # systemd's StartLimit, so WE must not re-arm a crashloop forever.
ACTIVE_STATUSES = ("queued", "triaging", "first-aid", "investigating")
# Terminal states are all distinct + honest (no silent success-as-failure):
#   recovered     — first-aid restart brought it back
#   needs-approval — the agent proposed a fix; awaiting the human
#   diagnosed     — investigated, no safe automatic fix found (a SUCCESS, not a failure)
#   handed-off    — handed to a Hermes run (Phase 2)
#   blocked       — a dependency isn't available (e.g. Hermes write-API off) — honest "not yet"
#   failed        — the dispatch itself broke (worker/model error, gone catalog, timed out)
TERMINAL_STATUSES = ("recovered", "needs-approval", "diagnosed", "handed-off", "blocked", "failed")
WORKER_TIMEOUT_S = 300      # the transient unit's hard wall-clock cap (RuntimeMaxSec); also the
                            # reaper threshold for a stuck active incident (+ a slack margin).
STALE_ACTIVE_S = WORKER_TIMEOUT_S + 30


def dispatch_enabled() -> bool:
    return os.environ.get("AGENTOS_DISPATCH", "1") != "0"


def cloud_enabled() -> bool:
    """The cloud (Claude) target sends evidence off-box; it can be hard-disabled (privacy)."""
    return os.environ.get("AGENTOS_DISPATCH_CLOUD", "1") != "0"


def can_auto_recover(svc: dict) -> tuple[bool, str]:
    """The closed, OPT-IN allowlist for the worker's deterministic first-aid. Returns (ok, reason).
    A False means the worker must ESCALATE (model proposes), never auto-apply. Auto-recovery is
    OFF by default — a catalog entry must explicitly set `auto_recover:true` (so GPU/lease/
    compositor units, which are dangerous to bounce, are never auto-restarted unless opted in)."""
    if not svc.get("auto_recover"):
        return False, "service is not opted in to auto-recovery (auto_recover)"
    if svc.get("scope", "user") != "user":
        return False, "system-scope unit needs sudo/polkit (NoNewPrivileges blocks it)"
    unit = svc.get("unit", "")
    if not unit:
        return False, "no unit to restart"
    if unit in NEVER_AUTO_UNITS:
        return False, "unit is never auto-restarted (self / lease daemon)"
    return True, "opted-in user-scope catalog unit"


def recover_command(svc: dict) -> str:
    """The reversible recovery one-liner — identical to the panel's copy-fix (fix_command)."""
    return sp.fix_command(svc)


# ── redaction (privacy: anything bound for the cloud or a durable file is scrubbed first) ────
_REDACTIONS = [
    (re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd)\b(\s*[=:]\s*)\S+"), r"\1\2[redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}\b"), "[jwt]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[aws-key]"),
    (re.compile(r"\bsk-[A-Za-z0-9\-]{16,}\b"), "[key]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "[hf-token]"),     # HuggingFace token (ADR-0044)
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[email]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
    (re.compile(r"/home/[^/\s:\"']+"), "~"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "[hex]"),
]


def redact(text: str) -> str:
    """Best-effort scrub of secrets/PII before evidence leaves the box (cloud) or lands in a
    durable log. Conservative + lossy by design — privacy beats a slightly-richer diagnosis."""
    out = text or ""
    for pat, repl in _REDACTIONS:
        out = pat.sub(repl, out)
    return out


# ── the ledger ($XDG_RUNTIME_DIR/agentos-dispatch/ledger.json) ──────────────────────────────
def _ledger_dir() -> Path:
    rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    d = Path(rt) / "agentos-dispatch"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ledger_path() -> Path:
    return _ledger_dir() / "ledger.json"


def log_dir() -> Path:
    """Durable per-incident transcripts. Under $HOME — written ONLY by the out-of-sandbox worker."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "agentos" / "dispatch"


def _read_locked(fh) -> dict:
    fh.seek(0)
    raw = fh.read()
    if not raw.strip():
        return {"v": 1, "incidents": {}}
    try:
        d = json.loads(raw)
        if not isinstance(d, dict) or "incidents" not in d:
            return {"v": 1, "incidents": {}}
        return d
    except Exception:
        return {"v": 1, "incidents": {}}


def read_ledger() -> dict:
    """Read-only snapshot (no lock for a pure read; a torn write degrades to empty)."""
    try:
        return json.loads(ledger_path().read_text())
    except Exception:
        return {"v": 1, "incidents": {}}


def _mutate_ledger(fn):
    """Read-modify-write under an exclusive flock, then atomic-replace. `fn(data)` mutates `data`
    in place; its return value is returned to the caller. Cross-process safe (panel + worker)."""
    p = ledger_path()
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with os.fdopen(fd, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            data = _read_locked(fh)
            ret = fn(data)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, p)
            return ret
    except Exception:
        return None


def _now() -> float:
    return time.time()


def _reap(data: dict) -> None:
    """A worker SIGKILLed by RuntimeMaxSec never runs its `finally`, so its incident would sit in
    an active status forever — blocking re-dispatch + showing an eternal spinner. Reap any active
    incident whose last update is older than the worker's hard cap into an honest `failed`."""
    now = _now()
    for e in data.get("incidents", {}).values():
        if e.get("status") in ACTIVE_STATUSES and (now - e.get("updated", 0)) > STALE_ACTIVE_S:
            e["status"] = "failed"
            e["outcome"] = "the dispatch worker stopped responding"
            e["updated"] = now


def prune(data: dict, keep_terminal: int = 40, max_age_s: float = 6 * 3600) -> None:
    inc = data.get("incidents", {})
    term = [(i, e) for i, e in inc.items() if e.get("status") in TERMINAL_STATUSES]
    term.sort(key=lambda kv: kv[1].get("updated", 0), reverse=True)
    now = _now()
    for n, (iid, e) in enumerate(term):
        if n >= keep_terminal or (now - e.get("updated", 0)) > max_age_s:
            inc.pop(iid, None)


def _new_incident(svc: dict, target: str, skip_first_aid: bool) -> dict:
    ok_auto = can_auto_recover(svc)[0] and not skip_first_aid
    now = _now()
    return {
        "id": secrets.token_hex(8),
        "svc": svc.get("id"), "name": svc.get("name", svc.get("id")),
        "unit": svc.get("unit", ""), "scope": svc.get("scope", "user"),
        "target": target, "status": "queued", "method": None,
        "auto_eligible": ok_auto, "skip_first_aid": skip_first_aid,
        "first_aid_tried": False, "outcome": "", "diagnosis": "", "proposal": "",
        "confidence": "", "model": "", "created": now, "updated": now,
    }


def validate(svc_id: str, target: str, status_snapshot: dict) -> tuple[dict | None, str]:
    """Non-racy admission: kill-switches, target, cloud-enabled, and that the svc is a real catalog
    row CURRENTLY in attention. The ledger-derived guards (dedupe/cap/cooldown/crashloop) are
    enforced atomically in try_create_incident, not here, to avoid a check-then-write race."""
    if not dispatch_enabled():
        return None, "dispatch is disabled on this box (AGENTOS_DISPATCH=0)"
    if target not in VALID_TARGETS:
        return None, "unknown target"
    if target == "claude" and not cloud_enabled():
        return None, "cloud dispatch is disabled (AGENTOS_DISPATCH_CLOUD=0) — use Hermes (local)"
    svc = next((s for s in status_snapshot.get("services", []) if s.get("id") == svc_id), None)
    if not svc:
        return None, "unknown service"
    if not sp._is_attention(svc):
        return None, "service is not in an attention state"
    return svc, ""


def try_create_incident(svc: dict, target: str) -> tuple[dict | None, str]:
    """Atomically (under the ledger flock) re-check the rate limits + crashloop brake AND insert
    the incident, so two near-simultaneous POSTs can't both pass dedupe/cap. Returns (entry, "")
    or (None, reason)."""
    result: dict = {}

    def _txn(data):
        _reap(data)
        prune(data)
        inc = data.setdefault("incidents", {})
        svc_id = svc.get("id")
        active = [e for e in inc.values() if e.get("status") in ACTIVE_STATUSES]
        if any(e.get("svc") == svc_id for e in active):
            result["reason"] = "already investigating this service"
            return
        if len(active) >= MAX_ACTIVE:
            result["reason"] = "too many dispatches in flight — try again shortly"
            return
        term = [e for e in inc.values() if e.get("svc") == svc_id and e.get("status") in TERMINAL_STATUSES]
        if term and (_now() - max(e.get("updated", 0) for e in term)) < COOLDOWN_S:
            result["reason"] = "just tried — give it a moment before dispatching again"
            return
        # Crashloop brake: if this svc has already had FIRST_AID_MAX first-aid restarts in the
        # window, stop auto-restarting it — dispatch still runs, but escalates straight to a human.
        recent_fa = sum(1 for e in inc.values() if e.get("svc") == svc_id
                        and e.get("first_aid_tried") and (_now() - e.get("updated", 0)) < FIRST_AID_WINDOW_S)
        entry = _new_incident(svc, target, skip_first_aid=recent_fa >= FIRST_AID_MAX)
        inc[entry["id"]] = entry
        result["entry"] = entry

    _mutate_ledger(_txn)
    entry = result.get("entry")
    if entry:
        return entry, ""
    return None, result.get("reason", "could not record the dispatch")


def update_incident(iid: str, **fields) -> dict | None:
    def _patch(data):
        e = data.get("incidents", {}).get(iid)
        if not e:
            return None
        e.update(fields)
        e["updated"] = _now()
        return dict(e)

    return _mutate_ledger(_patch)


def claim_incident(iid: str) -> bool:
    """Atomic test-and-set: only an unclaimed `queued` incident transitions to `triaging`, and only
    one caller can win. Makes the worker's at-most-once property a property of THIS code, not of
    systemd unit-name uniqueness. Returns True if this caller claimed it."""
    res: dict = {}

    def _txn(data):
        e = data.get("incidents", {}).get(iid)
        if not e or e.get("status") != "queued":
            res["ok"] = False
            return
        e["status"] = "triaging"
        e["updated"] = _now()
        res["ok"] = True

    _mutate_ledger(_txn)
    return bool(res.get("ok"))


def public_incident(e: dict, local: bool) -> dict:
    """The client-facing subset. A remote (phone) origin never gets the shell one-liner or the
    durable-log path; it gets the human summary. A stuck active incident is shown as failed."""
    status, outcome = e.get("status"), e.get("outcome", "")
    if status in ACTIVE_STATUSES and (_now() - e.get("updated", 0)) > STALE_ACTIVE_S:
        status, outcome = "failed", "the dispatch worker stopped responding"
    out = {
        "id": e.get("id"), "svc": e.get("svc"), "target": e.get("target"),
        "status": status, "method": e.get("method"), "outcome": outcome,
        "auto_eligible": e.get("auto_eligible", False), "updated": e.get("updated"),
    }
    if e.get("confidence"):
        out["confidence"] = e["confidence"]
    if local and e.get("proposal"):           # shell one-liner — same trust rule as copy-fix
        out["proposal"] = e["proposal"]
    if e.get("diagnosis"):                     # already redacted before storage
        out["diagnosis"] = e["diagnosis"]
    return out


def build_brief(svc: dict, run=sp._run) -> dict:
    """Everything the model needs, gathered server-side (so the model needs NO tools): the service
    identity + its live unit state + a REDACTED journal tail (secrets/PII scrubbed before it can
    leave the box)."""
    j = sp.why(svc.get("id", ""), run=run)
    return {
        "id": svc.get("id"), "name": svc.get("name"), "desc": svc.get("desc", ""),
        "unit": svc.get("unit", ""), "scope": svc.get("scope", "user"),
        "status": svc.get("status"), "state": svc.get("state"), "reach": svc.get("reach", ""),
        "journal": redact(j.get("lines", j.get("error", ""))),
    }


def spawn_worker(iid: str, target: str) -> tuple[bool, str]:
    """Launch the worker for an incident as a transient `systemd-run --user` unit — OUTSIDE the
    panel's sandbox. The incident id (server-minted hex) is the only argument, so there's no
    client-controlled argv. The Hermes credential is forwarded ONLY for a Hermes dispatch (never
    co-located with the cloud/Claude worker). Honest failure: never silently run a sandboxed child."""
    if not shutil.which("systemd-run"):
        return False, "systemd-run not available — cannot launch the dispatch worker"
    home = os.path.expanduser("~")
    path = f"{home}/.local/bin:" + os.environ.get("PATH", "/usr/bin:/bin")
    cmd = [
        "systemd-run", "--user", "--collect", "--quiet",
        "--unit", f"agentos-dispatch-{iid}",
        "--description", f"AgentOS dispatch worker ({iid})",
        f"--property=RuntimeMaxSec={WORKER_TIMEOUT_S}",
        "--expand-environment=no",            # the worker takes no ${VAR} args — make that explicit
        "--setenv", f"PATH={path}",
    ]
    passthrough = ["XDG_RUNTIME_DIR", "XDG_STATE_HOME", "AGENTOS_DISPATCH_CLAUDE_MODEL",
                   "AGENTOS_DISPATCH_LOG_TTL_H"]
    if target == "hermes":
        passthrough += ["HERMES_BASE", "HERMES_API_KEY"]
    for k in passthrough:
        v = os.environ.get(k)
        if v:
            cmd += ["--setenv", f"{k}={v}"]
    cmd += ["/usr/bin/python3", str(WORKER), iid]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except Exception as e:
        return False, f"could not launch worker: {e}"
    if r.returncode != 0:
        return False, f"systemd-run failed: {(r.stderr or r.stdout or '').strip()[:200]}"
    return True, ""


_HEXID = re.compile(r"^[0-9a-f]{16}$")          # ids are minted at exactly token_hex(8) = 16 hex


def valid_incident_id(s: str) -> bool:
    return bool(_HEXID.match(s or ""))

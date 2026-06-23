#!/usr/bin/env python3
"""AgentOS adopt worker (ADR-0043) — the body of a feature adoption.

Launched by the panel as a transient `systemd-run --user` unit (OUTSIDE the panel's
ProtectHome=read-only sandbox), one per job, with a single server-minted job id.

It does the one thing the hardened panel cannot: run the registry's own
`install.sh --only <id> --yes` (adopt) or `uninstall.sh --only <id> --yes` (un-adopt),
capture the transcript, re-probe the component, and record an honest terminal state.

The component id + action come from the ledger (written by the panel from a registry-validated
request); the worker re-validates them against components.conf before running, and only ever runs
a `root: no` component — defense in depth on top of the panel's validate(). install.sh re-checks
the id too, so no wire string is ever executed. stdlib-only. Run: adopt_run.py <job_id>"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import adopt as A
import dispatch as D        # reuse redact() so the durable transcript is scrubbed like dispatch's

RUN_TIMEOUT = 840          # under the unit's RuntimeMaxSec (900) so we can mark failed, not get SIGKILLed
LOG_TTL_H = float(os.environ.get("AGENTOS_ADOPT_LOG_TTL_H", "168"))   # durable transcript TTL (7d)
LOG_KEEP = 100


def _open_log(jid: str, comp_id: str):
    d = A.log_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)          # close the brief umask-default (0755) window
    except OSError:
        pass
    _prune_logs(d)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    p = d / f"{stamp}-{comp_id}-{jid}.log"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    return os.fdopen(fd, "a", encoding="utf-8"), str(p)


def _prune_logs(d: Path) -> None:
    try:
        logs = sorted(d.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    now = time.time()
    for n, p in enumerate(logs):
        try:
            if n >= LOG_KEEP or (now - p.stat().st_mtime) > LOG_TTL_H * 3600:
                p.unlink()
        except OSError:
            pass


def _log(f, msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        f.write(line + "\n")
        f.flush()
    except Exception:
        pass
    print(line, flush=True)


def _toast(title: str, body: str) -> None:
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(["notify-send", "-a", "AgentOS", "-u", "normal",
                        "-i", "dialog-information", title, body], check=False, timeout=5)
    except Exception:
        pass


def _run_driver(action: str, comp_id: str, f) -> tuple[int, str]:
    """Run the registry's own driver for ONE component, non-interactively. The id is a trusted
    registry id (re-validated by the worker AND by install.sh's --only) — never a wire string."""
    script = A.INSTALL if action == "adopt" else A.UNINSTALL
    cmd = ["/usr/bin/env", "bash", str(script), "--only", comp_id, "--yes"]
    _log(f, f"{'adopt' if action == 'adopt' else 'un-adopt'}: {' '.join(cmd)}")
    # AGENTOS_DRIVER_RESULT=1 makes the driver emit a structured `AGENTOS-RESULT <id> ok|fail` line
    # per component, so success is a deterministic token, not a grep over free-text prose.
    env = {**os.environ, "AGENTOS_DRIVER_RESULT": "1"}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(A.INTEGRATIONS),
                           env=env, timeout=RUN_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        _log(f, f"driver timed out after {RUN_TIMEOUT}s")
        return 124, "(timed out)"
    out = (r.stdout or "") + (("\n--- stderr ---\n" + r.stderr) if r.stderr.strip() else "")
    for line in out.splitlines():
        _log(f, f"  {D.redact(line)}")        # scrub secrets/PII before the durable transcript
    _log(f, f"driver exit {r.returncode}")
    return r.returncode, out


def run(jid: str) -> None:
    if not A.claim_job(jid):                # atomic CAS: only one worker claims a queued job
        print(f"adopt worker: job {jid} not claimable (gone/terminal/already claimed)", flush=True)
        return
    led = A.read_ledger()
    entry = led.get("jobs", {}).get(jid, {})
    comp_id, action = entry.get("comp"), entry.get("action")
    f, log_path = _open_log(jid, comp_id or "unknown")
    A.update_job(jid, log=log_path)
    try:
        _log(f, f"adopt {jid}: component={comp_id} action={action}")
        # Re-validate against the registry in the worker (defense in depth): real row, root:no only.
        comp = A.find(comp_id or "")
        if not comp or action not in A.VALID_ACTIONS:
            A.update_job(jid, status="failed", outcome="component left the registry")
            _log(f, "component not in registry / bad action — aborting")
            return
        if comp["root"] != "no":
            A.update_job(jid, status="failed",
                         outcome=f"{comp_id} needs a {comp['root']} step — not one-click")
            _log(f, f"refusing: root={comp['root']} is never auto-run")
            return
        if action == "unadopt" and comp_id in A.NO_ONECLICK_REMOVE:
            A.update_job(jid, status="failed", outcome=f"{comp_id} is not one-click removable")
            _log(f, f"refusing: {comp_id} is install-only from the panel (would bounce lease/self)")
            return

        rc, output = _run_driver(action, comp_id, f)
        # Ground truth is the re-probe; `driver_ok` (a structured token, not a prose grep) only
        # decides the probe-blind (headless) fallback. Adopt fails CLOSED when it can't confirm.
        state = A.component_state(comp)
        driver_ok = f"AGENTOS-RESULT {comp_id} ok" in output
        _log(f, f"re-probe: {comp_id} now reads '{state}' (driver_ok={driver_ok}, rc={rc})")

        if action == "adopt":
            if state == "adopted":
                status, outcome = "adopted", "installed"
            elif state == "unknown" and driver_ok:
                status, outcome = "adopted", "installed (could not verify on this host)"
            else:
                status, outcome = "failed", "apply ran but the component still reads not-installed"
        else:  # unadopt — root:no only, so absent is always "available" (never needs-you)
            if state == "available":
                status, outcome = "available", "removed"
            elif state == "unknown" and driver_ok:
                status, outcome = "available", "removed (could not verify on this host)"
            else:
                status, outcome = "failed", "restore ran but the component still reads installed"

        A.update_job(jid, status=status, outcome=outcome)
        _log(f, f"{status.upper()} — {outcome}")
        verb = "adopted" if status == "adopted" else ("removed" if status == "available" else "didn't complete")
        title = f"{comp_id} — {verb}"
        _toast(title, outcome if status != "failed" else f"{outcome}. See the status panel.")
    except Exception as e:
        A.update_job(jid, status="failed", outcome="adopt worker error (see log)")
        _log(f, f"worker error: {e!r}")
    finally:
        try:
            f.close()
        except Exception:
            pass


if __name__ == "__main__":
    if len(sys.argv) != 2 or not A.valid_job_id(sys.argv[1]):
        print("usage: adopt_run.py <job_id>", file=sys.stderr)
        sys.exit(2)
    run(sys.argv[1])

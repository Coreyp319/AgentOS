#!/usr/bin/env python3
"""AgentOS dispatch worker (ADR-0039) — the body of a dispatched agent.

Launched by the panel as a transient `systemd-run --user` unit (OUTSIDE the panel's
ProtectHome=read-only sandbox), one per incident, with a single server-minted incident id.

It does the work the hardened panel cannot:
  1. FIRST-AID (the bounded auto-fix, code-disposed) — for an OPTED-IN, user-scope catalog unit
     on the allowlist, and ONLY if the service is still in attention on a fresh re-probe, run the
     reversible `reset-failed && restart` ONCE, then re-probe. Recovered → done.
  2. ESCALATE (model proposes, code disposes) — otherwise hand the gathered, REDACTED evidence to
     the chosen agent (claude | hermes) as a PURE READING-FREE reasoner (no tools, no file access,
     no MCP); record its proposal as needs-approval for the human. The worker never executes it.
  3. LOG — a 0600 durable transcript per incident (TTL-pruned) + ledger updates that drive the UI.

stdlib-only. Run: dispatch_run.py <incident_id>"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import dispatch as D
import status_panel as sp

SETTLE_PROBES = 5                      # re-probe after a restart: up to ~12s for it to come back
SETTLE_INTERVAL = 2.5
CLAUDE_TIMEOUT = 150
HERMES_TIMEOUT = 120
LOG_TTL_H = float(os.environ.get("AGENTOS_DISPATCH_LOG_TTL_H", "48"))  # durable transcript TTL
LOG_KEEP = 100                         # …or keep at most this many, newest-first


# ── logging (0600, $HOME, TTL-pruned — may contain residual journal detail) ─────────────────
def _open_log(iid: str, svc_id: str):
    d = D.log_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    _prune_logs(d)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    p = d / f"{stamp}-{svc_id}-{iid}.log"
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
    print(line, flush=True)            # also to the transient unit's journal


def _toast(title: str, body: str) -> None:
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(["notify-send", "-a", "AgentOS", "-u", "normal",
                        "-i", "dialog-information", title, body], check=False, timeout=5)
    except Exception:
        pass


# ── health re-probe (reuses the panel's own logic) ─────────────────────────────────────────
def _probe(svc: dict) -> dict:
    st = sp._unit_status(svc)
    health = svc.get("health", "")
    do_probe = bool(health) and st.get("status") not in ("absent", "idle")
    return {**svc, **st, "kind": svc.get("kind", "daemon"),
            "reach": sp._reachable(health) if do_probe else ""}


def _catalog_svc(svc_id: str) -> dict | None:
    try:
        cat = json.loads(sp.CATALOG_PATH.read_text())
    except Exception:
        return None
    return next((s for s in cat.get("services", []) if s.get("id") == svc_id), None)


# ── first-aid: the only state mutation the worker does without a human ──────────────────────
def _first_aid(svc: dict, iid: str, f) -> str:
    """Run the allowlisted, reversible recovery ONCE, then re-probe. Returns one of:
      'recovered'      — service left attention after the restart,
      'self-resolved'  — service was ALREADY healthy on the pre-restart re-probe (no restart done),
      'start-limit'    — systemd's StartLimit had parked it; we did NOT reset-and-retry (escalate),
      'no'             — restart didn't recover it (escalate).
    The unit comes from the trusted catalog, never a wire string → no injection."""
    unit = svc["unit"]
    base = ["systemctl", "--user"] if svc.get("scope", "user") == "user" else ["systemctl"]

    # TOCTOU guard: validate() checked attention against an up-to-2.5s-stale snapshot at POST time,
    # and we run seconds later. Re-probe NOW; if it already recovered (on its own or by a human),
    # don't bounce a healthy service. (ADR-0039: the disposer acts on CURRENT state.)
    pre = _probe(svc)
    if not sp._is_attention(pre):
        _log(f, "first-aid: service already healthy on re-probe — not restarting")
        return "self-resolved"

    # Don't re-arm a crashloop systemd already gave up on: if it's parked by StartLimit, a blind
    # `reset-failed` would clear systemd's own brake. Escalate to a human instead.
    if pre.get("result") == "start-limit-hit":
        _log(f, "first-aid: unit is parked by StartLimit (crashloop) — escalating, not re-arming")
        return "start-limit"

    D.update_incident(iid, first_aid_tried=True)
    _log(f, f"first-aid: {' '.join(base)} reset-failed {unit} && restart {unit}")
    for verb in (["reset-failed", unit], ["restart", unit]):
        try:
            r = subprocess.run(base + verb, capture_output=True, text=True, timeout=30, check=False)
        except subprocess.TimeoutExpired:
            _log(f, f"  {verb[0]} timed out")
            continue
        if r.stdout.strip():
            _log(f, f"  {verb[0]}: {r.stdout.strip()[:300]}")
        if r.returncode != 0:
            _log(f, f"  {verb[0]} exit {r.returncode}: {(r.stderr or '').strip()[:300]}")
    for n in range(SETTLE_PROBES):
        time.sleep(SETTLE_INTERVAL)
        row = _probe(svc)
        attn = sp._is_attention(row)
        _log(f, f"  probe {n + 1}/{SETTLE_PROBES}: status={row.get('status')} "
                f"reach={row.get('reach') or '-'} attention={attn}")
        if not attn:
            return "recovered"
    return "no"


# ── escalation: the model is a TOOL-FREE reasoner; it PROPOSES, never executes ──────────────
def _diag_prompt(brief: dict) -> str:
    return (
        "You are an AgentOS diagnostic agent. A systemd service on a Linux/KDE (CachyOS, "
        "Wayland/Plasma 6) box is failing, and an automatic `systemctl restart` either didn't fix "
        "it or wasn't allowed. From ONLY the evidence below, diagnose the most likely ROOT CAUSE "
        "and, if an obvious safe fix exists, propose ONE shell command a human could run. You have "
        "no tools and cannot run anything — you propose, a human disposes.\n\n"
        f"Service : {brief.get('name')} (id={brief.get('id')}, unit={brief.get('unit')}, "
        f"scope={brief.get('scope')})\n"
        f"What it is: {brief.get('desc')}\n"
        f"Status  : {brief.get('status')} / {brief.get('state')} "
        f"(port reach: {brief.get('reach') or 'n/a'})\n"
        f"Recent journal (secrets/PII redacted):\n{brief.get('journal')}\n\n"
        "Reply with a brief explanation (≤ 40 words), then EXACTLY one fenced json block and "
        "nothing after it:\n"
        "```json\n"
        '{"diagnosis": "<one or two short sentences>", "proposed_fix": "<a single shell command, '
        'or empty string if no safe fix is evident>", "confidence": "low|medium|high"}\n'
        "```"
    )


_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _balanced_obj(text: str) -> str | None:
    """Find the LAST brace-balanced {...} containing "diagnosis" — robust to a proposed_fix that
    itself contains braces/quotes (a plain [^{}] class would drop such a fix)."""
    best = None
    for m in re.finditer(r"\{", text or ""):
        i, depth = m.start(), 0
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[i:j + 1]
                    if '"diagnosis"' in chunk:
                        best = chunk
                    break
    return best


def _parse_model_json(text: str) -> dict:
    m = list(_JSON_BLOCK.finditer(text or ""))
    raw = m[-1].group(1) if m else _balanced_obj(text)
    if raw:
        try:
            d = json.loads(raw)
            return {"diagnosis": str(d.get("diagnosis", "")).strip(),
                    "proposed_fix": str(d.get("proposed_fix", "")).strip(),
                    "confidence": str(d.get("confidence", "")).strip().lower()}
        except Exception:
            pass
    return {"diagnosis": (text or "").strip()[:600], "proposed_fix": "", "confidence": ""}


def _run_claude(brief: dict, f) -> dict:
    """Run Claude headless as a PURE reasoner: no MCP (--strict-mcp-config), every built-in tool
    disallowed (incl. Read/Grep/Glob → no filesystem reach, no off-box exfil beyond the redacted
    brief), default permission mode (an un-pre-approved tool can't prompt in -p → hard deny), and a
    PINNED model (so the JSON contract + latency budget are stable). It reasons over the brief only."""
    claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not Path(claude).exists():
        return {"ok": False, "error": "claude CLI not found on PATH"}
    cmd = [claude, "-p", _diag_prompt(brief),
           "--model", D.CLAUDE_MODEL,
           "--strict-mcp-config",                 # drop every inherited MCP server (Gmail/Supabase/gpu…)
           "--permission-mode", "default",
           "--disallowedTools", "Bash", "Write", "Edit", "NotebookEdit", "KillShell",
           "Read", "Grep", "Glob", "WebFetch", "WebSearch", "Agent", "Task", "Skill",
           "Workflow", "ScheduleWakeup", "ToolSearch", "AskUserQuestion",
           "--output-format", "text"]
    _log(f, f"escalating to Claude (model={D.CLAUDE_MODEL}, no tools)")
    try:
        # cwd is a neutral tmp dir — the model has no file tools anyway, but don't even seat it in the repo.
        r = subprocess.run(cmd, capture_output=True, text=True, cwd="/tmp",
                           timeout=CLAUDE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Claude timed out after {CLAUDE_TIMEOUT}s"}
    except Exception as e:
        return {"ok": False, "error": f"could not run Claude: {e}"}
    if r.stderr.strip():
        _log(f, f"claude stderr: {D.redact(r.stderr.strip())[:500]}")
    out = (r.stdout or "").strip()
    _log(f, f"claude said:\n{D.redact(out)}")
    if not out:
        return {"ok": False, "error": f"Claude returned no output (exit {r.returncode}; "
                                      "not authenticated?)"}
    parsed = _parse_model_json(out)
    return {"ok": True, "model": D.CLAUDE_MODEL, **parsed}


def _post_json(url: str, body: dict, headers: dict, timeout: float) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw or b"{}")
            except Exception:
                return resp.status, {"raw": raw.decode("utf-8", "replace")[:500]}
    except urllib.error.HTTPError as e:
        return e.code, {"error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return 0, {"error": f"unreachable: {e.reason}"}


def _run_hermes(brief: dict, f) -> dict:
    """Phase 2 (ADR-0039): the Hermes write-API (`POST /v1/runs`, Bearer) isn't enabled by default.
    Fail HONESTLY — never pretend a run was created. Hermes gets its own (tool-free) prompt."""
    key = os.environ.get("HERMES_API_KEY", "")
    base = os.environ.get("HERMES_BASE", "http://127.0.0.1:8642").rstrip("/")
    if not key:
        return {"ok": False, "blocked": True,
                "error": "Hermes write-API not enabled — set HERMES_API_KEY + "
                         "platforms.api_server in ~/.hermes/config.yaml, then restart the gateway."}
    _log(f, f"escalating to Hermes ({base}/v1/runs)")
    prompt = (f"Diagnose why the systemd service '{brief.get('name')}' ({brief.get('unit')}) is "
              f"{brief.get('status')} and propose a fix. Evidence (redacted):\n{brief.get('journal')}")
    code, resp = _post_json(f"{base}/v1/runs",
                            {"input": prompt, "metadata": {"source": "agentos-status-panel",
                                                           "service": brief.get("id"), "tier": "batch"}},
                            {"Authorization": f"Bearer {key}"}, HERMES_TIMEOUT)
    _log(f, f"hermes /v1/runs → {code}: {str(resp)[:400]}")
    if code == 0 or code >= 400:
        return {"ok": False, "blocked": True,
                "error": f"Hermes write-API unavailable ({resp.get('error', code)}). "
                         "It's a Phase-2 dependency — enable platforms.api_server."}
    run_id = resp.get("id") or resp.get("run_id")
    if not run_id:
        return {"ok": False, "blocked": True,
                "error": "Hermes accepted the request but returned no run id — can't track it."}
    return {"ok": True, "handed_off": True,
            "diagnosis": f"Handed off to Hermes as run {run_id}; watch it on the Hermes board."}


# ── orchestration ───────────────────────────────────────────────────────────────────────────
def run(iid: str) -> None:
    if not D.claim_incident(iid):          # atomic CAS: only one worker claims a queued incident
        print(f"dispatch worker: incident {iid} not claimable (gone/terminal/already claimed)", flush=True)
        return
    led = D.read_ledger()
    entry = led.get("incidents", {}).get(iid, {})
    svc_id, target = entry.get("svc"), entry.get("target")
    f, log_path = _open_log(iid, svc_id or "unknown")
    D.update_incident(iid, log=log_path)
    try:
        _log(f, f"dispatch {iid}: service={svc_id} target={target}")
        svc = _catalog_svc(svc_id)
        if not svc:
            D.update_incident(iid, status="failed", outcome="service left the catalog")
            _log(f, "service not in catalog — aborting")
            return

        ok_auto, why = D.can_auto_recover(svc)
        if ok_auto and not entry.get("skip_first_aid"):
            D.update_incident(iid, status="first-aid")
            verdict = _first_aid(svc, iid, f)
            if verdict in ("recovered", "self-resolved"):
                outcome = ("recovered by restart" if verdict == "recovered"
                           else "already healthy — nothing to do")
                D.update_incident(iid, status="recovered", method=verdict, outcome=outcome)
                _log(f, f"RECOVERED ({verdict})")
                _toast(f"{entry.get('name')} — recovered", f"{outcome}. Logged.")
                return
            _log(f, f"first-aid={verdict} — escalating to the agent")
        else:
            reason = "skipped (crashloop brake)" if entry.get("skip_first_aid") else why
            _log(f, f"first-aid not run ({reason}) — escalating to the agent")

        D.update_incident(iid, status="investigating")
        brief = D.build_brief(svc)
        result = _run_claude(brief, f) if target == "claude" else _run_hermes(brief, f)

        if not result.get("ok"):
            status = "blocked" if result.get("blocked") else "failed"
            D.update_incident(iid, status=status, outcome=result.get("error", "investigation failed"))
            _log(f, f"{status}: {result.get('error')}")
            _toast(f"{entry.get('name')} — needs you",
                   result.get("error", "Dispatch couldn't complete — see the status panel."))
            return

        diagnosis = D.redact(result.get("diagnosis", ""))   # belt-and-suspenders before it's stored/served
        confidence = result.get("confidence", "")
        if result.get("handed_off"):
            D.update_incident(iid, status="handed-off", method="hermes", diagnosis=diagnosis,
                              outcome="handed off to a Hermes run")
            _log(f, f"HANDED OFF — {diagnosis}")
            _toast(f"{entry.get('name')} — handed to Hermes", diagnosis)
        elif result.get("proposed_fix"):
            D.update_incident(iid, status="needs-approval", method="agent-proposed",
                              diagnosis=diagnosis, proposal=result["proposed_fix"],
                              confidence=confidence, model=result.get("model", ""),
                              outcome="found a fix — needs your approval")
            _log(f, f"NEEDS APPROVAL ({confidence}) — {diagnosis}\n  proposed: {result['proposed_fix']}")
            _toast(f"{entry.get('name')} — a fix is ready",
                   "The agent proposed a fix. Review and approve it in the status panel.")
        else:
            D.update_incident(iid, status="diagnosed", method="agent-proposed", diagnosis=diagnosis,
                              confidence=confidence, model=result.get("model", ""),
                              outcome="investigated — no safe automatic fix found")
            _log(f, f"DIAGNOSED, NO FIX ({confidence}) — {diagnosis}")
            _toast(f"{entry.get('name')} — investigated",
                   "No safe automatic fix found. See the diagnosis in the status panel.")
    except Exception as e:                 # never leave an incident stuck mid-flight
        D.update_incident(iid, status="failed", outcome="dispatch worker error (see log)")
        _log(f, f"worker error: {e!r}")
    finally:
        try:
            f.close()
        except Exception:
            pass


if __name__ == "__main__":
    if len(sys.argv) != 2 or not D.valid_incident_id(sys.argv[1]):
        print("usage: dispatch_run.py <incident_id>", file=sys.stderr)
        sys.exit(2)
    run(sys.argv[1])

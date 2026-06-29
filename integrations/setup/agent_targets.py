#!/usr/bin/env python3
"""Agent-target adapters — propagate a chosen default model to the agents (ADR-0049 Phase 2).

ONE concrete adapter today: ``HermesAdapter``, which reads / writes the Hermes inference default in
``~/.hermes/config.yaml``. The ``current() / propose() / set_default() / revert()`` contract documented
here is the EXTENSION POINT for a second agent (e.g. "openclaw") when one actually exists — per
ADR-0001 / ADR-0049 we build NO framework for a hypothetical agent.

Why the write is what it is (the council's must-fixes):
  * SURGICAL single-key edit — replace only the ``model.default`` scalar, every other byte unchanged.
    NO ``yaml.safe_load`` / ``safe_dump`` round-trip: it would strip the file's comments and the
    commented-out ``fallback_model`` block. Aborts unless EXACTLY one ``default:`` exists under the
    top-level ``model:`` block (0 or >1 → refuse, never guess).
  * PER-KEY inverse in the setup-manifest — the exact prior scalar — NOT gpu-coordinator's whole-file
    ``config.yaml.agentos-bak`` (a shared snapshot would make the two components' reverts clobber).
  * ATOMIC same-dir temp + fsync + rename, preserving the file's mode/owner.

Hermes runs as a systemd user service (``hermes-gateway.service``); a config change is applied by a
RESTART — never mid-turn, never a silent auto-restart (the gpu-coordinator plugin already restarts the
same way). ``propose()`` is a DRY RUN: it writes nothing.

ADR-0049 Phase 2 STAGING: this module is BUILT + unit-tested, but the wizard only ever calls
``propose()`` / ``estimate_fit()`` (read-only) for now. The live ``set_default()`` write is wired to a
UI action only after the dry-run is reviewed on-box; and it should run behind a MEASURED canary
(``estimate_fit`` is the static pre-check; the measured load-and-verify gate is the remaining 2b work).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import setup  # reuse: hermes path, hardware probe, ollama presence, the manifest ledger + lock

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
# canary thresholds — a candidate must run ~entirely on the GPU and at a usable speed, or it's refused.
# (the 2026-06-19 incident the canary exists to prevent: a capable 27B at 87% on CPU → 1.5 tok/s.)
CANARY_ON_GPU = float(os.environ.get("AGENTOS_CANARY_ON_GPU", "0.95"))   # size_vram/size floor
CANARY_TOK_S = float(os.environ.get("AGENTOS_CANARY_TOK_S", "8.0"))       # generation tok/s floor

# the model.default scalar, capturing (prefix)(value)(trailing comment/space) so we replace ONLY value.
_DEFAULT_RE = re.compile(r"^(\s*default\s*:\s*)(\"[^\"]*\"|'[^']*'|\S+)(.*)$")
# a safe single-line model ref — no whitespace/newline/quotes, so it can't inject YAML structure into
# config.yaml (security review). Applied to user-supplied refs before any write.
_REF_RE = re.compile(r"^[A-Za-z0-9._:/+@-]{1,200}$")

# the command that applies a config change (mirrors integrations/hermes/gpu-coordinator/DEPLOY.md).
HERMES_RESTART = ("systemctl", "--user", "restart", "hermes-gateway.service")
HERMES_UNIT = "hermes-gateway.service"


def _ollama_size_gb(ref: str) -> float:
    """Best-effort on-disk size (GB) of an ollama ref from `ollama list` — the WEIGHTS floor of the
    runtime footprint (KV/context add more at high ctx). 0.0 if unknown. Never raises."""
    base = ref.split(":")[0]
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=15, check=False)
        for line in r.stdout.splitlines()[1:]:
            tok = line.split()
            if not tok:
                continue
            name = tok[0]
            if name == ref or name.split(":")[0] == base:
                m = re.search(r"([\d.]+)\s*(GB|MB)", line)
                if m:
                    v = float(m.group(1))
                    return round(v / 1024, 1) if m.group(2) == "MB" else round(v, 1)
    except Exception:
        pass
    return 0.0


def _atomic_write_text(path: Path, text: str) -> tuple[bool, str]:
    """Atomic same-dir temp + fsync + rename, preserving the existing file's mode. (ok, reason)."""
    try:
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            mode = 0o600
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "w") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            try:                                 # fsync the dir so the rename is durable (ordering vs the inverse)
                dfd = os.open(str(path.parent), os.O_DIRECTORY)
                os.fsync(dfd)
                os.close(dfd)
            except OSError:
                pass
            return True, ""
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception as e:                       # pragma: no cover
        return False, f"write-failed: {e}"


# ── the measured canary (ADR-0024 §3 / ADR-0049 Phase 2b) ────────────────────────────────────────
def _ollama_ps() -> list[dict]:
    """Ollama's currently-resident models (/api/ps): each carries `size` (total) + `size_vram`."""
    try:
        with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=5) as r:
            return json.load(r).get("models", [])
    except Exception:
        return []


def _ollama_generate(ref: str, prompt: str = "Say OK.", num_predict: int = 16,
                     keep_alive: str = "30s", timeout: int = 240) -> dict:
    """One tiny generation — forces the model resident and yields eval timing for the tok/s measure."""
    body = json.dumps({"model": ref, "prompt": prompt, "stream": False,
                       "options": {"num_predict": num_predict}, "keep_alive": keep_alive}).encode()
    req = urllib.request.Request(OLLAMA + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def measured_canary(ref: str, *, run=subprocess.run, generate=None, ps=None, hw=None,
                    stop_after: bool = True) -> dict:
    """MEASURE that `ref` actually runs on the GPU under the live load — the go/no-go before a live
    default change. Loads the model via Ollama, reads `/api/ps` (`size_vram` vs `size`) and the
    generation tok/s, and PASSES only if it is ~entirely on the GPU AND above the tok/s floor. A model
    that offloads to CPU (the 2026-06-19 thrash) or OOMs is REFUSED no matter how capable. Read-only
    w.r.t. config; frees the model afterward (so the canary doesn't leave it resident) unless it is the
    one the gateway is already using. Returns a verdict dict (never raises)."""
    generate = generate or _ollama_generate
    ps = ps or _ollama_ps
    if not setup._ollama_has(ref):
        return {"pass": False, "reason": "not-present", "measured": True}
    already_resident = any((m.get("name") or m.get("model") or "").split(":")[0] == ref.split(":")[0]
                           for m in ps())
    # PREDICT-BEFORE-LOAD (ADR-0024 §4): refuse to even load the candidate if it won't fit CURRENT free
    # VRAM — never OOM a standing graphics holder (ComfyUI's allocation is invisible to Ollama's own fit
    # math) or stack two big LLMs. Fail-closed; the user frees the GPU (or it ages out) and retries.
    # (Full Spawn(batch)-lease admission is the remaining hardening — noted in ADR-0049 §Phase-2b.)
    if not already_resident:
        hw = hw or setup.detect_hardware()
        free_gb = round((hw.get("vram_free_mib") or 0) / 1024, 1)
        foot = round((_ollama_size_gb(ref) or 0) * 1.3, 1)        # weights + KV/context headroom
        if foot and free_gb and foot > free_gb - 1.0:             # 1 GB desktop headroom
            return {"pass": False, "reason": "insufficient-free-vram", "needed_gb": foot,
                    "free_gb": free_gb, "measured": True}
    try:
        d = generate(ref)
    except Exception as e:
        return {"pass": False, "reason": f"load-failed: {e}", "measured": True}
    ec, ed = d.get("eval_count") or 0, d.get("eval_duration") or 0
    tok_s = round(ec / (ed / 1e9), 1) if ec and ed else 0.0
    size = size_vram = 0
    for m in ps():
        nm = m.get("name") or m.get("model") or ""
        if nm == ref or nm.split(":")[0] == ref.split(":")[0]:
            size, size_vram = m.get("size") or 0, m.get("size_vram") or 0
            break
    frac = round(size_vram / size, 3) if size else 0.0
    on_gpu = frac >= CANARY_ON_GPU
    fast = tok_s >= CANARY_TOK_S
    ok = on_gpu and fast
    reason = "ok" if ok else ("cpu-offload" if not on_gpu else "too-slow")
    # free it — UNLESS it was already resident (it may be the model the gateway is serving; don't evict that).
    if stop_after and not already_resident:
        try:
            run(["ollama", "stop", ref], capture_output=True, timeout=20, check=False)
        except Exception:
            pass
    return {"pass": ok, "on_gpu_frac": frac, "tok_s": tok_s, "size_gb": round(size / 1024**3, 1),
            "size_vram_gb": round(size_vram / 1024**3, 1), "reason": reason, "measured": True}


class HermesAdapter:
    """Read / propose / set / revert the Hermes inference default in config.yaml."""
    agent = "hermes"
    role = "inference"

    def __init__(self, config_path: str | Path | None = None):
        self.path = Path(config_path) if config_path else setup.hermes_config_path()

    # ── read ──
    def current(self) -> str | None:
        return setup.hermes_current_default(self.path)

    def _default_lines(self, text: str) -> tuple[list[int], list[str]]:
        """Line indices of `default:` under the TOP-LEVEL `model:` block (split on '\\n' so a join is
        byte-exact)."""
        lines = text.split("\n")
        out, in_model = [], False
        for i, ln in enumerate(lines):
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            if ln[:1] not in (" ", "\t"):                    # a top-level key — (re)decide model: membership
                in_model = (ln.split(":", 1)[0].strip() == "model")
                continue
            if in_model and ln.strip().split(":", 1)[0].strip() == "default":
                out.append(i)
        return out, lines

    # ── dry run (writes NOTHING) ──
    def propose(self, ref: str) -> dict:
        cur = self.current()
        changes = (cur != ref)
        diff = (f"  model:\n-   default: {cur}\n+   default: {ref}" if changes
                else "(no change — already the default)")
        return {"agent": self.agent, "role": self.role, "path": str(self.path),
                "current": cur, "target": ref, "changes": changes, "diff": diff,
                "apply_hint": f"applies on: {' '.join(HERMES_RESTART)}"}

    def estimate_fit(self, ref: str, hw: dict | None = None) -> dict:
        """A STATIC pre-check — present + a footprint estimate vs VRAM, leaving room for a co-resident
        creative server (ADR-0018). NOT the measured canary (that loads the model + reads size_vram/tok-s
        and is the gate before the live write); honestly labelled so the proposal never overclaims."""
        hw = hw or setup.detect_hardware()
        present = setup._ollama_has(ref)
        size = _ollama_size_gb(ref)
        foot = round(size * 1.3, 1) if size else 0.0         # +KV/context headroom over the weights floor
        vram = hw.get("vram_gb") or 0
        if not vram or not foot:
            verdict = "unknown"
        elif foot <= vram * 0.6:                              # ≤60%: comfortable alongside a creative lane
            verdict = "fits"
        elif foot <= vram * 0.92:
            verdict = "tight"
        else:
            verdict = "too-big"
        return {"present": present, "est_footprint_gb": foot, "vram_gb": vram, "verdict": verdict,
                "measured": False,
                "note": "estimate only — a measured canary (load + on-GPU check) runs before the live write"}

    # ── the real, reversible write (Phase 2b) ──
    def _do_write(self, value: str) -> tuple[bool, str, str | None]:
        """Locate the single model.default and replace its value verbatim → (ok, reason, prior_literal).
        MUST be called inside `setup._file_lock(self.path)`. `value` is written as-is (caller validates)."""
        try:
            text = self.path.read_text()
        except Exception:
            return False, "no-config", None
        idxs, lines = self._default_lines(text)
        if len(idxs) != 1:                                   # 0 or >1 → refuse; never guess which to edit
            return False, f"expected exactly one model.default, found {len(idxs)}", None
        m = _DEFAULT_RE.match(lines[idxs[0]])
        if not m:
            return False, "unparseable default line", None
        prior_literal = m.group(2)                            # the EXACT bytes we overwrite (quotes incl.)
        lines[idxs[0]] = m.group(1) + value + m.group(3)      # replace ONLY the value; keep indent + comment
        ok, why = _atomic_write_text(self.path, "\n".join(lines))
        return ok, why, prior_literal

    def set_default(self, ref: str, *, record: bool = True) -> dict:
        """Surgically set model.default → ref, reversibly, under the config lock. The PER-KEY inverse (the
        EXACT prior literal, quotes preserved) is recorded BEFORE the write and rolled back if the write
        fails — so an apply never leaves config.yaml changed with no way to Revert. Does NOT restart
        Hermes (returns the hint; applying is an explicit, separate, never-mid-turn step)."""
        if not _REF_RE.fullmatch(ref or ""):                  # fullmatch: reject even a trailing newline (no YAML injection)
            return {"ok": False, "reason": "bad-ref"}
        with setup._file_lock(self.path):                     # serialize concurrent applies (ThreadingHTTPServer)
            try:
                text = self.path.read_text()
            except Exception:
                return {"ok": False, "reason": "no-config"}
            idxs, lines = self._default_lines(text)
            if len(idxs) != 1:
                return {"ok": False, "reason": f"expected exactly one model.default, found {len(idxs)}"}
            m = _DEFAULT_RE.match(lines[idxs[0]])
            if not m:
                return {"ok": False, "reason": "unparseable default line"}
            prior_literal = m.group(2)
            cur_val = prior_literal.strip('"').strip("'")
            if cur_val == ref:
                return {"ok": True, "skipped": "already-default", "current": cur_val}
            aid = "setdefault-" + self.agent + "-" + hashlib.sha1(
                f"{prior_literal}>{ref}>{time.time()}".encode("utf-8", "replace")).hexdigest()[:8]
            action = {"kind": "set-default", "agent": self.agent, "id": aid, "prior": prior_literal,
                      "new": ref, "path": str(self.path), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            if record and not setup.record_action(action):    # inverse FIRST + durable, else abort (no write)
                return {"ok": False, "reason": "ledger-write-failed"}
            lines[idxs[0]] = m.group(1) + ref + m.group(3)
            ok, why = _atomic_write_text(self.path, "\n".join(lines))
            if not ok:
                if record:
                    setup.remove_action(aid, kind="set-default")   # roll back the inverse — no orphan entry
                return {"ok": False, "reason": why or "write-failed"}
        return {"ok": True, "prior": cur_val, "new": ref, "restart_cmd": list(HERMES_RESTART),
                "restart_hint": "restart Hermes to apply: " + " ".join(HERMES_RESTART)}

    def revert(self) -> dict:
        """Undo the most recent set-default for this agent: restore the EXACT prior literal (so quoted/
        commented defaults round-trip byte-for-byte) under the lock, then drop the action. Idempotent."""
        acts = [a for a in setup.manifest_actions()
                if a.get("kind") == "set-default" and a.get("agent") == self.agent]
        if not acts:
            return {"ok": False, "reason": "no-set-default-to-revert"}
        last = acts[-1]
        prior_literal = last.get("prior")
        if prior_literal is None:
            return {"ok": False, "reason": "no-prior-recorded"}
        with setup._file_lock(self.path):
            ok, why, _ = self._do_write(prior_literal)        # write the exact prior literal back, verbatim
        if not ok:
            return {"ok": False, "reason": why or "write-failed"}
        setup.remove_action(last["id"], kind="set-default")
        return {"ok": True, "restored": prior_literal.strip('"').strip("'"),
                "restart_cmd": list(HERMES_RESTART), "restart_hint": "restart Hermes to apply"}

    def restart(self, run=subprocess.run) -> dict:
        """Apply a pending change by restarting the gateway — only if it's actually active (mirrors the
        gpu-coordinator). Explicit/opt-in; never called implicitly by set_default."""
        try:
            active = run(["systemctl", "--user", "is-active", "--quiet", HERMES_UNIT], check=False).returncode == 0
        except Exception:
            active = False
        if not active:
            return {"ok": False, "reason": "hermes-gateway not active — nothing to restart"}
        r = run(list(HERMES_RESTART), check=False)
        return {"ok": getattr(r, "returncode", 1) == 0}


# The adapter registry: today exactly one real target. A second agent (openclaw, …) becomes a new
# class implementing the same 4-method contract + an entry here — no other change (ADR-0049 §6).
ADAPTERS = {"hermes": HermesAdapter}


def get_adapter(agent: str = "hermes", config_path: str | Path | None = None):
    cls = ADAPTERS.get(agent)
    return cls(config_path) if cls else None

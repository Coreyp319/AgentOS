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
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import setup  # reuse: hermes path, hardware probe, ollama presence, the manifest ledger + lock

# the model.default scalar, capturing (prefix)(value)(trailing comment/space) so we replace ONLY value.
_DEFAULT_RE = re.compile(r"^(\s*default\s*:\s*)(\"[^\"]*\"|'[^']*'|\S+)(.*)$")

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
            return True, ""
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception as e:                       # pragma: no cover
        return False, f"write-failed: {e}"


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

    # ── the real, reversible write (Phase 2 — built + tested; wired live only after on-box review) ──
    def _write_default(self, ref: str) -> tuple[bool, str]:
        try:
            text = self.path.read_text()
        except Exception:
            return False, "no-config"
        idxs, lines = self._default_lines(text)
        if len(idxs) != 1:                                   # 0 or >1 → refuse; never guess which to edit
            return False, f"expected exactly one model.default, found {len(idxs)}"
        m = _DEFAULT_RE.match(lines[idxs[0]])
        if not m:
            return False, "unparseable default line"
        lines[idxs[0]] = m.group(1) + ref + m.group(3)       # replace ONLY the value; keep indent + comment
        return _atomic_write_text(self.path, "\n".join(lines))

    def set_default(self, ref: str, *, record: bool = True) -> dict:
        """Surgically set model.default → ref, reversibly. Records a PER-KEY inverse (the prior scalar) in
        the setup-manifest so revert() restores it exactly. Idempotent. Does NOT restart Hermes — returns
        the restart hint; applying is an explicit, separate step (never mid-turn)."""
        cur = self.current()
        if cur == ref:
            return {"ok": True, "skipped": "already-default", "current": cur}
        ok, reason = self._write_default(ref)
        if not ok:
            return {"ok": False, "reason": reason}
        if record:
            aid = "setdefault-" + self.agent + "-" + hashlib.sha1(
                f"{cur}>{ref}>{time.time()}".encode("utf-8", "replace")).hexdigest()[:8]
            setup.record_action({"kind": "set-default", "agent": self.agent, "id": aid,
                                 "prior": cur, "new": ref, "path": str(self.path),
                                 "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        return {"ok": True, "prior": cur, "new": ref, "restart_cmd": list(HERMES_RESTART),
                "restart_hint": "restart Hermes to apply: " + " ".join(HERMES_RESTART)}

    def revert(self) -> dict:
        """Undo the most recent set-default for this agent: restore the prior scalar + drop the action."""
        acts = [a for a in setup.manifest_actions()
                if a.get("kind") == "set-default" and a.get("agent") == self.agent]
        if not acts:
            return {"ok": False, "reason": "no-set-default-to-revert"}
        last = acts[-1]
        ok, reason = self._write_default(last.get("prior") or "")
        if not ok:
            return {"ok": False, "reason": reason}
        setup.remove_action(last["id"], kind="set-default")
        return {"ok": True, "restored": last.get("prior"), "restart_hint": "restart Hermes to apply"}

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

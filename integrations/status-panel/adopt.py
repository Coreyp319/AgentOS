#!/usr/bin/env python3
"""AgentOS status-panel adoption core (ADR-0043).

Progressive feature adoption: from the status / Atrium page, see which components.conf
components are adopted vs available, and turn a user-scope one on (or off) with one click.

This module runs INSIDE the hardened, loopback-bound panel. Like dispatch.py it never mutates
system state and never leaves the sandbox — it only:

  • parses the component registry (components.conf) — the SAME source of truth install.sh uses,
  • probes each component's install artifact READ-ONLY → adopted | available | needs-you | unknown,
  • mints + checks the anti-CSRF token,
  • validates an adopt/un-adopt against the registry + kill-switch (root:no only — sudo/manual
    are copy-don't-execute, never one-click),
  • reads/writes the adopt ledger (in $XDG_RUNTIME_DIR), atomically + flock'd, and
  • launches the worker (adopt_run.py) as a transient `systemd-run --user` unit, OUTSIDE the
    sandbox, which runs the registry's own install.sh/uninstall.sh.

The registry id only SELECTS a trusted row; the command run is the registry's own apply/restore
path and install.sh re-validates the id — no wire string is ever executed. stdlib-only.

See ADR-0043. The safety spine is inherited verbatim from ADR-0039 (dispatch); adoption adds two
stronger gates: POST /adopt is LOCAL-ORIGIN ONLY (the phone sees the catalog, can't adopt), and
only `root: no` components are one-click (NoNewPrivileges blocks escalation anyway)."""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
INTEGRATIONS = HERE.parent                      # integrations/ — holds components.conf + install.sh
REGISTRY = INTEGRATIONS / "components.conf"
INSTALL = INTEGRATIONS / "install.sh"
UNINSTALL = INTEGRATIONS / "uninstall.sh"
WORKER = HERE / "adopt_run.py"

# Per-process anti-CSRF token (ADR-0039 pattern). Served same-origin via GET /adopt/token, required
# as X-Adopt-Token on POST /adopt. A cross-origin page can POST but cannot read it (SOP) → CSRF-safe;
# the route also rejects Sec-Fetch-Site:cross-site AND a non-local origin. Rotates on restart.
TOKEN = secrets.token_hex(16)

VALID_ACTIONS = ("adopt", "unadopt")

# Removing these via one-click would disrupt live state or the panel itself, so they are
# install-only from the panel (mirrors dispatch.py's NEVER_AUTO_UNITS). Remove from a terminal.
#   core-substrate — stopping/restarting it bounces the VRAM lease daemon, which SIGKILLs a running
#                    dream (kill-on-drop) and disrupts live leases;
#   status-panel   — it is THIS page; removing it kills the surface you're clicking from.
NO_ONECLICK_REMOVE = frozenset({"core-substrate", "status-panel"})

# Human-readable display names for the catalog. The pipe-delimited components.conf stays the source
# of truth for WHAT exists (id is the stable key everything keys off); this is presentation only, so
# every surface (status panel + setup wizard) shows a real name instead of the raw slug id. An id not
# listed here falls back to its own slug — adding a component never breaks, it just reads less prettily
# until a name is added.
FRIENDLY_NAMES = {
    "core-substrate":      "AgentOS core service",
    "hermes-dashboard":    "Hermes agent board",
    "comfyui":             "ComfyUI engine",
    "lucid":               "Lucid — the dreaming app",
    "status-panel":        "Status page",
    "models-panel":        "Models panel",
    "share-hub":           "Phone-to-box photo share",
    "lucid-drain":         "Background video queue",
    "keyhole":             "Keyhole tray instrument",
    "reactive-wallpaper":  "Reactive wallpaper (+ window-drag wind)",
    "portal-timeout":      "Portal cold-boot fix",
    "aurora-theme":        "Aurora theme",
    "aurora-panel":        "Aurora panel & tray",
    "aurora-notifications": "Aurora notifications",
    "dolphin-create":      "Create Video — Dolphin menu",
    "browser-host":        "Create Video — browser menu",
    "krunner-finder":      "KRunner — ask Claude/Hermes/web",
    "firefox-pin":         "Pin the Firefox extension",
    "hermes-plugins":      "Needs-you signal",
    "gpu-coordinator":     "GPU coordinator for Hermes",
    "tailscale-remote":    "Remote access (Tailscale)",
}

# ── rate limits + reaper (abuse + resource guard), mirroring dispatch.py ─────────────────────
COOLDOWN_S = 5.0            # don't re-run the SAME component within this window of a finish
MAX_ACTIVE = 2             # cap concurrent in-flight adoptions
WORKER_TIMEOUT_S = 900     # the transient unit's hard cap — generous: core-substrate cargo-builds cold
STALE_ACTIVE_S = WORKER_TIMEOUT_S + 30
ACTIVE_STATUSES = ("queued", "applying", "unadopting")
# Terminal states, all distinct + honest (no silent failure-as-success):
#   adopted    — install.sh --only <id> succeeded and the artifact now probes present
#   available  — uninstall.sh --only <id> succeeded; the component is now available again
#   failed     — the apply/restore broke (driver error, build failure, timed out)
TERMINAL_STATUSES = ("adopted", "available", "failed")


def adopt_enabled() -> bool:
    return os.environ.get("AGENTOS_ADOPT", "1") != "0"


# ── the registry (components.conf) — parsed the SAME way _driver.sh does ─────────────────────
def _trim(s: str) -> str:
    return (s or "").strip()


def parse_registry() -> list[dict]:
    """id | tier | default | root | apply | restore | description (pipe-separated, # comments,
    blanks skipped) — identical field model to integrations/_driver.sh."""
    rows: list[dict] = []
    try:
        text = REGISTRY.read_text()
    except Exception:
        return rows
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [_trim(p) for p in line.split("|")]
        if not parts or not parts[0]:
            continue
        f = parts + [""] * (7 - len(parts))     # pad short rows
        rows.append({"id": f[0], "tier": f[1], "default": f[2], "root": f[3],
                     "apply": f[4], "restore": f[5], "desc": f[6]})
    return rows


def find(comp_id: str) -> dict | None:
    return next((c for c in parse_registry() if c["id"] == comp_id), None)


# ── read-only "is it adopted?" detection (ADR-0043 table) ────────────────────────────────────
# Each row's install artifact, probed read-only. probe_present() returns True (adopted) /
# False (not present) / None (can't tell — honest-when-blind, e.g. kpackagetool6 absent on a
# headless host). The state mapping (below) turns False into "available" for root:no and
# "needs-you" for sudo/manual.
PROBES: dict[str, tuple] = {
    "core-substrate":   ("unit", "agentos-lease.service"),
    "hermes-dashboard": ("unit", "hermes-dashboard.service"),
    "comfyui":          ("unit", "comfyui.service"),          # ships disabled → present = adopted
    "lucid":            ("unit", "agentos-lucid.service"),
    "status-panel":     ("unit", "agentos-status-panel.service"),
    "models-panel":     ("unit", "agentos-models-panel.service"),
    "share-hub":        ("unit", "agentos-share.service"),
    "lucid-drain":      ("unit", "lucid-drain.timer"),
    "keyhole":          ("applet", "org.agentos.keyhole"),
    "swaync-race":      ("file", "~/.config/systemd/user/swaync.service.d/nimbus-race.conf"),
    "reactive-wallpaper": ("file", "~/.local/state/agentos/reactive-wallpaper/prev-wallpaper.json"),
    "portal-timeout":   ("file", "~/.config/systemd/user/xdg-desktop-portal.service.d/timeout.conf"),
    "aurora-theme":     ("kconfig", ("kdeglobals", "KDE", "widgetStyle", "Union")),
    # aurora-panel writes this marker on apply and rm -f's it on restore (see aurora-panel/apply.sh,
    # restore.sh) — the only stable signal, since the cloned theme name is dynamic (<theme>-aurora).
    "aurora-panel":     ("file", "~/.local/share/aurora-theme/prev-plasmatheme"),
    # aurora-notifications writes an applied-marker on apply, rm -f's it on restore — the cloned theme
    # name is dynamic, so a fixed marker in the shared state dir is the only stable signal.
    "aurora-notifications": ("file", "~/.local/share/aurora-theme/aurora-notifications.on"),
    "dolphin-create":   ("file", "~/.local/share/kio/servicemenus/agentos-create-video.desktop"),
    "browser-host":     ("file", "~/.local/share/agentos/agentos_create_video_host.py"),
    "krunner-finder":   ("file", "~/.local/share/dbus-1/services/dev.corey.krunner.claude.service"),
    "firefox-pin":      ("file", "/etc/firefox/policies/policies.json"),
    "hermes-plugins":   ("file", "~/.hermes/plugins/needs-you-signal"),   # the needs-you-signal plugin (NOT gpu-coordinator)
    "gpu-coordinator":  ("file", "~/.hermes/plugins/gpu-coordinator"),    # promoted from DEPLOY.md to a component
    "tailscale-remote": ("tailscale", None),
}

# `systemctl is-enabled` words that all mean "the unit file exists" (i.e. installed/adopted).
_PRESENT_WORDS = frozenset({"enabled", "enabled-runtime", "static", "linked", "linked-runtime",
                            "masked", "masked-runtime", "alias", "indirect", "generated",
                            "transient", "disabled"})


def _cmd(args: list[str], timeout: float = 4.0) -> tuple[int, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception:
        return 127, ""


def _read_kde_value(fname: str, group: str, key: str) -> str | None:
    """Read a single KDE config value the way `kreadconfig6` would, but by parsing the INI
    cascade DIRECTLY — never shelling out to kreadconfig6.

    Why: this panel runs as a systemd unit with ProtectHome=read-only, so its $HOME (incl.
    ~/.config) is mounted read-only. kreadconfig6 is a Qt GUI binary that write-locks its own
    ~/.config/kreadconfig6rc even to READ a value; under the read-only-home sandbox that probe
    fails and it pops a BLOCKING "kreadconfig6rc not writable" GUI modal (the service inherits
    the session DISPLAY/WAYLAND_DISPLAY, so it's visible). Probed on every /components.json the
    modal loops on the user's screen. Reading the file ourselves spawns no Qt toolkit — read-only
    home is fine for reads — so the modal is impossible.

    Searches XDG_CONFIG_HOME then each XDG_CONFIG_DIRS entry; highest-precedence hit wins
    (mirrors kreadconfig6's cascade). Returns the value, or None if unset everywhere.
    """
    home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    dirs = (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":")
    want = f"[{group}]"
    for base in [home, *dirs]:
        try:
            text = (Path(base) / fname).read_text(errors="ignore")
        except OSError:
            continue
        in_group = False
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("["):
                in_group = (s == want)
                continue
            if not in_group:
                continue
            eq = s.find("=")
            if eq < 0:
                continue
            # KDE keys may carry a locale/flag suffix: key[$i], key[en_US]. Match the base name.
            if s[:eq].strip().split("[", 1)[0].strip() == key:
                return s[eq + 1:].strip()
    return None


def probe_present(comp: dict) -> bool | None:
    spec = PROBES.get(comp["id"])
    if not spec:
        return None
    kind, arg = spec
    if kind == "unit":
        if not shutil.which("systemctl"):
            return None
        rc, out = _cmd(["systemctl", "--user", "is-enabled", arg])
        word = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if word in _PRESENT_WORDS:
            return True
        if "not-found" in out or word == "":
            return False
        return rc == 0
    if kind == "applet":
        if not shutil.which("kpackagetool6"):
            return None
        rc, out = _cmd(["kpackagetool6", "--type", "Plasma/Applet", "--list"])
        return any(line.strip() == arg for line in out.splitlines())
    if kind == "kwin":
        # Direct INI read (NOT kreadconfig6) — see _read_kde_value: kreadconfig6 pops a
        # "not writable" GUI modal under this unit's ProtectHome=read-only sandbox.
        val = _read_kde_value("kwinrc", "Plugins", f"{arg}Enabled")
        return (val or "").strip().lower() == "true"
    if kind == "kconfig":
        fname, group, key, expect = arg
        val = _read_kde_value(fname, group, key)
        return (val or "").strip() == expect
    if kind == "file":
        return Path(os.path.expanduser(arg)).exists()
    if kind == "file-contains":
        path, needle = arg
        p = Path(os.path.expanduser(path))
        if not p.exists():
            return False
        try:
            return needle in p.read_text(errors="ignore")
        except OSError:
            return None
    if kind == "tailscale":
        if not shutil.which("tailscale"):
            return None
        rc, out = _cmd(["tailscale", "serve", "status"])
        return rc == 0 and bool(out.strip()) and "No serve config" not in out
    return None


def component_state(comp: dict) -> str:
    """adopted | available | needs-you | unknown. `needs-you` = a sudo/manual component that isn't
    installed (must be run by hand, never one-click)."""
    present = probe_present(comp)
    if present is None:
        return "unknown"
    if present:
        return "adopted"
    return "available" if comp["root"] == "no" else "needs-you"


# Each /components.json probes ~17 components (systemctl/kpackagetool6/kreadconfig6 subprocesses).
# Cache the probed states for a few seconds so a polling client doesn't fork a fan-out every tick.
# Keyed on the registry path + mtime so an edit (or a test swapping REGISTRY) busts the cache.
_CAT_TTL = 8.0
_cat: dict = {"t": 0.0, "rows": None, "sig": None}
_cat_lock = threading.Lock()


def _probed_rows() -> list[dict]:
    """Registry rows + the read-only adoption state, memoised (state is origin-independent)."""
    try:
        sig = (str(REGISTRY), REGISTRY.stat().st_mtime)
    except Exception:
        sig = (str(REGISTRY), 0)
    now = time.monotonic()
    # Single-flight: compute the ~17-subprocess fan-out UNDER the lock so a concurrent cold-cache
    # herd (e.g. a polling client) waits on one fan-out instead of each forking its own.
    with _cat_lock:
        if _cat["rows"] is not None and _cat["sig"] == sig and (now - _cat["t"]) <= _CAT_TTL:
            return _cat["rows"]
        rows = [{"id": c["id"], "tier": c["tier"], "default": c["default"], "root": c["root"],
                 "desc": c["desc"], "apply": c["apply"], "state": component_state(c)}
                for c in parse_registry()]
        _cat.update(rows=rows, t=time.monotonic(), sig=sig)
        return rows


def list_components(local: bool) -> list[dict]:
    """The client-facing catalog. `adoptable` (one-click) is true ONLY for a root:no component on a
    provably-local origin. For sudo/manual components a local origin also gets the printed command.
    The origin-independent state is cached; the local-only flags are applied per request."""
    out = []
    for r in _probed_rows():
        row = {"id": r["id"], "name": FRIENDLY_NAMES.get(r["id"], r["id"]),
               "tier": r["tier"], "default": r["default"], "root": r["root"],
               "desc": r["desc"], "state": r["state"],
               "adoptable": bool(local and r["root"] == "no"),
               "removable": r["id"] not in NO_ONECLICK_REMOVE}   # sensitive units: install-only here
        if local and r["root"] != "no":
            row["manual_cmd"] = ("sudo " if r["root"] == "sudo" else "") + f"./{r['apply']}"
        out.append(row)
    return out


# ── the ledger ($XDG_RUNTIME_DIR/agentos-adopt/ledger.json) ──────────────────────────────────
def _ledger_dir() -> Path:
    rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    d = Path(rt) / "agentos-adopt"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)          # 0700 like the dispatch RuntimeDirectory (mkdir honours umask)
    except OSError:
        pass
    return d


def ledger_path() -> Path:
    return _ledger_dir() / "ledger.json"


def log_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "agentos" / "adopt"


def _read_locked(fh) -> dict:
    fh.seek(0)
    raw = fh.read()
    if not raw.strip():
        return {"v": 1, "jobs": {}}
    try:
        d = json.loads(raw)
        if not isinstance(d, dict) or "jobs" not in d:
            return {"v": 1, "jobs": {}}
        return d
    except Exception:
        return {"v": 1, "jobs": {}}


def read_ledger() -> dict:
    try:
        return json.loads(ledger_path().read_text())
    except Exception:
        return {"v": 1, "jobs": {}}


def _mutate_ledger(fn):
    p = ledger_path()
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with os.fdopen(fd, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            data = _read_locked(fh)
            ret = fn(data)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            os.chmod(tmp, 0o600)        # the fresh tmp would land 0644 (umask) and survive os.replace
            os.replace(tmp, p)
            return ret
    except Exception:
        return None


def _now() -> float:
    return time.time()


def _reap(data: dict) -> None:
    """A worker SIGKILLed by RuntimeMaxSec never runs its finally, so its job would sit active
    forever. Reap a stale active job into an honest `failed`."""
    now = _now()
    for e in data.get("jobs", {}).values():
        if e.get("status") in ACTIVE_STATUSES and (now - e.get("updated", 0)) > STALE_ACTIVE_S:
            e["status"] = "failed"
            e["outcome"] = "the adopt worker stopped responding"
            e["updated"] = now


def prune(data: dict, keep_terminal: int = 40, max_age_s: float = 6 * 3600) -> None:
    jobs = data.get("jobs", {})
    term = [(i, e) for i, e in jobs.items() if e.get("status") in TERMINAL_STATUSES]
    term.sort(key=lambda kv: kv[1].get("updated", 0), reverse=True)
    now = _now()
    for n, (jid, e) in enumerate(term):
        if n >= keep_terminal or (now - e.get("updated", 0)) > max_age_s:
            jobs.pop(jid, None)


def _new_job(comp: dict, action: str) -> dict:
    now = _now()
    return {
        "id": secrets.token_hex(8),
        "comp": comp["id"], "tier": comp.get("tier", ""), "action": action,
        "status": "queued", "outcome": "", "log": "",
        "created": now, "updated": now,
    }


def validate(comp_id: str, action: str) -> tuple[dict | None, str]:
    """Admission: kill-switch, known action, real registry row, and root:no (one-click) only.
    Ledger-derived guards (dedupe/cap/cooldown) are enforced atomically in try_create_job."""
    if not adopt_enabled():
        return None, "feature adoption is disabled on this box (AGENTOS_ADOPT=0)"
    if action not in VALID_ACTIONS:
        return None, "unknown action"
    comp = find(comp_id)
    if not comp:
        return None, "unknown component"
    if comp["root"] != "no":
        kind = comp["root"]
        return None, (f"{comp_id} needs a {kind} step — it is printed for you to run, never "
                      "one-click (the panel never escalates)")
    if action == "unadopt" and comp_id in NO_ONECLICK_REMOVE:
        return None, (f"{comp_id} is removed from a terminal, not one-click — taking it down would "
                      "interrupt the VRAM coordinator (and any running dream) or this panel itself")
    return comp, ""


def try_create_job(comp: dict, action: str) -> tuple[dict | None, str]:
    """Atomically (under the ledger flock) re-check dedupe/cap/cooldown AND insert, so two
    near-simultaneous POSTs can't both run the driver on one component."""
    result: dict = {}

    def _txn(data):
        _reap(data)
        prune(data)
        jobs = data.setdefault("jobs", {})
        cid = comp["id"]
        active = [e for e in jobs.values() if e.get("status") in ACTIVE_STATUSES]
        if any(e.get("comp") == cid for e in active):
            result["reason"] = "already working on this component"
            return
        if len(active) >= MAX_ACTIVE:
            result["reason"] = "too many adoptions in flight — try again shortly"
            return
        term = [e for e in jobs.values() if e.get("comp") == cid and e.get("status") in TERMINAL_STATUSES]
        if term and (_now() - max(e.get("updated", 0) for e in term)) < COOLDOWN_S:
            result["reason"] = "just ran — give it a moment before trying again"
            return
        entry = _new_job(comp, action)
        jobs[entry["id"]] = entry
        result["entry"] = entry

    _mutate_ledger(_txn)
    entry = result.get("entry")
    return (entry, "") if entry else (None, result.get("reason", "could not record the job"))


def update_job(jid: str, **fields) -> dict | None:
    def _patch(data):
        e = data.get("jobs", {}).get(jid)
        if not e:
            return None
        e.update(fields)
        e["updated"] = _now()
        return dict(e)

    return _mutate_ledger(_patch)


def claim_job(jid: str) -> bool:
    """Atomic test-and-set: only an unclaimed `queued` job transitions to applying/unadopting, and
    only one caller wins. Returns True if this caller claimed it."""
    res: dict = {}

    def _txn(data):
        e = data.get("jobs", {}).get(jid)
        if not e or e.get("status") != "queued":
            res["ok"] = False
            return
        e["status"] = "unadopting" if e.get("action") == "unadopt" else "applying"
        e["updated"] = _now()
        res["ok"] = True

    _mutate_ledger(_txn)
    return bool(res.get("ok"))


def public_job(e: dict, local: bool) -> dict:
    """Client-facing subset. A stale active job reads as failed. The durable-log PATH is local-only."""
    status, outcome = e.get("status"), e.get("outcome", "")
    if status in ACTIVE_STATUSES and (_now() - e.get("updated", 0)) > STALE_ACTIVE_S:
        status, outcome = "failed", "the adopt worker stopped responding"
    out = {"id": e.get("id"), "comp": e.get("comp"), "action": e.get("action"),
           "status": status, "outcome": outcome, "updated": e.get("updated")}
    if local and e.get("log"):
        out["has_log"] = True
    return out


def spawn_worker(jid: str) -> tuple[bool, str]:
    """Launch the worker for a job as a transient `systemd-run --user` unit — OUTSIDE the panel's
    sandbox. The job id (server-minted hex) is the only argument; the component + action are read
    from the ledger by the worker. Honest failure: never silently run a sandboxed child."""
    if not shutil.which("systemd-run"):
        return False, "systemd-run not available — cannot launch the adopt worker"
    home = os.path.expanduser("~")
    path = f"{home}/.local/bin:" + os.environ.get("PATH", "/usr/bin:/bin")
    cmd = [
        "systemd-run", "--user", "--collect", "--quiet",
        "--unit", f"agentos-adopt-{jid}",
        "--description", f"AgentOS adopt worker ({jid})",
        f"--property=RuntimeMaxSec={WORKER_TIMEOUT_S}",
        "--expand-environment=no",
        "--setenv", f"PATH={path}",
    ]
    # apply.sh/install.sh read $HOME + the XDG dirs heavily; pass them through explicitly.
    for k in ("HOME", "XDG_RUNTIME_DIR", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
              "XDG_CACHE_HOME", "AGENTOS_ADOPT_LOG_TTL_H"):
        v = os.environ.get(k)
        if v:
            cmd += ["--setenv", f"{k}={v}"]
    cmd += ["/usr/bin/python3", str(WORKER), jid]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    except Exception as e:
        return False, f"could not launch worker: {e}"
    if r.returncode != 0:
        return False, f"systemd-run failed: {(r.stderr or r.stdout or '').strip()[:200]}"
    return True, ""


_HEXID = re.compile(r"^[0-9a-f]{16}$")           # ids are token_hex(8) = 16 hex


def valid_job_id(s: str) -> bool:
    return bool(_HEXID.match(s or ""))

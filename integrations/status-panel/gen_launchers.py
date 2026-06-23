#!/usr/bin/env python3
"""Generate Plasma/KRunner `.desktop` launchers for the AgentOS service family (ADR-0031).

The launch surface is split by device: the phone gets the read-only PWA launch view
(launch.html, served by status_panel.py); the *desktop* gets KRunner — which indexes
`.desktop` entries for free, so "launch" needs no new app there, just entries. This emits one
launcher per *door* (a catalog service with a `url`) so typing the service name (or "agentos")
in KRunner opens it via `xdg-open`. Monitor-only services (no url) get no launcher — you can't
"open" a daemon. Deterministic + reversible: apply.sh installs these, restore.sh removes them.

  python3 gen_launchers.py            # dry-run: print what would be written
  python3 gen_launchers.py --install  # write to ~/.local/share/applications
  python3 gen_launchers.py --remove   # remove the ones we wrote (agentos-launch-*.desktop)
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG_PATH = HERE / "services.json"
DEFAULT_ICON = HERE / "icons" / "icon-192.png"
APPS_DIR = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "applications"
PREFIX = "agentos-launch-"           # our namespace — restore globs this to remove cleanly
DISPATCH_HELPER = HERE / "dispatch_launch.sh"        # the ONE non-URL launcher target (ADR-0039)
DISPATCH_FILE = f"{PREFIX}dispatch.desktop"

_URL_RE = re.compile(r"^https?://[^\s;]+$")          # trusted catalog, but keep Exec injection-free
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")       # safe filename component


def _val(s: str) -> str:
    """A `.desktop` value: single line (strip CR/LF), no leading/trailing space."""
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _exec_url(url: str) -> str:
    """Escape a URL for an Exec= field: `%` is reserved (field codes), so double it. Our URLs
    have none, but spec-correctness keeps a future catalog entry from silently misbehaving."""
    return url.replace("%", "%%")


def desktop_entries(catalog: dict, icon: str = "applications-internet") -> dict:
    """Pure: catalog → {filename: file-contents}. Only services with a valid http(s) `url` become
    launchers; everything else (daemons, feeds, watchers) is monitor-only and gets none."""
    out: dict[str, str] = {}
    for svc in catalog.get("services", []):
        sid = svc.get("id", "")
        url = svc.get("url", "")
        if not _ID_RE.match(sid) or not _URL_RE.match(url or ""):
            continue
        name = _val(svc.get("name") or sid)
        desc = _val(svc.get("desc"))
        comment = f"{desc} — open in your browser (AgentOS)" if desc else "Open in your browser (AgentOS)"
        # Keywords let KRunner surface the whole family when you type "agentos".
        keywords = f"agentos;atrium;{sid};"
        body = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={name}\n"
            "GenericName=AgentOS service\n"
            f"Comment={_val(comment)}\n"
            f"Exec=xdg-open {_exec_url(url)}\n"
            f"Icon={icon}\n"
            "Terminal=false\n"
            "Categories=Network;Utility;\n"
            f"Keywords={keywords}\n"
            "StartupNotify=false\n"
            "NoDisplay=false\n"
            "X-AgentOS-Launch=true\n"
        )
        out[f"{PREFIX}{sid}.desktop"] = body
    return out


def dispatch_entry(icon: str = "system-run") -> dict:
    """The ONE launcher whose Exec is a fixed SCRIPT, not a catalog url (ADR-0039 dispatch-from-KRunner).
    CONSTANT emitter: the absolute helper path is resolved here and never interpolated from catalog
    data, so gen_launchers' Exec-injection-free guarantee still holds for this non-URL entry. The helper
    is invoked as `bash <abs>` so it stays 0644 — a flipped exec bit can't turn it into a direct-exec
    foothold. The dispatch itself is Hermes-only + consent-gated (see dispatch_launch.sh)."""
    body = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=AgentOS: dispatch a fix\n"
        "GenericName=AgentOS service repair\n"
        "Comment=Dispatch a local Hermes agent to investigate and fix a down AgentOS service\n"
        f"Exec=/usr/bin/env bash {shlex.quote(str(DISPATCH_HELPER))}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "Keywords=agentos;dispatch;fix;repair;hermes;\n"
        "StartupNotify=false\n"
        "NoDisplay=false\n"               # KRunner-reachable (type "dispatch"/"agentos")
        "X-AgentOS-Launch=true\n"
    )
    return {DISPATCH_FILE: body}


def _helper_safe() -> bool:
    """Emit the dispatch launcher ONLY if its helper exists, is a regular file, is 0644, and is owned
    by us — so the Exec target is exactly the file we shipped, never a swapped-in or world-writable one."""
    try:
        st = DISPATCH_HELPER.stat()
    except OSError:
        return False
    return (DISPATCH_HELPER.is_file()
            and (st.st_mode & 0o777) == 0o644
            and st.st_uid == os.getuid())


def _all_entries(icon: str) -> dict:
    """The full launcher set: URL doors + (when its helper is safe) the one dispatch launcher."""
    entries = desktop_entries(_load_catalog(), icon=icon)
    if _helper_safe():
        entries.update(dispatch_entry())
    return entries


def _load_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text())


MARKER = "X-AgentOS-Launch=true"


def _ours(path: Path) -> bool:
    """A launcher this tool authored carries the MARKER line. Provenance-gate every delete so a
    user-authored `agentos-launch-*.desktop` (same namespace, no marker) is never removed —
    apply must only ever undo what apply wrote (reversibility)."""
    try:
        return MARKER in path.read_text()
    except OSError:
        return False


def install(apps_dir: Path = APPS_DIR, icon: str | None = None) -> list[str]:
    icon = icon or (str(DEFAULT_ICON) if DEFAULT_ICON.exists() else "applications-internet")
    entries = _all_entries(icon)
    apps_dir.mkdir(parents=True, exist_ok=True)
    # Write new/updated launchers FIRST, atomically (temp + os.replace) — a crash never leaves a
    # torn .desktop KRunner would index as garbage, nor fewer launchers than we started with.
    written = []
    for fname, body in entries.items():
        tmp = apps_dir / (fname + ".tmp")
        tmp.write_text(body)
        os.replace(tmp, apps_dir / fname)
        written.append(fname)
    # THEN prune launchers WE wrote previously that are no longer in the catalog. Provenance-gated:
    # a same-named file without our marker (user-authored) is left untouched.
    for old in apps_dir.glob(f"{PREFIX}*.desktop"):
        if old.name not in entries and _ours(old):
            old.unlink()
    _refresh(apps_dir)
    return written


def remove(apps_dir: Path = APPS_DIR) -> list[str]:
    removed = []
    for f in apps_dir.glob(f"{PREFIX}*.desktop"):
        if _ours(f):                          # only undo what apply wrote
            f.unlink()
            removed.append(f.name)
    _refresh(apps_dir)
    return removed


def _refresh(apps_dir: Path) -> None:
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(apps_dir)],
                       check=False, capture_output=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--install" in argv:
        w = install()
        print(f"✓ installed {len(w)} AgentOS launcher(s) in {APPS_DIR}")
        for f in w:
            print(f"  {f}")
    elif "--remove" in argv:
        r = remove()
        print(f"✓ removed {len(r)} AgentOS launcher(s) from {APPS_DIR}")
    else:
        for fname, body in _all_entries(str(DEFAULT_ICON)).items():
            print(f"--- {fname} ---\n{body}")


if __name__ == "__main__":
    main()

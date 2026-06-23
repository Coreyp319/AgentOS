#!/usr/bin/env python3
"""css-tx.py — a reversible apply/checkpoint/revert transaction for a Union CSS style.

The manual-loop safety rail for AgentOS UI theming (see SKILL.md). You edit the CSS
working tree freely (`~/.local/share/union/css/styles/<style>/`); this tool gives those
edits the same honesty net the kde-plasma-customization audit has for kdeglobals
(ADR-0034 backup_once/do_revert), with one addition that fits "model proposes, code
disposes": the DETERMINISTIC GATE is `union-ruleinspector` — a CSS state that does not
parse/load (Union throws) can NOT be checkpointed. So a known-good checkpoint is always
a *validated* state, and revert always lands on one.

    css-tx.py status               # is the working tree valid? does it differ from the last checkpoint?
    css-tx.py diff                 # unified diff: working tree vs last checkpoint
    css-tx.py verify               # run the ruleinspector gate only (no mutation)
    css-tx.py checkpoint [--note]  # validate → snapshot working tree + ledger entry; REFUSE if invalid
    css-tx.py revert [--to RUN]    # restore working tree from the latest (or named) checkpoint
    css-tx.py history              # list checkpoints (run id, time, note, files changed)

Style defaults to $UNION_STYLE_NAME or 'aurora'. Pure stdlib. Checkpoints + the
append-only ledger live in `<data-root>/css/.tx/`. A checkpoint snapshots BOTH the style
dir and the shared `defaults/` (Union resolves defaults/ relative to the user data root,
and a missing defaults/ makes Union throw — so it is part of the restorable unit).
"""
from __future__ import annotations
import argparse, difflib, json, os, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

# ---- paths ----------------------------------------------------------------
DATA = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share"))
ROOT = DATA / "union" / "css"               # the Union user data root
STYLES = ROOT / "styles"
DEFAULTS = ROOT / "defaults"
TX = ROOT / ".tx"                            # our checkpoints + ledger
LEDGER = TX / "ledger.json"
RULEINSPECTOR = shutil.which("union-ruleinspector") or "union-ruleinspector"

# Widget types that exercise the main CSS files (button/check/popup/bars/text). Any
# parse/load error in the style makes Union THROW before matching, so these double as a
# load-probe. We only FAIL on a load-throw, never on "no rule matched" (rc=0).
PROBE_TYPES = ["Button", "CheckBox", "ComboBox", "Menu", "ScrollBar", "TextField"]
THROW_MARKERS = ("terminate called", "cxxbridge", "panicked", "IO Error", "No such file or directory")


def style_dir(style: str) -> Path:
    return STYLES / style


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- the deterministic gate ----------------------------------------------
def validate(style: str) -> tuple[bool, dict]:
    """Run union-ruleinspector against `style` (cache-bypassed). OK iff every probe
    loads without Union throwing. Returns (ok, detail). This is the code-disposes gate."""
    if not style_dir(style).is_dir():
        return False, {"error": f"style dir not found: {style_dir(style)}"}
    if not DEFAULTS.is_dir():
        return False, {"error": f"defaults/ missing at {DEFAULTS} — Union will throw on load; "
                                f"`cp -r /usr/share/union/css/defaults {DEFAULTS}`"}
    env = {**os.environ, "UNION_STYLE_NAME": style, "UNION_DISABLE_STYLE_CACHE": "1"}
    failures = []
    for t in PROBE_TYPES:
        try:
            p = subprocess.run([RULEINSPECTOR, "--style", "org.kde.union", "--type", t],
                               env=env, capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            return False, {"error": f"union-ruleinspector not found ({RULEINSPECTOR})"}
        except subprocess.TimeoutExpired:
            failures.append({"type": t, "why": "timeout"}); continue
        blob = (p.stdout or "") + (p.stderr or "")
        threw = any(m in blob for m in THROW_MARKERS)
        if p.returncode != 0 or threw:
            # first line of the throw is the useful bit
            msg = next((ln for ln in blob.splitlines() if any(m in ln for m in THROW_MARKERS)),
                       blob.strip().splitlines()[-1] if blob.strip() else f"rc={p.returncode}")
            failures.append({"type": t, "rc": p.returncode, "why": msg[:200]})
    ok = not failures
    return ok, {"ok": ok, "probes": len(PROBE_TYPES), "failures": failures}


# ---- snapshots ------------------------------------------------------------
def _snapshot_into(dst: Path, style: str):
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(style_dir(style), dst / "style", dirs_exist_ok=True)
    if DEFAULTS.is_dir():
        shutil.copytree(DEFAULTS, dst / "defaults", dirs_exist_ok=True)


def _restore_from(src: Path, style: str):
    sd = style_dir(style)
    if (src / "style").is_dir():
        shutil.rmtree(sd, ignore_errors=True)
        shutil.copytree(src / "style", sd)
    if (src / "defaults").is_dir():
        shutil.rmtree(DEFAULTS, ignore_errors=True)
        shutil.copytree(src / "defaults", DEFAULTS)


def _files(p: Path) -> dict[str, str]:
    out = {}
    if not p.is_dir():
        return out
    for f in sorted(p.rglob("*")):
        if f.is_file():
            try:
                out[str(f.relative_to(p))] = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                out[str(f.relative_to(p))] = "<binary>"
    return out


def _tree_diff(old: Path, new: Path) -> tuple[list[str], str]:
    """Unified diff of two 'style' subtrees. Returns (changed_files, unified_text)."""
    a, b = _files(old), _files(new)
    changed, chunks = [], []
    for rel in sorted(set(a) | set(b)):
        av, bv = a.get(rel, ""), b.get(rel, "")
        if av == bv:
            continue
        changed.append(rel)
        chunks.extend(difflib.unified_diff(av.splitlines(), bv.splitlines(),
                                            fromfile=f"checkpoint/{rel}", tofile=f"working/{rel}", lineterm=""))
    return changed, "\n".join(chunks)


# ---- ledger ---------------------------------------------------------------
def _ledger() -> list[dict]:
    if LEDGER.exists():
        try:
            return json.loads(LEDGER.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _append(entry: dict):
    TX.mkdir(parents=True, exist_ok=True)
    log = _ledger()
    log.append(entry)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(log, indent=2), encoding="utf-8")
    os.replace(tmp, LEDGER)


def _checkpoints(style: str) -> list[dict]:
    return [e for e in _ledger() if e.get("action") == "checkpoint" and e.get("style") == style]


def _latest(style: str) -> dict | None:
    cps = _checkpoints(style)
    return cps[-1] if cps else None


# ---- commands -------------------------------------------------------------
def cmd_verify(style: str, as_json: bool):
    ok, detail = validate(style)
    if as_json:
        print(json.dumps(detail, indent=2)); return 0 if ok else 1
    if ok:
        print(f"✓ {style}: valid — {detail['probes']} probes loaded clean (union-ruleinspector gate passed)")
    else:
        print(f"✗ {style}: INVALID — Union would throw on load:")
        for f in detail.get("failures", [{"why": detail.get("error")}]):
            print(f"    {f.get('type','?')}: {f.get('why')}")
    return 0 if ok else 1


def cmd_status(style: str, as_json: bool):
    ok, detail = validate(style)
    last = _latest(style)
    changed, _ = ([], "") if not last else _tree_diff(TX / last["run_id"] / "style", style_dir(style))
    info = {"style": style, "valid": ok, "last_checkpoint": last["run_id"] if last else None,
            "dirty": bool(changed), "changed_files": changed,
            "active_target": os.environ.get("UNION_STYLE_NAME") == style,
            "validation": detail}
    if as_json:
        print(json.dumps(info, indent=2)); return 0
    g = "✓ valid" if ok else "✗ INVALID (cannot checkpoint until fixed)"
    print(f"style:           {style}   [{g}]")
    print(f"last checkpoint: {last['run_id'] if last else '(none yet)'}"
          + (f"  — {last.get('note','')}" if last and last.get('note') else ""))
    if not last:
        print("working tree:    no checkpoint yet — run `checkpoint` to bless the current state")
    elif changed:
        print(f"working tree:    DIRTY — {len(changed)} file(s) changed since last checkpoint: {', '.join(changed)}")
        print("                 (`diff` to see, `checkpoint` to save, `revert` to discard)")
    else:
        print("working tree:    clean — matches the last checkpoint")
    return 0


def cmd_diff(style: str):
    last = _latest(style)
    if not last:
        print("no checkpoint yet — nothing to diff against"); return 0
    changed, text = _tree_diff(TX / last["run_id"] / "style", style_dir(style))
    if not changed:
        print("clean — working tree matches the last checkpoint"); return 0
    print(text); return 0


def cmd_checkpoint(style: str, note: str, as_json: bool):
    ok, detail = validate(style)
    if not ok:
        out = {"checkpointed": False, "reason": "validation failed (code disposes)", "validation": detail}
        print(json.dumps(out, indent=2) if as_json else
              "✗ REFUSED — the working tree does not load (Union would throw). Fix it first:\n    " +
              "\n    ".join(f"{f.get('type','?')}: {f.get('why')}" for f in detail.get("failures", [])))
        return 1
    last = _latest(style)
    changed, _ = ([], "") if not last else _tree_diff(TX / last["run_id"] / "style", style_dir(style))
    if last and not changed:
        msg = {"checkpointed": False, "reason": "no changes since last checkpoint", "run_id": last["run_id"]}
        print(json.dumps(msg, indent=2) if as_json else
              f"nothing to checkpoint — clean since {last['run_id']}")
        return 0
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    _snapshot_into(TX / run_id, style)
    entry = {"run_id": run_id, "ts": _now(), "action": "checkpoint", "style": style,
             "note": note or "", "changed_files": changed if last else ["(initial)"],
             "validated": True, "probes": detail.get("probes")}
    _append(entry)
    out = {"checkpointed": True, "run_id": run_id, "changed_files": entry["changed_files"]}
    print(json.dumps(out, indent=2) if as_json else
          f"✓ checkpoint {run_id} — validated, snapshot saved"
          + (f"; changed: {', '.join(changed)}" if last else " (initial baseline)"))
    return 0


def cmd_revert(style: str, to: str | None, as_json: bool):
    cps = _checkpoints(style)
    if not cps:
        print(json.dumps({"reverted": False, "reason": "no checkpoints"}) if as_json else
              "no checkpoints to revert to"); return 1
    target = next((c for c in reversed(cps) if c["run_id"] == to), None) if to else cps[-1]
    if not target:
        print(json.dumps({"reverted": False, "reason": f"no checkpoint {to}"}) if as_json else
              f"no checkpoint '{to}' for {style}"); return 1
    src = TX / target["run_id"]
    if not src.is_dir():
        print(f"checkpoint snapshot missing on disk: {src}"); return 1
    _restore_from(src, style)
    ok, _ = validate(style)   # honesty: confirm the restored state loads
    _append({"run_id": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"), "ts": _now(),
             "action": "revert", "style": style, "reverted_to": target["run_id"], "valid_after": ok})
    print(json.dumps({"reverted": True, "to": target["run_id"], "valid_after": ok}, indent=2) if as_json else
          f"✓ reverted {style} → checkpoint {target['run_id']}"
          + ("  (restored state validates ✓)" if ok else "  ⚠ restored state does NOT validate — investigate"))
    return 0


def cmd_history(style: str, as_json: bool):
    log = [e for e in _ledger() if e.get("style") == style]
    if as_json:
        print(json.dumps(log, indent=2)); return 0
    if not log:
        print(f"no history for {style}"); return 0
    for e in log:
        if e["action"] == "checkpoint":
            cf = e.get("changed_files", [])
            print(f"  {e['run_id']}  checkpoint  [{len(cf)} file(s)]  {e.get('note','')}".rstrip())
        else:
            print(f"  {e['run_id']}  revert→{e.get('reverted_to')}  valid={e.get('valid_after')}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Reversible apply/checkpoint/revert tx for a Union CSS style")
    ap.add_argument("--style", default=os.environ.get("UNION_STYLE_NAME") or "aurora",
                    help="Union style name (default: $UNION_STYLE_NAME or aurora)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status"); sub.add_parser("diff"); sub.add_parser("verify"); sub.add_parser("history")
    c = sub.add_parser("checkpoint"); c.add_argument("--note", default="")
    r = sub.add_parser("revert"); r.add_argument("--to", default=None, help="checkpoint run id (default: latest)")
    a = ap.parse_args()
    fn = {
        "verify": lambda: cmd_verify(a.style, a.json),
        "status": lambda: cmd_status(a.style, a.json),
        "diff": lambda: cmd_diff(a.style),
        "checkpoint": lambda: cmd_checkpoint(a.style, a.note, a.json),
        "revert": lambda: cmd_revert(a.style, a.to, a.json),
        "history": lambda: cmd_history(a.style, a.json),
    }[a.cmd]
    sys.exit(fn())


if __name__ == "__main__":
    main()

# 0009 — Lucid: recovered session history (transcripts lost)

> Forensic reconstruction from `~/.claude/file-history/` only. Three Claude Code sessions on
> 2026-06-16 did the lucid + adjacent work; **their conversation transcripts were lost** (deleted
> from `~/.claude/projects/`, ext4 with no snapshots — unrecoverable). This note reconstructs what
> they *did* from the surviving file-history versions + git, so the work has a readable trail beside
> [ADR-0014](../adr/0014-lucid-interactive-branching-dream-loop.md). Anchored on file mtimes and
> byte-identical diffs against the working tree, not on the lost dialogue.

## Hash → file map (file-history is content-addressed)

| session | file |
|---|---|
| `8b7d80d7` | `docs/adr/0014-…md`, `spikes/dreaming/lucid/{lucid_engine,lucid_panel}.py`, `README.md` |
| `a2e2c45b` | `spikes/keyhole/contents/ui/{main,CompactRepresentation}.qml` |
| `99c3e4e0` | `spikes/dreaming/lucid/lucid_engine.py` (the debug pass) |

## Session A — `8b7d80d7` (~10:09) — authored ADR-0014 + the whole spike
Created, in one save point, all four lucid artifacts byte-identical to today's tree (except the
engine, later edited): **ADR-0014** (Proposed; extends ADR-0009 Surface B, inherits its consent /
opt-in / no-auto-open / red-line constraints), **`lucid_engine.py`** (252-line throwaway engine,
runs generation directly with no lease), **`lucid_panel.py`** (180-line stdlib HTTP panel), and
**`README.md`**. Also did an unrelated ~09:00 secret-hygiene chore on `~/enh-dl*.sh` (replaced a
hardcoded Civitai token with `secret-tool lookup` + fail-fast guard).

## "Session B" `a2e2c45b` (~09:47–10:59) — keyhole, NOT lucid
**Correction to the working assumption:** this session debugged the **keyhole tray plasmoid**
(ADR-0012), not lucid. It produced the three tray-activation fixes now uncommitted in `git status`
(`spikes/keyhole/contents/ui/*.qml`): (1) `feedPath` `file://` scheme strip so the `cat` backend
reads the runtime file; (2) make `FullRepresentation` the popup root (the `ColumnLayout` wrapper
broke the system-tray popup); (3) remove a compact `MouseArea` that swallowed the tray's
activation click.

## Session C — `99c3e4e0` (~10:52–11:05) — the real lucid-engine debug
Opened `lucid_engine.py` at Session A's state and made the two fixes that are today's uncommitted
`M spikes/dreaming/lucid/lucid_engine.py` (now committed alongside this note):
1. **`DEFAULT_LEN` 49 → 33** — align the engine default with the length baked into the ComfyUI
   workflow; "~2s portrait @16fps, under the VRAM-thrash line."
2. **`VHS_VideoCombine` `filename_prefix` fix** — ComfyUI expands `%date%` tokens only in its UI
   frontend; submitted via the API they're literal, producing a dir named `%date:…%`. Route clips
   to `output/lucid/<parent-anchor>` instead.

## Caveat
file-history kept only end-of-session snapshots (e.g. only `@v2` for A's lucid files), so
intra-session edit granularity for the original authoring is not recoverable — only the net result.

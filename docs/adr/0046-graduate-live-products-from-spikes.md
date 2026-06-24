# ADR-0046: Graduate live products out of `spikes/` — an `apps/` home for what shipped

- Status: **Proposed.** The census is done and the safe disk purge is executed (see §1); the directory
  move itself is **not** executed — it touches running services and a rebuilt `agentosd`, so it waits on
  an explicit go-ahead. This ADR is the plan of record so the move, when done, is mechanical and
  reversible rather than improvised.
- Date: 2026-06-23
- Deciders: Corey (asked for the census → graduation plan), forensic census of all 15 spike directories
  (deployment wiring, ADR cross-refs, git history, live process table, tracked-vs-on-disk bytes).
- Relates to / reaffirms: **ADR-0001** (substrate not orchestrator — the products under `spikes/` are
  consumers of the substrate, not the substrate; they deserve a real home but stay out of `crates/`),
  the CLAUDE.md conventions (`spikes/` is "throwaway and excluded from the Cargo workspace" — this ADR
  makes that statement *true again* by moving the things that violate it), and every ADR whose feature
  lives under `spikes/dreaming` (0014/0015/0016/0017/0019/0024/0025/0027/0028/0032/0033/0036/0037/
  0040/0044/0045) — none of those decisions change; only the path on disk does.

---

## 1. Context — "throwaway" is now load-bearing

CLAUDE.md says `spikes/` is **throwaway and excluded from the Cargo workspace**. A classic spike answers
one question, becomes an ADR, and dies. That contract held for the early Rust probes. It no longer
describes reality:

- **`spikes/` is 486 MB on disk but only 17 MB tracked in git.** 96 % of the weight was gitignored
  caches/renders/venvs. The safe purge in §1.1 reclaimed it.
- **Two spikes have become live products that the installed system executes by path.** Installed
  systemd user units `ExecStart` *directly into* `spikes/`:
  - `agentos-lucid.service` → `spikes/dreaming/lucid/lucid_web.py` (running, :8765)
  - `agentos-share.service` → `spikes/dreaming/lucid/lucid_share.py` (running, :8770)
  - `crates/agentosd/src/lease.rs` hardcodes the **absolute** path
    `…/spikes/dreaming/start-comfyui.sh` and SIGKILL-spawns it under a VRAM lease.
- The blunt consequence: **a literal `rm -rf spikes/` would take down Lucid, the Share hub, and the
  ComfyUI launcher.** The most-used parts of AgentOS sit in a tree the docs invite you to delete.

This is not a bug today — nothing deletes `spikes/`. It is a structural mislabel: live product, hard
runtime deps, and an implicit "delete me" sign on the same door. The fix is to move what shipped into a
home that *means* shipped, and restore `spikes/` to meaning what the docs say.

### 1.1 Already done (reversible, zero repo impact)

The safe disk purge ran first (all targets confirmed untracked/gitignored, all regenerable):

| Reclaimed | Path | Regenerate via |
|---|---|---|
| 206 MB | `dreaming/lucid/web/node_modules` | `npm ci` |
| 46 MB | `dreaming/lucid/web/.ds-sync` | design-sync tool |
| 131 MB | `creative-env/out/{frames,graded}` | `creative-env` run script (`out/move.mp4`, `scene.blend`, refs **kept**) |
| 31 MB | `kwin-mcp/.venv` | `uv`/`pip` (source clone + `kwin-mcp-FINDINGS.md` **kept**) |
| 31 MB | `ue-probe/ue_build.log` | UE rebuild (distilled findings live in `cvar_ladder.md`) |
| 6.5 MB | `atrium/out` | atrium harness (code already in `integrations/status-panel/`) |
| ~4 MB | all `spikes/**/__pycache__` | Python import |

Result: **486 MB → 32 MB**, `git status` clean.

### 1.2 Census verdict per spike (the basis for what moves)

| Spike | Class | Live runtime dep? |
|---|---|---|
| **dreaming** | 🟢 LIVE PRODUCT (mis-filed) | **Yes** — 2 services + `lease.rs` |
| **ue-probe** | 🟢 Active workspace + shipped scene | Soft only (a comment + a skill + an allow-rule) |
| keyhole, atrium, window-drag-wind, ambient-backdrop, aurora-accent, creative-env, hills-reactive, dream-as-texture, video-wallpaper | 🔵/🟡 Graduated-reference / design-source | No — code already in `crates/`/`integrations/`; only comments & docs cite them |
| proxy-fidelity, mcp-session-granularity | ⚪ Cited reference artifacts | No (kept — see note) |
| kwin-mcp | ⚫ Vendored external (gitignored) | No |

> Census correction: proxy-fidelity and mcp-session-granularity were first flagged "dead, safe to
> delete." The reference audit found proxy-fidelity is cited **by line number** (`src/main.rs:49-80`)
> across ~12 reviewer-agent personas and is the **reference implementation of the proxy that is not yet
> built** (`crates/` has no proxy); mcp-session-granularity is the named verification artifact for
> ADR-0021. Both are tiny (52 KB combined) and **stay** — they are records, not dead weight.

---

## 2. Decision

1. **Create `apps/` as the home for AgentOS products that ship and run** (Python/QML/web — *not* Rust,
   so they remain outside the Cargo workspace by nature; `crates/` stays the only workspace member).
2. **Move `spikes/dreaming/` → `apps/dreaming/`** as a pure path-prefix change. The internal layout
   (`lucid/`, `comfy_client.py`, `start-comfyui.sh`, `workflows/`, …) is preserved, so every reference
   is a mechanical `spikes/dreaming/…` → `apps/dreaming/…` swap. **This is the only urgent phase** — it
   is the only spike with live runtime deps.
3. **(Phase 2, optional) Move `spikes/ue-probe/` → `studio/ue-wallpaper/`** — it is an active authoring
   workspace + a shipped scene, not a probe. Its hard-dep surface is near-zero, so this is low-risk but
   also low-urgency.
4. **Redefine `spikes/`** in CLAUDE.md to mean *throwaway probes and their historical records*, and add
   `spikes/README.md` classifying each remaining entry (probe / graduated-reference / vendored). Nothing
   else moves; the graduated-references stay put as records, now honestly labelled.
5. **Fix-while-you're-there:** `lease.rs` hardcodes a machine-specific **absolute** path. The move is the
   moment to replace it with an `$AGENTOS_REPO`/XDG-resolved or config-driven lookup so the coordinator
   is not pinned to `/home/corey/Documents/AgentOS`. (Determinism/resource reviewers would flag this.)

---

## 3. Migration mechanics (Phase 1: `dreaming` → `apps/dreaming`)

### 3.1 The exact reference checklist (from the full audit)

**HARD — breaks the running system until updated:**

| File | What to change | Follow-up |
|---|---|---|
| `crates/agentosd/src/lease.rs:92, :1947` | absolute `start-comfyui.sh` path (also do §2.5 fix) | **rebuild + reinstall `agentosd`** |
| `integrations/lucid/agentos-lucid.service:10,14` | `ExecStart`/`ExecStop` path | reinstall unit + `daemon-reload` + restart |
| `integrations/share/agentos-share.service:11` | `ExecStart` path | reinstall unit + `daemon-reload` + restart |
| `integrations/lucid/apply.sh:15`, `dev.sh:13` | `web`/`lucid_web.py` paths | — |
| `integrations/lucid-drain/apply.sh:9,11` | `dist` + `lucid_drain.py` paths | re-run apply.sh |
| `integrations/create-video/apply.sh:8` | `LUCID_DIR` | re-run apply.sh |
| `integrations/browser-create-video/apply.sh:10` | `LAUNCHER` | re-run apply.sh |
| `integrations/design/make_pwa_icons.py:257,258,264` | `web/{public,dist}`, `share_assets` | — |
| `integrations/blender/phase0-render.sh:100` | printed instruction path | — |

**SOFT — stale text only, non-breaking (sweep after):** comment lines in `comfyui.service:17`,
`status-panel/status_panel.py:282`, `status-panel/tests/test_launch.py:2`, `keyhole/apply.sh:26`;
`models/registry.json:83` note; doc-comments in `crates/.../{governor,keyhole,wind}.rs`; and **251
documentation citations** (84 in `.claude/agents/*.md`, 167 other `.md`) — all `sed`-able in one pass.

### 3.2 Sequence (no broken window)

The running Python processes hold their code in memory, so the on-disk move does not kill them — but a
restart would fail until paths are fixed. Therefore: **edit everything first, restart last.**

1. `git mv spikes/dreaming apps/dreaming`.
2. Edit all HARD refs (§3.1) + apply the §2.5 `lease.rs` path fix.
3. `cargo build -p agentosd` → `crates/agentosd/dist/apply.sh` (reinstall the binary the coordinator runs).
4. Re-run the touched integrations' `apply.sh` (lucid, share, lucid-drain, create-video,
   browser-create-video) → `systemctl --user daemon-reload`.
5. `systemctl --user restart agentos-lease agentos-lucid agentos-share`.
6. **Verify:** Lucid answers on :8765; Share on :8770; trigger a lease `Spawn` and confirm the
   coordinator finds `apps/dreaming/start-comfyui.sh`; `agentosd keyhole` still reads.
7. Sweep the SOFT refs + 251 doc citations (`sed`), update CLAUDE.md (§4), add `spikes/README.md`.

### 3.3 Reversibility

Everything is `git mv` + edits → `git revert` restores the tree; re-running the *old* `apply.sh` repoints
the units back. The only non-instant cost is one `agentosd` rebuild. No data is destroyed (the §1.1 purge
already happened independently and touched nothing tracked).

---

## 4. Consequences

- **(+) `rm -rf spikes/` is safe again** — no production lives there. CLAUDE.md's claim becomes true.
- **(+) Honest taxonomy:** `apps/` = ships and runs; `spikes/` = probes + records; `crates/` = the Rust
  substrate. New contributors stop mistaking a product for a throwaway (a real failure mode — see the
  rater-feasibility persona's "a plan citing `spikes/` as if it were `crates/`").
- **(+) Latent portability bug fixed** as a side effect (the hardcoded absolute `lease.rs` path).
- **(−) One-time churn:** a rebuild, ~6 service reinstalls, and a 251-line doc `sed`. Bounded and scripted.
- **(−) `apps/` is non-Rust**, so it gets no test/build coverage from the Cargo workspace — same as
  `spikes/` today; the products carry their own Python test suites (already the case).
- **Neutral:** the Cargo `exclude = ["spikes"]` is unaffected — it exists because `proxy-fidelity` has a
  `Cargo.toml` (and stays in `spikes/`); `apps/dreaming` has no `Cargo.toml`, so Cargo ignores it.

---

## 5. What stays in `spikes/` (and the new contract)

`spikes/README.md` (added in Phase 1, step 7) will state: *`spikes/` holds throwaway probes and the
historical record of probes whose lesson graduated elsewhere. Nothing here is executed by an installed
service. If you build something here that ships, it moves to `apps/` (deployable) or `studio/`
(authoring) — see ADR-0046.* It will then classify each remaining entry:

- **Cited reference artifacts:** `proxy-fidelity` (proxy ref-impl, unbuilt), `mcp-session-granularity`
  (ADR-0021 verification), `dream-as-texture`, `video-wallpaper` (ADR-0009 proofs).
- **Graduated — code lives elsewhere, kept as record:** `atrium`→`integrations/status-panel`,
  `window-drag-wind`→`crates/.../wind.rs`, `ambient-backdrop`→keyhole/panel Flow look,
  `aurora-accent`→`integrations/aurora-theme/tools`, `creative-env`→superseded by ue-wallpaper,
  `hills-reactive` (ADR-0008/0009 grammar), `keyhole` (validation harness; live plasmoid in
  `integrations/keyhole`).
- **Vendored external (gitignored):** `kwin-mcp` + `kwin-mcp-FINDINGS.md`.
- **Open question for Corey:** `windable-grass` — the single largest *tracked* item in `spikes/` (8.4 MB
  of committed PNGs) with **no ADR and no integration reference**. Keep as an aesthetic record, archive,
  or delete? (Not decided here; flagged for a call.)

---

## 6. Alternatives considered

- **Leave it; just fix CLAUDE.md to admit `spikes/` holds products.** Rejected: documents the smell
  instead of removing it; `rm -rf spikes/` stays a footgun and the "is this a product?" ambiguity remains.
- **Move into `crates/`.** Rejected: they are Python/QML/web, not Rust; `crates/` is the substrate
  workspace (ADR-0001). Polluting it inverts the substrate/consumer boundary.
- **Move *everything* out of `spikes/` at once.** Rejected: only `dreaming` has live deps; moving the
  graduated-references (whose code already left) churns 200+ doc paths for zero risk reduction. Phase the
  urgent thing; relabel the rest in place.
- **One bucket `apps/` for both products and the UE workspace.** Soft-rejected: `ue-probe` is an
  authoring toolkit, not a deployed app — `studio/` keeps that honest. Deferred with Phase 2.

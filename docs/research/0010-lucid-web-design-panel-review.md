# Review 0010 — Lucid web surface (:8765) design-panel review

- Status: Review complete + **fixes applied** (2026-06-16). **Verdict: was 4/10 (correct spine,
  unbuilt interaction layer) → fixes landed for every blocker; re-rate owed after a live dream run.**
- Subject: the shipped interactive dream-loop page `apps/dreaming/lucid/lucid_web.py` (served at
  `127.0.0.1:8765`, opened from the status hub `:9123`), its systemd unit
  `integrations/lucid/agentos-lucid.service`, and the storage layer `apps/dreaming/lucid/lucid_store.py`.
- Distinct from [0008](0008-lucid-review-scorecard.md): that scored ADR-0014 + the throwaway
  `lucid_panel.py`; this scores the *shipped web surface* (`lucid_web.py` over the `lucid_linear`
  MVP, ADR-0015/0016) as built, end to end.
- Panel (6 specialists): ux · ui-accessibility · visual-systems · interaction · content-voice ·
  responsible-ai/privacy. Bar held: `integrations/design/instrument-tokens.md` (canon) +
  `integrations/status-panel/panel.html` (the in-family surface that already solved most of this).
- Relates to: ADR-0015 (linear MVP through the lease), 0016 (private/ephemeral + burn), 0012 (keyhole
  as honest progress surface), 0004 (graphics-yield → reduced-transparency fallback).

## Verdict

A **correct, honest *spine* wrapped in an unbuilt *interaction layer*.** Readiness honesty, CSRF/Origin
posture, the gate, and the private card were genuinely good; the surface borrowed the instrument *look*
without the instrument *infrastructure* (`panel.html` already had it). One root cause — the minutes-long
generation turn was **invisible** — was landed on independently by three reviewers and capped the score.

| Dimension | Score | Headline |
|---|---:|---|
| interaction | **3** | the minutes-long turn had **no in-flight state** — a regression from `lucid_panel.py` |
| ux | **4** | same gap + silent fail-open (`{ok,node:null}`) + 5s re-render ate a half-typed prompt |
| accessibility | **4** | colour-only status dots, no live region, contrast fails on top-of-gradient glass, no reduced-transparency |
| voice | **4** | ~40% leaked the machine (coordinator/ComfyUI/Ollama, CLI, "sink", "red-line gate", "fails open to the ambient shader") |
| visual-systems | **5** | near-miss reskin: no scales, status colours as orphaned literals, no glass fallback, warm-hue collision on the private card |
| responsible-ai/privacy | **6.5** | claims over-stated the code: "sealed in RAM (tmpfs)" / "Burned on logout" not backed; default mode had no delete |

## Findings → fixes applied this pass

- **[BLOCKER · ux/interaction/a11y] minutes-long turn was invisible.** `/api/dream` ran `L.step()`
  synchronously; no progress, no control-lock, fail-open returned a silent success.
  → **Fixed:** async worker + server `TURN` record (`dreaming/done/skipped/refused/error`); a real
  "✦ Dreaming this beat…" card with elapsed timer + keyhole pointer; double-submit guard; fail-open now
  renders an honest "skipped — graphics card was needed elsewhere" note. (`lucid_web.py`)
- **[BLOCKER · a11y] 5s `innerHTML` rebuild destroyed focus + half-typed prompt.**
  → **Fixed:** signature diff-render (rebuild only on real change) + capture/restore of the `#own`
  input value, caret, and focus; adaptive poll (2.5s dreaming / 5s idle / 15s hidden).
- **[MAJOR] native `alert()`/`confirm()` for errors + the irreversible burn.**
  → **Fixed:** inline instrument-register cards + a `role=alert`/`role=status` live-region pair; burn is
  now a **two-step inline** confirm with a persistent, honest partial-failure message. (B2 consent
  `confirm()` left intact — deliberate.)
- **[MAJOR] no reduced-transparency fallback** on the surface most likely open under VRAM pressure.
  → **Fixed:** ported the canon `prefers-reduced-transparency` solid-glass block + `prefers-reduced-motion`.
- **[MAJOR] colour-only dots / unescaped model+user text into `innerHTML`.**
  → **Fixed:** per-dot screen-reader text equivalent (`aria-hidden` dot + "ready/not responding"); an
  `esc()` helper now wraps every interpolated label/prompt/clip name (closes the XSS).
- **[MAJOR · visual] orphaned status literals + no scales + warm-hue collision.**
  → **Fixed:** adopted the shared token block (canonical palette + derived `--st-*` + scales); private
  card **cooled off the reserved warm hue** (it's a *mode*, not the "needs you" cue).
- **[MAJOR · voice] leaked plumbing.** → **Fixed:** dots read *Graphics turn-taking / Video generator /
  Story suggestions*; dropped VRAM-lease/co-resident/ambient-shader/red-line-gate/sink/ADR-citation/CLI
  from user-facing copy.
- **[HIGH · privacy] "sealed in RAM (tmpfs)" / "Burned on logout" over-claimed.** ComfyUI needs the
  seed/anchor frames on real disk; no `ExecStop` enforced the logout burn.
  → **Fixed:** copy reconciled with reality ("the one frame the renderer must write to disk is sealed and
  burned with it"); new **guarded `--burn-private` `ExecStop`** hook burns the on-disk sinks on
  logout *iff the session is private* (never destroys a persistent dream). (`agentos-lucid.service`)
- **[MEDIUM · privacy] default (persistent) mode had no retention/delete story; `clear()` left clips +
  frames on disk.** → **Fixed:** new `lucid_store.purge_persistent()` (chain-driven exact wipe of clips +
  anchor frames + chain dir, with an allowlist-bounded orphan sweep, symlink-aware, verified) behind a new
  CSRF-guarded `/api/delete`; UI now discloses "saved on this computer… until you delete it" and offers a
  two-step Delete. Unit-tested: 5 planted sinks → 0 leftover.

## Verified (no live GPU run)

`py_compile` (web + store) · `node --check` on the page JS · server serves `/`,`/healthz`,`/api/state`
(carries `turn`) · CSRF fails-closed (403) on every POST · `--burn-private` exits without starting a
server · `purge_persistent` wipes all sinks. A live click-through of the dreaming → done/skipped states
(needs a real ~minutes beat) is the remaining re-rate gate.

## Not done (owed follow-ups)

- Web surface is not scheme-reactive (still forces dark via `<meta color-scheme>`); the token doc
  defers this — mirror the light register into `:root` under `prefers-color-scheme:light` if wanted.
- Exact WCAG-AA contrast certification of every pair (improved via darker gradient + opaque fallback +
  promoting critical text off `--inst-label`, but not formally measured pair-by-pair).
- Parallel feature work by the author during this pass — seed **upload + B2 likeness guard (ADR-0017)**
  and **text-to-opening (`lucid_t2i`)** — was preserved, not reviewed here; owes its own pass.

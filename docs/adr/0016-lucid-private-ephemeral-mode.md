# ADR-0016: Lucid privacy — a private (ephemeral, incognito) mode

- Status: Proposed
- Date: 2026-06-16
- Extends: [ADR-0014](0014-lucid-interactive-branching-dream-loop.md) / [ADR-0015](0015-lucid-mvp-linear-chain-through-the-lease.md).
- Driven by: the design-council scorecard [0008](../research/0008-lucid-review-scorecard.md) — the
  responsible-ai/privacy dimension (3/10) flagged unbounded retention across three sinks (incl.
  shared, world-readable ComfyUI dirs), surfacing on the status hub, and the set-as-wallpaper
  ambient-leak. This ADR adds the user-facing control that seals them.
- Relates to: ADR-0001 (local-first), ADR-0005 (set-as-wallpaper tx), ADR-0012 (keyhole surfacing).

## Context

Lucid is already **layer-1 private**: the narrator (Ollama) and generation (ComfyUI) are 100%
on-box and the surface is loopback-only — nothing leaves the machine. But "local" ≠ "private": a
dream leaves a persistent **on-disk** trail in three sinks — `~/.local/share/agentos/dreams/`
(chain + clips), `~/ComfyUI/output/lucid/` (clips), and `~/ComfyUI/input/` (the **seed image and
every anchor frame**, co-mingled in a shared, default-umask dir) — with no TTL and no "delete my
dreams." For a feature that can run on a personal seed image and an adult-tone opt-in, that trail
is the privacy hole.

## Decision

Offer a **per-dream Private (ephemeral) toggle** — incognito. The default stays persistent
(needed for the future branching/scrub). Private mode seals every layer-2 trace:

1. **RAM-backed, sealed storage.** chain + clips live under `$XDG_RUNTIME_DIR/agentos/lucid-priv/
   <session>/` — **tmpfs (RAM), `0700`** — never the persistent dream cache, never the shared
   ComfyUI output dir. The OS wipes tmpfs on logout.
2. **The one unavoidable disk spot is sealed.** ComfyUI's `LoadImage` must read the anchor/seed
   frame from its `input/` tree, so private frames go in a **`0700` subdir** `input/.lucid-priv-
   <session>/` (not the shared root) and are burned. Clips render to `output/lucid-priv-<session>/`
   then are **moved into tmpfs and the shared-output copy removed** — a private clip never lingers.
3. **Auto-burn + manual burn.** `burn(session)` removes all three sinks; the web surface has a
   "🔥 Burn this dream now" control and burns on logout via tmpfs. No retention ⇒ deletion is free.
4. **Unsurfaced.** A private dream is not shown on the :9123 hub with content (the hub shows only
   the lucid *service*, never a dream's prompt/clip), narrator beats are never logged, and
   **"set as wallpaper" is disabled** in private mode (a private dream must not reach the ambient
   desktop).
5. **One auditable storage layer.** All persistent-vs-private path decisions live in
   `lucid_store.py` (not scattered joins), with strict session-name validation (fail-closed)
   because a name flows into an `rmtree` target — no traversal, no clobber.

## Honest residuals (documented, not hidden)

- The anchor/seed frame is briefly on real disk in the `0700` sealed subdir (ComfyUI can't read
  tmpfs) until burned. It is mode-`0700`, owned-and-symlink-checked, and removed on burn/logout —
  but it is not RAM-only.
- ComfyUI retains the **prompt + workflow** in its in-process `/history` (loopback-queryable on
  :8188) until the ComfyUI process dies. The MVP `Spawn`s + SIGKILLs ComfyUI per lease, so a normal
  turn's history dies with the process — **the seal depends on that per-lease SIGKILL**; a reused or
  long-lived ComfyUI would leave the prompt enumerable until restart. lucid burns its own sinks, not
  ComfyUI's internal state.
- tmpfs lives in RAM and **may be swapped** unless swap is encrypted/disabled — out of scope here.
- The B2 real-person seed guard (ADR-0015) is still owed; private mode *contains* a real-person
  dream but does not yet *prevent* one.
- The browser **private-start toggle** lands with seed-upload (B2-blocked today); for now a private
  dream is started via the CLI (`lucid_linear.py start <s> --image … --private`), and the web
  surface reflects + burns it. The badge is driven by a non-volatile signal (sealed-dir + chain
  flag), not the wipeable tmpfs dir, so it can't silently lie after a wipe.

## Consequences

- Evidence: `apps/dreaming/lucid/lucid_store.py` (+ 41 tests in `test_lucid_store.py`), with
  `lucid_linear` threading `--private`/`burn` and `lucid_web` exposing the burn. An adversarial
  responsible-ai + security pass (verdict: initially *leaky*) drove the fixes now in place:
  - the **entire** private output dir — clip **and its prompt-bearing metadata PNG** — is drained
    to tmpfs and the shared subdir removed (no prompt lingers in shared output);
  - frame extraction writes a **store-owned absolute path** (the privacy-unaware engine never
    re-derives a private location);
  - the sealed input subdir is created **symlink-safe** (`mkdir 0700`-or-refuse-foreign, never
    `makedirs(exist_ok)+chmod` through a planted link); `burn` is symlink-aware and **verifies
    removal** (no false "burned"); a crash/logout **orphan is reaped at launch**;
  - `is_private` is **fail-closed**; frame names are validated; the web burn/dream POSTs require a
    **per-process CSRF token**.
- Default behavior is unchanged (persistent); privacy is an explicit, reversible choice.

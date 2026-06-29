# ADR-0049: Interactive model policy + research→adoption loop + agent-default propagation

- Status: Accepted (Phase 1 built; Phases 2–3 designed, gated)
- Date: 2026-06-28
- Relates to: ADR-0024 (model-currency scout — **this is its interactive, human-triggered front-half**),
  ADR-0044 (onboarding / brownfield bundles + the setup wizard — extended here),
  ADR-0001 (don't reinvent — Hermes owns the LLM; we only mutate its config reversibly),
  ADR-0005 (apply/rollback tx — *named as the intended mechanism but UNBUILT; this ADR uses the shipped
  snapshot + manifest-inverse until it exists*), ADR-0011 (proposals-only), ADR-0018 (VRAM coexistence
  budget), ADR-0008 (mature-lane age affirmation — the red line).

## Context

Model "research" today dead-ends at *printed suggestions → hand-edit `registry.json`*
(`setup.py:_cmd_research` / `research_models()` only prints text). There is:

1. **No way for a reviewed result to update the local system** — the loop is open. The model proposes
   into a terminal; a human re-types the decision into the registry by hand.
2. **No user control over which models may be proposed/adopted** — the only gate is the hard safety
   `DENYLIST = ("deadman44",)` (CSAM / non-consensual real-likeness), and it runs *only at fetch time*
   (`fetch_artifact` / `plan_bundle`). A user who is comfortable with the whole Ollama library cannot say
   so; a user who wants to keep a model *family* out cannot express that.
3. **No path to propagate a chosen default to the agents.** The Hermes inference LLM lives in
   `~/.hermes/config.yaml` (`model.default: qwen3.6-27b-64k`) and is **not in the registry at all** — so
   the one model whose VRAM footprint is the substrate's central concern (ADR-0018, ADR-0024 §2) is a
   blind spot, and nothing can set it.

The user asked for all three: *"model research → update local system; allow any Ollama model, or
allowlist/blocklist specific model families; and update default models across agents like Hermes (and,
later, others)."*

Two hard constraints shape the answer:

- **The 2026-06-19 incident is the failure a naive "set as default" button re-creates.** With ComfyUI
  holding 13.3 GB outside the lease, a capable 27B loaded **87 % onto CPU** → generation **1.5 tok/s**
  (a 208 s turn). A more-capable model would have been *strictly worse*. Capability in isolation is the
  wrong metric; **the fit gate must be coexistence-aware and measured** (ADR-0024 §3).
- **ADR-0005's tx engine is a decision with no runtime code.** The only reversibility primitive that
  ships is the gpu-coordinator snapshot + surgical config edit, plus the onboarding
  `setup-manifest.json` inverse records (`record_fetch`/`read_manifest`). This work reuses those; it does
  **not** block on the unbuilt Rust tx.

## Decision

1. **A pure local policy file** `integrations/models/policy.json` (0600) with a fail-closed resolver
   `policy.permits(ref, *, in_registry, family)` whose precedence is **fixed and rendered in the UI**:

   > **safety-DENYLIST  >  family_block  >  allow_any_ollama  >  family_allow**

   `family` is **derived in code** from a structured parse of the ref (host / namespace / name) via a
   curated map; it is **never** a model-supplied field. A missing/malformed policy file fails **closed**
   to *curated-only* (registry models permitted; raw refs denied) — never to allow-any. `allow_any_ollama`
   relaxes **curation only, never safety**: an explicit `family_block` stays sticky even under allow-any,
   and the DENYLIST can be overridden by *no* setting.

2. **The DENYLIST gates research-propose, adopt, AND (phase 2) set-default — as the FIRST check, on the
   bare ref, unconditionally.** Today it runs only at fetch. `is_denied_ref(ref)` is the canonical bare-ref
   form; the existing artifact-level `is_denied(art)` delegates to it. A test asserts a denied ref is
   refused *with `allow_any_ollama=True` and its family allow-listed* — proving curation can never reach
   under the red line.

3. **Family policy gates ALL models, not just Ollama LLMs** (the user's choice). Every registry model
   carries an **explicit, authoritative `family` tag** (so the curated set has zero taxonomy fuzziness);
   only raw, non-registry refs (the `allow_any_ollama` surface) fall back to the heuristic `derive_family`.
   This makes "all models" tractable without parsing Civitai version-ids — we *tag*, we don't *parse*, the
   creative models.

4. **Research returns structured candidates that code re-derives and validates.** `research_models()` also
   requests a strict JSON array; `validate_candidates()` re-derives `family` in code, runs
   `is_denied_ref` **then** `policy.permits`, present-checks the ref, and marks each
   `adoptable | rejected(reason)`. Every model-authored field (size/license/rating) is **advisory display
   only** — it never gates. Junk / denied / blocked / partial output yields **zero adoptable candidates and
   never an exception** (the `derive_feed` degrade-to-idle discipline). The cloud `claude -p` hop is
   **disclosed at click** and **stripped of policy/inventory** (only modality + a coarse VRAM bucket leave
   the box — never the family policy, which is a sensitive taste profile).

5. **Adopt-into-registry is the first reversible mutation** (Phase 1). A *present, permitted* ref becomes a
   registry entry; the inverse is appended to `setup-manifest.json`; idempotent; atomic (temp + rename
   under a process lock). `registry.json` is in-repo and wedges nothing — this closes the user's stated
   gap 1 with zero wedge risk. Every adopt/revert is surfaced in the ledger with a per-entry **Revert**.

6. **Default propagation is ONE concrete `HermesAdapter`** (Phase 2), not a framework. openclaw has zero
   code in the tree; an N-agent registry/interface/stub is YAGNI. `set_default(role, ref)` does a
   **surgical single-key in-place edit** of `model.default` (locate exactly one match; abort on 0 or >1;
   replace only that scalar; write every other byte unchanged — **no `yaml.safe_load`/`safe_dump`**, which
   would strip the file's comments + the commented-out `fallback_model` block). Atomic same-dir temp+rename
   preserving 0600/owner; hash-guarded abort if the file changed under us. The **inverse is a per-key record
   (the exact prior scalar) in `setup-manifest.json` — NOT a whole-file `config.yaml.agentos-bak`** (that
   filename is owned by gpu-coordinator; a shared snapshot makes the two components' reverts clobber each
   other). A round-trip test proves the comment blocks + `fallback_model` section survive byte-for-byte.
   The `set_default / current / revert` contract is documented here as the **extension point** for when a
   real second agent (e.g. "openclaw") exists — no fan-out, no stub, is built now.

7. **The live default write is gated on a MEASURED canary** (Phase 2, ADR-0024 §3): present-check
   (`ollama show`) + footprint = weights + KV(ctx, num_parallel) + **admit against coexistence-reserved
   free VRAM** (not "free now" at a quiet desktop, which lies at render time) + an offload tripwire
   (`size_vram >= size`, a tok/s floor), run through the existing `Spawn(batch)` lease. The canary
   **result** is the go/no-go. On apply: **force-evict the outgoing model** (`ollama stop`) so two big LLMs
   never stack; **write `fallback_model = prior default`** so a bad pick degrades instead of wedging;
   **write-only + honest "restart Hermes to apply"** + an optional is-active-guarded restart — never
   mid-turn, never a silent auto-restart.

8. **Proposal / confirm only; no cron, canary-scheduling, GC, or tried-ledger in `setup.py`.** That
   autonomous scout is the Hermes `model-scout` skill (brief 0004 / ADR-0024) — Hermes owns cron/agent/
   ledger; AgentOS grows no scheduler/model-manager. This feature is explicitly the *interactive,
   human-triggered front-half* of ADR-0024.

9. **`allow_any_ollama` lands in stages** (the user's choice). In Phase 1 the toggle exists and **relaxes
   curation among models already pulled locally** (lifts the curated-registry restriction for present
   refs). Its risky behaviors — proposing/pulling **un-pulled or non-default-registry-host** refs — land in
   Phase 3 behind registry-host pinning + strict ref charset + Modelfile `SYSTEM`/`TEMPLATE` inspection +
   an 18+ affirmation. allow_any + uncensored/mature families always route through the ADR-0008 age
   affirmation, persisted as a revocable `mature_affirmed_at`.

10. **Reconcile the egress honesty gap.** `models_panel`/ADR-0044 §143 say "nothing leaves the box," yet
    cloud research contradicts it. Phase 1 discloses the explicitly-invoked research hop and strips it of
    policy/inventory; Phase 3 adds an on-box research path (the box runs `qwen3.6-27b`) so currency
    research need never leave the box, with cloud a disclosed opt-in.

## Phasing

- **Phase 1 (built):** policy file + resolver; DENYLIST-first on adopt; family tags + the inference LLM in
  the registry; structured + validated research; **adopt-into-registry** reversibly; the "Models & policy"
  wizard section; a **read-only** view of the current Hermes default. No live config write; no dangerous
  allow-any behavior; no fan-out.
- **Phase 2 (designed, gated):** `HermesAdapter` live `model.default` write behind the measured canary +
  evict + `fallback_model` + surgical per-key reversible edit + honest restart. Must pass
  `reversibility-tx-reviewer` (per-key inverse), `security-reviewer` (atomic write / 0600 / concurrent
  writer), `resource-safety-reviewer` (canary + evict + fallback) before ship. **Verify first** whether
  Hermes hot-reloads `config.yaml`, takes it next-turn, or needs a restart — that determines whether a
  revert reaches the *running* session.
- **Phase 3 (designed):** hardened `allow_any_ollama` (arbitrary-host pulling behind host-pinning +
  Modelfile inspection + 18+ affirmation); on-box research path; the adapter contract realized for a real
  second agent.

## Consequences

- The user gains trusted-family curation + a one-click, reversible loop that actually updates the local
  system. The agent brain's default (Phase 2) is protected by the exact measured gate ADR-0024 was written
  to require — the CPU-thrash / OOM-wedge failure is **structurally excluded**, not merely warned about.
- The dangerous third (live Hermes swap, allow-any's arbitrary-host surface) is **deferred behind real
  gates** rather than shipped with prose safety.
- Net-new code is the policy resolver + structured-candidate validator + (Phase 2) one Hermes config edit.
  **Ollama still owns the library and `ollama pull` the fetch; Hermes still owns the LLM** (ADR-0001 held).
- The unbuilt ADR-0005 tx and the standing-creative-server-under-lease gap (ADR-0024 §4) are tracked as
  **dependencies, not assumed**. `config.yaml` remains the single source of truth for the *live* default;
  the registry entry is for currency/policy/audit only (no shadow `agent_targets` matrix).

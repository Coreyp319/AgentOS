# ADR-0044 — Guided onboarding: brownfield detect → bundle fetch → first reviewable result

Status: Accepted — v1 built + shipped on master (2026-06-23). CLI engine (`setup.py`) + browser
wizard (`setup_web.py`/`wizard.html`) + ComfyUI bootstrap + text-first ordering + hardware-aware
fit + optional research agent; all three modalities, SFW + an opt-in Mature lane. Token via curl
stdin (never argv). 49 setup + 10 web tests green; real downloads validated. Design-council review
in progress; the localhost-only/never-tailnet, keyring-credential, and Mature-opt-in invariants hold.
Date: 2026-06-23
Relates to / inherits: ADR-0001 (substrate, don't-reinvent), ADR-0002 (Ollama does
residency; configure-don't-rebuild), ADR-0005 (reversible-by-default tx), ADR-0008 +
ADR-0009 (dreaming + NSFW = deliberate opt-in, imperative-only, never auto-surfaced — the
binding consent precedent), ADR-0015 (the coordinator owns ComfyUI under the lease),
ADR-0031 (the launch surface / Atrium), ADR-0043 (progressive adoption — the systemd-run
worker + CSRF/local-origin gates this reuses), ADR-0007 (secrets-at-HEAD discipline).

## Context

A fresh `git clone` gives a user the Rust substrate + the component installer + service code
+ ComfyUI workflow JSONs + a model *manifest* — and **zero model weights, no ComfyUI
checkout, no Ollama models**. Every one of the three generation modalities (text, image,
video) depends on heavyweight neighbors AgentOS deliberately does not bundle. There is **no
download automation anywhere in the repo** today; `comfyui/apply.sh` *aborts* if ComfyUI
isn't already installed and never touches a weight. The `--preflight` probe (ADR-0043-era)
*detects* the gaps but fetches nothing. The realistic full SFW+NSFW stack is ~150–200 GB; a
minimal "first dream" set is ~50–70 GB.

The goal: get a user from launch to **seeing their first generated result** — across text,
image, and video, SFW or an explicit Mature lane — as fast as possible, never stranded.

Two constraints shape the design:

1. **Brownfield is the common case.** The user may already have Ollama installed (with models
   pulled), a ComfyUI checkout with weights on disk, Hermes, even API tokens in their keyring
   (the box already runs `~/enh-dl.sh` with `secret-tool`). Onboarding must **detect and adopt
   what exists** and fetch only the *gaps* — never re-download, never clobber a working install.
2. **Don't reinvent (ADR-0001).** A generic model-downloader/account-manager is exactly what
   StabilityMatrix / Pinokio / `ollama pull` / `hf download` already do. AgentOS must reuse those
   as primitives and build only the glue nothing else does for *this* stack.

## Decision

### The reframe: a detector, not a downloader

Onboarding is **detect → reconcile against a curated bundle → fetch only the gaps → fire one
sample → drop the user into the result.** The curation, the brownfield reconciliation, the
lease-gated first generation, and the Lucid handoff are the AgentOS-specific value; the bytes
come from reused fetchers.

### Build-vs-reuse

| REUSE (do not build) | BUILD (the genuine glue) |
|---|---|
| `ollama pull` / `hf download` as fetch primitives; the model-*manager* category | **Brownfield detector / presence resolver** — grows `--preflight`: `ollama list`, on-disk scan of `~/ComfyUI/models/**`, Hermes + keyring probes → per-model `have ✓ / fetch ✗` |
| `adopt.py`/`adopt_run.py` systemd-run worker + CSRF + local-origin + root:no gates (ADR-0043) | **Curated bundles** in `registry.json` (`modality × rating × tier` + a structured `fetch` ref) — curation *is* the product |
| The VRAM lease (already gates first-gen via `lucid_linear.lease_spawn`) | A thin **gap-fetch action** that shells to the reused fetchers with two-level progress |
| Lucid `POST /api/start`; swaync toasts; the OS keyring (`secret-tool`) | The **Lucid handoff** — auto-fire one sample at "minimum viable," show it, deep-link into Lucid |

### The registry is the single source of truth (schema extension, no fork)

`integrations/models/registry.json` already describes each model (`id, runtime, size_gb,
source, safety_critical, used_by`). Extend it minimally — do not create a second manifest:

- Per model, formalize the prose `source` into a structured **`fetch`**:
  `{ "via": "ollama" | "hf" | "civitai", "ref": "<repo | model:tag | modelVersionId>",
     "dest": "<comfy subdir, for via:hf/civitai>", "auth": "none" | "hf" | "civitai",
     "rating": "sfw" | "mature", "tier": "minimum" | "hero", "modality": "text|image|video",
     "license": "<spdx-or-name>", "redistribute": false }`
- Add a top-level **`bundles`**: named sets that are just *queries* over the models
  (`{ id, modality, rating, tier, model_ids: [...] }`). "Express SFW Video" = a bundle id.
- The ~20 supporting weight files that today live only in workflow JSONs / `lucid_engine.py`
  constants (the Qwen-Image-Edit set, all LoRAs/VAEs/text-encoders, SAM2, the upscaler) must be
  **enumerated into the registry** so the fetch manifest is complete (a one-time backfill,
  ideally derived from the active workflow JSONs).

### Three acquisition lanes — and the cardinal rule: never block the first result on a manual gate

| Lane | Auth | Used for | Mechanics |
|---|---|---|---|
| **Ollama** | none | all text + the safety vision model | `ollama pull <ref>`; auto-resumes; auto-pull on first `run` |
| **HuggingFace** | none for open repos; token + a one-time browser license click for *gated* | video (Wan 2.2 — open/Apache), SFW image mirrors, optional Flux/Llama (gated) | `hf download <repo> <file> --local-dir <dest>`; resume is automatic |
| **Civitai** | account + API key + a mature-content account toggle | the Mature image/video checkpoints + realism LoRAs | `GET /api/download/models/<modelVersionId>` with `Authorization: Bearer`; **serialize** (new accounts 429 easily) |

**Wan 2.2 14B (Apache, ungated), SDXL/ungated HF image mirrors, and Mistral-Nemo / Mag-Mell
on Ollama cover SFW text+image+video with ZERO accounts.** So the first result never blocks on
a signup; gated/Mature models are opt-in upgrades that download *behind* the first result.

### "Minimum viable → first result" per modality (don't wait for the full stack)

| Modality | Minimum set | First sample | Lands in |
|---|---|---|---|
| Text | 1 Ollama chat model | a streamed completion (token-by-token — proves local + fast) | a chat pane → Hermes |
| Image | t2i checkpoint (ungated mirror) **+** `b2-vision` safety model | a built-in-prompt render | inline → Lucid |
| Video | the image floor **+** the i2v model (Wan 2.2, under the lease) | image → ~2s dream via the real `POST /api/start` | **into Lucid**, dream on screen |

For a user who picks **Video**, render the **Image** result first (~15 min) as a bridge while
the ~24 GB i2v model downloads (~20–30 min) — they are never staring at a long bar with nothing
to look at. The first generation reuses Lucid's *real* `POST /api/start` (not a canned demo) so
it honors the VRAM lease and is genuinely representative.

### The surface: a localhost-only setup wizard, bash-bootstrapped

`./install.sh --onboard` is a thin bootstrap (builds `agentosd`, runs `--preflight`, ensures
linger, prints any sudo steps) that brings up a **loopback-only Setup wizard** and opens the
browser (the Pinokio pattern; we already run three stdlib loopback panels). The wizard is the
interaction surface; **bash + the driver remain the actuator-of-record** — every "fetch this"
action delegates to `install.sh --only <id>` via the **same `systemd-run --user` worker +
CSRF + local-origin + root:no gates that ADR-0043 already ships** (the wizard *proposes*, code
*disposes*; it selects a trusted registry id, never executes a wire string).

**The wizard binds 127.0.0.1 ONLY and is architecturally excluded from `tailscale serve`**
(its own port, never added to `agentosd-remote.sh` `PORTS`). It captures credentials and can
browse Mature models — it must never be reachable off-box. Headless/no-browser degrades to a
curated-preset whiptail path that prints "open Lucid when ready."

### Brownfield detection (the centerpiece)

Before any download, the resolver reconciles the chosen bundle against reality, read-only:
- **Ollama**: installed? `ollama list` → which refs are already pulled (adopt them).
- **ComfyUI**: installed + `COMFY_ROOT`? scan `~/ComfyUI/models/**` for each bundle file by
  name/size (adopt; never re-fetch or overwrite an existing weight).
- **Hermes / lease / venv**: present? (reuse the existing preflight probes.)
- **Tokens**: already in the keyring (`secret-tool lookup`)? reuse; don't re-prompt.
The wizard then shows **"have ✓ / fetch ✗"** and fetches only the gaps. Adopting an existing
ComfyUI/Ollama means pointing AgentOS at it (env: `COMFY_ROOT`, the registry refs), never
installing a parallel copy.

### Safety (non-negotiable — inherits ADR-0008/0009; a prior hardcoded-token incident exists)

1. **Credentials → the OS keyring, never a file.** Reuse the box's existing pattern
   (`secret-tool store/lookup service {huggingface,civitai} key api-token`, KWallet). The token
   enters a child process's env (or stdin-config) for the `curl`/`hf` call only — **never argv**
   (`/proc/<pid>/cmdline`), never a unit file, never a log. Keyring-unavailable fallback is a
   `0600 O_CREAT|O_EXCL` file under `$XDG_CONFIG_HOME/agentos/`, **disclosed** ("stored in a
   protected file — no keyring found"), never silent.
2. **The Mature lane is explicit, default-off, separately affirmed.** A distinct screen with an
   18+ affirmation — never a pre-checked box folded into "creative stack" (ADR-0008 was walked
   back precisely for default-on NSFW). Capability ≠ standing consent to fetch more. The red line
   stays: **no minors, no non-consensual real-person likeness** — surfaced in the affirmation copy
   and enforced by a shipped **denylist** (CSAM/real-likeness repos hard-blocked, e.g. the
   `deadman44/*` exclusion already in the local-video-gen skill).
3. **Localhost-only.** The wizard + credential entry + Mature browser are excluded from the
   tailnet (the models panel `:9124` IS tailnet-served today — the wizard must not inherit that).
4. **No telemetry.** Nothing about *which* models are chosen, pulled, or generated leaves the box;
   the only network calls are the downloads themselves. Add an `hf_[A-Za-z0-9]{20,}` pattern to
   `dispatch.py` `_REDACTIONS` (the bare HF-token blind spot) so a stray token can't escape into a
   cloud-bound diagnosis.
5. **Link-and-download, never bundle.** AgentOS fetches *on the user's behalf with the user's
   credentials* — it never re-hosts/redistributes weights (Flux = non-commercial, Hunyuan =
   territorial, many Civitai = per-model terms). A `.gitignore` guard prevents a fetched weight
   from ever being staged. The wizard states the ToS reality plainly and links the canonical model
   card.
6. **Reversible deletion wipes both stores.** "Remove" clears the keyring entries
   (`secret-tool clear …`) AND the weights it fetched (tracked by a wizard-written manifest = the
   ADR-0005 inverse record), and tears down the unit — with a confirmation of each, plus a
   "what's stored" audit (presence, never values).

## Non-goals

- **Not a model manager.** No resume/dedup/version-pinning/"manage all your models" UI — that's
  StabilityMatrix/Pinokio; reuse, don't compete.
- **No lease-aware downloads.** Bandwidth ≠ VRAM; fetching is disk/network I/O and never touches
  the coordinator. The lease only gates the first *generation* (already wired).
- **Not tailnet-exposed.** The wizard is on-box only.
- **No bundled/redistributed weights**, no telemetry, no NSFW-at-install-default.

## Consequences

- A new loopback-only Setup wizard surface (bash-bootstrapped, reusing the ADR-0043 apply worker)
  + a presence/brownfield resolver (grows `--preflight`) + a registry schema extension
  (`fetch` + `bundles`, plus a backfill of the ~20 unenumerated weight files).
- The user fetches ~50–200 GB under *their* accounts; AgentOS bundles nothing. First *image*
  result is reachable in ~15–30 min; the full stack in ~1–3 h, backgrounded.
- The credential + Mature surface raises the safety bar; it gets the same security review ADR-0039/
  0043 got before the apply path ships, plus the privacy must-fixes above.
- **Build sequencing (even though v1 covers all three modalities):** (1) brownfield detector +
  registry `fetch`/`bundles` schema + backfill; (2) the gap-fetch action over the reused
  fetchers (Ollama/HF, no-auth) with progress; (3) the Lucid first-result handoff; (4) the
  credential keyring + HF/Civitai mid-flow walkthrough; (5) the Mature opt-in lane (age gate +
  Civitai + tailnet carve-out + denylist). Each is independently shippable; the SFW no-account
  path (1–3) reaches a first result before the credentialed lane (4–5) lands.

## Amendments (post design-council review, 2026-06-23)

The council (`docs/design/0044-setup-wizard-council-brief.md`) flagged drift between this ADR's
promises and the shipped code. Reconciling here (corrections to our own claims, not new behavior):

- **Fetch worker — NOT the ADR-0043 systemd-run worker.** The setup wizard runs each fetch as a
  direct `subprocess.Popen` of `setup.py` (`setup_web.py`), not the sandboxed `systemd-run --user`
  worker the dispatch/adopt surfaces use. Rationale: the installer is not a hardened, network-facing
  surface that must confine its actuator — it *is* the installer, run deliberately by the user. Any
  earlier wording implying the systemd-run worker is corrected to this.
- **Token to curl via stdin, never argv.** Downloads pass the Authorization header through
  `curl --config -` (stdin), so the token never appears in `/proc/<pid>/cmdline`.
- **Keyring 0600 fallback — implemented.** When no Secret Service is reachable, the token is
  stored in a disclosed `0600 O_CREAT` file under `$XDG_CONFIG_HOME/agentos/` (announced on stderr),
  so a headless install isn't a dead-end. Keyring is the default; the file is the disclosed exception.
- **Reversibility — partial, honestly scoped.** A per-fetch inverse manifest
  (`$XDG_STATE_HOME/agentos/setup-manifest.json`) is written on every successful download, surfaced
  as a read-only "what's stored" audit, and paired with a "Forget token" action (keyring_clear).
  Per-bundle **weight** removal is **deferred a phase** — it must be refcount-gated because shared
  artifacts (`b2-vision`, `narrator`, `t2i-opening`) recur across bundles. Until it ships, the UI
  says "listed", never "removable".
- **Rating ≠ account gate (honesty).** `rating` (capability: sfw|mature) and the account gate
  (token needed or not) are independent. Copy across the README/registry/wizard is reconciled so a
  mature bundle can't read as "no gate". **Both video bundles are mature (18+); there is no SFW video
  lane yet** — a real `rating:"sfw"` Apache-Wan i2v bundle (+ a structured `fetch_gate` registry
  field) is a proposed, human-gated follow-up (the council's ADR stub), not shipped this round.
- **Accessibility.** The wizard honors `prefers-reduced-motion` / `prefers-reduced-transparency`,
  uses `aria-live` for download/ready announcements, and gives the `tight`/`too-big` fit rungs
  non-colour glyphs (◐ / ⚠). Fit copy *warns*, never *guarantees* (it is isolation-only — it does not
  model the ~2.5 GB the always-on desktop already holds, ADR-0004).

## Amendment (2026-06-23) — the wizard becomes the single first-run front door

The `:9125` wizard grows from models-only into the **one first-run entry for *the desktop*** — models,
desktop customizations, agent wiring, and remote access — by **reusing existing engines, not forking an
installer** (Option A). Built + reviewed by a 6-lens panel (security · privacy · determinism · ux ·
ambient · ai-product); three CRITICALs (KRunner→cloud dispatch, "adopted" lying for an incomplete row,
stale-as-serene wallpaper) were resolved by design before build. Normative additions:

- **Route-not-dashboard (binding).** The wizard *routes and adopts*; it is **not** a control center.
  The ONLY live reads allowed are (1) the desktop catalog + badges folded from the panel's
  `/components.json` and (2) adopt-job lifecycle from `/adopt.json` — both **advisory display**, never
  driving a mutating default. Resource/health/service-state gauges are **out of scope** for `:9125`
  (they belong to the keyhole tray + the `:9123` panel, ADR-0012/0031). This fences the aggregate
  (models + desktop + agents + remote on one page) off the ADR-0031 anti-hub line.
- **Desktop section = a proxy of the ADR-0043 adopt engine.** A collapsed "Customize your desktop"
  section adopts `tier∈{desktop,hermes}, root=no` components by **server-to-server proxying** the
  hardened `:9123` `/adopt` engine over loopback (`/api/desktop`, `/api/component`,
  `/api/component_jobs`). The wizard never parses `components.conf` itself and never shells the driver;
  it builds a FRESH request (no client headers reflected outward) and re-validates the id against the
  same live fold before the hop. **The wizard token now gates software install/remove** — per-process
  rotation + loopback-only binding are load-bearing; `GET /api/token` is loopback-readable by any local
  UID under the accepted single-user threat model (gate it with `SO_PEERCRED` first if multi-user ever
  enters scope). Grouping (Ambient / Look / Agents / Integrations) + preview thumbnails are setup-side
  presentation only; the catalog stays the single authority.
- **"Adopted" must not lie.** Rows whose `apply.sh` ends in a manual desktop step (keyhole's tray
  placement) carry a `post_adopt` note surfaced after a successful adopt with a "one more step ↗"
  affordance — never a bare ✓.
- **Lucid handoff (owed since this ADR) closed.** The model-written opening prompt reaches Lucid as
  `?prompt=…`; Lucid prefills `#opentext` once (`.value`, never `innerHTML`), **strips it from the URL**
  (`history.replaceState`), truncates ≤2000 chars, and does **not** auto-start (the user still clicks
  Begin — B2 likeness gate + `rating_floor` preserved).
- **Remote access = copy-don't-execute, never self-exposing.** A bottom "Remote access" card surfaces
  the `tailscale-remote` setup behind a consent checkbox with ample warnings (no per-request auth on the
  exposed UIs; `serve` not `funnel`; the reboot gap). The wizard **runs no `tailscale` command**, does
  **no live `serve`/`status` reflect** (which would leak tailnet identity metadata onto the credential
  page), and **its own `:9125` is never added to the exposure list** — asserted by test.
- **Retention map.** The durable stores the front door turns on are named: the dispatch ledger +
  transcripts (`$XDG_STATE`, `0600`+TTL), the reactive-wallpaper prev-state cache, the adopt ledger.
  `build_state()`/`_desktop_state()` output is in-memory, never persisted outside the `0700` runtime dir.

Tests: setup **73**, status-panel **165**. Relates: ADR-0043 (adopt engine reused + `gpu-coordinator`
row), ADR-0039 (dispatch hardening), ADR-0023 (reactive-wallpaper default), ADR-0031 (anti-hub bound).

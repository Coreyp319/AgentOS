# ADR-0039 — Dispatch an agent to investigate a down service

Status: Accepted (Phase 1 built; Phase 2 gated on the Hermes write-API)
Date: 2026-06-21
Relates to: ADR-0026 (boot stack + status panel), ADR-0031 (launch surface / Atrium),
ADR-0003 (fail-open-supervised), ADR-0034 (deterministic UI audit — collect→decide→apply
allowlist), ADR-0011 (model proposes, code disposes), the Hermes write-API research (0013).

## Context

The status panel (ADR-0026) and the Atrium (ADR-0031) tell you *that* a service is down.
Today the only recovery affordance is **copy-don't-execute**: the panel computes a
`systemctl reset-failed && restart` one-liner and the human runs it. That's deliberate —
the panel is strictly read-only, network-facing, and hardened (`ProtectHome=read-only`,
`NoNewPrivileges=true`, `ProtectSystem=strict`).

The ask: from a down row, **dispatch a Hermes or Claude Code agent to investigate, fix, and
log** — without surrendering the read-only/honest posture the surfaces are built on. Two
user decisions framed the design:

1. **Bounded auto-fix + escalate** — auto-apply *only* the reversible recovery the panel
   already trusts; anything beyond it is investigated and *proposed* for human approval.
2. **Dispatch from desktop *and* phone** — so the mutate path needs real anti-CSRF auth (the
   panel is otherwise unauthenticated on `:9123`).

## Decision

### The safety spine — where the mutation gate lives

The enforcement point is **the box, never the client**, and the *only* state change that
ever happens without a human is a single closed-allowlist operation, applied by code:

- **First-aid (the bounded auto-fix), code-disposed.** When a dispatch starts, the worker
  first runs the *exact* recovery the panel already proposes — `systemctl --user
  reset-failed <unit> && systemctl --user restart <unit>` — for a **user-scope** catalog
  unit, **once**, then re-probes health. This is deterministic, reversible, and identical to
  what a human would copy-paste. It is gated by an **opt-in** allowlist (`can_auto_recover`):
  the catalog entry must carry `auto_recover:true` (so GPU/lease/compositor units — `comfyui`,
  `wallpaper`, `lucid`, `share-hub` — are *never* auto-restarted, which would start GPU work
  outside the lease; off unless explicitly opted in) **and** be user-scope (system scope needs
  `sudo`/polkit, which `NoNewPrivileges` blocks) **and** off the never-auto denylist (the panel's
  own unit, the lease daemon). The unit is re-derived from the trusted catalog (never a wire
  string → no injection). Two re-checks at the moment of acting: the worker **re-probes
  attention** before restarting (don't bounce a service that already recovered), and **refuses to
  re-arm a crashloop** — if systemd parked the unit by `StartLimit`, or this service already had
  `FIRST_AID_MAX` first-aid restarts in a rolling window, it escalates rather than clearing
  systemd's own brake.

- **Escalation → the model *proposes*, code disposes.** If first-aid doesn't recover it (or
  it's system-scope, or the fix is anything other than a restart), the worker hands the
  gathered evidence (unit state + journal tail + what first-aid tried) to the chosen agent.
  **The model is a TOOL-FREE reasoner over the (redacted) brief** — Claude runs headless with
  `--strict-mcp-config` (no inherited MCP servers — Gmail/Drive/Supabase/**agentos-gpu**, which
  could otherwise mutate the live VRAM lease), every built-in tool disallowed (incl.
  `Read`/`Grep`/`Glob` → no filesystem reach, no off-box exfil beyond the brief),
  `--permission-mode default`, and a **pinned `--model`**. It returns a *structured proposal*
  `{diagnosis, proposed_fix, confidence}`. The worker **never executes the model's output** — by the
  time we escalate, the one auto-applicable op (the restart) has already been tried, so every
  model proposal is, by construction, outside the allowlist and lands as **needs-approval**:
  surfaced to the human via the existing copy-fix affordance. This is the strongest reading
  of "bounded auto-fix": the allowlist is applied by deterministic code; the model only ever
  proposes, and its proposals are always human-gated.

### Process model — the panel never leaves its sandbox

```
POST /dispatch ──(panel, hardened)──▶ validate (token + catalog + attention + rate-limit)
                                      write ledger entry  ($XDG_RUNTIME_DIR — writable)
                                      systemd-run --user  ──▶ transient worker unit
                                                                (owned by the user manager;
                                                                 OUTSIDE the panel sandbox)
worker (dispatch_run.py) ──▶ first-aid (user-scope recover, once) ──▶ recovered? ──▶ done
                          └▶ else: gather evidence ──▶ model (claude -p | Hermes /v1/runs)
                                   ──▶ proposal ──▶ ledger needs-approval ──▶ human approves
            (every step appends to a durable transcript; ledger drives the live UI)
```

`systemd-run --user` is the sanctioned way for the hardened, loopback-bound panel to launch
work it cannot do itself: the transient unit is spawned by the *user manager* (a D-Bus
request over the already-allowed `AF_UNIX`), so it does not inherit the panel's
`ProtectHome`/`NoNewPrivileges` mount+exec sandbox. The panel itself stays maximally
hardened and writes nothing under `$HOME`. (Alternative considered: a spool file + a
`.path`-triggered worker unit — more idiomatic but a wider surface, since any local process
could drop a spool file; an authenticated POST that calls `systemd-run` is the narrower
trust boundary. Recorded for the security review to weigh.)

### Auth (desktop + phone)

The mutate route is guarded by a **per-process random token** (`secrets.token_hex`), served
only same-origin via `GET /dispatch/token` and required as `X-Dispatch-Token` on
`POST /dispatch`. A cross-origin page can issue the POST but cannot *read* the token
(same-origin policy on the token response) → CSRF-safe; the route also rejects
`Sec-Fetch-Site: cross-site` (a browser-stamped, page-unforgeable header) as defense-in-depth.
The panel stays loopback-bound; `tailscale serve` (never `funnel`) remains the only remote path,
so a remote caller is an authenticated tailnet member who fetched the token over the tailnet.
This is browser-CSRF defense, **not** local-process isolation: any process running as the user
can dispatch — but the action is bounded, reversible, logged, and that process could already run
`systemctl` directly. Per-incident logs are `0600`, TTL-pruned, and local-only
(`GET /dispatch/log` is served to a provably-local origin only); the phone sees the redacted
ledger summary, never the raw journal, the shell one-liner, or the log path.

### Privacy (the cloud target sends evidence off-box)

The journal/brief is **redacted** (bearer tokens, `key=/token=/password=`, JWTs, cloud keys,
emails, IPs, `$HOME` paths, high-entropy blobs) before it can leave the box or land in a durable
log; the model is tool-free so it can't read anything beyond the brief. The **Claude (cloud)**
target shows a once-per-session **consent** prompt naming exactly what is sent and to whom;
**Hermes (local)** sends nothing off-box. Kill-switches: `AGENTOS_DISPATCH=0` disables dispatch
entirely, `AGENTOS_DISPATCH_CLOUD=0` removes the cloud target (server-rejected + button hidden).
The Hermes credential is forwarded only to a Hermes worker, never co-located with the cloud one.

### Targets

- **Claude Code** (Phase 1): cloud (Anthropic API) → **no VRAM/lease cost**; best for novel
  diagnosis. Run headless (`claude -p`) as a tool-free reasoner (see above), fed the redacted brief.
- **Hermes** (Phase 2): local Ollama → already coordinated by the existing lease; best for
  routine/local. Via `POST /v1/runs` (Bearer). The Hermes write-API is **not yet enabled**
  (`platforms.api_server`), so a Hermes dispatch fails *honestly* ("Hermes write-API not
  enabled") until that one-line config flip + gateway restart.

## Consequences

- The panel gains its first write route, but stays hardened and never mutates desktop/system
  state itself — it validates and delegates. The headline read-only/honest posture holds.
- "Investigate **and fix**" is delivered for the common case (a user daemon fell over) by the
  deterministic first-aid, with no model cost; the model is spent only on genuine escalations.
- Honest failure throughout, with distinct terminal states (no silent success-as-failure):
  `recovered` · `needs-approval` · `diagnosed` (investigated, no safe auto-fix — a success) ·
  `handed-off` (to a Hermes run) · `blocked` (a dependency isn't ready) · `failed` (the dispatch
  itself broke). A SIGKILLed worker's incident is **reaped** to `failed`, never stuck active.
- Concurrency is correct by construction: incident creation re-checks the rate limits/crashloop
  brake **atomically under the ledger flock**, and the worker **claims** an incident with an
  atomic compare-and-set (at-most-once first-aid is a property of this code, not of systemd).
- Every dispatch logs: a `0600`, TTL-pruned transcript on the box + a ledger entry the UI reads.
- Open (on-box, flagged not assumed): (a) `systemd-run --user` from the hardened unit launches
  the worker unsandboxed; (b) `claude -p` authenticates from the worker env *and* the locked-down
  flags (`--strict-mcp-config` + the disallow list + `--permission-mode default`) leave it with
  exactly zero tools (re-run a `ToolSearch` probe to assert); Phase 2 (Hermes) is gated on the
  write-API. A server-side shape-allowlist on the proposed command (must look like
  `systemctl`/`journalctl`) before it becomes a one-click "copy fix" is a tracked follow-up.

## Review hardening (2026-06-21, 7-lens adversarial pass)

A parallel review (security, resource-safety, determinism, privacy, applied-AI, UX, a11y) found
no desktop-bricking blocker but several real must-fixes, all applied above:
- **applied-AI/privacy Blocker** — `--allowedTools` is auto-approve, not restrictive; a headless
  session inherited the full MCP fleet (incl. `agentos-gpu` → could mutate the live lease) and
  `Agent`/`Skill`/`Workflow`. Fix: tool-free reasoner (`--strict-mcp-config` + comprehensive
  disallow + default permission mode + pinned model).
- **privacy Blockers** — unredacted journal to cloud, no consent, no kill-switch, world-readable
  logs. Fix: redaction, once-per-session consent, `AGENTOS_DISPATCH[_CLOUD]` kill-switches,
  `0600`+TTL logs, key-only-to-Hermes.
- **resource-safety Blocker** — auto-restart of `comfyui`/`wallpaper`/`lucid` starts GPU work
  outside the lease (the documented OOM failure). Fix: `auto_recover` opt-in + crashloop brake.
- **determinism/security** — TOCTOU before first-aid, non-atomic dedupe + worker-claim. Fix:
  re-probe attention, atomic create + claim, stuck-incident reaper, `Sec-Fetch-Site` check.
- **UX/a11y** — honest two-phase label, surfaced refusals, `aria-live` region, 24px target,
  focus restore, calmer Atrium (no infinite spinner), `confidence` surfaced.

## Amendment (2026-06-23) — dispatch-from-KRunner + fail-closed-to-local hardening

A KRunner-reachable launcher (`dispatch_launch.sh`, a fixed-`Exec` `.desktop` riding the ADR-0031
`gen_launchers` set) now POSTs the existing `/dispatch` route — the small reuse path, **not** a new
`org.kde.krunner1` runner (which this ADR/ADR-0031 deferred). Because KRunner has **no browser consent
surface**, an ADR-0044 review found a latent leak — `/dispatch` defaulted an omitted `target` to
**`claude`** with cloud ON by default, so a one-keystroke launcher (or a malformed/forged body) could
have silently shipped a redacted journal off-box. Two boundary changes close it:

- **`/dispatch` fails CLOSED to local.** An omitted/garbled `target` now resolves to `hermes`, never
  `claude` (`resolve_dispatch_target`, unit-tested). The browser still reaches cloud by sending
  `target=claude` itself, behind its existing once-per-session consent.
- **Launcher-class gate.** A request carrying `source=launcher` is **forced to `hermes`, and an explicit
  `claude` is refused with 409 at the route** — the trust boundary enforces local-only, not a string in
  the launcher script. A test asserts the launcher path can never produce a `claude` incident.
- The helper builds its body with `json.dumps` (target structurally un-overridable), validates the
  service id, confirms via a `notify-send` action **before** POSTing, does **not** auto-pick when >1
  service needs attention (routes to the panel), and stays `0644` (invoked via `bash`; install-time
  asserts 0644 + owner). The emitter is a **constant** (fixed absolute `Exec`, no catalog interpolation,
  byte-pinned test) so `gen_launchers`' injection-free guarantee holds for the one non-URL entry.
- **A cloud (`claude`) KRunner verb is NOT shipped** — it would re-open this ADR's consent decision and
  needs its own amendment first.

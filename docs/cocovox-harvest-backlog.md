# Cocovox harvest backlog

Reusable patterns from Cocovox (the author's own EdTech app — see ADR-0007), mapped to
AgentOS milestones. **Default lift mode: clean-room reimplementation in Rust** — AgentOS
is Rust, the value is patterns/algorithms, and reimplementing sidesteps both the BSD-3
attribution burden and the secrets-in-repo landmine. Source paths are relative to
`~/Documents/Cocovox/cocovox-development/backend/cocovox/` (frontend rows note `src/`).

> **Gate:** do **not** clone/copy any file out of Cocovox until its committed secrets are
> rotated and history is scrubbed (live Google OAuth + Pexels keys at HEAD,
> `OSS-READINESS-REPORT.md:54,56`). Pattern reuse via Rust reimplementation is unaffected.

## Substrate (Phase B) — de-risks the spine
| Pattern | Source | Milestone | Mode | Note |
|---|---|---|---|---|
| Transparent `/v1` passthrough proxy (multi-backend, faithful SSE, runtime-mutable base URLs, Ollama config-only) | `apps/openai/main.py` | **S2** | pattern→Rust | Proves ADR-0002's thin gateway end-to-end; the spike's production cousin. |
| Ollama-aware circuit breaker + multi-backend failover | `utils/circuit_breaker_factory.py`, `utils/provider_chain.py` | **S2** | pattern→Rust | Resilience floor for proxy + NVML + nimbus-flux calls. |
| Apply/rollback tx shape: destructive-op scan + backup precondition + manual-confirm gate + advisory lock | `migrations/migration_safety_checks.py` | **S3** | pattern | Direct analog of the reversible apply/rollback transaction. |
| Fail-closed guardrail-flag ledger (append-only who/old→new/why; missing flag → ON) | `config/safety_flags.py`, `database/models/safety_flag_audit.py` | **S3** | pattern | The ledger discipline for earned-autonomy + tx. |
| Race-safe budget consume + downshift-under-pressure | `apps/agents/models.py:try_consume_budget`, `apps/billing/cost_ceiling.py` | **S1/S4** | pattern→Rust | "Downshift to efficient model at 80–100% budget" = the VRAM-yield logic. |
| Plugin seam: priority-sorted inlet filter + Valves typed config + signature-introspected (least-privilege) injection | `apps/webui` (`main.py:1878/1907/2316`, `functions.py`) | **S4** | pattern | Invert their unsandboxed `exec()`+`pip install` loader — AgentOS sandboxes. |
| A2A self-exposure (one handler, MCP+REST dual transport, `.well-known/*` manifests) | `apps/agents/mcp_server.py`, `router.py`, `static/.well-known/*` | S4+ | pattern | Expose the tx/lease API as agent tools. |

## Dreaming composer — model-proposes/code-disposes is pervasive here
| Pattern | Source | Milestone | Mode | Note |
|---|---|---|---|---|
| AST-allowlist sandbox that refuses to write unvalidated code + reaping subprocess executor | `apps/animations/sandbox.py`, `render_queue.py` | dreaming | pattern→Rust (rustpython/AST) | The composer's safety floor. |
| Two-stage generate: analyze→**JSON spec**→codegen, then deterministic multi-axis validator + cache | `apps/cocovox_chats/mcp/servers/genui/{generator,validator}.py` + primitives JSON | dreaming | pattern | Swap Claude→Ollama, Svelte→Bevy-scene-JSON; closes the dreaming feedback gap. |
| Producer-critic loop + tiered-veto consensus (hard-veto safety tier, best-so-far rollback, LLM budget) | `apps/cocovox_chats/agents/producer_critic_orchestrator.py`, `critic_coordinator.py` | dreaming / S3 | pattern | Proposer=model, critics=validators, veto tier=safety scanner. |
| Prompt-as-versioned-artifact (semver'd composable modules + content-hash drift manifest + KS/entropy regression) | `apps/audio/voice_prompt_builder.py`, `prompt_regression_service.py` | dreaming | pattern | Treat composer prompts as testable, regression-monitored artifacts. |
| Daily-seeded determinism (stable within a day, fresh across days) | `services/unified_digest_service.py` | dreaming | pattern | Matches the existing FNV date-seed in the composer. |
| Generative-media resilience triad (circuit-breaker + in-flight dedup + content-aware SHA256 cache w/ similarity reuse) | `apps/cocovox_chats/mcp/servers/image_generator/` | dreaming/E2 | pattern | Cache nightly-dreamed ambient surfaces. |

## Ambient desktop (Phase A) — the vision's vocabulary, already shipped
| Pattern | Source (`src/`) | Milestone | Mode | Note |
|---|---|---|---|---|
| Event-bus + pluggable sensory handlers = notification-as-nervous-system | `lib/stores/celebrationBus.svelte.ts` + `handlers/` | P2/E1 | pattern→QML | Handlers = wallpaper-react, swaync, tray, sound. |
| Reactive-wallpaper / mood engine (tone→physics, persistent accumulation, context-switch flood/burst) | `lib/stores/{bokehGarden,bokehPersonality,courseBeacon,ringBurst}` | **E1** | pattern→QML/Bevy | Concrete tuning constants to mine. |
| Attention-overlay state machines (1+8 spotlight, darken-except-pointing, asymmetric collapse) | `lib/stores/{focusMode,ucuIllumination}`, `shadow-presence/` | C2/overlay | pattern | The attention overlay; Miller 7±2 cap is reusable. |
| GenUI registry-extractor-renderer (51 widget types, role-gated, progressive partial-JSON hydration) | `lib/components/.../ResponseMessage/{toolCardRegistry,toolDataExtractors,ToolCardRenderer}` | inline-rules / tray | pattern→QML | Streamed tool-call → hydrated widget; shape for inline-rule cards. |
| Agency-first "margin pattern" + transparency-of-hydration (`personalization_meta`) | docs + `agent-sdk` | embodiment | doctrine | Present-but-deferrable, transparent about inputs, always mutable. |

## Ambient learning / metacognition layer
| Pattern | Source | Milestone | Mode | Note |
|---|---|---|---|---|
| Flywheel bus (domain-neutral, fire-and-forget, swappable subscribers) | `apps/webui/services/flywheel_event_bus.py` | learning v1 | pattern→Rust (persisted) | The signal transport; persist events (days-long window). |
| Governance registry (honest-labeling: each adaptive system declares target/IO/toggle/evidence) | `apps/webui/services/adaptive_system_catalog.py` | learning v1 | pattern | Anti "engagement masquerading as understanding." |
| Measurement loop: baseline-paired lift% + learn-from-outcomes-not-clicks + Wilson/decay pure fns + confidence-gating | `flywheel_effectiveness_service.py`, `suggestion_weight_learning.py`, `suggestion_reranker.py`, `strategy_effectiveness_cache.py` | learning v2 | pattern→Rust | "Measure if the loop actually helped"; the anti-clickbait discipline. |
| Full chain: topic-infer → mastery EMA → rollup-up-a-DAG → personalized decay → human-readable WHY | `chat_topic_link_service`, `mastery_rollup`, `personalized_decay_service`, `recommendation_explainer` | learning v2 | pattern→Rust | Node types change: skill→concept→course ⇒ **commit→module→package**. |
| Push-to-ambient transport (SSE publisher, FIFO drop-oldest, cadence cap, quiet-hours gate) | `services/dashboard_events.py`, `notification_dispatcher.py`, `notification_window.py` | learning/P2 | pattern→Rust | The ambient-notification spine. |
| Calibration as the user-side of the trust ramp (over-trust = accept-without-inspect paired with later revert) | (net-new; calibration is report-only in Cocovox) | learning v2 | design | Closing it into earned-autonomy is net-new wiring. |

## Dev process (building AgentOS itself)
| Pattern | Source | Mode | Note |
|---|---|---|---|
| Worktree-per-session harness + the 2 wired git-guard hooks + human-gated integrator | `scripts/wt`, `.claude/hooks/{worktree,branch-op}-guard.sh` | adopt | "Model proposes, human fires." |
| Persona-panel self-learning mechanism (history aggregator + threshold rules) | `.claude/skills/persona-panel/` | pattern | Recast EdTech personas as Safety / Reversibility / Hermes-Integration reviewers. |
| Skip | 70 TTS "agent-vibes" hooks; the 5-month-dormant 17-agent + ci-agents + vendored BMAD | — | Museum of earlier ideas; not run day-to-day. |

## Cross-cutting doctrine (the deepest harvest)
Every census agent surfaced the same doctrine in Cocovox — **deterministic floor / LLM
advisory / never fail-open · model proposes, code disposes · measure if it actually
helped · transparent about what it remembers · never interrupt.** It is verbatim
AgentOS's CLAUDE.md + ADRs. Cocovox is the Python proof of AgentOS's design language;
AgentOS re-expresses it in Rust on the desktop. Harvest the doctrine, not the code.

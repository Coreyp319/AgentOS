# AgentOS

A small resource + safety **substrate** for a single-machine local-AI desktop.

AgentOS is **not** a new operating system, a distro, or an agent orchestrator — that
framing is deliberately resolved in [ADR-0001](docs/adr/0001-substrate-not-orchestrator.md).
On this box:

- **Hermes Agent** (`~/.hermes`, Nous Research) is the orchestrator / brain.
- **CachyOS + the Nimbus pack** (`~/whitesur-cachyos-pack`) are the desktop.
- **AgentOS (`agentosd`)** is the floor they both stand on: it arbitrates the one GPU
  between always-on graphics and on-demand LLM inference, and gives every agent a
  deterministic apply/rollback transaction with one undo button.

## Why it exists

One 24GB GPU runs an always-on ray-traced wallpaper (~3.5GB) **and** wants to serve
17–21GB LLMs to Hermes. They collide for the largest model, and nothing coordinates
them. `agentosd` is that coordinator — plus the safety substrate generalized from the
Nimbus `ui-audit` agent.

## Shape (see [docs/adr/](docs/adr/))

**The core substrate** — the floor under everything:

- **Inference:** configure Ollama for residency/queue + a thin transparent enforcing
  proxy in front for priority/metrics. *Not LiteLLM, not a custom scheduler.* (ADR-0002)
- **VRAM coordinator (the lease):** one daemon serves `org.agentos.Coordinator1` on the
  session bus and owns batch children. `Acquire(tier,est)` = cooperative (caller owns its
  process — Hermes inference); `Spawn(tier,est,argv)` = owned (agentosd spawns + evicts —
  ComfyUI/batch); plus `Release`/`Status`. Predict-before-load admission; SIGKILL on
  preempt. (ADR-0006/0010/0013)
- **VRAM yield:** conditional — only when `model + graphics > VRAM`, evict idle Ollama
  models (`ollama stop`) and relaunch the wallpaper without ray-tracing. (ADR-0004)
- **Apply/rollback:** a transaction API (`tx begin → ops → commit/rollback`) in the
  daemon, hybrid file-backup + inverse ops. (ADR-0005)
- **Fail-open:** if the broker hiccups, inference still flows; AI never goes dark. (ADR-0003)
- **Coexistence partition:** a warm-pool / heavy-lane budget so always-on graphics and
  on-demand inference share the card without thrash; telemetry + analyzer + AIMD admission. (ADR-0018)

**Surfaces & lanes it enables** — built on the substrate:

- **Keyhole:** a read-only tray instrument to *see* ongoing agent work — lease tier/holder,
  VRAM, residency, fleet — honest about UNKNOWN. (ADR-0012)
- **Reviewable request queue:** durable deferral buffer so create-from-image requests are
  never dropped; private = ephemeral-in-session. (ADR-0019)
- **Agent-facing GPU MCP:** lets agents ask *why* a lease was denied + an admission-feedback
  loop, with an act-tier clamp + session-identity binding. (ADR-0020/0021)
- **Creative-app lane:** admit-before-launch + cgroup/scope reclaim for Blender (Unreal
  deferred) — heavy creative apps coordinated through the same lease. (ADR-0022)
- **Creative-environment pipeline:** a live UE5 real-time desktop wallpaper as a composed
  "dark-ride" stage; VRAM management *is* the product. (ADR-0023)
- **Dreaming / Lucid:** an interactive branching dream loop of i2v video clips, steered beat
  by beat through the lease — now a real authored *tree* with spatial feed-forward
  annotations. (ADR-0008/0009/0014/0015/0016/0017/0025)
- **Model-currency scout:** keep the local model mix current as open weights move. (ADR-0024)

**Hermes** stays the orchestrator: a plugin (no fork) tags priority + acquires a GPU lease. (ADR-0006)

## Status

- ✅ Design locked (grilling session, 2026-06-15) — and 25 ADRs since; see [docs/adr/](docs/adr/).
- ✅ Spikes de-risked: proxy fidelity (streaming + tool-calls pass), VRAM-yield mechanism
  (live shed not viable → kill/relaunch — ADR-0004).
- ✅ Substrate core built: the `lease` coordinator daemon (`org.agentos.Coordinator1`,
  Acquire/Spawn/Release/Status) + the `monitor`/`feed`/`keyhole` read-only producers.
- ✅ Keyhole instrument verified in-host; coexistence telemetry + analyzer landed
  (ADR-0018 Phases 1–2); reviewable request queue built + verified end-to-end (ADR-0019);
  Blender creative-app lane built + reviewed + verified (ADR-0022).
- 🚧 In flight: the creative-environment UE wallpaper (ADR-0023 — Phase-A VRAM footprint
  now measured: ~1 GB packaged Lumen runtime, see `spikes/ue-probe/`) and the Lucid dream
  loop advancing onto the branching tree + spatial annotations (ADR-0025, logic verified,
  GPU end-to-end pending).

## Layout

```
crates/agentosd/   the broker daemon — subcommands: monitor · feed · keyhole · coord · lease
config/ollama.env  recommended Ollama configuration (ADR-0002)
integrations/      glue into the live system (Hermes, model registry, status panel, remote access)
docs/adr/          architecture decision records (the source of truth for every choice)
docs/design/       per-feature design briefs + council scorecards
docs/research/     build/buy/configure research passes feeding the ADRs
spikes/            throwaway de-risking experiments (not in the workspace) — incl. lucid, ue-probe, keyhole
```

## License

MIT — see [LICENSE](LICENSE).

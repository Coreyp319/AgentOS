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

- **Inference:** configure Ollama for residency/queue + a thin transparent enforcing
  proxy in front for priority/metrics. *Not LiteLLM, not a custom scheduler.* (ADR-0002)
- **VRAM yield:** conditional — only when `model + graphics > VRAM`, evict idle Ollama
  models (`ollama stop`) and relaunch the wallpaper without ray-tracing. (ADR-0004)
- **Apply/rollback:** a transaction API (`tx begin → ops → commit/rollback`) in the
  daemon, hybrid file-backup + inverse ops. (ADR-0005)
- **Hermes:** a plugin (no fork) tags priority + acquires a GPU lease. (ADR-0006)
- **Fail-open:** if the broker hiccups, inference still flows; AI never goes dark. (ADR-0003)

## Status

- ✅ Design locked (grilling session, 2026-06-15) — see ADRs.
- ✅ Spike: proxy fidelity (streaming + tool-calls pass) — `spikes/proxy-fidelity/`.
- ✅ Spike: VRAM-yield mechanism (live shed not viable → kill/relaunch) — ADR-0004.
- ✅ Research: build / buy / configure pass — folded into ADR-0002 & ADR-0004.
- 🚧 Building: `agentosd` VRAM coordinator — v0 = read-only monitor.

## Layout

```
crates/agentosd/   the broker daemon (v0: `agentosd monitor`, read-only)
config/ollama.env  recommended Ollama configuration (ADR-0002)
docs/adr/          architecture decision records
spikes/            throwaway de-risking experiments (not in the workspace)
```

## License

MIT — see [LICENSE](LICENSE).

# AgentOS — guidance for agents

## What this is (read ADR-0001 first)
AgentOS is a small Rust **resource + safety substrate** (`agentosd`), NOT a new OS,
distro, or orchestrator. The orchestrator already exists: **Hermes Agent** at
`~/.hermes` (Nous Research, MIT — gateway, kanban task engine, delegation, cron,
skills, memory, Ollama as default model). **Do NOT build another orchestrator.** The
desktop is CachyOS + the Nimbus pack (`~/whitesur-cachyos-pack`). AgentOS is the floor
under both.

## The rule that keeps mattering: don't reinvent
A research pass (ADR-0002) established: Ollama already does residency/concurrency/
queueing (config, not code); a thin transparent `axum` proxy already passes streaming +
tool-calls faithfully (the spike); LiteLLM is the wrong fit here (single backend, CVE
surface, Ollama-translation bugs). Build only what nothing else does: the VRAM
coordinator, the apply/rollback tx, and the Hermes plugin glue.

## Decisions live in docs/adr/
Every architectural choice is an ADR. Changing behavior → add or supersede an ADR; do
not silently drift from one.

## Conventions
- Reversible by default; deterministic where possible (model proposes, code disposes).
- MIT. Conventional commits (`feat:`, `fix:`, `docs(adr):`, `chore:` …).
- Skills are NOT stored here — the canonical store is `~/.hermes/skills/<category>/`.
- Commit `Cargo.lock` (this is an app, not a library).
- `spikes/` is throwaway and excluded from the Cargo workspace.

## Build / run
- `cargo build -p agentosd`
- `agentosd monitor` — v0 read-only VRAM + Ollama `/api/ps` monitor (does nothing
  destructive; proves NVML + pressure math against the real GPU).
- `agentosd feed [--once]` — P1 producer: read-only Hermes fleet state
  (`kanban.db` + `gateway_state.json`) → `$XDG_RUNTIME_DIR/nimbus-aurora/agent.json`
  for the reactive wallpaper. Install as a `--user` service via
  `crates/agentosd/dist/{apply,restore}.sh`.

## Relationship map
```
Hermes (~/.hermes) ── inference ──▶ agentosd proxy ──▶ Ollama (:11434)
Hermes plugin      ── D-Bus lease/priority ──▶ agentosd
agentosd           ── NVML read + `ollama stop` + nimbus-flux kill/relaunch ──▶ GPU
Nimbus desktop     = consumer of agentosd (theme/wallpaper agents call the tx API)
```

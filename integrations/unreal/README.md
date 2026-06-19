# integrations/unreal — Unreal Engine MCP lane (ADR-0022, experimental Linux)

AgentOS-side scaffolding to pair **Unreal Engine** (via MCP) to the agent harness, as a sibling to the
Blender forge lane. **Read [ADR-0022](../../docs/adr/0022-creative-app-mcp-blender-unreal.md) §8 first** —
Unreal was *deferred behind a Linux render-feasibility gate*. The human is now installing UE on Linux
(precompiled binary) and lifting that deferral for an **experimental** lane; the gate's caveat still
stands (see "Render reality"), so treat this as a spike until proven.

> Status (2026-06-18): **LIVE + VERIFIED on UE 5.8.0 (Linux).** `editor_run_python` round-trips into the
> running editor (`unreal.SystemLibrary.get_engine_version()` returned `5.8.0-…` via the MCP). Wiring:
> `~/UnrealEngine` (precompiled binary) → `launch.sh` (blank project + RE config + loopback route + editor)
> → `unreal` route added to the forge `.mcp.json`.
>
> **Cold-start caveat:** runreal discovers the editor over multicast on server start (npx boot + up to ~5s).
> The **first** tool call right after the server launches can return "Remote node is not available" if it
> fires before discovery completes — it succeeds once warm. (Project `DefaultEngine.ini` `bRemoteExecution`
> is what enables RE; the per-user `DefaultEditorPerProjectUserSettings.ini` copy `launch.sh` also writes is
> belt-and-suspenders.)

## The pieces

1. **MCP server — `runreal/unreal-mcp`** (MIT, `npx -y @runreal/unreal-mcp`, **stdio**). Chosen because it
   needs **no compiled UE plugin**: it drives the editor through UE's built-in **Python Remote Execution**.
   - **No env vars** — discovery is hardcoded (UDP multicast `239.0.0.1:6766`, binds `0.0.0.0`,
     auto-discovers the editor; TCP command channel on `6776`). So `unreal.mcp.json` is just `command`+`args`.
   - **15 MCP tools**; the workhorse is **`editor_run_python`** (arbitrary `unreal` Python). `editor_take_screenshot`
     is a **640×520 viewport** grab only. **There is no Movie Render Queue tool** — a real render goes
     through `editor_run_python` (emit `unreal.MoviePipeline…`) or, better for batch, a direct
     `UnrealEditor-Cmd … -MoviePipelineConfig=…` job under the lease (same shape as the Blender lane).
   - **Needs a live editor with a project open** (not a headless commandlet). UE **5.4+**.
   - **Supply-chain:** MIT, signed tarball, no install hooks, tiny clean dep tree (`@modelcontextprotocol/sdk`,
     `unreal-remote-execution`, `zod`) → low-risk. Caveat: **effectively unmaintained** (v0.1.4, last
     publish 2025-06), and `editor_run_python` = arbitrary code in the editor — so keep Remote Execution
     local-only (TTL 0, below) and gate the lane behind the VRAM lease.
   - **Alt:** Epic's first-party MCP ships *inside* **UE 5.8** (HTTP `http://127.0.0.1:8000/mcp`, enable the
     "Unreal MCP" plugin) — editor-automation only, no render. Swap to it if you pull 5.8 and prefer the
     house base; on 5.6 only the Python-RE path exists.
2. **Route** — `unreal.mcp.json` is the snippet to merge into the forge `.mcp.json`
   (`~/whitesur-cachyos-pack/.mcp.json`), beside the existing `blender` route. (Exact match to runreal's
   documented block — do not add an `env` map.)
3. **Editor prerequisites** (handled by `launch.sh`, or one-time by hand):
   - Enable the **Python Editor Script Plugin** (`Edit → Plugins`, search "Python"; **not guaranteed on by
     default** — enable + restart).
   - Enable Remote Execution: `Edit → Project Settings → Plugins → Python → "Enable Remote Execution?"`,
     which writes — in **`<Project>/Config/DefaultEngine.ini`**:
     ```ini
     [/Script/PythonScriptPlugin.PythonScriptPluginSettings]
     bRemoteExecution=True
     RemoteExecutionMulticastGroupEndpoint=239.0.0.1:6766
     RemoteExecutionMulticastBindAddress=0.0.0.0
     RemoteExecutionMulticastTtl=0
     ```
     (`Ttl=0` keeps the multicast on this host. The section is `…PythonScriptPluginSettings` — **no `User`**.)
   - **Linux gotcha (load-bearing):** Linux won't deliver `239.0.0.1` multicast over loopback by default, so
     same-host editor+MCP needs a one-per-boot route:
     ```bash
     sudo ip route add 239.0.0.1 dev lo
     ```
     Without it the MCP reports "no nodes found" even though the editor is up.

## Wiring it (once `UnrealEditor` is on disk)

```bash
UE_ROOT=~/UnrealEngine integrations/unreal/setup.sh    # 1. non-destructive pre-flight (verify binary/npx)
UE_ROOT=~/UnrealEngine integrations/unreal/launch.sh    # 2. create blank project + RE config + lo route + launch editor
# 3. merge unreal.mcp.json into ~/whitesur-cachyos-pack/.mcp.json (done via review, not by a script)
# 4. the harness starts the server per the route: npx -y @runreal/unreal-mcp
```

## Render reality (the ADR-0022 §8 caveat — still true)

On Linux UE gives the **Vulkan rasterizer / Lumen** only — the **Path Tracer (film-quality MRQ) is
Windows/DX12-only**, and Vulkan path-tracing on Linux is experimental/flaky on NVIDIA. So this lane does
editor automation + **rasterized** renders, not path-traced beauty frames. For the calm/abstract
"dreaming" aesthetic, Blender EEVEE (already lease-gated, ADR-0022 §6) + ComfyUI/Wan remain primary; treat
UE output as an additional on-demand texture source.

## VRAM coordination plan (NOT the Blender path)

The Blender lane uses `AdoptScope` (cgroup-scope reclaim) because flatpak reparents Blender into a systemd
scope. A **precompiled UE binary is not flatpak** — it runs in the session normally — so its lane is
coordinated differently:
- **agentosd-launched headless render** → an allowlisted `Spawn` profile (ADR-0013): agentosd owns the PID
  and reclaims by **process-group SIGKILL** (the existing `Reclaim::Spawned` path; cap UE's VRAM in the
  wrapper so a heavy scene fails its frame, not the driver — same discipline as the deferred Blender Cycles
  render). A `UnrealEditor-Cmd … -RenderOffscreen -MoviePipelineConfig=…` render is the natural `Spawn`.
- **Interactive editor the human launched** → admit-before-launch only for now (read-only `Status` gate),
  same as Blender Phase 0. Owned reclaim of a *user-launched* editor is a later step — and only here would
  the Blender-style scope reclaim apply, *if* the editor ends up in its own systemd scope (observe the
  process tree once it runs; a directly-launched binary is just a child of the shell, reclaimed by PID).

This is design intent; the actual lease profile lands after the editor process is observed running.

## Files

| File | Purpose |
|---|---|
| `unreal.mcp.json` | the `unreal` route snippet to merge into the forge `.mcp.json` |
| `setup.sh` | non-destructive pre-flight: verify `UnrealEditor` + `npx`, print the route + next steps |
| `launch.sh` | create a blank project, write the RE config, add the loopback multicast route, launch the editor |

## Sources (verified 2026-06-18)

- runreal/unreal-mcp — https://github.com/runreal/unreal-mcp (run cmd, hardcoded discovery, 15 tools, UE 5.4+)
- nils-soderman/unreal-remote-execution — https://github.com/nils-soderman/unreal-remote-execution (Linux `ip route add 239.0.0.1 dev lo`, bind `0.0.0.0`)
- Epic Remote Execution protocol (ports 6766/6776, group 239.0.0.1, TTL 0) — EpicGamesExt/BlenderTools `remote_execution.py`
- Epic UE 5.8 "Unreal MCP" (native HTTP `:8000/mcp`, Experimental) — https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor

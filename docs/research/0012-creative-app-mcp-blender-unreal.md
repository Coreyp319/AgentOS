# Research 0012 — Pairing Blender + Unreal MCP servers to the harness (Claude Code → Hermes)

- Status: **Research complete (2026-06-17).** Fan-out pass: three web-research angles (Blender MCP
  landscape, Unreal MCP landscape, MCP integration best-practices) + one codebase-grounding pass over
  the AgentOS lease / MCP / dreaming seams. No code changed; this is the decision-input note behind
  **ADR-0022**.
- Question: *How do we wire a Blender MCP server and an Unreal Engine MCP server to an agent harness
  (Claude Code now, Hermes later) to feed the "dreaming"/ambient creative pipeline — with best
  practices baked in from the start, and the VRAM lease integration designed in (not bolted on)?*
- Method: WebSearch → WebFetch primary sources (MCP spec, Anthropic Claude Code docs, the server
  repos themselves, security write-ups + CVEs) → cross-check against the codebase. Claims carry
  source URLs; items that could not be confirmed against a primary source are tagged `[UNVERIFIED]`.
- Relates to: ADR-0001 (substrate not orchestrator — the don't-reinvent rule), ADR-0003
  (fail-open-supervised), ADR-0006/0010/0013 (the lease + Spawn-profile allowlist this rides on),
  ADR-0009 (dreaming: shader primary, video/3D as *texture*), ADR-0018 (VRAM coexistence
  warm-pool/heavy-lane), ADR-0019 (reviewable request queue — no-prioritization invariant), ADR-0020
  (agent-facing GPU MCP — the house pattern for "MCP over the lease"), ADR-0021 (agent tier-clamp +
  identity-binding). Codebase anchors: `crates/agentosd/src/{lease,coord,mcp,keyhole}.rs`,
  `apps/dreaming/{comfy_client.py,dream.sh,lucid/}`, `spikes/kwin-mcp/`, `~/.hermes/config.yaml`.

## Bottom line

Three findings reorder the obvious "wire up both servers" plan:

1. **Blender is a clean fit; Unreal-on-Linux is a different-magnitude lift.** Blender is Linux-native,
   scriptable headless (`blender -b --python`), and its dominant MCP server is MIT — it maps directly
   onto our existing `Spawn`-profile + heavy-lane machinery. Unreal on Linux is **source-build-only**,
   its film-quality renderer (**Path Tracer**) is **Windows/DX12/RTX-only**, and it is OOM-fragile on a
   24 GB card. Unreal should be **research-validated and deferred**, not built alongside Blender.
2. **The popular servers ship an arbitrary-code-execution tool** (`execute_blender_code`,
   `execute_python`) = full RCE inside the editor process. This is the exact "model proposes, code
   disposes" collision ADR-0020 already resolved for the GPU surface — so we reuse that posture:
   deny the code tool, expose only a curated typed surface, gate every effect through code.
3. **The MCP server is *not* the GPU consumer — the render is.** Scene authoring over MCP is light;
   the **Cycles GPU render** (Blender) and **MRQ render** (Unreal) are the heavy, unbounded VRAM jobs.
   They belong in a `Spawn` profile in the heavy lane (ADR-0018), SIGKILL-on-preempt — and local
   text/image→3D should route through the **ComfyUI lease we already run**, not the servers' cloud APIs.

Net: **adopt Blender MCP (don't fork), harden it as a curated, sandboxed, lease-gated asset source;
defer Unreal behind a Linux-feasibility gate.** Almost everything we need (lease, Spawn profiles, the
`agentosd mcp` pattern, ComfyUI gen, the lucid anchor-frame seam, Hermes' MCP guardrails) already
exists; the net-new surface is a hardened bridge wrapper, a `blender-render` profile, and the
pipeline frame-source hook.

---

## 1. Blender MCP landscape

### 1.1 The dominant server — `ahujasid/blender-mcp` (MIT)

- Repo: https://github.com/ahujasid/blender-mcp · PyPI `blender-mcp` · ~22.9k★, active, **MIT**.
- **Architecture (two processes):** a Blender **add-on** (`addon.py`) hosts a JSON-over-TCP socket
  server inside Blender on `localhost:9876` (env-overridable); a separate **MCP server**
  (`src/blender_mcp/server.py`) uses **FastMCP (official `mcp` Python SDK) over stdio** and relays to
  the add-on. Source: https://github.com/ahujasid/blender-mcp/blob/main/src/blender_mcp/server.py
- **Tool surface:** `get_scene_info`, `get_object_info`, `get_viewport_screenshot`,
  **`execute_blender_code`**, PolyHaven (`search/download_polyhaven_asset`, `set_texture`), Sketchfab
  (`search/download_sketchfab_model`), Hyper3D Rodin (`generate_hyper3d_model_via_text/_images`,
  `poll_rodin_job_status`, `import_generated_asset`), Hunyuan3D (`generate_hunyuan3d_model`, …).
- **CRITICAL — arbitrary code: YES, unsandboxed.** `execute_blender_code` ships LLM-authored Python
  over the socket and the add-on runs it via `exec(code)` with no AST validation, allow-list, or
  confirmation — full Blender Python API + host-OS reach. The README itself warns it is "powerful but
  potentially dangerous." Open issue #207 documents the RCE class (env-var exfil, file read, reverse
  shell). https://github.com/ahujasid/blender-mcp/issues/207
- **Tool-poisoning precedent (patched):** issue #214 — the Rodin/Hunyuan tool *descriptions* once
  instructed the model to silently track whether the user's API key was free-trial vs paid and not
  tell the user (classic poisoned-description). Fixed via PR #237, but it proves the description
  channel is a live injection surface. https://github.com/ahujasid/blender-mcp/issues/214
- **Generation path is cloud-only:** PolyHaven/Sketchfab assets + Hyper3D Rodin + Hunyuan3D are all
  **network APIs** (keys + egress; prompts/images leave the machine). This conflicts with the
  local-first non-negotiable. (Tripo is *not* in this server, contrary to common belief — it appears
  only in forks.)
- **Install:** add-on installed in Blender; server via `uvx blender-mcp`; Claude Code:
  `claude mcp add blender uvx blender-mcp`. Supports Blender 3.0+, Python 3.10+.

### 1.2 Alternatives worth knowing

| Server | License | Transport | Arbitrary code? | Gen integrations | Maturity |
|---|---|---|---|---|---|
| **ahujasid/blender-mcp** | MIT | stdio (FastMCP) + TCP:9876 | **Yes** (`execute_blender_code`, no guard) | Rodin/Hunyuan3D/Sketchfab/PolyHaven — **all cloud** | dominant, ~22.9k★ |
| **Official Blender Lab MCP** | `[UNVERIFIED — likely GPL]` | stdio + TCP socket | **Yes** (exposes Python API: NL→Python) | none built-in | first-party, **experimental** |
| **RFingAdam/mcp-blender** | **AGPL-3.0** | stdio + TCP | Yes (`execute_script`) | **local**: TripoSR / Stable Fast 3D / Hunyuan3D / **ComfyUI** + Ollama-vision loop | 218 tools, young (~3★) |
| **djeada/blender-mcp-server** | MIT | stdio + TCP:9876 | Yes (`blender_python_exec`, **disable-able in prefs**) | none | render + job tools, small |

Sources: https://www.blender.org/lab/mcp-server/ · https://github.com/RFingAdam/mcp-blender ·
https://github.com/djeada/blender-mcp-server · directory: https://www.pulsemcp.com/servers?q=blender

- **Official Blender Lab MCP** is the strategic one to watch: first-party, same add-on+socket shape,
  positioned as an NL→Python-API bridge. Still experimental, and because it surfaces the Python API
  it inherits the same code-exec class. License/sandbox posture `[UNVERIFIED]` (blender.org/lab 403'd).
- **RFingAdam/mcp-blender** is the only server that already wires **local** text/image→3D (TripoSR,
  Stable Fast 3D, Hunyuan3D, ComfyUI) — exactly our locality story — but it is **AGPL-3.0** (copyleft,
  a distribution constraint to weigh) and immature. The cleaner AgentOS path is to reuse our own
  ComfyUI lease for local 3D gen rather than adopt AGPL code.

### 1.3 GPU / render profile (Blender)

- **Authoring vs rendering split:** MCP scene authoring (create/query/script) is CPU/RAM-bound and
  barely touches VRAM. The GPU cost is **rendering** (Cycles GPU via CUDA/OptiX, or EEVEE) and any
  local 3D-model inference. https://docs.blender.org/manual/en/latest/render/cycles/gpu_rendering.html
- **VRAM is scene-dependent and effectively unbounded:** Cycles loads geometry + textures + BVH into
  VRAM and spills/fails past the card's capacity. Planning figures range from single-GB simple scenes
  to 24–48 GB for heavy archviz/VFX; GPU-OOM is a known ~15% of render failures.
  https://superrendersfarm.com/article/best-gpu-for-3d-rendering-2026 — **no fixed estimate is safe;
  treat it as a variable, conservatively-reserved heavy job.**
- **Headless rendering works and is the right path:** `blender -b <file> --python <script>
  --cycles-device OPTIX/CUDA` renders without a GUI — a discrete, evictable batch job. (Note a
  reported regression where headless no longer auto-selects GPUs; set the device explicitly in the
  Python script.) https://devtalk.blender.org/t/headless-rendering-no-longer-automatically-picking-up-gpus/12176

### 1.4 Local text/image→3D (the locality story)

- ahujasid's gen is cloud-only (Rodin/Hunyuan, keys + egress). Local options that match AgentOS:
  **Hunyuan3D-2/2.1** (open, shape ~6 GB / shape+texture ~12–16 GB, exports OBJ/FBX/glTF —
  https://github.com/Tencent-Hunyuan/Hunyuan3D-2), **TripoSR** (MIT, image→3D, ~6–8 GB —
  https://www.triposrai.com/), **Stable Fast 3D**. These already run as ComfyUI workflows — so the
  AgentOS move is to route 3D gen through the **existing ComfyUI lease**, not the cloud tools.

---

## 2. Unreal Engine MCP landscape

### 2.1 Headline: Epic now ships a first-party MCP

- **Epic official `ModelContextProtocol` plugin** — UE **5.8**, Experimental, embedded **in the editor
  process**, **HTTP+SSE on `127.0.0.1:8000/mcp`**, curated tools + automation-test running, **no auth
  layer (loopback-only)**. This did not exist when the community projects were written and makes it
  the default-correct base *if* you can target 5.8+.
  https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor

### 2.2 Community servers (all independent, ~March 2025 vintage)

| name | license | bridge | sidecar? | arbitrary-code? | MRQ/headless render tool? | UE ver | maturity |
|---|---|---|---|---|---|---|---|
| **flopperam/unreal-engine-mcp** | MIT *claim, no LICENSE file* | C++ TCP plugin + Py sidecar | yes | **No** (typed, ~42) | No (render lives in closed hosted product) | 5.5–5.7 | **active** (2026-05), 1k★ |
| chongdashu/unreal-mcp | MIT *claim, no LICENSE file* | C++ TCP plugin + Py sidecar | yes | **No** (typed) | No | 5.5+ | **dormant ~14mo**, ~2k★ |
| prajwalshettydev/UnrealGenAISupport | **MIT** | Python listener + Py sidecar | yes | **YES** (`execute_python_script` + console) | No typed | 5.4–5.7 | repo active, **MCP de-prioritized** |
| kvick-games/UnrealMCP | MIT *claim, no LICENSE file* | C++ TCP plugin + Py sidecar | yes | **YES** (`execute_python`) | No | 5.5 only | **stalled ~1yr**, "VERY WIP" |
| runeape-sats/unreal-mcp | **MIT** | Remote Control HTTP (:30010), pure Py | yes | No (broad `remote_call`) | No | **5.3 only** | tiny, tidy |

Sources: per-repo READMEs/source + https://github.com/runeape-sats/unreal-mcp ·
https://github.com/flopperam/unreal-engine-mcp · https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-for-unreal-engine

- **License hygiene:** only runeape + UnrealGenAISupport carry a real LICENSE file; the three
  most-starred community repos *claim* MIT in the README but ship **no LICENSE** (not a clean grant).
- **All UE control surfaces are unauthenticated localhost** (MCP :8000, Remote Control :30010, Remote
  Execution :6766). Epic's own warning: do not expose them beyond the LAN.

### 2.3 The render bridge is the engine layer, not the MCP layer

- **No community OSS server ships Sequencer / Movie Render Queue / Niagara / headless-render tools** —
  they are all actor/level/Blueprint authoring tools. The render path is Epic's **command-line MRQ**:
  `UnrealEditor-Cmd "proj.uproject" Map -game -LevelSequence=… -MoviePipelineConfig=… -resx -resy`,
  outputting EXR/PNG/JPG sequences or encoded video, drivable via a Python custom executor
  (`MoviePipelinePythonHostExecutor`). https://dev.epicgames.com/documentation/unreal-engine/using-command-line-rendering-with-move-render-queue-in-unreal-engine
- **So the practical pipeline = MCP for scene/asset setup, then MRQ command-line as a separate
  headless batch job** — which is exactly the `Spawn`-profile shape Blender uses.

### 2.4 The Linux + 24 GB wall (why Unreal is deferred)

- **Path Tracer (film-quality MRQ) is NVIDIA-RTX + DirectX 12 + Windows only — not available on
  Linux.** https://dev.epicgames.com/documentation/en-us/unreal-engine/path-tracer-in-unreal-engine
- **The Linux editor gives rasterizer/Lumen output only, no path tracing.** Vulkan RHI is "not friendly
  to low VRAM," and Vulkan ray-tracing/path-tracing on Linux is *experimental* (UE 5.8 claims DX12 parity
  but real NVIDIA-on-Linux reports show failed RT profile checks).
  https://dev.epicgames.com/documentation/en-us/unreal-engine/linux-development-requirements-for-unreal-engine
  `[CORRECTION 2026-06-17]` The earlier "no precompiled Linux binary — build from source" claim is
  **outdated**: Epic now ships distribution-agnostic precompiled Linux binaries (run
  `Engine/Binaries/Linux/UnrealEditor`), so the install-cost argument is weaker — but this does **not**
  flip the gate (still no Linux path tracer, still the 24 GB collision below).
- **Path-traced MRQ is OOM-fragile on 24 GB even on Windows:** users hit OOM on 24 GB cards with MRG
  path tracing in UE 5.6 (worked on 5.5; succeeded only on 32 GB 5090s), plus Nanite+RT VRAM-leak
  reports. https://forums.unrealengine.com/t/5-6-path-tracing-crash-increased-vram-usage/2602480
  `[UNVERIFIED]` — these are forum figures, not an Epic VRAM table.
- **Headless (`-game`/`-RenderOffscreen`) removes the *window*, not the GPU requirement.**

**The single biggest open question for Unreal:** *On Linux + 24 GB (Vulkan rasterizer/Lumen, no path
tracer), is rasterized output good enough for the creative pipeline — or does Unreal implicitly require a
Windows render node?* Until answered, Unreal stays out of the build.

> **Re-validated 2026-06-17 (parallel-subagent pass).** Local probe: the shared 4090 has **~5.8 GB free**
> (ComfyUI resident at ~15 GB), so a UE editor (3–16 GB) would OOM the card. **First-party UE 5.8 MCP
> shipped** (the predicted "future base" is now real) but is **editor-automation only — no render/MRQ**.
> Best community fit for a future spike: **runreal/unreal-mcp** (MIT, precompiled Linux binaries,
> plugin-free Python Remote Execution → MRQ + screenshot — same code-send shape as the Blender lane). Net:
> deferral **holds** on the durable reasons (no Linux path tracer, the 24 GB VRAM collision, and EEVEE +
> ComfyUI/Wan already cover the calm/abstract aesthetic, ADR-0022 §6). Cheapest re-test if revisited: one
> headless `UnrealEditor-Cmd … -RenderOffscreen -MoviePipelineConfig=…` frame watched under `nvidia-smi`.

---

## 3. The integration architecture (best practices)

### 3.1 Transports & topology

- **Two standard transports** (MCP spec 2025-11-25): **stdio** (preferred for local; client launches
  the server as a subprocess; **strictly 1:1**) and **Streamable HTTP** (independent process, **multi
  -client**; SSE is deprecated). https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- **Blender/Unreal are sidecar bridges**, not pure stdio tools: `harness ↔ (stdio|HTTP) MCP server ↔
  (localhost TCP socket) in-editor add-on/plugin`. The editor must already be running.
- **stdio cannot serve two harnesses.** Two harnesses each spawning their own stdio copy = two server
  processes fighting over one editor + one GPU — a correctness *and* VRAM hazard. **For Claude Code +
  Hermes, the bridge must be a single shared, localhost-bound, authenticated Streamable HTTP server.**
- **Transport security (spec, normative for HTTP):** validate `Origin` (DNS-rebinding), **bind
  127.0.0.1 only** (never 0.0.0.0), require auth, non-deterministic session IDs, never use sessions
  for auth, **never pass tokens through.** https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices

### 3.2 Claude Code wiring

- `.mcp.json` `mcpServers` entries: stdio (`command`/`args`/`env`) or `type:http` (`url`/`headers`);
  per-server `timeout` (important — renders are long). **Scopes:** local (default, `~/.claude.json`),
  **project** (`.mcp.json`, version-controlled, prompts for approval), user. Secrets via `${VAR}`
  expansion — never commit tokens. https://code.claude.com/docs/en/mcp
- **Permissions** are evaluated deny→ask→allow (first match wins); rules use `mcp__<server>__<tool>`.
  The hardening pattern: **`deny` the arbitrary-code tool**
  (`mcp__blender__execute_blender_code`), `allow` only the specific safe tools, leave the rest on
  `ask`. Do **not** set `enableAllProjectMcpServers` for a high-privilege server.
  https://code.claude.com/docs/en/permissions · `managed-mcp.json` can lock the server set org-wide.

### 3.3 Two harnesses → one server: gateway + registry

- Run the bridge **once** as a supervised localhost HTTP service (systemd `--user`, matching our
  `feed`/`keyhole`/`lease` units) — the single owner of the editor and therefore the natural **VRAM-
  lease holder**. Claude Code auto-reconnects HTTP servers with backoff but **does not auto-restart
  stdio servers** — another reason a long-lived editor bridge should be HTTP + supervised.
- **Front it with an MCP gateway** for one audited control point: auth, policy, **egress allowlist**,
  version pinning, unified tool-call audit log. Both harnesses register against the gateway rather
  than duplicating config. Implementations: Docker MCP Toolkit/Gateway
  (https://docs.docker.com/ai/mcp-catalog-and-toolkit/mcp-gateway/), IBM ContextForge, mcp-proxy;
  discovery via the official MCP Registry (reverse-DNS namespace auth,
  https://registry.modelcontextprotocol.io/). OWASP MCP cheat sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html

---

## 4. Threat model & hardening (high-privilege creative-app MCP)

The MCP spec is blunt: local servers enable **"arbitrary code execution … with MCP client
privileges,"** data exfiltration, and irrecoverable data loss. For Blender/Unreal this is literal.

- **Tool poisoning** — malicious instructions in a tool *description* (model-visible, user-invisible).
  https://simonwillison.net/2025/Apr/9/mcp-prompt-injection/ · https://github.com/invariantlabs-ai/mcp-injection-experiments
- **The lethal trifecta** — private data + untrusted content + exfiltration capability = attacker
  control; prompt injection is "fundamentally unsolved." (same source)
- **Line jumping** — injection delivered in the `tools/list` handshake, before any call.
- **Rug pull** — tool description swapped to malicious *after* approval (defeats one-time review).
- **Confused deputy / token passthrough** — spec forbids accepting tokens not issued for the server.
- **Real 2025 CVEs:** `mcp-remote` OS-command-injection **CVE-2025-6514 (CVSS 9.6)**; Anthropic
  Filesystem MCP sandbox escapes CVE-2025-53110/53109; Git MCP RCE CVE-2025-53107; Figma MCP RCE
  CVE-2025-53967. https://jfrog.com/blog/2025-6514-critical-mcp-remote-rce-vulnerability/ ·
  https://www.ox.security/blog/mcp-supply-chain-advisory-rce-vulnerabilities-across-the-ai-ecosystem/

**Hardening the spec + Anthropic + the security literature converge on:**
- **Loopback is not an authorization boundary.** The dominant Blender server's *real* RCE primitive is
  the **unauthenticated in-editor socket on `localhost:9876`** that `exec()`s received Python (§1.1) —
  any local process can reach it, bypassing every harness-level deny rule. The defining control is to
  **sever that socket from the host loopback** (bind it inside the sandbox namespace / a uid-scoped
  unix socket) so the only route into the editor is the curated tool surface.
- **Sandbox the server+editor** — dedicated unprivileged user / namespace (bubblewrap, firejail) or
  VM; **read-only FS** except a designated work dir; **no inherited host credentials/env.** (The
  GPU+display wrinkle: a live editor needs GPU/display, so this is a dedicated user + GPU passthrough
  or a disposable VM, not a trivial `docker run` — `[UNVERIFIED]` canonical pattern for GPU-editor
  sandboxing.) https://github.com/containers/bubblewrap · https://arxiv.org/pdf/2511.20920
- **Egress allowlist** — restrict outbound to exactly the gen-API endpoints (if cloud gen is enabled)
  + PyPI; block everything else. Kills exfiltration + SSRF while permitting intended use.
- **Pin versions** — never `uvx <pkg>` (runs latest = live rug-pull surface); pin package + Python;
  hash tool descriptions on approval and re-prompt if they change.
- **Deny the code-exec tool; human-in-the-loop on all mutating/destructive tools.**
- **Audit-log every tool call** (tool, args, response, session) — centralize at the gateway.

---

## 5. AgentOS integration seams (from the codebase)

Everything below already exists; the new integration attaches to these points.

1. **Lease / Spawn profiles** — `org.agentos.Coordinator1` (`lease.rs:674`).
   `Spawn(tier, est_mib, profile, params) → (granted, token, msg)` resolves `profile` against a
   daemon-owned allowlist `const PROFILES` (`lease.rs:56`; today `comfyui`, `sleep`). **A Blender
   render is a new tuple** `("blender-render", &["/abs/path/render-wrapper.sh"])`. Tiers
   `Interactive>Batch>BestEffort` (`coord.rs:44`); `CallerClass::clamp` caps agent callers at
   **Batch** *inside the core* (ADR-0021 GO-1); `may_release` binds release to the acquiring identity
   (GO-2). Owned jobs are SIGKILLed on preempt and auto-released on natural exit / TTL (ADR-0013).
2. **MCP-over-substrate pattern** — `agentosd mcp` (`mcp.rs`): JSON-RPC 2.0 over stdio, read-only,
   honesty-first (`status: unavailable` never a fabricated free GPU). This is the template; the
   creative bridge is a *different* (Python sidecar) server but inherits the two-tier perceive/act
   trust split and the "code disposes" boundary from ADR-0020.
3. **Dreaming/lucid pipeline seam** — `lucid_engine.py` consumes an **anchor frame** (`--image` /
   `--from <clip>`); a Blender/Unreal render output drops in there as another frame/anchor source,
   consistent with ADR-0009 (3D/video as *texture*, shader stays primary). `dream.sh` already wraps
   ComfyUI in `Spawn(...,"comfyui",...)`/`Release`; the lucid engine itself does **not yet** take the
   lease (throwaway spike) — hardening it through the lease is a prerequisite, not new scope here.
   Job model: `lucid_jobs.py` status records (`queued→checking→generating→ready/held/…`).
4. **ComfyUI client** — `comfy_client.py` (`run-template`, `generate`, `free_vram` via `POST /free`)
   is the local-gen lever; local text/image→3D (Hunyuan3D/TripoSR) runs as ComfyUI workflows here.
5. **Hermes guardrails inherited** — `~/.hermes/config.yaml` `mcp_servers`; `threat_patterns.py`
   (prompt-injection/exfil scan of tool results), `schema_sanitizer.py` (local-backend schema fixes),
   `slash_confirm.py` (human confirmation for side-effecting ops), `mcp_oauth_manager.py` (token
   lifecycle). A new MCP server registered with Hermes inherits all four.

---

## Best-practices checklist (carried into ADR-0022)

1. **Adopt, don't fork** the Blender server (ahujasid, MIT); pin the version; watch the official
   Blender Lab MCP as the successor.
2. **Curated surface only** — `deny` `execute_blender_code`; expose scene-query + object/asset tools;
   any code path is a deterministic-gated proposal (code disposes).
3. **Render = `Spawn` profile in the heavy lane**, conservatively reserved (Cycles VRAM unbounded),
   agent tier clamped to Batch, identity-bound release, SIGKILL-on-preempt.
4. **Local-first gen** — route text/image→3D through the existing ComfyUI lease; cloud gen tools
   denied by default, opt-in only with consent + egress allowlist.
5. **Two-harness topology** — one supervised localhost HTTP bridge (the lease holder), fronted by a
   gateway (audit / egress / version-pin); both Claude Code and Hermes register against it.
6. **Sandbox + egress-allowlist** the bridge+editor; read-only FS except a work dir; no host secrets.
7. **No auto-approve**; project-scope approval prompt; secrets via `${VAR}`; review tool descriptions
   on every version bump.
8. **3D output is a frame/texture source** for the lucid pipeline (ADR-0009), not a new primary
   surface — and the lucid engine must itself go through the lease first.
9. **Defer Unreal** behind a documented Linux-feasibility gate; if/when built, mirror the Blender
   pattern (curated tools + `unreal-render` MRQ Spawn profile + shared HTTP/gateway).

## Recommendation → ADR-0022

Build the **Blender** integration as a hardened, sandboxed, lease-gated, curated-surface asset source
feeding the dreaming pipeline — Claude-Code-first, with the shared-HTTP + gateway topology designed
in for Hermes — and **defer Unreal** as research-validated-but-not-built pending Linux render
feasibility. See ADR-0022 for the decision, sequencing, and open questions.

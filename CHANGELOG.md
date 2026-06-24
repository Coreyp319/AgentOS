# Changelog

All notable changes to AgentOS are recorded here. The format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project uses
[Semantic Versioning](https://semver.org/) (pre-1.0: minor = features, patch = fixes).

## [0.1.0] — 2026-06-24

First public release. AgentOS is a small Rust **resource + safety substrate**
(`agentosd`) that sits under the Hermes agent orchestrator and a CachyOS / KDE
Plasma 6 desktop, arbitrating a single GPU so local AI (LLM inference, image &
video generation) and the live desktop coexist without OOM or wedging. It is
*not* a new OS, distro, or orchestrator — it is the floor under both.

### The substrate (`agentosd`)
- **VRAM coordinator + lease daemon** (`agentosd lease`) — a D-Bus service
  (`org.agentos.Coordinator1`) with predict-before-load admission, cooperative
  `Acquire` (the caller owns its process) and owned `Spawn` (agentosd spawns and
  can SIGKILL on preempt), priority tiers, and fail-open-supervised safety
  (ADR-0006 / ADR-0010).
- **Cross-workflow VRAM-demand queue** (`agentos queue`, `org.agentos.Queue1`) —
  a separate arbiter that serializes GPU demand across workflows so concurrent
  jobs queue instead of freezing the box (ADR-0041).
- **Graphics-yield coexistence** — proactively throttles the live wallpaper
  before denying compute; it never kills the desktop from under you
  (ADR-0004 / ADR-0029 / ADR-0030).
- **Read-only instruments** — `monitor`, `feed`, and `keyhole` producers feed the
  ambient wallpaper and the Keyhole tray (live GPU / lease / queue / fleet state),
  with honest UNKNOWN states (ADR-0012).

### Dreaming / Lucid
- **Lucid** — an interactive, branching "dreaming" video loop on local ComfyUI
  (Wan 2.2 / Hunyuan 1.5 / LTX-2), leased through the substrate. Includes
  prompt-guided keyframe edit, grounding gates, save/reopen with an encrypted
  private stash, one-file MP4 download, a two-tier quality path, anticipatory
  model pre-warm, and a phone PWA surface with push notifications
  (ADR-0014 / ADR-0015 / ADR-0040 / ADR-0037 / ADR-0028 / ADR-0033 / ADR-0045 / ADR-0047).
- **AgentOS Share** — send a photo from your phone to the box to turn into a
  video (ADR-0027).
- Graduated the live dreaming + Lucid stack out of `spikes/` into a real `apps/`
  home (ADR-0046).

### Desktop & ambient
- **Reactive wallpaper** (Nimbus Aurora) — agent.json-driven mood; the **Prism**
  shader is the default, and dragging a window sends a gust of wind across it.
- **Aurora theme** — a cohesive violet light/dark scheme via the Plasma 6.7 Union
  CSS style (ADR-0042).
- **Plasma-native notifications** with a portal cold-boot timeout fix.
- Optional **UE 5.8 "dark-ride" wallpaper** lane (the Prism Field scene),
  throttled by the coordinator (ADR-0023 / ADR-0029).

### Onboarding & integrations
- **Setup wizard** — a guided "first light" flow: brownfield-adopt an existing
  Ollama / ComfyUI / model install, fetch curated bundles, and reach a first
  reviewable result (ADR-0044).
- **Component registry** (`integrations/components.conf` + `install.sh`) with
  one-click progressive adopt/remove, and dispatch-an-agent-to-investigate-a-
  down-service (ADR-0043 / ADR-0039).
- Remote access over Tailscale; right-click "Create Video from Image" for Dolphin
  and Firefox; a KRunner "ask Claude / Hermes / web" finder.

### AMD GPU support (experimental)
- A `GpuBackend` sensing layer — NVML for NVIDIA, sysfs + `/proc` fdinfo for AMD —
  so the coordinator runs on Radeon as well as GeForce, plus a ROCm install lane,
  a ROCm torch index in the setup flow, and AMD ComfyUI launch flags. RDNA3 floor /
  RDNA4 recommended; video is slower and there is no fp8 on RDNA3 (ADR-0048).

### Notes
- Single-user, single-GPU, local-first. Reversible by default; model proposes,
  code disposes. MIT-licensed.
- Built and validated on an RTX 4090 (24 GB) / CachyOS / Plasma 6.7. AMD support
  is new and experimental.

[0.1.0]: https://github.com/Coreyp319/AgentOS/releases/tag/v0.1.0

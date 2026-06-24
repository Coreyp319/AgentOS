# ADR-0048 — AMD GPU support: a sysfs sensing backend + a ROCm install lane

Status: Accepted — decisions locked with the user 2026-06-24. **Phases 0–3 BUILT + COMMITTED** on
`feat/amd-support` (4 commits); two-lens review (resource-safety + rust-performance) = SHIP; the
NVIDIA path is behavior-preserving (229 crate + 95 setup tests green, clippy clean, validated live on
the 4090). The **one item the 4090 can't cover — running the AMD path on a real Radeon — is owed**.
Additive to the NVIDIA path, which stays the default. Supersedes nothing.
Date: 2026-06-24
Feeds from: [docs/research/0014-amd-gpu-support.md](../research/0014-amd-gpu-support.md) (the
four-lane sourced study this records the decisions of).
Relates to / inherits: ADR-0001 (don't-reinvent — reuse ROCm/`ollama-rocm`/PyTorch-ROCm; build
only the thin sensing glue), ADR-0002 (configure Ollama, don't rebuild — the AMD backend is a
package choice), ADR-0003 (fail-open-supervised — the new backend MUST preserve it), ADR-0004
(graphics-yield is already vendor-agnostic — nothing to port), ADR-0012 (keyhole honest UNKNOWN —
the AMD attribution gap surfaces as UNKNOWN, never a fabricated zero), ADR-0018 (VRAM coexistence —
the gfx/compute split this touches), ADR-0044 (onboarding — Phase 0 lives in `setup.py`).

## Context

AgentOS is NVIDIA/CUDA-only today. The coupling is narrower than it looks — research 0014 found it
lives in exactly **three** places:

1. **The `agentosd` substrate** drives `nvml-wrapper` directly across six modules, but the *entire*
   surface is **8 operations** (`init`, `device_by_index(0)`, `memory_info`, `utilization_rates`,
   `power_usage`, `temperature`, `running_{compute,graphics}_processes`, `name`), every module
   already opens its own handle with `Nvml::init().ok()`, and the whole thing **fails open to
   UNKNOWN sentinels** when NVML is absent. The load-bearing read — free VRAM → admission
   (`coord::free_mib`) — is available on AMD from plain **sysfs, zero new deps, no root**.
2. **The graphics-yield actuator** (`ollama stop`, nimbus-flux kill, process-group + `cgroup.kill`
   SIGKILL) touches process names and the Ollama API, **not** NVML — already vendor-agnostic.
3. **The installer** assumes NVIDIA in three spots: `setup.py:54` `TORCH_INDEX=…/whl/cu124`,
   `setup.py:206` `detect_hardware()` (probes `nvidia-smi` only), and `models_panel.py:34`.

The runtime stack ports with one honest tax: **Ollama-ROCm** is solid on RDNA3 and `/api/ps
size_vram` reports identically to CUDA (shared Go scheduler) — residency/reclaim need no change.
**ComfyUI video** runs but gets **no fp8 acceleration on RDNA3** and lands ~2.5–3.5× slower than
the 4090 for Wan/Hunyuan. AMD is therefore a *good* local-LLM box and a *usable-but-slower*
dreaming box. (Full evidence + sources: research 0014.)

## Decision

Support AMD as an **additive, fail-open backend** — NVIDIA stays the default and is untouched. The
four decisions research 0014 surfaced are resolved as follows:

### 1. Substrate dependency: sysfs primary + optional `libamdgpu_top`; **no ROCm in the daemon**
The daemon reads AMD VRAM/util/power/temp/name from **sysfs/hwmon** (`mem_info_vram_{total,used}`
with `free = total − used`, `gpu_busy_percent`, hwmon `power1_average`/`temp1_input`,
`product_name`) — no crate, no root, and it keeps reporting when ROCm's own `amdsmi` goes blind on
new silicon (the Strix-Halo lesson). Per-process attribution (the keyhole itemization) uses
`libamdgpu_top` (MIT, libdrm — *not* ROCm) **only if present**; absent → empty list, exactly the
existing missing-NVML posture. We do **not** take an `amdsmi`/ROCm FFI dependency in `agentosd`,
and we do **not** adopt the heavier vendor-neutral `all-smi` crate. The abstraction is a small
`GpuBackend { Nvml | AmdSysfs | None }` enum threaded where `&Nvml` flows today.

> **Phase-3 refinement (as built):** per-process was implemented by parsing `/proc/<pid>/fdinfo`
> DRM-client accounting **directly** — *no* `libamdgpu_top`/libdrm crate at all (it merely wraps the
> same fdinfo), so the daemon's dependency tree stays exactly as the NVIDIA build's. VRAM is summed
> per-PID across unique `drm-client-id`s on the GPU's PCI address; class is always `Unknown` (no AMD
> gfx/compute split); fail-open to `None` when `/proc` or another user's PID is unreadable (the latter
> needs the daemon in `render`/`video` or `CAP_PERFMON`).

### 2. The gfx/compute class split: derive from the `ARBITRATED` allowlist, residual UNKNOWN
AMD has **no** compute-vs-graphics process classification (NVML's two lists have no analogue). We
reconstruct the class from the allowlist `telemetry.rs` already keeps
(`ARBITRATED = [ollama, nimbus, comfy, python]` → compute vs the desktop's gfx); anything
unclassifiable is marked **UNKNOWN**, never a fabricated 0 (ADR-0012). The admission-gating free
number is device-global and never needs per-process data, so the coordinator's safety function is
unaffected by this gap.

### 3. v1 scope: ship **LLM + substrate sensing** first; gate dreaming behind "experimental on AMD"
High-confidence value (an AMD box as a local-LLM machine: Ollama-ROCm + the substrate sensing) ships
first. ComfyUI video is real but slow on AMD, so the dreaming path is gated behind an explicit
**"experimental, slower on AMD (no fp8)"** flag/notice — never presented as on-par with the 4090.

### 4. Target tier: **support RDNA3 (floor), recommend RDNA4**
RDNA3 (RX 7900 XTX = gfx1100) is the proven, supported floor for both Ollama and ComfyUI. RDNA4
(RX 9070/9070 XT) is the *recommended* AMD card (CI-tested tier, only arch with an fp8 future) but
is younger in ROCm — Ollama on it falls back to the Vulkan backend where ROCm is flaky. RDNA2 is
LLM-only (Ollama via `HSA_OVERRIDE_GFX_VERSION=10.3.0`; ComfyUI unsupported). CDNA/Instinct is out
of scope.

### Non-negotiables carried in
- **Fail-open is preserved verbatim** (ADR-0003): a missing/empty AMD backend reports UNKNOWN and
  the daemon keeps running read-only — same contract as missing NVML.
- **Honest UNKNOWN** (ADR-0012): the AMD attribution gap and any unread datum serialize as
  sentinel/`null`, never a synthesized value.
- **The installer never auto-escalates** (ADR-0044 / `components.conf` discipline): ROCm packages +
  the mandatory `usermod -aG render,video` are a `root: sudo` component that **prints** the commands;
  the driver runs nothing privileged.
- **Don't-reinvent** (ADR-0001): ROCm, `ollama-rocm`, and PyTorch-ROCm are reused as primitives; we
  build only the sysfs reader + the install/detect glue.

### Phasing
| Phase | Scope | Risk |
|---|---|---|
| **0 — Detection & honesty** | `detect_hardware()` + `models_panel.py` gain an AMD branch (amd-smi/rocm-smi/sysfs VRAM, same dict shape); README/wizard say "AMD = experimental (RDNA3/4)"; a `root: sudo` `amd-rocm-runtime` component prints the `pacman -S rocm-hip-runtime rocm-opencl-runtime` + `usermod` steps | low |
| **1 — Substrate backend** | `GpuBackend` trait + `AmdSysfs` (VRAM/util/power/temp/name); free-VRAM via sysfs; class via allowlist/UNKNOWN | low (seam exists) |
| **2 — Runtime stack** | vendor-conditional `TORCH_INDEX` (rocm); ComfyUI ROCm launch flags (`--use-pytorch-cross-attention`, `expandable_segments`); GGUF-Q8 (non-K) default + "no-fp8" note in `local-video-gen` | med (perf tax, version pins) |
| **3 — Per-process (optional)** | `/proc` fdinfo backend for keyhole/telemetry attribution | low |

**Build status (2026-06-24):** all four phases are **built + committed** on `feat/amd-support`
(`docs(adr)` → `feat(agentosd)` Phase 1 → `feat(setup)` Phase 0+2 → `feat(agentosd)` Phase 3). The
ComfyUI AMD launch flags (`start-comfyui.sh`) and the `local-video-gen` AMD recipe note live in the
working tree, riding with the in-flight `spikes→apps` migration. **Remaining = hardware validation
on a Radeon** (RDNA3/RDNA4): confirm the sysfs VRAM read, the fdinfo per-process parse against real
`drm-*-vram` keys, the ROCm torch-index version, and the derived-free admission headroom (the
Phase-2 must-do above).

## Non-goals
- **NVIDIA is not changed.** No regression to the default CUDA path; AMD is purely additive.
- **No ROCm/`amdsmi` dependency in `agentosd`.** sysfs is the contract; ROCm is the runtime stack's
  concern, not the daemon's.
- **No promise of 4090-parity dreaming on AMD.** The fp8/perf tax is disclosed, not hidden.
- **No new GPU-vendor abstraction beyond need** — not a generic multi-GPU manager; one enum, two
  real backends.
- **No CDNA/Instinct / multi-GPU scheduling**, no Windows/ZLUDA path.

## Consequences
- A small `gpu` backend module in `agentosd` (sysfs reader, ~no new deps) + the optional
  `libamdgpu_top` feature; the fail-open tests extend to the AMD-absent and AMD-present cases.
- **AMD `free` VRAM is *derived* (`total − used` via sysfs), not driver-reported like NVML's `.free`**
  — it omits reserved/fragmented VRAM, so it tends to **over-report headroom**. Admission gates on
  free and over-admit→OOM is the expensive (asymmetric) direction, so the AMD path needs a
  calibration headroom bump (à la `SAFETY_MIB`) before it drives `coord::admit`. Not exercised until
  AMD hardware runs the coordinator → recorded as a **Phase-2 must-do** (Phase-1 review finding,
  resource-safety + rust-performance, 2026-06-24).
- The keyhole/telemetry **lose the compute/gfx split on AMD** (allowlist-derived or UNKNOWN); the
  admission/reclaim core is unaffected.
- The installer stops silently assuming NVIDIA; AMD users get an honest detect + a printed,
  never-auto-run ROCm setup, and an "experimental dreaming" gate.
- Build sequencing (each phase independently shippable): **0** (this round) → **1** (the headline
  substrate port, small because of the existing seam) → **2** (dreaming on AMD, the perf-tax round)
  → **3** (attribution polish). The LLM+sensing value (0–1) lands before the dreaming tax (2).
</content>

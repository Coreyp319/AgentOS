# Research: AMD GPU support for the installer and packages

Date: 2026-06-24. Would feed a new ADR (next free number: **0048**, not yet written).
Four parallel research lanes (Rust substrate sensing, Ollama/ROCm, ComfyUI video on
ROCm, Arch/CachyOS packaging + vendor detection) + repo coupling recon. Primary sources
cited inline.

## Question
What does it take to make AgentOS — NVIDIA/CUDA-only today — also run on an AMD GPU,
across the three layers the user named ("installer and packages"):
- **A. the `agentosd` Rust substrate** (the cargo *packages*: `nvml-wrapper` and the
  GPU sensing it drives),
- **B. the runtime stack** (Ollama + ComfyUI, which the substrate arbitrates), and
- **C. the installer** (`integrations/_driver.sh` + `components.conf` + the `setup/`
  onboarding wizard, ADR-0044).

## TL;DR verdict
- **The substrate port is small and clean.** The entire NVML surface is **8 operations**,
  each module already opens its own handle with `Nvml::init().ok()` and **fails open to
  UNKNOWN sentinels**. The load-bearing read (free VRAM → admission) is available on AMD
  from **plain sysfs, zero new dependencies, no root**. Effort: ~1 focused change behind a
  backend trait.
- **The graphics-yield actuator is already vendor-agnostic** — `ollama stop`, nimbus-flux
  kill, process-group/`cgroup.kill` SIGKILL touch process names and the Ollama API, *not*
  NVML. Nothing to port (ADR-0003/0004).
- **The runtime stack ports, with one honest tax.** Ollama-ROCm is solid on **RDNA3**
  (RX 7900 XTX = gfx1100) and `/api/ps size_vram` reports **identically** to CUDA (shared
  Go scheduler), so residency tracking survives untouched. ComfyUI video *runs* but **gets
  no fp8 acceleration on RDNA3** and lands **~2.5–3.5× slower than the 4090** for Wan/Hunyuan.
- **The installer needs three edits**, all localized: vendor detection (currently
  `nvidia-smi`-only), a ROCm torch index, and a ROCm package-install path.
- **Honest framing for the user:** an AMD box is a *good* local-LLM machine and a *usable
  but slower* dreaming machine. **RDNA4 (RX 9070/9070 XT)** is the better strategic target
  than RDNA3 (CI-tested tier, only arch with an fp8 future) but is younger in ROCm.

---

## 1. Current NVIDIA-coupling map

| Where | Coupling | Vendor-agnostic? |
|---|---|---|
| `crates/agentosd/src/{telemetry,coord,lease,keyhole,reclaim,main}.rs` | `nvml-wrapper` direct: `Nvml::init`, `device_by_index(0)`, `memory_info`, `utilization_rates`, `power_usage`, `temperature`, `running_{graphics,compute}_processes`, `name` | **No** — port target (Layer A) |
| `crates/agentosd/src/reclaim.rs`, `lease.rs` | graphics-yield: `ollama stop`, nimbus-flux kill, process-group + `cgroup.kill` SIGKILL | **Yes** — process/API based, nothing to do |
| `integrations/setup/setup.py:54` | `TORCH_INDEX = ".../whl/cu124"` (hardcoded CUDA wheel) | **No** — port target (Layer C) |
| `integrations/setup/setup.py:206` `detect_hardware()` | VRAM read via `nvidia-smi --query-gpu` **only** | **No** — port target (Layer C) |
| `integrations/models/models_panel.py:34` | VRAM read via `nvidia-smi --query-gpu` | **No** — port target (Layer C) |
| `apps/dreaming/start-comfyui.sh`, `.claude/skills/local-video-gen` | ComfyUI venv pins `torch==2.6.0+cu124` | **No** — runtime-stack (Layer B) |
| `integrations/comfyui/{README,comfyui.service}` | prose: "idle server holds the CUDA context" | Cosmetic only |
| `README.md:26` | "a Linux box with a recent **NVIDIA** GPU" | Docs |

Everything else (Hermes, the lease/queue arbiter logic, the web apps, the desktop
integrations) is GPU-vendor-blind.

---

## 2. Layer A — the `agentosd` substrate (the cargo packages)

### The complete NVML surface to replace
Eight operations, that's the whole thing. `coord::free_mib(&Nvml)` is the single
load-bearing read (it gates VRAM admission and drives reclaim); the per-process split
feeds telemetry + the keyhole + the graphics-baseline estimate.

| # | NVML op | AMD source (recommended) | Root/group? | Maturity |
|---|---|---|---|---|
| 1 | `Nvml::init()` | open sysfs / probe backend | No | — |
| 2 | `device_by_index(0)` | `/sys/class/drm/card*/device` (single GPU) | No | Stable ABI |
| 3 | `memory_info()` total/used/**free** | `mem_info_vram_total` − `mem_info_vram_used` (**free = T−U**, AMD has no direct free field) | **No** | Stable kernel ABI |
| 4 | `utilization_rates()` | sysfs `gpu_busy_percent` | No | Stable |
| 5 | `power_usage()` | hwmon `power1_average` (µW) | No | Stable |
| 6 | `temperature(GPU)` | hwmon `temp1_input` (m°C) | No | Stable |
| 7 | `running_{compute,graphics}_processes()` | `libamdgpu_top`/DRM fdinfo (or amdsmi) | **render/video grp or CAP_PERFMON** for *other* users' PIDs | MIT lib, fdinfo stable |
| 7b | the compute-vs-graphics **class split** | **no AMD equivalent** — derive by name allowlist or mark UNKNOWN | — | N/A |
| 8 | `name()` | sysfs `product_name` / PCI-ID lookup | No | Stable |

Sources: [kernel amdgpu thermal/sysfs](https://docs.kernel.org/gpu/amdgpu/thermal.html),
[AMD SMI C/Py API](https://rocm.docs.amd.com/projects/amdsmi/en/latest/reference/amdsmi-py-api.html),
[libamdgpu_top (lib.rs)](https://lib.rs/crates/libamdgpu_top),
[ROCm #6035 — amdsmi all-N/A when lib lags silicon](https://github.com/ROCm/ROCm/issues/6035).

### The one real regression vs NVML
1. **No compute-vs-graphics class on AMD.** NVML's two process lists ("ollama/comfyui =
   compute" vs "kwin/wallpaper = graphics") have **no AMD analogue**. On AMD we either
   collapse `gfx_mib`/`compute_mib` into one figure, re-derive the class from a
   process-name allowlist (we already keep `ARBITRATED = [ollama, nimbus, comfy, python]`
   in `telemetry.rs:81`), or mark it `UNKNOWN` — consistent with the existing "null ==
   UNKNOWN, never fake a zero" contract.
2. **Per-process needs group membership.** NVML reads per-PID VRAM with no privileges;
   AMD fdinfo/amdsmi needs the daemon's user in `render`/`video` (or `CAP_PERFMON`) to see
   *other users'* PIDs — own PIDs are free. Degrade, don't wedge.

**What is NOT lost:** the admission-gating **free-VRAM number comes from device-global
sysfs**, needs no per-process data and no root — so the coordinator's core safety function
ports perfectly. The only thing that degrades without per-process is *attribution* (the
keyhole "who holds VRAM" itemization), which already has an UNKNOWN path.

### Crate / dependency options

| Option | Covers | License | Verdict |
|---|---|---|---|
| **sysfs/hwmon hand-rolled** (no crate) | items 2–6, 8 | — | **Primary.** Zero deps, root-free, keeps working when amdsmi goes blind. Compute `free = total − used` ourselves. |
| **`libamdgpu_top`** (libdrm, not ROCm) | adds item 7 (per-PID via fdinfo) | MIT, v0.11.5 (5/2026) | **Optional add-on** for per-process. No ROCm install needed. Thin docs. |
| **`all-smi`** | both NVIDIA+AMD, per-process | Apache-2.0, v0.22 (5/2026) | Single-crate shortcut for *both* vendors ("nvtop in a crate") but heavy, and abstracts away the free/class nuance we care about. |
| **`amdsmi` FFI** (`libamd_smi.so`) | everything incl. per-PID | MIT C lib, **no Rust bindings on crates.io** | **Avoid** unless we need Instinct/datacenter features — pulls in ROCm, hand-rolled bindgen, and the "all-N/A when lib lags silicon" risk. |
| `gfxinfo` | vendor/model/VRAM/load/temp | Apache-2.0 | Too thin (no power, no per-process). |

Sources: [all-smi](https://github.com/lablup/all-smi),
[gfxinfo](https://docs.rs/gfxinfo), [amdgpu_top repo](https://github.com/Umio-Yasuno/amdgpu_top).

### Recommended substrate design
Introduce a tiny `gpu` backend abstraction (the codebase already passes `Option<&Nvml>`
around and degrades on `None`, so the seam exists):

```
enum GpuBackend { Nvml(Nvml), AmdSysfs(AmdSysfs), None }
impl GpuBackend {
    fn mem(&self) -> MemInfo;            // {total,used,free}; UNK on None
    fn meta(&self) -> GpuMeta;           // util/power/temp/name; None-fields = unknown
    fn processes(&self) -> Vec<ProcVram> // empty if unavailable; class may be Unknown
}
```
- **Detect once** (PCI vendor id), construct the backend, thread it where `&Nvml` goes
  today (`coord::free_mib`, `telemetry::read_gpu/read_procs`, keyhole, reclaim).
- **sysfs is primary for AMD** items 2–6/8 (the Strix-Halo lesson: sysfs keeps reporting
  when amdsmi returns N/A). `libamdgpu_top` only-if-present for per-process.
- This is **additive** and preserves the fail-open posture verbatim.

---

## 3. Layer B — the runtime stack

### Ollama (ROCm) — ports cleanly
- **Packaging:** Arch ships split backends in `extra`; **CachyOS ships `ollama-rocm`
  directly** ([CachyOS pkg](https://packages.cachyos.org/package/extra/x86_64/ollama-rocm)).
  Prefer it over the upstream vendored 2 GB `…-rocm.tar.zst` tarball — leaner, tracks
  system ROCm. Install exactly **one** backend package.
- **`/api/ps size_vram` is backend-agnostic** — layer placement + VRAM estimation live in
  Ollama's Go scheduler *above* the CUDA/ROCm allocators, so residency + CPU-offload report
  identically on AMD ([issue #14351](https://github.com/ollama/ollama/issues/14351)). **Our
  `telemetry::read_residency` and reclaim need no changes.**
- **Supported tier:** RDNA3 (gfx1100/1101/1102 — RX 7900 XTX/XT/W7900) is the safe target
  ([docs.ollama.com/gpu](https://docs.ollama.com/gpu)). RDNA2 desktop (RX 6800/6900) works
  via `HSA_OVERRIDE_GFX_VERSION=10.3.0`. **RDNA4 listed but flaky** in early-2026 builds →
  Vulkan backend (`OLLAMA_VULKAN=1`, exists since v0.12.6) is the escape hatch.
- **Gotchas:** APUs/iGPUs misreport GTT as VRAM (don't trust `size_vram` there without an
  `amdgpu_top` cross-check — [#13677](https://github.com/ollama/ollama/issues/13677)); pin
  ROCm/Ollama on CachyOS (updates have broken offload); scope the discrete card with
  `HIP_VISIBLE_DEVICES`.

### ComfyUI video (the dreaming stack) — runs, with an fp8/perf tax
- **Baseline:** experimental-but-official. ComfyUI supports **RDNA3/3.5/4** (not RDNA1/2);
  **Linux needs ROCm 7.2** + `pip install torch --index-url …/whl/rocm7.x`. Key flag
  **`--use-pytorch-cross-attention`** + `PYTORCH_HIP_ALLOC_CONF=expandable_segments:True`
  ([ComfyUI sys-reqs](https://docs.comfy.org/installation/system_requirements),
  [AMD RX 9000 ComfyUI blog](https://rocm.blogs.amd.com/artificial-intelligence/comfyui-radeon-9000/README.html)).
- **fp8 (the real catch):** **no fp8 compute on RDNA3**; fp8 is even *broken* (not just
  slow) on RDNA4 today ([ROCm #6019](https://github.com/ROCm/ROCm/issues/6019)). ComfyUI on
  AMD **stores fp8 weights but forces FP16/BF16 math** — so fp8 checkpoints **still fit
  24 GB** but get **zero speedup** ([ComfyUI #11519](https://github.com/Comfy-Org/ComfyUI/issues/11519)).
  Full BF16 of the big video models does *not* fit 24 GB → we must stay on fp8/GGUF for
  capacity, just without the compute win.
- **GGUF = the best AMD path:** `city96/ComfyUI-GGUF` is **pure-PyTorch dequant, no CUDA
  kernels** → ROCm-safe. Prefer **non-K quants (Q4_0/Q5_0/Q8_0)**; K-quants are slow on
  this path.
- **Attention:** PyTorch SDPA → AOTriton is the AMD workhorse; xformers effectively
  unavailable, Flash-Attention fragile on RDNA3 (no backward). ComfyUI **falls back
  cleanly**, so nothing breaks — we just lose the CUDA speed edge.
- **Real datapoints:** AMD demos **Wan 2.2 5B on RX 9070 ≈ 25 s/frame**; LTX-2B on a
  7900 XTX ≈ 66 s total. Image work ~2× slower than the 4090; **Wan/Hunyuan video
  ~2.5–3.5× slower** (the fp8 disadvantage compounds). Hunyuan *Video* on consumer AMD is
  plausible-but-under-reported. RealESRGAN upscale is fine.

### Per-architecture support summary

| Arch | Examples | Ollama | ComfyUI video | Notes |
|---|---|---|---|---|
| RDNA2 (gfx103x) | RX 6800/6900 | ✅ via `HSA_OVERRIDE=10.3.0` | ❌ ComfyUI unsupported | LLM-only tier |
| **RDNA3 (gfx110x)** | **RX 7900 XTX** | ✅ native | ⚠️ runs, no fp8, ~2.5–3.5× slower | safe 24 GB fallback |
| **RDNA4 (gfx120x)** | **RX 9070/9070 XT** | ⚠️ flaky → Vulkan | ⚠️ CI-tested tier, fp8 future (broken now) | **strategic target** |
| CDNA (MI-series) | MI210/300 | ✅ | n/a | datacenter, out of scope |

---

## 4. Layer C — the installer

### The three edits in `setup/`
1. **`setup.py:54` `TORCH_INDEX`** — make it vendor-conditional: `…/whl/cu124` (NVIDIA) vs
   `…/whl/rocm7.x` (AMD). The pip step at `setup.py:153` already consumes the constant.
2. **`setup.py:206` `detect_hardware()`** — add an AMD branch. It currently only runs
   `nvidia-smi`; add `amd-smi`/`rocm-smi`/sysfs VRAM read and return the same dict shape so
   the fit-verdict math (`fits/tight/too-big`) is unchanged.
3. **`integrations/models/models_panel.py:34`** — same `nvidia-smi`→AMD fallback for the
   read-only VRAM panel.

### Vendor detection (recommended, dependency-light)
PCI vendor id from sysfs (always present) to *enumerate*, runtime probe to *pick the
compute backend* (handles the AMD-iGPU + NVIDIA-dGPU case):

```sh
# enumerate GPUs (0x10de NVIDIA, 0x1002 AMD, 0x8086 Intel)
for d in /sys/class/drm/card[0-9]*/device/vendor; do
  case "$(cat "$d")" in 0x10de) echo nvidia;; 0x1002) echo amd;; 0x8086) echo intel;; esac
done | sort -u
# pick compute backend by presence-probing a *working* runtime
if   nvidia-smi -L      >/dev/null 2>&1; then echo compute=cuda
elif rocminfo 2>/dev/null | grep -q 'Device Type:.*GPU'; then echo compute=rocm
else echo compute=cpu; fi
```

### ROCm packages (Arch / CachyOS `extra` — no AUR for the core)
- **Compute (PyTorch/Ollama):** `rocm-hip-runtime rocm-opencl-runtime amdsmi`
  (+ `python-pytorch-rocm` or CachyOS `ollama-rocm`). **Skip `rocm-hip-sdk`** (the dev
  compiler) unless building HIP — it's most of the 16–30 GB SDK footprint.
- **Monitoring lib for `agentosd`:** **`amdsmi`** (9.6 MB, ships `/opt/rocm/lib/libamd_smi.so`
  + headers) — the AMD analogue to NVML. (Only needed if we choose the amdsmi backend over
  sysfs+libamdgpu_top; sysfs needs no package at all.)
- **Prereqs (root, must-do):** in-kernel `amdgpu` is enough on Arch (no DKMS);
  **`sudo usermod -aG render,video $USER`** + relogin (the #1 cause of "ROCk module NOT
  loaded"). Devices: `/dev/kfd` + `/dev/dri/renderD*`.
- **RDNA4** supported since **ROCm 6.4.1** (May 2025); Arch `extra` is at 7.2.x, so RX 9000
  is covered by stock packages.

Sources: [Arch rocm-hip-runtime](https://archlinux.org/packages/extra/any/rocm-hip-runtime/),
[Arch amdsmi](https://archlinux.org/packages/extra/x86_64/amdsmi/),
[ROCm prerequisites](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/prerequisites.html),
[Phoronix — ROCm 6.4.1 RDNA4](https://www.phoronix.com/news/AMD-ROCm-6.4.1-Released).

### Driver/package install in the `components.conf` model
ROCm install is a **`root: sudo`** action (the driver prints `sudo pacman -S …` and the
`usermod`, never auto-escalates — matches the existing `firefox-pin`/`tailscale-remote`
discipline). A new optional component (e.g. `amd-rocm-runtime`, default `off`, tier
`privileged`) fits the registry without driver changes.

---

## 5. Recommended approach + phasing

| Phase | Scope | Effort | Risk | Unblocks |
|---|---|---|---|---|
| **0 — Detection & honesty** | vendor detect in `setup.py`/`models_panel.py`; README + wizard say "AMD = experimental, RDNA3/4"; ROCm-package print path in a `sudo` component | S | low | the installer stops assuming NVIDIA; AMD users aren't silently mis-served |
| **1 — Substrate backend** | `GpuBackend` trait + `AmdSysfs` (items 2–6, 8); free-VRAM via sysfs; collapse/UNKNOWN the gfx/compute class | M | low (fail-open seam exists) | the coordinator senses AMD VRAM → admission/reclaim work |
| **2 — Runtime stack** | conditional `TORCH_INDEX=rocm`; ComfyUI ROCm launch flags; GGUF-Q8 default + "no-fp8" note in `local-video-gen` | M | med (perf tax, version pins) | dreaming runs on AMD |
| **3 — Per-process (optional)** | `libamdgpu_top`/fdinfo backend for the keyhole itemization | S–M | low | restores "who holds VRAM" attribution |

**Sequencing rationale:** Phase 0 is cheap and removes the silent-NVIDIA-assumption today.
Phase 1 is the headline substrate work and is genuinely small because the fail-open seam
already exists. Phase 2 carries the real user-visible tax (perf). Phase 3 is polish.

---

## 6. Open decisions for the human (ADR-0048 fodder)
1. **Target tier:** RDNA3 (proven, no-fp8) as the supported floor, or hold for RDNA4
   (CI-tested, fp8 future) as the recommended AMD card? Recommendation: **support RDNA3,
   recommend RDNA4.**
2. **Substrate dependency:** sysfs+`libamdgpu_top` (low-dep, MIT) vs `all-smi` (one crate,
   both vendors, heavier) vs amdsmi-FFI (ROCm-coupled). Recommendation: **sysfs primary +
   optional `libamdgpu_top`**, no ROCm dependency in the daemon.
3. **gfx/compute split on AMD:** collapse vs name-allowlist-derive vs UNKNOWN.
   Recommendation: **derive from the existing `ARBITRATED` allowlist, mark residual
   UNKNOWN.**
4. **Scope of v1:** LLM-only AMD support (Ollama, substrate sensing — *high* confidence) vs
   full dreaming (ComfyUI video — *usable but slow*). Recommendation: **ship LLM+substrate
   first; gate dreaming behind an "experimental, slower on AMD" flag.**

---

## Sources (primary, dated 2025–2026)
**Substrate/sensing:** [libamdgpu_top](https://lib.rs/crates/libamdgpu_top) ·
[amdgpu_top](https://github.com/Umio-Yasuno/amdgpu_top) ·
[all-smi](https://github.com/lablup/all-smi) ·
[AMD SMI C/Py API](https://rocm.docs.amd.com/projects/amdsmi/en/latest/reference/amdsmi-py-api.html) ·
[ROCm #6035 (amdsmi N/A)](https://github.com/ROCm/ROCm/issues/6035) ·
[kernel amdgpu sysfs/thermal](https://docs.kernel.org/gpu/amdgpu/thermal.html)
**Ollama:** [docs.ollama.com/gpu](https://docs.ollama.com/gpu) ·
[issue #14351 (size_vram in Go scheduler)](https://github.com/ollama/ollama/issues/14351) ·
[CachyOS ollama-rocm](https://packages.cachyos.org/package/extra/x86_64/ollama-rocm) ·
[Phoronix — Ollama Vulkan](https://www.phoronix.com/news/ollama-Experimental-Vulkan)
**ComfyUI:** [ComfyUI sys-reqs](https://docs.comfy.org/installation/system_requirements) ·
[AMD RX 9000 ComfyUI blog (Wan 2.2)](https://rocm.blogs.amd.com/artificial-intelligence/comfyui-radeon-9000/README.html) ·
[ROCm #6019 (fp8 NotImplemented)](https://github.com/ROCm/ROCm/issues/6019) ·
[ComfyUI #11519 (fp8→FP16 forced)](https://github.com/Comfy-Org/ComfyUI/issues/11519) ·
[city96/ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)
**Packaging/detection:** [Arch rocm-hip-runtime](https://archlinux.org/packages/extra/any/rocm-hip-runtime/) ·
[Arch amdsmi](https://archlinux.org/packages/extra/x86_64/amdsmi/) ·
[ROCm prerequisites](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/prerequisites.html) ·
[Phoronix — ROCm 6.4.1 RDNA4](https://www.phoronix.com/news/AMD-ROCm-6.4.1-Released)
</content>
</invoke>

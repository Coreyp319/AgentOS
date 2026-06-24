//! `gpu` — the vendor-neutral GPU sensing backend (ADR-0048).
//!
//! Every mode that reads the GPU (`monitor`, `telemetry`, `keyhole`, `coord`, `lease`, `reclaim`)
//! goes through ONE [`GpuBackend`] instead of touching `nvml-wrapper` directly. Three variants:
//!   * [`GpuBackend::Nvml`] — NVIDIA via NVML (the original path, byte-for-byte unchanged).
//!   * [`GpuBackend::Amd`]  — AMD via **sysfs** (`/sys/class/drm/cardN/device/…`) — NO ROCm/amd-smi
//!     dependency, no root. sysfs keeps reporting even when ROCm's amd-smi lags new silicon.
//!   * [`GpuBackend::None`] — neither present. Every read returns unknown; the daemon fails open
//!     (ADR-0003) exactly as it did when `Nvml::init()` failed.
//!
//! The contract every caller relies on: a read that can't be taken returns `None`/unknown — NEVER a
//! fabricated `0` (ADR-0012). Per-process attribution is `Option<Vec<…>>`: `None` means "couldn't
//! attribute" (→ the telemetry/keyhole `-1` UNK sentinel), `Some` means attributed (possibly empty).
//!
//! AMD has **no graphics-vs-compute process split** (NVML's two lists have no analogue) and sysfs
//! can't attribute per-PID at all, so `Amd::processes()` returns `None` in this phase — per-process
//! on AMD is ADR-0048 Phase 3 (`libamdgpu_top`/DRM fdinfo). The admission-gating *free VRAM* number
//! comes from device-global sysfs and needs neither per-process data nor root, so the coordinator's
//! core safety function ports fully.

use nvml_wrapper::enum_wrappers::device::TemperatureSensor;
use nvml_wrapper::enums::device::UsedGpuMemory;
use nvml_wrapper::Nvml;

mod amd;

use crate::mib;

/// Total / used / free VRAM, in MiB.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MemInfo {
    pub total: u64,
    pub used: u64,
    pub free: u64,
}

/// GPU scalar sensors. Each `None` == unreadable/unknown (never synthesized).
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct GpuMeta {
    pub util_pct: Option<u32>,
    pub power_w: Option<f64>,
    pub temp_c: Option<u32>,
}

/// NVML per-process attribution class. AMD has no such split → [`ProcClass::Unknown`] (ADR-0048).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProcClass {
    Graphics,
    Compute,
    /// AMD has no graphics/compute split; its per-process backend (ADR-0048 Phase 3, via
    /// `libamdgpu_top`/fdinfo) tags holders it can't classify by name as `Unknown`. The consumers
    /// already route `Unknown` onto the compute side; the variant isn't constructed until Phase 3.
    #[allow(dead_code)]
    Unknown,
}

/// A process holding VRAM: pid, MiB, and (NVML) whether it's a graphics or compute context.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProcVram {
    pub pid: u32,
    pub mib: u64,
    pub class: ProcClass,
}

/// The detected GPU sensing backend. `Send + Sync + 'static` (so it lives in an `Arc` and crosses
/// `spawn_blocking` in the async coordinator, exactly as the bare `Nvml` handle did before).
pub enum GpuBackend {
    // Boxed: the NVML handle is ~9.7 KB, which would bloat every `GpuBackend` (and the `Arc` it
    // lives in) — box it so the enum stays small (clippy::large_enum_variant). Deref makes the
    // call-sites unchanged.
    Nvml(Box<Nvml>),
    Amd(amd::AmdSysfs),
    None,
}

// `GpuBackend` MUST stay `Send + Sync + 'static`: it lives in an `Arc` shared with the async
// coordinator and is cloned into `tokio::task::spawn_blocking` (`coord::free_mib`). This compile-time
// assertion fails loudly if a future variant (e.g. a Phase-3 AMD handle from `libamdgpu_top`) ever
// breaks the invariant — rather than surfacing as an opaque `spawn_blocking` bound error elsewhere.
const _: fn() = || {
    fn assert_send_sync_static<T: Send + Sync + 'static>() {}
    assert_send_sync_static::<GpuBackend>();
};

impl GpuBackend {
    /// Detect once, in priority order: **NVML (NVIDIA) → AMD sysfs → None**. On a hybrid box
    /// (AMD iGPU + NVIDIA dGPU) NVML wins, which is correct — the discrete card is the compute
    /// target (ADR-0048; matches the runtime-probe-picks-the-working-backend rule).
    pub fn detect() -> GpuBackend {
        match Nvml::init() {
            Ok(n) => GpuBackend::Nvml(Box::new(n)),
            Err(_) => match amd::AmdSysfs::detect() {
                Some(a) => GpuBackend::Amd(a),
                None => GpuBackend::None,
            },
        }
    }

    /// `"nvidia" | "amd" | "none"` — for honest logging / the keyhole vendor note.
    pub fn vendor(&self) -> &'static str {
        match self {
            GpuBackend::Nvml(_) => "nvidia",
            GpuBackend::Amd(_) => "amd",
            GpuBackend::None => "none",
        }
    }

    /// `true` when no GPU was detected — callers that require sensing (the lease/coord daemons)
    /// refuse to start, matching the old "NVML init failed → exit" behavior.
    pub fn is_absent(&self) -> bool {
        matches!(self, GpuBackend::None)
    }

    /// Device name (e.g. "NVIDIA GeForce RTX 4090"). `None` if unreadable.
    pub fn name(&self) -> Option<String> {
        match self {
            GpuBackend::Nvml(n) => n.device_by_index(0).ok()?.name().ok(),
            GpuBackend::Amd(a) => a.name(),
            GpuBackend::None => None,
        }
    }

    /// Total/used/free VRAM (MiB), or `None` if unreadable. Blocking (NVML FFI / sysfs read) — async
    /// callers run it under `spawn_blocking` (see [`crate::coord::free_mib`]).
    pub fn mem(&self) -> Option<MemInfo> {
        match self {
            GpuBackend::Nvml(n) => {
                let m = n.device_by_index(0).ok()?.memory_info().ok()?;
                Some(MemInfo { total: mib(m.total), used: mib(m.used), free: mib(m.free) })
            }
            GpuBackend::Amd(a) => a.mem(),
            GpuBackend::None => None,
        }
    }

    /// Util % / power (W) / temp (°C). Missing fields stay `None` (NVML reports power in mW;
    /// AMD hwmon in µW/m°C — both normalized here).
    pub fn meta(&self) -> GpuMeta {
        match self {
            GpuBackend::Nvml(n) => {
                let dev = n.device_by_index(0).ok();
                GpuMeta {
                    util_pct: dev.as_ref().and_then(|d| d.utilization_rates().ok()).map(|u| u.gpu),
                    power_w: dev.as_ref().and_then(|d| d.power_usage().ok()).map(|mw| (mw as f64) / 1000.0),
                    temp_c: dev.as_ref().and_then(|d| d.temperature(TemperatureSensor::Gpu).ok()),
                }
            }
            GpuBackend::Amd(a) => a.meta(),
            GpuBackend::None => GpuMeta::default(),
        }
    }

    /// Per-process VRAM holders. `None` == attribution unavailable (NVML present but no per-proc
    /// data; a None backend; or an AMD GPU whose PCI address / `/proc` we can't read) → the caller
    /// records the `-1` UNK sentinel. `Some(vec)` == attributed (possibly empty). NVIDIA: graphics
    /// then compute processes, each with its class. AMD: `/proc/<pid>/fdinfo` DRM clients, all
    /// `ProcClass::Unknown` (no gfx/compute split). Blocking — async callers must `spawn_blocking`.
    pub fn processes(&self) -> Option<Vec<ProcVram>> {
        match self {
            GpuBackend::Nvml(n) => {
                let dev = n.device_by_index(0).ok()?;
                let mut out = Vec::new();
                let mut attributed = false;
                for p in dev.running_graphics_processes().unwrap_or_default() {
                    if let Some(m) = used_mib(&p.used_gpu_memory) {
                        attributed = true;
                        out.push(ProcVram { pid: p.pid, mib: m, class: ProcClass::Graphics });
                    }
                }
                for p in dev.running_compute_processes().unwrap_or_default() {
                    if let Some(m) = used_mib(&p.used_gpu_memory) {
                        attributed = true;
                        out.push(ProcVram { pid: p.pid, mib: m, class: ProcClass::Compute });
                    }
                }
                // NVML present but nothing attributed → honest UNK (matches the pre-refactor
                // `attributed` flag), never a fabricated empty-attributed result.
                attributed.then_some(out)
            }
            // AMD per-process via /proc/<pid>/fdinfo DRM accounting (ADR-0048 Phase 3); class is
            // always Unknown (AMD has no gfx/compute split). `None` if it can't attribute at all.
            GpuBackend::Amd(a) => a.processes(),
            GpuBackend::None => None,
        }
    }
}

/// NVML's per-process memory wrapper → `Some(MiB)` or `None` (unavailable). Internal to the NVML
/// backend now (was a shared helper in `main.rs`).
fn used_mib(u: &UsedGpuMemory) -> Option<u64> {
    match u {
        UsedGpuMemory::Used(b) => Some(mib(*b)),
        UsedGpuMemory::Unavailable => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn none_backend_is_all_unknown() {
        let g = GpuBackend::None;
        assert!(g.is_absent());
        assert_eq!(g.vendor(), "none");
        assert!(g.mem().is_none());
        assert!(g.name().is_none());
        assert!(g.processes().is_none());
        assert_eq!(g.meta(), GpuMeta::default());
    }

    #[test]
    fn used_mib_maps_used_and_unavailable() {
        assert_eq!(used_mib(&UsedGpuMemory::Used(1024 * 1024 * 1500)), Some(1500));
        assert_eq!(used_mib(&UsedGpuMemory::Unavailable), None);
    }
}

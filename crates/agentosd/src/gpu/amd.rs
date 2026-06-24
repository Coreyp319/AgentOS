//! AMD GPU sensing via **sysfs** (ADR-0048) — no ROCm, no amd-smi, no root.
//!
//! Reads the kernel `amdgpu` driver's stable sysfs/hwmon attributes for the first AMD GPU
//! (PCI vendor `0x1002`) under `/sys/class/drm`:
//!   * VRAM   — `device/mem_info_vram_total` & `…_used` (bytes; **free = total − used**)
//!   * util   — `device/gpu_busy_percent`
//!   * power  — `device/hwmon/hwmon*/power1_average` (µW)
//!   * temp   — `device/hwmon/hwmon*/temp1_input` (m°C)
//!   * name   — `device/product_name` (absent on some kernels → `None`)
//!
//! Per-process attribution is NOT here — sysfs is device-global. That's ADR-0048 Phase 3
//! (`libamdgpu_top`/fdinfo). Every read fails soft to `None` (fail-open, ADR-0003).

use std::path::{Path, PathBuf};

use super::{GpuMeta, MemInfo};

const AMD_VENDOR: &str = "0x1002";
const DRM_ROOT: &str = "/sys/class/drm";

/// A discovered AMD render device: its `device` sysfs dir and the hwmon dir for power/temp.
pub struct AmdSysfs {
    device: PathBuf,
    hwmon: Option<PathBuf>,
}

impl AmdSysfs {
    /// Discover the first AMD GPU under `/sys/class/drm`, or `None` if there isn't one.
    pub fn detect() -> Option<AmdSysfs> {
        Self::detect_in(Path::new(DRM_ROOT))
    }

    /// Testable core of [`detect`](Self::detect): scan `drm_root` for a `cardN/device` whose PCI
    /// `vendor` is AMD. Cards are sorted so `card0` wins over `card1` deterministically.
    fn detect_in(drm_root: &Path) -> Option<AmdSysfs> {
        let mut cards: Vec<PathBuf> = std::fs::read_dir(drm_root)
            .ok()?
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.file_name().and_then(|n| n.to_str()).is_some_and(is_card_name))
            .collect();
        cards.sort();
        for card in cards {
            let device = card.join("device");
            if read_trim(&device.join("vendor")).as_deref() == Some(AMD_VENDOR) {
                let hwmon = find_hwmon(&device);
                return Some(AmdSysfs { device, hwmon });
            }
        }
        None
    }

    /// Total/used/free VRAM (MiB). `free = total − used` (saturating). `None` if `…_total` is
    /// unreadable; a missing `…_used` is treated as 0 used.
    ///
    /// CAVEAT (ADR-0048): unlike NVML's driver-reported `.free`, this *derived* free omits VRAM the
    /// driver has reserved/fragmented, so it tends to **over-report headroom**. Admission gates on
    /// free and over-admit→OOM is the expensive direction, so the AMD path needs a calibration
    /// headroom bump (à la `SAFETY_MIB`) before it drives `coord::admit` — deferred to when AMD
    /// hardware actually runs the coordinator (Phase 2).
    pub fn mem(&self) -> Option<MemInfo> {
        let total = read_u64(&self.device.join("mem_info_vram_total"))?;
        let used = read_u64(&self.device.join("mem_info_vram_used")).unwrap_or(0);
        let to_mib = |b: u64| b / (1024 * 1024);
        Some(MemInfo {
            total: to_mib(total),
            used: to_mib(used),
            free: to_mib(total.saturating_sub(used)),
        })
    }

    /// Util % (`gpu_busy_percent`, clamped 0–100), power W (hwmon µW → W), temp °C (hwmon m°C → °C).
    /// Each missing attribute stays `None`.
    pub fn meta(&self) -> GpuMeta {
        GpuMeta {
            util_pct: read_u64(&self.device.join("gpu_busy_percent")).map(|v| v.min(100) as u32),
            power_w: self
                .hwmon
                .as_ref()
                .and_then(|h| read_u64(&h.join("power1_average")))
                .map(|uw| (uw as f64) / 1_000_000.0),
            temp_c: self
                .hwmon
                .as_ref()
                .and_then(|h| read_u64(&h.join("temp1_input")))
                .map(|mc| (mc / 1000) as u32),
        }
    }

    /// Marketing/product name if the kernel exposes `device/product_name`; else `None`.
    pub fn name(&self) -> Option<String> {
        read_trim(&self.device.join("product_name")).filter(|s| !s.is_empty())
    }
}

/// `card0`, `card12` — a render card dir, NOT a connector like `card0-DP-1` (which has no GPU
/// `device/vendor` we'd want anyway, but we filter early to keep discovery clean).
fn is_card_name(n: &str) -> bool {
    n.strip_prefix("card").is_some_and(|r| !r.is_empty() && r.bytes().all(|b| b.is_ascii_digit()))
}

/// First `hwmon*` subdir under `device/hwmon` (where power/temp live), if any.
fn find_hwmon(device: &Path) -> Option<PathBuf> {
    std::fs::read_dir(device.join("hwmon"))
        .ok()?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .find(|p| p.file_name().and_then(|n| n.to_str()).is_some_and(|n| n.starts_with("hwmon")))
}

fn read_trim(p: &Path) -> Option<String> {
    std::fs::read_to_string(p).ok().map(|s| s.trim().to_string())
}

fn read_u64(p: &Path) -> Option<u64> {
    read_trim(p)?.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};

    /// A throwaway sysfs fixture dir (no tempfile dep). Cleaned on drop.
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Fixture {
            static N: AtomicU32 = AtomicU32::new(0);
            let dir = std::env::temp_dir().join(format!(
                "agentosd-amd-test-{}-{}",
                std::process::id(),
                N.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir_all(&dir).unwrap();
            Fixture(dir)
        }
        /// Write `name` (a relative path) under the fixture with `body`, creating parents.
        fn put(&self, name: &str, body: &str) -> &Self {
            let p = self.0.join(name);
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(p, body).unwrap();
            self
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn gib(n: u64) -> u64 {
        n * 1024 * 1024 * 1024
    }

    #[test]
    fn is_card_name_accepts_cards_rejects_connectors() {
        assert!(is_card_name("card0"));
        assert!(is_card_name("card12"));
        assert!(!is_card_name("card0-DP-1"));
        assert!(!is_card_name("card"));
        assert!(!is_card_name("renderD128"));
        assert!(!is_card_name("version"));
    }

    #[test]
    fn detects_amd_card_and_reads_vram_free_is_total_minus_used() {
        let fx = Fixture::new();
        // An NVIDIA card at card0 (must be skipped) and an AMD card at card1.
        fx.put("card0/device/vendor", "0x10de\n");
        fx.put("card1/device/vendor", "0x1002\n")
            .put("card1/device/mem_info_vram_total", &format!("{}\n", gib(24)))
            .put("card1/device/mem_info_vram_used", &format!("{}\n", gib(9)))
            .put("card1/device/gpu_busy_percent", "37\n")
            .put("card1/device/product_name", "Radeon RX 7900 XTX\n")
            .put("card1/device/hwmon/hwmon3/power1_average", "61000000\n") // 61 W in µW
            .put("card1/device/hwmon/hwmon3/temp1_input", "44000\n"); // 44 °C in m°C

        let amd = AmdSysfs::detect_in(&fx.0).expect("should find the AMD card");
        let mem = amd.mem().expect("vram readable");
        assert_eq!(mem.total, 24576); // 24 GiB in MiB
        assert_eq!(mem.used, 9216);
        assert_eq!(mem.free, 24576 - 9216); // free = total − used

        let meta = amd.meta();
        assert_eq!(meta.util_pct, Some(37));
        assert_eq!(meta.power_w, Some(61.0));
        assert_eq!(meta.temp_c, Some(44));
        assert_eq!(amd.name().as_deref(), Some("Radeon RX 7900 XTX"));
    }

    #[test]
    fn no_amd_card_returns_none() {
        let fx = Fixture::new();
        fx.put("card0/device/vendor", "0x10de\n"); // NVIDIA only
        assert!(AmdSysfs::detect_in(&fx.0).is_none());
    }

    #[test]
    fn missing_sensors_degrade_to_none_not_zero() {
        let fx = Fixture::new();
        // VRAM present, but no hwmon, no util, no name → those read unknown, VRAM still works.
        fx.put("card0/device/vendor", "0x1002\n")
            .put("card0/device/mem_info_vram_total", &format!("{}\n", gib(16)))
            .put("card0/device/mem_info_vram_used", &format!("{}\n", gib(2)));
        let amd = AmdSysfs::detect_in(&fx.0).expect("amd card");
        let mem = amd.mem().unwrap();
        assert_eq!(mem.total, 16384);
        assert_eq!(mem.free, 16384 - 2048);
        let meta = amd.meta();
        assert_eq!(meta.util_pct, None);
        assert_eq!(meta.power_w, None);
        assert_eq!(meta.temp_c, None);
        assert_eq!(amd.name(), None);
    }
}

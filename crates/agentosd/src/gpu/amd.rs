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
//! Per-process attribution (ADR-0048 Phase 3) does NOT come from sysfs (which is device-global) —
//! it parses `/proc/<pid>/fdinfo` DRM-client accounting directly (no `libamdgpu_top`/libdrm dep),
//! matched to this GPU's PCI address. Every read fails soft to `None` (fail-open, ADR-0003).

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use super::{GpuMeta, MemInfo, ProcClass, ProcVram};

const AMD_VENDOR: &str = "0x1002";
const DRM_ROOT: &str = "/sys/class/drm";

/// A discovered AMD render device: its `device` sysfs dir, the hwmon dir for power/temp, and the
/// PCI address (`pdev`, e.g. "0000:03:00.0") used to attribute `/proc/<pid>/fdinfo` DRM clients to
/// this GPU (ADR-0048 Phase 3 per-process).
pub struct AmdSysfs {
    device: PathBuf,
    hwmon: Option<PathBuf>,
    pdev: Option<String>,
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
                // `device` is a symlink to the PCI node, e.g. ../../../0000:03:00.0 — its basename is
                // the `drm-pdev` we match fdinfo against. Absent (e.g. a non-symlink) → no per-proc.
                let pdev = std::fs::read_link(&device)
                    .ok()
                    .and_then(|t| t.file_name().map(|n| n.to_string_lossy().into_owned()));
                return Some(AmdSysfs { device, hwmon, pdev });
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

    /// Per-process VRAM holders, from `/proc/<pid>/fdinfo` DRM-client accounting (ADR-0048 Phase 3).
    /// `None` if we can't attribute at all (no PCI address, or `/proc` unreadable). Class is always
    /// [`ProcClass::Unknown`] — AMD exposes per-PID VRAM but NOT a graphics/compute split, so the
    /// consumers route these onto the compute side (the keyhole "who holds VRAM" / telemetry
    /// itemisation work; the gfx-vs-compute *totals* can't be split on AMD — the decided trade-off).
    ///
    /// NOTE (ADR-0048): seeing *other users'* PIDs needs the daemon in the `render`/`video` group (or
    /// `CAP_PERFMON`); without it this silently sees only our own jobs — degrade, never wedge. Not yet
    /// hardware-validated (needs a Radeon); the fdinfo *parsing* is unit-tested with fixtures.
    pub fn processes(&self) -> Option<Vec<ProcVram>> {
        let pdev = self.pdev.as_deref()?;
        Self::processes_in(Path::new("/proc"), pdev)
    }

    /// Testable core of [`processes`](Self::processes): scan `proc_root/<pid>/fdinfo` for DRM clients
    /// on `pdev`. `None` if `proc_root` is unreadable; `Some([])` if readable but nothing holds VRAM.
    fn processes_in(proc_root: &Path, pdev: &str) -> Option<Vec<ProcVram>> {
        let mut out = Vec::new();
        for e in std::fs::read_dir(proc_root).ok()?.flatten() {
            let Some(pid) = e.file_name().to_str().and_then(|s| s.parse::<u32>().ok()) else {
                continue; // non-numeric /proc entries (self, sys, …)
            };
            if let Some(mib) = pid_vram_mib(&e.path().join("fdinfo"), pdev) {
                if mib > 0 {
                    out.push(ProcVram { pid, mib, class: ProcClass::Unknown });
                }
            }
        }
        Some(out)
    }
}

/// Sum a process's VRAM (MiB) across its UNIQUE DRM clients on GPU `pdev`, from `<fdinfo_dir>/*`. A
/// process holds many fds per client, so we dedup by `drm-client-id` (max VRAM seen per client) then
/// sum across clients. Returns `Some(0)` for a process with no DRM client on this GPU; `None` only if
/// the fdinfo dir itself is unreadable (e.g. another user's PID without privilege) — degrade, not fake.
fn pid_vram_mib(fdinfo_dir: &Path, pdev: &str) -> Option<u64> {
    let mut by_client: HashMap<String, u64> = HashMap::new();
    for fd in std::fs::read_dir(fdinfo_dir).ok()?.flatten() {
        let Ok(text) = std::fs::read_to_string(fd.path()) else { continue };
        let (mut this_pdev, mut client, mut vram_kib) = (None, None, None);
        for line in text.lines() {
            if let Some(v) = line.strip_prefix("drm-pdev:") {
                this_pdev = Some(v.trim().to_string());
            } else if let Some(v) = line.strip_prefix("drm-client-id:") {
                client = Some(v.trim().to_string());
            } else if let Some(v) = line.strip_prefix("drm-resident-vram:") {
                vram_kib = parse_mem_kib(v); // standardized key, preferred
            } else if vram_kib.is_none() {
                if let Some(v) = line.strip_prefix("drm-memory-vram:") {
                    vram_kib = parse_mem_kib(v); // amdgpu legacy fallback
                }
            }
        }
        if this_pdev.as_deref() == Some(pdev) {
            if let (Some(c), Some(kib)) = (client, vram_kib) {
                let slot = by_client.entry(c).or_insert(0);
                *slot = (*slot).max(kib);
            }
        }
    }
    Some(by_client.values().sum::<u64>() / 1024) // KiB → MiB
}

/// Parse a DRM fdinfo memory value like "1024 KiB" / "2 MiB" → KiB. Bare number ⇒ KiB (amdgpu default).
fn parse_mem_kib(v: &str) -> Option<u64> {
    let mut it = v.split_whitespace();
    let num: u64 = it.next()?.parse().ok()?;
    Some(match it.next() {
        Some("MiB") => num * 1024,
        Some("B") | Some("bytes") => num / 1024,
        _ => num, // "KiB" or no unit
    })
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

    #[test]
    fn parse_mem_kib_handles_units() {
        assert_eq!(parse_mem_kib("2048 KiB"), Some(2048));
        assert_eq!(parse_mem_kib("2 MiB"), Some(2048));
        assert_eq!(parse_mem_kib("4096"), Some(4096)); // bare ⇒ KiB
        assert_eq!(parse_mem_kib("garbage"), None);
    }

    #[test]
    fn fdinfo_sums_unique_clients_filtered_by_pdev() {
        let fx = Fixture::new();
        let pdev = "0000:03:00.0";
        // pid 100: two fds → the SAME client 7 (counted ONCE: 2048 KiB) + client 8 (1024 KiB resident).
        fx.put("100/fdinfo/3", &format!("drm-pdev:\t{pdev}\ndrm-client-id:\t7\ndrm-memory-vram:\t2048 KiB\n"))
            .put("100/fdinfo/4", &format!("drm-pdev:\t{pdev}\ndrm-client-id:\t7\ndrm-memory-vram:\t2048 KiB\n"))
            .put("100/fdinfo/5", &format!("drm-pdev:\t{pdev}\ndrm-client-id:\t8\ndrm-resident-vram:\t1024 KiB\n"))
            // pid 200: a client on a DIFFERENT GPU → excluded.
            .put("200/fdinfo/3", "drm-pdev:\t0000:09:00.0\ndrm-client-id:\t1\ndrm-memory-vram:\t9999 KiB\n")
            // a non-numeric /proc entry → skipped.
            .put("self/fdinfo/0", &format!("drm-pdev:\t{pdev}\ndrm-client-id:\t1\ndrm-memory-vram:\t500 KiB\n"));

        let procs = AmdSysfs::processes_in(&fx.0, pdev).expect("/proc readable");
        assert_eq!(procs.len(), 1, "only pid 100 holds VRAM on this GPU");
        assert_eq!(procs[0].pid, 100);
        assert_eq!(procs[0].mib, 3); // (client7 2048 once) + (client8 1024) = 3072 KiB = 3 MiB
        assert_eq!(procs[0].class, ProcClass::Unknown); // AMD has no gfx/compute split
    }

    #[test]
    fn fdinfo_unreadable_proc_is_none_not_zero() {
        // Can't even read /proc → None (honest unknown), not an empty/fabricated result.
        assert!(AmdSysfs::processes_in(Path::new("/no/such/proc-xyz"), "0000:03:00.0").is_none());
    }
}

//! `telemetry` — the read-only historian (ADR-0018).
//!
//! The other producers emit the *current* snapshot for a consumer: `feed`→`agent.json` (mood),
//! `keyhole`→`keyhole.json` (the tray instrument), `lease`→`lease.json` (contention mirror). None
//! keeps history. You cannot tune a coexistence policy — which models stay warm, what keep-alive,
//! how much the warm pool may grow before the heavy lane reclaims it — on data you never recorded.
//!
//! This mode is the time-series sibling: every tick it appends ONE self-contained JSON object to a
//! **persistent** `$XDG_STATE_HOME/agentosd/telemetry.jsonl` (survives reboot, unlike the runtime
//! tmpfs the snapshots live in), size-rotated. Analyse it with `jq`/`duckdb`; it is not a metrics
//! server (ADR-0018 non-goal).
//!
//! Safety boundary (ADR-0003 fail-open): strictly READ-ONLY of the system — it observes NVML,
//! Ollama `/api/ps`, and the lease mirror; it never acts on the GPU. A failed append degrades to a
//! warning and the loop continues. Own NVML handle (like `monitor`/`keyhole`), never the lease
//! daemon's.
//!
//! Per-tick line (field order == serialization order == the pinned contract):
//!   {"schema":1,"ts_ms":…,"nvml_ms":…,
//!    "vram":{used_mib,free_mib,total_mib},          // -1 == unknown (NOT zero)
//!    "gpu":{util_pct,power_w,temp_c},               // null == unknown, never synthesized
//!    "gfx_mib":…,"compute_mib":…,                   // per-process attribution; -1 if NVML can't
//!    "procs":[{name,mib,kind}],                     // who holds VRAM (nimbus-flux vs ollama vs …)
//!    "residency":[{name,vram_mib,loaded_secs}],     // Ollama-reported size_vram UNDERCOUNTS (ADR-0018)
//!    "events":["load:…","unload:…"],                // diffed across ticks → load latency & churn
//!    "lease":{tier,holder,preempt},                 // empty string == no datum
//!    "tokens_per_sec":null}                          // null == UNKNOWN until the ADR-0002 proxy

use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::{BufRead, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use nvml_wrapper::Nvml;
use serde::{Deserialize, Serialize};

use crate::feed::feed_dir;
use crate::keyhole::Lease;
use crate::{mib, proc_name, used_mib};

/// Bump on any breaking shape change to a `telemetry.jsonl` line. Pinned by a test below.
const SCHEMA: u32 = 1;

/// Negative sentinel for an unreadable integer datum — distinct from a real `0` (ADR-0012 §4).
const UNK: i64 = -1;

const OLLAMA_PS: &str = "http://127.0.0.1:11434/api/ps";

/// Default sampling cadence. A time series wants regular samples (gaps are data too), so — unlike
/// the edge-driven snapshots — telemetry writes EVERY tick. Override with `--interval SECS`.
const DEFAULT_INTERVAL_SECS: u64 = 2;

/// Rotate `telemetry.jsonl` → `.1` past this size, keeping one prior generation. Override with
/// `AGENTOSD_TELEMETRY_MAX_MIB`.
const DEFAULT_MAX_MIB: u64 = 64;
/// Privacy retention policy (ADR-0018): rotate when the current file's oldest line exceeds this age,
/// and drop a prior generation past it — so the trace is time-bounded, not just disk-bounded. Override
/// with `AGENTOSD_TELEMETRY_RETENTION_DAYS`.
const DEFAULT_RETENTION_DAYS: u64 = 7;
/// How often (in ticks) to check for rotation — cheap, but no need every tick.
const ROTATE_CHECK_EVERY: u64 = 64;

/// Drop sub-threshold procs from the itemised list (idle GL/Wayland contexts are ~6 MiB of pure
/// noise) and cap the list — keeps each line bounded. The `gfx_mib`/`compute_mib` TOTALS are still
/// summed over ALL procs, so the budget math stays complete; only the itemisation is trimmed.
const MIN_PROC_MIB: u64 = 16;
const MAX_PROCS: usize = 16;

/// Privacy minimization (ADR-0018, responsible-AI review): the historian records a persistent,
/// timestamped trace, so it must NOT itemise the names of every app the user runs (Telegram,
/// spotify, …). We name ONLY the processes agentosd actually arbitrates — the GPU-AI runtimes and
/// the graphics-yield target — and collapse everything else into `other-gfx`/`other-compute`
/// aggregates. The per-kind TOTALS are still summed over the full population, so the budget math is
/// unchanged; the analyzer never reads names anyway. Match is a case-insensitive substring.
const ARBITRATED: &[&str] = &["ollama", "nimbus", "comfy", "python"];
fn arbitrated(name: &str) -> bool {
    let n = name.to_ascii_lowercase();
    ARBITRATED.iter().any(|a| n.contains(a))
}

// ---------------------------------------------------------------------------
// The emitted contract (field order == serialization order == the pinned string).
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize)]
struct Tick {
    schema: u32,
    /// Wall-clock unix epoch milliseconds — the time axis for analysis.
    ts_ms: u64,
    /// How long the NVML read took; a cheap proxy for GPU/driver contention.
    nvml_ms: u64,
    vram: Vram,
    gpu: Gpu,
    /// Graphics-process VRAM (the ~7.7 GB Plasma+shader baseline — ADR-0018's dominant lever).
    gfx_mib: i64,
    /// Compute-process VRAM (Ollama/ComfyUI workers).
    compute_mib: i64,
    procs: Vec<Proc>,
    residency: Vec<Residency>,
    /// load:/unload: transitions vs the previous tick — makes load latency & churn greppable.
    events: Vec<String>,
    lease: Lease,
    /// `None` → `null`. Summed from the ADR-0002 proxy stream when it exists; never faked.
    tokens_per_sec: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
struct Vram {
    used_mib: i64,
    free_mib: i64,
    total_mib: i64,
}

/// Each `None` serializes to `null` (unknown). Util %, power (W), temperature (°C).
#[derive(Debug, Clone, PartialEq, Serialize)]
struct Gpu {
    util_pct: Option<u32>,
    power_w: Option<f64>,
    temp_c: Option<u32>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
struct Proc {
    name: String,
    mib: u64,
    /// "gfx" or "compute" — the NVML attribution class.
    kind: &'static str,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
struct Residency {
    name: String,
    /// Ollama's reported `size_vram` in MiB. NOTE: undercounts the real footprint (ADR-0018) — the
    /// per-process `compute_mib` is the truer number; both are recorded so analysis sees the gap.
    vram_mib: u64,
    loaded_secs: i64,
}

// ---------------------------------------------------------------------------
// Sources (each fails soft — fail-open, never wedge, never lie).
// ---------------------------------------------------------------------------

#[derive(Deserialize, Default)]
struct PsResp {
    #[serde(default)]
    models: Vec<PsModel>,
}
#[derive(Deserialize)]
struct PsModel {
    #[serde(default)]
    name: String,
    #[serde(default)]
    size_vram: u64,
}

/// NVML memory + util/power/temp (own handle). Returns the VRAM block, the GPU block, and the read
/// latency. A missing driver → all-unknown sentinels but the tick still records residency + lease.
fn read_gpu(nvml: Option<&Nvml>) -> (Vram, Gpu, Duration) {
    let t = Instant::now();
    let dev = nvml.and_then(|n| n.device_by_index(0).ok());
    let vram = match dev.as_ref().and_then(|d| d.memory_info().ok()) {
        Some(m) => Vram {
            used_mib: mib(m.used) as i64,
            free_mib: mib(m.free) as i64,
            total_mib: mib(m.total) as i64,
        },
        None => Vram { used_mib: UNK, free_mib: UNK, total_mib: UNK },
    };
    let gpu = Gpu {
        util_pct: dev.as_ref().and_then(|d| d.utilization_rates().ok()).map(|u| u.gpu),
        // NVML reports milliwatts; present whole watts.
        power_w: dev.as_ref().and_then(|d| d.power_usage().ok()).map(|mw| (mw as f64) / 1000.0),
        temp_c: dev
            .as_ref()
            .and_then(|d| d.temperature(nvml_wrapper::enum_wrappers::device::TemperatureSensor::Gpu).ok()),
    };
    (vram, gpu, t.elapsed())
}

/// Per-process VRAM attribution (graphics vs compute), itemised by process name. This is what
/// turns "8.2 GB used" into "python 4.6 GB (compute) + kwin_wayland 0.8 GB (gfx) + …" — the basis
/// for ADR-0018's budget analysis. Returns (gfx_total, compute_total, procs); totals are summed
/// over ALL procs (and CAN overlap — NVML lists a dual-use process like `code` in both classes, so
/// they don't sum to `used_mib`; `free_mib` is the ground truth). The itemised list is thresholded
/// + capped to keep each line bounded. Totals are `-1` when NVML can't attribute per-process.
fn read_procs(nvml: Option<&Nvml>) -> (i64, i64, Vec<Proc>) {
    let Some(dev) = nvml.and_then(|n| n.device_by_index(0).ok()) else {
        return (UNK, UNK, Vec::new());
    };
    let mut named: Vec<Proc> = Vec::new();
    let mut gfx = 0u64;
    let mut compute = 0u64;
    let mut other_gfx = 0u64;
    let mut other_compute = 0u64;
    let mut attributed = false;
    for p in dev.running_graphics_processes().unwrap_or_default() {
        if let Some(m) = used_mib(&p.used_gpu_memory) {
            attributed = true;
            gfx += m;
            let name = proc_name(p.pid);
            if arbitrated(&name) {
                named.push(Proc { name, mib: m, kind: "gfx" });
            } else {
                other_gfx += m; // privacy: don't persist third-party app identities
            }
        }
    }
    for p in dev.running_compute_processes().unwrap_or_default() {
        if let Some(m) = used_mib(&p.used_gpu_memory) {
            attributed = true;
            compute += m;
            let name = proc_name(p.pid);
            if arbitrated(&name) {
                named.push(Proc { name, mib: m, kind: "compute" });
            } else {
                other_compute += m;
            }
        }
    }
    if !attributed {
        // NVML present but per-process attribution unavailable — be honest, don't fabricate a 0.
        return (UNK, UNK, named);
    }
    // Itemise only the arbitrated holders: drop noise, biggest first, capped.
    named.retain(|p| p.mib >= MIN_PROC_MIB);
    named.sort_by_key(|p| std::cmp::Reverse(p.mib));
    named.truncate(MAX_PROCS);
    // Everything else is folded into aggregate rows — the baseline size is preserved, the identities
    // are not. Totals (gfx/compute) above are summed over ALL procs, so the budget math is complete.
    if other_gfx > 0 {
        named.push(Proc { name: "other-gfx".into(), mib: other_gfx, kind: "gfx" });
    }
    if other_compute > 0 {
        named.push(Proc { name: "other-compute".into(), mib: other_compute, kind: "compute" });
    }
    (gfx as i64, compute as i64, named)
}

/// Resident Ollama models → residency rows (name + reported size_vram + loaded_secs). `loaded_secs`
/// comes from a producer-local first-seen clock (`/api/ps` reports no load time). Unreachable
/// Ollama → empty.
fn read_residency(
    http: &reqwest::blocking::Client,
    first_seen: &mut HashMap<String, Instant>,
    now: Instant,
) -> Vec<Residency> {
    let models: Vec<(String, u64)> = http
        .get(OLLAMA_PS)
        .send()
        .and_then(|r| r.json::<PsResp>())
        .map(|ps| {
            ps.models
                .into_iter()
                .filter(|m| !m.name.is_empty())
                .map(|m| (m.name, mib(m.size_vram)))
                .collect()
        })
        .unwrap_or_default();

    // Forget models that have unloaded so a reload reports a fresh duration.
    let live: Vec<String> = models.iter().map(|(n, _)| n.clone()).collect();
    first_seen.retain(|name, _| live.contains(name));

    models
        .into_iter()
        .map(|(name, vram_mib)| {
            let since = *first_seen.entry(name.clone()).or_insert(now);
            Residency { name, vram_mib, loaded_secs: now.duration_since(since).as_secs() as i64 }
        })
        .collect()
}

/// Optional lease mirror (`$XDG_RUNTIME_DIR/nimbus-aurora/lease.json`), pushed off-lock by the
/// `lease` daemon. Absent/unparseable → empty (no contention). Same source the keyhole reads.
fn read_lease(path: &Path) -> Lease {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str::<Lease>(&s).ok())
        .unwrap_or_default()
}

/// load:/unload: transitions between the previous and current residency sets. Pure so it can be
/// tested; the order is deterministic (unloads then loads, each in prev/current order).
fn diff_events(prev: &[String], current: &[String]) -> Vec<String> {
    let mut events = Vec::new();
    for name in prev {
        if !current.contains(name) {
            events.push(format!("unload:{name}"));
        }
    }
    for name in current {
        if !prev.contains(name) {
            events.push(format!("load:{name}"));
        }
    }
    events
}

// ---------------------------------------------------------------------------
// Persistence: append-only JSONL with size rotation.
// ---------------------------------------------------------------------------

/// `$XDG_STATE_HOME/agentosd` (default `~/.local/state/agentosd`), created if absent. Persistent —
/// deliberately NOT the runtime tmpfs the snapshots use, because the history must survive reboot.
fn state_dir() -> std::io::Result<PathBuf> {
    let base = std::env::var("XDG_STATE_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| {
            let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
            format!("{home}/.local/state")
        });
    let dir = PathBuf::from(base).join("agentosd");
    // 0700: the log is a behavioral trace (model + GPU-process history) — not world-readable, even
    // on a shared box (ADR-0018 privacy; ADR-0016 set this bar). Don't trust umask.
    fs::DirBuilder::new().recursive(true).mode(0o700).create(&dir)?;
    Ok(dir)
}

/// Fix perms on artifacts an earlier (umask-default) run may have created world-readable, and tighten
/// the dir if it already existed at 0755. Idempotent, best-effort.
fn harden_perms(dir: &Path, out: &Path) {
    let _ = fs::set_permissions(dir, fs::Permissions::from_mode(0o700));
    for p in [out.to_path_buf(), out.with_extension("jsonl.1")] {
        if p.exists() {
            let _ = fs::set_permissions(&p, fs::Permissions::from_mode(0o600));
        }
    }
}

fn max_bytes() -> u64 {
    std::env::var("AGENTOSD_TELEMETRY_MAX_MIB")
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .filter(|&m| m > 0)
        .unwrap_or(DEFAULT_MAX_MIB)
        * 1024
        * 1024
}

fn retention_ms() -> u64 {
    std::env::var("AGENTOSD_TELEMETRY_RETENTION_DAYS")
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .filter(|&d| d > 0)
        .unwrap_or(DEFAULT_RETENTION_DAYS)
        * 24
        * 3600
        * 1000
}

/// `ts_ms` of the first (oldest) line of a jsonl file — for the age-based retention check. Cheap:
/// reads only the first line.
fn first_line_ts(path: &Path) -> Option<u64> {
    let f = fs::File::open(path).ok()?;
    let mut line = String::new();
    std::io::BufReader::new(f).read_line(&mut line).ok()?;
    serde_json::from_str::<serde_json::Value>(&line).ok()?.get("ts_ms")?.as_u64()
}

/// Rotate `path` → `path.1` when it exceeds `max` bytes OR its oldest line is past the retention
/// window, and drop a prior `.1` that is itself past the window. Bounds the trace by BOTH disk and
/// time. Best-effort: a failed rotate just means a little overshoot — never a reason to stop logging.
fn rotate_if_needed(path: &Path, max: u64, now_ms: u64, retention: u64) {
    let backup = path.with_extension("jsonl.1");
    if let Some(ts) = first_line_ts(&backup) {
        if now_ms.saturating_sub(ts) > retention {
            let _ = fs::remove_file(&backup); // a prior generation past the window — forget it
        }
    }
    let too_big = fs::metadata(path).map(|m| m.len() > max).unwrap_or(false);
    let too_old = first_line_ts(path).is_some_and(|ts| now_ms.saturating_sub(ts) > retention);
    if too_big || too_old {
        let _ = fs::rename(path, &backup);
    }
}

/// Append one line (the JSON + '\n') in a single write. Single writer + line-terminated → a tailing
/// reader never sees a torn record. Opens per-append: cheap, and survives a rotation underneath us.
fn append_line(path: &Path, line: &str) -> std::io::Result<()> {
    // mode(0o600): a file created here is private from birth (no umask race window).
    let mut f = OpenOptions::new().create(true).append(true).mode(0o600).open(path)?;
    f.write_all(line.as_bytes())?;
    f.write_all(b"\n")
}

// ---------------------------------------------------------------------------
// The loop.
// ---------------------------------------------------------------------------

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

fn parse_args(args: &[String]) -> (bool, Duration) {
    let once = args.iter().any(|a| a == "--once");
    let interval = args
        .iter()
        .position(|a| a == "--interval")
        .and_then(|i| args.get(i + 1))
        .and_then(|s| s.parse::<u64>().ok())
        .filter(|&s| s > 0)
        .unwrap_or(DEFAULT_INTERVAL_SECS);
    (once, Duration::from_secs(interval))
}

/// Sample the read-only sources and append one line per tick to `telemetry.jsonl`. `--once` writes
/// a single line and exits (verify/CI). `--interval SECS` overrides the 2 s cadence. Fail-open: a
/// write error logs and the loop continues; a missing NVML records unknown VRAM but keeps going.
pub fn run(args: Vec<String>) -> Result<(), Box<dyn std::error::Error>> {
    let (once, interval) = parse_args(&args);
    let dir = state_dir()?;
    let out = dir.join("telemetry.jsonl");
    harden_perms(&dir, &out);
    let lease_mirror = feed_dir()?.join("lease.json");
    let max = max_bytes();
    let retention = retention_ms();
    let retention_days = retention / (24 * 3600 * 1000);
    println!(
        "agentosd telemetry → {} (every {}s, rotate at {} MiB / {} days, mode 0600)",
        out.display(),
        interval.as_secs(),
        max / (1024 * 1024),
        retention_days,
    );
    // Disclosure (ADR-0018 privacy): record what this trace contains and how to stop/forget it.
    println!(
        "  records: VRAM + arbitrated GPU-process sizes (other apps bucketed, not named) + Ollama \
         model residency/load history. Local-only, retained ~{} days. Stop: `systemctl --user stop \
         agentos-telemetry`. Forget: delete {}*",
        retention_days,
        out.display(),
    );

    let nvml = Nvml::init().ok();
    if nvml.is_none() {
        eprintln!("agentosd telemetry: NVML unavailable — VRAM/gpu will record unknown (continuing)");
    }
    let http = reqwest::blocking::Client::builder().timeout(Duration::from_secs(2)).build()?;

    let mut first_seen: HashMap<String, Instant> = HashMap::new();
    let mut prev_models: Vec<String> = Vec::new();
    let mut ticks: u64 = 0;

    loop {
        if ticks.is_multiple_of(ROTATE_CHECK_EVERY) {
            rotate_if_needed(&out, max, now_ms(), retention);
        }

        let now = Instant::now();
        let (vram, gpu, nvml_read) = read_gpu(nvml.as_ref());
        let (gfx_mib, compute_mib, procs) = read_procs(nvml.as_ref());
        let residency = read_residency(&http, &mut first_seen, now);
        let lease = read_lease(&lease_mirror);

        let current_models: Vec<String> = residency.iter().map(|r| r.name.clone()).collect();
        let events = diff_events(&prev_models, &current_models);
        prev_models = current_models;

        let tick = Tick {
            schema: SCHEMA,
            ts_ms: now_ms(),
            nvml_ms: nvml_read.as_millis() as u64,
            vram,
            gpu,
            gfx_mib,
            compute_mib,
            procs,
            residency,
            events,
            lease,
            tokens_per_sec: None, // P2: summed from the ADR-0002 proxy stream. Never faked.
        };

        match serde_json::to_string(&tick) {
            Ok(line) => {
                if let Err(e) = append_line(&out, &line) {
                    eprintln!("agentosd telemetry: append failed: {e}");
                } else if !tick.events.is_empty() {
                    // Loud only on residency transitions — the moments worth watching live.
                    println!("[{}] {}", crate::now_hms(), tick.events.join(" "));
                }
            }
            Err(e) => eprintln!("agentosd telemetry: serialize failed: {e}"),
        }

        ticks = ticks.wrapping_add(1);
        if once {
            break;
        }
        thread::sleep(interval);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn events_diff_loads_and_unloads() {
        // A swap: qwen out, gemma in. Unloads listed before loads; deterministic order.
        let prev = vec!["qwen3.6:27b".to_string(), "gemma4:latest".to_string()];
        let now = vec!["gemma4:latest".to_string(), "hermes4-14b:latest".to_string()];
        assert_eq!(diff_events(&prev, &now), vec!["unload:qwen3.6:27b", "load:hermes4-14b:latest"]);

        // Steady state → no events (the common idle tick).
        assert_eq!(diff_events(&now, &now), Vec::<String>::new());

        // Cold start → only loads.
        assert_eq!(diff_events(&[], &now), vec!["load:gemma4:latest", "load:hermes4-14b:latest"]);
    }

    #[test]
    fn args_parse_once_and_interval() {
        let (once, iv) = parse_args(&["--once".into()]);
        assert!(once && iv == Duration::from_secs(DEFAULT_INTERVAL_SECS));
        let (once, iv) = parse_args(&["--interval".into(), "10".into()]);
        assert!(!once && iv == Duration::from_secs(10));
        // Garbage / zero interval falls back to the default, never 0 (which would busy-loop).
        let (_, iv) = parse_args(&["--interval".into(), "0".into()]);
        assert_eq!(iv, Duration::from_secs(DEFAULT_INTERVAL_SECS));
    }

    #[test]
    fn pins_the_exact_contract() {
        // Producer pin (mirrors keyhole.rs). If this string changes, bump SCHEMA and update any
        // analysis tooling in lockstep.
        let tick = Tick {
            schema: 1,
            ts_ms: 1_718_500_000_000,
            nvml_ms: 3,
            vram: Vram { used_mib: 7734, free_mib: 16374, total_mib: 24564 },
            gpu: Gpu { util_pct: Some(39), power_w: Some(60.2), temp_c: Some(44) },
            gfx_mib: 7700,
            compute_mib: 0,
            procs: vec![Proc { name: "nimbus-flux".into(), mib: 1500, kind: "gfx" }],
            residency: vec![Residency {
                name: "gemma4:latest".into(),
                vram_mib: 3390,
                loaded_secs: 12,
            }],
            events: vec!["load:gemma4:latest".into()],
            lease: Lease { tier: "batch".into(), holder: "ComfyUI".into(), preempt: String::new() },
            tokens_per_sec: None,
        };
        assert_eq!(
            serde_json::to_string(&tick).unwrap(),
            r#"{"schema":1,"ts_ms":1718500000000,"nvml_ms":3,"vram":{"used_mib":7734,"free_mib":16374,"total_mib":24564},"gpu":{"util_pct":39,"power_w":60.2,"temp_c":44},"gfx_mib":7700,"compute_mib":0,"procs":[{"name":"nimbus-flux","mib":1500,"kind":"gfx"}],"residency":[{"name":"gemma4:latest","vram_mib":3390,"loaded_secs":12}],"events":["load:gemma4:latest"],"lease":{"tier":"batch","holder":"ComfyUI","preempt":""},"tokens_per_sec":null}"#
        );
    }

    #[test]
    fn unknown_gpu_serializes_as_sentinels_and_nulls() {
        // Fail-open posture: a missing NVML records honest unknowns, never fabricated zeros.
        let tick = Tick {
            schema: SCHEMA,
            ts_ms: 0,
            nvml_ms: 0,
            vram: Vram { used_mib: UNK, free_mib: UNK, total_mib: UNK },
            gpu: Gpu { util_pct: None, power_w: None, temp_c: None },
            gfx_mib: UNK,
            compute_mib: UNK,
            procs: vec![],
            residency: vec![],
            events: vec![],
            lease: Lease::default(),
            tokens_per_sec: None,
        };
        let s = serde_json::to_string(&tick).unwrap();
        assert!(s.contains(r#""vram":{"used_mib":-1,"free_mib":-1,"total_mib":-1}"#));
        assert!(s.contains(r#""gpu":{"util_pct":null,"power_w":null,"temp_c":null}"#));
        assert!(s.contains(r#""gfx_mib":-1,"compute_mib":-1"#));
        assert!(s.contains(r#""tokens_per_sec":null"#));
    }
}

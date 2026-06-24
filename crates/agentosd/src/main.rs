//! agentosd — AgentOS resource broker.
//!
//! Modes:
//!   * `monitor` — v0 read-only VRAM monitor (below).
//!   * `feed`    — P1 producer: Hermes fleet state → `agent.json` (see `feed.rs`).
//!   * `keyhole` — ADR-0012 producer: lease/VRAM/residency/fleet → `keyhole.json` for the
//!     tray instrument; read-only, own NVML handle, honest UNKNOWN (see `keyhole.rs`).
//!   * `coord`   — VRAM coordinator slice: own a GPU job's PID, NVML-gated
//!     admission, SIGKILL on preempt (ADR-0010; see `coord.rs`).
//!   * `lease`   — D-Bus GPU lease server: acquire/release/status over the session
//!     bus, same admission + priority core (ADR-0006/0010; see `lease.rs`).
//!   * `telemetry` — read-only historian: append per-tick VRAM/residency/lease to a
//!     persistent `telemetry.jsonl` so coexistence policy can be tuned and validated on
//!     real data, not guessed (ADR-0018; see `telemetry.rs`).
//!   * `coexist` — read-only analyzer: turn `telemetry.jsonl` into a proposed residency plan
//!     (warm pool vs heavy lane, max-loaded/keep-alive, real footprints) — proposes, never
//!     applies (ADR-0018 §4; see `analyze.rs`).
//!   * `mcp` — agent-facing GPU MCP server (ADR-0020 Phase 1, perceive-only): `gpu_status`/
//!     `gpu_residency`/`gpu_why` over stdio, read-only, no NVML/D-Bus/network (see `mcp.rs`).
//!   * `scene` — ADR-0030 reactive disposer: reads `agent.json`/`wind.json` → one pre-disposed
//!     `scene-params.json` (mood axes only; clamp/slew/snap-to-rest, fail-to-calm) for the UE
//!     dark-ride applier. PAUSED prototype ahead of the UE wallpaper layer (see `scene.rs`).
//!   * `rc` — ADR-0030 D1 reactive MOOD pusher: reads `scene-params.json` → loopback UE Remote
//!     Control (`SetScalarParameterValue` on the reactive MPC). MOOD only (NOT the throttle
//!     channel); loopback-literal, allowlisted verb, silent-at-rest, resync-on-reconnect. GATED on
//!     the ADR-0029 §B live security verification (see `rc.rs`).
//!
//! `monitor` proves the load-bearing pieces of the VRAM coordinator WITHOUT doing
//! anything destructive:
//!   * read GPU VRAM via NVML, attributed PER-PROCESS (graphics vs compute),
//!     not by crude subtraction
//!   * read Ollama's loaded models (`/api/ps`) and local model sizes (`/api/tags`)
//!   * compute a FIT-BASED verdict (ADR-0004): would the largest local model load
//!     alongside the current graphics footprint — and would evicting the wallpaper's
//!     ray tracing even make it fit?
//!
//! No eviction, no `ollama stop`, no nimbus-flux kill/relaunch yet. Decisions are
//! logged dry so the policy can be validated before it is ever allowed to act.

use std::{fs, thread, time::Duration};

use serde::Deserialize;

mod analyze;
mod coord;
mod feed;
mod governor;
mod gpu;
mod keyhole;
mod lease;
mod mcp;
mod queue;
mod rc;
mod rc_throttle;
mod reclaim;
mod scene;
mod scope_reclaim;
mod telemetry;
mod wind;

const OLLAMA_PS: &str = "http://127.0.0.1:11434/api/ps";
const OLLAMA_TAGS: &str = "http://127.0.0.1:11434/api/tags";

/// Headroom kept free to avoid an edge-case OOM on load.
const SAFETY_MIB: u64 = 512;
/// Approx VRAM reclaimed by relaunching nimbus-flux without ray tracing (ADR-0004,
/// spike #2 measured ~0.5–1.5GB; tuned later from real per-process data).
const RT_SAVING_MIB: u64 = 1500;
/// Rough KV-cache / context overhead added to a model's on-disk weight size to
/// estimate its resident VRAM footprint. Tunable; intentionally conservative.
const KV_EST_MIB: u64 = 1024;

#[derive(Deserialize)]
struct PsResp {
    #[serde(default)]
    models: Vec<PsModel>,
}
#[derive(Deserialize)]
struct PsModel {
    name: String,
    #[serde(default)]
    size_vram: u64,
}

#[derive(Deserialize)]
struct TagsResp {
    #[serde(default)]
    models: Vec<TagModel>,
}
#[derive(Deserialize)]
struct TagModel {
    name: String,
    #[serde(default)]
    size: u64, // on-disk bytes
}

pub(crate) fn mib(bytes: u64) -> u64 {
    bytes / (1024 * 1024)
}

pub(crate) fn proc_name(pid: u32) -> String {
    fs::read_to_string(format!("/proc/{pid}/comm"))
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|_| "?".into())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mode = std::env::args().nth(1).unwrap_or_else(|| "monitor".into());
    match mode.as_str() {
        "monitor" => run_monitor(),
        "feed" => feed::run(std::env::args().any(|a| a == "--once")),
        "keyhole" => keyhole::run(std::env::args().any(|a| a == "--once")),
        "coord" => coord::run(std::env::args().skip(2).collect()),
        "lease" => lease::run(),
        "queue" => queue::run(),
        "telemetry" => telemetry::run(std::env::args().skip(2).collect()),
        "coexist" => analyze::run(std::env::args().skip(2).collect()),
        "mcp" => mcp::run(std::env::args().skip(2).collect()),
        "scene" => scene::run(std::env::args().any(|a| a == "--once")),
        "rc" => rc::run(std::env::args().any(|a| a == "--once")),
        other => {
            eprintln!(
                "agentosd: unknown mode `{other}`. Modes: monitor (read-only VRAM), \
                 feed (emit agent.json), keyhole (emit keyhole.json for the tray instrument), \
                 coord (VRAM lease + SIGKILL evict), lease (D-Bus lease server), \
                 telemetry (append telemetry.jsonl history for coexistence tuning), \
                 coexist (analyze telemetry → propose a residency plan), \
                 mcp (agent-facing read-only GPU MCP server, ADR-0020), \
                 scene (reactive dark-ride mood disposer → scene-params.json, ADR-0030), \
                 rc (reactive mood pusher: scene-params.json → loopback UE Remote Control, ADR-0030/0029 §B). See docs/adr/."
            );
            std::process::exit(2);
        }
    }
}

/// v0 read-only VRAM monitor (ADR-0004) — unchanged from the original `main`.
fn run_monitor() -> Result<(), Box<dyn std::error::Error>> {
    let gpu = gpu::GpuBackend::detect();
    if gpu.is_absent() {
        eprintln!("No GPU detected (no NVIDIA NVML, no AMD sysfs) — nothing to monitor.");
        std::process::exit(1);
    }
    println!(
        "agentosd monitor (read-only) — {} [{}]",
        gpu.name().unwrap_or_else(|| "GPU0".into()),
        gpu.vendor()
    );

    let http = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()?;

    loop {
        let Some(mem) = gpu.mem() else {
            eprintln!("[{}] VRAM read failed (continuing)", now_hms());
            thread::sleep(Duration::from_secs(2));
            continue;
        };
        let (total, used, free) = (mem.total, mem.used, mem.free);

        // --- per-process attribution (the real fix vs `used - model_vram`). `None` == the backend
        //     can't attribute per-process (an AMD/None backend, or NVML without per-proc data) → we
        //     fall back to the non-Ollama-remainder estimate below. ---
        let procs = gpu.processes();
        let attributed = procs.is_some();
        let procs = procs.unwrap_or_default();

        let mut gfx_mib = 0u64;
        let mut gfx_list: Vec<String> = Vec::new();
        let mut rt_running = false; // is the nimbus-flux RT wallpaper actually up?
        let mut comp_mib = 0u64;
        let mut comp_list: Vec<String> = Vec::new();
        for p in &procs {
            let name = proc_name(p.pid);
            match p.class {
                gpu::ProcClass::Graphics => {
                    if name.contains("nimbus") {
                        rt_running = true;
                    }
                    gfx_mib += p.mib;
                    gfx_list.push(format!("{name}:{}M", p.mib));
                }
                // Compute, plus AMD's unclassified holders, count toward the compute side.
                gpu::ProcClass::Compute | gpu::ProcClass::Unknown => {
                    comp_mib += p.mib;
                    comp_list.push(format!("{name}:{}M", p.mib));
                }
            }
        }

        // --- Ollama: loaded model + local model sizes ---
        let loaded: Vec<(String, u64)> = http
            .get(OLLAMA_PS)
            .send()
            .and_then(|r| r.json::<PsResp>())
            .map(|ps| ps.models.into_iter().map(|m| (m.name, mib(m.size_vram))).collect())
            .unwrap_or_default();
        let loaded_vram: u64 = loaded.iter().map(|(_, v)| v).sum();

        let graphics_mib = if attributed {
            gfx_mib
        } else {
            used.saturating_sub(loaded_vram) // crude fallback
        };

        let largest: Option<(String, u64)> = http
            .get(OLLAMA_TAGS)
            .send()
            .and_then(|r| r.json::<TagsResp>())
            .map(|t| {
                t.models
                    .into_iter()
                    .map(|m| (m.name, mib(m.size) + KV_EST_MIB))
                    .max_by_key(|(_, v)| *v)
            })
            .unwrap_or(None);

        // --- fit-based verdict (ADR-0004) ---
        let budget_now = total.saturating_sub(graphics_mib + SAFETY_MIB);
        // The wallpaper-eviction lever only exists if nimbus-flux is actually running.
        let rt_saving = if rt_running { RT_SAVING_MIB } else { 0 };
        let budget_evicted =
            total.saturating_sub(graphics_mib.saturating_sub(rt_saving) + SAFETY_MIB);
        let verdict = match &largest {
            Some((_, est)) if *est <= budget_now => "FITS",
            Some((_, est)) if *est <= budget_evicted => "EVICT-WALLPAPER→FITS",
            Some(_) if rt_running => "WONT-FIT (even evicting wallpaper; smaller model / CPU offload)",
            Some(_) => "WONT-FIT (no RT wallpaper to evict; smaller model / CPU offload)",
            None => "ok (ollama unreachable)",
        };

        let attr_tag = if attributed { "" } else { " (est, NVML per-proc unavailable)" };
        println!(
            "\n[{}] total {}M | used {}M | free {}M",
            now_hms(),
            total,
            used,
            free
        );
        println!(
            "  gfx≈{}M{} (wallpaper RT: {}) | compute≈{}M | loaded: {}",
            graphics_mib,
            attr_tag,
            if rt_running { "on" } else { "off" },
            comp_mib,
            if loaded.is_empty() {
                "(none)".into()
            } else {
                loaded
                    .iter()
                    .map(|(n, v)| format!("{n}({v}M)"))
                    .collect::<Vec<_>>()
                    .join(", ")
            }
        );
        if attributed {
            println!(
                "  procs: gfx[{}] compute[{}]",
                gfx_list.join(", "),
                comp_list.join(", ")
            );
        }
        match &largest {
            Some((n, est)) => println!(
                "  fit: largest={} est {}M | budget now {}M / post-evict {}M  →  {}",
                n, est, budget_now, budget_evicted, verdict
            ),
            None => println!("  fit: {}", verdict),
        }

        thread::sleep(Duration::from_secs(2));
    }
}

/// HH:MM:SS (UTC) without a date crate — adequate for a monitor log.
pub(crate) fn now_hms() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let s = secs % 86_400;
    format!("{:02}:{:02}:{:02}", s / 3600, (s % 3600) / 60, s % 60)
}

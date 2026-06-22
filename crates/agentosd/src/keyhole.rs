//! `keyhole` — the legibility producer (ADR-0012).
//!
//! A read-only sibling of `feed` (ADR-0007 producer family). Where `feed` emits the
//! 4-scalar ambient mood (`agent.json`) the reactive wallpaper consumes, `keyhole` emits a
//! richer, SEPARATE `keyhole.json` for the tray instrument — the slice only agentosd owns:
//! the GPU lease/arbitration state (led), VRAM pressure, model residency, and the fleet
//! counts behind the mood. It does NOT widen the `agent.json` contract (ADR-0012 §2).
//!
//! Safety boundary (ADR-0012 §3, ADR-0003 fail-open): this is a strictly READ-ONLY observer.
//! It holds its OWN NVML handle (like `monitor`), never the `lease` daemon's — observation is
//! downstream of a file the substrate writes, so a render can never delay a SIGKILL. Lease
//! state is read from an OPTIONAL `lease.json` mirror (the daemon pushes it off-lock; absent →
//! "no contention"). When a source is unreadable the field degrades to a sentinel and the UI
//! renders an em-dash — never a fabricated value.
//!
//! The honesty fix (ADR-0012 §4): `feed` folds an unreachable Hermes into calm `idle`
//! (`read_fleet().unwrap_or_default()`); the keyhole does NOT. A gateway it cannot read →
//! `gateway:"unknown"` + `state:"unknown"`, which the consumer renders as
//! "Status unavailable — can't reach Hermes", distinct from `idle` and from a real `0`.
//!
//! Output contract — schema 3, consumed by `spikes/keyhole/contents/ui/KeyholeModel.qml`:
//!   {"schema":3,"state":…,"gateway":…,"floats":{busy,warm,snag},
//!    "fleet":{running,queued,snagged},          // -1 == unknown (NOT zero)
//!    "lease":{tier,holder,preempt},             // empty string == no datum
//!    "vram":{used_mib,total_mib},               // -1 == unknown
//!    "residency":[{name,loaded_secs}],
//!    "workload":{name,used_mib},                 // schema 3: dominant GPU compute process (NVML).
//!                                                // name "" == none/unknown (UI hides the row);
//!                                                // the one attribution Ollama-residency + lease
//!                                                // both miss — most of all ComfyUI (the dreaming
//!                                                // backend), which holds no lease and is no model.
//!    "tokens_per_sec":null,                      // null == UNKNOWN, never synthesized (P2: proxy)
//!    "pending_requests":{held,needs_review}}     // schema 2 (ADR-0019 §6): lucid queue mirror.
//!                                                // 0 == empty (a REAL datum, never -1/UNKNOWN);
//!                                                // `held` is calm weather (count only, NEVER warm),
//!                                                // `needs_review` is the warm-bloom cohort — but
//!                                                // the warmth is the lucid→feed.rs sidecar's job
//!                                                // (G1), NOT written here (read-only mirror).

use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::thread;
use std::time::{Duration, Instant};

use nvml_wrapper::enums::device::UsedGpuMemory;
use nvml_wrapper::Nvml;
use serde::{Deserialize, Serialize};

use crate::feed::{
    derive_feed, feed_dir, hermes_path, read_fleet, read_gateway, read_needs_you, state_word,
    FleetCounts,
};

/// Bump on any breaking shape change to `keyhole.json`. Pinned by `pins_the_exact_contract`.
/// schema 2 (ADR-0019 §6): adds the additive `pending_requests` block.
/// schema 3 (ADR-0012 workload attribution): adds the additive `workload` block — the dominant
/// GPU compute process by VRAM (from NVML), the slice neither Ollama-residency nor the lease
/// daemon can see. Both additions are purely additive; an older consumer ignores the unknown
/// field and the serializer always emits an honest-empty default, never absent.
const SCHEMA: u32 = 3;

/// Negative sentinel for an unreadable integer datum — distinct from a real `0` (ADR-0012 §4).
const UNK: i64 = -1;

const OLLAMA_PS: &str = "http://127.0.0.1:11434/api/ps";

/// GPU "is actually working" gate (ADR-0012): an otherwise-idle fleet promotes to `working`
/// only when the GPU is BOTH computing (util ≥ floor) AND holding a real workload (VRAM ≥ frac
/// of total, OR a resident Ollama model). The AND deliberately excludes the reactive shader
/// wallpaper (nimbus aurora), which burns GPU compute but uses ~no VRAM and loads no model.
const GPU_UTIL_MIN: u32 = 25;
const GPU_VRAM_MIN_FRAC: f64 = 0.20;

/// WORKLOAD attribution (schema 3): only name the dominant GPU compute process when it holds a
/// HEAVY share of VRAM (≥ this fraction of total). Keeps an idle-resident process out of the row
/// — ComfyUI sitting at a few hundred MB between dreams stays hidden; a 21GB generation shows.
/// Decoupled from the util gate on purpose: "who holds the memory" is a steadier signal than
/// instantaneous util (which dips between diffusion steps and would make the row flicker).
const WORKLOAD_MIN_FRAC: f64 = 0.20;

// ---------------------------------------------------------------------------
// The emitted contract (field order == serialization order == the pinned string).
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct KeyholeFeed {
    pub schema: u32,
    pub state: String,
    pub gateway: String,
    pub floats: Floats,
    pub fleet: Fleet,
    pub lease: Lease,
    pub vram: Vram,
    pub residency: Vec<Residency>,
    /// schema 3: the dominant GPU compute process by VRAM (NVML), or empty when nothing heavy runs.
    /// This is the attribution Ollama-residency and the lease daemon both miss — chiefly ComfyUI
    /// (the dreaming backend), which holds no agentosd lease and is no Ollama model, so without this
    /// a 21GB generation reads as a bare "Working" with every explanatory field blank.
    pub workload: Workload,
    /// `None` serializes to `null` — UNKNOWN. Never synthesized in v1 (needs the ADR-0002 proxy).
    pub tokens_per_sec: Option<f64>,
    /// schema 2 (ADR-0019 §6): the local lucid request queue, split into TWO honest counts.
    /// `held` is calm "waiting on the GPU" weather (renders idle/`--st-idle`, NEVER warm);
    /// `needs_review` is "your move is one click" (the warm-bloom cohort). Two counts → two tray
    /// lines, per the load-bearing held(deferred) vs needs-review distinction (ADR-0019 §3, §6).
    /// Always present (default `{0,0}`), never `null`: an empty queue is a real datum, not UNKNOWN.
    pub pending_requests: Pending,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Floats {
    pub busy: f64,
    pub warm: f64,
    pub snag: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Fleet {
    pub running: i64,
    pub queued: i64,
    pub snagged: i64,
}

/// Empty strings render as em-dash / "no contention" — the safe-unknown posture.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct Lease {
    #[serde(default)]
    pub tier: String,
    #[serde(default)]
    pub holder: String,
    #[serde(default)]
    pub preempt: String,
    /// ADR-0041 drain-on-free signal: a monotonic counter the lease daemon bumps whenever the lease
    /// transitions to holder-none (a workflow released / exited / its TTL expired) — i.e. cross-workflow
    /// VRAM capacity MAY have increased. It is an OPTIMISTIC re-check hint, NOT a confirmed-free signal:
    /// it is bumped on the holder-none edge (before a Spawned victim's async reap completes), so a
    /// consumer must always re-verify via the daemon's live-NVML `admit` — never drive a destructive act
    /// off it. The VRAM-demand arbiter (`agentosd queue`) polls `lease.json` (this counter is the future
    /// inotify latency optimization) and offers a waiter a turn when the lease reads free. NOT a keyhole
    /// field (the tray ignores it); `skip_serializing_if` keeps it OUT of the at-rest keyhole.json so the
    /// ADR-0012 contract is byte-unchanged until the lease has actually freed at least once.
    #[serde(default, skip_serializing_if = "is_zero_u64")]
    pub freed_seq: u64,
}

fn is_zero_u64(n: &u64) -> bool {
    *n == 0
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Vram {
    pub used_mib: i64,
    pub total_mib: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Residency {
    pub name: String,
    pub loaded_secs: i64,
}

/// schema 3: the dominant GPU compute process — "what is actually holding the card." Read straight
/// from NVML's running-compute-process table (a measured datum, never synthesized), gated so only a
/// heavy holder surfaces. The honest-empty default is `{name:"", used_mib:-1}`: an empty name tells
/// the UI to hide the row (calm at rest, density grows with load) — distinct from a fabricated value.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Workload {
    /// Human label for the process ("ComfyUI" / "Ollama" / `comm`). "" == none / unreadable.
    #[serde(default)]
    pub name: String,
    /// VRAM the process holds, MiB. -1 == unknown (the UI never renders a guessed number).
    #[serde(default = "minus_one")]
    pub used_mib: i64,
}
fn minus_one() -> i64 {
    -1
}
impl Default for Workload {
    /// `{name:"", used_mib:-1}` — the no-heavy-workload / unreadable posture (UI hides the row).
    fn default() -> Self {
        Workload { name: String::new(), used_mib: -1 }
    }
}

/// schema 2 (ADR-0019 §6): the local lucid request-queue breakdown. Two SEPARATE counts because
/// `held` and `needs_review` are terminal-distinct states with distinct chrome AND distinct ambient
/// channels — collapsing them into one int reproduces the exact dark pattern the honest-mapping
/// non-negotiable forbids (ADR-0019 §3). This is a READ-ONLY mirror the tray displays; the keyhole
/// is an observer and NEVER increments `needs_you.json` itself — the warm bloom is the lucid
/// sidecar's job through `feed.rs` (G1), gated on `gateway_alive` (`feed.rs:83-86`). Both counts
/// default to `0` (never `-1`/UNKNOWN): an empty queue is a real, knowable datum, and an unreadable
/// sidecar collapses to "no pending work" — the calm, fail-open posture (a missing queue must read
/// as nothing-waiting, never as a fabricated backlog). Sources from the OPTIONAL lucid sidecar
/// (`pending.json`); absent → `{0,0}`.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize, Default)]
pub struct Pending {
    /// Couldn't-run-now, will auto-run on a lease-free event. CALM weather — keyhole count only,
    /// never warm (`held(deferred)` invariant, ADR-0019 §3).
    #[serde(default)]
    pub held: i64,
    /// B2 can't-verify / borderline consent → "Needs your OK." The warm-bloom cohort; the warmth
    /// itself is produced by the lucid→`feed.rs` sidecar (G1), NOT by the keyhole writing it here.
    #[serde(default)]
    pub needs_review: i64,
}

// ---------------------------------------------------------------------------
// Sources (each fails soft to a sentinel — fail-open, never wedge, never lie).
// ---------------------------------------------------------------------------

/// `/api/ps` model entry — we only need the name (residency list).
#[derive(Deserialize)]
struct PsModel {
    #[serde(default)]
    name: String,
}
#[derive(Deserialize, Default)]
struct PsResp {
    #[serde(default)]
    models: Vec<PsModel>,
}

/// Read NVML VRAM (own handle). `None` → sentinel; never blocks on a missing driver.
fn read_vram(nvml: Option<&Nvml>) -> Vram {
    let read = || -> Option<(i64, i64)> {
        let dev = nvml?.device_by_index(0).ok()?;
        let m = dev.memory_info().ok()?;
        let mib = |b: u64| (b / (1024 * 1024)) as i64;
        Some((mib(m.used), mib(m.total)))
    };
    match read() {
        Some((used, total)) => Vram { used_mib: used, total_mib: total },
        None => Vram { used_mib: UNK, total_mib: UNK },
    }
}

/// Read NVML GPU utilization % (own handle). `None` → unknown, which disables the util gate.
fn read_gpu_util(nvml: Option<&Nvml>) -> Option<u32> {
    let dev = nvml?.device_by_index(0).ok()?;
    dev.utilization_rates().ok().map(|u| u.gpu)
}

/// GPU work intensity in `0.0..=1.0`, or `0.0` when the GPU isn't doing *real* work. Real work =
/// actively computing (`util ≥ GPU_UTIL_MIN`) AND a workload is resident (VRAM ≥ GPU_VRAM_MIN_FRAC
/// of total, OR an Ollama model is loaded). The AND keeps the shader wallpaper — high-ish util,
/// negligible VRAM, no model — reading as idle (the glyph must not light up for the wallpaper).
fn gpu_work_level(util: Option<u32>, vram: &Vram, resident: bool) -> f64 {
    let u = match util {
        Some(u) if u >= GPU_UTIL_MIN => u,
        _ => return 0.0,
    };
    let heavy_vram =
        vram.total_mib > 0 && (vram.used_mib as f64 / vram.total_mib as f64) >= GPU_VRAM_MIN_FRAC;
    if heavy_vram || resident {
        (u as f64 / 100.0).clamp(0.0, 1.0)
    } else {
        0.0
    }
}

/// Resident Ollama models → residency rows, with `loaded_secs` from a producer-local
/// first-seen clock (`/api/ps` reports no load time). Unreachable Ollama → empty (em-dash).
fn read_residency(
    http: &reqwest::blocking::Client,
    first_seen: &mut HashMap<String, Instant>,
    now: Instant,
) -> Vec<Residency> {
    let names: Vec<String> = http
        .get(OLLAMA_PS)
        .send()
        .and_then(|r| r.json::<PsResp>())
        .map(|ps| ps.models.into_iter().map(|m| m.name).filter(|n| !n.is_empty()).collect())
        .unwrap_or_default();

    // Forget models that have unloaded so a reload reports a fresh duration.
    first_seen.retain(|name, _| names.contains(name));

    names
        .into_iter()
        .map(|name| {
            let since = *first_seen.entry(name.clone()).or_insert(now);
            Residency { name, loaded_secs: now.duration_since(since).as_secs() as i64 }
        })
        .collect()
}

/// PID → a human label for the workload. `/proc/<pid>/comm` is just "python" for ComfyUI, so we
/// peek at the cmdline to recognize the heavy workloads the keyhole exists to surface — ComfyUI
/// (the dreaming backend) and Ollama — and fall back to `comm` for everything else. Read-only,
/// fail-soft: an unreadable `/proc` entry degrades to `pid N`, never panics.
fn process_label(pid: u32) -> String {
    if let Ok(cmd) = fs::read_to_string(format!("/proc/{pid}/cmdline")) {
        let lower = cmd.to_ascii_lowercase();
        if lower.contains("comfyui") {
            return "ComfyUI".to_string();
        }
        if lower.contains("ollama") {
            return "Ollama".to_string();
        }
    }
    fs::read_to_string(format!("/proc/{pid}/comm"))
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| format!("pid {pid}"))
}

/// Pure selection: pick the largest-VRAM process and surface it ONLY when it holds a heavy share
/// (≥ `WORKLOAD_MIN_FRAC` of total) — so an idle-resident holder stays hidden and the row appears
/// only under a real workload. Empty/unknown total or no heavy holder → the honest-empty default.
fn choose_workload(mut named: Vec<(String, i64)>, total_mib: i64) -> Workload {
    if total_mib <= 0 {
        return Workload::default();
    }
    named.sort_by_key(|(_, mib)| std::cmp::Reverse(*mib));
    match named.into_iter().next() {
        Some((name, mib))
            if !name.is_empty() && (mib as f64) >= WORKLOAD_MIN_FRAC * total_mib as f64 =>
        {
            Workload { name, used_mib: mib }
        }
        _ => Workload::default(),
    }
}

/// Read NVML's running-compute-process table → the dominant heavy GPU holder. `None`/empty table →
/// honest-empty (no fabricated workload). This is the schema-3 attribution: it sees ComfyUI and any
/// other CUDA workload that holds no agentosd lease and is no Ollama model.
fn read_workload(nvml: Option<&Nvml>, total_mib: i64) -> Workload {
    let collect = || -> Option<Vec<(String, i64)>> {
        let dev = nvml?.device_by_index(0).ok()?;
        let named = dev
            .running_compute_processes()
            .ok()?
            .into_iter()
            .filter_map(|p| match p.used_gpu_memory {
                UsedGpuMemory::Used(b) => Some((process_label(p.pid), (b / (1024 * 1024)) as i64)),
                UsedGpuMemory::Unavailable => None,
            })
            .collect();
        Some(named)
    };
    choose_workload(collect().unwrap_or_default(), total_mib)
}

/// Optional lease mirror (`$XDG_RUNTIME_DIR/nimbus-aurora/lease.json`), pushed off-lock by the
/// `lease` daemon (integration TODO; ADR-0012 §3). Absent/unparseable → empty (no contention).
fn read_lease(path: &Path) -> Lease {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str::<Lease>(&s).ok())
        .unwrap_or_default()
}

/// schema 2 (ADR-0019 §6): the OPTIONAL lucid pending-queue mirror
/// (`$XDG_RUNTIME_DIR/nimbus-aurora/pending.json`), written atomically by the lucid drainer (the
/// promoted `lucid_jobs.py`, v1 unbuilt). Absent/unparseable → `{held:0,needs_review:0}` — the
/// calm fail-open posture (no sidecar == nothing waiting, NOT a fabricated backlog, NOT UNKNOWN).
///
/// CROSS-PRODUCER NOTE (G1 contract pin): the keyhole only DISPLAYS these counts; it must never be
/// the thing that drives the warm bloom. The lucid producer owns BOTH writes and they must agree:
/// the `needs_review` count it puts in `pending.json` is the SAME set whose presence it signals to
/// `feed.rs` via its own sidecar (G1). Two files, one source of truth in lucid — the keyhole is a
/// pure read mirror so a render can never race or double-count the warmth (`feed.rs:86` reads
/// `needs_you` as a bare origin-less count via single-writer `os.replace`, so a second writer here
/// would corrupt it; the keyhole deliberately does NOT write `needs_you.json`).
fn read_pending(path: &Path) -> Pending {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str::<Pending>(&s).ok())
        .map(|p| Pending { held: p.held.max(0), needs_review: p.needs_review.max(0) })
        .unwrap_or_default()
}

// ---------------------------------------------------------------------------
// The pure assembly — fleet/gateway/needs_you → {state, gateway, floats, fleet}.
// ---------------------------------------------------------------------------

/// Derive the legible `state`/`gateway`/`floats`/`fleet` block. Mirrors the wallpaper's mood
/// grammar via `derive_feed`, then layers the keyhole-only honesty: a gateway we cannot read is
/// `unknown` (NOT idle), and an unreadable kanban shows `-1` counts (NOT a fabricated `0`).
fn derive_legible(
    fleet_res: &rusqlite::Result<FleetCounts>,
    gw: Option<&crate::feed::GatewayInfo>,
    needs_you: u32,
    gpu_work: f64,
) -> (String, String, Floats, Fleet) {
    let gateway = gw.map(|g| g.gateway_state.clone()).unwrap_or_else(|| "unknown".into());
    let gateway_alive = matches!(gateway.as_str(), "running" | "starting" | "degraded");

    let fleet = match fleet_res {
        Ok(f) => Fleet { running: f.running as i64, queued: f.pending as i64, snagged: f.snagged as i64 },
        Err(_) => Fleet { running: UNK, queued: UNK, snagged: UNK },
    };

    let (state, floats) = if gw.is_none() {
        // Can't reach Hermes → honest UNKNOWN, not the reassuring null `feed` would emit.
        ("unknown".to_string(), Floats { busy: 0.0, warm: 0.0, snag: 0.0 })
    } else if !gateway_alive {
        // Reachable but stopped/other — nothing is running; the `gateway` field carries the
        // truth for the consumer to render distinctly later.
        ("idle".to_string(), Floats { busy: 0.0, warm: 0.0, snag: 0.0 })
    } else {
        // Alive: same grammar as the wallpaper (needs_you > snag > working > idle).
        let counts = fleet_res.as_ref().ok().cloned().unwrap_or_default();
        // ADR-0019 build-unblock: derive_feed gained a 4th `lucid_review` arg. The keyhole doesn't
        // read lucid data, so pass 0 (mood unchanged); plumb a real count here if the tray instrument
        // should reflect lucid review too. (Left for the ADR-0019 author to decide.)
        let af = derive_feed(&counts, gw, needs_you, 0);
        (state_word(af.state).to_string(), Floats { busy: af.busy, warm: af.warm, snag: af.snag })
    };

    // GPU override (ADR-0012): a reachable-but-idle fleet still reads `working` when the GPU is
    // genuinely busy with a non-Hermes workload (e.g. ComfyUI). It NEVER overrides `unknown`
    // (an unreachable Hermes stays honestly unknown) nor a higher live signal already in play
    // (needs_you / snag / a Hermes `working`) — it only lifts the calm idle null.
    let (state, floats) = if state == "idle" && gpu_work > 0.0 {
        ("working".to_string(), Floats { busy: gpu_work.clamp(0.0, 1.0), warm: 0.0, snag: 0.0 })
    } else {
        (state, floats)
    };

    (state, gateway, floats, fleet)
}

// ---------------------------------------------------------------------------
// Cadence + atomic write + the loop.
// ---------------------------------------------------------------------------

/// Adaptive cadence (ADR-0012 §data): 2s at rest; back off when the GPU is busy or NVML is
/// slow, so the observer never competes with inference. Sync loop → reads never overlap
/// (skip-on-stale for free).
fn next_interval(state: &str, nvml_read: Duration) -> Duration {
    if nvml_read > Duration::from_millis(250) {
        Duration::from_secs(10) // a contended/slow GPU — observe sparingly
    } else if matches!(state, "working" | "acting") {
        Duration::from_secs(5) // under load
    } else {
        Duration::from_secs(2) // idle cadence
    }
}

/// Atomic write: dot-prefixed temp (a `*.json` poller skips it) then rename — the tray never
/// reads a half-written file. Mirrors `feed::write_feed`.
fn write_keyhole(dir: &Path, feed: &KeyholeFeed) -> std::io::Result<()> {
    let mut json = serde_json::to_string(feed).expect("KeyholeFeed serializes");
    json.push('\n');
    let tmp = dir.join(format!(".keyhole.{}.tmp", std::process::id()));
    fs::write(&tmp, json)?;
    fs::rename(&tmp, dir.join("keyhole.json"))
}

/// Poll the substrate's read-only sources and emit `keyhole.json`. Edge-driven: rewrites only
/// when the feed changes. `--once` emits a single snapshot and exits (verify/CI). Degrades to
/// honest UNKNOWN — never wedges the desktop, never blocks inference.
pub fn run(once: bool) -> Result<(), Box<dyn std::error::Error>> {
    let dir = feed_dir()?;
    let out = dir.join("keyhole.json");
    let kanban = hermes_path("kanban.db");
    let gateway = hermes_path("gateway_state.json");
    let needs_you_path = hermes_path("needs_you.json");
    let lease_mirror = dir.join("lease.json");
    let pending_mirror = dir.join("pending.json");
    println!("agentosd keyhole → {}", out.display());

    // Own NVML handle (like `monitor`), never the lease daemon's. None → VRAM degrades to
    // unknown but the rest of the instrument still works (fail-open, ADR-0003).
    let nvml = Nvml::init().ok();
    if nvml.is_none() {
        eprintln!("agentosd keyhole: NVML unavailable — VRAM will read unknown (continuing)");
    }
    let http = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()?;

    let mut first_seen: HashMap<String, Instant> = HashMap::new();
    let mut last: Option<KeyholeFeed> = None;

    loop {
        let now = Instant::now();
        let fleet_res = read_fleet(&kanban);
        let gw = read_gateway(&gateway);
        let needs_you = read_needs_you(&needs_you_path);
        let t_nvml = Instant::now();
        let vram = read_vram(nvml.as_ref());
        let gpu_util = read_gpu_util(nvml.as_ref());
        let nvml_read = t_nvml.elapsed();

        let residency = read_residency(&http, &mut first_seen, now);
        let workload = read_workload(nvml.as_ref(), vram.total_mib);
        let lease = read_lease(&lease_mirror);
        let pending_requests = read_pending(&pending_mirror);

        // Promote an idle fleet to `working` when the GPU is genuinely busy with a real workload
        // (not the shader wallpaper) — gated on util AND (heavy VRAM OR a resident model).
        let gpu_work = gpu_work_level(gpu_util, &vram, !residency.is_empty());
        let (state, gateway_word, floats, fleet) =
            derive_legible(&fleet_res, gw.as_ref(), needs_you, gpu_work);

        let feed = KeyholeFeed {
            schema: SCHEMA,
            state: state.clone(),
            gateway: gateway_word,
            floats,
            fleet,
            lease,
            vram,
            residency,
            workload,             // schema 3: dominant GPU compute process (NVML); empty == none.
            tokens_per_sec: None, // P2: summed from the ADR-0002 proxy stream. Never faked.
            pending_requests,     // schema 2: lucid queue mirror; {0,0} until the sidecar exists.
        };

        let changed = last.as_ref() != Some(&feed);
        if changed || once {
            match write_keyhole(&dir, &feed) {
                Ok(()) if changed => println!(
                    "[{}] {} (gw {}, run {}, vram {}/{}M, models {}, work {}, lease {})",
                    crate::now_hms(),
                    feed.state,
                    feed.gateway,
                    feed.fleet.running,
                    feed.vram.used_mib,
                    feed.vram.total_mib,
                    feed.residency.len(),
                    if feed.workload.name.is_empty() { "-" } else { &feed.workload.name },
                    if feed.lease.tier.is_empty() { "-" } else { &feed.lease.tier },
                ),
                Ok(()) => {}
                Err(e) => eprintln!("agentosd keyhole: write failed: {e}"),
            }
            last = Some(feed);
        }

        if once {
            break;
        }
        thread::sleep(next_interval(&state, nvml_read));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::feed::GatewayInfo;

    fn gw(state: &str, active: u32) -> GatewayInfo {
        GatewayInfo { gateway_state: state.into(), active_agents: active }
    }
    fn counts(running: u32, snagged: u32, pending: u32) -> FleetCounts {
        FleetCounts { running, snagged, pending, total: running + snagged + pending }
    }

    #[test]
    fn unreachable_gateway_is_unknown_not_idle() {
        // The headline honesty fix: no gateway file → UNKNOWN, never the reassuring null.
        let (state, gateway, floats, _) = derive_legible(&Ok(counts(0, 0, 0)), None, 0, 0.0);
        assert_eq!(state, "unknown");
        assert_eq!(gateway, "unknown");
        assert_eq!(floats, Floats { busy: 0.0, warm: 0.0, snag: 0.0 });
    }

    #[test]
    fn unreadable_kanban_shows_sentinel_counts_not_zero() {
        // A real 0 and "couldn't read" must be distinguishable downstream.
        let err: rusqlite::Result<FleetCounts> = Err(rusqlite::Error::InvalidQuery);
        let (_, _, _, fleet) = derive_legible(&err, Some(&gw("running", 0)), 0, 0.0);
        assert_eq!(fleet, Fleet { running: UNK, queued: UNK, snagged: UNK });
    }

    #[test]
    fn alive_gateway_mirrors_the_wallpaper_grammar() {
        // working: a running task reads as working with the same ramp the shader gets.
        let (state, gateway, floats, fleet) =
            derive_legible(&Ok(counts(1, 0, 2)), Some(&gw("running", 0)), 0, 0.0);
        assert_eq!(state, "working");
        assert_eq!(gateway, "running");
        assert_eq!(floats.busy, 0.7);
        assert_eq!(fleet, Fleet { running: 1, queued: 2, snagged: 0 });

        // needs_you outranks, and is the one warm signal.
        let (state, _, floats, _) = derive_legible(&Ok(counts(3, 1, 0)), Some(&gw("running", 0)), 1, 0.0);
        assert_eq!(state, "needs_you");
        assert!(floats.warm > 0.0 && floats.busy == 0.0 && floats.snag == 0.0);

        // snag reads calm (cool), never as working.
        let (state, _, floats, _) = derive_legible(&Ok(counts(0, 1, 0)), Some(&gw("running", 0)), 0, 0.0);
        assert_eq!(state, "snag");
        assert!(floats.snag > 0.0);
    }

    #[test]
    fn stopped_gateway_is_idle_with_truthful_gateway_field() {
        let (state, gateway, floats, _) =
            derive_legible(&Ok(counts(0, 0, 0)), Some(&gw("stopped", 0)), 0, 0.0);
        assert_eq!(state, "idle");
        assert_eq!(gateway, "stopped"); // the consumer can render "off" from this later
        assert_eq!(floats, Floats { busy: 0.0, warm: 0.0, snag: 0.0 });
    }

    #[test]
    fn gpu_work_promotes_idle_to_working_but_shader_stays_idle() {
        // Idle Hermes fleet + a genuinely busy GPU (e.g. ComfyUI) → the glyph reads `working`.
        let (state, _, floats, _) =
            derive_legible(&Ok(counts(0, 0, 0)), Some(&gw("running", 0)), 0, 1.0);
        assert_eq!(state, "working");
        assert_eq!(floats.busy, 1.0);

        // The shader wallpaper: high util but negligible VRAM and no resident model → level 0.0,
        // so the fleet stays idle (the glyph must NOT light up for nimbus aurora).
        let shader = Vram { used_mib: 400, total_mib: 24564 };
        assert_eq!(gpu_work_level(Some(95), &shader, false), 0.0);
        let (state, _, _, _) = derive_legible(&Ok(counts(0, 0, 0)), Some(&gw("running", 0)), 0, 0.0);
        assert_eq!(state, "idle");

        // A real workload trips the gate: heavy VRAM, or a resident Ollama model at smaller VRAM.
        let heavy = Vram { used_mib: 16800, total_mib: 24564 };
        assert!(gpu_work_level(Some(100), &heavy, false) > 0.0);
        let small = Vram { used_mib: 3000, total_mib: 24564 };
        assert!(gpu_work_level(Some(80), &small, true) > 0.0);
        // Below the util floor → idle regardless of VRAM/residency.
        assert_eq!(gpu_work_level(Some(10), &heavy, true), 0.0);

        // GPU work never masks an unreachable Hermes (stays honestly unknown).
        let (state, _, _, _) = derive_legible(&Ok(counts(0, 0, 0)), None, 0, 1.0);
        assert_eq!(state, "unknown");
    }

    #[test]
    fn empty_lease_mirror_parses_to_no_contention() {
        // Absent/blank lease.json → empty strings → the UI shows "no contention", never a guess.
        assert_eq!(serde_json::from_str::<Lease>("{}").unwrap(), Lease::default());
        assert_eq!(
            serde_json::from_str::<Lease>(r#"{"tier":"interactive","holder":"Hermes","preempt":""}"#).unwrap(),
            Lease { tier: "interactive".into(), holder: "Hermes".into(), preempt: String::new(), ..Default::default() }
        );
    }

    #[test]
    fn workload_surfaces_only_the_dominant_heavy_holder() {
        // The headline case: ComfyUI dreaming at 21GB on a 24GB card → named, with its VRAM.
        let total = 24564;
        assert_eq!(
            choose_workload(vec![("ComfyUI".into(), 21000), ("code".into(), 540)], total),
            Workload { name: "ComfyUI".into(), used_mib: 21000 }
        );
        // Idle-resident ComfyUI (a few hundred MB) is below the heavy floor → hidden (no row).
        assert_eq!(choose_workload(vec![("ComfyUI".into(), 530)], total), Workload::default());
        // The largest holder wins even when several processes are present.
        assert_eq!(
            choose_workload(vec![("blender".into(), 6000), ("ComfyUI".into(), 9000)], total),
            Workload { name: "ComfyUI".into(), used_mib: 9000 }
        );
        // No processes, unknown total, or a blank name all collapse to the honest-empty default.
        assert_eq!(choose_workload(vec![], total), Workload::default());
        assert_eq!(choose_workload(vec![("ComfyUI".into(), 21000)], -1), Workload::default());
        assert_eq!(choose_workload(vec![(String::new(), 21000)], total), Workload::default());
    }

    #[test]
    fn pins_the_exact_contract() {
        // The producer/consumer pin (ADR-0012 §2), mirroring feed.rs's exact-string test. If
        // this string changes, bump SCHEMA and update KeyholeModel.qml in lockstep.
        // schema 2 (ADR-0019 §6): `pending_requests` appended additively after `tokens_per_sec`.
        // schema 3 (workload attribution): `workload` inserted after `residency`.
        let feed = KeyholeFeed {
            schema: 3,
            state: "working".into(),
            gateway: "running".into(),
            floats: Floats { busy: 0.85, warm: 0.0, snag: 0.0 },
            fleet: Fleet { running: 3, queued: 2, snagged: 0 },
            lease: Lease {
                tier: "interactive".into(),
                holder: "Hermes".into(),
                preempt: "wallpaper yielded ~1.5GB -> qwen2.5 loaded".into(),
                ..Default::default()
            },
            vram: Vram { used_mib: 6240, total_mib: 8192 },
            residency: vec![Residency { name: "qwen2.5:14b".into(), loaded_secs: 240 }],
            workload: Workload { name: "ComfyUI".into(), used_mib: 21000 },
            tokens_per_sec: None,
            pending_requests: Pending { held: 2, needs_review: 1 },
        };
        assert_eq!(
            serde_json::to_string(&feed).unwrap(),
            r#"{"schema":3,"state":"working","gateway":"running","floats":{"busy":0.85,"warm":0.0,"snag":0.0},"fleet":{"running":3,"queued":2,"snagged":0},"lease":{"tier":"interactive","holder":"Hermes","preempt":"wallpaper yielded ~1.5GB -> qwen2.5 loaded"},"vram":{"used_mib":6240,"total_mib":8192},"residency":[{"name":"qwen2.5:14b","loaded_secs":240}],"workload":{"name":"ComfyUI","used_mib":21000},"tokens_per_sec":null,"pending_requests":{"held":2,"needs_review":1}}"#
        );
    }

    #[test]
    fn pending_defaults_to_empty_not_unknown() {
        // schema 2 (ADR-0019 §6): an empty queue is a REAL datum — `{held:0,needs_review:0}`,
        // serialized (never absent), never the `-1`/UNKNOWN sentinel the fleet/vram fields use.
        // A missing/unreadable lucid sidecar must read as "nothing waiting" (calm, fail-open),
        // never as a fabricated backlog and never as UNKNOWN.
        assert_eq!(Pending::default(), Pending { held: 0, needs_review: 0 });
        assert_eq!(
            serde_json::to_string(&Pending::default()).unwrap(),
            r#"{"held":0,"needs_review":0}"#
        );
        // Absent sidecar / blank / garbage all collapse to the same empty datum.
        assert_eq!(read_pending(Path::new("/nonexistent/pending.json")), Pending::default());
        // The two counts are independent (the held(deferred) vs needs-review split is structural).
        assert_eq!(
            serde_json::from_str::<Pending>(r#"{"held":3,"needs_review":0}"#).unwrap(),
            Pending { held: 3, needs_review: 0 }
        );
        // A schema-1-era file (no pending fields) round-trips to empty via serde defaults — a v2
        // reader tolerates a v1 sidecar.
        assert_eq!(serde_json::from_str::<Pending>("{}").unwrap(), Pending::default());
        // Negative junk is clamped to 0 (the count is a cardinality; -1 is reserved for UNKNOWN
        // numerics elsewhere and must never leak into a pending count).
        let tmp = std::env::temp_dir().join(format!("agentosd_pending_test_{}.json", std::process::id()));
        std::fs::write(&tmp, r#"{"held":-5,"needs_review":2}"#).unwrap();
        assert_eq!(read_pending(&tmp), Pending { held: 0, needs_review: 2 });
        let _ = std::fs::remove_file(&tmp);
    }

    #[test]
    fn unknown_numerics_serialize_as_negative_sentinels_and_null_tokens() {
        let feed = KeyholeFeed {
            schema: SCHEMA,
            state: "unknown".into(),
            gateway: "unknown".into(),
            floats: Floats { busy: 0.0, warm: 0.0, snag: 0.0 },
            fleet: Fleet { running: UNK, queued: UNK, snagged: UNK },
            lease: Lease::default(),
            vram: Vram { used_mib: UNK, total_mib: UNK },
            residency: vec![],
            workload: Workload::default(),
            tokens_per_sec: None,
            pending_requests: Pending::default(),
        };
        let s = serde_json::to_string(&feed).unwrap();
        assert!(s.contains(r#""fleet":{"running":-1,"queued":-1,"snagged":-1}"#));
        assert!(s.contains(r#""vram":{"used_mib":-1,"total_mib":-1}"#));
        assert!(s.contains(r#""tokens_per_sec":null"#));
        assert!(s.contains(r#""residency":[]"#));
        // schema 3: the no-heavy-workload default is honest-empty — empty name + -1, NOT a guess.
        assert!(s.contains(r#""workload":{"name":"","used_mib":-1}"#));
        // schema 2: even in the all-UNKNOWN frame, pending stays an empty datum, NOT -1.
        // (An unreachable Hermes says nothing about a LOCAL lucid queue — the counts are
        // independent producers; the keyhole must not let one UNKNOWN smear into the other.)
        assert!(s.contains(r#""pending_requests":{"held":0,"needs_review":0}"#));
    }
}

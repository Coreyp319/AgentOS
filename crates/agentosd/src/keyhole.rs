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
//! Output contract — schema 5, consumed by `{integrations,spikes}/keyhole/.../KeyholeModel.qml`:
//!   {"schema":5,"state":…,"gateway":…,"floats":{busy,warm,snag},
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
//!    "pending_requests":{held,needs_review},     // schema 2 (ADR-0019 §6): lucid queue mirror.
//!                                                // 0 == empty (a REAL datum, never -1/UNKNOWN);
//!                                                // `held` is calm weather (count only, NEVER warm),
//!                                                // `needs_review` is the warm-bloom cohort — but
//!                                                // the warmth is the lucid→feed.rs sidecar's job
//!                                                // (G1), NOT written here (read-only mirror).
//!    "queue":{depth,next_tier},                  // schema 4 (ADR-0041): live arbiter wait-queue.
//!    "gpu_util_pct":N,                           // schema 5: NVML GPU util %. -1 == unreadable
//!                                                // (a real 0 == idle GPU — distinct, ADR-0012 §4).
//!    "check_ins":[{id,title,assignee,status,…}], // schema 5 (ADR-0051): per-task Hermes cards,
//!                                                // read READ-ONLY from kanban.db. [] == none OR
//!                                                // can't-read (the consumer splits the two on the
//!                                                // gateway/state "unknown"). RAW status — the view
//!                                                // derives the mood (no UI vocab in the contract).
//!    "check_ins_total":N,                        // pre-cap count (honest "16 of 23"); -1 unknown.
//!    "recurring":[{id,name,schedule,…}]}         // schema 5 (ADR-0051): Hermes cron jobs from
//!                                                // ~/.hermes/cron/jobs.json. [] == none / can't-read.

use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::gpu::{GpuBackend, ProcClass};
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};

use crate::feed::{
    derive_feed, feed_dir, hermes_path, read_fleet, read_gateway, read_needs_you, state_word,
    FleetCounts,
};

/// Bump on any breaking shape change to `keyhole.json`. Pinned by `pins_the_exact_contract`.
/// schema 2 (ADR-0019 §6): adds the additive `pending_requests` block.
/// schema 3 (ADR-0012 workload attribution): adds the additive `workload` block — the dominant
/// GPU compute process by VRAM (from NVML), the slice neither Ollama-residency nor the lease
/// daemon can see.
/// schema 4 (ADR-0041): adds the additive `queue` block (live arbiter wait-queue).
/// schema 5 (ADR-0051): adds the additive `gpu_util_pct` scalar + the `check_ins`/`check_ins_total`
/// per-task cards (read-only from kanban.db) + `recurring` (Hermes cron). Each addition is purely
/// additive; an older consumer ignores the unknown field and the serializer always emits an
/// honest-empty default ([], -1, ""), never absent.
/// schema 6 (ADR-0051 amendment): adds the additive `written_at` producer heartbeat (epoch secs) —
/// without it a CRASHED producer leaves a readable last file and the consumer keeps asserting the
/// stale "N LIVE" forever (edge-dedup makes mtime useless for this). The stamp refreshes at least
/// every `HEARTBEAT_SECS` even when the payload dedups.
const SCHEMA: u32 = 6;

/// schema 6: rewrite the (otherwise edge-deduped) feed at least this often, purely to refresh
/// `written_at`. Consumers treat a stamp older than ~3× this as a dead producer → honest UNKNOWN
/// (KeyholeModel.qml uses 90s). Cheap: one small tmpfs write every 30s at worst.
const HEARTBEAT_SECS: u64 = 30;

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

/// schema 5 (ADR-0051): cap the Check-ins card list so the hot `keyhole.json` stays small and the
/// tray reflow stays bounded. The pre-cap total rides in `check_ins_total` for honest truncation.
const CHECK_IN_CAP: usize = 16;
/// A `done` task stays in the Check-ins DONE column only this long after `completed_at`, then ages
/// out — so the card list is "what is live + just finished," not the whole archive.
const DONE_WINDOW_SECS: i64 = 6 * 3600;
/// Truncate a task's `last_failure_error` to this many chars (+ "…") so one giant traceback can't
/// bloat the feed; the view only needs the gist for the card's status line / blurt.
const ERROR_MAX: usize = 200;
/// Cap the recurring/cron list (defensive — Hermes cron is tiny today, but never unbounded).
const RECUR_CAP: usize = 12;

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
    /// schema 4 (ADR-0041): the LIVE cross-workflow VRAM-demand queue (arbiter `queue.json`) — who is
    /// waiting their turn at the lease right now + the tier served next. Always present (default
    /// `{0,""}`), never `null`. Distinct from `pending_requests` (the durable deferral buffer).
    pub queue: Queue,
    /// schema 5 (ADR-0051): live NVML GPU utilization %. -1 == unreadable (distinct from a real 0%
    /// idle GPU, ADR-0012 §4). Surfaces the value `read_gpu_util` already computes for the work-gate
    /// so the Check-ins metrics rail shows a real number instead of inferring one from `busy`.
    pub gpu_util_pct: i64,
    /// schema 5 (ADR-0051): the Check-ins cards — per-task rows of Hermes' `tasks` table, read
    /// READ-ONLY, ordered active-first and capped at `CHECK_IN_CAP`. `[]` == no active tasks OR an
    /// unreadable kanban; the consumer distinguishes the two via `gateway`/`state` == `"unknown"`
    /// (no separate flag). Cards carry the RAW kanban `status` — the view derives the creature mood
    /// (calm/working/stalled/needsyou/done), so UI vocabulary never leaks into the contract.
    pub check_ins: Vec<CheckIn>,
    /// schema 5: count matching the inclusion filter BEFORE the cap, so the UI can say "16 of 23"
    /// honestly instead of silently dropping cards. -1 == kanban unreadable (distinct from 0 rows).
    pub check_ins_total: i64,
    /// schema 5 (ADR-0051): Hermes recurring/cron jobs from `~/.hermes/cron/jobs.json` (the cadence
    /// the `tasks` table does not carry). `[]` == none / unreadable — calm fail-open, never faked.
    pub recurring: Vec<Recurring>,
    /// schema 6: producer-liveness heartbeat (epoch secs), stamped at each actual write and
    /// refreshed at least every `HEARTBEAT_SECS`. Held at `0` while the frame is BUILT so the
    /// edge-dedup compares payloads, not clocks; the loop stamps it just before writing.
    pub written_at: i64,
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

/// schema 4 (ADR-0041): the LIVE cross-workflow VRAM-demand queue — workflows blocked on
/// `WaitTurn` for their turn at the lease RIGHT NOW. DISTINCT from `pending_requests`: that is the
/// durable defer-don't-deny buffer (intents to run later); this is who is actively in line for the GPU
/// this moment. Read from the arbiter's `queue.json` mirror (`agentosd queue`, org.agentos.Queue1);
/// absent → `{0,""}`. CALM weather like `held` — count only, never warm; an empty queue collapses the
/// row. `next_tier` is the tier served next (highest waiting) — an aggregate, NEVER a waiter's identity
/// (the arbiter's no-leak contract). `depth` clamped ≥ 0; a missing mirror reads as nothing-waiting.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct Queue {
    #[serde(default)]
    pub depth: i64,
    #[serde(default)]
    pub next_tier: String,
}

/// schema 5 (ADR-0051): one Check-ins card — a per-task row of Hermes' `tasks` table, read
/// READ-ONLY. The contract ships the **raw** kanban `status` + `consecutive_failures`; the view
/// derives the creature mood (calm/working/stalled/needsyou/done), so UI vocabulary never leaks
/// into the producer (single source of truth for mood = the QML model). Timestamps are epoch
/// SECONDS (Hermes writes `int(time.time())`); `-1` == that stamp is unset — a queued task has no
/// heartbeat, distinct from a real `0`. Strings: `""` == none / unknown. Every field is always
/// serialized (the `Vram`/`Fleet` always-emit convention) so the pin-test stays deterministic and
/// the QML binding needs no `undefined` juggling.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CheckIn {
    pub id: String,
    pub title: String,
    /// The agent on the task ("" == unassigned/unknown).
    pub assignee: String,
    /// RAW kanban status (`running`/`blocked`/`review`/`ready`/`todo`/`scheduled`/`triage`/`done`).
    pub status: String,
    pub priority: i64,
    pub consecutive_failures: i64,
    pub created_at: i64,
    /// -1 == not started.
    pub started_at: i64,
    /// -1 == no heartbeat yet (the view's "last check-in" = first of [heartbeat, started, created]).
    pub last_heartbeat_at: i64,
    /// `current_step_key` — a cheap progress hint with no `task_runs` join. "" == none.
    pub step: String,
    /// `last_failure_error`, truncated to `ERROR_MAX`. "" == none.
    pub last_error: String,
}

/// schema 5 (ADR-0051): one recurring/cron job from `~/.hermes/cron/jobs.json` (the cadence the
/// `tasks` table doesn't carry). A thin honest passthrough; timestamps are Hermes' ISO-8601 strings
/// (NOT epoch — the view relative-formats them). "" == none / not-yet-run. Read-only mirror.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Recurring {
    pub id: String,
    pub name: String,
    /// `schedule_display` — the human cron expr, e.g. "0 4 * * *".
    pub schedule: String,
    pub enabled: bool,
    /// "scheduled" | "paused" | ….
    pub state: String,
    /// `next_run_at` ISO-8601; "" == none.
    pub next_run: String,
    /// `last_run_at` ISO-8601; "" == never run.
    pub last_run: String,
    /// "ok" | "error" | "" — the cheap "did it snag" signal.
    pub last_status: String,
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
fn read_vram(gpu: &GpuBackend) -> Vram {
    match gpu.mem() {
        Some(m) => Vram { used_mib: m.used as i64, total_mib: m.total as i64 },
        None => Vram { used_mib: UNK, total_mib: UNK },
    }
}

/// Read GPU utilization % (own backend). `None` → unknown, which disables the util gate.
fn read_gpu_util(gpu: &GpuBackend) -> Option<u32> {
    gpu.meta().util_pct
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
fn read_workload(gpu: &GpuBackend, total_mib: i64) -> Workload {
    // Compute holders (and AMD's unclassified `Unknown`, matching the telemetry/monitor routing) —
    // the heavy GPU workload (ComfyUI/CUDA), never the graphics baseline. On AMD the holder comes
    // from /proc fdinfo as `Unknown` (no gfx/compute split — ADR-0048 Phase 3), so it shows here too.
    let named: Vec<(String, i64)> = gpu
        .processes()
        .unwrap_or_default()
        .into_iter()
        .filter(|p| matches!(p.class, ProcClass::Compute | ProcClass::Unknown))
        .map(|p| (process_label(p.pid), p.mib as i64))
        .collect();
    choose_workload(named, total_mib)
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

/// schema 4 (ADR-0041): the OPTIONAL arbiter live-queue mirror
/// (`$XDG_RUNTIME_DIR/nimbus-aurora/queue.json`), written atomically by `agentosd queue`. Absent/
/// unparseable → `{depth:0,next_tier:""}` — the calm fail-open posture (no arbiter == nothing waiting,
/// never a fabricated backlog, never UNKNOWN). `depth` clamped ≥ 0. Pure read mirror, like `read_pending`.
fn read_queue(path: &Path) -> Queue {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str::<Queue>(&s).ok())
        .map(|q| Queue { depth: q.depth.max(0), next_tier: q.next_tier })
        .unwrap_or_default()
}

/// schema 5: map the NVML util read to the wire sentinel — `Some(u) → u`, `None → -1` (UNKNOWN),
/// keeping a real `0%` idle distinct from "couldn't read the GPU" (ADR-0012 §4).
fn util_field(util: Option<u32>) -> i64 {
    util.map(|u| u as i64).unwrap_or(UNK)
}

/// `read_check_ins`' per-task SELECT. Active + recently-finished rows, ordered active-first; the
/// `COUNT(*) OVER ()` is the pre-cap total on every row (one read, no second query). `?1` = the
/// done-window cutoff (epoch secs), `?2` = the cap. Reads Hermes' internal `tasks` table directly
/// (no API boundary), so a schema drift is probed once at startup (`probe_check_in_columns`).
const CHECK_IN_SQL: &str = "\
SELECT \
  id, title, COALESCE(assignee,''), status, COALESCE(priority,0), \
  COALESCE(consecutive_failures,0), created_at, COALESCE(started_at,-1), \
  COALESCE(last_heartbeat_at,-1), COALESCE(current_step_key,''), \
  COALESCE(last_failure_error,''), COUNT(*) OVER () AS total \
FROM tasks \
WHERE status IN ('running','blocked','triage','todo','scheduled','ready','review') \
   OR (status = 'done' AND completed_at IS NOT NULL AND completed_at >= ?1) \
ORDER BY \
  CASE status WHEN 'running' THEN 0 WHEN 'blocked' THEN 1 WHEN 'review' THEN 2 \
              WHEN 'ready' THEN 3 WHEN 'todo' THEN 4 WHEN 'scheduled' THEN 5 \
              WHEN 'triage' THEN 6 WHEN 'done' THEN 7 ELSE 8 END, \
  (consecutive_failures > 0) DESC, priority DESC, \
  COALESCE(last_heartbeat_at, started_at, created_at) DESC \
LIMIT ?2";

/// schema 5 (ADR-0051): read the Check-ins cards READ-ONLY from `tasks`. Returns the capped cards +
/// the pre-cap total. Mirrors `read_fleet`'s open/busy_timeout; any error (DB absent/locked/drift)
/// bubbles to the caller, which folds it to `(vec![], -1)` — fail-open (ADR-0003). `now` is the
/// producer clock (epoch secs), bound as a param so the read is deterministic + testable. The view
/// derives mood/last-seen from the raw fields; the producer ships no UI vocabulary.
fn read_check_ins(db: &Path, now: i64, cap: usize) -> rusqlite::Result<(Vec<CheckIn>, i64)> {
    let conn = Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    conn.busy_timeout(Duration::from_millis(2000))?;
    let cutoff = now - DONE_WINDOW_SECS;
    let mut stmt = conn.prepare(CHECK_IN_SQL)?;
    let mut total = 0i64;
    let rows = stmt
        .query_map(rusqlite::params![cutoff, cap as i64], |r| {
            total = r.get::<_, i64>(11)?; // the window count — identical on every row
            let raw_err: String = r.get(10)?;
            let last_error = if raw_err.chars().count() > ERROR_MAX {
                let mut t: String = raw_err.chars().take(ERROR_MAX).collect();
                t.push('…');
                t
            } else {
                raw_err
            };
            Ok(CheckIn {
                id: r.get(0)?,
                title: r.get(1)?,
                assignee: r.get(2)?,
                status: r.get(3)?,
                priority: r.get(4)?,
                consecutive_failures: r.get(5)?,
                created_at: r.get(6)?,
                started_at: r.get(7)?,
                last_heartbeat_at: r.get(8)?,
                step: r.get(9)?,
                last_error,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    Ok((rows, total))
}

/// Columns `CHECK_IN_SQL` reads beyond the always-present id/title/status. A Hermes upgrade that
/// renamed/dropped one would make `read_check_ins` error → the caller folds to empty cards, which
/// would read as "no tasks" instead of a real break. Probed ONCE at startup and logged loudly so the
/// drift lands in the journal. Diagnostic only — the runtime path stays fail-open (ADR-0003).
const CHECK_IN_COLUMNS: [&str; 9] = [
    "assignee",
    "priority",
    "consecutive_failures",
    "created_at",
    "started_at",
    "completed_at",
    "last_heartbeat_at",
    "current_step_key",
    "last_failure_error",
];

/// One-shot check that `tasks` still exposes the `CHECK_IN_COLUMNS`. `Ok(())` == present OR the DB /
/// `tasks` table isn't there yet (Hermes simply not initialized — benign, like `feed.rs`'s `Absent`);
/// `Err(why)` == a real column drift to log loudly. Never panics, never blocks the loop.
fn probe_check_in_columns(db: &Path) -> Result<(), String> {
    let conn = match Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_ONLY) {
        Ok(c) => c,
        Err(_) => return Ok(()), // DB not present yet — benign, not drift.
    };
    let mut stmt = match conn.prepare("SELECT name FROM pragma_table_info('tasks')") {
        Ok(s) => s,
        Err(_) => return Ok(()),
    };
    let have: std::collections::HashSet<String> = match stmt.query_map([], |r| r.get::<_, String>(0)) {
        Ok(rows) => rows.flatten().collect(),
        Err(_) => return Ok(()),
    };
    if have.is_empty() {
        return Ok(()); // `tasks` table not found yet — benign.
    }
    let missing: Vec<&str> =
        CHECK_IN_COLUMNS.iter().copied().filter(|c| !have.contains(*c)).collect();
    if missing.is_empty() {
        Ok(())
    } else {
        Err(format!("`tasks` is missing {missing:?}"))
    }
}

/// `~/.hermes/cron/jobs.json` shape — only the fields the Check-ins RECURRING section needs.
#[derive(Deserialize, Default)]
struct CronFile {
    #[serde(default)]
    jobs: Vec<CronJob>,
}
#[derive(Deserialize, Default)]
struct CronJob {
    #[serde(default)]
    id: String,
    #[serde(default)]
    name: String,
    #[serde(default)]
    schedule_display: String,
    #[serde(default)]
    enabled: bool,
    #[serde(default)]
    state: String,
    #[serde(default)]
    next_run_at: Option<String>,
    #[serde(default)]
    last_run_at: Option<String>,
    #[serde(default)]
    last_status: Option<String>,
}

/// schema 5 (ADR-0051): read Hermes recurring/cron jobs from `~/.hermes/cron/jobs.json`. Absent/
/// unparseable → `[]` (calm fail-open, never a fabricated schedule). Capped at `cap`. Pure read
/// mirror — the keyhole never writes cron. ISO-8601 timestamps pass through verbatim (the view
/// relative-formats); a JSON `null` for a stamp/status collapses to `""`.
fn read_recurring(path: &Path, cap: usize) -> Vec<Recurring> {
    let file: CronFile =
        match fs::read_to_string(path).ok().and_then(|s| serde_json::from_str(&s).ok()) {
            Some(f) => f,
            None => return vec![],
        };
    file.jobs
        .into_iter()
        .take(cap)
        .map(|j| Recurring {
            id: j.id,
            name: j.name,
            schedule: j.schedule_display,
            enabled: j.enabled,
            state: j.state,
            next_run: j.next_run_at.unwrap_or_default(),
            last_run: j.last_run_at.unwrap_or_default(),
            last_status: j.last_status.unwrap_or_default(),
        })
        .collect()
}

/// Wall-clock seconds since the epoch, for the Check-ins done-window + the producer's read clock.
fn now_secs() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
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
    let queue_mirror = dir.join("queue.json");
    let cron_jobs = hermes_path("cron/jobs.json");
    println!("agentosd keyhole → {}", out.display());

    // schema 5 (ADR-0051): surface a Hermes `tasks` schema drift loudly once at startup — the
    // per-task Check-ins read goes straight into Hermes' internal table, so a renamed/dropped column
    // would otherwise read as "no tasks" (phantom-empty) rather than a real break. The runtime read
    // stays fail-open either way (ADR-0003).
    match probe_check_in_columns(&kanban) {
        Ok(()) => {}
        Err(why) => eprintln!(
            "agentosd keyhole: WARNING — Hermes kanban schema drift: {why}. Check-ins cards now \
             read empty (fail-open); update CHECK_IN_SQL/CHECK_IN_COLUMNS in keyhole.rs to match."
        ),
    }

    // Own GPU backend (like `monitor`), never the lease daemon's. Absent → VRAM degrades to
    // unknown but the rest of the instrument still works (fail-open, ADR-0003).
    let backend = GpuBackend::detect();
    if backend.is_absent() {
        eprintln!("agentosd keyhole: no GPU detected (NVML / AMD sysfs) — VRAM will read unknown (continuing)");
    }
    let http = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()?;

    let mut first_seen: HashMap<String, Instant> = HashMap::new();
    let mut last: Option<KeyholeFeed> = None;
    let mut last_write: Option<Instant> = None;

    loop {
        let now = Instant::now();
        let fleet_res = read_fleet(&kanban);
        let gw = read_gateway(&gateway);
        let needs_you = read_needs_you(&needs_you_path);
        let t_nvml = Instant::now();
        let vram = read_vram(&backend);
        let gpu_util = read_gpu_util(&backend);
        let nvml_read = t_nvml.elapsed();

        let residency = read_residency(&http, &mut first_seen, now);
        let workload = read_workload(&backend, vram.total_mib);
        let lease = read_lease(&lease_mirror);
        let pending_requests = read_pending(&pending_mirror);
        let queue = read_queue(&queue_mirror);
        // schema 5 (ADR-0051): the Check-ins cards + Hermes cron. Each read is fail-open: a DB error
        // folds to `(vec![], UNK)` (empty cards, total UNKNOWN — never a panic, never a fabricated
        // task); a missing cron file folds to `[]`. One indexed SELECT per tick (idx_tasks_status).
        let (check_ins, check_ins_total) =
            read_check_ins(&kanban, now_secs(), CHECK_IN_CAP).unwrap_or((vec![], UNK));
        let recurring = read_recurring(&cron_jobs, RECUR_CAP);

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
            pending_requests,     // schema 2: lucid deferral buffer; {0,0} until the sidecar exists.
            queue,                // schema 4: live arbiter wait-queue; {0,""} until `agentosd queue` runs.
            gpu_util_pct: util_field(gpu_util), // schema 5: real NVML util %; -1 == unreadable.
            check_ins,            // schema 5: per-task cards (read-only kanban); [] == none/can't-read.
            check_ins_total,      // schema 5: pre-cap total for honest truncation; -1 == unknown.
            recurring,            // schema 5: Hermes cron jobs; [] == none/can't-read.
            written_at: 0,        // schema 6: held at 0 for the payload compare; stamped at write.
        };

        // Edge-dedup on the PAYLOAD (written_at held at 0 on both sides), plus a liveness
        // heartbeat: even a byte-identical frame is rewritten once HEARTBEAT_SECS lapse, so a
        // consumer can tell "producer alive, nothing changed" from "producer dead, file frozen".
        let changed = last.as_ref() != Some(&feed);
        let heartbeat_due =
            last_write.is_none_or(|t: Instant| t.elapsed() >= Duration::from_secs(HEARTBEAT_SECS));
        if changed || heartbeat_due || once {
            let mut stamped = feed.clone();
            stamped.written_at = now_secs();
            match write_keyhole(&dir, &stamped) {
                Ok(()) => {
                    last_write = Some(Instant::now());
                    if changed {
                        println!(
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
                        );
                    }
                }
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
        // schema 4 (ADR-0041): `queue` (live arbiter wait-queue) appended after `pending_requests`.
        // schema 5 (ADR-0051): `gpu_util_pct` + `check_ins` + `check_ins_total` + `recurring`
        // appended after `queue` (additive; an older consumer ignores the trailing fields).
        // schema 6 (ADR-0051 amendment): `written_at` producer heartbeat appended last (additive).
        let feed = KeyholeFeed {
            schema: 6,
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
            queue: Queue { depth: 3, next_tier: "batch".into() },
            gpu_util_pct: 42,
            check_ins: vec![CheckIn {
                id: "t1".into(),
                title: "Refactor feed".into(),
                assignee: "qwen".into(),
                status: "running".into(),
                priority: 5,
                consecutive_failures: 0,
                created_at: 1_750_000_000,
                started_at: 1_750_000_100,
                last_heartbeat_at: 1_750_000_200,
                step: "edit".into(),
                last_error: String::new(),
            }],
            check_ins_total: 1,
            recurring: vec![Recurring {
                id: "job1".into(),
                name: "Daily audit".into(),
                schedule: "0 4 * * *".into(),
                enabled: true,
                state: "scheduled".into(),
                next_run: "2026-06-30T04:00:00-07:00".into(),
                last_run: "2026-06-29T05:21:52-07:00".into(),
                last_status: "ok".into(),
            }],
            written_at: 1_751_000_000,
        };
        assert_eq!(
            serde_json::to_string(&feed).unwrap(),
            r#"{"schema":6,"state":"working","gateway":"running","floats":{"busy":0.85,"warm":0.0,"snag":0.0},"fleet":{"running":3,"queued":2,"snagged":0},"lease":{"tier":"interactive","holder":"Hermes","preempt":"wallpaper yielded ~1.5GB -> qwen2.5 loaded"},"vram":{"used_mib":6240,"total_mib":8192},"residency":[{"name":"qwen2.5:14b","loaded_secs":240}],"workload":{"name":"ComfyUI","used_mib":21000},"tokens_per_sec":null,"pending_requests":{"held":2,"needs_review":1},"queue":{"depth":3,"next_tier":"batch"},"gpu_util_pct":42,"check_ins":[{"id":"t1","title":"Refactor feed","assignee":"qwen","status":"running","priority":5,"consecutive_failures":0,"created_at":1750000000,"started_at":1750000100,"last_heartbeat_at":1750000200,"step":"edit","last_error":""}],"check_ins_total":1,"recurring":[{"id":"job1","name":"Daily audit","schedule":"0 4 * * *","enabled":true,"state":"scheduled","next_run":"2026-06-30T04:00:00-07:00","last_run":"2026-06-29T05:21:52-07:00","last_status":"ok"}],"written_at":1751000000}"#
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
    fn queue_defaults_to_empty_not_unknown() {
        // schema 4 (ADR-0041): an empty live arbiter queue is a REAL datum — {depth:0,next_tier:""},
        // serialized, never absent, never UNKNOWN. A missing/unreadable arbiter mirror (no `agentosd
        // queue` running) reads as nothing-waiting — calm, fail-open, never a fabricated backlog.
        assert_eq!(Queue::default(), Queue { depth: 0, next_tier: String::new() });
        assert_eq!(read_queue(Path::new("/nonexistent/queue.json")), Queue::default());
        assert_eq!(
            serde_json::from_str::<Queue>(r#"{"depth":2,"next_tier":"batch"}"#).unwrap(),
            Queue { depth: 2, next_tier: "batch".into() }
        );
        // a pre-schema-4 / blank file round-trips to empty via serde defaults.
        assert_eq!(serde_json::from_str::<Queue>("{}").unwrap(), Queue::default());
        // negative depth is clamped to 0 (a cardinality; -1/UNKNOWN must never leak into the count).
        let tmp = std::env::temp_dir().join(format!("agentosd_queue_test_{}.json", std::process::id()));
        std::fs::write(&tmp, r#"{"depth":-3,"next_tier":"interactive"}"#).unwrap();
        assert_eq!(read_queue(&tmp), Queue { depth: 0, next_tier: "interactive".into() });
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
            queue: Queue::default(),
            gpu_util_pct: UNK,
            check_ins: vec![],
            check_ins_total: UNK,
            recurring: vec![],
            written_at: 0,
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
        // schema 5: an unreadable GPU is -1 (distinct from a real 0% idle); unreadable kanban →
        // empty cards + a -1 total (NOT 0 rows); no cron → empty recurring.
        assert!(s.contains(r#""gpu_util_pct":-1"#));
        assert!(s.contains(r#""check_ins":[]"#));
        assert!(s.contains(r#""check_ins_total":-1"#));
        assert!(s.contains(r#""recurring":[]"#));
    }

    // ---- schema 5 (ADR-0051): the Check-ins per-task read + GPU util + cron ----

    #[test]
    fn util_field_maps_none_to_unknown_and_keeps_a_real_zero() {
        assert_eq!(util_field(Some(42)), 42);
        assert_eq!(util_field(Some(0)), 0); // a real idle GPU is 0, NOT UNKNOWN
        assert_eq!(util_field(None), UNK); // unreadable NVML → -1, distinct from 0
    }

    /// Build a `tasks` table with the full Check-ins column set + insert rows for the test.
    fn write_check_in_db(path: &Path, inserts: &str) {
        let _ = fs::remove_file(path);
        let conn = Connection::open(path).unwrap();
        conn.execute_batch(
            "CREATE TABLE tasks (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, assignee TEXT, status TEXT NOT NULL,
                priority INTEGER, consecutive_failures INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL, started_at INTEGER, completed_at INTEGER,
                last_heartbeat_at INTEGER, current_step_key TEXT, last_failure_error TEXT);",
        )
        .unwrap();
        conn.execute_batch(inserts).unwrap();
    }

    #[test]
    fn read_check_ins_maps_orders_and_windows() {
        let p = std::env::temp_dir().join(format!("agentos_checkins_{}.db", std::process::id()));
        let now = 1_750_000_000i64;
        // running, a stalled (failures), a needs-you (review), a queued (todo), a recent done,
        // an OLD done (outside the 6h window → excluded), and an archived (excluded).
        let recent_done = now - 60;
        let old_done = now - DONE_WINDOW_SECS - 10;
        let inserts = format!(
            "INSERT INTO tasks
             (id,title,assignee,status,priority,consecutive_failures,created_at,started_at,completed_at,last_heartbeat_at,current_step_key,last_failure_error) VALUES
             ('a','Index sources','atlas','running',5,0,{c},{s},NULL,{hb},'edit',NULL),
             ('b','Train reranker','local','running',5,2,{c},{s},NULL,{hb},'step',NULL),
             ('c','Review draft','scribe','review',3,0,{c},NULL,NULL,NULL,NULL,NULL),
             ('d','Backfill','atlas','todo',1,0,{c},NULL,NULL,NULL,NULL,NULL),
             ('e','Nightly backup','local','done',0,0,{c},{s},{rd},{hb},NULL,NULL),
             ('f','Old finished','local','done',0,0,{c},{s},{od},{hb},NULL,NULL),
             ('g','Archived job','local','archived',0,0,{c},{s},{rd},{hb},NULL,NULL);",
            c = now - 1000, s = now - 900, hb = now - 100, rd = recent_done, od = old_done
        );
        write_check_in_db(&p, &inserts);

        let (cards, total) = read_check_ins(&p, now, CHECK_IN_CAP).unwrap();
        // 5 included (a,b,c,d,e); old-done (f) + archived (g) excluded.
        assert_eq!(total, 5);
        assert_eq!(cards.len(), 5);
        // active-first ordering: running before review before todo before done; within running the
        // failing one (b) sorts ahead (the `consecutive_failures>0 DESC` tiebreak).
        let ids: Vec<&str> = cards.iter().map(|c| c.id.as_str()).collect();
        assert_eq!(ids, vec!["b", "a", "c", "d", "e"]);
        // mapping: raw status passes through; a NULL assignee/step/error → "" ; NULL started → -1.
        let c = &cards[2]; // 'c' the review card
        assert_eq!(c.status, "review");
        assert_eq!(c.assignee, "scribe");
        assert_eq!(c.started_at, UNK);
        assert_eq!(c.last_heartbeat_at, UNK);
        assert_eq!(c.step, "");
        assert_eq!(c.last_error, "");
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn read_check_ins_caps_and_reports_pre_cap_total() {
        let p = std::env::temp_dir().join(format!("agentos_checkins_cap_{}.db", std::process::id()));
        let now = 1_750_000_000i64;
        let mut rows = String::from(
            "INSERT INTO tasks (id,title,assignee,status,priority,consecutive_failures,created_at,started_at,completed_at,last_heartbeat_at,current_step_key,last_failure_error) VALUES ",
        );
        for i in 0..25 {
            rows.push_str(&format!(
                "('t{i}','Task {i}','a','running',0,0,{c},{c},NULL,{c},NULL,NULL){}",
                if i == 24 { ";" } else { "," },
                c = now - 100
            ));
        }
        write_check_in_db(&p, &rows);
        let (cards, total) = read_check_ins(&p, now, CHECK_IN_CAP).unwrap();
        assert_eq!(cards.len(), CHECK_IN_CAP); // capped at 16
        assert_eq!(total, 25); // but the honest pre-cap total is reported
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn read_check_ins_truncates_a_giant_error() {
        let p = std::env::temp_dir().join(format!("agentos_checkins_err_{}.db", std::process::id()));
        let now = 1_750_000_000i64;
        let big = "x".repeat(500);
        let inserts = format!(
            "INSERT INTO tasks (id,title,assignee,status,priority,consecutive_failures,created_at,started_at,completed_at,last_heartbeat_at,current_step_key,last_failure_error) VALUES
             ('a','Boom','local','blocked',0,1,{c},{c},NULL,{c},NULL,'{big}');",
            c = now - 100
        );
        write_check_in_db(&p, &inserts);
        let (cards, _) = read_check_ins(&p, now, CHECK_IN_CAP).unwrap();
        assert_eq!(cards.len(), 1);
        // bounded to ERROR_MAX chars + the ellipsis
        assert_eq!(cards[0].last_error.chars().count(), ERROR_MAX + 1);
        assert!(cards[0].last_error.ends_with('…'));
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn read_check_ins_absent_db_is_an_error_so_caller_fails_open_empty() {
        // The runtime folds Err → (vec![], UNK): empty cards + a -1 total (unreadable, NOT "0 tasks").
        let p = Path::new("/nonexistent/dir/kanban.db");
        assert!(read_check_ins(p, 1_750_000_000, CHECK_IN_CAP).is_err());
        let (cards, total) = read_check_ins(p, 1_750_000_000, CHECK_IN_CAP).unwrap_or((vec![], UNK));
        assert!(cards.is_empty());
        assert_eq!(total, UNK);
    }

    #[test]
    fn read_check_ins_reachable_but_empty_is_zero_not_unknown() {
        // A real empty board: the read succeeds, 0 rows, total 0 — distinct from the -1 above.
        let p = std::env::temp_dir().join(format!("agentos_checkins_empty_{}.db", std::process::id()));
        write_check_in_db(&p, "");
        let (cards, total) = read_check_ins(&p, 1_750_000_000, CHECK_IN_CAP).unwrap();
        assert!(cards.is_empty());
        assert_eq!(total, 0);
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn read_recurring_parses_cron_jobs_and_collapses_nulls() {
        let p = std::env::temp_dir().join(format!("agentos_cron_{}.json", std::process::id()));
        fs::write(
            &p,
            r#"{"jobs":[
                {"id":"j1","name":"Daily audit","schedule_display":"0 4 * * *","enabled":true,
                 "state":"scheduled","next_run_at":"2026-06-30T04:00:00-07:00",
                 "last_run_at":"2026-06-29T05:21:52-07:00","last_status":"ok"},
                {"id":"j2","name":"Never ran","schedule_display":"*/5 * * * *","enabled":false,
                 "state":"paused","next_run_at":null,"last_run_at":null,"last_status":null}
            ],"updated_at":"x"}"#,
        )
        .unwrap();
        let r = read_recurring(&p, RECUR_CAP);
        assert_eq!(r.len(), 2);
        assert_eq!(r[0].schedule, "0 4 * * *");
        assert_eq!(r[0].last_status, "ok");
        assert!(r[0].enabled);
        // a never-run job: nulls collapse to "" (honest, never a fabricated time).
        assert_eq!(r[1].next_run, "");
        assert_eq!(r[1].last_run, "");
        assert_eq!(r[1].last_status, "");
        assert!(!r[1].enabled);
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn read_recurring_absent_or_garbage_is_empty() {
        assert!(read_recurring(Path::new("/nonexistent/cron/jobs.json"), RECUR_CAP).is_empty());
        let p = std::env::temp_dir().join(format!("agentos_cron_bad_{}.json", std::process::id()));
        fs::write(&p, "not json").unwrap();
        assert!(read_recurring(&p, RECUR_CAP).is_empty());
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn probe_check_in_columns_ok_present_drift_when_dropped_benign_when_absent() {
        // present: full column set → Ok.
        let p = std::env::temp_dir().join(format!("agentos_ci_probe_ok_{}.db", std::process::id()));
        write_check_in_db(&p, "");
        assert!(probe_check_in_columns(&p).is_ok());
        let _ = fs::remove_file(&p);
        // drift: a column we read is gone → Err (loud log, not phantom-empty).
        let p2 = std::env::temp_dir().join(format!("agentos_ci_probe_drift_{}.db", std::process::id()));
        let _ = fs::remove_file(&p2);
        let conn = Connection::open(&p2).unwrap();
        conn.execute_batch("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT);").unwrap();
        assert!(probe_check_in_columns(&p2).is_err());
        let _ = fs::remove_file(&p2);
        // absent: no DB yet (Hermes not up) is benign → Ok, not a false drift warning.
        assert!(probe_check_in_columns(Path::new("/nonexistent/kanban.db")).is_ok());
    }
}

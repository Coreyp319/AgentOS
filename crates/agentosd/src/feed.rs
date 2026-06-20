//! P1 — the agent-state producer (the signal-bus producer side; ADR-0007 harvest).
//!
//! Reads Hermes fleet state READ-ONLY (`~/.hermes/kanban.db` +
//! `~/.hermes/gateway_state.json`) and emits `$XDG_RUNTIME_DIR/nimbus-aurora/agent.json`
//! — the ambient feed the reactive wallpaper consumes (same family as the existing
//! window/audio bridges). Safe + reversible: read-only queries → one atomically-written
//! JSON file; nothing destructive, no GPU/NVML dependency.
//!
//! Output contract (must match the hills-reactive spike EXACTLY):
//!   {"state":N,"busy":f,"warm":f,"snag":f}
//!   state ∈ 0 idle · 1 working · 2 needs_you · 3 acting · 4 snag   (informational)
//!   busy/warm/snag ∈ 0.0..1.0   (these drive the shader)
//!
//! Status mapping is grounded in the Hermes kernel (`hermes_cli/kanban_db.py`):
//!   working = `tasks.status = 'running'`  (+ gateway `active_agents` chat-loop turns)
//!   snag    = `tasks.status = 'blocked'` OR `consecutive_failures > 0`
//!             (crash/timeout fall back to `ready` w/ the counter bumped — the column
//!              alone can't show them)
//!   needs_you (warm) = a pending Hermes approval (P2), read from
//!     `~/.hermes/needs_you.json` (written by the `needs-you-signal` plugin) and
//!     gated on the gateway being alive (the signal is in-process → could go stale).

use std::fs;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::Duration;

use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};

/// Active-task count at which "working" intensity saturates to 1.0. Hermes'
/// `kanban.max_in_progress` is unbounded by default, so we saturate at a nominal
/// concurrency rather than a hard cap (intensity ∝ busy, per the embodiment design).
const NOMINAL_ACTIVE: f64 = 4.0;

/// Counts derived from one read-only pass over `tasks` (+ what the gateway reports).
#[derive(Debug, Default, Clone, PartialEq)]
pub struct FleetCounts {
    pub total: u32,
    pub running: u32,
    pub snagged: u32,
    pub pending: u32,
}

/// The two fields P1 needs from `gateway_state.json`.
#[derive(Debug, Clone)]
pub struct GatewayInfo {
    pub gateway_state: String,
    pub active_agents: u32,
}

/// The emitted feed — serializes to the exact spike contract.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct AgentFeed {
    pub state: u8,
    pub busy: f64,
    pub warm: f64,
    pub snag: f64,
}

fn round3(x: f64) -> f64 {
    (x * 1000.0).round() / 1000.0
}

/// Ramp `n` over [1..=NOMINAL_ACTIVE] from `lo` to `hi`, clamped; 0 → 0.0.
fn ramp(n: u32, lo: f64, hi: f64) -> f64 {
    if n == 0 {
        return 0.0;
    }
    let t = ((n as f64 - 1.0) / (NOMINAL_ACTIVE - 1.0)).clamp(0.0, 1.0);
    round3(lo + (hi - lo) * t)
}

/// Pure mapping: fleet counts + optional gateway info + Hermes pending-approval count +
/// LOCAL lucid review count → the ambient feed. Precedence: **needs_you > snag > working >
/// idle** — a pending request waiting on YOU is the most actionable signal.
///
/// The warm bloom folds two ORIGINS at the count level (ADR-0019 §6, G1): a Hermes
/// command-approval (gated on the gateway being alive, since the signal lives in gateway RAM)
/// plus a local lucid request awaiting human review (its OWN liveness gate lives upstream in
/// `read_lucid_review`, so it survives a Hermes outage — fail-open, ADR-0019 §1). They are
/// disjoint intent sets summed once → one warm scalar in [0.75, 0.9], regardless of the split.
/// `lucid_review = 0` reproduces the pre-ADR-0019 output bit-for-bit.
pub fn derive_feed(
    fleet: &FleetCounts,
    gw: Option<&GatewayInfo>,
    needs_you: u32,
    lucid_review: u32,
) -> AgentFeed {
    let active = fleet.running + gw.map(|g| g.active_agents).unwrap_or(0);
    // A Hermes pending approval lives only in the gateway's RAM (P2 recon), so a crashed
    // gateway could leave a stale needs_you.json. Only honour it while the gateway is alive;
    // the plugin also rewrites an empty file on clean startup. The lucid count is NOT gated
    // here — its liveness is checked at read time — so a local review survives Hermes being down.
    let gateway_alive = gw
        .map(|g| matches!(g.gateway_state.as_str(), "running" | "starting" | "degraded"))
        .unwrap_or(false);
    let hermes_pending = if gateway_alive { needs_you } else { 0 };
    let pending = hermes_pending + lucid_review;

    if pending > 0 {
        // the ONE deliberate warm bloom (design-ux) — outranks working/snag.
        AgentFeed { state: 2, busy: 0.0, warm: ramp(pending, 0.75, 0.9), snag: 0.0 }
    } else if fleet.snagged > 0 {
        AgentFeed { state: 4, busy: 0.0, warm: 0.0, snag: ramp(fleet.snagged, 0.6, 0.9) }
    } else if active > 0 {
        AgentFeed { state: 1, busy: ramp(active, 0.7, 1.0), warm: 0.0, snag: 0.0 }
    } else {
        AgentFeed { state: 0, busy: 0.0, warm: 0.0, snag: 0.0 }
    }
}

/// One-pass fleet snapshot. `running` and `snagged` can overlap (a running task with
/// `consecutive_failures>0`); `derive_feed` lets snag win.
const FLEET_SQL: &str = "\
SELECT \
  COUNT(*) AS total, \
  COALESCE(SUM(status = 'running'), 0) AS running, \
  COALESCE(SUM(status = 'blocked' OR consecutive_failures > 0), 0) AS snagged, \
  COALESCE(SUM(status IN ('triage','todo','scheduled','ready','review')), 0) AS pending \
FROM tasks";

pub(crate) fn read_fleet(db: &Path) -> rusqlite::Result<FleetCounts> {
    let conn = Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    // WAL DB with busy_timeout=0; give the reader a small window so a writer's
    // checkpoint doesn't trip an instant SQLITE_BUSY.
    conn.busy_timeout(Duration::from_millis(2000))?;
    conn.query_row(FLEET_SQL, [], |r| {
        Ok(FleetCounts {
            total: r.get::<_, i64>(0)? as u32,
            running: r.get::<_, i64>(1)? as u32,
            snagged: r.get::<_, i64>(2)? as u32,
            pending: r.get::<_, i64>(3)? as u32,
        })
    })
}

/// Columns `FLEET_SQL` depends on. We read straight into Hermes' internal `tasks` table
/// (no API boundary), so if that schema drifts — a column renamed or dropped on a Hermes
/// upgrade — `read_fleet` errors and the caller folds it into all-zeros. That makes a real
/// schema break indistinguishable from "Hermes is down" or "no tasks": phantom calm on the
/// wallpaper. We probe ONCE at startup and log loudly so drift lands in the journal instead.
/// Diagnostic only — the runtime path stays fail-open (ADR-0003).
const FLEET_COLUMNS: [&str; 2] = ["status", "consecutive_failures"];

/// Outcome of the one-shot startup schema probe (`probe_fleet_schema`).
pub(crate) enum SchemaCheck {
    /// `tasks` exists and exposes every column `FLEET_SQL` reads.
    Ok,
    /// DB or `tasks` table not present yet — benign: Hermes simply isn't up.
    Absent(String),
    /// `tasks` exists but a column we depend on is gone — a real Hermes schema drift.
    Drift(String),
}

/// One-shot check that Hermes' `tasks` table still exposes the columns `FLEET_SQL` needs,
/// so a schema change surfaces as a log line rather than a silent slide to idle.
pub(crate) fn probe_fleet_schema(db: &Path) -> SchemaCheck {
    let conn = match Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_ONLY) {
        Ok(c) => c,
        Err(e) => return SchemaCheck::Absent(format!("open {}: {e}", db.display())),
    };
    let mut have = std::collections::HashSet::new();
    match conn.prepare("SELECT name FROM pragma_table_info('tasks')") {
        Ok(mut stmt) => match stmt.query_map([], |r| r.get::<_, String>(0)) {
            Ok(rows) => have.extend(rows.flatten()),
            Err(e) => return SchemaCheck::Absent(format!("pragma_table_info: {e}")),
        },
        Err(e) => return SchemaCheck::Absent(format!("prepare pragma: {e}")),
    }
    if have.is_empty() {
        return SchemaCheck::Absent("`tasks` table not found (Hermes not initialized?)".into());
    }
    let missing: Vec<&str> =
        FLEET_COLUMNS.iter().copied().filter(|c| !have.contains(*c)).collect();
    if missing.is_empty() {
        SchemaCheck::Ok
    } else {
        SchemaCheck::Drift(format!("`tasks` is missing {missing:?}"))
    }
}

#[derive(Deserialize, Default)]
struct GatewayFile {
    #[serde(default)]
    gateway_state: String,
    #[serde(default)]
    active_agents: u32,
}

pub(crate) fn read_gateway(path: &Path) -> Option<GatewayInfo> {
    let g: GatewayFile = serde_json::from_str(&fs::read_to_string(path).ok()?).ok()?;
    Some(GatewayInfo { gateway_state: g.gateway_state, active_agents: g.active_agents })
}

#[derive(Deserialize, Default)]
struct NeedsYouFile {
    #[serde(default)]
    pending: u32,
}

/// Count of pending Hermes approvals, written by the `needs-you-signal` plugin.
/// Absent/unparseable → 0 (Hermes isn't blocked on us, or the plugin isn't installed).
pub(crate) fn read_needs_you(path: &Path) -> u32 {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str::<NeedsYouFile>(&s).ok())
        .map(|n| n.pending)
        .unwrap_or(0)
}

#[derive(Deserialize, Default)]
struct LucidReviewFile {
    #[serde(default)]
    pending_review: u32,
    #[serde(default)]
    updated_at: f64,
}

/// Count of LOCAL lucid requests awaiting human review, from `review.json` (ADR-0019 §6, of
/// which lucid is the SOLE writer — a different path from the Hermes `needs_you.json`, so the
/// two producers never collide). Has its OWN liveness gate, decoupled from the Hermes gateway:
/// a local review blooms even while Hermes is down (fail-open), and retracts within `STALE_SECS`
/// of the drainer going quiet even though the file lingers. Absent/unparseable/stale → 0.
pub(crate) fn read_lucid_review(path: &Path, now: f64) -> u32 {
    const STALE_SECS: f64 = 12.0; // > 3× the drainer tick (≤4s); re-tune if the cadence changes
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str::<LucidReviewFile>(&s).ok())
        .filter(|r| now - r.updated_at <= STALE_SECS)
        .map(|r| r.pending_review)
        .unwrap_or(0)
}

/// `$XDG_DATA_HOME/agentos/lucid-queue/<file>` (or `$HOME/.local/share/...`) — the durable lucid
/// queue dir lucid owns and `feed.rs` only reads.
pub(crate) fn lucid_data_path(file: &str) -> PathBuf {
    let base = std::env::var("XDG_DATA_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| {
            let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
            format!("{home}/.local/share")
        });
    PathBuf::from(base).join("agentos").join("lucid-queue").join(file)
}

/// Wall-clock seconds since the epoch, for the `read_lucid_review` liveness gate.
fn now_epoch() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn current_uid() -> u32 {
    // Real getuid(2). The old `/proc/self`-then-`unwrap_or(1000)` path could, on a
    // failed metadata read, point the `/run/user/<uid>` fallback at ANOTHER user's
    // runtime dir (security-reviewer / ADR-0030 must-fix). getuid never fails.
    // SAFETY: getuid() is an always-successful syscall with no preconditions.
    unsafe { libc::getuid() }
}

/// `$XDG_RUNTIME_DIR/nimbus-aurora` (created if absent), with the `/run/user/<uid>`
/// fallback the existing bridges use.
pub(crate) fn feed_dir() -> std::io::Result<PathBuf> {
    let runtime = std::env::var("XDG_RUNTIME_DIR")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| format!("/run/user/{}", current_uid()));
    let dir = PathBuf::from(runtime).join("nimbus-aurora");
    fs::create_dir_all(&dir)?;
    harden_feed_dir(&dir)?;
    Ok(dir)
}

/// The reactive feed files (`agent.json` / `wind.json` / `audio.json`) carry fleet +
/// desktop signals; the dir must be private to us. `create_dir_all` inherits the
/// umask — on this box that yields 0755 (world-readable), so any local uid can read
/// the feed (security-reviewer / ADR-0030 must-fix). Force 0700, and refuse if the
/// dir is owned by another uid (a pre-created dir is a write-trap, not ours to use).
fn harden_feed_dir(dir: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let meta = fs::metadata(dir)?;
    if meta.uid() != current_uid() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::PermissionDenied,
            format!(
                "{} is owned by uid {} (not us) — refusing to use a foreign runtime dir",
                dir.display(),
                meta.uid()
            ),
        ));
    }
    // Only rewrite the mode if it grants group/other anything (idempotent + quiet).
    if meta.permissions().mode() & 0o077 != 0 {
        fs::set_permissions(dir, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

pub(crate) fn hermes_path(file: &str) -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
    PathBuf::from(home).join(".hermes").join(file)
}

/// Atomic write: temp file in the same dir (dot-prefixed so a `*.json` poller skips
/// it) then rename — the wallpaper never reads a half-written file.
fn write_feed(dir: &Path, feed: &AgentFeed) -> std::io::Result<()> {
    let mut json = serde_json::to_string(feed).expect("AgentFeed serializes");
    json.push('\n');
    let tmp = dir.join(format!(".agent.{}.tmp", std::process::id()));
    fs::write(&tmp, json)?;
    fs::rename(&tmp, dir.join("agent.json"))
}

pub(crate) fn state_word(s: u8) -> &'static str {
    match s {
        0 => "idle",
        1 => "working",
        2 => "needs_you",
        3 => "acting",
        4 => "snag",
        _ => "?",
    }
}

/// Poll Hermes fleet state and emit `agent.json`. Edge-driven: rewrites only when the
/// feed changes (the consumer eases uniforms, so prompt-on-change is enough). `--once`
/// emits a single snapshot and exits (for `verify`/CI). Degrades to idle when Hermes
/// is unreachable.
pub fn run(once: bool) -> Result<(), Box<dyn std::error::Error>> {
    let dir = feed_dir()?;
    let out = dir.join("agent.json");
    let kanban = hermes_path("kanban.db");
    let gateway = hermes_path("gateway_state.json");
    let needs_you_path = hermes_path("needs_you.json");
    let lucid_review_path = lucid_data_path("review.json");
    println!("agentosd feed → {}", out.display());

    // Surface a Hermes `tasks` schema drift loudly once at startup; the runtime read below
    // stays fail-open either way (the drift would otherwise read as phantom calm).
    match probe_fleet_schema(&kanban) {
        SchemaCheck::Ok => {}
        SchemaCheck::Absent(why) => {
            println!("agentosd feed: kanban not ready ({why}); fail-open to idle until Hermes is up")
        }
        SchemaCheck::Drift(why) => eprintln!(
            "agentosd feed: WARNING — Hermes kanban schema drift: {why}. Fleet counts now read \
             as zero (fail-open); update FLEET_SQL in feed.rs to match Hermes' tasks table."
        ),
    }

    let mut last: Option<AgentFeed> = None;
    loop {
        let fleet = read_fleet(&kanban).unwrap_or_default();
        let gw = read_gateway(&gateway);
        let needs_you = read_needs_you(&needs_you_path);
        let lucid_review = read_lucid_review(&lucid_review_path, now_epoch());
        let feed = derive_feed(&fleet, gw.as_ref(), needs_you, lucid_review);

        let changed = last.as_ref() != Some(&feed);
        if changed || once {
            match write_feed(&dir, &feed) {
                Ok(()) if changed => println!(
                    "[{}] {} (run {}, snag {}, pend {}/{}, approve {}, review {}, gw {}) → {}",
                    crate::now_hms(),
                    state_word(feed.state),
                    fleet.running,
                    fleet.snagged,
                    fleet.pending,
                    fleet.total,
                    needs_you,
                    lucid_review,
                    gw.as_ref().map(|g| g.gateway_state.as_str()).unwrap_or("-"),
                    serde_json::to_string(&feed).unwrap(),
                ),
                Ok(()) => {}
                Err(e) => eprintln!("agentosd feed: write failed: {e}"),
            }
            last = Some(feed);
        }
        if once {
            break;
        }
        thread::sleep(Duration::from_secs(2));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn gw(active: u32) -> GatewayInfo {
        gw_state("running", active)
    }
    fn gw_state(state: &str, active: u32) -> GatewayInfo {
        GatewayInfo { gateway_state: state.into(), active_agents: active }
    }
    fn counts(running: u32, snagged: u32) -> FleetCounts {
        FleetCounts { running, snagged, total: running + snagged, pending: 0 }
    }

    #[test]
    fn idle_when_empty() {
        assert_eq!(
            derive_feed(&FleetCounts::default(), None, 0, 0),
            AgentFeed { state: 0, busy: 0.0, warm: 0.0, snag: 0.0 }
        );
    }

    #[test]
    fn one_running_task_reads_as_working() {
        assert_eq!(
            derive_feed(&counts(1, 0), None, 0, 0),
            AgentFeed { state: 1, busy: 0.7, warm: 0.0, snag: 0.0 }
        );
    }

    #[test]
    fn busy_scales_then_saturates() {
        assert_eq!(derive_feed(&counts(2, 0), None, 0, 0).busy, 0.8);
        assert_eq!(derive_feed(&counts(3, 0), None, 0, 0).busy, 0.9);
        assert_eq!(derive_feed(&counts(4, 0), None, 0, 0).busy, 1.0);
        assert_eq!(derive_feed(&counts(10, 0), None, 0, 0).busy, 1.0);
    }

    #[test]
    fn gateway_chat_turns_count_as_working() {
        // No kanban tasks, but the gateway is actively processing 2 turns.
        assert_eq!(
            derive_feed(&FleetCounts::default(), Some(&gw(2)), 0, 0),
            AgentFeed { state: 1, busy: 0.8, warm: 0.0, snag: 0.0 }
        );
    }

    #[test]
    fn snag_takes_precedence_over_working() {
        // Busy AND snagged → snag wins, busy is forced to 0.
        assert_eq!(
            derive_feed(&counts(3, 1), Some(&gw(5)), 0, 0),
            AgentFeed { state: 4, busy: 0.0, warm: 0.0, snag: 0.6 }
        );
    }

    #[test]
    fn snag_scales() {
        assert_eq!(derive_feed(&counts(0, 1), None, 0, 0).snag, 0.6);
        assert_eq!(derive_feed(&counts(0, 4), None, 0, 0).snag, 0.9);
    }

    #[test]
    fn pending_approval_lights_needs_you_warm() {
        // gateway alive + 1 pending approval → the warm needs_you bloom (state 2).
        assert_eq!(
            derive_feed(&FleetCounts::default(), Some(&gw(0)), 1, 0),
            AgentFeed { state: 2, busy: 0.0, warm: 0.75, snag: 0.0 }
        );
    }

    #[test]
    fn needs_you_outranks_snag_and_working() {
        // busy AND snagged AND a pending approval → needs_you wins; busy+snag forced 0.
        assert_eq!(
            derive_feed(&counts(3, 2), Some(&gw(5)), 2, 0),
            AgentFeed { state: 2, busy: 0.0, warm: 0.8, snag: 0.0 }
        );
    }

    #[test]
    fn needs_you_suppressed_when_gateway_not_alive() {
        // a stale needs_you.json (gateway stopped / file missing) must NOT raise warm.
        assert_eq!(derive_feed(&FleetCounts::default(), Some(&gw_state("stopped", 0)), 3, 0).warm, 0.0);
        assert_eq!(derive_feed(&FleetCounts::default(), None, 3, 0).warm, 0.0);
        // and it falls through to the real state — a running task still reads as working.
        assert_eq!(derive_feed(&counts(1, 0), Some(&gw_state("stopped", 0)), 3, 0).state, 1);
    }

    #[test]
    fn warm_zero_without_pending_approval() {
        // with no pending approval (and no local review), warm stays 0 across all working/snag combos.
        for running in 0..6 {
            for snagged in 0..6 {
                assert_eq!(derive_feed(&counts(running, snagged), Some(&gw(running)), 0, 0).warm, 0.0);
            }
        }
    }

    // ---- ADR-0019 §6 (G1): the local lucid review folds additively into the warm bloom ----

    #[test]
    fn lucid_review_blooms_warm_even_with_dead_gateway() {
        // The fail-open core (§1): a LOCAL review must light warm while Hermes is down — its
        // liveness is gated upstream in read_lucid_review, NOT by gateway_alive here.
        assert_eq!(
            derive_feed(&FleetCounts::default(), None, 0, 1),
            AgentFeed { state: 2, busy: 0.0, warm: 0.75, snag: 0.0 }
        );
        assert_eq!(
            derive_feed(&FleetCounts::default(), Some(&gw_state("stopped", 0)), 0, 1).warm,
            0.75
        );
    }

    #[test]
    fn lucid_and_hermes_pending_sum_into_one_warm_scalar() {
        // Two disjoint origins (1 Hermes approval + 1 lucid review) sum to pending 2, ramped ONCE.
        assert_eq!(
            derive_feed(&FleetCounts::default(), Some(&gw(0)), 1, 1),
            AgentFeed { state: 2, busy: 0.0, warm: 0.8, snag: 0.0 }
        );
        // The sum saturates the warm ramp at 0.9 and never exceeds it (1 Hermes + 3 lucid = 4).
        assert_eq!(derive_feed(&FleetCounts::default(), Some(&gw(0)), 1, 3).warm, 0.9);
    }

    #[test]
    fn lucid_review_zero_adds_no_warm() {
        // lucid_review = 0 is inert: warm stays 0 across the working/snag matrix (bit-identical).
        for running in 0..6 {
            for snagged in 0..6 {
                assert_eq!(derive_feed(&counts(running, snagged), Some(&gw(running)), 0, 0).warm, 0.0);
            }
        }
    }

    #[test]
    fn read_lucid_review_counts_a_fresh_file() {
        let p = std::env::temp_dir().join(format!("agentos_review_fresh_{}.json", std::process::id()));
        fs::write(&p, r#"{"schema":1,"pending_review":2,"updated_at":1000.0}"#).unwrap();
        assert_eq!(read_lucid_review(&p, 1005.0), 2); // 5s old < 12s STALE → live
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn read_lucid_review_retracts_a_stale_file() {
        let p = std::env::temp_dir().join(format!("agentos_review_stale_{}.json", std::process::id()));
        fs::write(&p, r#"{"schema":1,"pending_review":3,"updated_at":1000.0}"#).unwrap();
        assert_eq!(read_lucid_review(&p, 1100.0), 0); // 100s old > 12s → the drainer went quiet
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn read_lucid_review_absent_is_zero() {
        let p = std::env::temp_dir().join(format!("agentos_review_absent_{}.json", std::process::id()));
        let _ = fs::remove_file(&p);
        assert_eq!(read_lucid_review(&p, 1000.0), 0);
    }

    #[test]
    fn serializes_to_the_exact_spike_contract() {
        let f = AgentFeed { state: 1, busy: 0.7, warm: 0.0, snag: 0.0 };
        assert_eq!(
            serde_json::to_string(&f).unwrap(),
            r#"{"state":1,"busy":0.7,"warm":0.0,"snag":0.0}"#
        );
    }

    // ---- schema-drift probe (hardens the direct-into-Hermes `tasks` SQL read) ----

    fn write_tasks_db(path: &Path, columns: &str) {
        let _ = fs::remove_file(path);
        let conn = Connection::open(path).unwrap();
        conn.execute_batch(&format!("CREATE TABLE tasks ({columns});")).unwrap();
    }

    #[test]
    fn schema_probe_ok_when_required_columns_present() {
        let p = std::env::temp_dir().join(format!("agentos_schema_ok_{}.db", std::process::id()));
        write_tasks_db(&p, "id INTEGER, status TEXT, consecutive_failures INTEGER, extra TEXT");
        assert!(matches!(probe_fleet_schema(&p), SchemaCheck::Ok));
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn schema_probe_flags_drift_when_a_column_is_dropped() {
        // `consecutive_failures` gone → FLEET_SQL would error → loud Drift, not silent zeros.
        let p = std::env::temp_dir().join(format!("agentos_schema_drift_{}.db", std::process::id()));
        write_tasks_db(&p, "id INTEGER, status TEXT");
        assert!(matches!(probe_fleet_schema(&p), SchemaCheck::Drift(_)));
        let _ = fs::remove_file(&p);
    }

    #[test]
    fn schema_probe_absent_when_db_missing() {
        // No DB yet (Hermes not up) is benign, NOT drift.
        let p = std::env::temp_dir().join(format!("agentos_schema_absent_{}.db", std::process::id()));
        let _ = fs::remove_file(&p);
        assert!(matches!(probe_fleet_schema(&p), SchemaCheck::Absent(_)));
    }

    #[test]
    fn harden_feed_dir_forces_private_0700() {
        use std::os::unix::fs::PermissionsExt;
        // A world-readable runtime dir (what create_dir_all yields under a 022 umask)
        // must be tightened to owner-only — the feed files carry fleet/desktop state.
        let dir = std::env::temp_dir().join(format!("agentos_harden_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o755)).unwrap();
        // We own it (just created it), so harden tightens rather than refusing.
        harden_feed_dir(&dir).unwrap();
        let mode = fs::metadata(&dir).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o700, "feed dir must be private to the owner");
        let _ = fs::remove_dir_all(&dir);
    }
}

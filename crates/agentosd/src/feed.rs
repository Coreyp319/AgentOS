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

/// Pure mapping: fleet counts + optional gateway info + pending-approval count →
/// the ambient feed. Precedence: **needs_you > snag > working > idle** — a pending
/// approval means the agent is blocked waiting on YOU, the most actionable signal.
pub fn derive_feed(fleet: &FleetCounts, gw: Option<&GatewayInfo>, needs_you: u32) -> AgentFeed {
    let active = fleet.running + gw.map(|g| g.active_agents).unwrap_or(0);
    // A pending approval lives only in the gateway's RAM (P2 recon), so a crashed
    // gateway could leave a stale needs_you.json. Only honour it while the gateway
    // is actually alive; the plugin also rewrites an empty file on clean startup.
    let gateway_alive = gw
        .map(|g| matches!(g.gateway_state.as_str(), "running" | "starting" | "degraded"))
        .unwrap_or(false);
    let pending = if gateway_alive { needs_you } else { 0 };

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

fn read_fleet(db: &Path) -> rusqlite::Result<FleetCounts> {
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

#[derive(Deserialize, Default)]
struct GatewayFile {
    #[serde(default)]
    gateway_state: String,
    #[serde(default)]
    active_agents: u32,
}

fn read_gateway(path: &Path) -> Option<GatewayInfo> {
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
fn read_needs_you(path: &Path) -> u32 {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str::<NeedsYouFile>(&s).ok())
        .map(|n| n.pending)
        .unwrap_or(0)
}

fn current_uid() -> u32 {
    fs::metadata("/proc/self").map(|m| m.uid()).unwrap_or(1000)
}

/// `$XDG_RUNTIME_DIR/nimbus-aurora` (created if absent), with the `/run/user/<uid>`
/// fallback the existing bridges use.
fn feed_dir() -> std::io::Result<PathBuf> {
    let runtime = std::env::var("XDG_RUNTIME_DIR")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| format!("/run/user/{}", current_uid()));
    let dir = PathBuf::from(runtime).join("nimbus-aurora");
    fs::create_dir_all(&dir)?;
    Ok(dir)
}

fn hermes_path(file: &str) -> PathBuf {
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

fn state_word(s: u8) -> &'static str {
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
    println!("agentosd feed → {}", out.display());

    let mut last: Option<AgentFeed> = None;
    loop {
        let fleet = read_fleet(&kanban).unwrap_or_default();
        let gw = read_gateway(&gateway);
        let needs_you = read_needs_you(&needs_you_path);
        let feed = derive_feed(&fleet, gw.as_ref(), needs_you);

        let changed = last.as_ref() != Some(&feed);
        if changed || once {
            match write_feed(&dir, &feed) {
                Ok(()) if changed => println!(
                    "[{}] {} (run {}, snag {}, pend {}/{}, approve {}, gw {}) → {}",
                    crate::now_hms(),
                    state_word(feed.state),
                    fleet.running,
                    fleet.snagged,
                    fleet.pending,
                    fleet.total,
                    needs_you,
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
            derive_feed(&FleetCounts::default(), None, 0),
            AgentFeed { state: 0, busy: 0.0, warm: 0.0, snag: 0.0 }
        );
    }

    #[test]
    fn one_running_task_reads_as_working() {
        assert_eq!(
            derive_feed(&counts(1, 0), None, 0),
            AgentFeed { state: 1, busy: 0.7, warm: 0.0, snag: 0.0 }
        );
    }

    #[test]
    fn busy_scales_then_saturates() {
        assert_eq!(derive_feed(&counts(2, 0), None, 0).busy, 0.8);
        assert_eq!(derive_feed(&counts(3, 0), None, 0).busy, 0.9);
        assert_eq!(derive_feed(&counts(4, 0), None, 0).busy, 1.0);
        assert_eq!(derive_feed(&counts(10, 0), None, 0).busy, 1.0);
    }

    #[test]
    fn gateway_chat_turns_count_as_working() {
        // No kanban tasks, but the gateway is actively processing 2 turns.
        assert_eq!(
            derive_feed(&FleetCounts::default(), Some(&gw(2)), 0),
            AgentFeed { state: 1, busy: 0.8, warm: 0.0, snag: 0.0 }
        );
    }

    #[test]
    fn snag_takes_precedence_over_working() {
        // Busy AND snagged → snag wins, busy is forced to 0.
        assert_eq!(
            derive_feed(&counts(3, 1), Some(&gw(5)), 0),
            AgentFeed { state: 4, busy: 0.0, warm: 0.0, snag: 0.6 }
        );
    }

    #[test]
    fn snag_scales() {
        assert_eq!(derive_feed(&counts(0, 1), None, 0).snag, 0.6);
        assert_eq!(derive_feed(&counts(0, 4), None, 0).snag, 0.9);
    }

    #[test]
    fn pending_approval_lights_needs_you_warm() {
        // gateway alive + 1 pending approval → the warm needs_you bloom (state 2).
        assert_eq!(
            derive_feed(&FleetCounts::default(), Some(&gw(0)), 1),
            AgentFeed { state: 2, busy: 0.0, warm: 0.75, snag: 0.0 }
        );
    }

    #[test]
    fn needs_you_outranks_snag_and_working() {
        // busy AND snagged AND a pending approval → needs_you wins; busy+snag forced 0.
        assert_eq!(
            derive_feed(&counts(3, 2), Some(&gw(5)), 2),
            AgentFeed { state: 2, busy: 0.0, warm: 0.8, snag: 0.0 }
        );
    }

    #[test]
    fn needs_you_suppressed_when_gateway_not_alive() {
        // a stale needs_you.json (gateway stopped / file missing) must NOT raise warm.
        assert_eq!(derive_feed(&FleetCounts::default(), Some(&gw_state("stopped", 0)), 3).warm, 0.0);
        assert_eq!(derive_feed(&FleetCounts::default(), None, 3).warm, 0.0);
        // and it falls through to the real state — a running task still reads as working.
        assert_eq!(derive_feed(&counts(1, 0), Some(&gw_state("stopped", 0)), 3).state, 1);
    }

    #[test]
    fn warm_zero_without_pending_approval() {
        // with no pending approval, warm stays 0 across all working/snag combos.
        for running in 0..6 {
            for snagged in 0..6 {
                assert_eq!(derive_feed(&counts(running, snagged), Some(&gw(running)), 0).warm, 0.0);
            }
        }
    }

    #[test]
    fn serializes_to_the_exact_spike_contract() {
        let f = AgentFeed { state: 1, busy: 0.7, warm: 0.0, snag: 0.0 };
        assert_eq!(
            serde_json::to_string(&f).unwrap(),
            r#"{"state":1,"busy":0.7,"warm":0.0,"snag":0.0}"#
        );
    }
}

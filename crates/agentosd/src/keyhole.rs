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
//! Output contract — schema 1, consumed by `spikes/keyhole/contents/ui/KeyholeModel.qml`:
//!   {"schema":1,"state":…,"gateway":…,"floats":{busy,warm,snag},
//!    "fleet":{running,queued,snagged},          // -1 == unknown (NOT zero)
//!    "lease":{tier,holder,preempt},             // empty string == no datum
//!    "vram":{used_mib,total_mib},               // -1 == unknown
//!    "residency":[{name,loaded_secs}],
//!    "tokens_per_sec":null}                      // null == UNKNOWN, never synthesized (P2: proxy)

use std::collections::HashMap;
use std::fs;
use std::path::Path;
use std::thread;
use std::time::{Duration, Instant};

use nvml_wrapper::Nvml;
use serde::{Deserialize, Serialize};

use crate::feed::{
    derive_feed, feed_dir, hermes_path, read_fleet, read_gateway, read_needs_you, state_word,
    FleetCounts,
};

/// Bump on any breaking shape change to `keyhole.json`. Pinned by `pins_the_exact_contract`.
const SCHEMA: u32 = 1;

/// Negative sentinel for an unreadable integer datum — distinct from a real `0` (ADR-0012 §4).
const UNK: i64 = -1;

const OLLAMA_PS: &str = "http://127.0.0.1:11434/api/ps";

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
    /// `None` serializes to `null` — UNKNOWN. Never synthesized in v1 (needs the ADR-0002 proxy).
    pub tokens_per_sec: Option<f64>,
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

/// Optional lease mirror (`$XDG_RUNTIME_DIR/nimbus-aurora/lease.json`), pushed off-lock by the
/// `lease` daemon (integration TODO; ADR-0012 §3). Absent/unparseable → empty (no contention).
fn read_lease(path: &Path) -> Lease {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str::<Lease>(&s).ok())
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
        let af = derive_feed(&counts, gw, needs_you);
        (state_word(af.state).to_string(), Floats { busy: af.busy, warm: af.warm, snag: af.snag })
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
        let (state, gateway_word, floats, fleet) = derive_legible(&fleet_res, gw.as_ref(), needs_you);

        let t_nvml = Instant::now();
        let vram = read_vram(nvml.as_ref());
        let nvml_read = t_nvml.elapsed();

        let residency = read_residency(&http, &mut first_seen, now);
        let lease = read_lease(&lease_mirror);

        let feed = KeyholeFeed {
            schema: SCHEMA,
            state: state.clone(),
            gateway: gateway_word,
            floats,
            fleet,
            lease,
            vram,
            residency,
            tokens_per_sec: None, // P2: summed from the ADR-0002 proxy stream. Never faked.
        };

        let changed = last.as_ref() != Some(&feed);
        if changed || once {
            match write_keyhole(&dir, &feed) {
                Ok(()) if changed => println!(
                    "[{}] {} (gw {}, run {}, vram {}/{}M, models {}, lease {})",
                    crate::now_hms(),
                    feed.state,
                    feed.gateway,
                    feed.fleet.running,
                    feed.vram.used_mib,
                    feed.vram.total_mib,
                    feed.residency.len(),
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
        let (state, gateway, floats, _) = derive_legible(&Ok(counts(0, 0, 0)), None, 0);
        assert_eq!(state, "unknown");
        assert_eq!(gateway, "unknown");
        assert_eq!(floats, Floats { busy: 0.0, warm: 0.0, snag: 0.0 });
    }

    #[test]
    fn unreadable_kanban_shows_sentinel_counts_not_zero() {
        // A real 0 and "couldn't read" must be distinguishable downstream.
        let err: rusqlite::Result<FleetCounts> = Err(rusqlite::Error::InvalidQuery);
        let (_, _, _, fleet) = derive_legible(&err, Some(&gw("running", 0)), 0);
        assert_eq!(fleet, Fleet { running: UNK, queued: UNK, snagged: UNK });
    }

    #[test]
    fn alive_gateway_mirrors_the_wallpaper_grammar() {
        // working: a running task reads as working with the same ramp the shader gets.
        let (state, gateway, floats, fleet) =
            derive_legible(&Ok(counts(1, 0, 2)), Some(&gw("running", 0)), 0);
        assert_eq!(state, "working");
        assert_eq!(gateway, "running");
        assert_eq!(floats.busy, 0.7);
        assert_eq!(fleet, Fleet { running: 1, queued: 2, snagged: 0 });

        // needs_you outranks, and is the one warm signal.
        let (state, _, floats, _) = derive_legible(&Ok(counts(3, 1, 0)), Some(&gw("running", 0)), 1);
        assert_eq!(state, "needs_you");
        assert!(floats.warm > 0.0 && floats.busy == 0.0 && floats.snag == 0.0);

        // snag reads calm (cool), never as working.
        let (state, _, floats, _) = derive_legible(&Ok(counts(0, 1, 0)), Some(&gw("running", 0)), 0);
        assert_eq!(state, "snag");
        assert!(floats.snag > 0.0);
    }

    #[test]
    fn stopped_gateway_is_idle_with_truthful_gateway_field() {
        let (state, gateway, floats, _) =
            derive_legible(&Ok(counts(0, 0, 0)), Some(&gw("stopped", 0)), 0);
        assert_eq!(state, "idle");
        assert_eq!(gateway, "stopped"); // the consumer can render "off" from this later
        assert_eq!(floats, Floats { busy: 0.0, warm: 0.0, snag: 0.0 });
    }

    #[test]
    fn empty_lease_mirror_parses_to_no_contention() {
        // Absent/blank lease.json → empty strings → the UI shows "no contention", never a guess.
        assert_eq!(serde_json::from_str::<Lease>("{}").unwrap(), Lease::default());
        assert_eq!(
            serde_json::from_str::<Lease>(r#"{"tier":"interactive","holder":"Hermes","preempt":""}"#).unwrap(),
            Lease { tier: "interactive".into(), holder: "Hermes".into(), preempt: String::new() }
        );
    }

    #[test]
    fn pins_the_exact_contract() {
        // The producer/consumer pin (ADR-0012 §2), mirroring feed.rs's exact-string test. If
        // this string changes, bump SCHEMA and update KeyholeModel.qml in lockstep.
        let feed = KeyholeFeed {
            schema: 1,
            state: "working".into(),
            gateway: "running".into(),
            floats: Floats { busy: 0.85, warm: 0.0, snag: 0.0 },
            fleet: Fleet { running: 3, queued: 2, snagged: 0 },
            lease: Lease {
                tier: "interactive".into(),
                holder: "Hermes".into(),
                preempt: "wallpaper yielded ~1.5GB -> qwen2.5 loaded".into(),
            },
            vram: Vram { used_mib: 6240, total_mib: 8192 },
            residency: vec![Residency { name: "qwen2.5:14b".into(), loaded_secs: 240 }],
            tokens_per_sec: None,
        };
        assert_eq!(
            serde_json::to_string(&feed).unwrap(),
            r#"{"schema":1,"state":"working","gateway":"running","floats":{"busy":0.85,"warm":0.0,"snag":0.0},"fleet":{"running":3,"queued":2,"snagged":0},"lease":{"tier":"interactive","holder":"Hermes","preempt":"wallpaper yielded ~1.5GB -> qwen2.5 loaded"},"vram":{"used_mib":6240,"total_mib":8192},"residency":[{"name":"qwen2.5:14b","loaded_secs":240}],"tokens_per_sec":null}"#
        );
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
            tokens_per_sec: None,
        };
        let s = serde_json::to_string(&feed).unwrap();
        assert!(s.contains(r#""fleet":{"running":-1,"queued":-1,"snagged":-1}"#));
        assert!(s.contains(r#""vram":{"used_mib":-1,"total_mib":-1}"#));
        assert!(s.contains(r#""tokens_per_sec":null"#));
        assert!(s.contains(r#""residency":[]"#));
    }
}

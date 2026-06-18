//! REFERENCE SKETCH — NOT wired into the crate. Illustrates the agentosd `wind` sink:
//! the D-Bus method the KWin script's `callDBus` targets, plus the deterministic low-pass
//! and atomic write to `wind.json`. Lives in spikes/ on purpose: adding a session-bus
//! SERVER to a producer is a STRUCTURAL change (the `feed`/`keyhole` producers are pure
//! synchronous file writers; only `lease` serves D-Bus, on tokio/zbus). This sketch shows
//! the smallest honest shape; the spec (§"structural cost") flags the decision for the panel.
//!
//! Two viable homes (spec §6 discusses):
//!   A) fold `org.agentos.Wind1` into the EXISTING `lease` daemon (already a zbus server on
//!      the session bus, already owns `org.agentos.Coordinator1`) — near-zero new surface;
//!   B) a standalone `agentosd wind` subcommand (new zbus server) — cleaner separation, but
//!      a second always-on D-Bus service. (A) is the recommended path.
//!
//! The math here is a critically-damped spring matching feed.rs's eased-ramp intent
//! (ambient omega ~1–2). It runs on a fixed tick, NOT per D-Bus call, so the file eases
//! smoothly even between drag steps and decays to neutral after a drag with no extra events.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// The neutral resting wind. dir points "up" in screen space (y down+, so up is -1),
/// gust 0. Idle / no producer / stale all resolve here (fail-open, ADR-0003).
const NEUTRAL_DIR: [f64; 2] = [0.0, -1.0];

/// Spring stiffness (omega). ~1.5 rad/s ⇒ a drag's nudge eases back over ~1.5–2 s — below
/// the attention-capture threshold (spec §"calm constraint"). Same family as feed.rs:88-97.
const OMEGA: f64 = 1.5;

/// A drag ended this long ago with no new step ⇒ force `active=false` even if Finished was
/// missed (a window destroyed mid-drag never fires Finished). Belt-and-suspenders fail-open.
const ACTIVE_TIMEOUT_S: f64 = 0.30;

/// The eased, written state. The KWin script feeds RAW targets into `target_*`; the tick
/// integrates the current `dir`/`gust` toward them.
#[derive(Clone, Copy)]
struct WindState {
    dir: [f64; 2],   // current eased unit direction
    gust: f64,       // current eased gust
    target_dir: [f64; 2],
    target_gust: f64,
    active: bool,
    last_step: f64,  // epoch secs of the last Gust(...) D-Bus call
}

impl Default for WindState {
    fn default() -> Self {
        WindState {
            dir: NEUTRAL_DIR,
            gust: 0.0,
            target_dir: NEUTRAL_DIR,
            target_gust: 0.0,
            active: false,
            last_step: 0.0,
        }
    }
}

impl WindState {
    /// The D-Bus method body: `Gust(dirX, dirY, speed, active)`. Producer-side RAW input.
    /// Validates + clamps (never trust the wire); identity is structurally absent — this is
    /// the ENTIRE payload surface, three floats and a flag (spec §4).
    fn on_gust(&mut self, dir_x: f64, dir_y: f64, speed: f64, active: bool, now: f64) {
        if active {
            // Renormalize defensively; ignore a zero/NaN vector (keep last-good direction).
            let mag = (dir_x * dir_x + dir_y * dir_y).sqrt();
            if mag.is_finite() && mag > 1e-6 {
                self.target_dir = [dir_x / mag, dir_y / mag];
            }
            self.target_gust = speed.clamp(0.0, 1.0);
            self.active = true;
            self.last_step = now;
        } else {
            // Drag finished: keep last-good direction, decay gust to 0.
            self.target_gust = 0.0;
            self.active = false;
        }
    }

    /// Fixed-tick integration toward target. `dt` is the tick period (e.g. 1/60 s). Critically
    /// damped first-order lerp: x += (target - x) * (1 - e^{-omega·dt}). Deterministic.
    fn tick(&mut self, dt: f64, now: f64) {
        // Missed-Finished guard: no step for a while ⇒ treat as inactive, ease gust down.
        if self.active && now - self.last_step > ACTIVE_TIMEOUT_S {
            self.active = false;
            self.target_gust = 0.0;
        }
        let a = 1.0 - (-OMEGA * dt).exp();
        self.gust += (self.target_gust - self.gust) * a;
        for k in 0..2 {
            self.dir[k] += (self.target_dir[k] - self.dir[k]) * a;
        }
        // When gust has eased to ~0, relax direction back to neutral so a long-idle desktop
        // sits at the canonical resting wind (keeps `idle` byte-identical for the shader).
        if self.gust < 0.02 {
            for k in 0..2 {
                self.dir[k] += (NEUTRAL_DIR[k] - self.dir[k]) * a;
            }
        }
    }

    fn to_json(&self, now: f64) -> String {
        // Round like feed.rs (round3) so an idle frame is a stable, diffable string.
        let r3 = |x: f64| (x * 1000.0).round() / 1000.0;
        format!(
            "{{\"schema\":1,\"dir\":[{},{}],\"gust\":{},\"active\":{},\"updated_at\":{}}}\n",
            r3(self.dir[0]), r3(self.dir[1]), r3(self.gust), self.active, r3(now)
        )
    }
}

/// Atomic write — dot-prefixed temp + rename, identical discipline to feed::write_feed so a
/// `*.json` poller never catches a half-written file.
fn write_wind(dir: &Path, json: &str) -> std::io::Result<()> {
    let tmp = dir.join(format!(".wind.{}.tmp", std::process::id()));
    fs::write(&tmp, json)?;
    fs::rename(&tmp, dir.join("wind.json"))
}

fn _now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// Sketch of the run loop. In the real build the D-Bus server (zbus) drives `on_gust` from
/// the `Gust` method handler; a 60 Hz tick task drives `tick` + `write_wind`. Edge-write is
/// fine (only write when the rounded string changes) so an idle desktop stops touching the
/// file — the consumer holds last-good, which is already neutral.
fn _run(_dir: PathBuf) { /* see homes (A)/(B) above */ }

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn idle_is_exactly_neutral_and_stable() {
        let s = WindState::default();
        // The canonical idle frame the shader diffs against. If this string ever drifts,
        // the "idle byte-identical" claim (spec §5) is broken — pin it.
        assert_eq!(s.to_json(1750000000.0),
            "{\"schema\":1,\"dir\":[0,-1],\"gust\":0,\"active\":false,\"updated_at\":1750000000}\n");
    }

    #[test]
    fn a_drag_nudges_then_eases_back_to_neutral() {
        let mut s = WindState::default();
        s.on_gust(-1.0, 0.0, 0.8, true, 0.0);           // shove left, hard
        for _ in 0..30 { s.tick(1.0 / 60.0, 0.1); }      // ~0.5 s of integration
        assert!(s.gust > 0.3, "gust should have risen toward the shove");
        assert!(s.dir[0] < -0.5, "direction should lean left");

        s.on_gust(0.0, 0.0, 0.0, false, 1.0);           // drag ends
        for i in 0..300 { s.tick(1.0 / 60.0, 1.0 + i as f64 / 60.0); }  // ~5 s of ease-back
        assert!(s.gust < 0.02, "gust must ease back to ~0 (calm)");
        assert!((s.dir[0] - NEUTRAL_DIR[0]).abs() < 0.05, "direction relaxes to neutral");
    }

    #[test]
    fn missed_finished_still_calms() {
        // A window destroyed mid-drag never fires Finished. The active-timeout must calm it.
        let mut s = WindState::default();
        s.on_gust(1.0, 0.0, 1.0, true, 0.0);
        for i in 0..120 { s.tick(1.0 / 60.0, i as f64 / 60.0); } // ticks past ACTIVE_TIMEOUT_S
        assert!(!s.active, "stale active must flip false without a Finished event");
        assert!(s.gust < 0.05, "and gust eases down on its own");
    }

    #[test]
    fn payload_carries_no_identity() {
        // Compile-time proof of the privacy posture: on_gust's signature is the entire input
        // surface and it is (f64,f64,f64,bool,f64) — there is no String/title/pid parameter
        // to receive an identity even if the producer tried to send one.
        let mut s = WindState::default();
        s.on_gust(0.0, -1.0, 0.5, true, 0.0); // four scalars; nothing nameable
        let _ = s;
    }
}

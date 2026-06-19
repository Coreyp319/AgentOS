//! `wind` — the live window-drag → ambient-wind sink (Design 0023, ADR-0023 P1).
//!
//! The server half of the window-drag→wind loop: it serves `org.agentos.Wind1` on the
//! session bus (a KWin script's `callDBus` targets the `Gust(...)` method), runs a
//! deterministic first-order spring on a fixed tick, and atomically publishes
//! `$XDG_RUNTIME_DIR/nimbus-aurora/wind.json` for the reactive-wallpaper shader to poll.
//! Ported from `spikes/window-drag-wind/wind_sink_sketch.rs`.
//!
//! **Home (A) — folded into the `lease` daemon** (Design 0023 §3.1). The crate keeps "one
//! D-Bus server": the lease daemon already runs a zbus session-bus server + a tokio reactor,
//! so the wind interface + tick task are mounted alongside `org.agentos.Coordinator1` rather
//! than spinning up a second always-on service. The mounting is done by `attach()` (called
//! from `lease::serve`).
//!
//! **Load-bearing safety property (Design 0023 §3.1 / open-Q4, ADR-0003):** the 60 Hz wind
//! tick must NEVER be able to delay a preemption SIGKILL. That is guaranteed *structurally*
//! here, not by discipline:
//!   * This module owns its OWN state — `Arc<Mutex<WindState>>` — which is a DIFFERENT mutex
//!     from `lease::Inner`'s arbitration lock. Nothing in this file can name, reach, or lock
//!     `Inner`; there is no import of it and no field carrying it.
//!   * `Gust` (the D-Bus handler) and the tick task only ever take the wind mutex, held for a
//!     few field assignments — never across an `.await`, never across the file write.
//!   * The atomic write happens OFF the wind mutex (snapshot under the lock, write after drop).
//!
//! So the worst a misbehaving 60 Hz tick can do is contend its own tiny lock; the lease lock —
//! and therefore the SIGKILL path — is untouchable from here. The `no_inner_lock` test pins
//! this by asserting the wind path takes nothing but its own `WindState`.
//!
//! **Neutral-vector contract (the consumer's mapping, stated for the contract):** the shader
//! computes `windDir = dir · gust`, so rest (`gust → 0`) ⇒ wind `(0,0)` regardless of `dir`.
//! The sink therefore only has to ease `gust` to exactly 0 at rest (it does) and keep the idle
//! `wind.json` frame byte-stable (edge-write: it stops touching the file once neutral). `dir`
//! is held at its canonical resting value `[0,-1]` so the idle string is the pinned anchor.
//!
//! **Fail-open (ADR-0003):** every failure mode resolves to neutral wind. A NaN/zero gust
//! vector keeps the last-good direction; a missed `Finished` is caught by `ACTIVE_TIMEOUT_S`;
//! a failed file write is a best-effort no-op (the consumer holds last-good, already neutral).

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use tokio::sync::Mutex;

/// The well-known interface + object path the KWin producer's `callDBus` targets. Served on the
/// SAME connection as `org.agentos.Coordinator1` (home A) — one bus name (`org.agentos.Coordinator1`)
/// already owned by the lease daemon, a second object path mounted here.
pub const WIND_OBJ_PATH: &str = "/org/agentos/Wind";

/// The neutral resting wind. `dir` points "up" in screen space (Wayland y down+, so up is -1),
/// `gust` 0. Idle / no producer / stale all resolve here (fail-open, ADR-0003). Because the
/// consumer computes `windDir = dir·gust`, gust 0 makes this resolve to no wind regardless.
const NEUTRAL_DIR: [f64; 2] = [0.0, -1.0];

/// Spring stiffness (omega). ~1.5 rad/s ⇒ a drag's nudge eases back over ~1.5–2 s — below the
/// attention-capture threshold (Design 0023 §3 "calm constraint"). Same eased family as the
/// reactive-wallpaper consumer.
const OMEGA: f64 = 1.5;

/// Fixed tick period (~60 Hz). The spring integrates toward the last `Gust` target every tick so
/// the file eases smoothly *between* drag steps and decays to neutral after a drag with no events.
const TICK: Duration = Duration::from_millis(16);
const TICK_DT: f64 = 0.016;

/// A drag ended this long ago with no new step ⇒ force `active=false` even if `Finished` was missed
/// (a window destroyed mid-drag never fires `Finished`). Belt-and-suspenders fail-open (Design 0023 §5).
const ACTIVE_TIMEOUT_S: f64 = 0.30;

/// Below this eased gust we treat the wind as "at rest": snap gust to exactly 0 (so `windDir = dir·gust`
/// is exactly `(0,0)`) and relax `dir` back to neutral so a long-idle desktop sits at the pinned anchor.
const REST_GUST: f64 = 0.02;

// ---------------------------------------------------------------------------
// Pure spring state (the only new decision logic — unit-tested below). Owns NOTHING from the
// lease daemon: it is a plain value, integrated on a tick, with no path to `Inner`.
// ---------------------------------------------------------------------------

/// The eased, written state. The `Gust` handler feeds RAW targets into `target_*`; the tick
/// integrates the current `dir`/`gust` toward them. Pure + deterministic.
#[derive(Clone, Copy, Debug)]
pub struct WindState {
    dir: [f64; 2],   // current eased unit direction
    gust: f64,       // current eased gust ∈ [0,1]
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
    /// The `Gust(dirX, dirY, speed, active)` method body — RAW producer input. Validates + clamps
    /// (never trust the wire): a NaN/zero direction vector is ignored (keep last-good); `speed` is
    /// clamped to `[0,1]`. Identity is structurally absent — three floats and a flag is the ENTIRE
    /// payload surface (Design 0023 §4). Pure; the time source is injected for tests.
    pub fn on_gust(&mut self, dir_x: f64, dir_y: f64, speed: f64, active: bool, now: f64) {
        if active {
            // Renormalize defensively; ignore a zero/NaN vector (keep last-good direction).
            let mag = (dir_x * dir_x + dir_y * dir_y).sqrt();
            if mag.is_finite() && mag > 1e-6 {
                self.target_dir = [dir_x / mag, dir_y / mag];
            }
            // `clamp(0,1)` maps NaN→? — guard it: a non-finite speed reads as 0 (calm), never a hold.
            self.target_gust = if speed.is_finite() { speed.clamp(0.0, 1.0) } else { 0.0 };
            self.active = true;
            self.last_step = now;
        } else {
            // Drag finished: keep last-good direction, decay gust to 0.
            self.target_gust = 0.0;
            self.active = false;
        }
    }

    /// Fixed-tick integration toward target. `dt` is the tick period. Critically-damped first-order
    /// lerp: `x += (target − x)·(1 − e^{−ω·dt})`. Deterministic. The missed-`Finished` guard calms a
    /// stale-active state on its own.
    pub fn tick(&mut self, dt: f64, now: f64) {
        // Missed-Finished guard: no step for a while ⇒ treat as inactive, ease gust down.
        if self.active && now - self.last_step > ACTIVE_TIMEOUT_S {
            self.active = false;
            self.target_gust = 0.0;
        }
        let a = 1.0 - (-OMEGA * dt).exp();
        self.gust += (self.target_gust - self.gust) * a;
        for (d, t) in self.dir.iter_mut().zip(self.target_dir) {
            *d += (t - *d) * a;
        }
        // Settling toward rest (target eased to ~0) and the eased gust has crossed the rest band:
        // SNAP gust to exactly 0 so `windDir = dir·gust` is exactly `(0,0)` (the neutral-vector
        // contract), and relax direction back to neutral so a long-idle desktop sits at the canonical
        // resting wind (idle frame byte-identical). Gated on `target_gust` so a LIVE drag — whose gust
        // legitimately ramps UP through this band — is never zeroed mid-rise (only a decay is snapped).
        if self.target_gust < REST_GUST && self.gust < REST_GUST {
            self.gust = 0.0;
            // Point the direction target at neutral too, so the main lerp above eases dir toward neutral
            // on the NEXT tick instead of holding the last drag's heading (otherwise the two pulls fight
            // and dir settles mid-way — the idle anchor would never be reached).
            self.target_dir = NEUTRAL_DIR;
            for (d, n) in self.dir.iter_mut().zip(NEUTRAL_DIR) {
                *d += (n - *d) * a;
            }
        }
    }

    /// Render the schema-1 frame. Round like `feed.rs` (`round3`) so an idle frame is a stable,
    /// diffable string — the edge-write + idle-anchor contract depends on this being deterministic.
    pub fn to_json(self, now: f64) -> String {
        let r3 = |x: f64| (x * 1000.0).round() / 1000.0;
        format!(
            "{{\"schema\":1,\"dir\":[{},{}],\"gust\":{},\"active\":{},\"updated_at\":{}}}\n",
            r3(self.dir[0]), r3(self.dir[1]), r3(self.gust), self.active, r3(now)
        )
    }

    /// The body of the frame WITHOUT `updated_at` — the part that drives the edge-write decision.
    /// Two frames with identical bodies differ only by timestamp, so re-writing them would just
    /// churn the file (and wake the consumer's poller) without changing the wind. Comparing the
    /// body lets an idle desktop stop touching the file entirely.
    fn body(&self) -> String {
        let r3 = |x: f64| (x * 1000.0).round() / 1000.0;
        format!(
            "{{\"schema\":1,\"dir\":[{},{}],\"gust\":{},\"active\":{}",
            r3(self.dir[0]), r3(self.dir[1]), r3(self.gust), self.active
        )
    }

    /// Is the state exactly the neutral resting wind? Used to make the idle edge-write a no-op.
    fn is_neutral(&self) -> bool {
        self.gust == 0.0
            && !self.active
            && self.dir[0] == NEUTRAL_DIR[0]
            && self.dir[1] == NEUTRAL_DIR[1]
    }
}

/// Atomic write — dot-prefixed temp + rename, identical discipline to `feed::write_feed`
/// (`feed.rs:235-241`) so a `*.json` poller never catches a half-written file. Best-effort /
/// fail-open: a failed write leaves the consumer holding last-good (already neutral after any drag).
fn write_wind(dir: &Path, json: &str) -> std::io::Result<()> {
    let tmp = dir.join(format!(".wind.{}.tmp", std::process::id()));
    std::fs::write(&tmp, json)?;
    std::fs::rename(&tmp, dir.join("wind.json"))
}

fn now_epoch() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

// ---------------------------------------------------------------------------
// D-Bus shell (zbus) — `org.agentos.Wind1`, mounted on the lease daemon's connection.
// ---------------------------------------------------------------------------

/// The `org.agentos.Wind1` sink. Holds ONLY its own wind state — no reference to `lease::Inner`,
/// the arbitration lock, or any reclaim handle exists in this type, so a `Gust` call provably
/// cannot touch the SIGKILL path (Design 0023 §3.1).
pub struct WindSink {
    state: Arc<Mutex<WindState>>,
}

#[zbus::interface(name = "org.agentos.Wind1")]
impl WindSink {
    /// `Gust(dirX, dirY, speed, active)` — the method the KWin window-drag script's `callDBus`
    /// targets. RAW producer input: `on_gust` renormalizes + clamps it (no NaN, unit-ish dir,
    /// gust ∈ [0,1]). Returns nothing (fire-and-forget on the producer side). Takes ONLY the wind
    /// mutex, never the lease lock — held across three field writes, dropped before this returns.
    async fn gust(&self, dir_x: f64, dir_y: f64, speed: f64, active: bool) {
        let mut s = self.state.lock().await;
        s.on_gust(dir_x, dir_y, speed, active, now_epoch());
    }
}

/// The fixed-tick spring + edge-write loop. A SEPARATE tokio task (spawned by `attach`) that owns
/// only `Arc<Mutex<WindState>>` and the output dir — it has no handle to the lease daemon at all.
/// Each tick: lock the wind mutex briefly to integrate + snapshot, DROP it, then (only if the frame
/// body changed) atomically write off-lock. Idle edge-write: once neutral, the body stops changing,
/// so an idle desktop stops touching the file (no wakeful spin, Design 0023 open-Q4).
async fn tick_loop(state: Arc<Mutex<WindState>>, dir: PathBuf) {
    // Track the last-written body so we only rewrite when the rounded frame actually changes. Seed
    // with a sentinel that never matches a real body, so the first non-neutral frame always writes.
    let mut last_body: Option<String> = None;
    let mut interval = tokio::time::interval(TICK);
    loop {
        interval.tick().await;
        let now = now_epoch();
        // Brief critical section: integrate + snapshot the body/json, then DROP the lock before any
        // I/O. The lock is the wind mutex ONLY — the lease lock is unreachable from here.
        let (body, json, neutral) = {
            let mut s = state.lock().await;
            s.tick(TICK_DT, now);
            (s.body(), s.to_json(now), s.is_neutral())
        };
        // Idle edge-write: when at neutral AND we've already written a neutral frame, don't rewrite —
        // a present-but-old file already reads calm (the consumer holds last-good). Otherwise write
        // only when the body changed (timestamp-only deltas don't move the wind).
        let already_neutral = neutral && last_body.as_deref() == Some(body.as_str());
        if !already_neutral && last_body.as_deref() != Some(body.as_str()) {
            let _ = write_wind(&dir, &json); // best-effort / fail-open
            last_body = Some(body);
        }
    }
}

/// Mount the `org.agentos.Wind1` sink on an EXISTING zbus connection (the lease daemon's) and spawn
/// the fixed-tick spring task. Home (A): no new bus name, no second service. `out_dir` is the
/// `$XDG_RUNTIME_DIR/nimbus-aurora` directory `feed`/`keyhole`/`lease` already publish into; `None`
/// (runtime dir unavailable) ⇒ the interface still serves but the writer is skipped (fail-open —
/// the desktop simply never sees a `wind.json`, holding neutral).
///
/// Best-effort: a failure to add the interface is logged, never fatal — the lease daemon (the
/// safety substrate) keeps serving `org.agentos.Coordinator1` regardless (ADR-0003).
pub async fn attach(conn: &zbus::Connection, out_dir: Option<PathBuf>) {
    let state = Arc::new(Mutex::new(WindState::default()));
    let sink = WindSink { state: Arc::clone(&state) };
    match conn.object_server().at(WIND_OBJ_PATH, sink).await {
        Ok(_) => {
            println!(
                "agentosd coordd — wind sink serving org.agentos.Wind1 at {WIND_OBJ_PATH} (Design 0023)"
            );
            if let Some(dir) = out_dir {
                // Publish an initial neutral frame so the consumer has a file to read from tick one,
                // then ease from it. The tick loop's edge-write keeps it stable while idle.
                let _ = write_wind(&dir, &WindState::default().to_json(now_epoch()));
                tokio::spawn(tick_loop(state, dir));
            } else {
                eprintln!(
                    "agentosd coordd — wind sink: no runtime dir, writer disabled (interface still serves; neutral)"
                );
            }
        }
        Err(e) => {
            // Fail-open: the wind feed is a nicety; never let it wedge the coordinator.
            eprintln!("agentosd coordd — wind sink could not mount (fail-open, wind stays neutral): {e}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // --- ported from wind_sink_sketch.rs ---

    #[test]
    fn idle_is_exactly_neutral_and_stable() {
        let s = WindState::default();
        // The canonical idle frame the shader diffs against. If this string ever drifts, the
        // "idle byte-identical" claim (Design 0023 §5) is broken — pin it.
        assert_eq!(
            s.to_json(1750000000.0),
            "{\"schema\":1,\"dir\":[0,-1],\"gust\":0,\"active\":false,\"updated_at\":1750000000}\n"
        );
    }

    #[test]
    fn a_drag_nudges_then_eases_back_to_neutral() {
        let mut s = WindState::default();
        s.on_gust(-1.0, 0.0, 0.8, true, 0.0);            // shove left, hard
        for _ in 0..30 { s.tick(1.0 / 60.0, 0.1); }       // ~0.5 s of integration
        assert!(s.gust > 0.3, "gust should have risen toward the shove");
        assert!(s.dir[0] < -0.5, "direction should lean left");

        s.on_gust(0.0, 0.0, 0.0, false, 1.0);            // drag ends
        for i in 0..300 { s.tick(1.0 / 60.0, 1.0 + i as f64 / 60.0); } // ~5 s of ease-back
        assert_eq!(s.gust, 0.0, "gust must ease back to EXACTLY 0 (calm; windDir=dir·gust ⇒ (0,0))");
        assert!((s.dir[0] - NEUTRAL_DIR[0]).abs() < 0.05, "direction relaxes to neutral");
    }

    #[test]
    fn missed_finished_still_calms() {
        // A window destroyed mid-drag never fires Finished. The active-timeout must calm it.
        let mut s = WindState::default();
        s.on_gust(1.0, 0.0, 1.0, true, 0.0);
        // First few ticks cross ACTIVE_TIMEOUT_S → stale-active flips false on its own...
        for i in 0..30 { s.tick(1.0 / 60.0, i as f64 / 60.0); }
        assert!(!s.active, "stale active must flip false without a Finished event");
        // ...and with no further events the gust decays all the way back to EXACTLY 0 (calm).
        for i in 30..300 { s.tick(1.0 / 60.0, i as f64 / 60.0); }
        assert_eq!(s.gust, 0.0, "and gust eases down to exactly 0 on its own");
    }

    // --- the three contract tests this slice owes (ADR-0023 P1, item 5) ---

    #[test]
    fn gust_clamps_renormalizes_and_rejects_nan() {
        let mut s = WindState::default();
        // gust > 1 clamps to 1.
        s.on_gust(0.0, -1.0, 5.0, true, 0.0);
        assert_eq!(s.target_gust, 1.0, "speed > 1 clamps to 1");
        // A non-unit direction is renormalized to a unit vector.
        s.on_gust(3.0, 4.0, 0.5, true, 0.0);
        let mag = (s.target_dir[0].powi(2) + s.target_dir[1].powi(2)).sqrt();
        assert!((mag - 1.0).abs() < 1e-9, "direction is renormalized to unit length");
        assert!((s.target_dir[0] - 0.6).abs() < 1e-9 && (s.target_dir[1] - 0.8).abs() < 1e-9);
        // A zero vector is ignored — last-good direction is kept (no NaN from /0).
        let kept = s.target_dir;
        s.on_gust(0.0, 0.0, 0.5, true, 0.0);
        assert_eq!(s.target_dir, kept, "a zero direction keeps the last-good direction (no NaN)");
        // A NaN direction is ignored; a NaN speed reads as calm (0), never a hold.
        s.on_gust(f64::NAN, f64::NAN, f64::NAN, true, 0.0);
        assert_eq!(s.target_dir, kept, "a NaN direction is ignored");
        assert_eq!(s.target_gust, 0.0, "a NaN speed resolves to calm, never propagates");
        // And nothing ever produced a NaN in the live state.
        assert!(s.dir[0].is_finite() && s.dir[1].is_finite() && s.gust.is_finite());
    }

    #[test]
    fn idle_frame_is_byte_stable_across_ticks() {
        // At rest the rounded frame BODY must not change tick-to-tick, so the edge-write stops
        // touching the file (Design 0023 §5 idle byte-stable). Only `updated_at` may differ.
        let mut s = WindState::default();
        let b0 = s.body();
        for i in 0..600 { s.tick(1.0 / 60.0, i as f64 / 60.0); } // 10 s of idle ticking
        assert_eq!(s.body(), b0, "an idle wind's frame body is byte-identical across ticks");
        assert!(s.is_neutral(), "and the state remains exactly neutral");
    }

    /// The load-bearing safety property (Design 0023 §3.1 / open-Q4): the wind path takes NO lease
    /// lock. We assert it structurally — the entire wind pipeline (`Gust` body + `tick`) operates on
    /// a `WindState` value that has no field of, and no path to, `lease::Inner` or its `Mutex`. This
    /// test exercises a full gust→tick→serialize cycle touching ONLY `WindState`; it could not even
    /// be written to touch `Inner`, because nothing in this module can name it. If a future refactor
    /// tried to thread the lease lock in here, this module would have to import it — and this test
    /// (and the `WindSink`-holds-only-`WindState` shape) is the tripwire.
    #[test]
    fn wind_path_takes_no_inner_lock() {
        // A `WindState` is a plain Copy value — provably independent of the arbitration mutex.
        fn assert_plain_value<T: Copy>() {}
        assert_plain_value::<WindState>();
        // Drive the whole pipeline; the only lock a real `Gust`/tick takes is over THIS value.
        let mut s = WindState::default();
        s.on_gust(-1.0, 0.0, 0.7, true, 0.0);
        s.tick(TICK_DT, 0.0);
        let _ = s.to_json(0.0);
        // WindSink — the D-Bus object — carries ONLY Arc<Mutex<WindState>>; assert that's all it can
        // lock by constructing one over the same value the tick uses (no Inner anywhere in scope).
        let _sink = WindSink { state: Arc::new(Mutex::new(s)) };
    }
}

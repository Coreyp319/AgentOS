//! `scene` — the reactive dark-ride DISPOSER (ADR-0030 D2; extends ADR-0029 / ADR-0023 P1).
//!
//! The CONSUMER half of the UE-wallpaper reactive loop. It reads the existing producer
//! feeds READ-ONLY (`$XDG_RUNTIME_DIR/nimbus-aurora/agent.json` — the `feed.rs` mood signal;
//! `wind.json` — the `wind.rs` window-drag gust) and emits ONE pre-disposed
//! `scene-params.json` for a UE-side "dumb applier" to read. Per ADR-0030 D1 the disposer
//! lives HERE (in Rust, unit-tested) and the UE side never re-homes the grammar: it applies
//! a frame that is already eased / clamped / slewed / snapped-to-rest.
//!
//! **What this module owns vs. does NOT own (ADR-0030 D3 — disjoint channels):**
//!   * OWNS the MOOD axes — motion-rate, fog-density, focal backlight, warm-inscatter,
//!     desaturate, and the wind/AIR impulse. These map onto the "Indigo Channel" tableau's
//!     `INDIGO_MOTION_SPEED` / `INDIGO_FOG_DENSITY` / `INDIGO_LIGHT_INT` levers.
//!   * Does **NOT** own the THROTTLE axes (`r.ScreenPercentage` / `sg.*` / `t.MaxFPS` /
//!     `r.Streaming.*` — the `cvar_ladder.md` rungs). Those are the daemon-owned `Tier::Yielding`
//!     channel over UE Remote Control (ADR-0029 §3), reached by a different path so VRAM pressure
//!     can NEVER read as a mood change. There is no throttle field in `SceneParams`, by construction.
//!
//! **Fail-to-calm, modeled on `wind.rs`/`feed.rs` (ADR-0003, ADR-0030 D2):** every input is
//! clamped to its declared domain; a non-finite input reads as CALM, never a hold (the
//! `wind.rs::on_gust` discipline); every output is slew-rate-limited (an un-slewed term is a
//! strobe risk — an accessibility + calm defect); every output is bounded by compile-time
//! min/max; and the mood axes decay to the EXACT authored-resting baseline, SNAPPING below a
//! rest band rather than asymptoting (the `wind.rs::REST_GUST` snap-to-zero), so idle is REACHED,
//! not merely approached (ADR-0030 D4 — a live stage's idle = parameter-identical to the resting
//! tableau, all agent deltas 0). The feed is treated as UNTRUSTED: a max read size, an
//! `O_NOFOLLOW` open, a schema check, and parse-fail → neutral (the `feed.rs` fold-error-to-zeros).
//!
//! **Two springs, two timescales (ADR-0030 D5):** the MOOD axes ease on the SLOW ambient family
//! (`OMEGA_MOOD`, seconds); the WIND/AIR axis is the FAST direct-manipulation channel — it is
//! NOT slow-eased (it arrives already eased from `wind.rs`'s ~1.5 rad/s spring), only slew-capped
//! against a producer restart. They never share a spring and never share an axis.
//!
//! **Stale ≠ serene (ADR-0030 D9):** a feed we can't read does NOT fold into the calm `idle` the
//! wallpaper feed gives an unreachable Hermes (the keyhole "honest UNKNOWN" lesson). A stale/blind
//! feed gets a DISTINCT quieter-than-idle look — drift slows *further* than idle, the backlight
//! dims one step — reading as "I can't see," calmer than idle, never alarming. The `Freshness` is
//! also published so the daemon governor can throttle a blind wallpaper toward FLOOR (D9) — that
//! throttle is the governor's read of this signal, NOT an action this disposer takes (D3).
//!
//! **No model-authored content (ADR-0030 D6):** `SceneParams` is bounded numeric scalars + a
//! freshness enum + a reduce-motion flag. NEVER a free-form or model-authored string — there is
//! no `String` field, which the `params_carry_no_strings` test pins structurally (`Copy`).

use std::io::Read;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::Deserialize;

// ---------------------------------------------------------------------------
// Output contract — the disposed scene parameters (schema 1). All bounded scalars.
// Idle (every value at its REST) is parameter-identical to the authored resting tableau (D4).
// ---------------------------------------------------------------------------

/// Output schema version (SemVer-major). Bumping is a breaking change to the UE applier's
/// contract (ADR-0023's SemVer schema discipline). Pinned by the idle-frame test.
pub const SCHEMA: u32 = 1;

/// Fast tick (~30 Hz — a capped-wallpaper frame rate; the slow mood spring needs nothing
/// faster, and the AIR channel re-reads `wind.json` each tick for direct-manipulation feel).
/// Used while the scene is easing/changing; an idle scene backs off to `IDLE_POLL`.
const TICK: Duration = Duration::from_millis(33);
/// The fast tick as fractional seconds — the canonical dt the pure-`tick` tests drive with (the run
/// loop itself uses the REAL measured elapsed dt, so this is only the tests' representative step).
#[cfg(test)]
const TICK_DT: f64 = 0.033;

/// Slow idle poll. Once the scene has been byte-stable for `BACKOFF_AFTER_QUIET_TICKS`, the loop
/// widens to this so a settled 24/7 wallpaper helper does not wake ~30×/s forever just to re-read
/// unchanged feeds (the resource-safety review's idle-quiescence finding). A new mood is still
/// detected within one slow-poll period and snaps the cadence back to `TICK` (the mood eases over
/// 2–20 s anyway, so ≤250 ms detection latency is imperceptible). Mirrors `keyhole`'s adaptive cadence.
const IDLE_POLL: Duration = Duration::from_millis(250);

/// Consecutive write-free fast ticks before backing off to `IDLE_POLL` (~1 s of stability at `TICK`).
const BACKOFF_AFTER_QUIET_TICKS: u32 = 30;

/// The poll cadence as a pure function of how long the scene has been quiet (no frame written).
/// Fast while moving, slow once settled. Pure → unit-tested without the loop.
fn poll_interval(quiet_ticks: u32) -> Duration {
    if quiet_ticks < BACKOFF_AFTER_QUIET_TICKS {
        TICK
    } else {
        IDLE_POLL
    }
}

/// Slow mood spring stiffness (rad/s). ~0.45 ⇒ a state change eases over ~2–3 s — the ambient
/// 2–20 s family (ADR-0009 §2), below the attention-capture threshold. Deliberately far slower
/// than `wind.rs`'s ~1.5 rad/s direct-manipulation spring (D5 — two timescales).
const OMEGA_MOOD: f64 = 0.45;

/// Hard anti-strobe slew ceilings (units/second), applied ON TOP of the spring as the explicit
/// guarantee ADR-0030 D2 requires ("slew-rate-limit all outputs"). The spring already bounds the
/// mood delta; these cap it regardless of `OMEGA_MOOD`, and cap the AIR pass-through so a `wind.json`
/// producer restart (0.8 → 0 in one frame) cannot strobe the fog impulse.
const MOOD_MAX_SLEW: f64 = 0.8; // per second, generous vs the slow spring — a backstop, not the shape
const AIR_MAX_SLEW: f64 = 4.0; // per second — fast channel, but still bounded (no instantaneous jump)

/// Once an eased axis is within this distance of ITS OWN target we SNAP to the target exactly (the
/// `wind.rs::REST_GUST` settle), so EVERY steady state — idle, a held mood, a stale look — reaches an
/// exact, byte-stable value rather than asymptoting (and so an idle frame is the pinned anchor, D4).
/// Snapping to the *target* (never to a fixed rest) means a small LIVE mood is REACHED, never
/// swallowed: only the residual distance to this axis's own destination is collapsed. ~1% of a
/// lever's range — imperceptible (and the `feed.rs` ramp's smallest non-zero mood is already 0.6).
const SNAP_BAND: f64 = 0.01;

// --- Per-axis rest baselines + bounds. REST is the authored-resting value (idle = exactly this,
//     D4). Mood lifts an axis WITHIN [MIN, MAX]; the disposer clamps to these compile-time bounds.

const MOTION_REST: f64 = 1.0; // INDIGO_MOTION_SPEED multiplier at idle (authored pace)
const MOTION_BUSY_MAX: f64 = 1.6; // busy quickens the parallax drift, up to here
// A blind/stale feed drifts SLOWER than idle ("I can't see"). Held GENTLE (0.7, not a half-speed
// 0.5): the backlight dim (BACKLIGHT_STALE) carries most of the "blind" weight, so the pair reads as
// "the display went quiet," not "the room is powering down / something is wrong" (ambient review,
// 2026-06-20). Pending a live-frame eyeball when the UE layer lands (ADR-0029 §A).
const MOTION_STALE: f64 = 0.7;
const MOTION_FROZEN: f64 = 0.0; // reduce-motion freeze (INDIGO_MOTION_SPEED=0)

const FOG_REST: f64 = 1.0; // INDIGO_FOG_DENSITY multiplier at idle
const FOG_SNAG_MAX: f64 = 1.5; // snag thickens the volumetric fog

const BACKLIGHT_REST: f64 = 1.0; // INDIGO_LIGHT_INT multiplier at idle (full authored cyan rake)
const BACKLIGHT_STALE: f64 = 0.7; // a blind/stale feed dims the focal backlight one step (D9)
const BACKLIGHT_BUSY_MAX: f64 = 1.15; // reduce-motion busy = a static brightness step, held under bloom

const WARM_MAX: f64 = 1.0; // far-end warm-amber inscatter; idle = 0 (D8 — warm reserved for needs_you)
const DESAT_MAX: f64 = 0.6; // snag desaturate — the NON-color-redundant pair to fog-thicken (accessibility)

// Axis compile-time clamp floor/ceiling (defensive — the eased value can never leave this).
const MOTION_MIN: f64 = 0.0;
const MOTION_MAX: f64 = 1.6;
const FOG_MIN: f64 = 0.5;
const FOG_MAX: f64 = 1.5;
const BACKLIGHT_MIN: f64 = 0.5;
const BACKLIGHT_MAX: f64 = 1.2;

/// Freshness of the agent feed (ADR-0030 D9). Published so the governor can throttle a blind
/// wallpaper toward FLOOR (the governor's read, not this disposer's action — D3).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Freshness {
    /// A present, valid, current feed.
    Fresh,
    /// The producer's heartbeat is past the staleness gate — the feed is no longer current.
    Stale,
    /// No readable/valid feed at all (absent, garbage, symlink, oversized, schema mismatch).
    Blind,
}

impl Freshness {
    /// Wire encoding (informational, like `feed::AgentFeed::state`): 0 fresh · 1 stale · 2 blind.
    fn code(self) -> u8 {
        match self {
            Freshness::Fresh => 0,
            Freshness::Stale => 1,
            Freshness::Blind => 2,
        }
    }
    /// Is the feed something we can trust as a CURRENT picture of the fleet? Only `Fresh` shows
    /// agent mood; stale/blind fold the (untrusted) mood to calm and show the quieter look.
    fn trusted(self) -> bool {
        matches!(self, Freshness::Fresh)
    }
}

/// Validated, clamped agent mood — the `feed.rs` `{busy,warm,snag}` scalars after untrusting.
/// `state` is informational and intentionally NOT consumed (the scalars drive everything, per the
/// feed contract); a non-finite or out-of-range scalar is folded to calm here, never propagated.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MoodInput {
    pub busy: f64,
    pub warm: f64,
    pub snag: f64,
}

impl MoodInput {
    /// The neutral (idle) mood — every agent delta 0.
    const NEUTRAL: MoodInput = MoodInput { busy: 0.0, warm: 0.0, snag: 0.0 };

    /// Clamp each scalar to [0,1]; a non-finite scalar reads as 0 (CALM), never a hold — the
    /// `wind.rs::on_gust` NaN discipline. This is the untrusting boundary for the agent feed.
    fn sanitized(self) -> MoodInput {
        let c = |x: f64| if x.is_finite() { x.clamp(0.0, 1.0) } else { 0.0 };
        MoodInput { busy: c(self.busy), warm: c(self.warm), snag: c(self.snag) }
    }
}

// ---------------------------------------------------------------------------
// Pure disposer state (the only new decision logic — unit-tested below). A plain Copy value with
// NO path to `lease::Inner` or any lock: this consumer cannot touch the SIGKILL path (D2 purity).
// ---------------------------------------------------------------------------

/// The eased, written scene state. `apply()` feeds RAW producer input → per-axis TARGETS; `tick()`
/// integrates each current axis toward its target (mood SLOW, air FAST) and snaps mood to rest.
/// Easing the OUTPUT axes (not the mood scalars) is what lets the look ease *into* the stale state
/// — a freshness flip just moves the targets; nothing hard-cuts.
#[derive(Clone, Copy, Debug)]
pub struct SceneState {
    // current eased axes
    motion: f64,
    fog: f64,
    backlight: f64,
    warm: f64,
    desat: f64,
    air: f64,
    // targets the axes ease toward
    t_motion: f64,
    t_fog: f64,
    t_backlight: f64,
    t_warm: f64,
    t_desat: f64,
    t_air: f64,
    // published signals (not eased — they describe the world, not a look)
    freshness: Freshness,
    reduce_motion: bool,
}

impl Default for SceneState {
    /// The idle resting tableau: every mood axis at its REST baseline, no warm, no desaturate,
    /// no air, fresh, full motion. Parameter-identical to the authored scene (D4).
    fn default() -> Self {
        SceneState {
            motion: MOTION_REST,
            fog: FOG_REST,
            backlight: BACKLIGHT_REST,
            warm: 0.0,
            desat: 0.0,
            air: 0.0,
            t_motion: MOTION_REST,
            t_fog: FOG_REST,
            t_backlight: BACKLIGHT_REST,
            t_warm: 0.0,
            t_desat: 0.0,
            t_air: 0.0,
            freshness: Freshness::Fresh,
            reduce_motion: false,
        }
    }
}

/// Slew a value toward a target, capped to `max_delta` this step (the explicit anti-strobe bound,
/// ADR-0030 D2). Returns the new value. `max_delta` already folds in `dt`.
fn slew(cur: f64, target: f64, max_delta: f64) -> f64 {
    let d = target - cur;
    if d.abs() <= max_delta {
        target
    } else {
        cur + d.signum() * max_delta
    }
}

impl SceneState {
    /// Recompute the per-axis TARGETS from the latest world: validated mood, the wind gust (already
    /// eased upstream), the feed freshness, and the user's reduce-motion preference. PURE — no I/O,
    /// no clock. This is where the whole mood/throttle-disjoint, warm-monopoly, stale≠serene, and
    /// reduce-motion-static-tone grammar lives (ADR-0030 D3/D4/D8/D9 + the accessibility fallback).
    pub fn apply(&mut self, mood: MoodInput, gust: f64, freshness: Freshness, reduce_motion: bool) {
        let m = mood.sanitized();
        let gust = if gust.is_finite() { gust.clamp(0.0, 1.0) } else { 0.0 };
        self.freshness = freshness;
        self.reduce_motion = reduce_motion;

        if !freshness.trusted() {
            // STALE / BLIND (D9): a feed we can't trust is NOT the calm idle. Fold the (untrusted)
            // mood to calm and show the DISTINCT quieter-than-idle look — drift slows further than
            // idle, the backlight dims a step — "I can't see," calmer than idle, never snag/alarm.
            // Wind is a SEPARATE, local, trustworthy producer (a real drag), so AIR stays live.
            self.t_motion = MOTION_STALE;
            self.t_backlight = BACKLIGHT_STALE;
            self.t_fog = FOG_REST;
            self.t_warm = 0.0;
            self.t_desat = 0.0;
            self.t_air = if reduce_motion { 0.0 } else { gust };
            return;
        }

        if reduce_motion {
            // REDUCE-MOTION (accessibility): the INDIGO_MOTION_SPEED=0 seam freezes ALL motion, so
            // the canvas drift AND the wind impulse freeze and mood must survive as STATIC tone
            // (ADR-0030 accessibility): busy = a static brightness step held under bloom; snag = a
            // static fog-thicken + desaturate; needs_you = a HELD (non-breathing) warm lobe. The
            // reduce_motion flag rides the frame so the applier holds warm steady instead of breathing it.
            // NOTE (conservative single-seam default): this also freezes the wind/AIR impulse (`t_air=0`),
            // i.e. the user's OWN direct-manipulation motion, not just the autonomous canvas drift. The
            // Indigo Channel exposes one freeze seam (INDIGO_MOTION_SPEED), so freezing both is the safe
            // default; whether to split direct-manipulation from autonomous motion is a `ui-accessibility-
            // reviewer` call (ADR-0030 accessibility hand-off), not changed here.
            self.t_motion = MOTION_FROZEN;
            self.t_backlight = BACKLIGHT_REST + m.busy * (BACKLIGHT_BUSY_MAX - BACKLIGHT_REST);
            self.t_fog = FOG_REST + m.snag * (FOG_SNAG_MAX - FOG_REST);
            self.t_desat = m.snag * DESAT_MAX;
            self.t_warm = m.warm * WARM_MAX;
            self.t_air = 0.0;
            return;
        }

        // FRESH, full motion (the normal mapping). Mood owns motion-rate / fog-density / warm-
        // inscatter (D3); busy quickens drift, snag thickens fog + desaturates (a non-color cue),
        // warm is the ONLY warm injector (D8 — and only `needs_you` raises `warm` upstream in the
        // feed precedence). The backlight stays at its authored rest (busy is carried by motion here).
        self.t_motion = MOTION_REST + m.busy * (MOTION_BUSY_MAX - MOTION_REST);
        self.t_backlight = BACKLIGHT_REST;
        self.t_fog = FOG_REST + m.snag * (FOG_SNAG_MAX - FOG_REST);
        self.t_desat = m.snag * DESAT_MAX;
        self.t_warm = m.warm * WARM_MAX;
        self.t_air = gust;
    }

    /// Integrate every axis toward its target. Mood axes ease on the SLOW spring (+ a hard slew
    /// cap) and SNAP to an exact REST baseline when settling there (D4). The AIR axis is the FAST
    /// channel: it is NOT slow-eased — it tracks the already-eased `wind.json` gust, capped only
    /// against a producer-restart jump (D5 — two springs, two timescales). Deterministic; `dt`
    /// injected for tests.
    pub fn tick(&mut self, dt: f64) {
        let a = 1.0 - (-OMEGA_MOOD * dt).exp(); // slow critically-damped lerp factor
        let mood_cap = MOOD_MAX_SLEW * dt;
        let air_cap = AIR_MAX_SLEW * dt;

        // One eased mood axis: spring toward target, clamp the step to the anti-strobe ceiling, SNAP
        // to the target once within SNAP_BAND of it (so every steady state — idle, a held mood, a
        // stale look — reaches an exact, byte-stable value and stops churning the file), then clamp
        // to compile-time bounds. Snapping to the TARGET (not a fixed rest) is what lets a small live
        // mood be reached rather than swallowed, while a decay-to-idle still lands on exact rest.
        let ease = |cur: f64, target: f64, lo: f64, hi: f64| -> f64 {
            let stepped = cur + (target - cur) * a;
            let capped = slew(cur, stepped, mood_cap);
            let snapped = if (capped - target).abs() < SNAP_BAND { target } else { capped };
            snapped.clamp(lo, hi)
        };

        self.motion = ease(self.motion, self.t_motion, MOTION_MIN, MOTION_MAX);
        self.fog = ease(self.fog, self.t_fog, FOG_MIN, FOG_MAX);
        self.backlight = ease(self.backlight, self.t_backlight, BACKLIGHT_MIN, BACKLIGHT_MAX);
        self.warm = ease(self.warm, self.t_warm, 0.0, WARM_MAX);
        self.desat = ease(self.desat, self.t_desat, 0.0, DESAT_MAX);

        // AIR — fast channel: track the eased wind gust directly, capped against a restart jump.
        self.air = slew(self.air, self.t_air, air_cap).clamp(0.0, 1.0);
    }

    /// One-shot SETTLE — jump every axis to its current target (no easing). Used by `--once` so a
    /// verify/CI snapshot shows where the mood points *now* rather than a half-eased first tick.
    fn settle(&mut self) {
        self.motion = self.t_motion.clamp(MOTION_MIN, MOTION_MAX);
        self.fog = self.t_fog.clamp(FOG_MIN, FOG_MAX);
        self.backlight = self.t_backlight.clamp(BACKLIGHT_MIN, BACKLIGHT_MAX);
        self.warm = self.t_warm.clamp(0.0, WARM_MAX);
        self.desat = self.t_desat.clamp(0.0, DESAT_MAX);
        self.air = self.t_air.clamp(0.0, 1.0);
    }

    /// Render the schema-1 frame. Rounded (like `wind.rs`/`feed.rs`) so an idle frame is a stable,
    /// diffable string — the edge-write + idle-anchor contract depends on this being deterministic.
    pub fn to_json(self, now: f64) -> String {
        format!("{},\"updated_at\":{}}}\n", self.fields(), round3(now))
    }

    /// The frame body WITHOUT `updated_at` (drives the edge-write decision — two frames that differ
    /// only by timestamp don't move the scene, so re-writing them would just churn the file and wake
    /// the consumer's poller).
    fn body(&self) -> String {
        format!("{}}}", self.fields())
    }

    /// The schema/axis key-values WITHOUT the trailing comma or closing brace — the single shared
    /// source `body`/`to_json` each finish with their own suffix (so the two frames can never drift).
    fn fields(&self) -> String {
        format!(
            "{{\"schema\":{},\"motion\":{},\"fog\":{},\"backlight\":{},\"warm\":{},\"desat\":{},\"air\":{},\"fresh\":{},\"reduce_motion\":{}",
            SCHEMA,
            round3(self.motion),
            round3(self.fog),
            round3(self.backlight),
            round3(self.warm),
            round3(self.desat),
            round3(self.air),
            self.freshness.code(),
            self.reduce_motion,
        )
    }

    /// Is the state EXACTLY the idle resting tableau (every mood axis at rest, fresh, full motion)?
    /// Used to make the idle edge-write a no-op (an idle desktop stops touching the file).
    fn is_idle(&self) -> bool {
        self.motion == MOTION_REST
            && self.fog == FOG_REST
            && self.backlight == BACKLIGHT_REST
            && self.warm == 0.0
            && self.desat == 0.0
            && self.air == 0.0
            && self.freshness == Freshness::Fresh
            && !self.reduce_motion
    }
}

fn round3(x: f64) -> f64 {
    let r = (x * 1000.0).round() / 1000.0;
    // Collapse -0.0 → +0.0. `clamp(0.0, _)` preserves a -0.0 input's sign bit, which `format!` would
    // render as `-0` — a residue that slips the untrusting boundary and breaks the byte-stable frame.
    if r == 0.0 {
        0.0
    } else {
        r
    }
}

// ---------------------------------------------------------------------------
// Untrusted-input readers. The feed dir is 0700 + assert-owned (feed.rs), but we still treat every
// file as hostile: a capped read, an O_NOFOLLOW open (no symlink swap), a schema check, parse-fail
// → neutral (the feed.rs fold-error-to-zeros posture, ADR-0003 / ADR-0030 D2).
// ---------------------------------------------------------------------------

/// Max bytes we will read from any feed file. `agent.json`/`wind.json` are ~40–80 bytes; this cap
/// is generous but bounds a hostile/garbage file (a multi-GB symlink target can't be slurped).
const MAX_FEED_BYTES: u64 = 64 * 1024;

/// `agent.json`'s on-disk shape — `feed::AgentFeed` (`{state,busy,warm,snag}`). No schema field
/// exists in that contract today, so validation here is range/finite clamping (`MoodInput::sanitized`)
/// rather than a version gate; a future `agent.json` schema bump would add a check here.
#[derive(Deserialize)]
struct AgentFile {
    #[serde(default)]
    busy: f64,
    #[serde(default)]
    warm: f64,
    #[serde(default)]
    snag: f64,
}

/// `wind.json`'s on-disk shape — `wind.rs`'s schema-1 frame. We consume only the eased `gust`
/// scalar (the AIR impulse magnitude); direction is a UE-side concern. The `schema` IS checked.
#[derive(Deserialize)]
struct WindFile {
    #[serde(default)]
    schema: u32,
    #[serde(default)]
    gust: f64,
}

/// A heartbeat file's shape — a single producer-liveness timestamp, written unconditionally each tick
/// by `feed::write_heartbeat` (ADR-0030 D9). Still OPTIONAL by construction: if `heartbeat.json` is
/// absent (the `feed` producer isn't running), there is simply no staleness signal → the feed reads
/// Fresh-or-Blind, never Stale. When present, a past-gate timestamp activates `Stale` (the quieter-
/// than-idle middle state) — which is why the consumer never depends on the producer being up.
#[derive(Deserialize)]
struct HeartbeatFile {
    #[serde(default)]
    updated_at: f64,
}

/// Read at most `MAX_FEED_BYTES` from `path` with an `O_NOFOLLOW` open (a symlink where a feed file
/// should be is refused, not followed). `None` = absent / symlink / unreadable / oversized.
fn read_capped(path: &Path) -> Option<String> {
    let mut f = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .ok()?;
    let mut buf = Vec::new();
    // Read one byte past the cap so an exactly-cap-sized garbage file is still rejected as oversized.
    f.by_ref().take(MAX_FEED_BYTES + 1).read_to_end(&mut buf).ok()?;
    if buf.len() as u64 > MAX_FEED_BYTES {
        return None;
    }
    String::from_utf8(buf).ok()
}

/// Read + untrust `agent.json` → the validated mood, or `None` when the feed can't be read/parsed
/// (the caller maps `None` → `Freshness::Blind` + neutral mood: a feed we can't read is "I can't see,"
/// NOT calm idle — D9).
fn read_agent(path: &Path) -> Option<MoodInput> {
    let s = read_capped(path)?;
    let a: AgentFile = serde_json::from_str(&s).ok()?;
    Some(MoodInput { busy: a.busy, warm: a.warm, snag: a.snag }.sanitized())
}

/// Read + untrust `wind.json` → the eased gust ∈ [0,1]. Absent / wrong-schema / parse-fail /
/// inactive → 0 (no air). The schema gate rejects a future incompatible `wind.json` rather than
/// misreading it. Direction is a UE-side concern; only the magnitude is the AIR impulse here.
fn read_wind(path: &Path) -> f64 {
    read_capped(path)
        .and_then(|s| serde_json::from_str::<WindFile>(&s).ok())
        .filter(|w| w.schema == 1)
        // The eased gust already decays to 0 on `Finished` (wind.rs), so the magnitude alone is the
        // AIR impulse — no need to consult `active`. Non-finite → 0 (calm), never a hold.
        .map(|w| if w.gust.is_finite() { w.gust.clamp(0.0, 1.0) } else { 0.0 })
        .unwrap_or(0.0)
}

/// Read the OPTIONAL producer heartbeat → its `updated_at` epoch, or `None` if no heartbeat file
/// exists (then the reader never emits `Stale`; see `HeartbeatFile`).
fn read_heartbeat(path: &Path) -> Option<f64> {
    read_capped(path)
        .and_then(|s| serde_json::from_str::<HeartbeatFile>(&s).ok())
        .map(|h| h.updated_at)
        .filter(|t| t.is_finite() && *t > 0.0)
}

/// Seconds past the last heartbeat at which a present feed reads `Stale`. 4× the `feed` producer's
/// 2 s loop cadence — comfortably past a delayed tick (no false Stale), tight enough that a dead
/// producer is noticed within a few seconds.
const STALE_SECS: f64 = 8.0;

/// Resolve `(mood, freshness)` from the feed dir at time `now`. The honest-UNKNOWN core (D9) —
/// `agent.json` unreadable/garbage/symlink/oversized reads **Blind** + neutral mood; present+valid
/// with a heartbeat past `STALE_SECS` reads **Stale** (mood kept but untrusted — `apply` folds it to
/// the quieter look); present+valid with no-heartbeat-or-fresh-heartbeat reads **Fresh**.
/// A live-but-idle producer (which stops rewriting `agent.json`, so its mtime goes old) correctly
/// reads **Fresh**, never Stale — that's why staleness is heartbeat-gated, not agent.json-mtime-gated.
fn read_world(dir: &Path, now: f64) -> (MoodInput, f64, Freshness) {
    let gust = read_wind(&dir.join("wind.json"));
    match read_agent(&dir.join("agent.json")) {
        None => (MoodInput::NEUTRAL, gust, Freshness::Blind),
        Some(mood) => {
            let fresh = match read_heartbeat(&dir.join("heartbeat.json")) {
                Some(hb) if now - hb > STALE_SECS => Freshness::Stale,
                _ => Freshness::Fresh,
            };
            (mood, gust, fresh)
        }
    }
}

// ---------------------------------------------------------------------------
// I/O shell — atomic write + the poll/ease loop.
// ---------------------------------------------------------------------------

/// Atomic write — dot-prefixed temp + rename, identical discipline to `wind::write_wind` /
/// `feed::write_feed` so a `*.json` poller never catches a half-written file. Best-effort /
/// fail-open: a failed write leaves the UE applier holding last-good.
fn write_scene(dir: &Path, json: &str) -> std::io::Result<()> {
    let tmp = dir.join(format!(".scene-params.{}.tmp", std::process::id()));
    std::fs::write(&tmp, json)?;
    std::fs::rename(&tmp, dir.join("scene-params.json"))
}

fn now_epoch() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Is reduce-motion requested? Mirrors the Indigo Channel's `INDIGO_MOTION_SPEED=0` seam: the
/// `AGENTOS_REDUCE_MOTION` env flag (the desktop's reduce-motion preference would set it). Default off.
fn reduce_motion_pref() -> bool {
    matches!(std::env::var("AGENTOS_REDUCE_MOTION").as_deref(), Ok("1") | Ok("true") | Ok("on") | Ok("yes"))
}

/// `agentosd scene [--once]` — the reactive disposer loop. Reads `agent.json` + `wind.json` (+ an
/// optional `heartbeat.json`) from the feed dir, eases the scene on a fixed tick, and edge-writes
/// `scene-params.json` for the UE applier. `--once` writes a single SETTLED snapshot and exits
/// (verify/CI). Fail-open throughout (ADR-0003): a missing feed reads Blind → the quieter look; a
/// failed write is a no-op.
///
/// **PAUSED — prototype ahead of ADR-0029 §A (ADR-0030 Consequences).** The UE *wallpaper layer*
/// is not yet the default surface, and the UE-side validating poller is the other half of D1/D2.
/// This is the agentosd half: buildable + verifiable now (it needs only files), de-risking the
/// slew/clamp/idle behavior before the layer lands.
pub fn run(once: bool) -> Result<(), Box<dyn std::error::Error>> {
    let dir = feed_dir()?;
    let out = dir.join("scene-params.json");
    let reduce_motion = reduce_motion_pref();
    println!(
        "agentosd scene → {} (mood disposer, ADR-0030; reduce_motion={})",
        out.display(),
        reduce_motion
    );

    let mut state = SceneState::default();

    if once {
        let (mood, gust, fresh) = read_world(&dir, now_epoch());
        state.apply(mood, gust, fresh, reduce_motion);
        state.settle();
        write_scene(&dir, &state.to_json(now_epoch()))?;
        println!(
            "[{}] once: fresh={:?} motion={} fog={} warm={} air={}",
            crate::now_hms(),
            fresh,
            round3(state.motion),
            round3(state.fog),
            round3(state.warm),
            round3(state.air),
        );
        return Ok(());
    }

    // Seed an initial idle frame so the applier has something to read from tick one, then ease.
    let _ = write_scene(&dir, &SceneState::default().to_json(now_epoch()));

    let mut last_body: Option<String> = None;
    let mut last_now = now_epoch();
    let mut quiet_ticks: u32 = 0;
    loop {
        let now = now_epoch();
        // Real elapsed dt so the spring eases correctly at ANY cadence (fast or backed-off). Clamped:
        // a backward/huge clock jump can't drive a negative or explosive step (fail-safe, ADR-0003).
        let dt = (now - last_now).clamp(0.0, 1.0);
        last_now = now;

        let (mood, gust, fresh) = read_world(&dir, now);
        state.apply(mood, gust, fresh, reduce_motion);
        state.tick(dt);

        let body = state.body();
        let idle = state.is_idle();
        // Idle edge-write: once idle AND we've already written an idle frame, stop touching the file
        // (a present-but-old idle frame reads calm; the applier holds last-good). Otherwise write only
        // when the rounded body changed (timestamp-only deltas don't move the scene).
        let already_idle = idle && last_body.as_deref() == Some(body.as_str());
        let wrote = !already_idle && last_body.as_deref() != Some(body.as_str());
        if wrote {
            let _ = write_scene(&dir, &state.to_json(now)); // best-effort / fail-open
            last_body = Some(body);
        }

        // Adaptive idle cadence (ADR-0030, resource-safety review): poll fast while the scene is
        // moving (a frame was written), back off to a slow poll once it has been stable for a while,
        // so a settled 24/7 wallpaper helper doesn't wake ~30×/s forever. Any feed change writes a
        // frame → resets to fast. The reads are page-cache-cheap tmpfs hits, but deep CPU idle isn't.
        quiet_ticks = if wrote { 0 } else { quiet_ticks.saturating_add(1) };
        std::thread::sleep(poll_interval(quiet_ticks));
    }
}

/// The `$XDG_RUNTIME_DIR/nimbus-aurora` dir — REUSE `feed::feed_dir` so the 0700 + assert-owned
/// hardening (ADR-0030 must-fix, landed in `feed.rs`) applies here too, not a second mode bit.
fn feed_dir() -> std::io::Result<PathBuf> {
    crate::feed::feed_dir()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mood(busy: f64, warm: f64, snag: f64) -> MoodInput {
        MoodInput { busy, warm, snag }
    }

    /// Settle a fresh, full-motion scene to its target for `mood` (no easing) — the steady-state
    /// the disposer eases toward, for asserting the pure mapping.
    fn settled(m: MoodInput) -> SceneState {
        let mut s = SceneState::default();
        s.apply(m, 0.0, Freshness::Fresh, false);
        s.settle();
        s
    }

    #[test]
    fn idle_is_exactly_the_resting_tableau() {
        // Parameter-identical idle (D4): every axis at its authored REST, all agent deltas 0.
        let s = SceneState::default();
        assert_eq!(s.motion, MOTION_REST);
        assert_eq!(s.fog, FOG_REST);
        assert_eq!(s.backlight, BACKLIGHT_REST);
        assert_eq!(s.warm, 0.0);
        assert_eq!(s.desat, 0.0);
        assert_eq!(s.air, 0.0);
        assert!(s.is_idle());
    }

    #[test]
    fn idle_frame_is_byte_stable_and_pinned() {
        // The canonical idle frame the UE applier diffs against. If this string ever drifts, the
        // "idle = parameter-identical" claim (D4) is broken — pin it. Only `updated_at` may differ.
        let s = SceneState::default();
        assert_eq!(
            s.to_json(1750000000.0),
            "{\"schema\":1,\"motion\":1,\"fog\":1,\"backlight\":1,\"warm\":0,\"desat\":0,\"air\":0,\"fresh\":0,\"reduce_motion\":false,\"updated_at\":1750000000}\n"
        );
        // And the body (sans timestamp) is stable across idle ticks → the edge-write stops writing.
        let mut s2 = SceneState::default();
        let b0 = s2.body();
        for _ in 0..600 {
            s2.apply(MoodInput::NEUTRAL, 0.0, Freshness::Fresh, false);
            s2.tick(TICK_DT);
        }
        assert_eq!(s2.body(), b0, "an idle scene's frame body is byte-identical across ticks");
        assert!(s2.is_idle());
    }

    #[test]
    fn busy_quickens_motion_only() {
        // Mood owns motion-rate (D3): busy lifts the drift toward MOTION_BUSY_MAX and touches NOTHING
        // else — fog/backlight/warm/desat stay at rest.
        let s = settled(mood(1.0, 0.0, 0.0));
        assert_eq!(s.motion, MOTION_BUSY_MAX);
        assert_eq!(s.fog, FOG_REST);
        assert_eq!(s.backlight, BACKLIGHT_REST);
        assert_eq!(s.warm, 0.0);
        assert_eq!(s.desat, 0.0);
        // Partial busy is proportional.
        let half = settled(mood(0.5, 0.0, 0.0));
        assert!((half.motion - (MOTION_REST + 0.5 * (MOTION_BUSY_MAX - MOTION_REST))).abs() < 1e-9);
    }

    #[test]
    fn snag_thickens_fog_and_desaturates() {
        // snag owns fog-density + the non-color-redundant desaturate pair (D3 + accessibility),
        // and does NOT raise warm (D8) or motion.
        let s = settled(mood(0.0, 0.0, 1.0));
        assert_eq!(s.fog, FOG_SNAG_MAX);
        assert_eq!(s.desat, DESAT_MAX);
        assert_eq!(s.warm, 0.0);
        assert_eq!(s.motion, MOTION_REST);
    }

    #[test]
    fn warm_is_injected_only_by_needs_you() {
        // D8 warm monopoly: warm rises ONLY with the warm scalar; busy and snag never inject it.
        assert_eq!(settled(mood(0.0, 1.0, 0.0)).warm, WARM_MAX);
        for b in [0.0, 0.5, 1.0] {
            for s in [0.0, 0.5, 1.0] {
                assert_eq!(settled(mood(b, 0.0, s)).warm, 0.0, "no warm without the warm scalar");
            }
        }
    }

    #[test]
    fn nan_and_out_of_range_inputs_fold_to_calm() {
        // The untrusting boundary (D2): a non-finite scalar reads as 0 (CALM, never a hold — the
        // wind.rs discipline: a garbage/overflow value must calm the wallpaper, not e.g. max out
        // the needs_you warmth and falsely scream for attention), an out-of-range FINITE one clamps.
        let s = settled(mood(f64::NAN, f64::INFINITY, -5.0));
        assert_eq!(s.motion, MOTION_REST, "NaN busy → calm (rest motion)");
        assert_eq!(s.warm, 0.0, "+inf warm folds to CALM (0), never a false needs_you scream");
        assert_eq!(s.fog, FOG_REST, "negative (finite) snag clamps to 0 → rest fog");
        assert_eq!(s.desat, 0.0);
        // A finite out-of-range value DOES clamp (vs. fold): warm 9.0 → 1.0 → WARM_MAX.
        assert_eq!(settled(mood(0.0, 9.0, 0.0)).warm, WARM_MAX, "finite over-range warm clamps to max");
        // A NaN gust reads as no air, never a hold.
        let mut g = SceneState::default();
        g.apply(MoodInput::NEUTRAL, f64::NAN, Freshness::Fresh, false);
        g.settle();
        assert_eq!(g.air, 0.0);
    }

    #[test]
    fn mood_eases_back_to_an_exact_rest_baseline() {
        // From a busy+snag state, drop to idle: every mood axis must SNAP to its EXACT rest (D4),
        // not asymptote. (~10 s of ticks at 30 Hz is well past the slow spring's settle.)
        let mut s = SceneState::default();
        s.apply(mood(1.0, 0.0, 1.0), 0.0, Freshness::Fresh, false);
        for _ in 0..120 {
            s.tick(TICK_DT);
        }
        s.apply(MoodInput::NEUTRAL, 0.0, Freshness::Fresh, false);
        for _ in 0..400 {
            s.apply(MoodInput::NEUTRAL, 0.0, Freshness::Fresh, false);
            s.tick(TICK_DT);
        }
        assert_eq!(s.motion, MOTION_REST, "motion snaps to EXACT rest");
        assert_eq!(s.fog, FOG_REST, "fog snaps to EXACT rest");
        assert_eq!(s.desat, 0.0, "desat snaps to EXACT 0");
        assert!(s.is_idle(), "and the whole frame returns to the pinned idle anchor");
    }

    #[test]
    fn outputs_are_slew_limited_no_strobe() {
        // ADR-0030 D2 anti-strobe: even a worst-case instantaneous full-scale input flip cannot move
        // an output more than its per-tick slew ceiling. Drive busy 0→1 and assert the FIRST tick's
        // motion delta is bounded by MOOD_MAX_SLEW·dt (a strobe would jump the whole range at once).
        let mut s = SceneState::default();
        s.apply(mood(1.0, 0.0, 0.0), 0.0, Freshness::Fresh, false);
        let before = s.motion;
        s.tick(TICK_DT);
        let delta = (s.motion - before).abs();
        assert!(
            delta <= MOOD_MAX_SLEW * TICK_DT + 1e-9,
            "motion moved {delta} in one tick — exceeds the anti-strobe ceiling {}",
            MOOD_MAX_SLEW * TICK_DT
        );
        // The AIR channel is faster but STILL bounded (a wind producer restart can't strobe the fog).
        let mut a = SceneState { air: 1.0, ..SceneState::default() }; // pretend a gust was up
        a.apply(MoodInput::NEUTRAL, 0.0, Freshness::Fresh, false); // wind.json now reads 0 (restart)
        let air_before = a.air;
        a.tick(TICK_DT);
        assert!(
            (a.air - air_before).abs() <= AIR_MAX_SLEW * TICK_DT + 1e-9,
            "air must not jump more than its fast slew ceiling in one tick"
        );
    }

    #[test]
    fn poll_cadence_is_fast_while_moving_then_backs_off_when_idle() {
        // Adaptive idle cadence: fast while the scene is changing, slow once it's been stable a while.
        assert_eq!(poll_interval(0), TICK, "a frame just wrote → poll fast");
        assert_eq!(poll_interval(BACKOFF_AFTER_QUIET_TICKS - 1), TICK, "still in the active window → fast");
        assert_eq!(poll_interval(BACKOFF_AFTER_QUIET_TICKS), IDLE_POLL, "stable long enough → slow idle poll");
        assert_eq!(poll_interval(u32::MAX), IDLE_POLL, "stays slow while idle (no overflow)");
        assert!(IDLE_POLL > TICK, "the idle poll must actually be slower than the fast tick");
    }

    #[test]
    fn slew_cap_bounds_a_large_step() {
        // The anti-strobe primitive in isolation (the spring step is well under the cap at OMEGA_MOOD,
        // so this is the only place the cap's binding branch is forced): a target far beyond the cap
        // moves EXACTLY the cap, never the whole way; within the cap, the target is reached, no overshoot.
        assert_eq!(slew(0.0, 1.0, 0.1), 0.1, "a far target moves exactly the cap");
        assert_eq!(slew(1.0, 0.0, 0.1), 0.9, "...in either direction");
        assert_eq!(slew(0.0, 0.05, 0.1), 0.05, "a near target is reached, no overshoot");
    }

    #[test]
    fn negative_zero_input_serializes_without_minus_zero() {
        // -0.0 keeps its sign bit through clamp; round3 must scrub it so the byte-stable frame never
        // carries "-0" (a hygiene defect at the untrusting boundary — rust-performance review).
        let json = settled(mood(-0.0, -0.0, -0.0)).to_json(1750000000.0);
        assert!(!json.contains("-0"), "a -0.0 mood must not serialize as -0: {json}");
        let mut g = SceneState::default();
        g.apply(MoodInput::NEUTRAL, -0.0, Freshness::Fresh, false); // a -0.0 gust on the air axis
        g.settle();
        assert!(!g.to_json(1.0).contains("-0"), "a -0.0 gust must not serialize as -0");
    }

    #[test]
    fn a_dying_feed_eases_into_the_stale_look_never_hard_cuts() {
        // ADR-0030 D9 + the docstring's promise: a producer that dies mid-BUSY must EASE down to the
        // quieter stale look, never jump. Ramp into busy, flip to Blind, and assert the first tick's
        // motion delta is bounded by the slew cap and the axis is mid-transition (not already at stale).
        let mut s = SceneState::default();
        for _ in 0..120 {
            s.apply(mood(1.0, 0.0, 0.0), 0.0, Freshness::Fresh, false);
            s.tick(TICK_DT);
        }
        assert!(s.motion > 1.4, "ramped into a quickened busy drift");
        s.apply(MoodInput::NEUTRAL, 0.0, Freshness::Blind, false); // the fleet feed goes dark
        let before = s.motion;
        s.tick(TICK_DT);
        assert!(
            (s.motion - before).abs() <= MOOD_MAX_SLEW * TICK_DT + 1e-9,
            "the blind transition must ease (bounded by the slew cap), not hard-cut"
        );
        assert!(s.motion < before && s.motion > MOTION_STALE, "...and is mid-transition, not yet at stale");
        // And it does eventually settle at the exact stale value (no asymptote churn).
        for _ in 0..400 {
            s.apply(MoodInput::NEUTRAL, 0.0, Freshness::Blind, false);
            s.tick(TICK_DT);
        }
        assert_eq!(s.motion, MOTION_STALE, "the stale look settles to its EXACT target (byte-stable)");
        assert_eq!(s.backlight, BACKLIGHT_STALE);
    }

    #[test]
    fn a_small_live_mood_is_reached_not_swallowed() {
        // The snap-to-TARGET fix (determinism review): a small but real busy must asymptote to its
        // lifted target, NOT be snapped back to rest. (The feed never emits this, but the disposer
        // treats the feed as untrusted and must not depend on the producer's 0.6 ramp floor.)
        let mut s = SceneState::default();
        let target = MOTION_REST + 0.005 * (MOTION_BUSY_MAX - MOTION_REST); // 1.003
        for _ in 0..600 {
            s.apply(mood(0.005, 0.0, 0.0), 0.0, Freshness::Fresh, false);
            s.tick(TICK_DT);
        }
        assert!(s.motion > MOTION_REST, "a small live busy is NOT swallowed to rest");
        assert!((s.motion - target).abs() < 1e-9, "it reaches its exact (small) target");
    }

    #[test]
    fn wind_air_is_a_distinct_fast_axis() {
        // D5 two-timescales: AIR tracks the gust on the FAST cap while mood is on the SLOW spring.
        // After ONE tick from idle with a full gust, air has moved far MORE than motion would for a
        // full busy input — the two are visibly different timescales.
        let mut air = SceneState::default();
        air.apply(mood(0.0, 0.0, 0.0), 1.0, Freshness::Fresh, false);
        air.tick(TICK_DT);
        let mut mot = SceneState::default();
        mot.apply(mood(1.0, 0.0, 0.0), 0.0, Freshness::Fresh, false);
        mot.tick(TICK_DT);
        let air_frac = air.air / 1.0; // fraction of full range covered in one tick
        let mot_frac = (mot.motion - MOTION_REST) / (MOTION_BUSY_MAX - MOTION_REST);
        assert!(air_frac > mot_frac, "the air axis must move faster than the slow mood axis per tick");
    }

    #[test]
    fn stale_is_quieter_than_idle_not_serene() {
        // D9: a stale feed is NOT the calm idle. Drift slows BELOW idle and the backlight dims a
        // step; mood is NOT shown (untrusted), but it never reads as snag/alarm.
        let mut s = SceneState::default();
        s.apply(mood(1.0, 1.0, 1.0), 0.0, Freshness::Stale, false); // a busy+warm+snag feed, but stale
        s.settle();
        assert_eq!(s.motion, MOTION_STALE, "stale drift is slower than idle");
        assert!(s.motion < MOTION_REST, "...strictly slower than idle (calmer, 'I can't see')");
        assert_eq!(s.backlight, BACKLIGHT_STALE, "stale dims the backlight one step");
        assert!(s.backlight < BACKLIGHT_REST);
        assert_eq!(s.warm, 0.0, "an untrusted feed shows no warm");
        assert_eq!(s.fog, FOG_REST, "and reads as rest fog, never snag");
        assert_eq!(s.freshness, Freshness::Stale);
    }

    #[test]
    fn blind_publishes_the_signal_and_the_quiet_look() {
        // A blind feed gets the same quieter look AND publishes fresh=2 so the governor can throttle
        // toward FLOOR (D9 — the governor's read, not an action this disposer takes; there is no
        // throttle field here).
        let mut s = SceneState::default();
        s.apply(mood(0.5, 0.0, 0.5), 0.0, Freshness::Blind, false);
        s.settle();
        assert_eq!(s.freshness, Freshness::Blind);
        assert_eq!(s.freshness.code(), 2);
        assert_eq!(s.motion, MOTION_STALE);
        assert_eq!(s.backlight, BACKLIGHT_STALE);
    }

    #[test]
    fn reduce_motion_carries_every_state_without_motion() {
        // Accessibility: with motion frozen, each agent state must survive as STATIC tone.
        // busy → a static backlight step (held under bloom); snag → static fog+desaturate;
        // needs_you → a held warm lobe. And the canvas/air are frozen.
        let mut busy = SceneState::default();
        busy.apply(mood(1.0, 0.0, 0.0), 1.0, Freshness::Fresh, true);
        busy.settle();
        assert_eq!(busy.motion, MOTION_FROZEN, "reduce-motion freezes the drift");
        assert_eq!(busy.air, 0.0, "and freezes the wind/air impulse");
        assert_eq!(busy.backlight, BACKLIGHT_BUSY_MAX, "busy becomes a static brightness step");
        assert!(busy.backlight <= BACKLIGHT_MAX, "held under the compile-time ceiling");

        let mut snag = SceneState::default();
        snag.apply(mood(0.0, 0.0, 1.0), 0.0, Freshness::Fresh, true);
        snag.settle();
        assert_eq!(snag.motion, MOTION_FROZEN);
        assert_eq!(snag.fog, FOG_SNAG_MAX, "snag survives as a static fog-thicken");
        assert_eq!(snag.desat, DESAT_MAX, "+ a static desaturate (non-color cue)");

        let mut warm = SceneState::default();
        warm.apply(mood(0.0, 1.0, 0.0), 0.0, Freshness::Fresh, true);
        warm.settle();
        assert_eq!(warm.warm, WARM_MAX, "needs_you survives as a held (non-breathing) warm lobe");
        assert!(warm.reduce_motion, "and the frame flags reduce_motion so the applier holds it steady");
    }

    #[test]
    fn outputs_never_leave_compile_time_bounds() {
        // Fuzz the pure mapping over the input cube + freshness + reduce-motion; assert every eased
        // axis stays inside its compile-time clamp, tick after tick. A bound violation is a defect.
        let states = [Freshness::Fresh, Freshness::Stale, Freshness::Blind];
        for &fresh in &states {
            for &rm in &[false, true] {
                for bi in 0..=4 {
                    for wi in 0..=4 {
                        for si in 0..=4 {
                            let m = mood(bi as f64 / 4.0, wi as f64 / 4.0, si as f64 / 4.0);
                            let mut s = SceneState::default();
                            for _ in 0..200 {
                                s.apply(m, 1.0, fresh, rm);
                                s.tick(TICK_DT);
                                assert!((MOTION_MIN..=MOTION_MAX).contains(&s.motion));
                                assert!((FOG_MIN..=FOG_MAX).contains(&s.fog));
                                assert!((BACKLIGHT_MIN..=BACKLIGHT_MAX).contains(&s.backlight));
                                assert!((0.0..=WARM_MAX).contains(&s.warm));
                                assert!((0.0..=DESAT_MAX).contains(&s.desat));
                                assert!((0.0..=1.0).contains(&s.air));
                                assert!(s.motion.is_finite() && s.warm.is_finite());
                            }
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn no_throttle_axis_is_emitted() {
        // D3 disjoint channels: the disposer owns MOOD only — the throttle cvars (screen percentage,
        // MaxFPS, scalability groups, streaming pool) must NEVER appear in a scene frame. Pin their
        // absence in the serialized body so adding one breaks this test (and the idle-frame anchor).
        let body = settled(mood(1.0, 1.0, 1.0)).body();
        for forbidden in ["screen", "fps", "maxfps", "sg.", "streaming", "lumen", "scalability", "pool"] {
            assert!(
                !body.to_lowercase().contains(forbidden),
                "scene frame leaked a throttle key `{forbidden}`: {body}"
            );
        }
    }

    #[test]
    fn params_carry_no_strings_and_no_lease_lock() {
        // D6: the wallpaper consumes ONLY bounded scalars + an enum + a flag — NEVER a model-authored
        // string. `SceneState` is a plain Copy value (a String field would break Copy), which also
        // proves — like `wind.rs::wind_path_takes_no_inner_lock` — that this consumer holds nothing
        // of `lease::Inner` and cannot reach the SIGKILL path.
        fn assert_plain_value<T: Copy>() {}
        assert_plain_value::<SceneState>();
        assert_plain_value::<MoodInput>();
        assert_plain_value::<Freshness>();
    }

    // ---- untrusted-input readers ----

    fn tmp(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("agentos_scene_{}_{}", std::process::id(), name))
    }

    #[test]
    fn read_agent_validates_and_clamps() {
        let p = tmp("agent_ok.json");
        std::fs::write(&p, r#"{"state":1,"busy":0.7,"warm":0.0,"snag":0.0}"#).unwrap();
        assert_eq!(read_agent(&p), Some(mood(0.7, 0.0, 0.0)));
        // Out-of-range / NaN on disk is clamped/folded, never trusted raw.
        std::fs::write(&p, r#"{"busy":9.0,"warm":-1.0,"snag":0.5}"#).unwrap();
        assert_eq!(read_agent(&p), Some(mood(1.0, 0.0, 0.5)));
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn read_agent_absent_or_garbage_is_none() {
        let p = tmp("agent_absent.json");
        let _ = std::fs::remove_file(&p);
        assert_eq!(read_agent(&p), None, "absent → None (→ Blind)");
        std::fs::write(&p, "not json at all {{{").unwrap();
        assert_eq!(read_agent(&p), None, "garbage → None (→ Blind), never a half-trusted read");
        let _ = std::fs::remove_file(&p);
    }

    #[test]
    fn read_capped_rejects_oversized_and_symlinks() {
        // Oversized → None (a hostile multi-MB file can't be slurped).
        let big = tmp("big.json");
        std::fs::write(&big, vec![b'x'; (MAX_FEED_BYTES + 10) as usize]).unwrap();
        assert_eq!(read_capped(&big), None, "an oversized feed file is refused");
        let _ = std::fs::remove_file(&big);
        // Symlink → None (O_NOFOLLOW; a symlink swapped where a feed file should be is not followed).
        let target = tmp("symtarget.json");
        std::fs::write(&target, r#"{"busy":1.0}"#).unwrap();
        let link = tmp("symlink.json");
        let _ = std::fs::remove_file(&link);
        std::os::unix::fs::symlink(&target, &link).unwrap();
        assert_eq!(read_capped(&link), None, "a symlink is refused (O_NOFOLLOW)");
        let _ = std::fs::remove_file(&link);
        let _ = std::fs::remove_file(&target);
    }

    #[test]
    fn read_wind_honors_schema_and_clamps() {
        let p = tmp("wind_ok.json");
        std::fs::write(&p, r#"{"schema":1,"dir":[0,-1],"gust":0.4,"active":true,"updated_at":1.0}"#).unwrap();
        assert!((read_wind(&p) - 0.4).abs() < 1e-9);
        // Wrong schema → 0 (no air), never a misread.
        std::fs::write(&p, r#"{"schema":2,"gust":0.9}"#).unwrap();
        assert_eq!(read_wind(&p), 0.0, "a future/incompatible wind schema reads as no air");
        // Absent → 0.
        let _ = std::fs::remove_file(&p);
        assert_eq!(read_wind(&p), 0.0);
    }

    #[test]
    fn read_world_maps_absent_agent_to_blind() {
        // The end-to-end honest-UNKNOWN: no agent.json in the dir → Blind + neutral mood.
        let dir = tmp("world_blind.d");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let (m, _g, f) = read_world(&dir, 1000.0);
        assert_eq!(f, Freshness::Blind);
        assert_eq!(m, MoodInput::NEUTRAL);
        // A present agent.json with NO heartbeat reads Fresh (a live-but-idle producer is not stale).
        std::fs::write(dir.join("agent.json"), r#"{"busy":0.7,"warm":0,"snag":0}"#).unwrap();
        let (m2, _g2, f2) = read_world(&dir, 1_000_000.0);
        assert_eq!(f2, Freshness::Fresh, "present + no heartbeat = Fresh, never Stale on mtime");
        assert_eq!(m2, mood(0.7, 0.0, 0.0));
        // Add a STALE heartbeat → Stale (the feed is present but the producer went quiet).
        std::fs::write(dir.join("heartbeat.json"), r#"{"updated_at":100.0}"#).unwrap();
        let (_m3, _g3, f3) = read_world(&dir, 1_000_000.0);
        assert_eq!(f3, Freshness::Stale, "present feed + past-gate heartbeat = Stale");
        // A FRESH heartbeat → Fresh again.
        std::fs::write(dir.join("heartbeat.json"), r#"{"updated_at":999999.0}"#).unwrap();
        let (_m4, _g4, f4) = read_world(&dir, 1_000_000.0);
        assert_eq!(f4, Freshness::Fresh);
        let _ = std::fs::remove_dir_all(&dir);
    }
}

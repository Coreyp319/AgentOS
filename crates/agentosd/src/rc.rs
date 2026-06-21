//! `rc` — the reactive-MOOD Remote Control pusher (ADR-0030 D1 RC-mood path; ADR-0029 §B).
//!
//! The transport half of the reactive UE-wallpaper loop, on the side that talks to UE. The
//! `scene` disposer (`scene.rs`, ADR-0030 D2) reads the producer feeds and edge-writes ONE
//! pre-disposed `scene-params.json` (eased / clamped / slewed mood axes). THIS mode reads that
//! frame and PUSHES each axis into the live UE `-game` wallpaper as a **Material Parameter
//! Collection** scalar, over the loopback UE Remote Control HTTP surface (`127.0.0.1:30010`).
//!
//! It is the MOOD sink ONLY. The THROTTLE channel (the `Tier::Yielding` rung ladder,
//! `governor.rs`) is a SEPARATE sink with a SEPARATE security model and is GATED on the
//! lease-side coexistence model (ADR-0029 §B resource-safety ruling) — it is NOT served here.
//! Mixing the two would re-open the surface the §B lockdown closes; this module's wire carries
//! ONLY `SetScalarParameterValue` calls against the one reactive MPC, never a console command.
//!
//! ## Why a SEPARATE process (the load-bearing safety property, ADR-0029 §B / ADR-0003)
//! The reactive push must NEVER be able to delay a preemption SIGKILL on the lease daemon. Here
//! that is guaranteed by **process isolation**, the strongest possible form: `rc` runs as its own
//! `agentosd rc` process (like `scene`/`feed`/`keyhole`), in its own address space, owning no
//! lease state and sharing no lock with the `lease` daemon. A wedged PUT can wedge only THIS
//! process, which holds no VRAM, no lock, and no handle to anything the lease lane needs. The
//! module imports nothing from `lease`/`coord`/`governor`; `pusher_state_is_a_plain_value` pins
//! the state as a `Copy` value with no path to `lease::Inner` (the `wind.rs` tripwire analogue).
//!
//! ## The security contract this realizes (ADR-0029 §B "RE-GROUNDED 2026-06-21")
//!   * **Loopback-literal target, not env-overridable.** `RC_URL` is a compile-time
//!     `http://127.0.0.1:30010/...` literal — an IP literal (no DNS) that cannot be repointed off
//!     the box. `assert_loopback_target` refuses to run if it is ever edited to a non-loopback
//!     host; `rc_target_is_loopback` is the tripwire.
//!   * **Allowlisted verb, fixed param names.** Every PUT calls the engine-allowlisted
//!     `KismetMaterialLibrary::SetScalarParameterValue` (NOT `ExecuteConsoleCommand`, NOT the
//!     `/remote/object/property` preset route — both rejected by §B). `ParameterName` is drawn
//!     ONLY from the compile-time `AXES` set; no feed content ever names a parameter.
//!   * **Client-side clamp.** Each value is re-clamped to its axis bound and NaN-skipped before
//!     it leaves Rust — defense-in-depth atop the disposer's clamp and atop UE's own engine-side
//!     material-graph saturation (the wire is unauthenticated; any local process can also call it).
//!   * **Accepted residual (recorded honestly in §B):** loopback is NOT a trust boundary on UE's
//!     RC server, so any local process can also drive these bounded scalars. Acceptable because
//!     the blast radius is bounded wallpaper params and this lane cannot reach the lease/SIGKILL.
//!
//! ## Fail-open + design-for-restart (ADR-0003 / ADR-0029 §B resource-safety contract)
//!   * **Per-PUT timeout, single in-flight, best-effort drop.** The blocking client has a hard
//!     request timeout; the loop is sequential so exactly one PUT is ever in flight and nothing
//!     queues. A failed PUT is dropped — UE holds its last MPC value (the disposer keeps writing
//!     the file regardless; the next change re-pushes).
//!   * **Silent at rest.** Per-axis epsilon gate: a PUT is emitted only when an axis has moved
//!     more than `EPSILON` since its last *accepted* value. A byte-stable frame ⇒ zero PUTs ⇒ an
//!     idle desktop's pusher does nothing on the wire. `rc_is_silent_at_rest` pins this.
//!   * **Resync-on-reconnect = relaunch handling.** A relaunched UE boots at the MPC defaults
//!     (idle). Any *connection* failure (UE down / mid-relaunch) resets the epsilon baseline to a
//!     sentinel, so the first reachable tick re-pushes the FULL current frame and re-converges UE
//!     from idle — the file-feed analogue of "reset `last_sent` on relaunch" (we never reach into
//!     the lease lane for a PID; an unreachable socket IS the relaunch signal).
//!   * **Reads the DISPOSED frame, not the raw feeds.** One disposer, one grammar (ADR-0030 D1):
//!     `rc` is a dumb forwarder of `scene-params.json`; it never re-derives mood from
//!     `agent.json`/`wind.json`. The file is read untrusted (capped, `O_NOFOLLOW`, schema-gated).
//!
//! **GATED — prototype ahead of the live security verification (ADR-0029 §B GO/NO-GO).** This is
//! the agentosd half: buildable + unit-tested now (it needs only a file + a loopback socket). The
//! channel may only *actuate* once the cooked UE build proves the §B lockdown (default-deny
//! allowlist on, `ExecuteConsoleCommand` rejected, `ss -ltnp` loopback-only) on the live box.

use std::io::Read;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// Wire constants — the loopback RC target + the allowlisted call shape (ADR-0029 §B contract).
// ---------------------------------------------------------------------------

/// The UE Remote Control generic-call endpoint, LOOPBACK-LITERAL and fixed to UE's default RC
/// port (`remote_control_setup.md` §2a). Not env-overridable by construction — an IP literal so
/// there is no DNS, and no host knob to repoint it off the box (§B "no env-overridable host").
const RC_URL: &str = "http://127.0.0.1:30010/remote/object/call";

/// The CDO of the engine material library — the documented object path for calling its static
/// `BlueprintCallable` setters over RC (`remote_control_setup.md` §3, F4 of the §B review).
const KISMET_MAT_CDO: &str = "/Script/Engine.Default__KismetMaterialLibrary";

/// The one allowlisted reactive verb (§B F4). NEVER `ExecuteConsoleCommand`.
const SET_SCALAR_FN: &str = "SetScalarParameterValue";

/// The reactive MPC asset path (authored headless by `indigo_reactive_setup.py`). The pusher only
/// ever writes the fixed `AXES` scalars on THIS collection.
const MPC_PATH: &str = "/Game/AgentOS/Materials/MPC_AgentOS_Reactive.MPC_AgentOS_Reactive";

/// Hard per-PUT timeout. Comfortably above a loopback round-trip, well below the UE relaunch
/// window, so a half-up RC server that accepts-then-stalls cannot wedge the loop (§B resource-
/// safety High finding — "a connected-but-silent socket must not block indefinitely").
const PUT_TIMEOUT: Duration = Duration::from_millis(250);

/// Per-axis change threshold for emitting a PUT (~1/255 — below 8-bit perceptibility). At rest no
/// axis crosses it ⇒ the wire goes silent (the network analogue of the disposer's idle edge-write).
const EPSILON: f64 = 0.004;

/// The `scene-params.json` schema this pusher understands (must track `scene::SCHEMA`). A frame
/// with any other schema is skipped, not mis-applied (the SemVer gate, ADR-0023 schema discipline).
const SCENE_SCHEMA: u32 = 1;

/// Fast poll while pushing; back off when idle/unreachable so a 24/7 helper isn't a busy spinner.
/// Mirrors `scene.rs`'s adaptive cadence (the resource-safety idle-quiescence finding).
const TICK: Duration = Duration::from_millis(33);
const IDLE_POLL: Duration = Duration::from_millis(250);
/// Consecutive PUT-free fast ticks before backing off to `IDLE_POLL` (~1 s of stability at `TICK`).
const BACKOFF_AFTER_QUIET_TICKS: u32 = 30;

/// Max bytes read from `scene-params.json` (the disposer writes ~120 bytes; this bounds a hostile
/// file). Same untrusted-read discipline as `scene.rs`/`feed.rs`.
const MAX_FRAME_BYTES: u64 = 64 * 1024;

/// One reactive MPC scalar: the compile-time `ParameterName` (never feed-derived) + its clamp
/// bound (mirrors `scene.rs`'s per-axis bounds). The DISPOSER's output axes ARE the MPC scalar
/// names — one grammar end to end. `Motion`/`Fog`/`Backlight` are multiplicative levers (idle =
/// 1.0 = unchanged); `Warm`/`Desat`/`Air` are additive (idle = 0.0 = unchanged); `Fresh` is the
/// D9 freshness code (0/1/2); `ReduceMotion` is the a11y flag as 0.0/1.0.
#[derive(Clone, Copy)]
struct Axis {
    name: &'static str,
    lo: f64,
    hi: f64,
}

/// The fixed reactive scalar set — the renderer-side mailbox the UE material reads. Order is the
/// `last_sent` index order; bounds match `scene.rs` exactly so the client clamp can never widen
/// what the disposer already bounded.
const AXES: [Axis; 8] = [
    Axis { name: "Motion", lo: 0.0, hi: 1.6 },
    Axis { name: "Fog", lo: 0.5, hi: 1.5 },
    Axis { name: "Backlight", lo: 0.5, hi: 1.2 },
    Axis { name: "Warm", lo: 0.0, hi: 1.0 },
    Axis { name: "Desat", lo: 0.0, hi: 0.6 },
    Axis { name: "Air", lo: 0.0, hi: 1.0 },
    Axis { name: "Fresh", lo: 0.0, hi: 2.0 },
    Axis { name: "ReduceMotion", lo: 0.0, hi: 1.0 },
];
const N_AXES: usize = AXES.len();

// ---------------------------------------------------------------------------
// The disposed frame (read untrusted from scene-params.json) → the per-axis values to push.
// ---------------------------------------------------------------------------

/// `scene-params.json`'s on-disk shape (`scene::SceneState::fields`). Every field defaults so a
/// truncated/garbage frame degrades to idle rather than failing — but the `schema` gate rejects an
/// incompatible future frame outright (parse-fail / wrong-schema ⇒ the loop simply skips the tick).
#[derive(Deserialize)]
struct Frame {
    #[serde(default)]
    schema: u32,
    #[serde(default)]
    motion: f64,
    #[serde(default)]
    fog: f64,
    #[serde(default)]
    backlight: f64,
    #[serde(default)]
    warm: f64,
    #[serde(default)]
    desat: f64,
    #[serde(default)]
    air: f64,
    #[serde(default)]
    fresh: f64,
    #[serde(default)]
    reduce_motion: bool,
}

impl Frame {
    /// The raw axis values in `AXES` order (pre-clamp). `reduce_motion` projects to 0.0/1.0.
    fn raw(&self) -> [f64; N_AXES] {
        [
            self.motion,
            self.fog,
            self.backlight,
            self.warm,
            self.desat,
            self.air,
            self.fresh,
            if self.reduce_motion { 1.0 } else { 0.0 },
        ]
    }
}

/// Clamp a raw axis value to its bound; a non-finite value yields `None` (skip the axis — never
/// PUT a NaN/inf onto the wire, the `scene.rs`/`wind.rs` NaN discipline at the transport edge).
fn clamp_axis(i: usize, v: f64) -> Option<f64> {
    if v.is_finite() {
        Some(v.clamp(AXES[i].lo, AXES[i].hi))
    } else {
        None
    }
}

/// Should this axis be pushed? Yes if it has never been accepted (`last` is the non-finite
/// sentinel — cold start or post-reconnect resync) or it has moved more than `EPSILON` since the
/// last accepted value. Pure → the silent-at-rest + resync behavior is unit-tested without a socket.
fn should_push(last: f64, val: f64) -> bool {
    !last.is_finite() || (val - last).abs() > EPSILON
}

// ---------------------------------------------------------------------------
// The RC call body — the EXACT §B wire contract (allowlisted setter, structured fields).
// ---------------------------------------------------------------------------

/// `parameters` of the `SetScalarParameterValue` call. `WorldContextObject: null` is the documented
/// CDO-call form (`remote_control_setup.md`); whether `null` resolves for the material library is
/// the one `[VERIFY-LIVE]` of §B (fallback: a thin allowlisted `UAgentOSReactive::SetMood` UFUNCTION
/// that grabs the world itself — still never the property/preset route). `ParameterName` comes from
/// `AXES` only.
#[derive(Serialize)]
struct CallParams {
    #[serde(rename = "WorldContextObject")]
    world_context_object: Option<()>, // serializes to JSON `null`
    #[serde(rename = "Collection")]
    collection: &'static str,
    #[serde(rename = "ParameterName")]
    parameter_name: &'static str,
    #[serde(rename = "ParameterValue")]
    parameter_value: f64,
}

/// The `PUT /remote/object/call` body for one MPC scalar set.
#[derive(Serialize)]
struct CallBody {
    #[serde(rename = "objectPath")]
    object_path: &'static str,
    #[serde(rename = "functionName")]
    function_name: &'static str,
    parameters: CallParams,
    #[serde(rename = "generateTransaction")]
    generate_transaction: bool,
}

impl CallBody {
    /// Build the call for `AXES[i] = value`. `value` is assumed already clamped by `clamp_axis`.
    fn scalar(name: &'static str, value: f64) -> CallBody {
        CallBody {
            object_path: KISMET_MAT_CDO,
            function_name: SET_SCALAR_FN,
            parameters: CallParams {
                world_context_object: None,
                collection: MPC_PATH,
                parameter_name: name,
                parameter_value: value,
            },
            generate_transaction: false,
        }
    }
}

/// The outcome of one PUT attempt — distinguishes a *connection* failure (UE down / mid-relaunch →
/// resync on return) from a *rejection* (UE up but refused the call → a wire/allowlist problem, log
/// once, do not resync). Keeps the loop quiet and the relaunch story precise.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum PutOutcome {
    Applied,
    Rejected,
    Unreachable,
}

/// PUT one MPC scalar to `url` (injected so tests can target a loopback mock). Best-effort: a
/// transport error ⇒ `Unreachable`; a non-2xx (incl. a refused redirect) ⇒ `Rejected`; a 2xx ⇒
/// `Applied`. The loopback guard is a HARD, release-present refusal (not just a `debug_assert`):
/// even though the loop only ever passes the validated const `RC_URL`, a non-loopback `url` here is
/// refused by construction, so a future caller that wires a non-const URL can't lose the guarantee.
fn put_scalar(
    client: &reqwest::blocking::Client,
    url: &str,
    name: &'static str,
    value: f64,
) -> PutOutcome {
    if !url_is_loopback(url) {
        // Fail closed: never put a body onto a non-loopback target (§B "127.0.0.1 by construction").
        return PutOutcome::Rejected;
    }
    let body = CallBody::scalar(name, value);
    // The client is built with `redirect::Policy::none()`, so a 3xx is returned as-is (status not
    // success ⇒ Rejected) and NEVER followed off the loopback target — the redirect-bypass guard.
    match client.put(url).json(&body).send() {
        Ok(resp) if resp.status().is_success() => PutOutcome::Applied,
        Ok(_) => PutOutcome::Rejected,
        Err(_) => PutOutcome::Unreachable,
    }
}

// ---------------------------------------------------------------------------
// Loopback assertion — refuse to drive a non-loopback target (§B "127.0.0.1 by construction").
// ---------------------------------------------------------------------------

/// Does `url`'s host parse to a loopback IP LITERAL? Accepts only `127.0.0.0/8` and `[::1]`-style
/// loopback literals — a NAME (even `localhost`) is refused, honoring the §B "IP literal, no DNS /
/// no name resolution" contract: `localhost` resolves through `/etc/hosts`/NSS, which a tampered
/// hosts file could repoint, so it is not a loopback guarantee. `RC_URL` is `127.0.0.1`; this guard
/// is the tripwire if it is ever edited to a name or a routable address. Used at startup AND per-PUT.
fn url_is_loopback(url: &str) -> bool {
    // Strip scheme, take the authority up to the first `/`, drop any `user@`, then the host.
    let after_scheme = url.split("://").nth(1).unwrap_or(url);
    let authority = after_scheme.split('/').next().unwrap_or("");
    let authority = authority.rsplit_once('@').map(|(_, h)| h).unwrap_or(authority);
    let host = if let Some(rest) = authority.strip_prefix('[') {
        // Bracketed IPv6 literal (`[::1]:port`): the host is everything up to `]`.
        rest.split(']').next().unwrap_or(rest)
    } else {
        // `host:port` → host before the last colon (bare IPv4 / hostname).
        authority.rsplit_once(':').map(|(h, _)| h).unwrap_or(authority)
    };
    // IP literal ONLY — a name (incl. `localhost`) is refused (no resolver step).
    host.parse::<std::net::IpAddr>().map(|ip| ip.is_loopback()).unwrap_or(false)
}

/// Refuse to run against a non-loopback target (the runtime half of the §B "no env-overridable
/// host" guard — even though `RC_URL` is a const, this is the tripwire if it is ever edited).
fn assert_loopback_target(url: &str) -> Result<(), String> {
    if url_is_loopback(url) {
        Ok(())
    } else {
        Err(format!("rc: RC target `{url}` is not loopback — refusing (ADR-0029 §B)"))
    }
}

// ---------------------------------------------------------------------------
// Untrusted reader for the DISPOSED frame (same discipline as scene.rs's feed readers).
// ---------------------------------------------------------------------------

/// Read at most `MAX_FRAME_BYTES` from `path` with an `O_NOFOLLOW` open (a symlink where the frame
/// should be is refused, not followed). `None` = absent / symlink / unreadable / oversized.
fn read_capped(path: &Path) -> Option<String> {
    let mut f = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .ok()?;
    let mut buf = Vec::new();
    f.by_ref().take(MAX_FRAME_BYTES + 1).read_to_end(&mut buf).ok()?;
    if buf.len() as u64 > MAX_FRAME_BYTES {
        return None;
    }
    String::from_utf8(buf).ok()
}

/// Read + untrust `scene-params.json` → a schema-checked `Frame`, or `None` (absent / symlink /
/// oversized / parse-fail / wrong-schema). `None` ⇒ the loop skips the tick (UE holds last-good).
fn read_frame(path: &Path) -> Option<Frame> {
    let s = read_capped(path)?;
    let f: Frame = serde_json::from_str(&s).ok()?;
    if f.schema == SCENE_SCHEMA {
        Some(f)
    } else {
        None
    }
}

// ---------------------------------------------------------------------------
// The poll → push loop.
// ---------------------------------------------------------------------------

/// The non-finite sentinel that forces a (re)push of every axis: equals no value, so `should_push`
/// is always true until an axis is first accepted, and resetting to it re-syncs the full frame.
const UNSENT: f64 = f64::NAN;

/// Build the blocking RC client with the per-PUT timeout AND `redirect::Policy::none()` — a 3xx must
/// never be followed off the loopback target (the §B redirect-bypass guard). Single-sourced so the
/// loop and the tests share the exact production transport config.
fn build_client() -> reqwest::Result<reqwest::blocking::Client> {
    reqwest::blocking::Client::builder()
        .timeout(PUT_TIMEOUT)
        .redirect(reqwest::redirect::Policy::none())
        .build()
}

/// The pusher's WHOLE mutable state — a plain `Copy` value with NO field of, and no path to,
/// `lease::Inner` (the `wind.rs::wind_path_takes_no_inner_lock` / `scene.rs` tripwire, now over the
/// entire state). `last_sent[i]` is the last *accepted* value for `AXES[i]` (`UNSENT`/NaN forces a
/// push); `reachable` gates once-logging (None until the first attempt); `rejecting` tracks a
/// "UE up but refusing" run for once-logging + reject-recovery resync.
#[derive(Clone, Copy)]
struct PusherState {
    last_sent: [f64; N_AXES],
    reachable: Option<bool>,
    rejecting: bool,
}

impl PusherState {
    fn new() -> Self {
        PusherState { last_sent: [UNSENT; N_AXES], reachable: None, rejecting: false }
    }
    /// Re-arm a FULL resync: mark every axis unsent so the next reachable tick re-pushes the whole
    /// frame. Used on reconnect (UE relaunches at MPC defaults) and on reject→accept recovery.
    fn arm_full_resync(&mut self) {
        self.last_sent = [UNSENT; N_AXES];
    }
}

/// What one read→push pass accomplished — drives the adaptive cadence. ONLY `Progressed` (≥1 PUT
/// actually applied to a reachable UE) keeps the loop fast; `Quiet` (nothing crossed epsilon) and
/// `Stalled` (UE unreachable / refusing — nothing applied) both back the cadence OFF. So neither a
/// settled desktop NOR a down/absent UE makes this a 30 Hz spinner (resource-safety MED-2).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TickOutcome {
    Progressed,
    Quiet,
    Stalled,
}

fn feed_dir() -> std::io::Result<PathBuf> {
    crate::feed::feed_dir()
}

/// `agentosd rc [--once]` — read the disposed `scene-params.json` and push its mood axes into the
/// live UE wallpaper's reactive MPC over loopback Remote Control. `--once` does a single full push
/// pass and exits (verify/CI; succeeds trivially if UE/RC is up, else logs Unreachable and exits 0).
/// Fail-open throughout (ADR-0003): a missing frame / unreachable RC is a no-op that holds last-good.
pub fn run(once: bool) -> Result<(), Box<dyn std::error::Error>> {
    // §B: refuse a non-loopback target by construction — before opening any socket.
    if let Err(e) = assert_loopback_target(RC_URL) {
        eprintln!("{e}");
        std::process::exit(2);
    }

    let dir = feed_dir()?;
    let frame_path = dir.join("scene-params.json");
    let client = build_client()?;

    println!(
        "agentosd rc → {RC_URL} (reactive MOOD pusher, ADR-0030 D1 / ADR-0029 §B; reading {})",
        frame_path.display()
    );

    let mut st = PusherState::new();
    let mut quiet_ticks: u32 = 0;

    loop {
        let outcome = push_once(&client, RC_URL, &frame_path, &mut st);

        if once {
            println!(
                "[{}] once: reachable={:?} outcome={:?}",
                crate::now_hms(),
                st.reachable,
                outcome
            );
            return Ok(());
        }

        // Adaptive cadence: stay fast ONLY while actively converging on a reachable UE. A settled
        // frame (`Quiet`) AND a down/refusing UE (`Stalled`) both back off — so neither an idle
        // desktop nor an absent UE makes this a 30 Hz spinner (resource-safety MED-2).
        quiet_ticks = if outcome == TickOutcome::Progressed {
            0
        } else {
            quiet_ticks.saturating_add(1)
        };
        std::thread::sleep(poll_interval(quiet_ticks));
    }
}

/// One read→push pass. Returns a `TickOutcome` (drives the cadence). Mutates `st` for the
/// epsilon-gate, resync-on-reconnect/reject-recovery, and once-logging. `url` is injected (the loop
/// passes the loopback `RC_URL`; tests pass a loopback mock).
///
/// The per-axis sweep `break`s on the FIRST failure (`Rejected` OR `Unreachable`), so one tick's
/// wall-time is bounded by the fast successes plus a single failure-timeout, NOT `N × timeout`
/// (resource-safety HIGH-1) — a slow/refusing server can't make a tick chew `8 × PUT_TIMEOUT`.
fn push_once(
    client: &reqwest::blocking::Client,
    url: &str,
    frame_path: &Path,
    st: &mut PusherState,
) -> TickOutcome {
    let frame = match read_frame(frame_path) {
        Some(f) => f,
        None => return TickOutcome::Quiet, // no/garbage/incompatible frame — hold last-good, no traffic
    };
    let raw = frame.raw();
    let mut applied_any = false;
    let mut attempted_any = false;

    for i in 0..N_AXES {
        let value = match clamp_axis(i, raw[i]) {
            Some(v) => v,
            None => continue, // non-finite axis — never PUT a NaN
        };
        if !should_push(st.last_sent[i], value) {
            continue;
        }
        attempted_any = true;
        match put_scalar(client, url, AXES[i].name, value) {
            PutOutcome::Applied => {
                // Reject-recovery: the FIRST Applied after a reject run re-arms a full resync, so the
                // axes that were marked-sent-while-rejected (never actually applied) re-push once the
                // wire is healthy again (rust-perf Low — the reject→accept blind spot).
                if st.rejecting {
                    st.rejecting = false;
                    st.arm_full_resync();
                }
                st.last_sent[i] = value;
                applied_any = true;
                if st.reachable != Some(true) {
                    println!(
                        "[{}] rc: RC reachable — converging the wallpaper mood",
                        crate::now_hms()
                    );
                    st.reachable = Some(true);
                }
            }
            PutOutcome::Rejected => {
                // UE is up but refused the call (allowlist / WorldContextObject not resolving). Mark
                // accepted to avoid per-tick spam; surface ONCE — the §B `[VERIFY-LIVE]` seam. Break:
                // a refusing server gets ONE PUT per tick, not eight (bounds per-tick wall time).
                st.last_sent[i] = value;
                st.reachable = Some(true);
                if !st.rejecting {
                    eprintln!(
                        "[{}] rc: RC rejected {} (status non-2xx) — check the SetScalarParameterValue \
                         allowlist + WorldContextObject:null resolution [ADR-0029 §B VERIFY-LIVE]",
                        crate::now_hms(),
                        AXES[i].name
                    );
                    st.rejecting = true;
                }
                break;
            }
            PutOutcome::Unreachable => {
                // UE down / mid-relaunch. Reset the WHOLE baseline so the first reachable tick
                // re-pushes the full frame (UE relaunches at MPC defaults → re-converge from idle).
                st.arm_full_resync();
                if st.reachable != Some(false) {
                    println!(
                        "[{}] rc: RC unreachable (UE down / mid-relaunch) — holding; will resync on return",
                        crate::now_hms()
                    );
                    st.reachable = Some(false);
                }
                break; // don't hammer a dead socket for the remaining axes this tick
            }
        }
    }

    if applied_any {
        TickOutcome::Progressed
    } else if attempted_any {
        TickOutcome::Stalled // attempted, but only refused/unreachable — back the cadence off
    } else {
        TickOutcome::Quiet // nothing crossed epsilon — silent at rest
    }
}

/// The poll cadence as a pure function of how long we've been quiet (no PUT). Fast while moving,
/// slow once settled. Pure → unit-tested without the loop (mirrors `scene::poll_interval`).
fn poll_interval(quiet_ticks: u32) -> Duration {
    if quiet_ticks < BACKOFF_AFTER_QUIET_TICKS {
        TICK
    } else {
        IDLE_POLL
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write as _;
    use std::net::TcpListener;

    // ---- structural safety (the wind.rs/scene.rs tripwire analogue) ----

    #[test]
    fn pusher_state_is_a_plain_value() {
        // The pusher's whole state is `[f64; N]` + small flags — a `Copy` value with NO field of,
        // and no path to, `lease::Inner`. Like `wind_path_takes_no_inner_lock`, this module could
        // not even be written to touch the lease lock: it imports nothing from `lease`. (Process
        // isolation makes it stronger still — `rc` is its own process.)
        fn assert_plain_value<T: Copy>() {}
        assert_plain_value::<PusherState>(); // the WHOLE state is a Copy value — strongest tripwire
        assert_plain_value::<[f64; N_AXES]>();
        assert_plain_value::<PutOutcome>();
        assert_plain_value::<TickOutcome>();
        assert_plain_value::<Axis>();
    }

    // ---- the loopback guard (§B "127.0.0.1 by construction") ----

    #[test]
    fn rc_target_is_loopback() {
        // The shipped const target MUST be loopback. If a future edit points it off-box, this fails.
        assert!(url_is_loopback(RC_URL), "RC_URL must be loopback");
        assert!(assert_loopback_target(RC_URL).is_ok());
    }

    #[test]
    fn non_loopback_targets_are_refused() {
        // Loopback IP LITERALS pass (v4 and bracketed v6).
        assert!(url_is_loopback("http://127.0.0.1:30010/x"));
        assert!(url_is_loopback("http://127.5.6.7:30010/x")); // all of 127.0.0.0/8
        assert!(url_is_loopback("http://[::1]:30010/x"));
        // A NAME — even `localhost` — is refused: §B is "IP literal, no DNS / no resolver step"
        // (a tampered /etc/hosts could repoint `localhost`).
        assert!(!url_is_loopback("http://localhost:30010/x"));
        // Routable / wildcard / hostile hosts are refused, incl. an `@`-smuggled host.
        assert!(!url_is_loopback("http://10.0.0.5:30010/x"));
        assert!(!url_is_loopback("http://evil.example.com:30010/x"));
        assert!(!url_is_loopback("http://0.0.0.0:30010/x"));
        assert!(!url_is_loopback("http://127.0.0.1@evil.example.com:30010/x"));
        assert!(assert_loopback_target("http://192.168.1.9:30010/x").is_err());
    }

    // ---- the epsilon gate (silent at rest) ----

    #[test]
    fn should_push_cold_start_then_silent() {
        // Cold start: the sentinel forces a push of any value.
        assert!(should_push(UNSENT, 1.0));
        // After accepting, an identical value does NOT push.
        assert!(!should_push(1.0, 1.0));
        // A sub-epsilon nudge does NOT push; a supra-epsilon move does.
        assert!(!should_push(1.0, 1.0 + EPSILON / 2.0));
        assert!(should_push(1.0, 1.0 + EPSILON * 2.0));
    }

    #[test]
    fn rc_is_silent_at_rest() {
        // After a full sync, re-presenting the SAME frame yields zero axes to push.
        let frame = Frame {
            schema: 1,
            motion: 1.2,
            fog: 1.1,
            backlight: 1.0,
            warm: 0.3,
            desat: 0.2,
            air: 0.0,
            fresh: 0.0,
            reduce_motion: false,
        };
        let raw = frame.raw();
        // Seed last_sent as if every axis was just accepted.
        let mut last_sent = [UNSENT; N_AXES];
        for i in 0..N_AXES {
            last_sent[i] = clamp_axis(i, raw[i]).unwrap();
        }
        let to_push = (0..N_AXES)
            .filter(|&i| {
                clamp_axis(i, raw[i]).map(|v| should_push(last_sent[i], v)).unwrap_or(false)
            })
            .count();
        assert_eq!(to_push, 0, "a settled frame must produce zero PUTs (silent at rest)");
    }

    // ---- clamp + NaN discipline ----

    #[test]
    fn clamp_bounds_and_skips_nonfinite() {
        // Motion clamps to [0, 1.6]; an over-range value is clamped, not sent raw.
        assert_eq!(clamp_axis(0, 99.0), Some(1.6));
        assert_eq!(clamp_axis(0, -5.0), Some(0.0));
        // Fog floor is 0.5.
        assert_eq!(clamp_axis(1, 0.0), Some(0.5));
        // A non-finite axis is dropped (never a NaN on the wire).
        assert_eq!(clamp_axis(0, f64::NAN), None);
        assert_eq!(clamp_axis(3, f64::INFINITY), None);
    }

    // ---- the wire contract (the exact §B body) ----

    #[test]
    fn call_body_is_the_allowlisted_setter_with_fixed_names() {
        let body = CallBody::scalar("Motion", 1.25);
        let v = serde_json::to_value(&body).unwrap();
        assert_eq!(v["objectPath"], KISMET_MAT_CDO);
        assert_eq!(v["functionName"], "SetScalarParameterValue");
        assert!(v["parameters"]["WorldContextObject"].is_null());
        assert_eq!(v["parameters"]["Collection"], MPC_PATH);
        assert_eq!(v["parameters"]["ParameterName"], "Motion");
        assert_eq!(v["parameters"]["ParameterValue"], 1.25);
        assert_eq!(v["generateTransaction"], false);
        // It is NOT a console-exec call (the §B hole) — pin the absence.
        let s = serde_json::to_string(&body).unwrap();
        assert!(!s.contains("ExecuteConsoleCommand"));
        assert!(!s.contains("Command"));
    }

    #[test]
    fn parameter_names_are_a_fixed_compile_time_set() {
        // Every wire ParameterName is drawn ONLY from AXES — no feed content can name a parameter.
        let expected = ["Motion", "Fog", "Backlight", "Warm", "Desat", "Air", "Fresh", "ReduceMotion"];
        let got: Vec<&str> = AXES.iter().map(|a| a.name).collect();
        assert_eq!(got, expected);
    }

    // ---- frame read (untrusted) ----

    #[test]
    fn read_frame_gates_schema_and_parse() {
        let p = std::env::temp_dir().join(format!("agentos_rc_{}_frame.json", std::process::id()));
        // A valid schema-1 frame parses.
        std::fs::write(
            &p,
            r#"{"schema":1,"motion":1.2,"fog":1.0,"backlight":1.0,"warm":0,"desat":0,"air":0,"fresh":0,"reduce_motion":false,"updated_at":1.0}"#,
        )
        .unwrap();
        let f = read_frame(&p).expect("schema-1 frame parses");
        assert_eq!(f.raw()[0], 1.2);
        // A future/incompatible schema is rejected (not mis-applied).
        std::fs::write(&p, r#"{"schema":2,"motion":9.9}"#).unwrap();
        assert!(read_frame(&p).is_none(), "wrong schema must be skipped");
        // Garbage parses to None.
        std::fs::write(&p, b"not json").unwrap();
        assert!(read_frame(&p).is_none());
        let _ = std::fs::remove_file(&p);
    }

    // ---- end-to-end transport against a loopback mock (no UE needed) ----

    /// A loopback HTTP mock that serves exactly `n` requests, replying `status` to each, and returns
    /// the raw request bytes. Loopback-only by construction (`127.0.0.1:0`). `n == 1` is the common case.
    fn mock_rc_n(
        status_line: &'static str,
        n: usize,
    ) -> (String, std::thread::JoinHandle<Vec<String>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let url = format!("http://{addr}/remote/object/call");
        let handle = std::thread::spawn(move || {
            let mut reqs = Vec::with_capacity(n);
            for _ in 0..n {
                let (mut sock, _) = match listener.accept() {
                    Ok(s) => s,
                    Err(_) => break,
                };
                let mut buf = vec![0u8; 8192];
                let read = sock.read(&mut buf).unwrap_or(0);
                reqs.push(String::from_utf8_lossy(&buf[..read]).to_string());
                let resp =
                    format!("{status_line}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                let _ = sock.write_all(resp.as_bytes());
            }
            reqs
        });
        (url, handle)
    }

    fn mock_rc(status_line: &'static str) -> (String, std::thread::JoinHandle<String>) {
        let (url, handle) = mock_rc_n(status_line, 1);
        let h = std::thread::spawn(move || handle.join().unwrap().pop().unwrap_or_default());
        (url, h)
    }

    fn write_frame(dir: &Path, json: &str) -> PathBuf {
        std::fs::create_dir_all(dir).unwrap();
        let p = dir.join("scene-params.json");
        std::fs::write(&p, json).unwrap();
        p
    }

    /// A loopback mock that accepts connections in a loop FOREVER on a detached thread (never joined,
    /// dies at process exit), replying `status` to each. For tests that don't count requests and must
    /// not block on a `join` for an unfilled accept.
    fn mock_rc_accept_all(status_line: &'static str) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            for conn in listener.incoming() {
                let mut sock = match conn {
                    Ok(s) => s,
                    Err(_) => continue,
                };
                let mut buf = [0u8; 8192];
                let _ = sock.read(&mut buf);
                let resp =
                    format!("{status_line}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                let _ = sock.write_all(resp.as_bytes());
            }
        });
        format!("http://{addr}/remote/object/call")
    }

    #[test]
    fn put_scalar_sends_the_contract_and_reads_200() {
        let (url, handle) = mock_rc("HTTP/1.1 200 OK");
        let client = build_client().unwrap();
        let outcome = put_scalar(&client, &url, "Fog", 1.3);
        assert_eq!(outcome, PutOutcome::Applied);
        let req = handle.join().unwrap();
        assert!(req.starts_with("PUT /remote/object/call HTTP/1.1"), "method+path: {req}");
        assert!(req.contains("\"functionName\":\"SetScalarParameterValue\""), "verb: {req}");
        assert!(req.contains("\"ParameterName\":\"Fog\""), "param name: {req}");
        assert!(req.contains("\"ParameterValue\":1.3"), "param value: {req}");
        assert!(req.contains("\"WorldContextObject\":null"), "world ctx: {req}");
    }

    #[test]
    fn put_scalar_maps_non_2xx_to_rejected() {
        let (url, handle) = mock_rc("HTTP/1.1 400 Bad Request");
        let client = build_client().unwrap();
        let outcome = put_scalar(&client, &url, "Motion", 1.0);
        assert_eq!(outcome, PutOutcome::Rejected);
        let _ = handle.join();
    }

    #[test]
    fn put_scalar_maps_dead_socket_to_unreachable() {
        // Bind+drop to get a port nothing is listening on → connect refused → Unreachable.
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        drop(listener);
        let url = format!("http://{addr}/remote/object/call");
        let client = build_client().unwrap();
        assert_eq!(put_scalar(&client, &url, "Air", 0.5), PutOutcome::Unreachable);
    }

    // ---- push_once end-to-end: cold full-push → silent at rest (driven through the real loop fn) ----

    #[test]
    fn push_once_cold_pushes_all_then_goes_silent() {
        let dir = std::env::temp_dir().join(format!("agentos_rc_{}_cold", std::process::id()));
        let frame_path = write_frame(
            &dir,
            r#"{"schema":1,"motion":1.4,"fog":1.2,"backlight":1.05,"warm":0.5,"desat":0.3,"air":0.2,"fresh":0,"reduce_motion":false,"updated_at":1.0}"#,
        );
        // All 8 axes are finite ⇒ a cold pass pushes all 8.
        let (url, handle) = mock_rc_n("HTTP/1.1 200 OK", N_AXES);
        let client = build_client().unwrap();

        let mut st = PusherState::new();
        let outcome = push_once(&client, &url, &frame_path, &mut st);
        assert_eq!(outcome, TickOutcome::Progressed, "a cold pass converges on a reachable UE");
        assert_eq!(st.reachable, Some(true));
        assert!(st.last_sent.iter().all(|v| v.is_finite()), "every axis accepted after a full sync");
        let reqs = handle.join().unwrap();
        assert_eq!(reqs.len(), N_AXES, "cold start pushes every axis once");

        // Re-present the SAME frame: nothing crosses epsilon ⇒ Quiet, zero PUTs (silent at rest).
        // Point at a dead url to PROVE no PUT is attempted (it would otherwise read Unreachable).
        let dead = {
            let l = TcpListener::bind("127.0.0.1:0").unwrap();
            let a = l.local_addr().unwrap();
            drop(l);
            format!("http://{a}/remote/object/call")
        };
        let outcome2 = push_once(&client, &dead, &frame_path, &mut st);
        assert_eq!(outcome2, TickOutcome::Quiet, "a settled frame attempts no PUTs (silent at rest)");
        assert_eq!(st.reachable, Some(true), "reachability unchanged when nothing is sent");

        let _ = std::fs::remove_dir_all(&dir);
    }

    // ---- resync-on-reconnect (relaunch handling) + cadence-backoff on a down UE ----

    #[test]
    fn unreachable_stalls_and_resets_the_whole_baseline_for_resync() {
        let dir = std::env::temp_dir().join(format!("agentos_rc_{}_resync", std::process::id()));
        let frame_path = write_frame(
            &dir,
            r#"{"schema":1,"motion":1.4,"fog":1.2,"backlight":1.0,"warm":0.5,"desat":0.3,"air":0.2,"fresh":0,"reduce_motion":false,"updated_at":1.0}"#,
        );
        // Dead socket ⇒ the first PUT is Unreachable.
        let dead_url = {
            let l = TcpListener::bind("127.0.0.1:0").unwrap();
            let a = l.local_addr().unwrap();
            drop(l);
            format!("http://{a}/remote/object/call")
        };
        let client = build_client().unwrap();

        // Pre-seed a "fully synced" baseline (as if UE was up), then go Unreachable.
        let mut st = PusherState { last_sent: [0.5_f64; N_AXES], reachable: Some(true), rejecting: false };
        let outcome = push_once(&client, &dead_url, &frame_path, &mut st);

        // Stalled (attempted, nothing applied) ⇒ the cadence backs OFF — NOT a 30 Hz spinner while
        // UE is down (resource-safety MED-2). And the baseline is wiped for a reconnect resync.
        assert_eq!(outcome, TickOutcome::Stalled, "an unreachable UE stalls (backs the cadence off)");
        assert_eq!(st.reachable, Some(false), "an unreachable RC is reported once");
        assert!(
            st.last_sent.iter().all(|v| !v.is_finite()),
            "Unreachable must reset EVERY axis to the sentinel so reconnect re-pushes the full frame"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn redirect_is_not_followed_off_loopback() {
        // A 3xx carrying an off-box Location must be REJECTED, never followed — the loopback target
        // is final (redirect::Policy::none(), security Medium). The body never leaves the box.
        let (url, handle) = mock_rc("HTTP/1.1 302 Found\r\nLocation: http://evil.example.com/x");
        let client = build_client().unwrap();
        assert_eq!(put_scalar(&client, &url, "Warm", 0.4), PutOutcome::Rejected);
        let _ = handle.join();
    }

    #[test]
    fn reject_then_recover_arms_a_full_resync() {
        // The reject→accept blind spot (rust-perf Low): on recovery, the axes marked-sent-while-
        // rejected (never actually applied) must re-converge, not silently stay stale.
        let dir = std::env::temp_dir().join(format!("agentos_rc_{}_reject", std::process::id()));
        let frame_path = write_frame(
            &dir,
            r#"{"schema":1,"motion":1.4,"fog":1.2,"backlight":1.0,"warm":0.5,"desat":0.3,"air":0.2,"fresh":0,"reduce_motion":false,"updated_at":1.0}"#,
        );
        let client = build_client().unwrap();
        let mut st = PusherState::new();

        // Phase 1: UE up but REFUSING → Stalled, `rejecting` flagged, ONE PUT (break-on-reject).
        let url_rej = mock_rc_accept_all("HTTP/1.1 400 Bad Request");
        let o1 = push_once(&client, &url_rej, &frame_path, &mut st);
        assert_eq!(o1, TickOutcome::Stalled, "a reject pass makes no progress");
        assert!(st.rejecting, "the reject run is flagged for once-logging + recovery");

        // Phase 2: UE now ACCEPTS → Progressed; the first Applied clears `rejecting` AND arms a full
        // resync. A follow-up pass re-pushes the axis arm_full_resync re-marked → all axes converge.
        let url_ok = mock_rc_accept_all("HTTP/1.1 200 OK");
        let o2 = push_once(&client, &url_ok, &frame_path, &mut st);
        assert_eq!(o2, TickOutcome::Progressed);
        assert!(!st.rejecting, "recovery clears the rejecting flag");
        let _ = push_once(&client, &url_ok, &frame_path, &mut st);
        assert!(
            st.last_sent.iter().all(|v| v.is_finite()),
            "every axis re-converged after reject→accept recovery"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    // ---- poll cadence ----

    #[test]
    fn poll_cadence_fast_then_backs_off() {
        assert_eq!(poll_interval(0), TICK);
        assert_eq!(poll_interval(BACKOFF_AFTER_QUIET_TICKS - 1), TICK);
        assert_eq!(poll_interval(BACKOFF_AFTER_QUIET_TICKS), IDLE_POLL);
    }
}

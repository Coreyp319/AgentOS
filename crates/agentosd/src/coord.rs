//! `coord` — the VRAM coordinator slice (ADR-0010).
//!
//! This is the substrate floor that turns ADR-0009/0010/0011's `[SUBSTRATE-BLOCKED]`
//! items into real behaviour. It proves three things against the live GPU, the way
//! `monitor` already proves the read path:
//!
//!   1. **Own the PID.** agentosd spawns the heavy GPU consumer (e.g. ComfyUI) and
//!      holds its `Child` — eviction is own-PID + SIGKILL, never a polite ask
//!      (`POST /free` measured freeing 0 VRAM — ADR-0009/0010 §5).
//!   2. **Predict before load** (ADR-0010 §4). Admission is deterministic budget math:
//!      grant a lease only if the estimated footprint + headroom fits the measured free
//!      VRAM. Never start-and-OOM; declining IS the success path.
//!   3. **SIGKILL on preempt** (ADR-0010 §5/§6). An interactive request (here: SIGUSR1)
//!      out-prioritises the overnight batch holder → the owned child is SIGKILLed, VRAM
//!      is reclaimed (read before/after to prove it), the lease releases, and in
//!      production the job requeues for the next window.
//!
//! Scope discipline (ADR-0001): this is a *lease + process supervisor*, not a scheduler.
//! There is ONE exclusive lease and ONE holder at a time; Hermes' cron drives the
//! overnight *sequence*, agentosd only enforces *one at a time* and *who yields to whom*.
//! The D-Bus lease server (ADR-0006) and the proxy are deliberately out of this slice —
//! but the `tokio` + `spawn_blocking` shift introduced here is the runtime they need.
//!
//! Decision math (`admit`, `arbitrate`, `Tier`) is pure and unit-tested; the runtime
//! (`supervise`) is the thin impure shell that the tests can't reach. Model proposes,
//! code disposes: no model output ever reaches the grant/deny/evict decision.

use std::process::ExitStatus;
use std::sync::Arc;
use std::time::Duration;

use crate::gpu::GpuBackend;
use tokio::process::{Child, Command};
use tokio::signal::unix::{signal, SignalKind};

// ---------------------------------------------------------------------------
// Pure decision core (the `derive_feed` analog — unit-tested below).
// ---------------------------------------------------------------------------

/// Lease priority tiers (ADR-0010 §2, extended by ADR-0029 §3). A strictly-higher tier
/// PREEMPTS a lower one; `interactive/live inference > overnight batch > best-effort >
/// yielding (the UE wallpaper)`. Ordering is by DECLARATION POSITION — `derive(Ord)` ranks
/// variants top-to-bottom and ignores any explicit discriminant — so the only thing that
/// matters is the order written here; keep them ascending. (No discriminants: nothing reads a
/// numeric tier — a tier crosses the wire as `as_str`. The `tier_priority_*` tests are the
/// guard if this is ever reordered.)
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Tier {
    /// The live UE 5.8 dark-ride wallpaper (ADR-0029). LOWEST priority — it yields to every
    /// other tier — but it yields by PROACTIVE THROTTLE (shrink to floor over UE Remote
    /// Control), NOT the SIGKILL a Batch holder gets: a wallpaper must never go to black.
    /// SIGKILL→relaunch-to-shader-floor is the backstop when the throttle can't free enough or
    /// UE misbehaves (see `yield_decision`). Owned PID (a `Spawn` profile), so the backstop kill
    /// is reachable.
    Yielding,
    BestEffort,
    Batch,
    Interactive,
}

impl Tier {
    /// Parse a tier name (shared by the `coord` CLI and the D-Bus lease server).
    pub fn from_arg(s: &str) -> Result<Tier, String> {
        match s.to_ascii_lowercase().as_str() {
            "interactive" | "live" => Ok(Tier::Interactive),
            "batch" | "overnight" => Ok(Tier::Batch),
            "best-effort" | "besteffort" | "idle" => Ok(Tier::BestEffort),
            "yielding" | "wallpaper" | "ue" => Ok(Tier::Yielding),
            other => {
                Err(format!("unknown tier `{other}` (interactive|batch|best-effort|yielding)"))
            }
        }
    }

    /// Lowercase canonical name (for D-Bus `Status` and logs).
    pub fn as_str(self) -> &'static str {
        match self {
            Tier::Interactive => "interactive",
            Tier::Batch => "batch",
            Tier::BestEffort => "best-effort",
            Tier::Yielding => "yielding",
        }
    }

    /// Clamp this tier down to a ceiling (`self.min(ceiling)`, ordering-driven). The
    /// admission-boundary transform behind ADR-0021 GO-1.
    pub fn clamp_to(self, ceiling: Tier) -> Tier {
        self.min(ceiling)
    }

    /// The agent-class band: an autonomous agent may hold a tier in `{BestEffort, Batch}` only
    /// (ADR-0021 GO-1 ceiling + Open-Q2 floor). The ceiling `Batch` is never `Interactive` — the only
    /// tier that PREEMPTS — so a clamped request can seize the GPU from neither the desktop nor a live
    /// human request. The floor `BestEffort` is never `Yielding`, which is reserved for the *owned*
    /// UE-wallpaper profile (it yields by proactive throttle, not the SIGKILL a cooperative agent lease
    /// can't drive); an agent asking `yielding` is raised to `best-effort`, not parked below it. Done
    /// with `Ord::clamp(min, max)` (BestEffort < Batch, so the bounds are well-ordered). A per-profile
    /// ceiling — needing an authenticated principal + its own privacy review — is a later ADR (Open-Q2).
    pub fn clamp_agent(self) -> Tier {
        self.clamp(Tier::BestEffort, Tier::Batch)
    }
}

/// Trust class of a lease requester (ADR-0021 GO-1). The clamp lives in this CORE transform —
/// not in the MCP shell — so it cannot be bypassed by a second D-Bus client: every request is
/// reduced to its admissible tier *here*, before `arbitrate` ever sees it. Today every caller is
/// `Trusted` (Hermes / human / CLI); the future ADR-0020 `act` verbs are the first `Agent` callers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallerClass {
    /// Trusted human-facing / Hermes / CLI path — its requested tier passes through unchanged.
    Trusted,
    /// An autonomous agent (the ADR-0020 MCP `act` verbs) — clamped to the agent band
    /// {BestEffort, Batch}. Constructed in production by the `AcquireAgent` D-Bus verb
    /// (`lease::Coordinator::acquire_agent`), reached from `agentosd mcp`'s `gpu_request` (ADR-0021).
    Agent,
}

impl CallerClass {
    /// Reduce a requested tier to what this caller class is allowed to hold.
    pub fn clamp(self, tier: Tier) -> Tier {
        match self {
            CallerClass::Trusted => tier,
            CallerClass::Agent => tier.clamp_agent(),
        }
    }
}

/// The single exclusive lease's current holder (ADR-0010 §1).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Holder {
    pub tier: Tier,
}

/// Result of predict-before-load admission control (ADR-0010 §4). Carries the inputs
/// so the log line (and any caller) can explain the verdict without re-deriving it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Admission {
    /// Footprint + headroom fits measured free VRAM — safe to spawn.
    Grant { free_mib: u64, est_mib: u64, headroom_mib: u64 },
    /// Would not fit — refuse rather than start-and-OOM. `short_mib` = the deficit.
    Deny { free_mib: u64, est_mib: u64, headroom_mib: u64, short_mib: u64 },
}

impl Admission {
    pub fn granted(&self) -> bool {
        matches!(self, Admission::Grant { .. })
    }

    /// The estimated footprint this verdict was computed for (carried so a preempt can
    /// predict post-eviction free VRAM from the victim's admitted estimate).
    pub fn est_mib(&self) -> u64 {
        match self {
            Admission::Grant { est_mib, .. } | Admission::Deny { est_mib, .. } => *est_mib,
        }
    }
}

/// Predict-before-load (ADR-0010 §4): grant only if the estimated footprint plus a
/// safety headroom fits the *measured* free VRAM. Pure, deterministic, saturating —
/// a wild estimate denies, it never panics. `need == free` is admissible (`<=`).
pub fn admit(free_mib: u64, est_mib: u64, headroom_mib: u64) -> Admission {
    let need = est_mib.saturating_add(headroom_mib);
    if need <= free_mib {
        Admission::Grant { free_mib, est_mib, headroom_mib }
    } else {
        Admission::Deny { free_mib, est_mib, headroom_mib, short_mib: need - free_mib }
    }
}

/// What to do with an incoming lease request given the current holder.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LeaseDecision {
    /// Lease is free → grant immediately.
    Grant,
    /// Held by a strictly-lower tier → preempt it (SIGKILL the holder), then grant.
    Preempt,
    /// Held by an equal-or-higher tier → the requester must wait.
    Queue,
}

/// Arbitrate an incoming request at `req` tier against the `current` holder
/// (ADR-0010 §1/§2). A strictly-higher tier preempts a lower one; an equal-or-higher
/// incumbent makes the requester queue — so live inference is never preempted, and a
/// best-effort job never displaces a running batch.
pub fn arbitrate(current: Option<Holder>, req: Tier) -> LeaseDecision {
    match current {
        None => LeaseDecision::Grant,
        Some(h) if req > h.tier => LeaseDecision::Preempt,
        Some(_) => LeaseDecision::Queue,
    }
}

/// How a higher-tier request preempts the live UE wallpaper (`Tier::Yielding`, ADR-0029 §3).
/// Because UE crashes rather than degrades under VRAM pressure, the coordinator shrinks it
/// PROACTIVELY — before the new job allocates — and prefers a non-destructive throttle to a kill:
/// a wallpaper must never go to black.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum YieldOutcome {
    /// Throttling UE to its floor frees enough VRAM to admit the request — UE stays resident at its
    /// floor and the new job COEXISTS with it (the curated-coexistence goal, ADR-0029 D4). The
    /// governor only ASKS UE to shrink (over Remote Control); the lease never kills on this path.
    ThrottleAndCoexist,
    /// Even at its floor UE can't free enough — the lease SIGKILLs the owned UE PID and relaunches
    /// the procedural shader floor (ADR-0004 / ADR-0029 D1), then admits the request against the
    /// reclaimed VRAM. The destructive backstop the throttle exists to avoid.
    KillToShaderFloor,
}

/// Decide how a higher-tier request preempts the live UE wallpaper (ADR-0029 §3 — proactive
/// throttle-not-kill, with kill→shader-floor as the backstop). PURE + saturating: the two-number-
/// footprint admission (D4) computed against UE's THROTTLED floor, never its full footprint.
///
/// * `free_now`     — measured free VRAM with UE at its CURRENT (full) footprint.
/// * `ue_full_mib` / `ue_floor_mib` — UE's two-number footprint (full vs throttled floor; the
///   Phase-A numbers were ~1.2 GB / ~1.0 GB on a trivial scene — re-measure per tableau, D4).
/// * `est_mib` / `headroom_mib` — the incoming request's predicted footprint + safety headroom.
///
/// Throttling UE full→floor frees `ue_full - ue_floor`; if that admits the request, COEXIST. Else the
/// throttle cannot free enough and the lease must kill. A wild estimate KILLS (saturates, never
/// panics). NB this only decides throttle-vs-kill; whether the request fits *after a kill* is a
/// separate `admit` the governor runs against the post-kill free VRAM — a kill is the backstop the
/// lease owns, never something this function performs.
pub fn yield_decision(
    free_now: u64,
    ue_full_mib: u64,
    ue_floor_mib: u64,
    est_mib: u64,
    headroom_mib: u64,
) -> YieldOutcome {
    // Defensive: a floor mistakenly recorded above full would make `saturating_sub` 0 (no throttle
    // gain) rather than underflow — fail toward the kill backstop, never toward a phantom free.
    let freed_by_throttle = ue_full_mib.saturating_sub(ue_floor_mib);
    let free_after_throttle = free_now.saturating_add(freed_by_throttle);
    if admit(free_after_throttle, est_mib, headroom_mib).granted() {
        YieldOutcome::ThrottleAndCoexist
    } else {
        YieldOutcome::KillToShaderFloor
    }
}

// ---------------------------------------------------------------------------
// CLI config (parsed + tested; no I/O).
// ---------------------------------------------------------------------------

const DEFAULT_CMD: &[&str] = &["sleep", "600"];

const USAGE: &str = "\
usage: agentosd coord [options] [-- <cmd> [args...]]

  Spawn <cmd> as the holder of the single exclusive VRAM lease, gated by
  predict-before-load admission, supervised until it exits or is preempted.
  With no `-- <cmd>`, supervises a harmless `sleep 600` stand-in to smoke-test
  the lease plumbing (pass a real GPU job, e.g. the ComfyUI launch, to own it).

options:
  --tier <interactive|batch|best-effort>   lease tier (default: batch)
  --estimate-mib <N>                        predicted VRAM footprint (default: 8000)
  --headroom-mib <N>                        safety headroom kept free (default: 512)
  --interval-secs <N>                       NVML poll interval (default: 2)

signals (while supervising):
  SIGUSR1   simulate an interactive request → preempt + SIGKILL the held job
  SIGINT/SIGTERM   graceful shutdown (SIGKILLs the owned child)";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    pub tier: Tier,
    pub estimate_mib: u64,
    pub headroom_mib: u64,
    pub interval_secs: u64,
    pub cmd: Vec<String>,
    /// True when `cmd` fell back to the built-in stand-in (no `-- <cmd>` given).
    pub cmd_is_default: bool,
}

impl Config {
    pub fn parse(args: &[String]) -> Result<Config, String> {
        let mut tier = Tier::Batch;
        let mut estimate_mib = 8_000u64;
        let mut headroom_mib = 512u64;
        let mut interval_secs = 2u64;
        let mut cmd: Vec<String> = Vec::new();

        let mut i = 0;
        while i < args.len() {
            match args[i].as_str() {
                "--tier" => tier = parse_tier(next(args, &mut i, "--tier")?)?,
                "--estimate-mib" => estimate_mib = parse_u64(next(args, &mut i, "--estimate-mib")?)?,
                "--headroom-mib" => headroom_mib = parse_u64(next(args, &mut i, "--headroom-mib")?)?,
                "--interval-secs" => {
                    interval_secs = parse_u64(next(args, &mut i, "--interval-secs")?)?.max(1)
                }
                "--" => {
                    cmd = args[i + 1..].to_vec();
                    break;
                }
                "-h" | "--help" => return Err(USAGE.to_string()),
                other => return Err(format!("coord: unknown arg `{other}`\n\n{USAGE}")),
            }
            i += 1;
        }

        let cmd_is_default = cmd.is_empty();
        if cmd_is_default {
            cmd = DEFAULT_CMD.iter().map(|s| s.to_string()).collect();
        }
        Ok(Config { tier, estimate_mib, headroom_mib, interval_secs, cmd, cmd_is_default })
    }

    fn cmd_display(&self) -> String {
        self.cmd.join(" ")
    }
}

/// Consume the value that follows a flag, advancing the cursor.
fn next<'a>(args: &'a [String], i: &mut usize, flag: &str) -> Result<&'a str, String> {
    *i += 1;
    args.get(*i)
        .map(String::as_str)
        .ok_or_else(|| format!("coord: `{flag}` needs a value"))
}

fn parse_tier(s: &str) -> Result<Tier, String> {
    Tier::from_arg(s).map_err(|e| format!("coord: {e}"))
}

fn parse_u64(s: &str) -> Result<u64, String> {
    s.parse::<u64>()
        .map_err(|_| format!("coord: `{s}` is not a non-negative integer"))
}

// ---------------------------------------------------------------------------
// Runtime (impure shell — introduces tokio per ADR-0010 §7).
// ---------------------------------------------------------------------------

/// Entry point for `agentosd coord`. Parses config, then drives the async supervisor
/// on a multi-thread tokio runtime built just for this mode (so `monitor`/`feed` keep
/// their existing blocking loops untouched).
pub fn run(args: Vec<String>) -> Result<(), Box<dyn std::error::Error>> {
    let cfg = match Config::parse(&args) {
        Ok(c) => c,
        Err(msg) => {
            eprintln!("{msg}");
            std::process::exit(2);
        }
    };
    let rt = tokio::runtime::Builder::new_multi_thread().enable_all().build()?;
    rt.block_on(supervise(cfg))
}

/// Read free VRAM (MiB) off GPU0. The backend read is blocking (NVML FFI / sysfs), so it runs on
/// the blocking pool (ADR-0010 §7) — never on the async executor. `None` on any read error.
/// Shared with the D-Bus lease server (admission control). Vendor-neutral (ADR-0048): NVIDIA via
/// NVML, AMD via sysfs `mem_info_vram_*`.
pub(crate) async fn free_mib(gpu: &Arc<GpuBackend>) -> Option<u64> {
    let gpu = Arc::clone(gpu);
    tokio::task::spawn_blocking(move || gpu.mem().map(|m| m.free))
        .await
        .ok()
        .flatten()
}

fn spawn_child(cfg: &Config) -> std::io::Result<Child> {
    let mut it = cfg.cmd.iter();
    let prog = it.next().expect("Config::parse guarantees a non-empty cmd");
    Command::new(prog)
        .args(it)
        // Fail-safe (ADR-0003): if agentosd dies/panics while holding the lease, the
        // OS SIGKILLs the owned GPU process rather than leaking it.
        .kill_on_drop(true)
        .spawn()
}

/// Why the supervisor loop stopped.
enum Stop {
    Exited(ExitStatus),
    WaitErr(String),
    /// An interactive request (SIGUSR1) preempted the lower-tier holder.
    Preempt,
    Term(&'static str),
}

async fn supervise(cfg: Config) -> Result<(), Box<dyn std::error::Error>> {
    let gpu = Arc::new(GpuBackend::detect());
    if gpu.is_absent() {
        return Err("no GPU detected (no NVIDIA NVML, no AMD sysfs) — the coordinator needs GPU sensing".into());
    }

    // 1. Predict-before-load admission (ADR-0010 §4) — the gate, before any allocation.
    let free0 = free_mib(&gpu).await.ok_or("could not read free VRAM")?;
    let admission = admit(free0, cfg.estimate_mib, cfg.headroom_mib);
    println!(
        "agentosd coord — admission: free {free0}M vs est {}M + headroom {}M → {}",
        cfg.estimate_mib,
        cfg.headroom_mib,
        match &admission {
            Admission::Grant { .. } => "GRANT".to_string(),
            Admission::Deny { short_mib, .. } => format!("DENY (short {short_mib}M)"),
        }
    );
    if !admission.granted() {
        // Declining IS the success path: the substrate exists to prevent the OOM.
        eprintln!(
            "coord: refusing to start the {:?} job — would not fit measured VRAM. \
             Predict-before-load (ADR-0010 §4): never start-and-regret.",
            cfg.tier
        );
        return Ok(());
    }

    // 2. Acquire the single exclusive lease (ADR-0010 §1). One holder in this slice;
    //    the multi-holder arbitration is exercised by the pure tests + the SIGUSR1 path.
    let holder = Holder { tier: cfg.tier };
    println!("coord: lease GRANTED to {:?}", cfg.tier);

    // 3. Spawn + OWN the child (ADR-0010 §5: own the PID — the only trusted evict lever).
    if cfg.cmd_is_default {
        println!("coord: no `-- <cmd>` given; supervising stand-in `{}` to demo the lease", cfg.cmd_display());
    }
    let mut child = spawn_child(&cfg)?;
    let pid = child.id().unwrap_or(0);
    println!("coord: spawned `{}` (pid {pid}) as lease holder", cfg.cmd_display());
    println!("coord: send SIGUSR1 (`kill -USR1 {}`) to simulate an interactive preemption", std::process::id());

    // 4. select! supervisor (ADR-0010 §7): NVML tick · child exit · preempt · shutdown.
    //    Only the child-exit arm borrows `child`, so the SIGKILL happens after the loop —
    //    no borrow conflict, and `Child::wait` is cancel-safe across select iterations.
    let mut tick = tokio::time::interval(Duration::from_secs(cfg.interval_secs));
    let mut sigusr1 = signal(SignalKind::user_defined1())?;
    let mut sigterm = signal(SignalKind::terminate())?;

    let stop = loop {
        tokio::select! {
            res = child.wait() => break match res {
                Ok(s) => Stop::Exited(s),
                Err(e) => Stop::WaitErr(e.to_string()),
            },
            _ = tick.tick() => {
                if let Some(f) = free_mib(&gpu).await {
                    println!("[{}] holding {:?} pid {pid} | free {f}M", crate::now_hms(), cfg.tier);
                }
            }
            _ = sigusr1.recv() => {
                // Tie the runtime action to the pure decision (model proposes, code disposes).
                let decision = arbitrate(Some(holder), Tier::Interactive);
                println!("coord: SIGUSR1 — interactive request vs held {:?} → {:?}", cfg.tier, decision);
                if decision == LeaseDecision::Preempt {
                    break Stop::Preempt;
                }
                println!("coord: interactive does not outrank {:?}; holder keeps the lease", cfg.tier);
            }
            _ = sigterm.recv() => break Stop::Term("SIGTERM"),
            _ = tokio::signal::ctrl_c() => break Stop::Term("SIGINT"),
        }
    };

    // 5. Resolve. The lease is exclusive, so the holder is always either gone (natural
    //    exit) or SIGKILLed here — an owned GPU process is never left running.
    match stop {
        Stop::Exited(s) => {
            println!("coord: batch job exited ({s}) → lease released");
        }
        Stop::WaitErr(e) => {
            eprintln!("coord: wait() error: {e}; SIGKILLing pid {pid} to be safe");
            let _ = child.kill().await;
        }
        Stop::Preempt => {
            let before = free_mib(&gpu).await.unwrap_or(0);
            println!(
                "coord: PREEMPT → SIGKILL pid {pid} (own-PID evict; `/free` not trusted, ADR-0010 §5)"
            );
            let _ = child.kill().await; // SIGKILL + reap
            tokio::time::sleep(Duration::from_millis(300)).await; // let the driver settle
            let after = free_mib(&gpu).await.unwrap_or(before);
            println!(
                "coord: reclaimed {}M ({before}M→{after}M); prior cached output persists; \
                 job requeues next window (ADR-0010 §6)",
                after.saturating_sub(before)
            );
        }
        Stop::Term(sig) => {
            println!("coord: {sig} — graceful shutdown; SIGKILL owned pid {pid}");
            let _ = child.kill().await;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    // --- Tier priority order (ADR-0010 §2) ---

    #[test]
    fn tier_priority_is_interactive_over_batch_over_best_effort() {
        assert!(Tier::Interactive > Tier::Batch);
        assert!(Tier::Batch > Tier::BestEffort);
        assert!(Tier::Interactive > Tier::BestEffort);
    }

    // --- Tier::Yielding — the live UE wallpaper (ADR-0029 §3) ---

    #[test]
    fn yielding_is_the_lowest_tier_yields_to_everything() {
        // The UE wallpaper sits BELOW best-effort: every real workload outranks it.
        assert!(Tier::BestEffort > Tier::Yielding);
        assert!(Tier::Batch > Tier::Yielding);
        assert!(Tier::Interactive > Tier::Yielding);
        // so any other tier PREEMPTS a yielding holder...
        for req in [Tier::BestEffort, Tier::Batch, Tier::Interactive] {
            assert_eq!(arbitrate(Some(Holder { tier: Tier::Yielding }), req), LeaseDecision::Preempt);
        }
        // ...and a yielding request waits behind any non-free holder (never displaces a real job),
        // but takes a free lease (the wallpaper runs when nothing else needs the card).
        for held in [Tier::BestEffort, Tier::Batch, Tier::Interactive, Tier::Yielding] {
            assert_eq!(arbitrate(Some(Holder { tier: held }), Tier::Yielding), LeaseDecision::Queue);
        }
        assert_eq!(arbitrate(None, Tier::Yielding), LeaseDecision::Grant);
    }

    #[test]
    fn yielding_tier_round_trips_through_from_arg_and_as_str() {
        assert_eq!(Tier::from_arg("yielding"), Ok(Tier::Yielding));
        assert_eq!(Tier::from_arg("wallpaper"), Ok(Tier::Yielding));
        assert_eq!(Tier::from_arg("UE"), Ok(Tier::Yielding)); // case-insensitive
        assert_eq!(Tier::Yielding.as_str(), "yielding");
        assert_eq!(Tier::from_arg(Tier::Yielding.as_str()), Ok(Tier::Yielding));
    }

    #[test]
    fn an_agent_requesting_yielding_is_floored_to_best_effort() {
        // ADR-0021 Open-Q2 (the floor): `Yielding` is reserved for the OWNED UE-wallpaper profile
        // (it yields by proactive throttle, not the SIGKILL a cooperative agent lease can't drive),
        // so an agent asking for it is raised to `BestEffort` — never parked below it. Agent ∈
        // {BestEffort, Batch}.
        assert_eq!(CallerClass::Agent.clamp(Tier::Yielding), Tier::BestEffort);
    }

    // --- yield_decision(): proactive throttle-not-kill vs the kill backstop (ADR-0029 D3/D4) ---

    #[test]
    fn throttle_alone_frees_enough_so_ue_coexists() {
        // UE full 1200 / floor 1000 → throttling frees 200. With 7000 free, a 7000-est gen needs
        // 7000+512; 7000 free is short, but 7000+200=7200 ≥ 7512? no — pick numbers that DO fit:
        // free 8000, throttle frees 200 → 8200; a 7500-est + 512 headroom = 8012 ≤ 8200 → coexist.
        assert_eq!(
            yield_decision(8_000, 1_200, 1_000, 7_500, 512),
            YieldOutcome::ThrottleAndCoexist
        );
    }

    #[test]
    fn throttle_insufficient_falls_through_to_kill() {
        // Same UE footprint, but the gen is too big to fit even with UE at floor: free 8000 + 200
        // throttled = 8200, but 9000-est + 512 = 9512 > 8200 → the lease must SIGKILL UE + relaunch
        // the shader floor (the backstop the throttle exists to avoid).
        assert_eq!(
            yield_decision(8_000, 1_200, 1_000, 9_000, 512),
            YieldOutcome::KillToShaderFloor
        );
    }

    #[test]
    fn yield_decision_admits_against_the_throttled_floor_not_full() {
        // The D4 invariant made concrete: a request that does NOT fit against UE-full but DOES fit
        // once UE is throttled to floor must read as coexist. UE full 4000 / floor 1000 (frees 3000).
        // free_now = 2000 (UE at full). Against full: 2000 free, est 4000+512 → would deny/kill.
        // After throttle: 2000+3000 = 5000 ≥ 4000+512 = 4512 → coexist. Proves we admit against floor.
        assert_eq!(
            yield_decision(2_000, 4_000, 1_000, 4_000, 512),
            YieldOutcome::ThrottleAndCoexist
        );
        // And a hair more demand tips it to a kill (the boundary is the throttled-floor admission).
        assert_eq!(
            yield_decision(2_000, 4_000, 1_000, 4_500, 512),
            YieldOutcome::KillToShaderFloor
        );
    }

    #[test]
    fn yield_decision_handles_the_full_equals_floor_degenerate_case() {
        // On a trivial tableau UE's full ≈ floor (Phase-A: ~1.2 vs ~1.0 GB), and some tableaux will
        // measure them EQUAL — then throttling frees nothing, so the outcome is purely "does it fit as-is".
        // Fits already (free alone admits) → coexist (a no-op shrink is fine).
        assert_eq!(yield_decision(8_000, 1_000, 1_000, 5_000, 512), YieldOutcome::ThrottleAndCoexist);
        // Doesn't fit and the throttle can't help (full==floor frees 0) → the kill backstop.
        assert_eq!(yield_decision(2_000, 1_000, 1_000, 5_000, 512), YieldOutcome::KillToShaderFloor);
    }

    #[test]
    fn yield_decision_is_saturating_and_defensive() {
        // A pathological estimate kills, never panics on overflow.
        assert_eq!(
            yield_decision(8_000, 1_200, 1_000, u64::MAX, 512),
            YieldOutcome::KillToShaderFloor
        );
        // A floor mistakenly recorded ABOVE full yields no throttle gain (saturating_sub → 0), so the
        // decision falls toward the kill backstop rather than inventing free VRAM from an underflow.
        assert_eq!(
            yield_decision(1_000, 1_000, 9_999, 5_000, 512),
            YieldOutcome::KillToShaderFloor
        );
        // Throttle gain exactly meeting the need is admissible (admit treats need==free as a grant).
        // free 0, full 9000/floor 1000 frees 8000; est 7488 + 512 = 8000 == 8000 → coexist.
        assert_eq!(yield_decision(0, 9_000, 1_000, 7_488, 512), YieldOutcome::ThrottleAndCoexist);
    }

    // --- CallerClass tier clamp (ADR-0021 GO-1): the core transform, not a shell check ---

    #[test]
    fn agent_class_clamps_to_the_best_effort_batch_band() {
        // ADR-0021 GO-1 ceiling + Open-Q2 floor: an agent class ∈ {BestEffort, Batch}.
        assert_eq!(CallerClass::Agent.clamp(Tier::Interactive), Tier::Batch); // ceiling: never preempts
        assert_eq!(CallerClass::Agent.clamp(Tier::Batch), Tier::Batch);
        assert_eq!(CallerClass::Agent.clamp(Tier::BestEffort), Tier::BestEffort);
        assert_eq!(CallerClass::Agent.clamp(Tier::Yielding), Tier::BestEffort); // floor: never the wallpaper tier
    }

    #[test]
    fn trusted_class_passes_every_tier_through_unchanged() {
        // The existing Hermes/human/CLI path is unaffected — clamp is identity for Trusted.
        for t in [Tier::BestEffort, Tier::Batch, Tier::Interactive] {
            assert_eq!(CallerClass::Trusted.clamp(t), t);
        }
    }

    #[test]
    fn a_clamped_agent_interactive_request_can_never_preempt() {
        // The GO-1 invariant end-to-end: an agent that ASKS for interactive is reduced to batch
        // BEFORE arbitrate, so against any incumbent it queues — it never preempts the desktop or
        // a live human request.
        let agent_tier = CallerClass::Agent.clamp(Tier::Interactive); // == Batch
        assert_eq!(
            arbitrate(Some(Holder { tier: Tier::Interactive }), agent_tier),
            LeaseDecision::Queue
        );
        assert_eq!(
            arbitrate(Some(Holder { tier: Tier::Batch }), agent_tier),
            LeaseDecision::Queue
        );
        // sanity: an UNclamped (trusted) interactive WOULD preempt the batch — proving the clamp
        // is what makes the difference, not arbitrate.
        assert_eq!(
            arbitrate(Some(Holder { tier: Tier::Batch }), CallerClass::Trusted.clamp(Tier::Interactive)),
            LeaseDecision::Preempt
        );
    }

    // --- admit(): predict before load (ADR-0010 §4) ---

    #[test]
    fn admit_grants_when_it_fits_with_headroom() {
        let a = admit(10_000, 8_000, 512);
        assert!(a.granted());
        assert_eq!(a, Admission::Grant { free_mib: 10_000, est_mib: 8_000, headroom_mib: 512 });
    }

    #[test]
    fn admit_denies_when_only_the_headroom_is_short() {
        // The raw estimate fits (8000 <= 8200) but estimate+headroom does not (8512 > 8200).
        let a = admit(8_200, 8_000, 512);
        assert_eq!(
            a,
            Admission::Deny { free_mib: 8_200, est_mib: 8_000, headroom_mib: 512, short_mib: 312 }
        );
    }

    #[test]
    fn admit_treats_exact_fit_as_a_grant() {
        assert!(admit(8_512, 8_000, 512).granted()); // need == free
        assert!(!admit(8_511, 8_000, 512).granted()); // one short
    }

    #[test]
    fn admit_saturates_instead_of_overflowing() {
        // A pathological estimate must deny, not panic on add-overflow.
        assert!(!admit(1_000, u64::MAX, 512).granted());
    }

    // --- arbitrate(): the lease decision (ADR-0010 §1/§2) ---

    #[test]
    fn arbitrate_grants_when_the_lease_is_free() {
        assert_eq!(arbitrate(None, Tier::Batch), LeaseDecision::Grant);
        assert_eq!(arbitrate(None, Tier::Interactive), LeaseDecision::Grant);
    }

    #[test]
    fn higher_tier_preempts_a_lower_holder() {
        assert_eq!(
            arbitrate(Some(Holder { tier: Tier::Batch }), Tier::Interactive),
            LeaseDecision::Preempt
        );
        assert_eq!(
            arbitrate(Some(Holder { tier: Tier::BestEffort }), Tier::Batch),
            LeaseDecision::Preempt
        );
    }

    #[test]
    fn equal_or_lower_request_queues_behind_the_holder() {
        // equal tier waits
        assert_eq!(
            arbitrate(Some(Holder { tier: Tier::Batch }), Tier::Batch),
            LeaseDecision::Queue
        );
        // best-effort never displaces a running batch
        assert_eq!(
            arbitrate(Some(Holder { tier: Tier::Batch }), Tier::BestEffort),
            LeaseDecision::Queue
        );
    }

    #[test]
    fn interactive_is_never_preempted() {
        for req in [Tier::BestEffort, Tier::Batch, Tier::Interactive] {
            assert_eq!(
                arbitrate(Some(Holder { tier: Tier::Interactive }), req),
                LeaseDecision::Queue
            );
        }
    }

    // --- Config::parse() ---

    #[test]
    fn parse_defaults_to_batch_and_the_standin() {
        let c = Config::parse(&[]).unwrap();
        assert_eq!(c.tier, Tier::Batch);
        assert_eq!(c.estimate_mib, 8_000);
        assert_eq!(c.headroom_mib, 512);
        assert_eq!(c.interval_secs, 2);
        assert!(c.cmd_is_default);
        assert_eq!(c.cmd, vec!["sleep".to_string(), "600".to_string()]);
    }

    #[test]
    fn parse_reads_flags_and_the_command() {
        let args: Vec<String> = ["--tier", "interactive", "--estimate-mib", "12000", "--", "python", "main.py"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let c = Config::parse(&args).unwrap();
        assert_eq!(c.tier, Tier::Interactive);
        assert_eq!(c.estimate_mib, 12_000);
        assert!(!c.cmd_is_default);
        assert_eq!(c.cmd, vec!["python".to_string(), "main.py".to_string()]);
    }

    #[test]
    fn parse_clamps_zero_interval_to_one() {
        let args: Vec<String> = ["--interval-secs", "0"].iter().map(|s| s.to_string()).collect();
        assert_eq!(Config::parse(&args).unwrap().interval_secs, 1);
    }

    #[test]
    fn parse_rejects_unknown_tier_and_args() {
        let bad_tier: Vec<String> = ["--tier", "turbo"].iter().map(|s| s.to_string()).collect();
        assert!(Config::parse(&bad_tier).is_err());
        let bad_flag: Vec<String> = ["--nope"].iter().map(|s| s.to_string()).collect();
        assert!(Config::parse(&bad_flag).is_err());
    }

    #[test]
    fn parse_errors_when_a_flag_is_missing_its_value() {
        let args: Vec<String> = ["--estimate-mib"].iter().map(|s| s.to_string()).collect();
        assert!(Config::parse(&args).is_err());
    }
}

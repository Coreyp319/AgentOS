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

use std::sync::Arc;
use std::time::Duration;

use nvml_wrapper::Nvml;
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

use crate::coord::{admit, arbitrate, free_mib, Admission, Holder, LeaseDecision, Tier};

/// Safety headroom kept free on a fresh grant (mirrors `coord` / ADR-0004 margins).
const HEADROOM_MIB: u64 = 512;

const BUS_NAME: &str = "org.agentos.Coordinator1";
const OBJ_PATH: &str = "/org/agentos/Coordinator1";

// ---------------------------------------------------------------------------
// Pure lease state machine (the only new decision logic — unit-tested below).
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Held {
    tier: Tier,
    token: u64,
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
    /// out of this testable core. Admission gates only a *fresh* grant: a preemption
    /// SIGKILLs the holder and frees its VRAM, so current-free is the wrong budget for
    /// it — the displaced job's footprint was already admitted when it acquired.
    pub fn acquire(&mut self, tier: Tier, admission: &Admission) -> AcquireResult {
        match arbitrate(self.holder.map(|h| Holder { tier: h.tier }), tier) {
            LeaseDecision::Queue => AcquireResult::Queued,
            LeaseDecision::Preempt => {
                let victim = self.holder.expect("Preempt implies a holder").token;
                let token = self.install(tier);
                AcquireResult::Preempted { token, victim }
            }
            LeaseDecision::Grant => {
                if !admission.granted() {
                    return AcquireResult::Denied;
                }
                let token = self.install(tier);
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

    /// Issue a fresh token and install `tier` as the sole holder.
    fn install(&mut self, tier: Tier) -> u64 {
        let token = self.next_token;
        self.next_token += 1;
        self.holder = Some(Held { tier, token });
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
}

impl Inner {
    fn new() -> Self {
        Inner { lease: LeaseState::new(), owned: None }
    }
}

fn spawn_owned(argv: &[String]) -> std::io::Result<(Child, u32)> {
    let mut it = argv.iter();
    let prog = it.next().expect("argv non-empty (checked by caller)");
    let child = Command::new(prog)
        .args(it)
        // Fail-safe (ADR-0003): never leak an owned GPU process if the daemon dies.
        .kill_on_drop(true)
        .spawn()?;
    let pid = child.id().unwrap_or(0);
    Ok((child, pid))
}

// ---------------------------------------------------------------------------
// D-Bus shell (zbus, session bus, tokio reactor).
// ---------------------------------------------------------------------------

struct Coordinator {
    inner: Arc<Mutex<Inner>>,
    nvml: Arc<Nvml>,
}

impl Coordinator {
    /// Shared path for both `Acquire` (cooperative, `argv = None`) and `Spawn`
    /// (owned, `argv = Some`). The lock is held across the decision AND the
    /// spawn/evict so the lease and the child it controls never disagree.
    async fn do_acquire(
        &self,
        tier_name: String,
        estimate_mib: u32,
        argv: Option<Vec<String>>,
    ) -> (bool, u64, String) {
        let tier = match Tier::from_arg(&tier_name) {
            Ok(t) => t,
            Err(e) => return (false, 0, format!("error: {e}")),
        };
        let free = free_mib(&self.nvml).await.unwrap_or(0);
        let admission = admit(free, estimate_mib as u64, HEADROOM_MIB);

        let mut inner = self.inner.lock().await;
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
                let short = match admission {
                    Admission::Deny { short_mib, .. } => short_mib,
                    _ => 0,
                };
                return (
                    false,
                    0,
                    format!(
                        "denied: short {short}M (free {free}M vs est {estimate_mib}M + headroom {HEADROOM_MIB}M)"
                    ),
                );
            }
            AcquireResult::Granted { token } => (token, None),
            AcquireResult::Preempted { token, victim } => (token, Some(victim)),
        };

        // Evict the previously-owned child on preemption (ADR-0010 §5: own-PID SIGKILL;
        // `POST /free` is never on this path). kill_on_drop reaps the rest when it drops.
        let mut evicted: Option<(u64, String, u32)> = None;
        if let Some(victim) = preempted {
            if let Some(mut job) = inner.owned.take() {
                if job.token == victim {
                    let _ = job.child.start_kill();
                    evicted = Some((job.token, job.label.clone(), job.pid));
                } else {
                    inner.owned = Some(job); // not the victim (shouldn't happen) — keep it
                }
            }
        }

        // `Spawn` requests: agentosd spawns + OWNS the child. On spawn failure, roll the
        // lease back — a lease without its process is worse than no lease.
        if let Some(args) = argv.as_deref() {
            match spawn_owned(args) {
                Ok((child, pid)) => {
                    inner.owned =
                        Some(OwnedJob { token, child, pid, label: args.join(" ") });
                }
                Err(e) => {
                    inner.lease.release(token);
                    drop(inner);
                    if let Some((vt, label, pid)) = evicted {
                        println!("coordd: PREEMPT token {vt} → SIGKILL `{label}` pid {pid}");
                    }
                    return (false, 0, format!("spawn failed: {e}"));
                }
            }
        }

        let owned_pid = inner.owned.as_ref().filter(|j| j.token == token).map(|j| j.pid);
        drop(inner);

        if let Some((vt, label, pid)) = evicted {
            println!(
                "coordd: PREEMPT token {vt} → SIGKILL `{label}` pid {pid} (own-PID evict, ADR-0010 §5)"
            );
            self.spawn_reclaim_probe(free);
        }

        let mut msg = if preempted.is_some() {
            format!("granted {} token {token}; preempted prior holder (free {free}M)", tier.as_str())
        } else {
            format!("granted {} token {token} (free {free}M)", tier.as_str())
        };
        if let Some(pid) = owned_pid {
            msg.push_str(&format!("; agentosd owns pid {pid}"));
        }
        (true, token, msg)
    }

    /// Fire-and-forget VRAM-reclaim proof after a SIGKILL: let the driver settle, then
    /// log free-VRAM delta. Off the D-Bus response path so `Acquire` stays snappy.
    fn spawn_reclaim_probe(&self, before: u64) {
        let nvml = Arc::clone(&self.nvml);
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(300)).await;
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
    async fn acquire(&self, tier: String, estimate_mib: u32) -> (bool, u64, String) {
        self.do_acquire(tier, estimate_mib, None).await
    }

    /// Owned lease: agentosd spawns `argv` and holds its PID, so it can SIGKILL it on
    /// preemption (the overnight batch / ComfyUI pattern, ADR-0009/0010 §5).
    async fn spawn(&self, tier: String, estimate_mib: u32, argv: Vec<String>) -> (bool, u64, String) {
        if argv.is_empty() {
            return (false, 0, "spawn: argv is empty (use Acquire for a cooperative lease)".into());
        }
        self.do_acquire(tier, estimate_mib, Some(argv)).await
    }

    /// Release the lease (true iff `token` is the current holder's). If that holder is an
    /// agentosd-owned job, its child is SIGKILLed too — releasing means "done with the GPU".
    async fn release(&self, token: u64) -> bool {
        let mut inner = self.inner.lock().await;
        let freed = inner.lease.release(token);
        if freed {
            if let Some(mut job) = inner.owned.take() {
                if job.token == token {
                    let _ = job.child.start_kill();
                } else {
                    inner.owned = Some(job);
                }
            }
        }
        freed
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
async fn supervise(inner: Arc<Mutex<Inner>>) {
    loop {
        tokio::time::sleep(Duration::from_millis(750)).await;
        let exited = {
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
            }
            done
        };
        if let Some((token, label, status)) = exited {
            println!("coordd: owned job `{label}` (token {token}) exited ({status}) → lease released");
        }
    }
}

async fn serve() -> Result<(), Box<dyn std::error::Error>> {
    let nvml = Arc::new(
        Nvml::init().map_err(|e| format!("NVML init failed (is the NVIDIA driver loaded?): {e}"))?,
    );
    let inner = Arc::new(Mutex::new(Inner::new()));
    tokio::spawn(supervise(Arc::clone(&inner)));

    let obj = Coordinator { inner: Arc::clone(&inner), nvml };
    let _conn = zbus::connection::Builder::session()?
        .name(BUS_NAME)?
        .serve_at(OBJ_PATH, obj)?
        .build()
        .await?;

    println!("agentosd coordd — VRAM coordinator daemon serving {BUS_NAME} (ADR-0006/0010)");
    println!("  spawn (owned) : busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} Spawn suas batch 2000 2 sleep 600");
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
}

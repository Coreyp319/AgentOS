//! `lease` — the D-Bus GPU lease server (ADR-0006 control plane + ADR-0010 §1–§3).
//!
//! ADR-0006 routes Hermes → a D-Bus lease call → agentosd; this is the agentosd side.
//! It exposes the **single exclusive, preemptible VRAM lease** over the session bus so
//! the Hermes plugin (`llm_execution` middleware) and the overnight batch agents can
//! serialize GPU-heavy work — *enforced* one-holder-at-a-time, not hoped.
//!
//! Two holder kinds, one lever: **agentosd-owned** jobs (ComfyUI/batch — see `coord`,
//! which can SIGKILL them) and **external** acquirers (Hermes inference). Because
//! `interactive` is the top tier (it preempts, is never preempted), agentosd never has
//! to kill an external process — the SIGKILL lever only ever lands on its own batch
//! children. So this server's job is purely: decide grant/preempt/queue/deny, and hand
//! the preemption verdict back to the owner to act on.
//!
//! The decision is the same deterministic core as `coord`: `admit` (predict-before-load,
//! ADR-0010 §4) + `arbitrate` (priority, §1/§2). `LeaseState` below adds the only new
//! logic — issuing/revoking the single lease token — and is pure + unit-tested. zbus is
//! the thin shell (model proposes, code disposes: no model output reaches the lease).
//!
//! Scope (ADR-0001): a lease server, not a scheduler. There is no wait-queue yet — a
//! losing acquirer is told `queued` and retries (real backpressure comes from the
//! gateway holding inference responses, ADR-0006). Emitting a revoke *signal* for
//! cooperative holders, and wiring an `Acquire(interactive)` to actually SIGKILL the
//! `coord`-owned child, are the next unification step.

use std::sync::Arc;

use nvml_wrapper::Nvml;
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
// D-Bus shell (zbus, session bus, tokio reactor).
// ---------------------------------------------------------------------------

struct Coordinator {
    state: Arc<Mutex<LeaseState>>,
    nvml: Arc<Nvml>,
}

#[zbus::interface(name = "org.agentos.Coordinator1")]
impl Coordinator {
    /// Acquire the single exclusive lease at `tier` for an estimated `estimate_mib`
    /// footprint. Returns `(granted, token, outcome)`; `token` is 0 when not granted.
    /// The owner of a preempted job reads the outcome and SIGKILLs it (`coord`).
    async fn acquire(&self, tier: String, estimate_mib: u32) -> (bool, u64, String) {
        let tier = match Tier::from_arg(&tier) {
            Ok(t) => t,
            Err(e) => return (false, 0, format!("error: {e}")),
        };
        let free = free_mib(&self.nvml).await.unwrap_or(0);
        let admission = admit(free, estimate_mib as u64, HEADROOM_MIB);

        let mut st = self.state.lock().await;
        match st.acquire(tier, &admission) {
            AcquireResult::Granted { token } => {
                (true, token, format!("granted {} (token {token}, free {free}M)", tier.as_str()))
            }
            AcquireResult::Preempted { token, victim } => (
                true,
                token,
                format!(
                    "granted {} (token {token}); preempted token {victim} → owner SIGKILLs it",
                    tier.as_str()
                ),
            ),
            AcquireResult::Queued => (
                false,
                0,
                format!(
                    "queued: lease held by {} (token {})",
                    st.holder_tier().map_or("?", Tier::as_str),
                    st.holder_token()
                ),
            ),
            AcquireResult::Denied => {
                let short = match admission {
                    Admission::Deny { short_mib, .. } => short_mib,
                    _ => 0,
                };
                (
                    false,
                    0,
                    format!(
                        "denied: short {short}M (free {free}M vs est {estimate_mib}M + headroom {HEADROOM_MIB}M)"
                    ),
                )
            }
        }
    }

    /// Release the lease. True iff `token` is the current holder's.
    async fn release(&self, token: u64) -> bool {
        self.state.lock().await.release(token)
    }

    /// `(held, tier, token, free_mib)` — current lease + live free VRAM.
    async fn status(&self) -> (bool, String, u64, u32) {
        let free = free_mib(&self.nvml).await.unwrap_or(0) as u32;
        let st = self.state.lock().await;
        match st.holder_tier() {
            Some(t) => (true, t.as_str().to_string(), st.holder_token(), free),
            None => (false, String::new(), 0, free),
        }
    }
}

pub fn run() -> Result<(), Box<dyn std::error::Error>> {
    let rt = tokio::runtime::Builder::new_multi_thread().enable_all().build()?;
    rt.block_on(serve())
}

async fn serve() -> Result<(), Box<dyn std::error::Error>> {
    let nvml = Arc::new(
        Nvml::init().map_err(|e| format!("NVML init failed (is the NVIDIA driver loaded?): {e}"))?,
    );
    let obj = Coordinator { state: Arc::new(Mutex::new(LeaseState::new())), nvml };

    let _conn = zbus::connection::Builder::session()?
        .name(BUS_NAME)?
        .serve_at(OBJ_PATH, obj)?
        .build()
        .await?;

    println!("agentosd lease — serving {BUS_NAME} on the session bus (ADR-0006)");
    println!("  acquire: busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} Acquire su batch 2000");
    println!("  status : busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} Status");
    println!("  release: busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} Release t <token>");
    tokio::signal::ctrl_c().await?;
    println!("agentosd lease — shutdown");
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

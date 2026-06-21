//! reclaim.rs — graceful warm-pool reclaim (ADR-0018 §2 + the 2026-06-19 amendment, item 6).
//!
//! "**Graceful reclaim before the sledgehammer**" (ADR-0018 §2): when a heavy-lane job is denied for
//! lack of VRAM, the coordinator first asks Ollama to unload resident **warm-pool** models
//! (`ollama stop`, cold models first), **re-MEASURES** free VRAM after each stop, and only then lets
//! the normal locked admission re-check fit. SIGKILL of an *owned* heavy holder stays the backstop
//! (ADR-0010), unchanged — this never reaches for it.
//!
//! Invariants honored (ADR-0018 §5 / the resource-safety + determinism review panel):
//!   * **Measure, don't predict** (acceptance #2). agentosd does not own Ollama's PID — there is no
//!     `wait()`. After each `ollama stop` we poll *real* free VRAM until it rises past the target
//!     (bounded), and the learned `size_vram` is only the poll *target*, never a substitute for the
//!     post-stop measurement.
//!   * **`ollama stop` ONLY** (acceptance #3). Killing the Ollama daemon kills the runtime; the warm
//!     pool is reclaimed by graceful per-model unload, nothing else. SIGKILL asymmetry is explicit:
//!     the sledgehammer is for *owned* heavy holders (`lease.rs`), never the warm pool.
//!   * **Bounded** — at most `max_stops` unloads, each with a bounded post-stop poll, so a stuck
//!     unload can never stall a grant.
//!   * **Fail-open** (ADR-0003). Any error (unreachable Ollama, a `stop` that fails, an unreadable
//!     NVML tick) degrades to "reclaimed what we could" and returns — never a panic, never a hang.
//!   * **Pure decision core.** The cold-first ordering, the offload test, and "did it free enough"
//!     are pure functions, unit-tested with a `MockReclaimer`; only `RealReclaimer` touches I/O.
//!
//! What this module is NOT: the tick-by-tick model-mix *controller* (ADR-0018 §5). This is the
//! reclaim *primitive* the amendment (item 6) says to spike before any actuating loop ships. The
//! controller, when justified by telemetry, is a separate `tokio` task that *calls* primitives like
//! this one through the lease's single locked apply path — it is never a second actor on the GPU.

use std::time::Duration;

use crate::coord::admit;

/// Default ceiling on graceful unloads per reclaim attempt. On this 24 GB box the warm pool is at
/// most ~2 small models co-resident (ADR-0018: "max 1 co-resident" observed), so 3 is generous
/// headroom while staying bounded. Tunable knob lives in `lease.rs` (the caller).
pub const DEFAULT_MAX_STOPS: usize = 3;
/// Post-`stop` poll budget: how many times to re-read free VRAM after an unload before giving up on
/// *that* model and trying the next-coldest. Ollama unload is fast (~hundreds of ms) but the driver
/// settles asynchronously; bounded so a hung unload can't stall the caller.
pub const DEFAULT_POLL_TRIES: u32 = 20;
pub const DEFAULT_POLL_INTERVAL: Duration = Duration::from_millis(100);

/// One Ollama-resident model, as reported by `/api/ps`. `size_vram` and `size` are in MiB.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ResidentModel {
    pub name: String,
    /// Ollama-reported resident VRAM (MiB). UNDERCOUNTS the true footprint (ADR-0018: CUDA ctx + KV
    /// are invisible to it) — used only as a *poll target* / ordering hint, never as the freed amount.
    pub size_vram_mib: u64,
    /// Total model size (MiB). When `size_vram < size`, part of the model is on CPU (see
    /// [`offload_detected`]).
    pub size_mib: u64,
    /// RFC3339 keep-alive expiry from `/api/ps` (`""` if unknown). Sorted ascending for cold-first:
    /// the model whose keep-alive expires soonest is the *coldest* (least-recently-used), so it is
    /// unloaded first and the actively-used model (freshest expiry) is protected — unloaded last, if
    /// at all. RFC3339 timestamps from a single Ollama instance sort correctly lexicographically.
    pub expires_at: String,
}

/// item 6 primitive #2 — the `size_vram < size` post-load **offload detector**. A fully-resident
/// model reports `size_vram == size`; a partially CPU-offloaded one reports `size_vram < size` (the
/// masking signal — the model "loaded" but spilled to CPU, the 87%-on-CPU failure of 2026-06-19).
/// "Never CPU-offload" is enforceable as *detect-and-reject*, NOT as a guarantee — this is the
/// detector. Pure + tested. (`size_vram == 0` means "not resident", not "offloaded".)
pub fn offload_detected(size_vram_mib: u64, size_mib: u64) -> bool {
    size_vram_mib > 0 && size_vram_mib < size_mib
}

/// Cold-first unload order: indices into `models`, coldest (soonest-to-expire) first. Stable for
/// equal keys so the order is deterministic (replayable — a determinism-review requirement). An
/// empty `expires_at` ("unknown") sorts coldest (unload it before a model with a known live lease).
pub fn cold_first_order(models: &[ResidentModel]) -> Vec<usize> {
    let mut idx: Vec<usize> = (0..models.len()).collect();
    // Sort key: expires_at ascending. `""` (unknown) naturally sorts before any RFC3339 string.
    idx.sort_by(|&a, &b| models[a].expires_at.cmp(&models[b].expires_at).then(a.cmp(&b)));
    idx
}

/// Outcome of a graceful reclaim attempt — what was stopped, what the card freed, and whether the
/// target was reached. Returned to the caller for narration + the refreshed admission input.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ReclaimOutcome {
    /// Models actually `ollama stop`-ped, in the order unloaded (cold-first).
    pub stopped: Vec<String>,
    /// Resident models detected partially-on-CPU (`size_vram < size`) at attempt start — a telemetry
    /// signal that the warm set was already offloading (ADR-0018 item 6 #2). Reported, not acted on.
    pub offloaded: Vec<String>,
    /// Free VRAM (MiB) measured before any unload, and after the last (the value the caller re-admits
    /// against — measure-don't-predict). `free_after == free_before` if nothing was stopped / read.
    pub free_before: u64,
    pub free_after: u64,
    /// Did free VRAM reach `need + headroom`? (Computed from the post-stop *measurement*, not a sum
    /// of reported `size_vram`.) The caller's locked `admit` is still the real gate — this is a hint.
    pub satisfied: bool,
}

/// The I/O surface a reclaim needs, abstracted so the orchestration is unit-testable without a live
/// Ollama or GPU (`MockReclaimer` in tests; `RealReclaimer` in the daemon). `#[allow]`: this is an
/// internal module, not a published API, so the auto-trait-bound lint on `async fn` doesn't apply.
#[allow(async_fn_in_trait)]
pub trait WarmReclaimer {
    /// Ollama's resident set (`/api/ps`). Empty on an unreachable Ollama (fail-open: nothing to do).
    async fn resident(&self) -> Vec<ResidentModel>;
    /// Live free VRAM (MiB), or `None` if NVML is unreadable this tick (fail-open: treated as "no
    /// progress", the bounded poll then expires — never blocks).
    async fn free_mib(&self) -> Option<u64>;
    /// Gracefully unload one model (`ollama stop <name>`). `true` on success; `false` (fail-open) on
    /// any error so the orchestrator moves on to the next-coldest rather than wedging.
    async fn stop(&self, model: &str) -> bool;
}

/// **The graceful reclaim orchestrator** (ADR-0018 §2). Unload warm-pool models cold-first, one at a
/// time, re-MEASURING free VRAM after each, until the card holds `need_mib + headroom_mib` or the
/// bounds are hit. Returns what happened; the caller re-admits against `free_after` under its lock.
///
/// Fail-open throughout: an empty resident set, a failed `stop`, or an unreadable NVML tick each just
/// limits what we can free — never a panic, never an unbounded wait. Idempotent in spirit (each model
/// is stopped at most once per call); cross-call anti-strobe is the caller's dwell (ADR-0018 #6).
pub async fn reclaim_until_fits<R: WarmReclaimer>(
    r: &R,
    need_mib: u64,
    headroom_mib: u64,
    max_stops: usize,
    poll_tries: u32,
    poll_interval: Duration,
) -> ReclaimOutcome {
    let free_before = r.free_mib().await.unwrap_or(0);
    let mut out = ReclaimOutcome { free_before, free_after: free_before, ..Default::default() };

    // Already fits (a race freed the card between the caller's denial and here) → no-op, honest.
    if admit(free_before, need_mib, headroom_mib).granted() {
        out.satisfied = true;
        return out;
    }

    let models = r.resident().await;
    out.offloaded = models
        .iter()
        .filter(|m| offload_detected(m.size_vram_mib, m.size_mib))
        .map(|m| m.name.clone())
        .collect();

    for &i in cold_first_order(&models).iter().take(max_stops) {
        let model = &models[i].name;
        if !r.stop(model).await {
            // Fail-open: this unload errored — its VRAM won't free; try the next-coldest.
            continue;
        }
        out.stopped.push(model.clone());

        // Measure, don't predict: poll real free VRAM until it rises enough (bounded).
        let mut last = out.free_after;
        for _ in 0..poll_tries {
            tokio::time::sleep(poll_interval).await;
            if let Some(free) = r.free_mib().await {
                last = free;
                if admit(free, need_mib, headroom_mib).granted() {
                    break;
                }
            }
        }
        out.free_after = last;
        if admit(out.free_after, need_mib, headroom_mib).granted() {
            out.satisfied = true;
            break;
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Real I/O impl (the impure shell — kept out of the tested core).
// ---------------------------------------------------------------------------

use std::sync::Arc;

use nvml_wrapper::Nvml;

const OLLAMA_PS: &str = "http://127.0.0.1:11434/api/ps";

/// `/api/ps` shape (a private mirror of the one in `main.rs`/`telemetry.rs` — same fields, parsed
/// for reclaim ordering + offload detection). Unknown fields are ignored.
#[derive(serde::Deserialize)]
struct PsResp {
    #[serde(default)]
    models: Vec<PsModel>,
}
#[derive(serde::Deserialize)]
struct PsModel {
    #[serde(default)]
    name: String,
    #[serde(default)]
    size: u64,
    #[serde(default)]
    size_vram: u64,
    #[serde(default)]
    expires_at: String,
}

fn mib(bytes: u64) -> u64 {
    bytes / (1024 * 1024)
}

/// Production [`WarmReclaimer`]: `/api/ps` over loopback HTTP, NVML free via `coord::free_mib`, and
/// `ollama stop <model>` as a subprocess (the ADR-0018 lever, matching the lucid beat path). Owns its
/// own NVML `Arc` (shared with the daemon's). The blocking HTTP read runs on a blocking thread so it
/// never stalls the tokio reactor (the §5 "async, not blocking the reactor" invariant).
pub struct RealReclaimer {
    pub nvml: Arc<Nvml>,
}

impl WarmReclaimer for RealReclaimer {
    async fn resident(&self) -> Vec<ResidentModel> {
        // reqwest's blocking client can't run on the reactor → hop to a blocking thread. Any failure
        // (unreachable Ollama, parse error) → empty set (fail-open: nothing to reclaim).
        let models = tokio::task::spawn_blocking(|| {
            let client = reqwest::blocking::Client::builder()
                .timeout(Duration::from_millis(800))
                .build()
                .ok()?;
            let resp: PsResp = client.get(OLLAMA_PS).send().ok()?.json().ok()?;
            Some(resp.models)
        })
        .await
        .ok()
        .flatten()
        .unwrap_or_default();
        models
            .into_iter()
            .map(|m| ResidentModel {
                name: m.name,
                size_vram_mib: mib(m.size_vram),
                size_mib: mib(m.size),
                expires_at: m.expires_at,
            })
            .collect()
    }

    async fn free_mib(&self) -> Option<u64> {
        crate::coord::free_mib(&self.nvml).await
    }

    async fn stop(&self, model: &str) -> bool {
        // `ollama stop <model>` — graceful unload (keep_alive→0). A subprocess (matches lucid's beat
        // evict + the ADR-0018 lever). Fail-open: a missing binary / nonzero exit → false, the
        // orchestrator moves on. Bounded by a kill-timeout so a hung CLI can't stall the reclaim.
        let fut = tokio::process::Command::new("ollama")
            .arg("stop")
            .arg(model)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
        match tokio::time::timeout(Duration::from_secs(10), fut).await {
            Ok(Ok(status)) => status.success(),
            _ => false, // spawn error, nonzero exit, or timeout — fail-open
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    #[test]
    fn offload_detector_flags_only_partial_residency() {
        // Fully resident: size_vram == size → not offloaded.
        assert!(!offload_detected(17000, 17000));
        // Partially on CPU: size_vram < size → the masking signal.
        assert!(offload_detected(9000, 17000));
        // Not resident at all (0 vram) is "absent", not "offloaded".
        assert!(!offload_detected(0, 17000));
        // size_vram > size (over-report) is not an offload.
        assert!(!offload_detected(18000, 17000));
    }

    #[test]
    fn cold_first_orders_by_soonest_expiry_unknown_first() {
        let m = |name: &str, exp: &str| ResidentModel {
            name: name.into(),
            size_vram_mib: 1000,
            size_mib: 1000,
            expires_at: exp.into(),
        };
        let models = vec![
            m("hot", "2026-06-20T18:00:00Z"),  // freshest keep-alive — protected, unloaded last
            m("cold", "2026-06-20T15:00:00Z"), // soonest expiry — coldest, unloaded first
            m("unknown", ""),                  // unknown expiry — treated as coldest
        ];
        let order: Vec<&str> = cold_first_order(&models).iter().map(|&i| models[i].name.as_str()).collect();
        assert_eq!(order, vec!["unknown", "cold", "hot"]);
    }

    /// Scripted reclaimer: a resident set + a free-VRAM trajectory that advances one step each time a
    /// model is stopped (and one step per poll while no stop happened — so polls eventually "see" the
    /// freed VRAM). Drives the orchestrator with no live Ollama/GPU.
    struct MockReclaimer {
        models: Vec<ResidentModel>,
        /// Free VRAM after k stops (index 0 = before any stop). The orchestrator's poll reads the
        /// value for the current stop count, so `stop()` is what advances free VRAM.
        free_by_stops: Vec<u64>,
        stops: RefCell<usize>,
        stop_log: RefCell<Vec<String>>,
        /// Models whose `stop()` should fail (fail-open path).
        fail: Vec<String>,
    }
    impl MockReclaimer {
        fn new(models: Vec<ResidentModel>, free_by_stops: Vec<u64>) -> Self {
            Self {
                models,
                free_by_stops,
                stops: RefCell::new(0),
                stop_log: RefCell::new(vec![]),
                fail: vec![],
            }
        }
    }
    impl WarmReclaimer for MockReclaimer {
        async fn resident(&self) -> Vec<ResidentModel> {
            self.models.clone()
        }
        async fn free_mib(&self) -> Option<u64> {
            let k = (*self.stops.borrow()).min(self.free_by_stops.len() - 1);
            Some(self.free_by_stops[k])
        }
        async fn stop(&self, model: &str) -> bool {
            if self.fail.iter().any(|f| f == model) {
                return false; // fail-open: no progress for this model
            }
            *self.stops.borrow_mut() += 1;
            self.stop_log.borrow_mut().push(model.to_string());
            true
        }
    }

    fn small(name: &str, exp: &str) -> ResidentModel {
        ResidentModel { name: name.into(), size_vram_mib: 5000, size_mib: 5000, expires_at: exp.into() }
    }

    // Fast poll params for tests (no real waiting of consequence).
    const T: u32 = 3;
    const I: Duration = Duration::from_millis(0);

    #[tokio::test]
    async fn no_op_when_already_fits() {
        // free 8000, need 5000 + headroom 512 → already fits; must stop nothing.
        let r = MockReclaimer::new(vec![small("a", "")], vec![8000]);
        let out = reclaim_until_fits(&r, 5000, 512, 3, T, I).await;
        assert!(out.satisfied);
        assert!(out.stopped.is_empty());
        assert_eq!(out.free_before, 8000);
    }

    #[tokio::test]
    async fn stops_cold_first_minimum_needed_then_satisfied() {
        // Need 5000+512. Start free 1000; each stop frees ~4500. After ONE cold stop → 5500 ≥ 5512? no
        // (5500 < 5512). After TWO → 10000 ≥ 5512 → satisfied. Must stop the two coldest, in order.
        let models = vec![
            small("hot", "2026-06-20T18:00:00Z"),
            small("cold", "2026-06-20T15:00:00Z"),
            small("mid", "2026-06-20T16:00:00Z"),
        ];
        let r = MockReclaimer::new(models, vec![1000, 5500, 10000]);
        let out = reclaim_until_fits(&r, 5000, 512, 3, T, I).await;
        assert!(out.satisfied);
        assert_eq!(out.stopped, vec!["cold", "mid"]); // cold-first, exactly the two needed
        assert_eq!(out.free_after, 10000);
        assert_eq!(*r.stop_log.borrow(), vec!["cold", "mid"]);
    }

    #[tokio::test]
    async fn bounded_by_max_stops_and_reports_unsatisfied() {
        // Free never rises enough within max_stops=1 → unsatisfied, exactly one stop, fail-open return.
        let models = vec![small("a", "2026-06-20T15:00:00Z"), small("b", "2026-06-20T16:00:00Z")];
        let r = MockReclaimer::new(models, vec![1000, 1200, 1400]);
        let out = reclaim_until_fits(&r, 5000, 512, 1, T, I).await;
        assert!(!out.satisfied);
        assert_eq!(out.stopped.len(), 1);
    }

    #[tokio::test]
    async fn failing_stop_is_skipped_fail_open() {
        // The coldest model's stop fails → skip it, move to the next-coldest, still make progress.
        let models = vec![small("cold", "2026-06-20T15:00:00Z"), small("warm", "2026-06-20T17:00:00Z")];
        let mut r = MockReclaimer::new(models, vec![1000, 6000]);
        r.fail = vec!["cold".into()];
        let out = reclaim_until_fits(&r, 5000, 512, 3, T, I).await;
        assert!(out.satisfied);
        assert_eq!(out.stopped, vec!["warm"]); // cold failed → only warm actually stopped
    }

    #[tokio::test]
    async fn reports_offloaded_models() {
        let models = vec![
            ResidentModel { name: "spilled".into(), size_vram_mib: 9000, size_mib: 17000, expires_at: "".into() },
            ResidentModel { name: "resident".into(), size_vram_mib: 5000, size_mib: 5000, expires_at: "".into() },
        ];
        let r = MockReclaimer::new(models, vec![1000, 20000]);
        let out = reclaim_until_fits(&r, 5000, 512, 3, T, I).await;
        assert_eq!(out.offloaded, vec!["spilled"]);
    }

    #[tokio::test]
    async fn empty_warm_pool_is_a_clean_no_progress() {
        // Nothing resident (unreachable Ollama / empty) → no stops, unsatisfied, no panic.
        let r = MockReclaimer::new(vec![], vec![1000]);
        let out = reclaim_until_fits(&r, 5000, 512, 3, T, I).await;
        assert!(!out.satisfied);
        assert!(out.stopped.is_empty());
    }
}

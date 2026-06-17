//! `coexist` — read the telemetry history, PROPOSE a residency plan (ADR-0018 §4).
//!
//! Phase 2 of the coexistence work. It is the "model proposes" half of model-proposes/code-disposes:
//! a strictly READ-ONLY analyzer over `telemetry.jsonl` (+ the rotated `.1`) that turns recorded
//! VRAM/residency/event history into a concrete, explainable recommendation —
//!   * which models can **coexist warm** (footprint fits the shared budget) vs. which are
//!     **heavy-lane / exclusive** (swap through the ADR-0010 lease, graphics-yielded);
//!   * `OLLAMA_MAX_LOADED_MODELS` / `OLLAMA_KEEP_ALIVE` for the warm set;
//!   * each model's **real** admission footprint, correcting Ollama's `size_vram` undercount
//!     (learned from the free-VRAM drop at each `load:` event, not guessed).
//!
//! It NEVER writes Ollama config, restarts anything, or touches the GPU. Applying the plan is a
//! separate, gated, reversible step (Phase 3). The pure aggregation/selection core is unit-tested.

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use serde::Deserialize;

/// Headroom kept free above the warm set (mirrors `main::SAFETY_MIB`); the budget is conservative.
const SAFETY_MIB: i64 = 512;
/// Free-VRAM "near-miss" threshold for the go/no-go signals — below this the next load is at risk.
const NEAR_MIB: i64 = 1500;
/// A model is "warm-poolable" only if its real footprint leaves room for at least one peer — i.e.
/// it takes no more than this fraction of the LLM budget. Above it, the model is exclusive
/// (heavy-lane). 0.5 ⇒ "must fit alongside an equal-sized peer", matching ADR-0018's bounded
/// coexistence (≤10 GB tier on a ~16 GB budget).
const COEXIST_FRACTION: f64 = 0.5;
/// Fallback `size_vram → real footprint` multiplier when no `load:` event was captured to learn it
/// from (measured ~1.45× for gemma4, 2026-06-16 — CUDA context + KV cache are invisible to size_vram).
const DEFAULT_UNDERCOUNT: f64 = 1.45;
/// Don't trust a footprint learned from fewer than this many CLEAN load-deltas (a 1-sample footprint
/// is indistinguishable from a 500-sample one — determinism review); below it, use corrected size_vram.
const MIN_FOOTPRINT_SAMPLES: usize = 3;
/// Cap on the learned size_vram→real multiplier. The plausible band is ~1.0–1.5 (measured 1.42–1.45);
/// above this is a noisy log, not physics. The low side is pinned to 1.0 by the clean-load filter.
const UNDERCOUNT_MAX: f64 = 2.0;
/// Below this many parsed samples the recommendation is low-confidence — say so, don't pretend.
const MIN_SAMPLES: usize = 60;
/// Recency half-life for the popularity signal: a load this long ago counts half as much as one
/// now. Anchored to the NEWEST sample in the log (not wall-clock) so the plan is replay-deterministic.
const RECENCY_HALF_LIFE_MS: f64 = 36.0 * 3600.0 * 1000.0; // 36 h

/// Recency weight in (0,1]: 1.0 at age 0, 0.5 at one half-life. `exp(-ln2 · age / halflife)`.
fn recency_weight(age_ms: u64) -> f64 {
    (-std::f64::consts::LN_2 * (age_ms as f64) / RECENCY_HALF_LIFE_MS).exp()
}

// ---------------------------------------------------------------------------
// The consumer's own parse view (decoupled from the producer struct — only the fields we need,
// each lenient so a partial/older line degrades instead of aborting the whole analysis).
// ---------------------------------------------------------------------------

#[derive(Deserialize)]
struct RawTick {
    #[serde(default)]
    ts_ms: u64,
    #[serde(default)]
    vram: RawVram,
    #[serde(default)]
    residency: Vec<RawRes>,
    #[serde(default)]
    events: Vec<String>,
}
#[derive(Deserialize, Default)]
struct RawVram {
    #[serde(default)]
    used_mib: i64,
    #[serde(default)]
    free_mib: i64,
    #[serde(default)]
    total_mib: i64,
}
#[derive(Deserialize)]
struct RawRes {
    name: String,
    #[serde(default)]
    vram_mib: u64,
}

// ---------------------------------------------------------------------------
// Aggregates (pure, testable).
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq)]
pub struct ModelStat {
    pub name: String,
    /// Ticks the model was resident. Kept as a DIAGNOSTIC only — NOT the ranking key: it is inflated
    /// by keep-alive (a model held warm banks ticks it didn't earn), so ranking on it is a feedback
    /// loop (longer keep-alive → more ticks → ranks higher → longer keep-alive). Rank on `load_score`.
    pub ticks_resident: u64,
    /// Invocation count (captured `load:` events) — keep-alive does NOT inflate this (a warm model
    /// isn't re-loaded), so it's the honest intent signal.
    pub loads: u64,
    pub unloads: u64,
    /// Recency-weighted load count (newer loads count more) — the popularity signal the warm-set
    /// ranking actually uses. Self-reinforcement-free and tracks current habits, not stale ones.
    pub load_score: f64,
    /// Max `size_vram` Ollama reported (MiB) — the undercounting number.
    pub reported_max_mib: u64,
    /// Median real footprint (MiB) from NVML free-drops at `load:` events; `None` if never observed
    /// loading (then we estimate from `reported_max_mib * undercount`).
    pub real_footprint_mib: Option<u64>,
    /// How many clean load-deltas the footprint was learned from — a confidence the Phase-3 admission
    /// gate must check (don't trust a 1-sample footprint).
    pub footprint_samples: usize,
}

impl ModelStat {
    /// The number admission should reserve. Trust a measured footprint only with enough clean samples;
    /// otherwise use corrected `size_vram`. Floored to `reported_max_mib` so a contaminated free-delta
    /// (observed: real read *below* size_vram) can never under-reserve — under-reserving causes OOM.
    pub(crate) fn admission_mib(&self, undercount: f64) -> u64 {
        let estimate = match self.real_footprint_mib {
            Some(v) if self.footprint_samples >= MIN_FOOTPRINT_SAMPLES => v,
            _ => ((self.reported_max_mib as f64) * undercount).round() as u64,
        };
        estimate.max(self.reported_max_mib)
    }
}

/// The Phase-3 go/no-go evidence, computed directly from the window. This is the decision the whole
/// measure-first effort hinges on — surfaced in one command instead of an ad-hoc script.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Signals {
    /// Most models resident at once — did `MAX_LOADED>1` ever actually hold two?
    pub max_concurrent: usize,
    /// Ticks with ≥2 co-resident (coexistence actually happening, not just permitted).
    pub coexist_ticks: usize,
    /// Reloads of warm-poolable models a persistent pool would have prevented (the swap penalty).
    pub avoided_swaps: u64,
    /// Ticks with free VRAM below `NEAR_MIB` / below `SAFETY_MIB` (OOM proximity).
    pub oom_near_ticks: usize,
    pub oom_danger_ticks: usize,
    pub min_free_mib: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    pub samples: usize,
    pub total_mib: i64,
    pub baseline_mib: i64,
    pub llm_budget_mib: i64,
    pub undercount: f64,
    /// All models seen, most-frequent first.
    pub models: Vec<ModelStat>,
    /// Frequency-greedy pick of warm-poolable models that fit the budget together.
    pub warm_set: Vec<String>,
    /// Models too big to share — swap through the lease (heavy lane).
    pub exclusive: Vec<String>,
    /// Per-model placement rationale (warm/heavy + why) — for the report and keyhole legibility.
    pub reasons: BTreeMap<String, String>,
    pub signals: Signals,
    pub max_loaded: usize,
    pub keep_alive_min: u64,
    /// `samples >= MIN_SAMPLES`. Below it the config block is suppressed (keep Ollama defaults) so a
    /// 2-minute sample can't masquerade as an authoritative recommendation.
    pub confident: bool,
    pub warnings: Vec<String>,
}

/// The go/no-go evidence, computed from the window. `warm_poolable` is the by-size set (footprint ≤
/// the coexist ceiling) — the models that COULD coexist if the budget allowed; their reloads are the
/// swaps a bigger warm pool would avoid.
fn compute_signals(
    ticks: &[RawTick],
    warm_poolable: &std::collections::BTreeSet<String>,
    models: &[ModelStat],
) -> Signals {
    let mut s = Signals { min_free_mib: -1, ..Signals::default() };
    let mut min_free = i64::MAX;
    for t in ticks {
        let n = t.residency.len();
        s.max_concurrent = s.max_concurrent.max(n);
        if n >= 2 {
            s.coexist_ticks += 1;
        }
        let f = t.vram.free_mib;
        if f > 0 {
            min_free = min_free.min(f);
            if f < NEAR_MIB {
                s.oom_near_ticks += 1;
            }
            if f < SAFETY_MIB {
                s.oom_danger_ticks += 1;
            }
        }
    }
    if min_free != i64::MAX {
        s.min_free_mib = min_free;
    }
    s.avoided_swaps = models
        .iter()
        .filter(|m| warm_poolable.contains(&m.name))
        .map(|m| m.loads.saturating_sub(1))
        .sum();
    s
}

fn median(mut v: Vec<u64>) -> Option<u64> {
    if v.is_empty() {
        return None;
    }
    v.sort_unstable();
    Some(v[v.len() / 2])
}

/// Aggregate raw ticks into per-model stats. Real footprint comes from the drop in free VRAM across
/// a `load:` event (NVML truth), reclaim from the rise across `unload:` — both ignored when free is
/// unknown (≤0). Deterministic; the heart of the recommendation.
fn aggregate(ticks: &[RawTick]) -> (Vec<ModelStat>, f64) {
    let mut resident: BTreeMap<String, u64> = BTreeMap::new();
    let mut loads: BTreeMap<String, u64> = BTreeMap::new();
    let mut load_score: BTreeMap<String, f64> = BTreeMap::new();
    let mut unloads: BTreeMap<String, u64> = BTreeMap::new();
    let mut reported: BTreeMap<String, u64> = BTreeMap::new();
    let mut deltas: BTreeMap<String, Vec<u64>> = BTreeMap::new();
    let mut undercount_ratios: Vec<f64> = Vec::new();

    // Anchor recency to the newest sample (deterministic; no wall-clock).
    let max_ts = ticks.iter().map(|t| t.ts_ms).max().unwrap_or(0);

    for (i, t) in ticks.iter().enumerate() {
        for r in &t.residency {
            *resident.entry(r.name.clone()).or_default() += 1;
            let e = reported.entry(r.name.clone()).or_default();
            *e = (*e).max(r.vram_mib);
        }
        for ev in &t.events {
            if let Some(name) = ev.strip_prefix("load:") {
                *loads.entry(name.to_string()).or_default() += 1;
                *load_score.entry(name.to_string()).or_default() +=
                    recency_weight(max_ts.saturating_sub(t.ts_ms));
                // Footprint = free[i-1] - free[i]. Accept ONLY a clean load: the drop must be at least
                // the reported size_vram. A drop smaller than size_vram means another allocation freed
                // concurrently in the tick — contaminated; discard it rather than learn a too-low
                // (OOM-dangerous) footprint. (Observed live: a single bad tick yielded a ×0.83 ratio.)
                if i > 0 {
                    let (prev, now) = (ticks[i - 1].vram.free_mib, t.vram.free_mib);
                    let rep =
                        t.residency.iter().find(|r| r.name == name).map(|r| r.vram_mib).unwrap_or(0);
                    if prev > 0 && now > 0 && prev > now && rep > 0 {
                        let delta = (prev - now) as u64;
                        if delta >= rep {
                            deltas.entry(name.to_string()).or_default().push(delta);
                            undercount_ratios.push(delta as f64 / rep as f64); // ≥ 1.0 by construction
                        }
                    }
                }
            } else if let Some(name) = ev.strip_prefix("unload:") {
                *unloads.entry(name.to_string()).or_default() += 1;
            }
        }
    }

    let undercount = if undercount_ratios.is_empty() {
        DEFAULT_UNDERCOUNT
    } else {
        // total_cmp: total order, can't panic on a non-finite ratio (NaN guard, determinism review).
        undercount_ratios.sort_by(|a, b| a.total_cmp(b));
        undercount_ratios[undercount_ratios.len() / 2] // median ratio
    }
    .min(UNDERCOUNT_MAX); // cap the high side; the low side is ≥1.0 from the clean-load filter

    let names: std::collections::BTreeSet<String> =
        resident.keys().chain(loads.keys()).cloned().collect();
    let mut models: Vec<ModelStat> = names
        .into_iter()
        .map(|name| {
            let d = deltas.remove(&name).unwrap_or_default();
            ModelStat {
                ticks_resident: resident.get(&name).copied().unwrap_or(0),
                loads: loads.get(&name).copied().unwrap_or(0),
                load_score: load_score.get(&name).copied().unwrap_or(0.0),
                unloads: unloads.get(&name).copied().unwrap_or(0),
                reported_max_mib: reported.get(&name).copied().unwrap_or(0),
                footprint_samples: d.len(),
                real_footprint_mib: median(d),
                name,
            }
        })
        .collect();
    // Rank by recency-weighted popularity (NOT ticks_resident — see ModelStat). ticks_resident then
    // name break ties deterministically; total_cmp can't panic on the f64 score.
    models.sort_by(|a, b| {
        b.load_score
            .total_cmp(&a.load_score)
            .then(b.ticks_resident.cmp(&a.ticks_resident))
            .then(a.name.cmp(&b.name))
    });
    (models, undercount)
}

/// The non-LLM VRAM floor the warm set must coexist with: the median `used_mib` over ticks where NO
/// model was resident (graphics + persistent compute). That is what is *always* taken before any LLM.
fn baseline_mib(ticks: &[RawTick]) -> Option<i64> {
    // Preferred: median used VRAM over ticks with NO model resident — the true non-LLM floor.
    let idle: Vec<u64> = ticks
        .iter()
        .filter(|t| t.residency.is_empty() && t.vram.used_mib > 0)
        .map(|t| t.vram.used_mib as u64)
        .collect();
    if let Some(m) = median(idle) {
        return Some(m as i64);
    }
    // Fallback (a model was always resident → no idle ticks, which a long keep-alive can cause):
    // used − the resident models' reported size_vram. size_vram UNDERcounts the model, so we remove
    // too little → baseline reads a touch HIGH → budget a touch LOW → conservative (the safe way).
    let est: Vec<u64> = ticks
        .iter()
        .filter(|t| t.vram.used_mib > 0)
        .map(|t| {
            let models: u64 = t.residency.iter().map(|r| r.vram_mib).sum();
            (t.vram.used_mib as u64).saturating_sub(models)
        })
        .collect();
    median(est).map(|m| m as i64)
}

/// Assemble the plan: budget = total − baseline − safety; classify each model warm-poolable vs
/// exclusive by footprint; greedily fill the warm set by frequency under the budget.
fn build_plan(ticks: &[RawTick]) -> Plan {
    let (models, undercount) = aggregate(ticks);
    let mut warnings = Vec::new();

    if ticks.len() < MIN_SAMPLES {
        warnings.push(format!(
            "only {} samples (<{MIN_SAMPLES}) — recommendation is low-confidence; keep collecting",
            ticks.len()
        ));
    }

    let total_mib =
        ticks.iter().map(|t| t.vram.total_mib).filter(|&v| v > 0).max().unwrap_or(0);
    let baseline_mib = baseline_mib(ticks).unwrap_or_else(|| {
        warnings.push(
            "no idle (no-model) samples — baseline unknown; using 0 (budget will read too high)".into(),
        );
        0
    });
    let llm_budget_mib = (total_mib - baseline_mib - SAFETY_MIB).max(0);

    let coexist_ceiling = (llm_budget_mib as f64 * COEXIST_FRACTION) as u64;
    let mut warm_set = Vec::new();
    let mut exclusive = Vec::new();
    let mut warm_poolable = std::collections::BTreeSet::new();
    let mut reasons: BTreeMap<String, String> = BTreeMap::new();
    let mut used = 0u64;
    let mut rank = 0usize;
    for m in &models {
        let fp = m.admission_mib(undercount);
        if fp == 0 {
            continue; // never observed with a footprint — can't place it
        }
        if fp > coexist_ceiling {
            exclusive.push(m.name.clone());
            reasons.insert(
                m.name.clone(),
                format!("heavy-lane: {} > {} ceiling", gib(fp as i64), gib(coexist_ceiling as i64)),
            );
        } else {
            warm_poolable.insert(m.name.clone()); // could coexist by size, budget permitting
            if (used + fp) as i64 <= llm_budget_mib {
                rank += 1;
                used += fp;
                warm_set.push(m.name.clone());
                reasons.insert(m.name.clone(), format!("warm: rank #{rank}, fits ({})", gib(fp as i64)));
            } else {
                exclusive.push(m.name.clone());
                reasons.insert(
                    m.name.clone(),
                    format!("heavy-lane: budget full ({} of {} used)", gib(used as i64), gib(llm_budget_mib)),
                );
            }
        }
    }
    let signals = compute_signals(ticks, &warm_poolable, &models);

    // Confidence-gated keep-alive: only recommend holding the warm set longer once there's enough
    // data; below MIN_SAMPLES the report keeps Ollama's defaults instead (no thin magic-number
    // heuristic — personalization review dropped the old `loads >= 2`-over-a-week trigger).
    let confident = ticks.len() >= MIN_SAMPLES;
    let keep_alive_min = if confident && !warm_set.is_empty() { 30 } else { 5 };

    Plan {
        samples: ticks.len(),
        total_mib,
        baseline_mib,
        llm_budget_mib,
        undercount,
        max_loaded: warm_set.len().max(1),
        warm_set,
        exclusive,
        reasons,
        signals,
        keep_alive_min,
        confident,
        models,
        warnings,
    }
}

// ---------------------------------------------------------------------------
// IO shell + report.
// ---------------------------------------------------------------------------

fn log_paths() -> Vec<PathBuf> {
    let base = std::env::var("XDG_STATE_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| {
            let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
            format!("{home}/.local/state")
        });
    let dir = PathBuf::from(base).join("agentosd");
    // Oldest first so chronological order is preserved across a rotation.
    vec![dir.join("telemetry.jsonl.1"), dir.join("telemetry.jsonl")]
}

fn load_ticks(paths: &[PathBuf]) -> Vec<RawTick> {
    let mut ticks = Vec::new();
    for p in paths {
        if let Ok(body) = fs::read_to_string(p) {
            for line in body.lines().filter(|l| !l.trim().is_empty()) {
                if let Ok(t) = serde_json::from_str::<RawTick>(line) {
                    ticks.push(t);
                }
            }
        }
    }
    ticks
}

fn gib(mib: i64) -> String {
    format!("{:.1} GiB", mib as f64 / 1024.0)
}

fn report(plan: &Plan) {
    println!("agentosd coexist — residency plan (ADR-0018; read-only, proposes only)\n");
    for w in &plan.warnings {
        println!("  ⚠ {w}");
    }
    if !plan.warnings.is_empty() {
        println!();
    }

    println!(
        "Budget: {} total − {} baseline − {} safety = {} for models  ({} samples)",
        gib(plan.total_mib),
        gib(plan.baseline_mib),
        gib(SAFETY_MIB),
        gib(plan.llm_budget_mib),
        plan.samples,
    );
    println!("size_vram undercount learned: ×{:.2} (CUDA ctx + KV)\n", plan.undercount);

    if plan.models.is_empty() {
        println!("No model residency recorded yet — exercise some models, then re-run.");
        return;
    }

    println!("Per-model (ranked by recency-weighted use; ticks shown as a diagnostic, not the rank key):");
    println!(
        "  {:<30} {:>6} {:>6} {:>10} {:>11} {:>8}",
        "model", "ticks", "loads", "size_vram", "real", "admit"
    );
    for m in &plan.models {
        let real = match m.real_footprint_mib {
            Some(v) => format!("{v}M({}x)", m.footprint_samples), // (N) = clean load-deltas learned from
            None => format!("~{}M*", (m.reported_max_mib as f64 * plan.undercount).round()),
        };
        println!(
            "  {:<30} {:>6} {:>6} {:>9}M {:>11} {:>7}M",
            m.name,
            m.ticks_resident,
            m.loads,
            m.reported_max_mib,
            real,
            m.admission_mib(plan.undercount) // the floored/gated reservation Phase 3 would use
        );
    }
    if plan.models.iter().any(|m| m.real_footprint_mib.is_none()) {
        println!("  (* estimated from size_vram × undercount — model never observed loading)");
    }
    println!();

    println!("Warm pool (coexist, ≤{:.0}% of budget each):", COEXIST_FRACTION * 100.0);
    if plan.warm_set.is_empty() {
        println!("  (none fit as a coexisting set — every used model is heavy-lane on this budget)");
    } else {
        for n in &plan.warm_set {
            println!("  • {n}  — {}", plan.reasons.get(n).map(String::as_str).unwrap_or(""));
        }
    }
    if !plan.exclusive.is_empty() {
        println!("\nHeavy lane (exclusive — swap via the ADR-0010 lease, graphics-yield):");
        for n in &plan.exclusive {
            println!("  • {n}  — {}", plan.reasons.get(n).map(String::as_str).unwrap_or(""));
        }
    }

    // The decision the whole effort hinges on — measured, not guessed.
    let s = &plan.signals;
    println!("\nGo/no-go signals over the window:");
    println!(
        "  models co-resident: max {} | coexistence ticks (≥2): {}",
        s.max_concurrent, s.coexist_ticks
    );
    println!("  avoided-swaps (warm-poolable reloads a persistent pool would prevent): {}", s.avoided_swaps);
    println!(
        "  OOM proximity: min free {}M | near-miss (<{}M): {} ticks | danger (<{}M): {} ticks",
        s.min_free_mib, NEAR_MIB, s.oom_near_ticks, SAFETY_MIB, s.oom_danger_ticks
    );
    let verdict = if s.max_concurrent < 2 && s.avoided_swaps == 0 {
        "no coexistence pressure observed → MAX_LOADED=1 is sufficient; Phase 3 not justified yet"
    } else if s.avoided_swaps > 0 {
        "warm-poolable models are being reloaded → coexistence / longer keep-alive would help"
    } else {
        "coexistence occurred without churn → benign"
    };
    println!("  → {verdict}");
    if s.oom_danger_ticks > 0 {
        println!("  ⚠ free fell below the {}M safety floor — the heavy-lane evictor would matter here", SAFETY_MIB);
    }

    if plan.confident {
        println!("\nProposed Ollama residency config (apply is a separate gated step — Phase 3):");
        println!("  OLLAMA_MAX_LOADED_MODELS={}", plan.max_loaded);
        println!("  OLLAMA_KEEP_ALIVE={}m", plan.keep_alive_min);
        println!("  OLLAMA_NUM_PARALLEL=2   OLLAMA_FLASH_ATTENTION=1   OLLAMA_KV_CACHE_TYPE=q8_0");
    } else {
        println!(
            "\nInsufficient data ({} < {} samples) — keep Ollama defaults; re-run after more real usage.",
            plan.samples, MIN_SAMPLES
        );
    }
}

/// Load the telemetry log and build the plan, or `None` if there is no data. Shared by `coexist`
/// (which reports it) and `agentosd mcp` (which serves `gpu_residency` from it) — ADR-0020.
pub fn load_plan() -> Option<Plan> {
    let ticks = load_ticks(&log_paths());
    if ticks.is_empty() {
        None
    } else {
        Some(build_plan(&ticks))
    }
}

pub fn run(_args: Vec<String>) -> Result<(), Box<dyn std::error::Error>> {
    match load_plan() {
        Some(plan) => report(&plan),
        None => {
            eprintln!(
                "agentosd coexist: no telemetry at {} — start the collector (`agentosd telemetry`) and let it run.",
                log_paths().last().map(|p| p.display().to_string()).unwrap_or_default()
            );
            std::process::exit(1);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tick_ts(ts: u64, used: i64, free: i64, total: i64, res: &[(&str, u64)], events: &[&str]) -> RawTick {
        RawTick {
            ts_ms: ts,
            vram: RawVram { used_mib: used, free_mib: free, total_mib: total },
            residency: res.iter().map(|(n, v)| RawRes { name: n.to_string(), vram_mib: *v }).collect(),
            events: events.iter().map(|s| s.to_string()).collect(),
        }
    }
    fn tick(used: i64, free: i64, total: i64, res: &[(&str, u64)], events: &[&str]) -> RawTick {
        tick_ts(0, used, free, total, res, events)
    }

    #[test]
    fn footprint_and_undercount_learned_from_load_deltas() {
        // Idle baseline 8000 used / free 16000; then a model loads taking a real 4700M (free→11300)
        // while Ollama reports only 3236M → undercount ≈ 1.45.
        let ticks = vec![
            tick(8000, 16000, 24000, &[], &[]),
            tick(12700, 11300, 24000, &[("gemma", 3236)], &["load:gemma"]),
            tick(12700, 11300, 24000, &[("gemma", 3236)], &[]),
            tick(8000, 16000, 24000, &[], &["unload:gemma"]),
        ];
        let (models, undercount) = aggregate(&ticks);
        let g = models.iter().find(|m| m.name == "gemma").unwrap();
        assert_eq!(g.real_footprint_mib, Some(4700));
        assert_eq!(g.footprint_samples, 1);
        assert!(g.load_score > 0.0);
        assert_eq!(g.loads, 1);
        assert_eq!(g.unloads, 1);
        assert_eq!(g.ticks_resident, 2);
        assert_eq!(g.reported_max_mib, 3236);
        assert!((undercount - (4700.0 / 3236.0)).abs() < 0.001);
        // admission reserves the REAL number, not size_vram.
        assert_eq!(g.admission_mib(undercount), 4700);
    }

    #[test]
    fn contaminated_load_delta_discarded_and_admission_floors_to_size_vram() {
        // Reproduces the live ×0.83 case: the free-drop (2797) read SMALLER than reported size_vram
        // (3385) because a concurrent free contaminated the tick. The delta must be discarded (not
        // learned as a too-low footprint), the undercount must fall back to the default (never <1.0),
        // and admission must never reserve below size_vram.
        let ticks = vec![
            tick(8000, 16000, 24000, &[], &[]),
            tick(10800, 13203, 24000, &[("m", 3385)], &["load:m"]), // free fell only 2797 < 3385
            tick(8000, 16000, 24000, &[], &["unload:m"]),
        ];
        let (models, undercount) = aggregate(&ticks);
        let m = &models[0];
        assert_eq!(m.real_footprint_mib, None, "contaminated delta must not be learned");
        assert_eq!(m.footprint_samples, 0);
        assert_eq!(undercount, DEFAULT_UNDERCOUNT, "no clean ratio → default, never <1.0");
        assert!(m.admission_mib(undercount) >= 3385, "must never reserve below size_vram");
    }

    #[test]
    fn one_clean_sample_is_not_yet_trusted_but_still_floors_above_size_vram() {
        // A single clean load (delta 5000 ≥ size_vram 3500): recorded, but below MIN_FOOTPRINT_SAMPLES,
        // so admission uses corrected size_vram rather than the lone measurement — and never less.
        let ticks = vec![
            tick(8000, 16000, 24000, &[], &[]),
            tick(13000, 11000, 24000, &[("m", 3500)], &["load:m"]),
        ];
        let (models, undercount) = aggregate(&ticks);
        let m = &models[0];
        assert_eq!(m.footprint_samples, 1);
        assert!(m.admission_mib(undercount) >= 3500);
        assert!(undercount <= UNDERCOUNT_MAX && undercount >= 1.0);
    }

    #[test]
    fn ranking_uses_loads_not_resident_ticks() {
        // `lingerer`: ONE load but resident for many ticks (keep-alive inflation). `frequent`: three
        // recent loads, barely resident. The fix ranks `frequent` first — the keep-alive feedback
        // loop is broken (ranking on loads, not ticks_resident).
        let mut ticks = vec![tick_ts(1000, 8000, 16000, 24000, &[], &["load:lingerer"])];
        for _ in 0..20 {
            ticks.push(tick_ts(1500, 12000, 4000, 24000, &[("lingerer", 3000)], &[]));
        }
        for k in 0..3 {
            ticks.push(tick_ts(2000 + k, 8000, 16000, 24000, &[], &["load:frequent"]));
        }
        let (models, _) = aggregate(&ticks);
        assert_eq!(models[0].name, "frequent", "rank must follow loads, not resident ticks");
        let ling = models.iter().find(|m| m.name == "lingerer").unwrap();
        let freq = models.iter().find(|m| m.name == "frequent").unwrap();
        assert!(freq.load_score > ling.load_score);
        assert!(ling.ticks_resident > freq.ticks_resident); // despite far more resident time
    }

    #[test]
    fn warm_pool_vs_heavy_lane_classification() {
        // Budget after baseline: total 24000 − baseline 8000 − 512 = 15488; coexist ceiling ~7744M.
        // small (5000M real, frequent) is warm-poolable; big (16000M) is heavy-lane (exclusive).
        let mut ticks = vec![tick(8000, 16000, 24000, &[], &[])];
        for _ in 0..5 {
            ticks.push(tick(13000, 11000, 24000, &[("small", 3500)], &[]));
        }
        // teach the real footprint of `small` via one load delta (16000→11000 = 5000M).
        ticks.insert(1, tick(13000, 11000, 24000, &[("small", 3500)], &["load:small"]));
        // a big model seen resident (never observed loading → estimated via undercount).
        ticks.push(tick(8000, 16000, 24000, &[("big", 11000)], &[]));

        let plan = build_plan(&ticks);
        assert_eq!(plan.baseline_mib, 8000);
        assert!(plan.warm_set.contains(&"small".to_string()), "small should be warm-poolable");
        assert!(plan.exclusive.contains(&"big".to_string()), "big should be heavy-lane");
        assert_eq!(plan.max_loaded, 1); // only `small` fits the warm set here
    }

    #[test]
    fn signals_detect_coexistence_and_avoided_swaps() {
        // Two small models co-reside for some ticks; `b` is unloaded then reloaded (a swap a bigger
        // warm pool would have avoided).
        let ticks = vec![
            tick(8000, 16000, 24000, &[], &[]),
            tick(13000, 11000, 24000, &[("a", 3000)], &["load:a"]),
            tick(18000, 6000, 24000, &[("a", 3000), ("b", 3000)], &["load:b"]),
            tick(13000, 11000, 24000, &[("a", 3000)], &["unload:b"]),
            tick(18000, 6000, 24000, &[("a", 3000), ("b", 3000)], &["load:b"]),
        ];
        let plan = build_plan(&ticks);
        assert_eq!(plan.signals.max_concurrent, 2);
        assert!(plan.signals.coexist_ticks >= 2);
        assert!(plan.signals.avoided_swaps >= 1, "b reloaded → at least one avoided-swap");
        // every placed model carries a reason
        for n in plan.warm_set.iter().chain(plan.exclusive.iter()) {
            assert!(plan.reasons.contains_key(n));
        }
    }

    #[test]
    fn low_data_is_flagged_not_hidden() {
        let plan = build_plan(&[tick(8000, 16000, 24000, &[], &[])]);
        assert!(plan.warnings.iter().any(|w| w.contains("low-confidence")));
        // No model residency → empty model list, budget still computed.
        assert!(plan.models.is_empty());
        assert_eq!(plan.total_mib, 24000);
    }

    #[test]
    fn missing_load_event_falls_back_to_corrected_size_vram() {
        // A model only ever seen mid-resident (no load: captured) → footprint estimated, not None.
        let ticks =
            vec![tick(8000, 16000, 24000, &[], &[]), tick(13000, 11000, 24000, &[("m", 4000)], &[])];
        let (models, undercount) = aggregate(&ticks);
        let m = &models[0];
        assert_eq!(m.real_footprint_mib, None);
        assert_eq!(undercount, DEFAULT_UNDERCOUNT);
        assert_eq!(m.admission_mib(undercount), (4000.0 * DEFAULT_UNDERCOUNT).round() as u64);
    }
}

//! `mcp` — the agent-facing GPU surface (ADR-0020: **perceive** + **act**).
//!
//! A minimal MCP server (JSON-RPC 2.0 over stdio, newline-delimited) so a Claude Code / Hermes agent
//! can *see* GPU state and reason about fit — "is the GPU free? what's resident? will my model fit
//! warm or force a swap? why did my request wait?" — and *act* on it within the substrate's rules.
//!
//! **Perceive** (READ-ONLY: reads `keyhole.json` + the `coexist` plan over `telemetry.jsonl`, no NVML,
//! no network): `gpu_status` (live VRAM + lease tier + resident models), `gpu_residency` (warm-pool/
//! heavy-lane + each model's learned admission footprint), `gpu_why` (last-contention, sourced from
//! telemetry — never invented, the ADR-0020 §Consequences honesty rule; with a `request_id` it narrates
//! only the CALLER's own past waits, never another holder — ADR-0021 #10, in-process + holder-free).
//!
//! **Act** (intent-only; the agent proposes, the daemon's `admit`/`arbitrate` core disposes —
//! ADR-0020 §1, gated by ADR-0021 GO-1 tier-clamp + GO-2 identity binding, both met + the ratification
//! must-fixes folded in): `gpu_request` (→ `AcquireAgent`: tier clamped to {best-effort, batch} so an
//! agent can NEVER preempt the desktop; typed outcome codes; ~90s TTL kept alive by a `Renew`
//! heartbeat) and `gpu_release` (→ `Release`; only the acquiring session may release — GO-2 layer 1
//! `holder_peer` + this server's in-process per-session table, layer 2). No raw `Spawn`, no SIGKILL/
//! preempt verb is exposed. The act path FAILS CLOSED: a down/timed-out coordinator yields
//! `unavailable` (no grant), so an agent never reads a dead substrate as permission to launch heavy
//! GPU work (ADR-0021 #4 — the inverse of Hermes interactive's fail-open).
//!
//! ⚠ **Scope (ADR-0021 §Ratification spike).** Act is enabled for the **Claude-Code stdio** transport
//! (subprocess-per-session → one bus name → GO-2 layer 1 suffices). The **Hermes** path is GATED: it
//! multiplexes sub-agents over ONE shared `agentosd mcp` connection AND the MCP frame carries no
//! session field, so neither layer can tell the children apart — closing it needs an upstream Hermes
//! change (per-child connection / trusted per-child principal). Layer 2 is built session-keyed anyway,
//! for the day Hermes surfaces distinct sessions.
//!
//! Every tool returns a typed `status` ("ok"|"granted"|"busy_retry"|"denied"|"released"|"not_holder"|
//! "unavailable"|"error") so an agent can NEVER read a dead substrate as a free GPU or a phantom grant
//! (the ADR-0003 fail-open-inversion trap). The perceive verbs report the lease *tier* but not the
//! holder's identity — naming who holds the card is deferred to v2 (ADR-0021).

use std::io::{BufRead, Write};

use serde_json::{json, Value};

use crate::feed::feed_dir;

/// MCP protocol revision we speak. Bumped only when the handshake shape changes.
const PROTOCOL_VERSION: &str = "2024-11-05";

pub fn run(_args: Vec<String>) -> Result<(), Box<dyn std::error::Error>> {
    let stdin = std::io::stdin();
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        let Ok(req) = serde_json::from_str::<Value>(&line) else {
            continue; // ignore unparseable frames rather than crash the agent's session
        };
        // A request has an `id`; a notification doesn't and gets no response.
        let id = req.get("id").cloned();
        let result = handle_request(&req);
        if let (Some(id), Some(result)) = (id, result) {
            let envelope = json!({"jsonrpc": "2.0", "id": id, "result": result});
            writeln!(out, "{}", serde_json::to_string(&envelope)?)?;
            out.flush()?;
        }
    }
    Ok(())
}

/// Dispatch a JSON-RPC request to its `result`. `None` ⇒ no response (a notification, or a method we
/// don't implement). Pure over its inputs except the `gpu_*` tools, which read substrate files.
fn handle_request(req: &Value) -> Option<Value> {
    match req.get("method").and_then(Value::as_str)? {
        "initialize" => Some(json!({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "agentosd-gpu", "version": env!("CARGO_PKG_VERSION")},
        })),
        "tools/list" => Some(tools_list()),
        "tools/call" => Some(tools_call(req)),
        "ping" => Some(json!({})),
        _ => None, // notifications/initialized, etc.
    }
}

fn tools_list() -> Value {
    let empty = json!({"type": "object", "properties": {}});
    json!({"tools": [
        {
            "name": "gpu_status",
            "description": "Live GPU snapshot: free/used/total VRAM (MiB), the current lease holder + \
                            tier, and which models are resident. Read-only.",
            "inputSchema": empty,
        },
        {
            "name": "gpu_residency",
            "description": "The warm-pool vs heavy-lane plan with each model's LEARNED admission \
                            footprint (corrects Ollama's size_vram undercount), plus go/no-go signals. \
                            Use to reason about whether a model will fit warm or force a swap.",
            "inputSchema": empty,
        },
        {
            "name": "gpu_why",
            "description": "Plain-language explanation of GPU contention, sourced — never invented. With no \
                            argument: the system card (last contention) PLUS your_recent_contention (THIS \
                            session's own recent waits). With request_id (from a prior gpu_request): why ONLY \
                            that request of yours waited — your own contention, never another holder.",
            "inputSchema": {"type": "object", "properties": {
                "request_id": {"type": "integer", "description": "optional: a request_id returned by gpu_request — narrate ONLY why that request of yours waited. Omit for the broad view."},
            }},
        },
        {
            "name": "gpu_request",
            "description": "ACQUIRE a GPU lease before running a model/job. Returns status: granted{token, \
                            tier_effective} | busy_retry{retry_after_ms} (held by an equal/higher tier OR a \
                            just-preempted cooldown — retry; no place is held, there is no queue) | \
                            denied{short_mib} (won't fit) | unavailable (coordinator down — do NOT launch). \
                            Your tier is clamped to 'batch' (an agent can never preempt the desktop). The \
                            lease auto-expires unless held by this session — call gpu_release(token) when done. \
                            Every reply carries a request_id you can later pass to gpu_why to learn why it waited.",
            "inputSchema": {"type": "object", "properties": {
                "tier": {"type": "string", "enum": ["batch", "best-effort"],
                         "description": "requested tier; 'interactive' is clamped to 'batch', 'yielding' raised to 'best-effort'"},
                "estimate_mib": {"type": "integer", "description": "estimated VRAM footprint, MiB"},
            }, "required": ["tier", "estimate_mib"]},
        },
        {
            "name": "gpu_release",
            "description": "RELEASE a lease you hold (the token from gpu_request). Only the session that \
                            acquired the lease may release it. Returns status: released | not_holder | unavailable.",
            "inputSchema": {"type": "object", "properties": {
                "token": {"type": "integer", "description": "the token returned by gpu_request"},
            }, "required": ["token"]},
        },
    ]})
}

fn tools_call(req: &Value) -> Value {
    let name = req.pointer("/params/name").and_then(Value::as_str).unwrap_or("");
    let arg = |p: &str| req.pointer(&format!("/params/arguments/{p}"));
    let (text, is_error) = match name {
        "gpu_status" => (gpu_status(), false),
        "gpu_residency" => (gpu_residency(), false),
        "gpu_why" => {
            // ADR-0021 #10: an optional `request_id` switches gpu_why into the focused per-caller view.
            let rid = arg("request_id").and_then(Value::as_u64);
            (gpu_why(rid), false)
        }
        "gpu_request" => {
            // Least-privilege on malformed input (security review, harvested): default to the LOWER
            // 'best-effort' (never the higher 'batch'), and saturate the estimate so a > u32 value can't
            // wrap to a tiny one and slip admission. The daemon clamps + admits regardless — this just
            // refuses to let a garbage frame ask for more than it typed.
            let tier = arg("tier").and_then(Value::as_str).unwrap_or("best-effort");
            let est = arg("estimate_mib").and_then(Value::as_u64).unwrap_or(0).min(u32::MAX as u64) as u32;
            (gpu_request(tier, est), false)
        }
        "gpu_release" => {
            let token = arg("token").and_then(Value::as_u64).unwrap_or(0);
            (gpu_release(token), false)
        }
        other => (json!({"error": format!("unknown tool: {other}")}).to_string(), true),
    };
    json!({"content": [{"type": "text", "text": text}], "isError": is_error})
}

// ---------------------------------------------------------------------------
// Sources — read only what the substrate already wrote. Honest-unknown on absence.
// ---------------------------------------------------------------------------

/// Parse `keyhole.json` (produced by `agentosd keyhole`) as a generic value — decoupled from the
/// producer struct so a schema bump doesn't break perceive.
fn read_keyhole() -> Option<Value> {
    let path = feed_dir().ok()?.join("keyhole.json");
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

fn gpu_status() -> String {
    status_json(read_keyhole()).to_string()
}

/// Pure shaping of `gpu_status` — separated from IO so the JSON contract is exactly pinnable. A
/// missing snapshot returns a typed `unavailable` posture, NEVER a zeroed/free reading: an agent
/// must not mistake a dead substrate for an idle GPU (ADR-0003 fail-open-inversion).
fn status_json(keyhole: Option<Value>) -> Value {
    let Some(k) = keyhole else {
        return json!({
            "status": "unavailable",
            "detail": "keyhole.json not found — is `agentosd keyhole` running? a missing snapshot is NOT a free GPU",
            "source": "keyhole.json",
        });
    };
    let used = k.pointer("/vram/used_mib").and_then(Value::as_i64).unwrap_or(-1);
    let total = k.pointer("/vram/total_mib").and_then(Value::as_i64).unwrap_or(-1);
    let free = if used >= 0 && total >= 0 { total - used } else { -1 };
    let resident: Vec<&str> = k
        .get("residency")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|m| m.get("name").and_then(Value::as_str)).collect())
        .unwrap_or_default();
    json!({
        "status": "ok",
        "vram": {"free_mib": free, "used_mib": used, "total_mib": total},  // -1 == unknown
        // v1 reports the lease *tier* only; the holder's identity is deferred to v2 (ADR-0021 —
        // naming who holds the card is identity info that needs the act-phase plumbing).
        "lease": {"tier": k.pointer("/lease/tier").and_then(Value::as_str).unwrap_or("")},
        "resident_models": resident,
        "source": "keyhole.json",
    })
}

fn gpu_residency() -> String {
    residency_json(crate::analyze::load_plan()).to_string()
}

/// Pure shaping of `gpu_residency`. No plan yet ⇒ typed `unavailable`, not an empty-looking budget.
fn residency_json(plan: Option<crate::analyze::Plan>) -> Value {
    let Some(plan) = plan else {
        return json!({
            "status": "unavailable",
            "detail": "no coexist plan yet — `agentosd telemetry` must run and collect first",
            "source": "coexist plan over telemetry.jsonl",
        });
    };
    let admit = |name: &str| -> u64 {
        plan.models
            .iter()
            .find(|m| m.name == name)
            .map(|m| m.admission_mib(plan.undercount))
            .unwrap_or(0)
    };
    let lane = |names: &[String]| -> Vec<Value> {
        names
            .iter()
            .map(|n| json!({"model": n, "admit_mib": admit(n), "reason": plan.reasons.get(n)}))
            .collect()
    };
    // On-CPU offload (ADR-0018 item 6 #2): models observed running partially on CPU (size_vram <
    // size) — the masking signal behind the 87%-on-CPU failure. Surfaced so an agent perceiving GPU
    // state sees the spill (and that the heavy-lane evictor / ComfyUI-under-lease would free the card).
    let offloaded: Vec<Value> = plan
        .models
        .iter()
        .filter(|m| m.offloaded_ticks > 0)
        .map(|m| json!({"model": m.name, "on_cpu_mib": m.max_offload_mib, "ticks": m.offloaded_ticks}))
        .collect();
    json!({
        "status": "ok",
        "budget_mib": plan.llm_budget_mib,
        "baseline_mib": plan.baseline_mib,
        "total_mib": plan.total_mib,
        "undercount": plan.undercount,
        "confident": plan.confident,
        "warm_pool": lane(&plan.warm_set),
        "heavy_lane": lane(&plan.exclusive),
        "offloaded": offloaded,
        "signals": {
            "max_concurrent": plan.signals.max_concurrent,
            "coexist_ticks": plan.signals.coexist_ticks,
            "avoided_swaps": plan.signals.avoided_swaps,
            "min_free_mib": plan.signals.min_free_mib,
            "oom_near_ticks": plan.signals.oom_near_ticks,
            "oom_danger_ticks": plan.signals.oom_danger_ticks,
        },
        "note": "admit_mib is the learned, size_vram-floored reservation; a model fits warm only if it \
                 is in warm_pool. `offloaded` lists models that spilled to CPU (VRAM was short). \
                 Phase-3 eviction is not yet active.",
    })
}

fn gpu_why(request_id: Option<u64>) -> String {
    // The per-caller contentions are this session's own (in-process, holder-free). Snapshot them, then
    // shape purely — `why_json` does the focused-vs-broad split + the no-leak split.
    let caller = lock_contention().recent(LOCAL_SESSION);
    why_json(read_keyhole(), crate::analyze::load_plan(), &caller, request_id).to_string()
}

/// Pure shaping of `gpu_why` — two queries, one shaper.
///
/// FOCUSED (`request_id` given, ADR-0021 #10): "why did MY request wait?" — answered PURELY from this
/// session's own holder-free contentions, NEVER the system-level `last_contention` (which may name a
/// holder). Works even when blind, since the ring is in-process.
///
/// BROAD (`request_id` = None): the system card sourced from the keyhole's `lease.preempt` (ADR-0012) +
/// coexist signals, NEVER generated, plus this session's own `your_recent_contention`. Three honest
/// states, not two — both system sources unreadable AND no per-caller history ⇒ `unavailable` ("blind",
/// not "calm"); readable + empty preempt ⇒ the calm line; a real preempt ⇒ its narration verbatim.
fn why_json(
    keyhole: Option<Value>,
    plan: Option<crate::analyze::Plan>,
    caller: &[Contention],
    request_id: Option<u64>,
) -> Value {
    // FOCUSED per-caller query — caller-only, never the system card (the strict no-leak path).
    if let Some(id) = request_id {
        return match caller.iter().find(|c| c.request_id == id) {
            Some(c) => json!({
                "status": "ok",
                "request_id": id,
                "kind": c.kind,
                "why": c.why,
                "source": "your own act requests (in-process, this session) — never another holder",
            }),
            None => json!({
                "status": "ok",
                "request_id": id,
                "why": format!(
                    "no contention recorded for request {id} — it was granted and didn't wait, was never \
                     issued, or has aged out of the recent window (last {CONTENTION_RING_CAP})"
                ),
                "source": "your own act requests (in-process, this session)",
            }),
        };
    }

    // BROAD query: "blind" (unavailable, not calm) ONLY when there is nothing to say at all — no system
    // sources AND no per-caller history. (This early-return shape is pinned by test.)
    if keyhole.is_none() && plan.is_none() && caller.is_empty() {
        return json!({
            "status": "unavailable",
            "detail": "can't see the GPU right now (no keyhole snapshot, no telemetry) — this is 'blind', not 'calm'",
            "source": "keyhole.json (lease.preempt) + telemetry signals",
        });
    }
    let last_contention = match keyhole.as_ref() {
        None => "no lease snapshot to read".to_string(),
        Some(k) => k
            .pointer("/lease/preempt")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| "the card was clear; nothing waited on your behalf".into()),
    };
    let recent_activity = match plan {
        Some(p) => {
            let s = &p.signals;
            if s.avoided_swaps > 0 {
                format!("{} warm-poolable reloads in the window (a bigger warm pool / longer keep-alive would avoid them)", s.avoided_swaps)
            } else if s.oom_danger_ticks > 0 {
                format!("free VRAM fell below the safety floor on {} ticks", s.oom_danger_ticks)
            } else {
                "no contention recorded in the window".to_string()
            }
        }
        None => "no telemetry yet".to_string(),
    };
    // Your OWN recent contentions (holder-free), alongside the system card. Distinct fields on purpose:
    // the system `last_contention` MAY name a holder (it is the keyhole's already-published tray
    // narration); `your_recent_contention` is the #10 surface and is holder-free by construction.
    let your_recent_contention: Vec<Value> = caller
        .iter()
        .map(|c| json!({"request_id": c.request_id, "kind": c.kind, "why": c.why}))
        .collect();
    json!({
        "status": "ok",
        "last_contention": last_contention,
        "recent_activity": recent_activity,
        "your_recent_contention": your_recent_contention,
        "source": "keyhole.json (lease.preempt) + telemetry signals — sourced, not generated; your_recent_contention is this session's own act requests (in-process, holder-free)",
    })
}

// ---------------------------------------------------------------------------
// Act verbs (ADR-0020 §1 "act", gated by ADR-0021 GO-1 tier-clamp + GO-2 identity binding — both met,
// ratification must-fixes folded in). Intent only: the agent proposes, the daemon's `admit`/`arbitrate`
// core disposes — no bypass, no preempt, no raw spawn. ONE session-lifetime D-Bus connection per
// `agentosd mcp` process; a `Renew` heartbeat keeps a live session's short-TTL lease alive; the act
// calls FAIL CLOSED (a down/timed-out coordinator → `unavailable`, never a fabricated grant).
// ---------------------------------------------------------------------------

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

const COORD_BUS: &str = "org.agentos.Coordinator1";
const COORD_PATH: &str = "/org/agentos/Coordinator1";
const COORD_IFACE: &str = "org.agentos.Coordinator1";

/// Bound on every act→daemon round-trip so the call NEVER hangs (ADR-0021 #4, fail-closed): a slow or
/// wedged coordinator yields `unavailable`, not a stuck agent.
const ACT_DBUS_TIMEOUT: Duration = Duration::from_secs(3);

/// The single MCP-session key for this stdio process. The MCP frame carries no session field and stdio
/// is one session per process (ADR-0021 §Ratification spike), so the live server registers all tokens
/// under one key; `SessionTable`'s cross-session guard is exercised by tests + ready for the day a
/// transport surfaces distinct sessions. (Layer 1 — distinct bus name per Claude-Code subprocess — is
/// what actually isolates sessions today.)
const LOCAL_SESSION: &str = "local";

/// `Renew` heartbeat cadence — DERIVED from the daemon's authoritative agent lease TTL
/// (`crate::lease::agent_lease_ttl`, the SAME env source the daemon reads) so the "tick faster than the
/// TTL" coupling holds BY CONSTRUCTION, not by two env vars in two processes agreeing (review:
/// resource-safety Blocker / determinism MED). Default = TTL/4 → ≥4 beats per TTL, so a live session's
/// lease survives ~2 consecutive missed/timed-out renews before the daemon's B5 backstop could reclaim
/// it mid-job. An optional `AGENTOSD_MCP_HEARTBEAT_SECS` override is CLAMPED to `[5s, TTL/3]` — it can
/// tune within the safe band but can NEVER invert the coupling. The 5s floor stops a tiny TTL
/// busy-spinning renews. (The daemon reclaims an abandoned lease within ~one TTL of the session going
/// quiet: the heartbeat — and this connection — die with the stdio process, and B4 also fires then.)
fn heartbeat_interval() -> Duration {
    let ttl = crate::lease::agent_lease_ttl();
    let ceiling = (ttl / 3).max(Duration::from_secs(5)); // never slower than TTL/3 (keep ≥3 beats)
    let chosen = std::env::var("AGENTOSD_MCP_HEARTBEAT_SECS")
        .ok()
        .and_then(|s| s.parse().ok())
        .map(Duration::from_secs)
        .unwrap_or(ttl / 4); // default: 4 beats per TTL
    chosen.clamp(Duration::from_secs(5), ceiling)
}

/// Layer 2 of the GO-2 identity model (ADR-0021 §Ratification): per-MCP-session lease-token ownership,
/// enforced IN-PROCESS *before* the daemon `Release` fires — at most one token per session, releasable
/// only by the session that acquired it. The merged daemon test pins layer 1 (distinct bus names); this
/// freezes the same-connection case the spike showed bus-name binding can't see.
///
/// ⚠ As built, the live stdio server keys everything under `LOCAL_SESSION` (the frame has no session
/// field), so this protects the Claude-Code path (one session/process, AND a distinct bus name = layer 1)
/// but does NOT rescue the multiplexed Hermes path — gated upstream. The structure is session-keyed for
/// the day Hermes surfaces distinct sessions; the cross-session guard is unit-tested via two keys.
#[derive(Debug, Default)]
struct SessionTable {
    /// session-key → the one token that session holds (the lease is single-exclusive: at most one).
    owned: HashMap<String, u64>,
}

impl SessionTable {
    /// Record `session` as the owner of `token` (on a granted `gpu_request`).
    fn acquired(&mut self, session: &str, token: u64) {
        self.owned.insert(session.to_string(), token);
    }
    /// May `session` release/own `token`? Only if it is the token THAT session currently holds.
    fn owns(&self, session: &str, token: u64) -> bool {
        self.owned.get(session) == Some(&token)
    }
    /// Forget `session`'s held token (after a release / observed expiry).
    fn released(&mut self, session: &str) {
        self.owned.remove(session);
    }
    /// The token `session` currently holds (for the heartbeat / cleanup).
    fn held(&self, session: &str) -> Option<u64> {
        self.owned.get(session).copied()
    }
    /// (session, token) pairs to renew — snapshot under the lock, renew OFF it (heartbeat).
    fn snapshot(&self) -> Vec<(String, u64)> {
        self.owned.iter().map(|(s, t)| (s.clone(), *t)).collect()
    }
}

/// Lock the session table, RECOVERING from poisoning (review: security Low). The table is a plain map
/// with no cross-field invariant a panic-while-holding could corrupt, so a poisoned lock must never
/// orphan a lease (drop a grant record so the agent can never `gpu_release` it) or silently no-op the
/// layer-2 guard — we always get a usable guard.
fn lock_sessions(sessions: &Mutex<SessionTable>) -> std::sync::MutexGuard<'_, SessionTable> {
    sessions.lock().unwrap_or_else(|e| e.into_inner())
}

// ---------------------------------------------------------------------------
// Per-caller contention log (ADR-0021 ratification must-fix #10) — the legibility refinement that lets
// an agent ask "why did MY request wait?" and get back ONLY its own lost contentions, NEVER another
// holder's identity. The no-leak guarantee is STRUCTURAL, not a runtime filter: every narration is
// synthesized from the daemon's already-holder-free agent reply (`(granted, token, code,
// tier_effective, short_mib, retry_after_ms)` — no holder name; the holder-naming `msg` goes only to
// the *trusted* busctl/log path) or from the heartbeat's own lease-lost observation. The act layer
// literally never receives a holder name, so it cannot leak one. Fully ephemeral + in-process: the log
// lives only as long as the `agentosd mcp` process (one Claude-Code session), is never persisted, and
// dies with the session — the same privacy bar as the rest of the act surface (ADR-0021 #7).
//
// Keyed by MCP session for the day a transport surfaces distinct sessions (mirrors `SessionTable`); the
// live stdio path is one session (`LOCAL_SESSION`), so the per-session ring IS this agent's ring, and a
// query is scoped to the CALLER's own ring (a guessed id from another session simply isn't found).
// Bounded per session so a hot-looping agent can't grow it without limit.

/// Max per-session retained contentions — the "recent window" `gpu_why` narrates. Small on purpose: an
/// agent cares about its latest few waits, and a full history would be unbounded state for a hot caller.
const CONTENTION_RING_CAP: usize = 8;

/// One narrated contention THIS session experienced — holder-free by construction.
#[derive(Debug, Clone)]
struct Contention {
    /// The correlation id the agent holds (returned by `gpu_request`) — its handle to ask
    /// `gpu_why(request_id)` later. A self-issued event (an async lease loss the agent never polled for)
    /// gets a fresh id so the ring entry stays individually addressable.
    request_id: u64,
    /// Stable machine tag for the contention class (`busy_retry` | `denied` | `lease_lost`).
    kind: &'static str,
    /// Plain-language "why YOUR request waited" — NEVER names another holder (the #10 invariant).
    why: String,
}

/// In-process per-caller contention state (ADR-0021 #10): a monotonic correlation-id source plus a
/// bounded per-session ring of narrated losses.
#[derive(Debug, Default)]
struct ContentionLog {
    /// Monotonic correlation-id source (process-global; an id is an opaque handle, not identity, so a
    /// shared counter across sessions is harmless — lookups are scoped to the caller's own ring).
    next_id: u64,
    /// session-key → its bounded ring of recent contentions (oldest at the front).
    per_session: HashMap<String, VecDeque<Contention>>,
}

impl ContentionLog {
    /// Mint the next correlation id (returned to the agent on EVERY `gpu_request`, loss or not).
    fn mint_id(&mut self) -> u64 {
        // `wrapping_add` documents that wrap is harmless (an id is an opaque per-session-scoped handle,
        // not a count) and is immune to a future pedantic arithmetic lint. Wrap is unreachable in practice.
        self.next_id = self.next_id.wrapping_add(1);
        self.next_id
    }
    /// Record a narrated loss for `session`, evicting the oldest beyond the ring cap.
    fn record(&mut self, session: &str, request_id: u64, kind: &'static str, why: String) {
        let ring = self.per_session.entry(session.to_string()).or_default();
        if ring.len() >= CONTENTION_RING_CAP {
            ring.pop_front();
        }
        ring.push_back(Contention { request_id, kind, why });
    }
    /// This session's recent contentions, MOST-RECENT FIRST (for `gpu_why`'s broad list).
    fn recent(&self, session: &str) -> Vec<Contention> {
        self.per_session
            .get(session)
            .map(|r| r.iter().rev().cloned().collect())
            .unwrap_or_default()
    }
}

fn contention_log() -> &'static Mutex<ContentionLog> {
    static C: OnceLock<Mutex<ContentionLog>> = OnceLock::new();
    C.get_or_init(|| Mutex::new(ContentionLog::default()))
}

/// Lock the contention log, recovering from poisoning (same rationale as `lock_sessions`: a plain ring
/// with no cross-field invariant — a poisoned lock must never silently drop the legibility surface).
fn lock_contention() -> std::sync::MutexGuard<'static, ContentionLog> {
    contention_log().lock().unwrap_or_else(|e| e.into_inner())
}

/// Holder-free narration for a request-time loss, derived from the AGENT-FACING status (never the
/// daemon's holder-naming prose). Pure, so the #10 no-leak invariant is testable here. `None` ⇒ not a
/// contention to narrate: a grant didn't wait, and `unavailable`/`error` are substrate blindness, not a
/// lease wait (`gpu_why`'s blind/unavailable posture already covers a down coordinator).
fn contention_why(status: &str, requested_tier: &str, short_mib: u64) -> Option<(&'static str, String)> {
    match status {
        "busy_retry" => Some((
            "busy_retry",
            format!(
                "your '{requested_tier}' request waited — the lease was held by an equal or higher tier \
                 (or a just-preempted cooldown); no place is held, so it had to retry"
            ),
        )),
        "denied" => Some((
            "denied",
            format!(
                "your request was declined — about {short_mib} MiB short of free VRAM (declining is the \
                 safe path; try a smaller estimate or retry once the GPU frees up)"
            ),
        )),
        _ => None,
    }
}

/// The async "a lease you held is gone" narration — the heartbeat saw `Renew → false` while we still
/// believed we held it. Honest-AMBIGUOUS by design: the act layer can't tell preempt from TTL-expiry
/// without daemon help (the daemon-authoritative refinement is deferred, ADR-0021 §10), so "sourced,
/// never invented" means we state both possibilities and never name who took it.
fn lease_lost_why() -> String {
    "a lease this session held is no longer held — preempted by a higher-priority request, or it expired"
        .to_string()
}

/// Session-lifetime D-Bus handle for the act verbs — built lazily on the first act call, so a
/// perceive-only session never spins up tokio or touches the bus. Multi-thread runtime so the spawned
/// `Renew` heartbeat keeps ticking on a worker thread while the main loop blocks on stdin.
struct Bus {
    rt: tokio::runtime::Runtime,
    conn: zbus::Connection,
    sessions: Arc<Mutex<SessionTable>>,
}

fn bus() -> &'static Mutex<Option<Bus>> {
    static B: OnceLock<Mutex<Option<Bus>>> = OnceLock::new();
    B.get_or_init(|| Mutex::new(None))
}

/// Ensure the act bus is connected (lazy, once) and hand back CLONES (a runtime `Handle`, the
/// `Connection`, and the session table `Arc`) so callers run their D-Bus round-trip WITHOUT holding the
/// bus lock across I/O. On first use it also spawns the `Renew` heartbeat. Err(detail) ⇒ the caller
/// emits a fail-CLOSED `unavailable` (no bus / no runtime — ADR-0021 #4).
fn act_handles() -> Result<(tokio::runtime::Handle, zbus::Connection, Arc<Mutex<SessionTable>>), String> {
    let mut guard = bus().lock().map_err(|_| "act bus lock poisoned".to_string())?;
    if guard.is_none() {
        let rt = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
            .map_err(|e| format!("tokio runtime: {e}"))?;
        let conn = rt
            .block_on(zbus::Connection::session())
            .map_err(|e| format!("session bus: {e}"))?;
        let sessions = Arc::new(Mutex::new(SessionTable::default()));
        // Spawn the heartbeat ONCE, sharing the connection + table (ADR-0021 #3). It idles until a
        // token exists; it runs on a worker thread, independent of the stdin-blocking main thread.
        rt.spawn(heartbeat(conn.clone(), Arc::clone(&sessions), heartbeat_interval()));
        guard.replace(Bus { rt, conn, sessions });
    }
    // We just ensured Some above; match (not `expect`) so a future fallible refactor can't panic the
    // whole stdin loop — the unreachable None degrades to the same fail-CLOSED `unavailable` (review).
    match guard.as_ref() {
        Some(b) => Ok((b.rt.handle().clone(), b.conn.clone(), Arc::clone(&b.sessions))),
        None => Err("act bus failed to initialize".to_string()),
    }
}

/// One `org.agentos.Coordinator1.AcquireAgent` round-trip → the typed reply tuple, or Err (bus error).
async fn call_acquire_agent(
    conn: &zbus::Connection,
    tier: &str,
    estimate_mib: u32,
) -> Result<(bool, u64, String, String, u32, u32), String> {
    let reply = conn
        .call_method(Some(COORD_BUS), COORD_PATH, Some(COORD_IFACE), "AcquireAgent", &(tier, estimate_mib))
        .await
        .map_err(|e| e.to_string())?;
    reply.body().deserialize::<(bool, u64, String, String, u32, u32)>().map_err(|e| e.to_string())
}

async fn call_release(conn: &zbus::Connection, token: u64) -> Result<bool, String> {
    let reply = conn
        .call_method(Some(COORD_BUS), COORD_PATH, Some(COORD_IFACE), "Release", &(token,))
        .await
        .map_err(|e| e.to_string())?;
    reply.body().deserialize::<bool>().map_err(|e| e.to_string())
}

async fn call_renew(conn: &zbus::Connection, token: u64) -> Result<bool, String> {
    let reply = conn
        .call_method(Some(COORD_BUS), COORD_PATH, Some(COORD_IFACE), "Renew", &(token,))
        .await
        .map_err(|e| e.to_string())?;
    reply.body().deserialize::<bool>().map_err(|e| e.to_string())
}

/// Keep this session's held lease alive (ADR-0021 #3): every `interval`, `Renew` each token the session
/// table holds. A definitive `false` (daemon no longer holds it — preempted/expired) prunes the entry so
/// we stop renewing a dead token and the next `gpu_status` reflects reality; a transient timeout/bus
/// error is left for the next tick (don't drop a still-valid belief on a blip). The renew rides the SAME
/// connection that acquired (same bus name), so the daemon's identity-bound `may_renew` always passes.
async fn heartbeat(conn: zbus::Connection, sessions: Arc<Mutex<SessionTable>>, interval: Duration) {
    loop {
        tokio::time::sleep(interval).await;
        let held = lock_sessions(&sessions).snapshot(); // brief lock, no await held
        for (session, token) in held {
            if let Ok(Ok(false)) = tokio::time::timeout(ACT_DBUS_TIMEOUT, call_renew(&conn, token)).await {
                // Authoritative: the daemon says we don't hold it. Prune (iff unchanged — a concurrent
                // release/re-acquire may have moved it). Decide under the sessions lock, DROP it before
                // touching the contention log (no nested locks — keeps the lock order acyclic).
                let lost = {
                    let mut t = lock_sessions(&sessions);
                    if t.held(&session) == Some(token) {
                        t.released(&session);
                        true
                    } else {
                        false
                    }
                };
                // ADR-0021 #10: we still believed we held this lease, so it was lost out from under us —
                // preempted or expired, NOT a self-release (a release would have cleared `held` already).
                // Record a holder-free per-caller contention with a fresh id (the agent didn't poll for
                // this; it discovers it in `gpu_why`'s list).
                if lost {
                    let mut c = lock_contention();
                    let rid = c.mint_id();
                    c.record(&session, rid, "lease_lost", lease_lost_why());
                }
            }
        }
    }
}

fn act_unavailable(detail: &str) -> Value {
    json!({"status": "unavailable", "detail": detail, "source": "org.agentos.Coordinator1"})
}

fn gpu_request(requested_tier: &str, estimate_mib: u32) -> String {
    // Mint the #10 correlation id up front so EVERY reply carries one (granted, busy_retry, denied,
    // even a fail-closed `unavailable`) — it is the agent's handle to ask `gpu_why(request_id)` later.
    let request_id = lock_contention().mint_id();
    let (handle, conn, sessions) = match act_handles() {
        Ok(h) => h,
        Err(e) => {
            let mut v = act_unavailable(&format!("act unavailable: {e} — is `agentosd lease` running?"));
            v["request_id"] = json!(request_id);
            return v.to_string();
        }
    };
    // Fail CLOSED: a timeout or any bus error ⇒ None ⇒ `unavailable`, never a fabricated grant (#4).
    let reply: Option<(bool, u64, String, String, u32, u32)> = match handle
        .block_on(async { tokio::time::timeout(ACT_DBUS_TIMEOUT, call_acquire_agent(&conn, requested_tier, estimate_mib)).await })
    {
        Ok(Ok(tuple)) => Some(tuple),
        _ => None,
    };
    // Record a genuine grant in the per-session table (layer 2) — only a consistent one (granted flag +
    // non-zero token + the `granted` code), so a malformed reply never registers a phantom hold.
    if let Some((granted, token, ref code, ..)) = reply {
        if granted && token != 0 && code == "granted" {
            lock_sessions(&sessions).acquired(LOCAL_SESSION, token);
        }
    }
    let mut v = request_json(requested_tier, reply);
    v["request_id"] = json!(request_id);
    // ADR-0021 #10: on a LOSS, record a holder-free per-caller contention so a later `gpu_why` can
    // narrate why THIS request waited without naming who held the lease. Keyed off the agent-facing
    // status (the single source of truth `request_json` already produced), never the daemon prose.
    if let Some(status) = v.get("status").and_then(Value::as_str) {
        let short = v.get("short_mib").and_then(Value::as_u64).unwrap_or(0);
        if let Some((kind, why)) = contention_why(status, requested_tier, short) {
            lock_contention().record(LOCAL_SESSION, request_id, kind, why);
        }
    }
    v.to_string()
}

fn gpu_release(token: u64) -> String {
    let (handle, conn, sessions) = match act_handles() {
        Ok(h) => h,
        Err(e) => return act_unavailable(&format!("act unavailable: {e}")).to_string(),
    };
    // LAYER 2 (ADR-0021 #6): refuse releasing a token THIS session did not acquire, BEFORE any D-Bus —
    // an in-process cross-session-release guard (the daemon's `holder_peer` is layer 1, the bus grain).
    if !lock_sessions(&sessions).owns(LOCAL_SESSION, token) {
        return json!({
            "status": "not_holder",
            "token": token,
            "detail": "this session did not acquire that token (cross-session release refused — GO-2 layer 2)",
        })
        .to_string();
    }
    let v = match handle.block_on(async { tokio::time::timeout(ACT_DBUS_TIMEOUT, call_release(&conn, token)).await }) {
        Ok(Ok(true)) => {
            lock_sessions(&sessions).released(LOCAL_SESSION);
            json!({"status": "released", "token": token})
        }
        Ok(Ok(false)) => {
            // The daemon no longer holds it (preempted / TTL-expired / already released). Drop our belief.
            lock_sessions(&sessions).released(LOCAL_SESSION);
            json!({"status": "not_holder", "token": token, "detail": "coordinator no longer holds this token (stale/expired/preempted)"})
        }
        // Timeout / bus error: keep the table entry (a retry may still release; an abandoned lease
        // TTL-expires at the daemon regardless).
        _ => act_unavailable("coordinator unreachable during release — retry; an abandoned lease TTL-expires"),
    };
    v.to_string()
}

/// Pure shaping of `gpu_request`: the daemon's typed AcquireAgent reply → the agent contract (ADR-0021
/// #2: map the CODE, never prose). `None` = unreachable/timed-out ⇒ fail CLOSED (#4). Defensive
/// consistency (determinism lens, harvested): a grant is honored ONLY if internally consistent
/// (granted flag + non-zero token + `granted` code) — a malformed reply (version skew / bus squatter)
/// must never mint a phantom grant the agent then acts on. `cooling` folds into `busy_retry` (the
/// agent-visible set is granted|busy_retry|denied per design 0020), carrying `retry_after_ms`.
fn request_json(requested_tier: &str, reply: Option<(bool, u64, String, String, u32, u32)>) -> Value {
    let Some((granted, token, code, tier_effective, short_mib, retry_after_ms)) = reply else {
        return json!({
            "status": "unavailable",
            "detail": "coordinator unreachable or timed out — NOT launching GPU work (act fails closed, ADR-0021 #4)",
            "source": "org.agentos.Coordinator1.AcquireAgent",
        });
    };
    match code.as_str() {
        "granted" if granted && token != 0 => {
            // Honest note (review: determinism NIT + RAI Low). Don't mislabel an alias/floor as a
            // preempt-clamp — just state the EFFECTIVE tier and the agent band. AND surface the #8
            // residual: a higher-priority request can preempt this lease and agentosd CANNOT reclaim
            // VRAM the agent already allocated, so the agent must self-release/offload on preempt.
            let band = if !requested_tier.is_empty() && !tier_effective.is_empty() && requested_tier != tier_effective {
                format!("effective tier '{tier_effective}' (agents are bounded to best-effort..batch). ")
            } else {
                String::new()
            };
            let note = format!(
                "{band}A higher-priority desktop/human request can PREEMPT this lease, and agentosd \
                 cannot reclaim VRAM you have already allocated — release or offload promptly if \
                 preempted. A heartbeat keeps the lease alive while this session lives; call \
                 gpu_release(token) when done."
            );
            json!({"status": "granted", "token": token, "tier_effective": tier_effective, "note": note})
        }
        "denied" => json!({
            "status": "denied",
            "short_mib": short_mib,
            "detail": "predict-before-load: the estimate + safety headroom won't fit free VRAM (declining IS the safe path — try a smaller model or retry later)",
        }),
        // C7 cooldown (`cooling`) and a held lease (`busy_retry`) both present to the agent as a
        // retryable `busy_retry` with a backoff hint — never the word "queued" (no wait-queue).
        "busy_retry" | "cooling" => json!({
            "status": "busy_retry",
            "retry_after_ms": retry_after_ms,
            "detail": "the lease is held by an equal/higher tier (or a just-preempted cooldown) — retry after retry_after_ms; no place is held",
        }),
        "error" => json!({
            "status": "error",
            "detail": "the request was rejected (bad tier name or malformed) — check the tier and estimate_mib",
            "source": "org.agentos.Coordinator1.AcquireAgent",
        }),
        // Forward-compat / inconsistency (incl. a malformed "granted" with token 0 or granted=false):
        // fail CLOSED rather than guess a grant.
        _ => json!({
            "status": "unavailable",
            "detail": format!("inconsistent or unrecognized outcome (code='{code}', granted={granted}, token={token}) — not acting on it"),
            "source": "org.agentos.Coordinator1.AcquireAgent",
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn initialize_advertises_tools_and_server() {
        let req = json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}});
        let r = handle_request(&req).unwrap();
        assert_eq!(r["protocolVersion"], PROTOCOL_VERSION);
        assert_eq!(r["serverInfo"]["name"], "agentosd-gpu");
        assert!(r["capabilities"]["tools"].is_object());
    }

    #[test]
    fn tools_list_exposes_perceive_then_the_act_verbs_with_schemas() {
        // ADR-0021: the act verbs are now LIVE (this pin flipped from asserting their absence). Perceive
        // first, then gpu_request/gpu_release with required-arg input schemas.
        let tools = tools_list();
        let arr = tools["tools"].as_array().unwrap();
        let names: Vec<String> = arr.iter().map(|t| t["name"].as_str().unwrap().to_string()).collect();
        assert_eq!(names, ["gpu_status", "gpu_residency", "gpu_why", "gpu_request", "gpu_release"]);
        // gpu_request takes (tier, estimate_mib); gpu_release takes (token) — both REQUIRED.
        let req = arr.iter().find(|t| t["name"] == "gpu_request").unwrap();
        assert_eq!(req["inputSchema"]["required"], json!(["tier", "estimate_mib"]));
        assert!(req["inputSchema"]["properties"]["tier"].is_object());
        let rel = arr.iter().find(|t| t["name"] == "gpu_release").unwrap();
        assert_eq!(rel["inputSchema"]["required"], json!(["token"]));
    }

    #[test]
    fn notifications_get_no_response() {
        // No `id`, and an `initialized` notification → handler yields nothing to send.
        let note = json!({"jsonrpc":"2.0","method":"notifications/initialized"});
        assert!(handle_request(&note).is_none());
    }

    #[test]
    fn unknown_tool_is_an_error_result_not_a_panic() {
        let req = json!({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"gpu_nuke"}});
        let r = handle_request(&req).unwrap();
        assert_eq!(r["isError"], json!(true));
        assert!(r["content"][0]["text"].as_str().unwrap().contains("unknown tool"));
    }

    #[test]
    fn tools_call_returns_mcp_content_shape() {
        // gpu_status with no keyhole.json present still returns a well-formed content block.
        let req = json!({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"gpu_status"}});
        let r = handle_request(&req).unwrap();
        assert_eq!(r["content"][0]["type"], "text");
        assert!(r["content"][0]["text"].is_string());
    }

    // --- exact-string contract pins (ADR-0020 council gap: a versioned, pinned JSON shape per
    // perceive verb across the Rust-producer ↔ MCP-consumer boundary) ---

    #[test]
    fn status_unavailable_is_a_typed_posture_never_a_free_card() {
        // The fail-open-inversion guard (ADR-0003): a missing snapshot must read as `unavailable`,
        // not as a zeroed/idle GPU. An agent keys off `status`, not absent fields.
        assert_eq!(
            status_json(None),
            json!({
                "status": "unavailable",
                "detail": "keyhole.json not found — is `agentosd keyhole` running? a missing snapshot is NOT a free GPU",
                "source": "keyhole.json",
            })
        );
    }

    #[test]
    fn status_ok_pins_the_shape_and_omits_holder_identity() {
        let k = json!({
            "vram": {"used_mib": 8000, "total_mib": 24000},
            "lease": {"tier": "batch", "holder": "comfyui-dream"},
            "residency": [{"name": "gemma3"}, {"name": "qwen2.5vl"}],
        });
        let v = status_json(Some(k));
        assert_eq!(
            v,
            json!({
                "status": "ok",
                "vram": {"free_mib": 16000, "used_mib": 8000, "total_mib": 24000},
                "lease": {"tier": "batch"},
                "resident_models": ["gemma3", "qwen2.5vl"],
                "source": "keyhole.json",
            })
        );
        // v1 must NOT leak the holder's identity — deferred to the act phase (ADR-0021).
        assert!(!v.to_string().contains("comfyui-dream"));
    }

    #[test]
    fn residency_unavailable_is_typed_not_an_empty_budget() {
        assert_eq!(
            residency_json(None),
            json!({
                "status": "unavailable",
                "detail": "no coexist plan yet — `agentosd telemetry` must run and collect first",
                "source": "coexist plan over telemetry.jsonl",
            })
        );
    }

    #[test]
    fn residency_surfaces_offloaded_models_to_the_agent() {
        // A model observed running partially on CPU must appear in `offloaded` so a perceiving agent
        // sees the spill (ADR-0018 item 6 #2); a fully-resident model contributes nothing there.
        let stat = |name: &str, off_ticks: u64, on_cpu: u64| crate::analyze::ModelStat {
            name: name.into(),
            ticks_resident: 10,
            loads: 1,
            unloads: 0,
            load_score: 1.0,
            reported_max_mib: 9000,
            real_footprint_mib: None,
            footprint_samples: 0,
            offloaded_ticks: off_ticks,
            max_offload_mib: on_cpu,
        };
        let plan = crate::analyze::Plan {
            samples: 100,
            total_mib: 24000,
            baseline_mib: 4000,
            llm_budget_mib: 19500,
            undercount: 1.4,
            models: vec![stat("big", 5, 8000), stat("small", 0, 0)],
            warm_set: vec![],
            exclusive: vec![],
            reasons: std::collections::BTreeMap::new(),
            signals: Default::default(),
            max_loaded: 1,
            keep_alive_min: 30,
            confident: true,
            warnings: vec![],
        };
        let v = residency_json(Some(plan));
        assert_eq!(v["offloaded"], json!([{"model": "big", "on_cpu_mib": 8000, "ticks": 5}]));
    }

    #[test]
    fn why_blind_is_unavailable_not_calm() {
        // No keyhole AND no telemetry == "blind". Must be distinct from a clear card.
        assert_eq!(
            why_json(None, None, &[], None),
            json!({
                "status": "unavailable",
                "detail": "can't see the GPU right now (no keyhole snapshot, no telemetry) — this is 'blind', not 'calm'",
                "source": "keyhole.json (lease.preempt) + telemetry signals",
            })
        );
    }

    #[test]
    fn why_clear_card_is_a_first_class_calm_line() {
        // Keyhole readable with an empty preempt is genuinely calm — a real sentence, not a null.
        let v = why_json(Some(json!({"lease": {"preempt": ""}})), None, &[], None);
        assert_eq!(v["status"], "ok");
        assert_eq!(v["last_contention"], "the card was clear; nothing waited on your behalf");
        assert_eq!(v["recent_activity"], "no telemetry yet");
        // With no act history, the per-caller surface is present and empty (a first-class field, not absent).
        assert_eq!(v["your_recent_contention"], json!([]));
    }

    // --- act verbs: typed-outcome mapping + fail-closed + layer-2 session isolation ---

    fn reply(granted: bool, token: u64, code: &str, tier: &str, short: u32, retry: u32) -> Option<(bool, u64, String, String, u32, u32)> {
        Some((granted, token, code.to_string(), tier.to_string(), short, retry))
    }

    #[test]
    fn request_json_maps_typed_codes_not_prose() {
        // ADR-0021 #2: the agent contract is keyed off the typed CODE, never a prose string.
        let g = request_json("batch", reply(true, 5, "granted", "batch", 0, 0));
        assert_eq!(g["status"], "granted");
        assert_eq!(g["token"], 5);
        assert_eq!(g["tier_effective"], "batch");

        // The effective tier is surfaced honestly when it differs from the requested one (no false
        // "preempt-clamp" framing for a floor/alias), and the #8 preempt/no-reclaim residual is stated.
        let clamped = request_json("interactive", reply(true, 9, "granted", "batch", 0, 0));
        assert_eq!(clamped["status"], "granted");
        let note = clamped["note"].as_str().unwrap();
        assert!(note.contains("effective tier 'batch'"));
        assert!(note.contains("PREEMPT") && note.contains("cannot reclaim"), "must surface the #8 residual");

        // busy_retry carries the backoff hint; never the word "queued".
        let busy = request_json("batch", reply(false, 0, "busy_retry", "batch", 0, 2000));
        assert_eq!(busy["status"], "busy_retry");
        assert_eq!(busy["retry_after_ms"], 2000);
        assert!(!busy.to_string().contains("queued"));

        // C7 `cooling` FOLDS into the agent-visible busy_retry, carrying the precise remaining dwell.
        let cooling = request_json("batch", reply(false, 0, "cooling", "batch", 0, 5000));
        assert_eq!(cooling["status"], "busy_retry");
        assert_eq!(cooling["retry_after_ms"], 5000);

        // denied carries the numeric shortfall.
        let denied = request_json("batch", reply(false, 0, "denied", "batch", 2048, 0));
        assert_eq!(denied["status"], "denied");
        assert_eq!(denied["short_mib"], 2048);

        // a daemon `error` code → typed error status.
        assert_eq!(request_json("nonsense", reply(false, 0, "error", "", 0, 0))["status"], "error");
    }

    #[test]
    fn request_json_fails_closed_and_never_mints_a_phantom_grant() {
        // ADR-0021 #4: a None reply (coordinator unreachable / timed out) is `unavailable` — NEVER a
        // free GPU or a grant. The agent must not launch heavy work on it.
        let none = request_json("batch", None);
        assert_eq!(none["status"], "unavailable");
        assert!(none["token"].is_null());

        // Defensive (determinism lens): a malformed grant must NOT become a phantom grant.
        assert_eq!(request_json("batch", reply(true, 0, "granted", "batch", 0, 0))["status"], "unavailable", "granted code but token 0");
        assert_eq!(request_json("batch", reply(false, 5, "granted", "batch", 0, 0))["status"], "unavailable", "granted code but granted=false");
        assert_eq!(request_json("batch", reply(true, 5, "weird_future_code", "batch", 0, 0))["status"], "unavailable", "unknown code fails closed");
        // a (true, N, "denied") inconsistency resolves to the denial, not a grant.
        assert_eq!(request_json("batch", reply(true, 5, "denied", "batch", 100, 0))["status"], "denied");
    }

    #[test]
    fn heartbeat_cadence_is_coupled_to_the_agent_ttl_by_construction() {
        // Review (resource-safety Blocker): the heartbeat must tick faster than the agent TTL, and that
        // coupling must hold BY CONSTRUCTION — not by two env vars agreeing. The interval is derived from
        // the SAME authoritative TTL the daemon uses, so ≥3 beats always fit (a live lease survives ≥2
        // consecutive missed renews). (Assumes the AGENTOSD_* env overrides are unset, as in CI/local.)
        let ttl = crate::lease::agent_lease_ttl();
        let beat = heartbeat_interval();
        assert!(beat >= Duration::from_secs(5), "floor: never busy-spin renews");
        assert!(beat * 3 <= ttl, "≥3 beats per TTL — the coupling holds by construction ({beat:?} * 3 vs {ttl:?})");
    }

    #[test]
    fn session_table_refuses_cross_session_release() {
        // ADR-0021 #6 (layer 2): two MCP sessions over ONE connection — session B must NOT be able to
        // release session A's token. The merged daemon go2 test only covers DISTINCT bus names; this
        // freezes the same-connection case the spike showed bus-name binding alone can't see. (Live
        // stdio = one session key, but the structure must enforce the guard for the multiplexed future.)
        let mut t = SessionTable::default();
        t.acquired("A", 7);
        assert!(t.owns("A", 7), "the acquiring session owns its token");
        assert!(!t.owns("B", 7), "a DIFFERENT session must not be able to release it (cross-session refused)");
        assert!(!t.owns("A", 8), "even the right session can't release a token it didn't acquire");
        assert_eq!(t.held("A"), Some(7));
        // at-most-one-per-session: a fresh acquire replaces (the lease is single-exclusive).
        t.acquired("A", 9);
        assert!(!t.owns("A", 7), "the superseded token is no longer this session's");
        assert!(t.owns("A", 9));
        // release clears ONLY that session; B is untouched.
        t.acquired("B", 11);
        t.released("A");
        assert!(!t.owns("A", 9));
        assert!(t.owns("B", 11), "releasing A does not touch B's hold");
    }

    // --- ADR-0021 #10: per-caller gpu_why (correlation id) — holder-free BY CONSTRUCTION ---

    fn ctn(request_id: u64, kind: &'static str, why: &str) -> Contention {
        Contention { request_id, kind, why: why.into() }
    }

    #[test]
    fn contention_why_is_holder_free_and_only_for_real_waits() {
        // A loss → a holder-free sentence keyed off the AGENT status (never the daemon's holder-naming
        // prose). A grant didn't wait; unavailable/error are substrate blindness, not a lease wait.
        let (kind, why) = contention_why("busy_retry", "batch", 0).unwrap();
        assert_eq!(kind, "busy_retry");
        assert!(why.contains("equal or higher tier") && why.contains("retry"));
        let (kind, why) = contention_why("denied", "batch", 2048).unwrap();
        assert_eq!(kind, "denied");
        assert!(why.contains("2048 MiB"));
        assert!(contention_why("granted", "batch", 0).is_none(), "a grant is not a contention");
        assert!(contention_why("unavailable", "batch", 0).is_none(), "blindness is not a lease wait");
        assert!(contention_why("error", "batch", 0).is_none());
    }

    #[test]
    fn lease_lost_narration_is_ambiguous_and_holder_free() {
        // The async loss is honest-AMBIGUOUS (the act layer can't tell preempt from expiry) and names no
        // winner — only the caller's own experience.
        let s = lease_lost_why();
        assert!(s.contains("preempted") && s.contains("expired"), "states both possibilities");
        assert!(!s.contains("interactive") && !s.contains("Hermes") && !s.contains("ComfyUI"));
    }

    #[test]
    fn contention_log_ring_is_bounded_per_session_and_ids_are_monotonic() {
        let mut log = ContentionLog::default();
        // ids are monotonic from 1.
        assert_eq!(log.mint_id(), 1);
        assert_eq!(log.mint_id(), 2);
        // Overfill A's ring; the oldest are evicted, the newest survive, most-recent-first.
        for i in 0..(CONTENTION_RING_CAP as u64 + 3) {
            log.record("A", 100 + i, "busy_retry", format!("wait {i}"));
        }
        let a = log.recent("A");
        assert_eq!(a.len(), CONTENTION_RING_CAP, "ring is capped");
        assert_eq!(a[0].request_id, 100 + CONTENTION_RING_CAP as u64 + 2, "most-recent first");
        assert_eq!(a.last().unwrap().request_id, 100 + 3, "oldest within the window");
        // A different session is untouched (per-caller isolation, mirrors SessionTable).
        log.record("B", 7, "denied", "b-wait".into());
        assert_eq!(log.recent("B").len(), 1);
        assert_eq!(log.recent("A").len(), CONTENTION_RING_CAP, "recording for B does not touch A");
        // An unseen session has no history.
        assert!(log.recent("C").is_empty());
    }

    #[test]
    fn why_focused_query_is_caller_only_and_never_the_system_card() {
        // ADR-0021 #10: "why did MY request wait?" answers PURELY from the caller's own contention —
        // NEVER the system-level last_contention (which can name a holder). Pin the no-leak structurally:
        // feed a keyhole whose preempt names a holder, and assert it cannot appear in the focused reply.
        let keyhole = json!({"lease": {"preempt": "batch preempted `SECRET_HOLDER` (fits)"}});
        let caller = [ctn(7, "busy_retry", "your 'batch' request waited — held by an equal or higher tier")];
        let v = why_json(Some(keyhole), None, &caller, Some(7));
        assert_eq!(v["status"], "ok");
        assert_eq!(v["request_id"], 7);
        assert_eq!(v["kind"], "busy_retry");
        assert!(v["why"].as_str().unwrap().contains("equal or higher tier"));
        // The focused reply must NOT carry the system card at all, and must not leak the holder name.
        assert!(v.get("last_contention").is_none(), "focused query never includes the system card");
        assert!(!v.to_string().contains("SECRET_HOLDER"), "focused query must never name another holder");
    }

    #[test]
    fn why_focused_query_for_unknown_id_is_an_honest_no_record_even_when_blind() {
        // No keyhole/plan at all (blind) — but the focused query is in-process, so it still answers.
        let v = why_json(None, None, &[], Some(999));
        assert_eq!(v["status"], "ok");
        assert_eq!(v["request_id"], 999);
        assert!(v["why"].as_str().unwrap().contains("no contention recorded for request 999"));
        // A foreign/guessed id is simply not found — the same no-record answer (enumeration is inert).
        let caller = [ctn(7, "denied", "short")];
        let v = why_json(None, None, &caller, Some(8));
        assert!(v["why"].as_str().unwrap().contains("no contention recorded for request 8"));
    }

    #[test]
    fn why_broad_query_surfaces_caller_contention_holder_free_alongside_the_system_card() {
        let keyhole = json!({"lease": {"preempt": "batch preempted `SECRET_HOLDER` (fits)"}});
        // `why_json` receives an already-most-recent-first slice (as `recent()` returns) and PRESERVES
        // that order — so id 9 (the later event) leads.
        let caller = [
            ctn(9, "lease_lost", &lease_lost_why()),
            ctn(7, "busy_retry", "your 'batch' request waited — held by an equal or higher tier"),
        ];
        let v = why_json(Some(keyhole), None, &caller, None);
        assert_eq!(v["status"], "ok");
        // The system card is the existing field (it MAY name a holder — out of #10's scope).
        assert!(v["last_contention"].as_str().unwrap().contains("SECRET_HOLDER"));
        // The #10 surface lists the caller's own waits, most-recent first, and is itself holder-free.
        let yours = v["your_recent_contention"].as_array().unwrap();
        assert_eq!(yours.len(), 2);
        assert_eq!(yours[0]["request_id"], 9, "most-recent first");
        assert_eq!(yours[0]["kind"], "lease_lost");
        assert_eq!(yours[1]["request_id"], 7);
        assert!(!v["your_recent_contention"].to_string().contains("SECRET_HOLDER"), "per-caller surface is holder-free");
    }

    #[test]
    fn why_broad_query_is_not_blind_when_only_caller_history_exists() {
        // System sources blind, but THIS session has its own history → ok (not unavailable), surfaced.
        let caller = [ctn(7, "denied", "your request was declined — about 1820 MiB short")];
        let v = why_json(None, None, &caller, None);
        assert_eq!(v["status"], "ok", "caller history alone is enough to answer — not 'blind'");
        assert_eq!(v["last_contention"], "no lease snapshot to read");
        assert_eq!(v["your_recent_contention"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn why_focused_query_is_session_scoped_despite_a_shared_id_counter() {
        // The correlation-id counter is process-global, but lookups are scoped to the CALLER's own ring.
        // So even though session A minted id 1, session B — querying its OWN (here empty) ring — gets the
        // honest no-record, never A's contention. Pins cross-session isolation for the multi-session future
        // (review: determinism Low + privacy Nit — the shared counter must not become a cross-session read).
        let mut log = ContentionLog::default();
        let id = log.mint_id(); // == 1, minted "by A"
        log.record("A", id, "busy_retry", "A's wait".into());
        // B shares the counter but not the ring: its focused query sees only recent("B") (empty).
        let v = why_json(None, None, &log.recent("B"), Some(id));
        assert_eq!(v["status"], "ok");
        assert!(v["why"].as_str().unwrap().contains("no contention recorded for request 1"));
        assert!(!v.to_string().contains("A's wait"), "session B must never see session A's contention");
        // A's OWN focused query does find it.
        let va = why_json(None, None, &log.recent("A"), Some(id));
        assert_eq!(va["why"], "A's wait");
    }

    #[test]
    fn gpu_why_schema_advertises_an_optional_request_id() {
        let tools = tools_list();
        let why = tools["tools"].as_array().unwrap().iter().find(|t| t["name"] == "gpu_why").unwrap();
        assert!(why["inputSchema"]["properties"]["request_id"].is_object());
        // request_id is OPTIONAL — gpu_why has no required args (the broad view takes none).
        assert!(why["inputSchema"].get("required").is_none());
    }
}

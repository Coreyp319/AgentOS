//! `mcp` — the agent-facing GPU surface (ADR-0020, Phase 1: **perceive**).
//!
//! A minimal MCP server (JSON-RPC 2.0 over stdio, newline-delimited) so a Claude Code / Hermes agent
//! can *see* GPU state and reason about fit — "is the GPU free? what's resident? will my model fit
//! warm or force a swap? why did my request wait?". Strictly READ-ONLY: it reads the files the
//! substrate already produces (`keyhole.json`, the `coexist` plan over `telemetry.jsonl`) — no NVML,
//! no D-Bus, no network listener (stdio transport, local-only). The **act** verbs (`gpu_request`/
//! `gpu_release`) are deliberately NOT here yet — ADR-0020 §3 ships perceive first and alone, with
//! the intent surface gated behind the determinism + privacy review.
//!
//! Tools: `gpu_status` (live VRAM + lease tier + resident models), `gpu_residency` (warm-pool/
//! heavy-lane + each model's learned admission footprint), `gpu_why` (plain-language last-contention,
//! sourced from telemetry — never invented, the ADR-0020 §Consequences honesty rule).
//!
//! Every tool returns a typed `status` ("ok" | "unavailable") so an agent can NEVER read a dead
//! substrate as a free GPU (the ADR-0003 fail-open-inversion trap). v1 reports the lease *tier* but
//! not the holder's identity — naming who holds the card needs the act-phase identity plumbing
//! (ADR-0021), so it is deferred to v2.

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
            "description": "Plain-language explanation of the last GPU contention (wait/preempt), \
                            sourced from telemetry — never invented. Use to explain a slow/queued run.",
            "inputSchema": empty,
        },
    ]})
}

fn tools_call(req: &Value) -> Value {
    let name = req.pointer("/params/name").and_then(Value::as_str).unwrap_or("");
    let (text, is_error) = match name {
        "gpu_status" => (gpu_status(), false),
        "gpu_residency" => (gpu_residency(), false),
        "gpu_why" => (gpu_why(), false),
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

fn gpu_why() -> String {
    why_json(read_keyhole(), crate::analyze::load_plan()).to_string()
}

/// Pure shaping of `gpu_why`. Sourced from the keyhole's `lease.preempt` narration (ADR-0012) + the
/// coexist signals — NEVER generated. The honesty rule has three distinct states, not two:
///   * both sources unreadable ⇒ `unavailable` ("blind", explicitly not "calm");
///   * sources readable, empty preempt ⇒ the calm line ("the card was clear …") — a first-class
///     honest answer, never a bare null;
///   * a real preempt ⇒ its recorded narration, verbatim.
fn why_json(keyhole: Option<Value>, plan: Option<crate::analyze::Plan>) -> Value {
    if keyhole.is_none() && plan.is_none() {
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
    json!({
        "status": "ok",
        "last_contention": last_contention,
        "recent_activity": recent_activity,
        "source": "keyhole.json (lease.preempt) + telemetry signals — sourced, not generated",
    })
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
    fn tools_list_has_the_three_perceive_tools_and_no_act_verbs() {
        let names: Vec<String> = tools_list()["tools"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t["name"].as_str().unwrap().to_string())
            .collect();
        assert_eq!(names, ["gpu_status", "gpu_residency", "gpu_why"]);
        // The act verbs must NOT be exposed in Phase 1 (ADR-0020 §3).
        assert!(!names.iter().any(|n| n == "gpu_request" || n == "gpu_release"));
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
            why_json(None, None),
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
        let v = why_json(Some(json!({"lease": {"preempt": ""}})), None);
        assert_eq!(v["status"], "ok");
        assert_eq!(v["last_contention"], "the card was clear; nothing waited on your behalf");
        assert_eq!(v["recent_activity"], "no telemetry yet");
    }
}

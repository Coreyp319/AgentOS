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
//! Tools: `gpu_status` (live VRAM + lease + resident models), `gpu_residency` (warm-pool/heavy-lane
//! + each model's learned admission footprint), `gpu_why` (plain-language last-contention, sourced
//! from telemetry — never invented, the ADR-0020 §Consequences honesty rule).

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
    let Some(k) = read_keyhole() else {
        return json!({"error": "keyhole.json unavailable — is `agentosd keyhole` running?"}).to_string();
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
        "vram": {"free_mib": free, "used_mib": used, "total_mib": total},  // -1 == unknown
        "lease": {
            "tier": k.pointer("/lease/tier").and_then(Value::as_str).unwrap_or(""),
            "holder": k.pointer("/lease/holder").and_then(Value::as_str).unwrap_or(""),
        },
        "resident_models": resident,
        "source": "keyhole.json",
    })
    .to_string()
}

fn gpu_residency() -> String {
    let Some(plan) = crate::analyze::load_plan() else {
        return json!({"error": "no telemetry yet — `agentosd telemetry` must run and collect first"})
            .to_string();
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
            .map(|n| {
                json!({"model": n, "admit_mib": admit(n), "reason": plan.reasons.get(n)})
            })
            .collect()
    };
    json!({
        "budget_mib": plan.llm_budget_mib,
        "baseline_mib": plan.baseline_mib,
        "total_mib": plan.total_mib,
        "undercount": plan.undercount,
        "confident": plan.confident,
        "warm_pool": lane(&plan.warm_set),
        "heavy_lane": lane(&plan.exclusive),
        "signals": {
            "max_concurrent": plan.signals.max_concurrent,
            "coexist_ticks": plan.signals.coexist_ticks,
            "avoided_swaps": plan.signals.avoided_swaps,
            "min_free_mib": plan.signals.min_free_mib,
            "oom_near_ticks": plan.signals.oom_near_ticks,
            "oom_danger_ticks": plan.signals.oom_danger_ticks,
        },
        "note": "admit_mib is the learned, size_vram-floored reservation; a model fits warm only if it \
                 is in warm_pool. Phase-3 eviction is not yet active.",
    })
    .to_string()
}

fn gpu_why() -> String {
    // Sourced from the keyhole's lease.preempt narration (ADR-0012) + the coexist verdict — never
    // generated. Empty preempt is the honest "no contention", not a guess.
    let preempt = read_keyhole()
        .and_then(|k| k.pointer("/lease/preempt").and_then(Value::as_str).map(str::to_string))
        .filter(|s| !s.is_empty());
    let recent = crate::analyze::load_plan().map(|p| {
        let s = &p.signals;
        if s.avoided_swaps > 0 {
            format!("{} warm-poolable reloads in the window (a bigger warm pool / longer keep-alive would avoid them)", s.avoided_swaps)
        } else if s.oom_danger_ticks > 0 {
            format!("free VRAM fell below the safety floor on {} ticks", s.oom_danger_ticks)
        } else {
            "no contention recorded in the window".to_string()
        }
    });
    json!({
        "last_contention": preempt.unwrap_or_else(|| "none recorded".into()),
        "recent_activity": recent.unwrap_or_else(|| "no telemetry yet".into()),
        "source": "keyhole.json (lease.preempt) + telemetry signals — sourced, not generated",
    })
    .to_string()
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
}

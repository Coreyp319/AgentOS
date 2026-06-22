//! `rc_throttle` — the UE wallpaper THROTTLE Remote Control sink (ADR-0029 §3, the actuation side of
//! the `Tier::Yielding` rung ladder). A SEPARATE sink from `rc.rs` (the MOOD pusher) BY DESIGN: §B
//! mandates the throttle channel keep its own, NARROWER security posture rather than ride the mood
//! channel. Where the mood sink calls the engine material library's `SetScalarParameterValue` (a
//! cosmetic scalar), this sink calls EXACTLY ONE project UFUNCTION —
//! `UAgentOSThrottleLibrary::ApplyRung(int32 Rung)` — passing a RUNG INDEX (0/1/2), never a cvar name
//! and never `ExecuteConsoleCommand`. UE maps the index to its fixed cvar set INTERNALLY (the C++
//! mirror of `governor::Rung::cvars()`), so no arbitrary console command crosses the wire even if the
//! RC allowlist were widened by mistake.
//!
//! The loopback-literal guard + redirect-none + hard 250 ms timeout are mirrored from `rc.rs` and kept
//! as this module's OWN copy on purpose: a separate sink owns its own copy of the §B security floor, so
//! a future edit to one channel can never silently weaken the other.
//!
//! Scope: this is the TRANSPORT only (build step B3). It is consumed by the lease preempt/restore path
//! (B4); until that lands and the UE-side `ApplyRung` UFUNCTION is compiled (B2), nothing calls
//! `apply_rung` — a dream cannot throttle a wallpaper that is not yet registered as throttleable.

use crate::governor::Rung;
use serde::Serialize;
use std::time::Duration;

/// UE Remote Control generic-call endpoint — loopback-literal, fixed to UE's default RC port, NOT
/// env-overridable (an IP literal so there is no DNS and no host knob to repoint it off the box, §B).
/// The throttle sink and the mood sink share UE's one RC server on `:30010`.
const RC_URL: &str = "http://127.0.0.1:30010/remote/object/call";

/// The CDO of the project function library carrying the throttle UFUNCTION (authored by B2 in the UE
/// project's C++ module `AgentOSBlank`). A `UBlueprintFunctionLibrary` static needs no world context —
/// it drives the global `IConsoleManager` directly — so, unlike the mood sink, no `WorldContextObject`
/// is sent. Fixed by construction (no env host knob, §B); the test path injects its own object path.
const THROTTLE_CDO: &str = "/Script/AgentOSBlank.Default__AgentOSThrottleLibrary";

/// The ONE allowlisted throttle verb (§B). It takes a rung INDEX and applies a fixed cvar set inside
/// UE — never a cvar name on the wire, never `ExecuteConsoleCommand`.
const APPLY_RUNG_FN: &str = "ApplyRung";

/// Hard per-PUT timeout (mirrors `rc.rs` `PUT_TIMEOUT`). This sink fires on the LEASE ARBITRATION path,
/// so a connected-but-silent RC socket must never wedge it — the bound matters even more here than for
/// the mood loop.
const PUT_TIMEOUT: Duration = Duration::from_millis(250);

/// `parameters` of the `ApplyRung` call — just the rung index. The key MUST match the UFUNCTION's
/// `UPARAM` name (`Rung`).
#[derive(Serialize)]
struct ApplyRungParams {
    #[serde(rename = "Rung")]
    rung: i32,
}

/// The `PUT /remote/object/call` body for one `ApplyRung(rung)` call.
#[derive(Serialize)]
struct ApplyRungBody {
    #[serde(rename = "objectPath")]
    object_path: &'static str,
    #[serde(rename = "functionName")]
    function_name: &'static str,
    parameters: ApplyRungParams,
    #[serde(rename = "generateTransaction")]
    generate_transaction: bool,
}

impl ApplyRungBody {
    fn for_rung(object_path: &'static str, rung: Rung) -> ApplyRungBody {
        ApplyRungBody {
            object_path,
            function_name: APPLY_RUNG_FN,
            parameters: ApplyRungParams { rung: rung.index() },
            generate_transaction: false,
        }
    }
}

/// The outcome of one `ApplyRung` PUT. The lease path (B4) treats anything but `Applied` as "the
/// throttle did NOT take" and MUST NOT then admit a gen against VRAM the throttle was supposed to free
/// (the cardinal-sin guard, §B) — so the honest outcome is surfaced here rather than swallowed.
/// Distinguishes a *connection* failure (UE down / mid-relaunch → `Unreachable`) from a *rejection*
/// (UE up but refused the call → a wire/allowlist problem → `Rejected`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ThrottleOutcome {
    Applied,
    Rejected,
    Unreachable,
}

impl ThrottleOutcome {
    /// Did the throttle actually take? Only `Applied` frees the VRAM the coexistence admit relies on.
    pub fn took(self) -> bool {
        matches!(self, ThrottleOutcome::Applied)
    }
}

/// Build the hardened blocking client (mirrors `rc.rs::build_client`): a hard timeout so a stalled
/// socket can't wedge the arbitration path, and `redirect::Policy::none()` so a 3xx is returned as-is
/// (status not success ⇒ `Rejected`) and NEVER followed off the loopback target.
fn build_client() -> reqwest::Result<reqwest::blocking::Client> {
    reqwest::blocking::Client::builder()
        .timeout(PUT_TIMEOUT)
        .redirect(reqwest::redirect::Policy::none())
        .build()
}

/// Does `url`'s host parse to a loopback IP LITERAL? Accepts only `127.0.0.0/8` and `[::1]`-style
/// literals — a NAME (even `localhost`) is refused (no resolver step), honoring §B "IP literal, no
/// DNS". Duplicated from `rc.rs` ON PURPOSE so this separate sink owns its own copy of the floor.
fn url_is_loopback(url: &str) -> bool {
    let after_scheme = url.split("://").nth(1).unwrap_or(url);
    let authority = after_scheme.split('/').next().unwrap_or("");
    let authority = authority.rsplit_once('@').map(|(_, h)| h).unwrap_or(authority);
    let host = if let Some(rest) = authority.strip_prefix('[') {
        rest.split(']').next().unwrap_or(rest)
    } else {
        authority.rsplit_once(':').map(|(h, _)| h).unwrap_or(authority)
    };
    host.parse::<std::net::IpAddr>().map(|ip| ip.is_loopback()).unwrap_or(false)
}

/// PUT `ApplyRung(rung)` to `url` against `object_path` (both injected so a test can target a loopback
/// mock). The loopback guard is a HARD, release-present refusal (not a `debug_assert`): even though the
/// public entry only ever passes the validated const `RC_URL`, a non-loopback `url` here is refused by
/// construction, so a future caller that wires a non-const URL cannot lose the guarantee.
fn put_apply_rung(
    client: &reqwest::blocking::Client,
    url: &str,
    object_path: &'static str,
    rung: Rung,
) -> ThrottleOutcome {
    if !url_is_loopback(url) {
        return ThrottleOutcome::Rejected; // never put a body onto a non-loopback target (§B)
    }
    let body = ApplyRungBody::for_rung(object_path, rung);
    match client.put(url).json(&body).send() {
        Ok(resp) if resp.status().is_success() => ThrottleOutcome::Applied,
        Ok(_) => ThrottleOutcome::Rejected,
        Err(_) => ThrottleOutcome::Unreachable,
    }
}

/// Apply a throttle `rung` to the live UE wallpaper over Remote Control — the PUBLIC entry the lease
/// preempt/restore path (B4) calls. Drives the const loopback `RC_URL` against the project throttle
/// CDO, building the hardened client per-call (cheap: this fires on a lease arbitration event, never in
/// a hot loop). Returns the honest `ThrottleOutcome` so the caller can refuse to admit a gen when the
/// throttle did not actually take (the cardinal-sin guard).
pub fn apply_rung(rung: Rung) -> ThrottleOutcome {
    let client = match build_client() {
        Ok(c) => c,
        Err(_) => return ThrottleOutcome::Unreachable,
    };
    put_apply_rung(&client, RC_URL, THROTTLE_CDO, rung)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn const_rc_url_is_loopback_literal() {
        // The shipped target must pass its own guard — the tripwire if RC_URL is ever edited to a name
        // or a routable address.
        assert!(url_is_loopback(RC_URL));
    }

    #[test]
    fn loopback_guard_accepts_only_ip_literals() {
        assert!(url_is_loopback("http://127.0.0.1:30010/remote/object/call"));
        assert!(url_is_loopback("http://127.0.0.5:30010/x"));
        assert!(url_is_loopback("http://[::1]:30010/x"));
        // A NAME — even localhost — is refused (no resolver step; a tampered hosts file is not a guarantee).
        assert!(!url_is_loopback("http://localhost:30010/x"));
        // A routable address is refused.
        assert!(!url_is_loopback("http://10.0.0.1:30010/x"));
        assert!(!url_is_loopback("http://192.168.1.4:30010/x"));
        // A `user@` authority must not smuggle a loopback host past a routable one.
        assert!(!url_is_loopback("http://127.0.0.1@evil.example:30010/x"));
    }

    #[test]
    fn non_loopback_target_is_refused_without_sending() {
        // Fail-closed: a non-loopback url returns Rejected BEFORE any body is sent (no network needed).
        let client = build_client().unwrap();
        assert_eq!(
            put_apply_rung(&client, "http://192.168.1.50:30010/remote/object/call", THROTTLE_CDO, Rung::Floor),
            ThrottleOutcome::Rejected
        );
    }

    #[test]
    fn body_is_apply_rung_with_the_index_only() {
        // The wire contract: objectPath = the throttle CDO, functionName = ApplyRung, parameters.Rung =
        // the rung INDEX (never a cvar name), no transaction. A rung index — not a cvar — crosses the wire.
        let v = serde_json::to_value(ApplyRungBody::for_rung(THROTTLE_CDO, Rung::Floor)).unwrap();
        assert_eq!(v["objectPath"], THROTTLE_CDO);
        assert_eq!(v["functionName"], "ApplyRung");
        assert_eq!(v["parameters"]["Rung"], 2); // Floor
        assert_eq!(v["generateTransaction"], false);
        // The body carries ONLY the rung index — no cvar name, no console command field.
        assert!(v["parameters"].get("Rung").is_some());
        assert_eq!(v["parameters"].as_object().unwrap().len(), 1);
    }

    #[test]
    fn rung_index_is_the_stable_wire_contract() {
        assert_eq!(Rung::Full.index(), 0);
        assert_eq!(Rung::Reduced.index(), 1);
        assert_eq!(Rung::Floor.index(), 2);
        // restore-to-full and throttle-to-floor are the two the lease path uses; both round-trip.
        assert_eq!(serde_json::to_value(ApplyRungBody::for_rung(THROTTLE_CDO, Rung::Full)).unwrap()["parameters"]["Rung"], 0);
    }

    #[test]
    fn outcome_took_only_on_applied() {
        assert!(ThrottleOutcome::Applied.took());
        assert!(!ThrottleOutcome::Rejected.took());
        assert!(!ThrottleOutcome::Unreachable.took());
    }
}

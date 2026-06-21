//! `governor` — the UE wallpaper throttle governor (ADR-0029 §3, the `Tier::Yielding` actuation side).
//!
//! When a higher-tier lease request preempts the live UE 5.8 dark-ride wallpaper, the coordinator
//! prefers a NON-DESTRUCTIVE throttle (shrink UE to its render floor over UE Remote Control) to the
//! SIGKILL a batch holder gets — a wallpaper must never go to black (ADR-0029 D1). `coord::yield_decision`
//! makes the throttle-vs-kill call (pure, two-number footprint, D4); THIS module is the actuation
//! plan: the fixed throttle ladder (the `spikes/ue-probe/cvar_ladder.md` rungs), the SECURITY-CRITICAL
//! cvar allowlist that fences the Remote Control channel, and the action the governor takes per decision.
//!
//! **Load-bearing safety (ADR-0029 D1 / ADR-0030 D1).** The throttle rides UE Remote Control
//! (`:30010`), an unauthenticated local-code-exec surface. The unit of authorization is a WHOLE
//! `Rung`: the (gated) RC client sends `rung.cvars()` atomically and NEVER caller-supplied pairs, so a
//! gen can never compose an incoherent state from cvars drawn across different rungs (e.g. Full's
//! `r.ScreenPercentage 100` beside Floor's Lumen-off — a half-throttle that defeats the yield). The
//! generic `ExecuteConsoleCommand` endpoint stays disabled, so an arbitrary console command never
//! reaches the wire. `is_allowed_in_rung` is the per-pair DEFENSE-IN-DEPTH check, bound to the rung
//! being applied — a value from another rung (`t.MaxFPS 30` while driving Floor) is refused, not just
//! an off-ladder value. The hard invariant from ADR-0029 §3 also holds structurally here: the governor
//! can only ASK UE to shrink — the LEASE owns the SIGKILL. This module performs no kill; it returns
//! `GovernorAction::Kill` for the lease to enact (the lease already owns UE's PID as a `Spawn` profile).
//!
//! **What is built here vs. GATED.** Built + unit-tested without a live engine: the ladder, the
//! allowlist gate, and the decision→action plan. GATED (the next slice): the Remote Control TRANSPORT
//! — the loopback-bind-asserted, DNS-rebinding-aware HTTP client that actually sends a rung — because
//! the `:30010` lockdown is a `security-reviewer` prerequisite (ADR-0029 Open §B) and there is no UE
//! wallpaper to talk to off this box. The lease-side COEXISTENCE model (keep UE resident at floor
//! while a gen holds the lease) is likewise unbuilt; today a `Yielding` victim still takes the SIGKILL
//! backstop. So this module is the pure half the rest is built against.

use crate::coord::YieldOutcome;

/// A rung on the throttle ladder (`cvar_ladder.md`, verified against this UE 5.8 install's
/// `BaseScalability.ini`). `Full` = no throttle (the authored baseline); `Reduced` = "yield a slice,
/// keep the look" (Lumen at High, 70% screen, 30 fps); `Floor` = "minimum heartbeat, hand the GPU
/// over" — Lumen GI+Reflections OFF, streaming pool hard-capped, 5 fps (the real VRAM-yield rung).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rung {
    /// No throttle — the restore baseline (UE returns here when the gen releases the lease). Only the
    /// gated RC restore path constructs it, so it is unreferenced today.
    #[allow(dead_code)]
    Full,
    /// "Yield a slice, keep the look" — the middle rung reserved for a future graduated-pressure
    /// controller (the preempt path goes straight to `Floor`), so it is unreferenced today.
    #[allow(dead_code)]
    Reduced,
    Floor,
}

impl Rung {
    /// The FIXED cvar set for this rung — the ONLY `(cvar, value)` pairs the governor may send over
    /// Remote Control (ADR-0029 D1 allowlist), sent ATOMICALLY as the unit of authorization. Verbatim
    /// from `cvar_ladder.md`. Widening the allowlist means adding a rung cvar HERE; there is no path to
    /// an arbitrary console command.
    /// (Unreferenced until the gated RC transport lands — the wire that consumes it, ADR-0029 §B.)
    #[allow(dead_code)]
    pub fn cvars(self) -> &'static [(&'static str, &'static str)] {
        match self {
            Rung::Full => &[
                ("r.ScreenPercentage", "100"),
                ("sg.ViewDistanceQuality", "3"),
                ("sg.AntiAliasingQuality", "3"),
                ("sg.ShadowQuality", "3"),
                ("sg.GlobalIlluminationQuality", "3"),
                ("sg.ReflectionQuality", "3"),
                ("sg.PostProcessQuality", "3"),
                ("sg.TextureQuality", "3"),
                ("sg.EffectsQuality", "3"),
                ("sg.FoliageQuality", "3"),
                ("sg.ShadingQuality", "3"),
            ],
            Rung::Reduced => &[
                ("r.ScreenPercentage", "70"),
                ("sg.GlobalIlluminationQuality", "2"),
                ("sg.ShadowQuality", "2"),
                ("sg.ReflectionQuality", "2"),
                ("t.MaxFPS", "30"),
            ],
            Rung::Floor => &[
                ("r.ScreenPercentage", "50"),
                ("sg.GlobalIlluminationQuality", "0"),
                ("sg.ShadowQuality", "0"),
                ("sg.ReflectionQuality", "0"),
                ("sg.PostProcessQuality", "0"),
                ("r.Streaming.PoolSize", "512"),
                ("r.Streaming.LimitPoolSizeToVRAM", "1"),
                ("t.MaxFPS", "5"),
            ],
        }
    }

    /// Canonical name for logs (matches the `cvar_ladder.md` rung headings).
    pub fn as_str(self) -> &'static str {
        match self {
            Rung::Full => "FULL",
            Rung::Reduced => "REDUCED",
            Rung::Floor => "FLOOR",
        }
    }
}

/// The security gate (ADR-0029 D1), RUNG-SCOPED defense-in-depth: is `(name, value)` a member of
/// `rung` SPECIFICALLY? The unit of authorization is a whole `Rung` (the RC client sends
/// `rung.cvars()` atomically), so this per-pair check is bound to the rung being applied — a pair from
/// ANOTHER rung (e.g. `t.MaxFPS 30` while driving `Floor`, whose cap is `5`), a typo, an injected
/// cvar, or a generic console command is refused. This is strictly tighter than a union-of-all-rungs
/// check, which would let a caller compose an incoherent half-throttle from pairs across rungs.
/// (Unreferenced until the gated RC transport lands — the wire that calls it, ADR-0029 §B.)
#[allow(dead_code)]
pub fn is_allowed_in_rung(rung: Rung, name: &str, value: &str) -> bool {
    rung.cvars().iter().any(|(n, v)| *n == name && *v == value)
}

/// What the governor does to preempt the UE wallpaper (ADR-0029 §3). The governor only ever ASKS UE
/// to shrink (`Throttle`); `Kill` is a request handed back to the LEASE (which owns UE's PID), never
/// an act this module performs — the hard invariant from D3 in the type.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GovernorAction {
    /// Send this rung over Remote Control — UE coexists at a cheaper render level.
    Throttle(Rung),
    /// The throttle could not free enough; hand back to the lease to SIGKILL the owned UE PID and
    /// relaunch the procedural shader floor (ADR-0004 / ADR-0029 D1). The governor never kills.
    Kill,
}

impl GovernorAction {
    /// A one-line narration for the daemon log / keyhole arbitration line.
    pub fn describe(self) -> String {
        match self {
            GovernorAction::Throttle(r) => format!("throttle UE → {} rung (RC, non-destructive)", r.as_str()),
            GovernorAction::Kill => "SIGKILL UE → relaunch shader floor (throttle insufficient)".to_string(),
        }
    }
}

/// Map a pure throttle-vs-kill decision (`coord::yield_decision`) to the governor's action. A
/// coexisting throttle shrinks UE all the way to `Floor` — the proactive "hand the GPU over" rung,
/// the cheapest-for-the-incoming-gen state (`Reduced` is reserved for a future graduated-pressure
/// controller, not the preempt path). Pure.
pub fn plan_preemption(outcome: YieldOutcome) -> GovernorAction {
    match outcome {
        YieldOutcome::ThrottleAndCoexist => GovernorAction::Throttle(Rung::Floor),
        YieldOutcome::KillToShaderFloor => GovernorAction::Kill,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_rung_admits_exactly_its_own_pairs() {
        // Every pair the governor emits for a rung is allowed for THAT rung, by construction.
        for rung in [Rung::Full, Rung::Reduced, Rung::Floor] {
            for (n, v) in rung.cvars() {
                assert!(is_allowed_in_rung(rung, n, v), "{} pair {n}={v} must be allowed in its rung", rung.as_str());
            }
        }
    }

    #[test]
    fn the_rung_gate_refuses_off_ladder_and_cross_rung_pairs() {
        // The generic console-exec hole (ADR-0029 §B / D1): an arbitrary command is NOT a ladder cvar.
        assert!(!is_allowed_in_rung(Rung::Floor, "ExecuteConsoleCommand", "quit"));
        assert!(!is_allowed_in_rung(Rung::Floor, "r.SetNearClipPlane", "0")); // a real cvar, not on the ladder
        // A ladder cvar with an OFF-ladder value is refused — value-scoped, so a gen can't smuggle
        // `t.MaxFPS 999` (un-throttle) or `r.Streaming.PoolSize 99999` (un-cap) into the Floor rung.
        assert!(!is_allowed_in_rung(Rung::Floor, "t.MaxFPS", "999"));
        assert!(!is_allowed_in_rung(Rung::Floor, "r.Streaming.PoolSize", "99999"));
        // The security fix the panel flagged: a CROSS-rung pair is refused even though it IS on some
        // other rung — driving Floor cannot smuggle Full's full-res pixels or Reduced's 30fps cap, so
        // no caller can compose an incoherent half-throttle from pairs across rungs.
        assert!(is_allowed_in_rung(Rung::Reduced, "t.MaxFPS", "30")); // legit FOR Reduced...
        assert!(!is_allowed_in_rung(Rung::Floor, "t.MaxFPS", "30"), "Reduced's 30fps cap is NOT a Floor pair");
        assert!(is_allowed_in_rung(Rung::Full, "r.ScreenPercentage", "100")); // legit FOR Full...
        assert!(!is_allowed_in_rung(Rung::Floor, "r.ScreenPercentage", "100"), "Full's full-res is NOT a Floor pair");
        // ...and Floor's own values ARE allowed for Floor (sanity that it's the right rung, not name-only).
        assert!(is_allowed_in_rung(Rung::Floor, "t.MaxFPS", "5"));
        assert!(is_allowed_in_rung(Rung::Floor, "r.ScreenPercentage", "50"));
    }

    #[test]
    fn every_ladder_value_is_injection_safe() {
        // Cheap insurance (security panel): when the gated RC client formats these into a payload, a
        // value with a space/comma/quote/semicolon/newline could chain a second command. Pin that every
        // ladder name AND value is a plain `[A-Za-z0-9._-]+` token, so a future rung author can't add an
        // injection-shaped value without this test going red.
        let ok = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'));
        for rung in [Rung::Full, Rung::Reduced, Rung::Floor] {
            for (n, v) in rung.cvars() {
                assert!(ok(n), "{} cvar name `{n}` is not an injection-safe token", rung.as_str());
                assert!(ok(v), "{} cvar value `{v}` is not an injection-safe token", rung.as_str());
            }
        }
    }

    #[test]
    fn the_floor_rung_actually_yields_vram_not_just_gpu_time() {
        // The FLOOR rung must carry the two genuine VRAM levers (cvar_ladder.md "what cuts VRAM"):
        // Lumen GI off + the streaming pool hard-capped-to-VRAM. A floor that only capped FPS would
        // free GPU-time but not the VRAM a co-resident gen needs.
        let floor = Rung::Floor.cvars();
        assert!(floor.contains(&("sg.GlobalIlluminationQuality", "0")), "FLOOR must disable Lumen GI");
        assert!(floor.contains(&("r.Streaming.PoolSize", "512")), "FLOOR must hard-cap the streaming pool");
        assert!(floor.contains(&("r.Streaming.LimitPoolSizeToVRAM", "1")), "FLOOR must forbid the pool growing past VRAM");
    }

    #[test]
    fn plan_preemption_throttles_to_floor_on_coexist_and_kills_otherwise() {
        // A coexisting throttle goes all the way to FLOOR (the proactive hand-over), never a half-rung.
        assert_eq!(
            plan_preemption(YieldOutcome::ThrottleAndCoexist),
            GovernorAction::Throttle(Rung::Floor)
        );
        // And an insufficient throttle defers to the lease's SIGKILL backstop — the governor never kills.
        assert_eq!(plan_preemption(YieldOutcome::KillToShaderFloor), GovernorAction::Kill);
    }

    #[test]
    fn the_governor_action_describe_is_honest_about_what_happens() {
        assert!(GovernorAction::Throttle(Rung::Floor).describe().contains("FLOOR"));
        assert!(GovernorAction::Throttle(Rung::Floor).describe().contains("non-destructive"));
        assert!(GovernorAction::Kill.describe().contains("SIGKILL"));
    }
}

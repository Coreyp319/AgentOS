# UE wallpaper throttle — the actuation module (ADR-0029 §3, build step B2)

When a dream needs VRAM the live UE wallpaper is holding, the coordinator **throttles UE to a cheap
"floor" render rung** (Lumen GI/reflections off, 5 fps, streaming pool capped) so the wallpaper **stays
on screen** while the dream renders, then **restores** it on release. This is the UE side of that — the
one verb the daemon calls over Remote Control.

## Why a C++ module (and not console commands over RC)

The §B RC lockdown (`Config/DefaultRemoteControl.ini`) sets `bAllowConsoleCommandRemoteExecution=False`,
so the daemon **cannot** push `r.ScreenPercentage 50` etc. over the wire. The sanctioned path is a thin
allowlisted UFUNCTION — `UAgentOSThrottleLibrary::ApplyRung(int Rung)` — that takes a **rung index**
(0=Full, 1=Reduced, 2=Floor) and applies the fixed cvar set for that rung **inside the engine** via
`IConsoleManager`. So a rung *index*, never a cvar name and never a console command, crosses the wire —
the allowlist holds one safe verb instead of opening generic console exec.

The rung → cvar mapping **mirrors `crates/agentosd/src/governor.rs::Rung::cvars()`** — that Rust enum is
the single source of truth (pinned by `governor`'s tests). Keep the two in lockstep.

## The pieces

- `Source/AgentOSBlank/AgentOSThrottleLibrary.{h,cpp}` — the `ApplyRung` UFUNCTION (the actuator).
- `Source/AgentOSBlank/AgentOSBlank.{Build.cs,cpp}` + `Source/AgentOSBlank{,Editor}.Target.cs` — the
  minimal C++ game module scaffolding the (previously blueprint-only) project needs to compile it.
- `install_throttle_module.sh` — copies the module into `~/UnrealProjects/AgentOSBlank/Source/`, patches
  the `.uproject` Modules entry and the RC allowlist (both backed up + idempotent), and prints/runs the
  UBT build command.

The Rust caller is `crates/agentosd/src/rc_throttle.rs` (B3) — it PUTs `ApplyRung(idx)` to
`/Script/AgentOSBlank.Default__AgentOSThrottleLibrary` on UE's loopback RC server (`:30010`).

## On-box steps (the gate — can't run here)

1. `./install_throttle_module.sh --build` — install + compile the editor target.
   - **Expected to compile against the Installed engine** (per the project-C++-compiles note); only
     *engine* patches (the layer-shell `LinuxWindow.cpp`) need a source build. If UBT errors with a
     `RulesError` about the installed engine, that's the one gate this can't clear — escalate to a
     source build.
2. Re-cook / re-package the `-game` wallpaper build so the module **and** the RC allowlist entry ship.
3. `../verify_rc_lockdown.sh` — confirm the three §B gates still hold (console-exec still rejected,
   python-over-RC still off, only the allowlist resolves) now that a second verb is allowlisted.
4. Live-call check (UE running): `PUT /remote/object/call {objectPath:".../Default__AgentOSThrottleLibrary",
   functionName:"ApplyRung", parameters:{Rung:2}}` → the wallpaper visibly drops to the Floor rung;
   `Rung:0` restores it. Measure the VRAM delta (full→floor) to confirm `ue_floor_mib()` (default 1000).

Until steps 1–4 pass on the box, the daemon-side throttle (B4) stays gated — `rc_throttle::apply_rung`
exists but nothing calls it, so the live lease behaviour is unchanged.

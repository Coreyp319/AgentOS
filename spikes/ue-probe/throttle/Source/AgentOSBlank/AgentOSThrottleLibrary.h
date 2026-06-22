// AgentOSThrottleLibrary — ADR-0029 §3 throttle actuation, UE side (build step B2).
//
// `ApplyRung(int)` is the ONE Remote-Control-allowlisted throttle verb. It takes a RUNG INDEX
// (0 = Full/restore, 1 = Reduced, 2 = Floor) and applies the FIXED cvar set for that rung via
// IConsoleManager INSIDE the engine — so no cvar NAME and no console COMMAND ever crosses the RC wire,
// and `bAllowConsoleCommandRemoteExecution` stays False (the ADR-0029 §B lockdown). This is exactly the
// "thin allowlisted UFUNCTION that maps an index to a cvar set internally" the ADR specifies, so the RC
// allowlist can hold one safe verb instead of opening generic console exec.
//
// The rung -> cvar mapping MIRRORS crates/agentosd/src/governor.rs::Rung::cvars() — that Rust enum is
// the SINGLE SOURCE OF TRUTH; keep this file in lockstep with it (governor_test pins the Rust side).
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "AgentOSThrottleLibrary.generated.h"

UCLASS()
class UAgentOSThrottleLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	// Apply throttle rung `Rung` (0 = Full/restore, 1 = Reduced, 2 = Floor) to the live wallpaper.
	// STATIC + BlueprintCallable so Remote Control resolves it on the class CDO with NO world context
	// (IConsoleManager is global). An out-of-range index is a NO-OP (never apply a partial/unknown
	// throttle — the whole rung is the unit of authorization, matching governor::is_allowed_in_rung).
	UFUNCTION(BlueprintCallable, Category = "AgentOS|Throttle")
	static void ApplyRung(int32 Rung);
};

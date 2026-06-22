// AgentOSThrottleLibrary — see the header. The rung cvar sets MIRROR governor.rs::Rung::cvars()
// VERBATIM; if you change a rung in governor.rs, change it here too (the Rust governor_test pins the
// authoritative values). Applied with ECVF_SetByCode so a later scalability pass can still override,
// and so the values read back as code-set (not console-set) — no console command is involved.

#include "AgentOSThrottleLibrary.h"
#include "HAL/IConsoleManager.h"

namespace
{
	struct FThrottleCvar
	{
		const TCHAR* Name;
		const TCHAR* Value;
	};

	// --- Rung::Full (idx 0) — the restore baseline (UE returns here when the gen releases the lease) ---
	static const FThrottleCvar GFull[] = {
		{TEXT("r.ScreenPercentage"), TEXT("100")},
		{TEXT("sg.ViewDistanceQuality"), TEXT("3")},
		{TEXT("sg.AntiAliasingQuality"), TEXT("3")},
		{TEXT("sg.ShadowQuality"), TEXT("3")},
		{TEXT("sg.GlobalIlluminationQuality"), TEXT("3")},
		{TEXT("sg.ReflectionQuality"), TEXT("3")},
		{TEXT("sg.PostProcessQuality"), TEXT("3")},
		{TEXT("sg.TextureQuality"), TEXT("3")},
		{TEXT("sg.EffectsQuality"), TEXT("3")},
		{TEXT("sg.FoliageQuality"), TEXT("3")},
		{TEXT("sg.ShadingQuality"), TEXT("3")},
	};

	// --- Rung::Reduced (idx 1) — "yield a slice, keep the look" (reserved for a future controller) ---
	static const FThrottleCvar GReduced[] = {
		{TEXT("r.ScreenPercentage"), TEXT("70")},
		{TEXT("sg.GlobalIlluminationQuality"), TEXT("2")},
		{TEXT("sg.ShadowQuality"), TEXT("2")},
		{TEXT("sg.ReflectionQuality"), TEXT("2")},
		{TEXT("t.MaxFPS"), TEXT("30")},
	};

	// --- Rung::Floor (idx 2) — "minimum heartbeat, hand the GPU over" (the real VRAM-yield rung) ---
	static const FThrottleCvar GFloor[] = {
		{TEXT("r.ScreenPercentage"), TEXT("50")},
		{TEXT("sg.GlobalIlluminationQuality"), TEXT("0")},
		{TEXT("sg.ShadowQuality"), TEXT("0")},
		{TEXT("sg.ReflectionQuality"), TEXT("0")},
		{TEXT("sg.PostProcessQuality"), TEXT("0")},
		{TEXT("r.Streaming.PoolSize"), TEXT("512")},
		{TEXT("r.Streaming.LimitPoolSizeToVRAM"), TEXT("1")},
		{TEXT("t.MaxFPS"), TEXT("5")},
	};

	static void ApplySet(const FThrottleCvar* Set, int32 Num)
	{
		IConsoleManager& CM = IConsoleManager::Get();
		for (int32 i = 0; i < Num; ++i)
		{
			if (IConsoleVariable* CVar = CM.FindConsoleVariable(Set[i].Name))
			{
				CVar->Set(Set[i].Value, ECVF_SetByCode);
			}
			// A missing cvar (engine-version drift) is skipped, not fatal — the rung degrades gracefully
			// rather than aborting the whole throttle (the wallpaper must never wedge on a renamed cvar).
		}
	}
}

void UAgentOSThrottleLibrary::ApplyRung(int32 Rung)
{
	switch (Rung)
	{
	case 0:
		ApplySet(GFull, UE_ARRAY_COUNT(GFull));
		break;
	case 1:
		ApplySet(GReduced, UE_ARRAY_COUNT(GReduced));
		break;
	case 2:
		ApplySet(GFloor, UE_ARRAY_COUNT(GFloor));
		break;
	default:
		// Unknown rung index — NO-OP. Never apply a partial or unknown set (the whole rung is the unit
		// of authorization). The Rust client only ever sends 0/1/2 (governor::Rung::index()).
		break;
	}
}

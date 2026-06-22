// AgentOSBlank game module — the primary C++ module for the (formerly blueprint-only) AgentOS
// wallpaper project. Hosts UAgentOSThrottleLibrary (ADR-0029 §3). Minimal deps: Core/CoreUObject/Engine
// give us UObject + IConsoleManager, nothing more (the throttle touches only console variables).
using UnrealBuildTool;

public class AgentOSBlank : ModuleRules
{
	public AgentOSBlank(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine" });
	}
}

// Game target (the cooked -game build the wallpaper actually runs). ExtraModuleNames pulls in the
// AgentOSBlank module so UAgentOSThrottleLibrary is present at runtime for Remote Control to call.
using UnrealBuildTool;
using System.Collections.Generic;

public class AgentOSBlankTarget : TargetRules
{
	public AgentOSBlankTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		// Shares build products with the Installed Build (we can't rebuild the engine) — force our
		// module's settings in the shared environment rather than a (non-viable) unique engine rebuild.
		bOverrideBuildEnvironment = true;
		ExtraModuleNames.Add("AgentOSBlank");
	}
}

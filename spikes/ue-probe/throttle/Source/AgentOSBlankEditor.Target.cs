// Editor target — needed so the headless UnrealEditor-Cmd authoring/preview flow (the ue-wallpaper
// skill's toolchain) keeps working after the project gains a C++ module. Same single module.
using UnrealBuildTool;
using System.Collections.Generic;

public class AgentOSBlankEditorTarget : TargetRules
{
	public AgentOSBlankEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		// This editor target SHARES build products with the Installed Build's UnrealEditor, so the V5
		// strict-warning defaults differ from how the engine was built. We can't rebuild the engine
		// (Installed Build), so force our module to compile in the shared environment with our settings.
		bOverrideBuildEnvironment = true;
		ExtraModuleNames.Add("AgentOSBlank");
	}
}

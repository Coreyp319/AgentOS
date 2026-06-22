// AgentOSBlank — primary game module entry point. The project was blueprint-only; adding this module
// (and the .uproject "Modules" entry the installer writes) is what lets UAgentOSThrottleLibrary compile
// into the cooked -game build the wallpaper runs.
#include "Modules/ModuleManager.h"

IMPLEMENT_PRIMARY_GAME_MODULE(FDefaultGameModuleImpl, AgentOSBlank, "AgentOSBlank");

#!/usr/bin/env bash
# install_throttle_module.sh — install the ADR-0029 §3 throttle C++ module (B2) into the UE project,
# patch the .uproject Modules entry + the Remote Control allowlist, then (optionally) compile.
#
#   ./install_throttle_module.sh           # copy sources + patch config (no compile)
#   ./install_throttle_module.sh --build   # ...and run UBT to compile the Development editor target
#
# Idempotent: re-running re-copies the sources and is a no-op for the (already-present) config patches.
# Reversible: every patched file is backed up to <file>.bak-pre-throttle once.
#
# WHY a C++ module on a blueprint-only project: Remote Control with the §B lockdown
# (bAllowConsoleCommandRemoteExecution=False) cannot run console cvars over the wire. The sanctioned
# path (ADR-0029 §B) is a thin allowlisted UFUNCTION — UAgentOSThrottleLibrary::ApplyRung(int) — that
# maps a rung INDEX to a fixed cvar set INSIDE the engine. That UFUNCTION needs a project C++ module.
#
# PER docs (REFERENCE / ue-wallpaper-authoring skill): an Installed Build CAN compile PROJECT C++
# (only ENGINE patches — e.g. the layer-shell LinuxWindow.cpp — need a source build). So this should
# compile against ~/UnrealEngine. If UBT errors with a RulesError about the engine being installed,
# THAT is the gate to resolve (a source build), and it is the one [VERIFY-LIVE] this script can't do.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJ="${AGENTOS_UE_PROJECT:-$HOME/UnrealProjects/AgentOSBlank}"
UE="${AGENTOS_UE_ENGINE:-$HOME/UnrealEngine}"
UPROJECT="$PROJ/AgentOSBlank.uproject"
RC_INI="$PROJ/Config/DefaultRemoteControl.ini"
ALLOWLINE='+CustomAllowedRemoteFunctionCalls=(ClassPath="/Script/AgentOSBlank.AgentOSThrottleLibrary",FunctionName="ApplyRung",bAllowChildClasses=False)'

[ -f "$UPROJECT" ] || { echo "install: no .uproject at $UPROJECT (set AGENTOS_UE_PROJECT)" >&2; exit 1; }

backup_once() { [ -f "$1" ] && [ ! -f "$1.bak-pre-throttle" ] && cp -a "$1" "$1.bak-pre-throttle" || true; }

echo "→ copying the AgentOSBlank C++ module into $PROJ/Source/"
mkdir -p "$PROJ/Source"
cp -a "$HERE/Source/." "$PROJ/Source/"

echo "→ patching $UPROJECT (add the AgentOSBlank Runtime module)"
backup_once "$UPROJECT"
python3 - "$UPROJECT" <<'PY'
import json, sys
p = sys.argv[1]
with open(p) as f: d = json.load(f)
mods = d.setdefault("Modules", [])
if not any(m.get("Name") == "AgentOSBlank" for m in mods):
    mods.append({"Name": "AgentOSBlank", "Type": "Runtime", "LoadingPhase": "Default"})
    with open(p, "w") as f: json.dump(d, f, indent=2); f.write("\n")
    print("   added Modules[AgentOSBlank]")
else:
    print("   Modules[AgentOSBlank] already present — no change")
PY

echo "→ patching $RC_INI (allowlist UAgentOSThrottleLibrary::ApplyRung — the ONE throttle verb)"
backup_once "$RC_INI"
if grep -qF 'FunctionName="ApplyRung"' "$RC_INI" 2>/dev/null; then
	echo "   ApplyRung already allowlisted — no change"
else
	printf '\n; ADR-0029 §3 throttle actuation — the ONE throttle verb (rung INDEX in, cvar set applied\n; INSIDE the engine; ExecuteConsoleCommand stays disabled). Mirrors governor.rs::Rung.\n%s\n' "$ALLOWLINE" >> "$RC_INI"
	echo "   appended the ApplyRung allowlist entry"
fi

echo
echo "✓ installed. Next: COMPILE on the box (this script does not compile unless --build), then COOK."
BUILD_CMD="$UE/Engine/Build/BatchFiles/Linux/Build.sh AgentOSBlankEditor Linux Development -project=\"$UPROJECT\""
if [ "${1:-}" = "--build" ]; then
	echo "→ $BUILD_CMD"
	[ -x "$UE/Engine/Build/BatchFiles/Linux/Build.sh" ] || { echo "install: no UBT at $UE (set AGENTOS_UE_ENGINE)" >&2; exit 1; }
	eval "$BUILD_CMD"
	echo "✓ compiled. Re-cook/-package the wallpaper -game build so the module + RC allowlist ship."
else
	echo "   build (editor): $BUILD_CMD"
	echo "   then verify the lockdown still holds: $HERE/../verify_rc_lockdown.sh"
	echo "   and that ApplyRung is callable: curl -s -X PUT http://127.0.0.1:30010/remote/object/call \\"
	echo "       -d '{\"objectPath\":\"/Script/AgentOSBlank.Default__AgentOSThrottleLibrary\",\"functionName\":\"ApplyRung\",\"parameters\":{\"Rung\":2}}'  # → UE drops to the Floor rung"
fi

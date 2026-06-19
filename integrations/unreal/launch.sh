#!/usr/bin/env bash
# integrations/unreal/launch.sh — bring up an Unreal Editor that the runreal MCP can drive (ADR-0022).
# Creates a minimal blank project (if missing), writes Python Remote Execution config, ensures the Linux
# loopback multicast route, then launches the editor. This DOES make changes (writes a project, may sudo
# for the route, starts the editor) — unlike the read-only setup.sh.
#
#   UE_ROOT=~/UnrealEngine integrations/unreal/launch.sh [--offscreen]
#     UE_ROOT   : extracted engine root (contains Engine/Binaries/Linux/UnrealEditor)   [required]
#     PROJECT   : project dir (default: ~/UnrealProjects/AgentOSBlank)
#     --offscreen : run headless (-RenderOffscreen) instead of a visible editor window
set -euo pipefail

UE_ROOT="${UE_ROOT:-}"
PROJECT="${PROJECT:-$HOME/UnrealProjects/AgentOSBlank}"
OFFSCREEN=0
[ "${1:-}" = "--offscreen" ] && OFFSCREEN=1

EDITOR="$UE_ROOT/Engine/Binaries/Linux/UnrealEditor"
[ -n "$UE_ROOT" ] && [ -x "$EDITOR" ] || {
  echo "Set UE_ROOT to the extracted engine root (dir containing Engine/Binaries/Linux/UnrealEditor)."; exit 1; }

NAME="$(basename "$PROJECT")"
UPROJECT="$PROJECT/$NAME.uproject"
INI="$PROJECT/Config/DefaultEngine.ini"
UINI="$PROJECT/Config/DefaultEditorPerProjectUserSettings.ini"

# 1. Minimal blank project with the Python plugin on.
if [ ! -f "$UPROJECT" ]; then
  echo "creating blank project: $UPROJECT"
  mkdir -p "$PROJECT/Config"
  cat >"$UPROJECT" <<'UPROJ'
{
  "FileVersion": 3,
  "EngineAssociation": "",
  "Category": "",
  "Description": "AgentOS blank project — hosts UE Python Remote Execution for the MCP lane (ADR-0022).",
  "Plugins": [ { "Name": "PythonScriptPlugin", "Enabled": true } ]
}
UPROJ
fi

# 2. Remote Execution config. UE reads the "Enable Remote Execution" toggle from the PER-USER settings
#    class, which OVERRIDES the project DefaultEngine.ini — so write BOTH (idempotent).
mkdir -p "$PROJECT/Config"
if ! grep -q "PythonScriptPluginSettings" "$INI" 2>/dev/null; then
  echo "writing Remote Execution config (project): $INI"
  cat >>"$INI" <<'CFG'

[/Script/PythonScriptPlugin.PythonScriptPluginSettings]
bRemoteExecution=True
RemoteExecutionMulticastGroupEndpoint=239.0.0.1:6766
RemoteExecutionMulticastBindAddress=0.0.0.0
RemoteExecutionMulticastTtl=0
CFG
fi
if ! grep -q "PythonScriptPluginUserSettings" "$UINI" 2>/dev/null; then
  echo "writing Remote Execution config (per-user, the one that actually enables it): $UINI"
  cat >>"$UINI" <<'CFG'

[/Script/PythonScriptPlugin.PythonScriptPluginUserSettings]
bRemoteExecution=True
RemoteExecutionMulticastGroupEndpoint=239.0.0.1:6766
RemoteExecutionMulticastBindAddress=0.0.0.0
RemoteExecutionMulticastTtl=0
CFG
fi

# 3. Linux loopback multicast route (else runreal discovery finds "no nodes"). Idempotent; needs sudo.
if ip route show 239.0.0.1 2>/dev/null | grep -q .; then
  echo "loopback multicast route present (239.0.0.1)"
else
  echo "adding loopback multicast route (sudo): ip route add 239.0.0.1 dev lo"
  sudo ip route add 239.0.0.1 dev lo || \
    echo "WARNING: could not add route — run 'sudo ip route add 239.0.0.1 dev lo' yourself or the MCP won't discover the editor"
fi

# 4. Launch the editor with the project (RE has no listener without a project open).
ARGS=("$UPROJECT")
if [ "$OFFSCREEN" = 1 ]; then
  ARGS+=(-RenderOffscreen -unattended -stdout -FullStdOutLogOutput)
  echo "launching headless (-RenderOffscreen): $EDITOR ${ARGS[*]}"
else
  echo "launching editor: $EDITOR ${ARGS[*]}"
fi
echo "(keep this running — the MCP connects to the live editor; first launch compiles shaders, be patient)"
exec "$EDITOR" "${ARGS[@]}"

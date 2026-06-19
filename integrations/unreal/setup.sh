#!/usr/bin/env bash
# integrations/unreal/setup.sh — NON-DESTRUCTIVE pre-flight for the Unreal MCP lane (ADR-0022).
# Verifies the installed Unreal Editor binary + npx, then prints the MCP route to merge and the
# remaining one-time editor steps. Changes nothing — run it once the precompiled UE is on disk.
#
#   UE_ROOT=~/UnrealEngine integrations/unreal/setup.sh        (or pass the root as $1)
set -euo pipefail

UE_ROOT="${1:-${UE_ROOT:-}}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# 1. Locate the editor binary.
EDITOR=""
if [ -n "$UE_ROOT" ] && [ -x "$UE_ROOT/Engine/Binaries/Linux/UnrealEditor" ]; then
  EDITOR="$UE_ROOT/Engine/Binaries/Linux/UnrealEditor"
else
  echo "Searching for UnrealEditor (pass UE_ROOT to skip)…"
  EDITOR="$(find "${UE_ROOT:-$HOME}" /opt -maxdepth 5 -type f -name UnrealEditor \
            -path '*Engine/Binaries/Linux*' 2>/dev/null | head -n1 || true)"
fi
if [ -z "$EDITOR" ] || [ ! -x "$EDITOR" ]; then
  echo "NOT FOUND: UnrealEditor binary. Set UE_ROOT to the extracted engine root (the dir that"
  echo "contains Engine/Binaries/Linux/UnrealEditor) and re-run. Download still in progress? wait."
  exit 1
fi
echo "OK  editor:  $EDITOR"
# Inspect the binary header only — do NOT launch UnrealEditor here (it boots the full engine).
file -b "$EDITOR" 2>/dev/null | cut -c1-60 || true

# 2. The MCP runner.
if command -v npx >/dev/null; then
  echo "OK  npx:     $(command -v npx)  (runreal MCP runs via 'npx -y @runreal/unreal-mcp')"
else
  echo "MISSING: npx (Node.js). Install node/npm so the unreal MCP route can run."
fi

# 3. The route to merge into the forge .mcp.json.
echo
echo "Merge this 'unreal' route into ~/whitesur-cachyos-pack/.mcp.json (beside 'blender'):"
sed 's/^/    /' "$HERE/unreal.mcp.json"

# 4. Remaining steps (launch.sh automates these — shown here for reference).
cat <<'NEXT'

Next: integrations/unreal/launch.sh creates a blank project, writes Remote Execution config, adds the
Linux loopback multicast route, and launches the editor. What it sets up (so runreal can connect):
  - Plugin "Python Editor Script Plugin" enabled (NOT guaranteed on by default in a stock build).
  - In <Project>/Config/DefaultEngine.ini:
      [/Script/PythonScriptPlugin.PythonScriptPluginSettings]   # note: ...Settings, NOT ...UserSettings
      bRemoteExecution=True
      RemoteExecutionMulticastGroupEndpoint=239.0.0.1:6766      # 6766 UDP discovery; TCP command on 6776
      RemoteExecutionMulticastBindAddress=0.0.0.0
      RemoteExecutionMulticastTtl=0                             # keeps multicast on this host
  - Linux loopback multicast route (one per boot, else discovery finds "no nodes"):
      sudo ip route add 239.0.0.1 dev lo
Then launch the editor WITH a project open (RE has no listener on a project-less editor), and the harness
starts the MCP via the .mcp.json route (npx -y @runreal/unreal-mcp). runreal needs no host/port env — it
auto-discovers the running editor over multicast.
NEXT
echo "pre-flight done (nothing was changed)."

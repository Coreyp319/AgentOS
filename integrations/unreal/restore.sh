#!/usr/bin/env bash
# Best-effort reverse of launch.sh's SYSTEM mutations (ADR-0022 Unreal lane, deferred). It does NOT
# delete your UE project (that's your data) — it removes the Remote-Execution config blocks launch.sh
# appended and the loopback multicast route it added. The route removal needs root; printed if not.
set -euo pipefail

PROJECT="${PROJECT:-$HOME/UnrealProjects/AgentOSBlank}"
INI="$PROJECT/Config/DefaultEngine.ini"
UINI="$PROJECT/Config/DefaultEditorPerProjectUserSettings.ini"

strip_section() {  # $1=file  $2=exact [section] header
  local f="$1" sec="$2"
  [ -f "$f" ] || return 0
  python3 - "$f" "$sec" <<'PY'
import sys
f, sec = sys.argv[1], sys.argv[2]
out, skip = [], False
for ln in open(f).read().splitlines(keepends=True):
    s = ln.strip()
    if s == sec:                          # entering our section → drop it
        skip = True
        if out and out[-1].strip() == "": # also drop the blank line launch.sh's heredoc prepended,
            out.pop()                     # so apply→restore round-trips bit-identical (no residue)
        continue
    if skip and s.startswith("[") and s.endswith("]"):
        skip = False                      # next section starts → stop dropping
    if not skip:
        out.append(ln)
open(f, "w").writelines(out)
PY
  echo "✓ stripped '$sec' from $f"
}

strip_section "$INI"  "[/Script/PythonScriptPlugin.PythonScriptPluginSettings]"
strip_section "$UINI" "[/Script/PythonScriptPlugin.PythonScriptPluginUserSettings]"

if ip route show 239.0.0.1 2>/dev/null | grep -q .; then
  if [ "$(id -u)" = 0 ]; then
    ip route del 239.0.0.1 dev lo && echo "✓ removed loopback multicast route (239.0.0.1)"
  else
    echo "! loopback multicast route still present — remove with:  sudo ip route del 239.0.0.1 dev lo"
  fi
fi
echo "  (your UE project at $PROJECT is left intact.)"

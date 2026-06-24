#!/usr/bin/env bash
# AgentOS — reverse the AMD ROCm component (ADR-0048).
#
# CONSERVATIVE BY DESIGN: ROCm packages and render/video group membership are SHARED system state —
# other GPU tools on the box likely depend on them. So this does NOT rip them out automatically; it
# prints exactly what to run if you genuinely want them gone. Printed by the driver as a `sudo` step;
# running it changes nothing on its own.
set -euo pipefail
TARGET_USER="${SUDO_USER:-$USER}"
cat <<EOF
The AMD ROCm runtime and your render/video group membership are shared system state — AgentOS won't
remove them for you (other GPU tools likely rely on them). If you really want them gone, run these
yourself, deliberately:

  sudo pacman -Rns rocm-hip-runtime rocm-opencl-runtime amdsmi   # ONLY if nothing else needs ROCm
  sudo gpasswd -d $TARGET_USER render                            # may break other GPU compute
  sudo gpasswd -d $TARGET_USER video

Nothing was changed by this restore step.
EOF

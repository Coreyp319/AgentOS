#!/usr/bin/env bash
# AgentOS — AMD ROCm runtime + monitoring setup (ADR-0048).
#
# The driver PRINTS this as a `sudo` step (components.conf root:sudo) and never auto-runs it — you
# run it yourself. It installs the ROCm *runtime* subset (NOT the ~16–30 GB dev SDK) and joins the
# render/video groups, so Ollama (ollama-rocm) and ComfyUI (PyTorch-ROCm) can reach the Radeon.
# Idempotent: `pacman -S --needed` skips what's present and a group-add is a no-op if you're already
# a member. Honest no-op if there's no AMD GPU. AMD support is EXPERIMENTAL — see ADR-0048.
set -euo pipefail

# Resolve the human even under sudo, so we add the right account to the GPU groups.
TARGET_USER="${SUDO_USER:-$USER}"

# 1. Confirm an AMD GPU is actually present (PCI vendor 0x1002) — be honest, don't act otherwise.
amd=""
for v in /sys/class/drm/card[0-9]*/device/vendor; do
  [ -r "$v" ] || continue
  if [ "$(cat "$v")" = "0x1002" ]; then amd=1; break; fi
done
if [ -z "$amd" ]; then
  echo "No AMD GPU detected (no PCI vendor 0x1002 under /sys/class/drm). Nothing to do."
  echo "If you have an NVIDIA card, AgentOS supports it out of the box — skip this component."
  exit 0
fi

if ! command -v pacman >/dev/null 2>&1; then
  echo "✗ This helper targets Arch/CachyOS (pacman). On another distro, install the ROCm runtime"
  echo "  (rocm-hip-runtime, rocm-opencl-runtime, amdsmi) your way, then add '$TARGET_USER' to"
  echo "  the render and video groups."
  exit 1
fi

echo "AgentOS will install the AMD ROCm RUNTIME subset (not the heavy dev SDK):"
echo "    rocm-hip-runtime  rocm-opencl-runtime  amdsmi"
echo "and add '$TARGET_USER' to the 'render' and 'video' groups (required for GPU compute)."
echo "pacman will ask you to confirm before anything is installed."
echo

# 2. Install the runtime + monitoring subset. We do NOT pass --noconfirm — pacman prompts y/N itself.
pacman -S --needed rocm-hip-runtime rocm-opencl-runtime amdsmi

# 3. Group membership — the #1 cause of "ROCk module is NOT loaded". usermod -aG is idempotent.
usermod -aG render,video "$TARGET_USER"

cat <<EOF

✓ ROCm runtime + monitoring installed; '$TARGET_USER' added to render,video.

Next (do these yourself):
  • LOG OUT and back in (or reboot) so the new group membership takes effect.
  • Verify the stack:  rocminfo | grep -m1 'Device Type:.*GPU'   (should print a GPU line)
  • Ollama on AMD:     sudo pacman -S --needed ollama-rocm        (install ONE backend only)
  • ComfyUI on AMD:    its venv needs the ROCm PyTorch wheel, not CUDA. AgentOS setup will pick the
                       ROCm torch index automatically once Phase 2 lands (ADR-0048). Until then,
                       reinstall torch with the wheel matching your ROCm version, e.g.:
                         --index-url https://download.pytorch.org/whl/rocm6.4

Heads up — AMD support is EXPERIMENTAL (ADR-0048):
  • Local LLMs (Ollama) on RDNA3 (RX 7900 XTX) work well.
  • Video "dreaming" (ComfyUI) runs but gets NO fp8 speedup on RDNA3 and is ~2.5–3.5× slower than a
    4090 — use GGUF Q8 quants. RDNA4 (RX 9070) is the better target.
EOF

#!/usr/bin/env bash
# Install the node packs TenStrip's full 10Eros workflows depend on.
# Safety: pin torch/numpy/etc via a constraints file so no pack upgrades core.
set -u
CN=~/ComfyUI/custom_nodes
PY=~/ComfyUI/.venv/bin/python
CONSTR=/tmp/comfy_constraints.txt
LOG=/tmp/nodepacks_install.log
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

echo "[pin] core versions before:"
$PY -m pip freeze | grep -iE '^(torch|torchvision|torchaudio|numpy|opencv-python|transformers|diffusers)==' | tee "$CONSTR"

REPOS=(
  "https://github.com/TenStrip/10S-Comfy-nodes"
  "https://github.com/evanspearman/ComfyMath"
  "https://github.com/jamesWalker55/comfyui-various"
  "https://github.com/Lightricks/ComfyUI-LTXVideo"
  "https://github.com/ClownsharkBatwing/RES4LYF"
  "https://github.com/gseth/ControlAltAI-Nodes"
  "https://github.com/yolain/ComfyUI-Easy-Use"
  "https://github.com/Smirnov75/ComfyUI-mxToolkit"
  "https://github.com/Comfy-Org/Nvidia_RTX_Nodes_ComfyUI"
)

cd "$CN" || exit 1
for url in "${REPOS[@]}"; do
  name=$(basename "$url")
  echo; echo "=== $name ==="
  if [ -d "$name" ]; then
    echo "[skip clone] already present; git pull"; (cd "$name" && git pull --ff-only 2>&1 | tail -2)
  else
    git clone --depth 1 "$url" "$name" 2>&1 | tail -3 || { echo "[CLONE FAILED] $name"; continue; }
  fi
  if [ -f "$name/requirements.txt" ]; then
    echo "[pip] $name/requirements.txt (constrained)"
    $PY -m pip install -c "$CONSTR" -r "$name/requirements.txt" 2>&1 | tail -8 || echo "[PIP FAILED] $name (continuing)"
  else
    echo "[pip] no requirements.txt"
  fi
done

echo; echo "[verify] core versions after:"
$PY -m pip freeze | grep -iE '^(torch|numpy)=='
echo "[DONE] $(date)"

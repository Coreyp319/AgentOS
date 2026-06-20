#!/usr/bin/env bash
# =============================================================================
# package_game.sh — Phase-A: cook + stage + package a Development Linux -game
#                    build of AgentOSBlank, headless, from a precompiled
#                    (binary / Installed) UE 5.8 Linux engine.
#
#  >>> DO NOT RUN DURING A LIVE VRAM MEASUREMENT. <<<
#  A cook+package is a heavy multi-minute CPU/GPU/disk job. It spins up
#  UnrealEditor-Cmd to cook (which initializes the RHI / touches the GPU for
#  shader work) and will pollute any VRAM baseline you are capturing for the
#  ComfyUI + Ollama co-tenancy study. Run it only on a quiet GPU.
#
#  This script is the *spec*; it is guarded so an accidental `bash
#  package_game.sh` is a no-op. Set  UE_PROBE_ARM=1  to actually run it.
# =============================================================================

set -euo pipefail

# ---- Paths (verified present on this machine, 2026-06-19) -------------------
UE_ROOT="${UE_ROOT:-$HOME/UnrealEngine}"
RUNUAT="$UE_ROOT/Engine/Build/BatchFiles/RunUAT.sh"       # verified -r-xr-xr-x
PROJECT="${PROJECT:-$HOME/UnrealProjects/AgentOSBlank/AgentOSBlank.uproject}"
ARCHIVE_DIR="${ARCHIVE_DIR:-$HOME/UnrealProjects/AgentOSBlank/Saved/ArchivedBuilds}"

# ---- Safety interlock -------------------------------------------------------
if [[ "${UE_PROBE_ARM:-0}" != "1" ]]; then
  echo "REFUSING TO RUN: this cooks+packages and touches the GPU."
  echo "It will corrupt a live VRAM measurement. Re-run with UE_PROBE_ARM=1"
  echo "ONLY on a quiet GPU. See packaged_run.md for what to do after."
  exit 3
fi

# ---- The invocation ---------------------------------------------------------
# RunUAT BuildCookRun is THE supported one-shot for build->cook->stage->package.
# Flags below are the minimal set for a headless Development client -game build
# targeting Linux, on a Blueprint-only project, from an Installed engine.
#
# Why BuildCookRun and not raw `UnrealEditor-Cmd -run=Cook` + manual stage:
#   BuildCookRun wraps cook + stage + pak + package atomically with the same
#   target/arch resolution the editor's "Package Project" uses. The raw -run=Cook
#   path leaves you to stage/pak by hand and is easy to get subtly wrong.

"$RUNUAT" BuildCookRun \
  -project="$PROJECT" \
  `# ^ the .uproject to operate on` \
  -noP4 \
  `# ^ we are not in Perforce; skip all source-control integration` \
  -utf8output \
  `# ^ clean UTF-8 logs (safe on Linux, avoids mojibake in the UAT log)` \
  -unattended -nullrhi=false \
  `# ^ unattended = never block on a dialog/prompt (headless/CI safe).` \
  `#   NOTE: do NOT pass -nullrhi to the COOK — cooking needs a real RHI to` \
  `#   compile shaders. (-nullrhi belongs only on the packaged *run*, and even` \
  `#   then prefer -RenderOffscreen. The =false here is just documentation.)` \
  -platform=Linux \
  `# ^ target platform == host platform == native Linux build (no cross-compile)` \
  -clientconfig=Development \
  `# ^ build configuration for the client/-game exe: Development` \
  `#   (symbols + light checks; Shipping later once the throttle ladder is proven)` \
  -cook \
  `# ^ convert .uasset content -> Linux runtime format (the heavy GPU/CPU step)` \
  -stage \
  `# ^ lay the cooked content + the precompiled UnrealGame exe into a runnable tree` \
  -pak \
  `# ^ pack staged content into .pak files (one fewer thousand-file tree to ship)` \
  -package \
  `# ^ finalize the platform package layout under StagedBuilds/Linux` \
  -build \
  `# ^ build the target first. For a BLUEPRINT-ONLY project this resolves to the` \
  `#   engine's PRECOMPILED 'UnrealGame' Linux target (no project C++ to compile),` \
  `#   so it is fast and needs no project module compile. If you ever add a C++` \
  `#   Source/ folder, -build is what compiles it; until then -build and -skipbuild` \
  `#   behave nearly identically here. Keep -build for correctness.` \
  -nocompileeditor \
  `# ^ never (re)compile editor modules — we only want the runtime/game target` \
  -nodebuginfo \
  `# ^ skip staging .debug/.sym (smaller package; drop this flag if you need a` \
  `#   symbolicated crash dump from the wallpaper runtime)` \
  -archive -archivedirectory="$ARCHIVE_DIR" \
  `# ^ also copy the finished package to a stable ArchivedBuilds/ path so the` \
  `#   wallpaper launcher has one canonical location to exec from`

# =============================================================================
# EXPECTED OUTPUT
# -----------------------------------------------------------------------------
#  Staged (working) tree:
#    ~/UnrealProjects/AgentOSBlank/Saved/StagedBuilds/Linux/
#       AgentOSBlank/Binaries/Linux/AgentOSBlank      <- the runnable binary
#       AgentOSBlank/Content/Paks/*.pak               <- cooked + paked content
#       Engine/...                                    <- staged engine content
#  Archived (canonical) tree, because of -archive:
#    ~/UnrealProjects/AgentOSBlank/Saved/ArchivedBuilds/Linux/
#       (same layout)
#
#  Because AgentOSBlank is Blueprint-only, the produced game executable is the
#  engine's stock 'UnrealGame' target renamed for the project. (The Installed
#  5.8 engine already ships UnrealGame / UnrealGame-Linux-Shipping precompiled
#  under Engine/Binaries/Linux/ — verified — which is why no project compile is
#  needed.)
#
#  Launch it with the flags documented in packaged_run.md (NOT the editor).
# =============================================================================

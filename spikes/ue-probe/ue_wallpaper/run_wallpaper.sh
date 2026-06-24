#!/usr/bin/env bash
# run_wallpaper.sh — ADR-0029 §A rung 3.
# Launch the "Indigo Channel" UE 5.8 build as the actual desktop WALLPAPER: the patched
# LinuxWindow.cpp (env-gated by AGENTOS_WALLPAPER) requests a custom Wayland surface role,
# then dlopen's libagentos_layershell.so to give UE's wl_surface the zwlr_layer_shell_v1
# BACKGROUND role — so UE composites at stacking index [1] (wallpaper slot) with the panel
# and every app window on top. Rungs 1 + 2 proved this contract on hardware.
#
#   ./run_wallpaper.sh
#   # confirm: ../wallpaper_role/dump_stacking.sh   (UnrealEditor expected at index [1])
#   # stop:    pkill -9 -f '[B]inaries/Linux/UnrealEditor'
#
# -windowed -ResX/-ResY match the DP-1 output (3440x1440) so UE's swapchain size equals the
# layer_surface.configure size. No -fullscreen (would fight the custom role).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HERE/libagentos_layershell.so"
UE="$HOME/UnrealEngine/Engine/Binaries/Linux/UnrealEditor"
PROJ="$HOME/UnrealProjects/AgentOSBlank/AgentOSBlank.uproject"
MAP="/Game/AgentOS/CalmWallpaper"

[ -f "$LIB" ]  || { echo "FATAL: missing helper $LIB"; exit 1; }
[ -x "$UE" ]   || { echo "FATAL: missing UnrealEditor $UE"; exit 1; }
[ -f "$PROJ" ] || { echo "FATAL: missing project $PROJ"; exit 1; }

echo "[wallpaper] launching UE as layer-shell BACKGROUND wallpaper (lib=$LIB)"
exec env AGENTOS_WALLPAPER=1 AGENTOS_LAYERSHELL_LIB="$LIB" \
	"$UE" "$PROJ" "$MAP" -game -windowed -ResX=3440 -ResY=1440 -nosplash -nosound

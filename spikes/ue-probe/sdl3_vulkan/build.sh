#!/usr/bin/env bash
# build.sh — ADR-0029 §A PoC-0b.
# Generates the wlr-layer-shell client glue with wayland-scanner, then compiles
# the SDL3 + Vulkan + layer-shell wallpaper PoC.
#
# Toolchain resolved on this box (CachyOS, 2026-06-20):
#   - SDL3:    system, pkg-config 'sdl3' (3.4.10) — has the custom-role property
#   - Vulkan:  loader libvulkan.so in /usr/lib (pkg-config 'vulkan'), but the
#              DEV HEADERS are NOT in /usr/include — they live in
#              ~/.local/share/nimbus-vulkan-headers/include. We add that -I.
#   - wayland: wayland-client (1.25.0) + wayland-scanner (system)
#   - protocols: xdg-shell.xml (qt6 copy) + wlr-layer-shell-unstable-v1.xml
#                (vendored alongside this script)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# --- preconditions -----------------------------------------------------------
command -v wayland-scanner >/dev/null || { echo "FATAL: wayland-scanner missing"; exit 1; }
pkg-config --exists wayland-client    || { echo "FATAL: wayland-client missing"; exit 1; }
pkg-config --exists sdl3              || { echo "FATAL: system SDL3 (pkg sdl3) missing"; exit 1; }
pkg-config --exists vulkan            || { echo "FATAL: vulkan loader (pkg vulkan) missing"; exit 1; }
[ -f wlr-layer-shell-unstable-v1.xml ] || { echo "FATAL: wlr-layer-shell xml missing"; exit 1; }
[ -f xdg-shell.xml ]                    || { echo "FATAL: xdg-shell.xml missing"; exit 1; }

# Vulkan dev headers: prefer the standard path if it ever appears, else nimbus.
VK_INC=""
if [ -f /usr/include/vulkan/vulkan.h ]; then
  VK_INC=""                       # standard path; pkg-config -I/usr/include covers it
elif [ -f "$HOME/.local/share/nimbus-vulkan-headers/include/vulkan/vulkan.h" ]; then
  VK_INC="-I$HOME/.local/share/nimbus-vulkan-headers/include"
else
  echo "FATAL: no Vulkan dev headers (neither /usr/include/vulkan nor nimbus)"; exit 1
fi
echo "[build] Vulkan include flag: ${VK_INC:-(system /usr/include)}"

# --- generate wayland protocol glue -----------------------------------------
# wlr-layer-shell references the xdg_popup interface symbol (get_popup request),
# so we also generate xdg-shell glue to resolve xdg_popup_interface at link time
# even though this PoC never calls get_popup.
echo "[build] wayland-scanner: xdg-shell"
wayland-scanner client-header xdg-shell.xml xdg-shell-client-protocol.h
wayland-scanner private-code  xdg-shell.xml xdg-shell-protocol.c

echo "[build] wayland-scanner: wlr-layer-shell"
wayland-scanner client-header wlr-layer-shell-unstable-v1.xml \
                              wlr-layer-shell-unstable-v1-client-protocol.h
wayland-scanner private-code  wlr-layer-shell-unstable-v1.xml \
                              wlr-layer-shell-unstable-v1-protocol.c

# --- compile -----------------------------------------------------------------
echo "[build] cc sdl3_vulkan_wallpaper"
cc -Wall -Wextra -O2 -o sdl3_vulkan_wallpaper \
   sdl3_vulkan_wallpaper.c \
   wlr-layer-shell-unstable-v1-protocol.c \
   xdg-shell-protocol.c \
   ${VK_INC} \
   $(pkg-config --cflags sdl3 vulkan wayland-client) \
   $(pkg-config --libs   sdl3 vulkan wayland-client)

echo "[build] OK -> $HERE/sdl3_vulkan_wallpaper"
echo "[build] ldd check (expect SDL3 + vulkan + wayland-client):"
ldd ./sdl3_vulkan_wallpaper | grep -iE 'libSDL3|libvulkan|libwayland-client' || \
  echo "[build] WARNING: expected libs not found in ldd output"

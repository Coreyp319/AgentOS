#!/usr/bin/env bash
# build.sh — regenerate wayland bindings from plasma-shell.xml and compile the
# plasma DESKTOP-role test client. Software-only (wl_shm), no GPU/Vulkan.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

command -v wayland-scanner >/dev/null || { echo "FATAL: wayland-scanner missing"; exit 1; }
pkg-config --exists wayland-client || { echo "FATAL: wayland-client missing"; exit 1; }
[ -f plasma-shell.xml ] || { echo "FATAL: plasma-shell.xml missing"; exit 1; }

echo "[build] wayland-scanner -> client header + private code"
wayland-scanner client-header plasma-shell.xml plasma-shell-client-protocol.h
wayland-scanner private-code  plasma-shell.xml plasma-shell-protocol.c

echo "[build] gcc wallpaper_role_plasma"
gcc -Wall -Wextra -O2 -o wallpaper_role_plasma \
    wallpaper_role_plasma.c plasma-shell-protocol.c \
    $(pkg-config --cflags --libs wayland-client)

echo "[build] OK -> $HERE/wallpaper_role_plasma"
ldd wallpaper_role_plasma | grep -qiE 'vulkan|libGL|libEGL' \
  && echo "[build] WARNING: GPU lib linked (unexpected)" \
  || echo "[build] confirmed: no Vulkan/EGL/GL linkage (software wl_shm only)"

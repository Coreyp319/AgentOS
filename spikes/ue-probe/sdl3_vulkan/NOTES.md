# ADR-0029 §A — PoC-0b: SDL3 custom-role wl_surface + Vulkan + zwlr_layer_shell BACKGROUND

**What it proves.** The *exact* rendering path the real Unreal Engine patch (rung 3)
will use to become the desktop wallpaper:

1. SDL3 creates a **bare `wl_surface`** (NOT an `xdg_toplevel`) by setting
   `SDL_PROP_WINDOW_CREATE_WAYLAND_SURFACE_ROLE_CUSTOM_BOOLEAN=true` at window creation.
2. We pull the raw `wl_display` + `wl_surface` back out via
   `SDL_PROP_WINDOW_WAYLAND_DISPLAY_POINTER` / `SDL_PROP_WINDOW_WAYLAND_SURFACE_POINTER`.
3. We assign **that** surface the `zwlr_layer_shell_v1` **BACKGROUND** role ourselves
   (the CONFIRMED-correct wallpaper role on this KWin — RUNG 1 already passed: foreign
   BACKGROUND surface lands at stacking index [1], above the Plasma desktop containment,
   below panel + windows).
4. We hand the **same** `wl_surface` to Vulkan via `SDL_Vulkan_CreateSurface`, build a
   swapchain, and render a **live clear-color loop** (dark green `#0b3618`) — wallpaper
   behaviour, not a single frame.

This is the bridge between the (passed) gtk4 layer-shell stand-in — which could only do
software fills via gtk4-layer-shell and could NOT drive Vulkan on a custom-role surface —
and the real UE patch, which will. PoC-0b does both at once on one surface.

The layer-shell params are matched 1:1 to the gtk4 stand-in
(`../wallpaper_layer_confirm.py`): BACKGROUND layer, 4-edge anchor, `exclusive_zone = -1`,
`keyboard_interactivity = 0`.

---

## Toolchain resolved on this box (CachyOS, KWin 6.6.5, RTX 4090, 2026-06-20)

| Component  | Choice | Status |
|-----------|--------|--------|
| **SDL3**   | **System SDL3**, `pkg-config sdl3` → **3.4.10** | Used. Has the property. |
| Vulkan loader | `pkg-config vulkan` → loader 1.4.350, `libvulkan.so` in `/usr/lib` | Used. |
| Vulkan **dev headers** | **`~/.local/share/nimbus-vulkan-headers/include`** (VK_HEADER_VERSION 354) | Used (see gap below). |
| wayland   | `wayland-client` 1.25.0 + `wayland-scanner` | Used. |
| protocols | `xdg-shell.xml` (from qt6 copy) + `wlr-layer-shell-unstable-v1.xml` (from wayland-protocols-wlr) | Vendored beside this file. |

### SDL3 selection + property presence (the crux)

**System SDL3 3.4.10 was chosen** — it has dev headers, a shared lib, and pkg-config, and
it **defines the custom-role property** at the exact line the handoff cited:

```
/usr/include/SDL3/SDL_video.h:1441:
  #define SDL_PROP_WINDOW_CREATE_WAYLAND_SURFACE_ROLE_CUSTOM_BOOLEAN "SDL.window.create.wayland.surface_role_custom"
/usr/include/SDL3/SDL_video.h:1653:  SDL_PROP_WINDOW_WAYLAND_DISPLAY_POINTER
/usr/include/SDL3/SDL_video.h:1654:  SDL_PROP_WINDOW_WAYLAND_SURFACE_POINTER
```

**UE's bundled SDL3** was also located and *also* defines the property — but it is NOT
used here (reasoning under "Gaps"):
- `~/UnrealEngine/Engine/Source/ThirdParty/SDL3/SDL-gui-backend/include/SDL3/SDL_video.h`
  — same property at line 1441; version **3.4.4** (`SDL_version.h`: 3.4.4).
- Only a prebuilt **static** `libSDL3_fPIC.a` ships (under `lib/Unix/x86_64-unknown-linux-gnu/`),
  no `.so` and no pkg-config wiring for standalone use.

The two SDL3s are API-identical for this PoC (3.4.4 vs 3.4.10), so the system SDL3 is a
faithful stand-in for what UE will compile against.

---

## How to build

```bash
cd spikes/ue-probe/sdl3_vulkan
./build.sh
```

`build.sh` runs `wayland-scanner` on both vendored XMLs (xdg-shell is generated only so the
`xdg_popup_interface` symbol that wlr-layer-shell references resolves at link time — the PoC
never calls `get_popup`), then compiles with `cc` against SDL3 + Vulkan + wayland-client. It
auto-adds `-I~/.local/share/nimbus-vulkan-headers/include` because the Vulkan dev headers are
NOT in `/usr/include` on this box (see "Gaps").

---

## How to run — VISUAL test, HUMAN-ONLY on the live session

> **Do NOT run offscreen.** `QT_QPA_PLATFORM=offscreen` / `env -u WAYLAND_DISPLAY` do not
> prevent connecting to the default socket — there is no dry-run. Running this paints a real
> full-screen dark-green fill over the wallpaper slot on the live desktop.

```bash
cd spikes/ue-probe/sdl3_vulkan
./sdl3_vulkan_wallpaper          # presents the BACKGROUND Vulkan clear-color fill

# in a SECOND shell, while it is presenting:
../wallpaper_role/dump_stacking.sh

# stop it:
#   Ctrl-C   in the first shell, OR
pkill -f '[s]dl3_vulkan_wallpaper'
```

### Exact PASS criterion

PASS if **all** hold while the PoC is presenting:

1. A **Vulkan-cleared dark-green (`#0b3618`)** surface fills the wallpaper slot
   (covers the previous wallpaper + desktop icons — icons hidden is EXPECTED, ADR-0029
   accepted giving them up).
2. `../wallpaper_role/dump_stacking.sh` shows the PoC surface at **stacking index [1]** —
   **above** the plasmashell desktop containment at index [0], **below** every app window
   **and** the panel.
3. The **panel/taskbar stays visible on top**, and an app **window raised on top** of the
   fill — i.e. the desktop is still usable with this as the wallpaper.

FAIL signatures:
- Blank / no fill, no stacking entry → the surface never mapped (check `[poc]` stderr for
  which step failed: configure, instance, surface, swapchain).
- Fill appears but at index [0] or above the panel → wrong stacking (would contradict RUNG 1;
  not expected on this KWin).

The `[poc] …` stderr trace narrates every step (driver pick → custom-role window → raw
display/surface → layer_shell bind → BACKGROUND role → configure ack → Vulkan instance →
VkSurfaceKHR → swapchain → LIVE), so a partial failure is self-locating.

---

## Static validation performed (all PASS, 2026-06-20)

- **Compiles + links cleanly**, zero warnings under `-Wall -Wextra -O2`.
- `ldd ./sdl3_vulkan_wallpaper` shows **`libSDL3.so.0`, `libvulkan.so.1`,
  `libwayland-client.so.0`** (plus libm/libc/libffi).
- Chosen SDL3 header (`/usr/include/SDL3/SDL_video.h`, pkg sdl3 3.4.10) **defines**
  `SDL_PROP_WINDOW_CREATE_WAYLAND_SURFACE_ROLE_CUSTOM_BOOLEAN` (line 1441) and the wayland
  display/surface pointer props (1653/1654).
- `vulkaninfo` confirms **`VK_KHR_wayland_surface` (extension revision 6)** + `VK_KHR_surface`
  on the RTX 4090 (driver NVIDIA, apiVersion 1.4.341).
- Binary symbol sanity (`nm -D`): imports `SDL_CreateWindowWithProperties`,
  `SDL_SetBooleanProperty`, `SDL_GetPointerProperty`, `SDL_Vulkan_CreateSurface`,
  `vkCreateInstance`, `vkCreateDevice`, `vkCreateSwapchainKHR`; embeds the exact property
  strings + the `agentos-ue-poc` layer namespace.

---

## Gaps / honest notes

- **Vulkan dev headers are NOT in the standard `/usr/include/vulkan/`.** This box has the
  Vulkan *loader* (`libvulkan.so`) and `vulkan.pc` (whose `Cflags: -I/usr/include` is stale),
  but the actual headers live in `~/.local/share/nimbus-vulkan-headers/include`. `build.sh`
  compensates. If `vulkan-headers` is later installed system-wide, `build.sh` auto-prefers
  `/usr/include`. **Not a blocker**, just non-standard layout.
- **UE's bundled SDL3 was NOT linked** (static-only `.a`, no pkg-config, version 3.4.4). The
  system SDL3 3.4.10 is API-identical for the calls used and is the faithful proxy. When the
  real UE patch lands, it will use UE's own SDL3 in-tree — this PoC proves the *protocol/API
  contract* that patch must honour, not UE's build wiring.
- **Render path uses `vkCmdClearColorImage` + layout barriers**, not a render pass/pipeline —
  deliberately, to keep the PoC tiny (no shaders, no geometry). UE will render through its own
  pipeline into the same swapchain; the swapchain-over-custom-role-surface contract is what is
  being de-risked here, and that is format/pipeline-agnostic.
- **VRAM:** one FIFO double/triple-buffered swapchain at output res (~tens of MB). No UE, no
  extra allocations. Safe to run alongside ongoing gens.
- **Output selection:** `get_layer_surface(output=NULL)` lets the compositor place it (primary).
  Multi-monitor placement is out of scope for PoC-0b (UE integration concern, not a protocol
  question).

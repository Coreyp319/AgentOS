# ADR-0029 §A — wallpaper-role PoC-0b (plasma Desktop role vs wlr BACKGROUND)

Goal: find the Wayland surface role that makes a **foreign, raw `wl_surface`** become
the KWin desktop wallpaper — full-screen, with the panel + app windows (and ideally the
desktop icons) stacked **on top of it**. This is the role we will later inject onto UE's
window via SDL3's `SDL_PROP_WINDOW_CREATE_WAYLAND_SURFACE_ROLE_CUSTOM_BOOLEAN`, so the
role must be expressible on a bare `wl_surface` (not via a GTK/Qt toolkit).

PoC-0a (`spikes/ue-probe/layershell_poc.py`, gtk4-layer-shell BACKGROUND) composited
full-screen but stacked **ABOVE** the desktop icons → that's the FAIL we're trying to beat.
gtk4-layer-shell can only speak `zwlr_layer_shell`; it cannot speak `org_kde_plasma_shell`.
Hence this raw-Wayland client.

Everything for PoC-0b lives in `spikes/ue-probe/wallpaper_role/`.

## What each client does

### A. Plasma DESKTOP role — `wallpaper_role/wallpaper_role_plasma` (the new candidate)
Raw C / `libwayland-client` client. No toolkit, no GPU, no Vulkan, no EGL — a software
`wl_shm` ARGB8888 fill only (~tens of MB). It:
1. binds `wl_compositor`, `wl_shm`, `org_kde_plasma_shell` (v8), `wl_output`;
2. creates a **bare `wl_surface`** (the same shape SDL3 hands UE);
3. `org_kde_plasma_shell.get_surface(wl_surface)` → `org_kde_plasma_surface`;
4. `org_kde_plasma_surface.set_role(DESKTOP)` — **role enum value `1`** (see below);
5. `set_skip_taskbar(1)`, `set_skip_switcher(1)`, `set_output(output)`, `set_position(0,0)`;
6. paints a distinct **dark-teal `#0b362a`** full-screen fill with an 8px bright-teal
   border (obviously a test, never a glitch) and commits;
7. prints what it did and stays mapped until Ctrl-C / SIGTERM.

Exact role requested (from the on-box protocol XML, `org_kde_plasma_surface` enum `role`):
```
normal=0   DESKTOP=1   panel=2   onscreendisplay=3   notification=4 ...
```
We request **`desktop` = `1`**. The XML's own description is the whole reason this is the
candidate: *"Desktop surfaces are placed below all other surfaces ... Only one surface per
output can have the desktop role."* That is exactly the wallpaper contract.

### B. wlr BACKGROUND layer — `run.sh --layer` (the PoC-0a comparison)
Re-runs the existing `spikes/ue-probe/layershell_poc.py` (gtk4-layer-shell, `BACKGROUND`
layer, full-screen, exclusive-zone -1, no keyboard) under the required
`LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so`. Same teal-ish test fill. Provided so the two
approaches are compared under **identical introspection** (`dump_stacking.sh`).

### C. Self-diagnostic — `dump_stacking.sh` + `kwin_dump_stacking.js`
Loads a KWin script via `org.kde.KWin /Scripting` and prints KWin's bottom→top
`workspace.stackingOrder` to the journal, then greps it back out. **Non-destructive — read
only.** Index `0` is the BOTTOM (drawn first / behind everything). Validated working on this
box (see below). Run it in a second shell *while* a PoC client is presenting.

## How to run (VISUAL test — a human runs this; do not auto-run on a busy session)

Build is already done, but to rebuild:
```
cd spikes/ue-probe/wallpaper_role && ./build.sh
```

Plasma DESKTOP-role test (terminal 1):
```
cd spikes/ue-probe/wallpaper_role && ./run.sh
```
Read where it landed (terminal 2, while it's presenting):
```
cd spikes/ue-probe/wallpaper_role && ./dump_stacking.sh
```
Stop it: Ctrl-C in terminal 1, or `pkill -f '[w]allpaper_role_plasma'`.

wlr BACKGROUND comparison (terminal 1):
```
cd spikes/ue-probe/wallpaper_role && ./run.sh --layer
```
Same `./dump_stacking.sh` in terminal 2. Stop: Ctrl-C, or `pkill -f '[l]ayershell_poc'`.

## What output to look for — PASS vs FAIL

`dump_stacking.sh` prints lines like:
```
[0] class='plasmashell' name='plasmashell' caption='' geo=0,0 3440x1440   <- desktop-icon view (BOTTOM)
[10] class='plasmashell' name='plasmashell' caption='' geo=860,1368 1719x72 <- the panel/dock
...
```
**The PoC surface** is a raw client → it shows as **empty `class`/`name`/`caption` with a
full-output geometry** (here `0,0 3440x1440`). Find that line.

- **PASS (plasma desktop role works):** the PoC line sits **at index 0 / below** the
  `plasmashell` full-screen desktop-icon view — i.e. behind icons **and** panel **and**
  windows. (KWin's own desktopcontainment also uses the desktop role, so the PoC may land
  right next to it; "at/below the plasmashell 3440x1440 view" is the bar.)
- **PARTIAL:** below the panel + windows but **above** the `plasmashell` 3440x1440 desktop
  view → it would cover the icons (same failure mode as PoC-0a, just via a different role).
- **FAIL:** the PoC line sits high in the list (above app windows) → KWin did not honour the
  desktop role for a foreign client (e.g. rejected the role, or the "one desktop surface per
  output" rule made KWin ignore the second one). Watch the run-terminal stdout and
  `journalctl --user -t kwin_wayland -f` for a protocol error in that case.

## Static validation results (done — non-visual)

- `org_kde_plasma_shell` v8 (name 21), `wl_shm` v2, `wl_compositor` v6, `wl_output` v4 all
  **bindable** per `wayland-info`. `zwlr_layer_shell_v1` v5 also present.
- Protocol XML: no `plasma-wayland-protocols` package installed on-box, but the **canonical
  upstream `plasma-shell.xml`** is present in the cargo registry
  (`wayland-protocols-plasma-0.3.12/.../protocols/plasma-shell.xml`); copied to
  `wallpaper_role/plasma-shell.xml` (sha256 `1b686f6f…`).
- `wayland-scanner 1.25.0` generated `plasma-shell-client-protocol.h` / `-protocol.c`
  cleanly; header confirms `ORG_KDE_PLASMA_SURFACE_ROLE_DESKTOP = 1` and the
  `get_surface` / `set_role` / `set_skip_taskbar` / `set_skip_switcher` / `set_output` /
  `set_position` wrappers.
- `gcc -Wall -Wextra` builds `wallpaper_role_plasma` with **zero warnings**; `ldd` confirms
  **no Vulkan / EGL / GL** linkage (libwayland-client + libc/libm/libffi only).
- gtk4-layer-shell python bindings import cleanly (`Gtk 4.0` + `Gtk4LayerShell 1.0`).
- KWin scripting introspection validated end-to-end **non-visually**: `loadScript` →
  `start()` → `print()` reaches `journalctl --user -t kwin_wayland`; baseline stack on this
  box is 25–26 windows, index 0 = plasmashell 3440×1440, a ~72px-tall plasmashell line = the
  panel.

## Gotchas / honesty notes

- **The binary connects via the default socket even with `WAYLAND_DISPLAY` unset** —
  libwayland-client falls back to `$XDG_RUNTIME_DIR/wayland-0`. So you cannot "dry-run" it
  off-screen; `./run.sh` is the real visual test. (During development one off-screen attempt
  briefly presented and was immediately killed; the stack was verified back to baseline.)
- `dump_stacking.sh` copies the JS to a unique temp path per run (KWin caches loaded scripts
  by path) and anchors the index grep as `\[[0-9]+\]` (journal lines are timestamp-prefixed,
  so `^\[` matches nothing).
- KWin's "only one surface per output can have the desktop role" could mean KWin **ignores**
  a foreign desktop-role surface because plasmashell already owns that slot. If the PoC
  surface never appears in the dump or a protocol error fires, that's the likely reason —
  and an important finding for A1 either way (it would mean the real integration must either
  replace/cooperate-with plasmashell's desktop surface, or fall back to the layer-shell
  BACKGROUND result with an icons-on-top accommodation).

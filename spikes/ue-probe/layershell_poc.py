#!/usr/bin/env python3
# layershell_poc.py — ADR-0029 §A PoC-0a (the disproof-first gate).
#
# QUESTION: will KWin (Plasma 6) host a FOREIGN zwlr_layer_shell_v1 BACKGROUND
# surface as the wallpaper — full-screen, BEHIND the desktop icons, click-through?
# Zero UE, zero Vulkan — isolates the compositor-stacking risk (the realer of the
# two §A unknowns) before any SDL3/UE engine work. If this PASSES, the only thing
# left is SDL3's documented custom-role surface + Vulkan (PoC-0b); if it FAILS,
# A1 needs a Plasma-side accommodation (org_kde_plasma_shell desktop role) and we
# learn that for ~zero cost.
#
# Run on the live Wayland session (WAYLAND_DISPLAY set). It paints a distinct,
# labelled dark-teal fill so it's obviously a test, not a glitch.
#   python3 spikes/ue-probe/layershell_poc.py     (Ctrl-C or pkill -f layershell_poc to stop)
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

# NB: gtk4-layer-shell must load before libwayland-client → run with
#   LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so python3 layershell_poc.py


def on_activate(app):
    win = Gtk.ApplicationWindow(application=app)
    LayerShell.init_for_window(win)
    LayerShell.set_layer(win, LayerShell.Layer.BACKGROUND)          # the wallpaper slot
    for edge in (LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT,
                 LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM):
        LayerShell.set_anchor(win, edge, True)                      # full-screen
    LayerShell.set_exclusive_zone(win, -1)                          # ignore panel reservations
    LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)  # input-less

    css = Gtk.CssProvider()
    css.load_from_data(
        b"window{background:#0b2a36;} "
        b"label{color:#6fc8de;font-size:40px;font-weight:bold;}")
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, 800)
    win.set_child(Gtk.Label(
        label="AgentOS — layer-shell BACKGROUND PoC\n"
              "your icons should sit ABOVE this teal fill"))
    win.present()
    print("[poc] presented BACKGROUND layer-shell surface", flush=True)


app = Gtk.Application(application_id="org.agentos.layershell.poc")
app.connect("activate", on_activate)
app.run(None)

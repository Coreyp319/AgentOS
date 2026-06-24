#!/usr/bin/env python3
# wallpaper_layer_confirm.py — ADR-0029 §A PoC-0a CONFIRM (give-up-icons path).
#
# Context (2026-06-20). PoC-0a (layershell_poc.py) proved KWin WILL host a foreign
# zwlr_layer_shell_v1 BACKGROUND surface full-screen. The on-box KWin stacking
# probe then established: on this box (KWin 6.6.5) the surface lands ABOVE the
# Plasma desktop containment (so it covers wallpaper + icons) but BELOW the panel
# and BELOW application windows. Corey disposed §A to the AUTHORITATIVE-UE path and
# ACCEPTED losing the Plasma desktop-icon grid (icons re-home to launcher/panel).
# For THAT path, "covers the icons" is no longer a fail — it is the intended
# behavior. The single thing left to confirm EMPIRICALLY (PoC-0a's screenshot was
# an ambiguous crop with no panel visible) is that the PANEL and APP WINDOWS
# genuinely float ON TOP of this surface, i.e. the desktop stays usable with UE as
# the wallpaper.
#
# This paints a labelled dark-indigo BACKGROUND surface and states exactly what to
# check. Zero UE, zero Vulkan, zero GPU memory, ZERO config change — fully
# transient (Ctrl-C or `pkill -f '[w]allpaper_layer_confirm'` dismisses it).
#   LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so python3 wallpaper_layer_confirm.py
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

# NB: gtk4-layer-shell must load before libwayland-client → run with
#   LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so python3 wallpaper_layer_confirm.py


def on_activate(app):
    win = Gtk.ApplicationWindow(application=app)
    LayerShell.init_for_window(win)
    LayerShell.set_layer(win, LayerShell.Layer.BACKGROUND)           # the wallpaper slot
    for edge in (LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT,
                 LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM):
        LayerShell.set_anchor(win, edge, True)                       # full-screen
    LayerShell.set_exclusive_zone(win, -1)                           # ignore panel reservations
    LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)  # input-less, like a wallpaper

    css = Gtk.CssProvider()
    css.load_from_data(
        b"window{background:#160b36;} "
        b"label{color:#8a7fd6;font-size:34px;font-weight:bold;}")
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, 800)
    win.set_child(Gtk.Label(
        label="AgentOS — UE-wallpaper layer CONFIRM (stand-in for UE)\n\n"
              "PASS if, with this fill up, you can still:\n"
              "  • see your PANEL / taskbar\n"
              "  • open or raise an app WINDOW and have it sit ON TOP of this\n\n"
              "Desktop ICONS being hidden by this fill is EXPECTED (we gave them up).\n\n"
              "Dismiss: Ctrl-C in the terminal, or  pkill -f '[w]allpaper_layer_confirm'"))
    win.present()
    print("[confirm] BACKGROUND layer-shell surface presented.", flush=True)
    print("[confirm] CHECK: panel visible on top? app window raises on top? "
          "(icons hidden is EXPECTED)", flush=True)


app = Gtk.Application(application_id="org.agentos.layershell.confirm")
app.connect("activate", on_activate)
app.run(None)

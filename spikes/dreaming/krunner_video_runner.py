#!/usr/bin/env python3
"""AgentOS — KRunner D-Bus runner for on-demand local video generation.

Surface B of the dreaming pivot: the user types `video: <prompt>` into KRunner
(KDE Plasma 6) and gets a locally-generated clip. This is a *D-Bus* runner — no
C++ plugin to compile; it just owns a bus name and implements org.kde.krunner1.
KRunner discovers it via a .desktop file in ~/.local/share/krunner/dbusplugins/
(see dist/agentos-video.desktop).

It dispatches to the SAME shared backend as the ambient wallpaper
(comfy_client.py -> ComfyUI), so there is one model stack, two consumers.

Status: SCAFFOLD. Matching + dispatch + result-open work; the default workflow
template and model are wired to the Wan 2.2 test config and should be made
configurable before this is a shipped feature. Generation runs detached so it
never blocks the KRunner UI; the user is notified when the clip is ready.

Run:  python3 krunner_video_runner.py     (foreground, for testing)
Install as a user service: see dist/agentos-video.desktop + README.
Deps: python3-dbus, python3-gobject (PyGObject) — standard on a KDE box.
"""
import os
import subprocess
import sys

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

SERVICE = "org.agentos.krunner.video"
PATH = "/krunner"
IFACE = "org.kde.krunner1"

TRIGGER = "video:"
HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(HERE, "comfy_client.py")
# Default backend config (kept aligned with the Wan 2.2 test path for now).
TEMPLATE = os.environ.get(
    "AGENTOS_VIDEO_TEMPLATE",
    os.path.expanduser(
        "~/ComfyUI/.venv/lib/python3.12/site-packages/"
        "comfyui_workflow_templates_media_video/templates/video_wan2_2_5B_ti2v.json"
    ),
)
PYTHON = os.environ.get("AGENTOS_VIDEO_PYTHON", "python3")


def _notify(summary, body=""):
    try:
        subprocess.Popen(
            ["notify-send", "-a", "AgentOS", "-i", "video-x-generic", summary, body]
        )
    except Exception:
        pass


def _generate_detached(prompt):
    """Fire the shared backend without blocking the KRunner UI.

    comfy_client --open does notify + xdg-open on completion. Fast test dims
    (768x432, 49 frames) keep the wait reasonable on a 24 GB GPU.
    """
    _notify("Dreaming up your video…", prompt)
    subprocess.Popen([
        PYTHON, CLIENT, "run-template", TEMPLATE,
        "--prompt", prompt,
        "--width", "768", "--height", "432", "--length", "49", "--steps", "20",
        "--out-prefix", "krunner", "--open",
    ])


class Runner(dbus.service.Object):
    @dbus.service.method(IFACE, in_signature="s", out_signature="a(sssida{sv})")
    def Match(self, query):
        q = query.strip()
        if not q.lower().startswith(TRIGGER):
            return []
        prompt = q[len(TRIGGER):].strip()
        if not prompt:
            return []
        # (data, display text, icon, type, relevance, properties)
        # type 100 == Plasma::QueryMatch::ExactMatch
        return [(
            prompt,
            f"Generate video: {prompt}",
            "video-x-generic",
            100,
            1.0,
            {"subtext": dbus.String("local · Wan 2.2 via ComfyUI")},
        )]

    @dbus.service.method(IFACE, out_signature="a(sss)")
    def Actions(self):
        return []  # (id, text, icon) — none for now

    @dbus.service.method(IFACE, in_signature="ss")
    def Run(self, matchId, actionId):
        _generate_detached(matchId)


def main():
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    name = dbus.service.BusName(SERVICE, bus)  # noqa: F841 (keep name owned)
    Runner(bus, PATH)
    print(f"[agentos-video] runner up: {SERVICE} {PATH} (trigger '{TRIGGER} …')",
          file=sys.stderr)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()

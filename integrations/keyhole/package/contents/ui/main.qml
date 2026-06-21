/*
 * main.qml — the keyhole plasmoid root (Plasma 6 PlasmoidItem).
 *
 * Wires the three representations and the file-poll seam:
 *   - compactRepresentation : the tray glyph (idle-vanish via Plasmoid.status)
 *   - fullRepresentation    : the arbitration-led instrument panel
 *   - KeyholeModel          : the Timer-poll of keyhole.json (NOT sync-XHR)
 *
 * The native KDE blurred popup is provided by Plasma itself: a PlasmoidItem's
 * fullRepresentation is hosted in a PlasmaCore Dialog whose background is the
 * translucent, blurred panel theme surface. We DO NOT hand-roll a shader
 * (ADR-0012: the instrument must never compete with inference for VRAM).
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Layouts
import QtCore                       // StandardPaths singleton (Qt 6.5+)

import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    // The shared state/poll model. Child reps reference it as `keyhole`.
    property var keyhole: model
    // The boot-health board model (status-panel :9123). Polled only while open.
    property var servicesModel: services
    // The instrument skin, switched by the active colour scheme so every surface
    // reacts to the desktop light/dark toggle. The dependency-light reps just consume it.
    property var skin: instSkin
    InstrumentPalette { id: instSkin; dark: root.schemeDark }

    // Light/dark follows the active COLOUR SCHEME (kdeglobals), which the taskbar toggle
    // changes via `plasma-apply-colorscheme`. We read it through the executable DataSource,
    // NOT Kirigami.Theme: a plasmoid's Kirigami.Theme reflects the Plasma *desktop theme*,
    // and the WhiteSur desktop themes ship the SAME dark colours in both light and dark
    // variants — so Kirigami.Theme never changes here. The scheme's window background does.
    property bool schemeDark: true
    function readScheme() {
        var cmd = "kreadconfig6 --file kdeglobals --group " + fileReader.shellQuote("Colors:Window")
                  + " --key BackgroundNormal"
        fileReader.run(cmd, function(txt) {
            var p = String(txt).trim().split(",")
            if (p.length >= 3) {
                var r = parseInt(p[0]), g = parseInt(p[1]), b = parseInt(p[2])
                if (!isNaN(r) && !isNaN(g) && !isNaN(b))
                    root.schemeDark = (0.299 * r + 0.587 * g + 0.114 * b) < 128
            }
        })
    }
    // Re-read on a calm cadence so a toggle is picked up within a few seconds, plus
    // immediately at load and whenever the popup opens.
    Timer {
        interval: 3000; running: true; repeat: true; triggeredOnStart: true
        onTriggered: root.readScheme()
    }

    KeyholeModel {
        id: model
        // $XDG_RUNTIME_DIR/nimbus-aurora/keyhole.json, resolved via StandardPaths
        // (RuntimeLocation == XDG_RUNTIME_DIR). No env-reading hacks, no extra dep.
        // Qt 6 QML StandardPaths.writableLocation returns a QUrl (file://…), but the
        // executable `cat` backend needs a plain filesystem path — otherwise it runs
        // `cat 'file:///run/user/1000/…'` which fails and the feed never loads (the
        // plasmoid then shows a permanent "unavailable"). Strip the scheme.
        feedPath: StandardPaths.writableLocation(StandardPaths.RuntimeLocation).toString()
                  .replace(/^file:\/\//, "") + "/nimbus-aurora/keyhole.json"
        // reduced-motion follows the Plasma animation-speed setting (clamps tweens too)
        reducedMotion: Kirigami.Units.longDuration <= 1
        // PROVEN production reader: Plasma5Support executable `cat`. qml6's XHR on
        // file:// is disabled by default and plasmashell does NOT set the override
        // env var, so XHR would read empty. DataSource reads with no flag.
        readBackend: function(path, onText) { fileReader.read(path, onText) }
    }

    // The robust shell reader (Plasma5Support executable engine). Re-runs its command
    // each tick; off the hot path of NVML / the lease lock. `read` fetches a file via
    // `cat` (the keyhole.json feed); `run` executes an arbitrary command (the services
    // board uses it for a short-timeout `curl` against the status panel). Same proven
    // engine in both cases — qml6's file:// XHR is disabled and plasmashell sets no
    // override, so DataSource is the reliable reader here.
    P5Support.DataSource {
        id: fileReader
        engine: "executable"
        connectedSources: []
        property var _cb: ({})          // source (command) -> callback
        function run(cmd, onText) {
            _cb[cmd] = onText
            connectSource(cmd)
        }
        function read(path, onText) { run("cat " + shellQuote(path), onText) }
        function shellQuote(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'" }
        onNewData: function(source, data) {
            disconnectSource(source)    // one-shot per tick; fresh read next tick
            var cb = _cb[source]; delete _cb[source]
            if (cb) cb((data && data["exit code"] === 0) ? (data["stdout"] || "") : "")
        }
    }

    // Boot-health board feed: the keyhole pulls the status panel's /status.json via
    // the SAME executable engine (curl), only while the popup is open, and degrades to
    // the honest UNAVAILABLE posture if the panel is down. No new IPC, no panel changes.
    ServicesModel {
        id: services
        active: root.expanded
        readBackend: function(onText) {
            fileReader.run("curl -s --max-time 5 " + fileReader.shellQuote(services.url), onText)
        }
    }
    // Prime the board ONCE at load so its rows (and thus the popup's measured height)
    // exist before the popup is first shown — otherwise the popup measures itself while
    // the board is still empty, locks in a short height, and clips when data arrives.
    Component.onCompleted: { services.poll(); readScheme() }
    // Reopening fetches fresh immediately (don't wait for the first interval).
    onExpandedChanged: if (root.expanded) { services.poll(); readScheme() }

    // --- Tray idle-vanish: byte-identical-to-baseline at true idle ----------
    Plasmoid.status: {
        var s = model.effectiveState
        if (s === "needs_you") return PlasmaCore.Types.NeedsAttentionStatus
        // As a standalone panel widget (not a tray item) PassiveStatus does NOT cleanly
        // vanish — it just stops the idle glyph presenting. Keep idle ACTIVE so the calm
        // ○ stays visible (CompactRepresentation dims it); the tray idle-vanish is moot here.
        // unknown is shown (dimmed) — honesty: a broken substrate must NOT look idle
        return PlasmaCore.Types.ActiveStatus
    }

    Plasmoid.icon: "view-visible"
    toolTipMainText: "AgentOS Keyhole"
    toolTipSubText: model.labelFor(model.effectiveState)

    // On the desktop show the full panel; on a panel keep the compact tray glyph.
    preferredRepresentation: Plasmoid.formFactor === PlasmaCore.Types.Planar
                             ? fullRepresentation : compactRepresentation

    compactRepresentation: CompactRepresentation { plasmoidItem: root; skin: root.skin }

    // FullRepresentation already carries implicitWidth/implicitHeight, so it can be the
    // popup root directly — matching the shipped tray-plasmoid pattern (org.kde.kdeconnect).
    // A ColumnLayout wrapper here produced a popup the system tray would not show.
    fullRepresentation: FullRepresentation {
        model: root.keyhole
        services: root.servicesModel
        skin: root.skin
    }
}

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

    KeyholeModel {
        id: model
        // $XDG_RUNTIME_DIR/nimbus-aurora/keyhole.json, resolved via StandardPaths
        // (RuntimeLocation == XDG_RUNTIME_DIR). No env-reading hacks, no extra dep.
        feedPath: StandardPaths.writableLocation(StandardPaths.RuntimeLocation)
                  + "/nimbus-aurora/keyhole.json"
        // reduced-motion follows the Plasma animation-speed setting (clamps tweens too)
        reducedMotion: Kirigami.Units.longDuration <= 1
        // PROVEN production reader: Plasma5Support executable `cat`. qml6's XHR on
        // file:// is disabled by default and plasmashell does NOT set the override
        // env var, so XHR would read empty. DataSource reads with no flag.
        readBackend: function(path, onText) { fileReader.read(path, onText) }
    }

    // The robust file reader (Plasma5Support executable engine). Re-runs `cat`
    // each tick; off the hot path of NVML / the lease lock (reads a JSON file only).
    P5Support.DataSource {
        id: fileReader
        engine: "executable"
        connectedSources: []
        property var _cb: ({})          // source -> callback
        function read(path, onText) {
            var cmd = "cat " + shellQuote(path)
            _cb[cmd] = onText
            connectSource(cmd)
        }
        function shellQuote(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'" }
        onNewData: function(source, data) {
            disconnectSource(source)    // one-shot per tick; fresh read next tick
            var cb = _cb[source]; delete _cb[source]
            if (cb) cb((data && data["exit code"] === 0) ? (data["stdout"] || "") : "")
        }
    }

    // --- Tray idle-vanish: byte-identical-to-baseline at true idle ----------
    Plasmoid.status: {
        var s = model.effectiveState
        if (s === "needs_you") return PlasmaCore.Types.NeedsAttentionStatus
        if (s === "idle")      return PlasmaCore.Types.PassiveStatus   // tray hides it
        // unknown is shown (dimmed) — honesty: a broken substrate must NOT look idle
        return PlasmaCore.Types.ActiveStatus
    }

    Plasmoid.icon: "view-visible"
    toolTipMainText: "AgentOS Keyhole"
    toolTipSubText: model.labelFor(model.effectiveState)

    // On the desktop show the full panel; on a panel keep the compact tray glyph.
    preferredRepresentation: Plasmoid.formFactor === PlasmaCore.Types.Planar
                             ? fullRepresentation : compactRepresentation

    compactRepresentation: CompactRepresentation { plasmoidItem: root }

    fullRepresentation: ColumnLayout {
        Layout.minimumWidth: 360
        Layout.minimumHeight: 280
        FullRepresentation {
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: root.keyhole
        }
    }
}

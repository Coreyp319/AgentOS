/*
 * CompactRepresentation.qml — the tray glyph.
 *
 * A state-tinted SHAPE (never color-only). Vanishes at true idle (the plasmoid
 * sets Plasmoid.status = PassiveStatus, which the system tray hides). The glyph
 * is the same shape vocabulary as the full panel so the zoom reads as one object.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore

Item {
    id: compact
    // `keyhole` is the KeyholeModel, exposed as a context/root property by main.qml
    property var model: keyhole
    // The PlasmoidItem root, passed by main.qml. In Plasma 6 `expanded` lives on the
    // PlasmoidItem (NOT the attached Plasmoid singleton — `Plasmoid.expanded` is undefined
    // and silently no-ops), so the popup toggle must go through this handle.
    required property PlasmoidItem plasmoidItem

    readonly property color warmFg: "#FF9957"
    readonly property color fg: "#E6E9F0"
    readonly property color dimFg: "#8A90A0"

    Text {
        anchors.centerIn: parent
        text: compact.model ? compact.model.glyphFor(compact.model.effectiveState) : "—"
        font.pixelSize: Math.round(Math.min(compact.width, compact.height) * 0.8)
        color: {
            var s = compact.model ? compact.model.effectiveState : "unknown"
            if (s === "needs_you") return compact.warmFg
            if (s === "snag" || s === "unknown") return compact.dimFg
            return compact.fg
        }
        // a quiet warm halo only on needs_you — the one warm element
        layer.enabled: compact.model && compact.model.effectiveState === "needs_you"
    }

    // Click toggles the popup. (DIAGNOSTIC build: logs to the journal so we can see
    // whether the handler fires, plasmoidItem is valid, and `expanded` actually flips.)
    MouseArea {
        anchors.fill: parent
        onClicked: {
            var pi = compact.plasmoidItem
            console.warn("KEYHOLE-CLICK fired; plasmoidItem=" + pi
                         + " expandedBefore=" + (pi ? pi.expanded : "<null>"))
            if (pi) pi.expanded = !pi.expanded
            console.warn("KEYHOLE-CLICK expandedAfter=" + (pi ? pi.expanded : "<null>"))
        }
    }
}

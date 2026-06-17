/*
 * CompactRepresentation.qml — the panel glyph.
 *
 * A state-tinted SHAPE (never color-only), using the same shape vocabulary as the
 * full panel so the zoom reads as one object. As a standalone panel widget it stays
 * present at idle (a calm, dimmed ○) rather than vanishing — the tray idle-vanish via
 * Plasmoid.status = PassiveStatus only applied when hosted in the system tray.
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
    // The PlasmoidItem root, passed by main.qml. Plasma 6 keeps `expanded` on the
    // PlasmoidItem (the attached Plasmoid singleton has no `expanded`).
    required property PlasmoidItem plasmoidItem
    // instrument skin (light/dark register), injected by main.qml so the panel glyph
    // follows the desktop light/dark toggle. Default dark if not injected.
    property var skin: _defaultSkin
    InstrumentPalette { id: _defaultSkin }

    readonly property color warmFg: skin.warm
    readonly property color fg: skin.text
    readonly property color dimFg: skin.dim

    // the tray glyph wears the SAME aurora ring/halo as the panel header, so the
    // tray→popup zoom reads as one object. Earned (calm at rest, blooms with busy).
    AuroraRing {
        anchors.centerIn: parent
        glyphSize: Math.round(Math.min(compact.width, compact.height) * 0.58)
        glyph: compact.model ? compact.model.glyphFor(compact.model.effectiveState) : "—"
        glyphColor: {
            var s = compact.model ? compact.model.effectiveState : "unknown"
            if (s === "needs_you") return compact.warmFg
            // idle/snag/unknown are quiet; a live working/acting state is full-strength
            if (s === "idle" || s === "snag" || s === "unknown") return compact.dimFg
            return compact.fg
        }
        // sample the live horizon colour; darken it in the light register so the ring
        // stays legible on a near-white panel.
        aurora: compact.model
            ? (compact.skin.dark ? compact.model.horizonColor
                                 : Qt.darker(compact.model.horizonColor, 1.7))
            : Qt.rgba(0.10, 0.13, 0.22, 1.0)
        intensity: {
            var s = compact.model ? compact.model.effectiveState : "unknown"
            if (s === "needs_you") return 1.0
            if (s === "working" || s === "acting")
                return Math.max(0.4, Math.min(1.0, compact.model ? compact.model.busy : 0))
            return 0.0
        }
        breathing: {
            var s = compact.model ? compact.model.effectiveState : "unknown"
            return s === "working" || s === "needs_you" || s === "acting"
        }
        reducedMotion: compact.model ? compact.model.reducedMotion : false
    }

    // Click toggles the popup via the PlasmoidItem's `expanded`. This opens the popup on
    // the panel/desktop. NOTE: in the system tray the popup is the tray's own shared
    // dialog (driven by the tray's state, not this `expanded`), so a tray click does not
    // open it — a known Plasma tray-host limitation tracked for follow-up.
    MouseArea {
        anchors.fill: parent
        onClicked: compact.plasmoidItem.expanded = !compact.plasmoidItem.expanded
    }
}

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
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami

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

    // Claim a full icon-sized SQUARE cell on the panel — the standard panel-icon idiom —
    // so the porthole matches the icontasks app-icon size instead of collapsing to a
    // small default. Fill the panel thickness; request a square in the free axis.
    readonly property bool horizontal: Plasmoid.formFactor === PlasmaCore.Types.Horizontal
    Layout.fillHeight: horizontal
    Layout.fillWidth: !horizontal
    Layout.minimumWidth:  horizontal ? height : Kirigami.Units.gridUnit
    Layout.minimumHeight: horizontal ? Kirigami.Units.gridUnit : width

    readonly property color warmFg: skin.warmText
    readonly property color fg: skin.text
    readonly property color dimFg: skin.dim

    // the tray glyph wears the SAME aurora ring/halo as the panel header, so the
    // tray→popup zoom reads as one object. Earned (calm at rest, blooms with busy).
    AuroraRing {
        anchors.centerIn: parent
        // the porthole fills the dock cell (the earned outer bloom is allowed to spill;
        // the compact Item doesn't clip, so a busy/needs-you bloom glows past the edge)
        diameter: Math.round(Math.min(compact.width, compact.height) * 0.94)
        glyph: compact.model ? compact.model.glyphFor(compact.model.effectiveState) : "—"
        glyphColor: "#ECEFF6"     // always a light ink; state is carried by shape + aurora mood (a11y)
        hovered: clickArea.containsMouse
        // the porthole shares the wallpaper palette + the same live floats, so the
        // keyhole and the nimbus-aurora wallpaper move together.
        dawnPalette: compact.model ? compact.model.auroraPalette : undefined
        busy:  compact.model ? compact.model.busy : 0
        warm:  compact.model ? compact.model.warmFor(compact.model.effectiveState, compact.model.warm) : 0
        snag:  compact.model ? compact.model.snag : 0
        unknownState: (compact.model ? compact.model.effectiveState : "unknown") === "unknown"
        energy: compact.model ? compact.model.auroraEnergyFor(compact.model.effectiveState, compact.model.busy) : 0.95
        bloom:  compact.model ? compact.model.ringIntensityFor(compact.model.effectiveState, compact.model.busy) : 0.0
        breathing: compact.model ? compact.model.breathingFor(compact.model.effectiveState) : false
        emphasized: (compact.model ? compact.model.effectiveState : "unknown") === "needs_you"
        reducedMotion: compact.model ? compact.model.reducedMotion : false
    }

    // Click toggles the popup via the PlasmoidItem's `expanded`. This opens the popup on
    // the panel/desktop. NOTE: in the system tray the popup is the tray's own shared
    // dialog (driven by the tray's state, not this `expanded`), so a tray click does not
    // open it — a known Plasma tray-host limitation tracked for follow-up.
    // screen-reader: the always-visible dock surface exposes its state + the open action
    Accessible.role: Accessible.Button
    Accessible.name: compact.model ? compact.model.labelFor(compact.model.effectiveState) : "AgentOS Keyhole"
    Accessible.onPressAction: compact.plasmoidItem.expanded = !compact.plasmoidItem.expanded

    MouseArea {
        id: clickArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor    // the dock glyph IS the entry point — make it read as clickable
        onClicked: compact.plasmoidItem.expanded = !compact.plasmoidItem.expanded
    }
}

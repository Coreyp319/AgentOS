/*
 * ActionButton.qml — a Check-ins per-task action affordance (ADR-0053).
 *
 * Phase A is READ-ONLY: these render but are DISABLED, painted in the dim token, with an honest
 * "why" tooltip (reusing the FullRepresentation "Board unavailable" disabled-with-reason idiom).
 * They are the SEAM where a later ADR (the agentosd→Hermes write client) wires real writes.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Controls as QQC2

Item {
    id: btn
    property var skin
    property string glyph: ""
    property string title: ""
    property bool enabledAction: false      // Phase A: always false (read-only); the wiring seam
    property string whyDisabled: "Read-only — write actions arrive with the Hermes write-API"
    signal triggered()

    implicitWidth: 28
    implicitHeight: 28

    Rectangle {
        anchors.fill: parent
        radius: 8
        color: ma.containsMouse && btn.enabledAction ? (skin ? skin.tintHover : "#1C2230")
                                                      : (skin ? Qt.rgba(skin.text.r, skin.text.g, skin.text.b, 0.05) : "#15171f")
        border.width: 1
        border.color: skin ? skin.hairline : "#262A36"
        opacity: btn.enabledAction ? 1.0 : 0.55

        Text {
            anchors.centerIn: parent
            text: btn.glyph
            font.pixelSize: 13
            font.family: "monospace"
            color: btn.enabledAction ? (skin ? skin.muted : "#B4BAC8")
                                     : (skin ? skin.dim : "#8A90A0")
        }
    }

    // When disabled (Phase A), announce as StaticText carrying the "why" — NOT an actionable Button
    // that does nothing on activate (WCAG 4.1.2: role/state must match reality). The why stays in the
    // name regardless; the tooltip still works because the root Item stays `enabled` for hover.
    Accessible.role: btn.enabledAction ? Accessible.Button : Accessible.StaticText
    Accessible.name: btn.title + (btn.enabledAction ? "" : (". " + btn.whyDisabled))

    QQC2.ToolTip.visible: ma.containsMouse
    QQC2.ToolTip.text: btn.enabledAction ? btn.title : (btn.title + " — " + btn.whyDisabled)
    QQC2.ToolTip.delay: 400

    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: btn.enabledAction ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (btn.enabledAction) btn.triggered()
    }
}

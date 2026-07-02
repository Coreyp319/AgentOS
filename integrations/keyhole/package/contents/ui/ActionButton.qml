/*
 * ActionButton.qml — a Check-ins per-task action affordance (ADR-0053).
 *
 * Phase A is READ-ONLY for writes: those render but are DISABLED, painted in the dim token, with
 * an honest "why" (tooltip on hover/focus, and flashed on a click so intent is never swallowed).
 * They are the SEAM where a later ADR (the agentosd→Hermes write client) wires real writes.
 * A LIVE action (read-only link-outs like "Open in board") paints link-blue, is keyboard-operable
 * (Tab + Return/Space + AT press), and wears the shell's shape focus ring (WCAG 2.1.1/2.4.7/4.1.2).
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
    property bool enabledAction: false      // Phase A: writes stay false (read-only); reads may be live
    property string whyDisabled: "Read-only — write actions arrive with the Hermes write-API"
    signal triggered()

    implicitWidth: 28
    implicitHeight: 28

    // the "why" must answer a CLICK too, not just a 400ms hover dwell — a swallowed click on a
    // disabled control is the moment the seam reads as broken instead of coming-soon
    property bool _whyFlash: false
    Timer { id: whyTimer; interval: 2500; onTriggered: btn._whyFlash = false }

    activeFocusOnTab: enabledAction
    Keys.onReturnPressed: if (enabledAction) triggered()
    Keys.onEnterPressed:  if (enabledAction) triggered()
    Keys.onSpacePressed:  if (enabledAction) triggered()

    Rectangle {
        anchors.fill: parent
        radius: 8
        color: ma.containsMouse && btn.enabledAction ? (skin ? skin.tintHover : "#1C2230")
                                                      : (skin ? Qt.rgba(skin.text.r, skin.text.g, skin.text.b, 0.05) : "#15171f")
        border.width: 1
        // the border doubles as the shape focus ring (the shell's board-row idiom) — WCAG 2.4.7
        border.color: btn.activeFocus ? (skin ? skin.text : "#E6E9F0")
                                      : (skin ? skin.hairline : "#262A36")
        opacity: btn.enabledAction ? 1.0 : 0.55

        Text {
            anchors.centerIn: parent
            text: btn.glyph
            font.pixelSize: 13
            font.family: "monospace"
            // live action = the shell's established link-blue "clickable" idiom (footer, svc rows) —
            // hue + lightness, never opacity alone, so it reads live beside the disabled seam
            color: btn.enabledAction ? (skin ? skin.blue : "#9B82E0")
                                     : (skin ? skin.dim : "#8A90A0")
        }
    }

    // When disabled (Phase A), announce as StaticText carrying the "why" — NOT an actionable Button
    // that does nothing on activate (WCAG 4.1.2: role/state must match reality). The why stays in the
    // name regardless; the tooltip still works because the root Item stays `enabled` for hover.
    Accessible.role: btn.enabledAction ? Accessible.Button : Accessible.StaticText
    Accessible.name: btn.title + (btn.enabledAction ? "" : (". " + btn.whyDisabled))
    Accessible.focusable: btn.enabledAction
    Accessible.onPressAction: if (btn.enabledAction) btn.triggered()

    QQC2.ToolTip.visible: ma.containsMouse || btn.activeFocus || btn._whyFlash
    QQC2.ToolTip.text: btn.enabledAction ? btn.title : (btn.title + " — " + btn.whyDisabled)
    QQC2.ToolTip.delay: (btn._whyFlash || btn.activeFocus) ? 0 : 400

    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: btn.enabledAction ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: {
            if (btn.enabledAction) {
                btn.triggered()
            } else {
                btn._whyFlash = true            // speak the why at the moment of intent
                whyTimer.restart()
            }
        }
    }
}

/*
 * ringtest.qml — Phase-2 integration check for the shaderised AuroraRing.
 *
 * Instantiates the REAL contents/ui/AuroraRing at all five moods WITH glyphs, over the
 * instrument base, to confirm the full composition (live shader disc + rim + earned
 * halo + scrim + glyph) holds together and the SHAPE glyph stays legible over the flow.
 * Grabs ONE PNG after the flow has advanced a little.
 *
 *   qml6 ringtest.qml        # writes ringtest.png, then quits   (live session only)
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Window
import "contents/ui"

Window {
    id: win
    visible: true
    width: 560; height: 200
    color: "#12141C"          // instrument deep-navy base (the popup register)
    title: "AuroraRing integration check"

    readonly property var moods: [
        { glyph: "○", busy: 0.0,  warm: 0.0,  snag: 0.0, unk: false, e: 0.86, bloom: 0.20, emph: false, label: "idle" },
        { glyph: "◐", busy: 0.85, warm: 0.0,  snag: 0.0, unk: false, e: 1.36, bloom: 0.85, emph: false, label: "working" },
        { glyph: "●", busy: 0.2,  warm: 0.90, snag: 0.0, unk: false, e: 1.08, bloom: 0.62, emph: true,  label: "needs_you" },
        { glyph: "▢", busy: 0.0,  warm: 0.0,  snag: 0.9, unk: false, e: 0.90, bloom: 0.0,  emph: false, label: "snag" },
        { glyph: "—", busy: 0.0,  warm: 0.0,  snag: 0.0, unk: true,  e: 0.68, bloom: 0.10, emph: false, label: "unknown" }
    ]

    Row {
        anchors.centerIn: parent
        spacing: 20
        Repeater {
            model: win.moods
            Column {
                spacing: 8
                AuroraRing {
                    anchors.horizontalCenter: parent.horizontalCenter
                    diameter: 72
                    glyph: modelData.glyph
                    busy: modelData.busy
                    warm: modelData.warm
                    snag: modelData.snag
                    unknownState: modelData.unk
                    energy: modelData.e
                    bloom: modelData.bloom
                    emphasized: modelData.emph
                    breathing: modelData.busy > 0 || modelData.warm > 0
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: modelData.label; color: "#B4BAC8"; font.pixelSize: 11
                }
            }
        }
    }

    Timer {
        interval: 1100; running: true; repeat: false
        onTriggered: {
            win.contentItem.grabToImage(function (res) {
                res.saveToFile("ringtest.png")
                console.log("RINGTEST: saved ringtest.png")
                Qt.callLater(Qt.quit)
            })
        }
    }
}

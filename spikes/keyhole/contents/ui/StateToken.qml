/*
 * StateToken.qml — the contrast-locked status token: SHAPE glyph + TEXT label,
 * never color-only (a11y canon). The glyph is a distinct shape per state; the
 * label is plain words; color is a third, redundant channel.
 *
 * The `snag` glyph ▢ is rendered with a dashed feel by drawing it dim; the
 * `needs_you` token is the ONE warm element (reserved dawn-glow). Everything
 * else is quiet on a contrast-locked foreground so it stays AA-legible even
 * under the snag-desaturate worst case.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Layouts

RowLayout {
    id: token
    property string glyph: "—"
    property string label: "Status unavailable"
    property string state: "unknown"
    property real warm: 0.0
    property bool reducedMotion: false
    spacing: 8

    // contrast-locked palette OUTSIDE the personalization envelope
    readonly property color fg: "#E6E9F0"          // near-white, AA on the dark popup
    readonly property color dimFg: "#8A90A0"       // for unknown/snag
    readonly property color warmFg: "#FF9957"      // the reserved dawn-glow rgb(255,153,87)

    readonly property color tokenColor: {
        if (state === "unknown") return dimFg
        if (state === "snag")    return dimFg
        if (state === "needs_you") return warmFg
        return fg
    }

    Text {
        id: glyphText
        text: token.glyph
        color: token.tokenColor
        font.pixelSize: 18
        font.bold: token.state === "needs_you"
        Layout.alignment: Qt.AlignVCenter
        // breathing liveness only when working/needs_you and motion allowed
        opacity: 1.0
        SequentialAnimation on opacity {
            running: !token.reducedMotion && (token.state === "working" || token.state === "needs_you")
            loops: Animation.Infinite
            // 3.2s breath, deliberately NOT synced to the 2s poll
            NumberAnimation { to: 0.55; duration: 1600; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.00; duration: 1600; easing.type: Easing.InOutSine }
        }
    }

    Text {
        text: token.label
        color: token.tokenColor
        font.pixelSize: 14
        font.bold: token.state === "needs_you"
        elide: Text.ElideRight
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignVCenter
    }
}

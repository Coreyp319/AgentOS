/*
 * RingGauge.qml — a zero-shader circular % arc for the Check-ins metrics rail (ADR-0051).
 *
 * A Canvas (CPU paint), repainted ONLY on data change — never a per-frame loop — so it stays
 * within the keyhole frame budget. The cool aurora ramp drives it (GPU pressure is cool; the
 * reserved warm is never spent here). Honest: an em-dash centre when the value is UNKNOWN.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick

Item {
    id: ring
    property var skin
    property real value: 0          // 0..1 ; negative is treated as UNKNOWN (track only)
    property string centerText: ""
    property bool unknown: false
    property color arcColor: skin ? skin.auroraMid : "#4A5AD2"
    property color trackColor: skin ? skin.hairline : "#262A36"

    implicitWidth: 44
    implicitHeight: 44

    onValueChanged: cv.requestPaint()
    onArcColorChanged: cv.requestPaint()
    onTrackColorChanged: cv.requestPaint()
    onUnknownChanged: cv.requestPaint()

    Canvas {
        id: cv
        anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var w = width, h = height
            var cx = w / 2, cy = h / 2, r = Math.min(w, h) / 2 - 3
            ctx.lineWidth = 3.5
            ctx.lineCap = "round"
            // full track
            ctx.beginPath()
            ctx.arc(cx, cy, r, 0, 2 * Math.PI)
            ctx.strokeStyle = ring.trackColor
            ctx.stroke()
            // value arc from 12 o'clock, clockwise — drawn only when known + > 0
            var v = Math.max(0, Math.min(1, ring.value))
            if (!ring.unknown && v > 0) {
                ctx.beginPath()
                ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + v * 2 * Math.PI)
                ctx.strokeStyle = ring.arcColor
                ctx.stroke()
            }
        }
    }

    Text {
        anchors.centerIn: parent
        text: ring.centerText
        color: ring.skin ? (ring.unknown ? ring.skin.label : ring.skin.text) : "#E6E9F0"
        font.pixelSize: 11
        font.bold: true
        font.family: "monospace"
        Accessible.ignored: true        // decorative — the rail cell carries the spoken name
    }
}
